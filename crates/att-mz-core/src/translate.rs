//! 正文翻译运行与模型请求。
//!
//! 本模块负责把当前游戏可提取正文组装成 OpenAI 兼容 Chat Completions
//! 请求，校验模型返回，并把成功译文或质量问题写入数据库。实现保持长期数据
//! 结构与 Python 版本兼容；内部调度按配置启动并发 worker，并用限流器保护
//! 模型服务。

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::Arc;
use std::time::{Duration, Instant};

use reqwest::StatusCode;
use serde::Deserialize;
use serde_json::{Map, Value, json};
use tokio::sync::Mutex;
use tokio::task::JoinSet;
use tokio::time::{Instant as TokioInstant, sleep, sleep_until};

use crate::config::{RuntimeSettings, TextRuleOptions};
use crate::db::{
    JapaneseResidualRuleRecord, LlmFailureRecord, TranslationErrorItemRecord,
    TranslationItemRecord, TranslationRunRecord,
};
use crate::error::{AttMzError, Result};
use crate::placeholder::{
    PlaceholderRule, build_placeholder_context, mask_translation_controls,
    restore_placeholder_lines, verify_placeholder_counts,
};
use crate::placeholder_scan::ActiveTextItem;
use crate::report::{AgentReport, issue};
use crate::rmmz::read_data_json_files;
use crate::translation_state::{
    check_japanese_residual_for_item, compile_line_width_pattern, load_active_translation_items,
    normalize_manual_translation_lines_with_pattern, validate_translation_text_structure,
};
use crate::{GameRecord, GameRegistry};

const SCENE_PROMPT_HEADER: &str = "# 场景";
const BODY_PROMPT_HEADER: &str = "# 正文";
const NARRATION_ROLE: &str = "旁白";
const TERMINOLOGY_PROMPT_HEADER: &str = "[[术语表]]";
const BASE_NAME_FILES: &[&str] = &[
    "Actors.json",
    "Classes.json",
    "Skills.json",
    "Items.json",
    "Weapons.json",
    "Armors.json",
    "Enemies.json",
    "States.json",
];
const SYSTEM_TERM_FIELDS: &[&str] = &[
    "elements",
    "skillTypes",
    "weaponTypes",
    "armorTypes",
    "equipTypes",
];

/// 正文翻译单次运行限制。
#[derive(Debug, Clone, Default, PartialEq)]
pub struct TranslationRunLimits {
    /// 本轮最多处理的还没成功保存译文条目数。
    pub max_items: Option<usize>,
    /// 本轮最多处理的模型批次数。
    pub max_batches: Option<usize>,
    /// 本轮翻译最长运行秒数。
    pub time_limit_seconds: Option<u64>,
    /// 检查没通过的译文比例达到该值时停止本轮。
    pub stop_on_error_rate: Option<f64>,
    /// 模型限流故障达到该次数时停止本轮；未设置时首个模型故障会停止。
    pub stop_on_rate_limit_count: Option<usize>,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
struct TranslationCacheKey {
    original_lines: Vec<String>,
    item_type: String,
    role: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct TerminologyPromptEntry {
    source_text: String,
    translated_text: String,
}

#[derive(Debug, Clone, Default)]
struct TerminologyPromptIndex {
    entries: Vec<TerminologyPromptEntry>,
    entries_by_match_text: BTreeMap<String, Vec<TerminologyPromptEntry>>,
    owner_entries: BTreeMap<String, Vec<TerminologyPromptEntry>>,
    system_entries: Vec<TerminologyPromptEntry>,
}

#[derive(Debug, Clone)]
struct TranslationBatch {
    items: Vec<ActiveTextItem>,
    messages: Vec<ChatMessage>,
}

#[derive(Debug, Clone)]
struct ChatMessage {
    role: &'static str,
    text: String,
}

#[derive(Debug, Clone)]
struct LlmFailureInfo {
    category: String,
    error_type: String,
    message: String,
    retryable: bool,
    attempt_count: usize,
}

#[derive(Debug)]
struct TranslationRunProgress {
    success_count: usize,
    quality_error_count: usize,
    llm_failure_count: usize,
    stop_reason: Option<String>,
    last_error: String,
}

struct TranslationProcessingInput<'a> {
    registry: &'a GameRegistry,
    game_title: &'a str,
    run_record: &'a mut TranslationRunRecord,
    batches: Vec<TranslationBatch>,
    duplicate_items: HashMap<TranslationCacheKey, Vec<ActiveTextItem>>,
    settings: &'a RuntimeSettings,
    custom_rules: Vec<PlaceholderRule>,
    residual_rules: BTreeMap<String, JapaneseResidualRuleRecord>,
    limits: &'a TranslationRunLimits,
    started_at: Instant,
}

#[derive(Clone)]
struct BatchTaskContext {
    client: Arc<reqwest::Client>,
    settings: Arc<RuntimeSettings>,
    custom_rules: Arc<Vec<PlaceholderRule>>,
    residual_rules: Arc<BTreeMap<String, JapaneseResidualRuleRecord>>,
    limiter: Arc<AsyncRateLimiter>,
}

#[derive(Debug)]
enum BatchProcessingResult {
    Success {
        batch_index: usize,
        items: Vec<TranslationItemRecord>,
    },
    QualityErrors {
        batch_index: usize,
        errors: Vec<TranslationErrorItemRecord>,
    },
    LlmFailure {
        batch_index: usize,
        failure: LlmFailureInfo,
    },
}

#[derive(Debug, Deserialize)]
struct TranslationResponseItem {
    id: String,
    translation_lines: Vec<String>,
}

/// 执行 `translate` 命令并生成稳定 JSON 报告。
pub fn translate_report(
    registry: &GameRegistry,
    game_record: &GameRecord,
    settings: &RuntimeSettings,
    custom_placeholder_rules_text: Option<&str>,
    limits: &TranslationRunLimits,
) -> Result<AgentReport> {
    let custom_rules =
        load_placeholder_rules(registry, game_record, custom_placeholder_rules_text)?;
    let active_items = load_active_translation_items(
        registry,
        game_record,
        &settings.source_text_required_pattern,
        &settings.text_rules,
    )?;
    let active_paths = active_items
        .iter()
        .map(|item| item.location_path.clone())
        .collect::<BTreeSet<_>>();
    registry.delete_translation_items_except_paths(&game_record.game_title, &active_paths)?;

    let total_extracted = active_items.len();
    if total_extracted == 0 {
        return Ok(blocked_translate_report(
            None,
            total_extracted,
            0,
            0,
            0,
            0,
            0,
            0,
            "没有提取到任何可翻译正文",
        ));
    }

    let translated_paths = registry.read_translation_location_paths(&game_record.game_title)?;
    let pending_items = active_items
        .into_iter()
        .filter(|item| !translated_paths.contains(&item.location_path))
        .collect::<Vec<_>>();
    let limited_items = limit_pending_items(pending_items, limits.max_items)?;
    let pending_count = limited_items.len();
    if pending_count == 0 {
        return Ok(translate_summary_report(TranslateSummary {
            run_id: None,
            total_extracted,
            pending_count: 0,
            deduplicated_count: 0,
            batch_count: 0,
            success_count: 0,
            quality_error_count: 0,
            llm_failure_count: 0,
        }));
    }

    let (deduplicated_items, duplicate_items) = deduplicate_items(limited_items);
    let glossary = registry
        .read_terminology_glossary(&game_record.game_title)?
        .unwrap_or_default();
    let data_files = read_data_json_files(&game_record.game_path)?;
    let terminology_index = TerminologyPromptIndex::from_glossary(&glossary, &data_files);
    let mut batches = build_translation_batches(
        &deduplicated_items,
        settings,
        &custom_rules,
        &terminology_index,
    )?;
    if let Some(max_batches) = limits.max_batches {
        batches.truncate(max_batches);
    }
    let deduplicated_count = batches.iter().map(|batch| batch.items.len()).sum::<usize>();
    if batches.is_empty() {
        return Ok(blocked_translate_report(
            None,
            total_extracted,
            pending_count,
            0,
            0,
            0,
            0,
            0,
            "相同原文合并后，没有可送入模型的批次",
        ));
    }

    let mut run_record = registry.start_translation_run(
        &game_record.game_title,
        total_extracted,
        pending_count,
        deduplicated_count,
        batches.len(),
    )?;
    let started_at = Instant::now();
    let residual_rules = registry
        .read_japanese_residual_rules(&game_record.game_title)?
        .into_iter()
        .map(|record| (record.location_path.clone(), record))
        .collect::<BTreeMap<_, _>>();
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(settings.text_translation.worker_count.max(1))
        .enable_all()
        .build()
        .map_err(|error| AttMzError::InvalidConfig(format!("创建翻译并发运行时失败: {error}")))?;
    let progress = runtime.block_on(process_translation_batches_concurrently(
        TranslationProcessingInput {
            registry,
            game_title: &game_record.game_title,
            run_record: &mut run_record,
            batches,
            duplicate_items,
            settings,
            custom_rules,
            residual_rules,
            limits,
            started_at,
        },
    ))?;

    run_record.status = if progress.stop_reason.is_none()
        && progress.quality_error_count == 0
        && progress.llm_failure_count == 0
    {
        "completed".to_string()
    } else {
        "blocked".to_string()
    };
    run_record.success_count = progress.success_count;
    run_record.quality_error_count = progress.quality_error_count;
    run_record.llm_failure_count = progress.llm_failure_count;
    run_record.stop_reason = if run_record.status == "completed" {
        String::new()
    } else {
        progress.stop_reason.unwrap_or_else(|| {
            if progress.quality_error_count > 0 {
                return "存在模型翻了但项目检查没通过的译文".to_string();
            }
            if progress.llm_failure_count > 0 {
                return "存在模型请求失败，部分文本还没成功保存译文".to_string();
            }
            "正文翻译未完成".to_string()
        })
    };
    run_record.last_error = if run_record.status == "completed" {
        String::new()
    } else {
        progress.last_error
    };
    registry.write_translation_run(&game_record.game_title, &run_record, true)?;
    if run_record.status != "completed" {
        return Ok(blocked_translate_report(
            Some(run_record.run_id),
            total_extracted,
            pending_count,
            deduplicated_count,
            run_record.batch_count,
            progress.success_count,
            progress.quality_error_count,
            progress.llm_failure_count,
            &run_record.stop_reason,
        ));
    }

    Ok(translate_summary_report(TranslateSummary {
        run_id: Some(run_record.run_id),
        total_extracted,
        pending_count,
        deduplicated_count,
        batch_count: run_record.batch_count,
        success_count: progress.success_count,
        quality_error_count: progress.quality_error_count,
        llm_failure_count: progress.llm_failure_count,
    }))
}

fn load_placeholder_rules(
    registry: &GameRegistry,
    game_record: &GameRecord,
    custom_placeholder_rules_text: Option<&str>,
) -> Result<Vec<PlaceholderRule>> {
    if let Some(rules_text) = custom_placeholder_rules_text {
        return crate::placeholder::parse_custom_placeholder_rules_text(rules_text);
    }
    registry.read_placeholder_rules(&game_record.game_title)
}

fn limit_pending_items(
    pending_items: Vec<ActiveTextItem>,
    max_items: Option<usize>,
) -> Result<Vec<ActiveTextItem>> {
    let Some(max_items) = max_items else {
        return Ok(pending_items);
    };
    if max_items == 0 {
        return Err(AttMzError::InvalidConfig(
            "max_items 必须是正整数".to_string(),
        ));
    }
    Ok(pending_items.into_iter().take(max_items).collect())
}

fn deduplicate_items(
    items: Vec<ActiveTextItem>,
) -> (
    Vec<ActiveTextItem>,
    HashMap<TranslationCacheKey, Vec<ActiveTextItem>>,
) {
    let mut seen = BTreeSet::new();
    let mut unique_items = Vec::new();
    let mut duplicates: HashMap<TranslationCacheKey, Vec<ActiveTextItem>> = HashMap::new();
    for item in items {
        let key = cache_key(&item);
        if seen.insert(key.clone()) {
            unique_items.push(item);
        } else {
            duplicates.entry(key).or_default().push(item);
        }
    }
    (unique_items, duplicates)
}

fn cache_key(item: &ActiveTextItem) -> TranslationCacheKey {
    TranslationCacheKey {
        original_lines: item.original_lines.clone(),
        item_type: item.item_type.clone(),
        role: item.role.clone(),
    }
}

fn build_translation_batches(
    items: &[ActiveTextItem],
    settings: &RuntimeSettings,
    custom_rules: &[PlaceholderRule],
    terminology_index: &TerminologyPromptIndex,
) -> Result<Vec<TranslationBatch>> {
    let token_size = settings.translation_context.token_size;
    if token_size == 0 {
        return Err(AttMzError::InvalidConfig(
            "translation_context.token_size 必须大于 0".to_string(),
        ));
    }
    if settings.translation_context.factor <= 0.0 {
        return Err(AttMzError::InvalidConfig(
            "translation_context.factor 必须大于 0".to_string(),
        ));
    }
    let mut batches = Vec::new();
    let mut current_items = Vec::new();
    let mut current_bodies = Vec::new();
    let mut current_length = 0usize;
    let mut index = 0usize;
    while index < items.len() {
        let item = &items[index];
        current_length +=
            append_item_to_batch(item, &mut current_items, &mut current_bodies, custom_rules)?;
        index += 1;
        let estimated_tokens =
            (current_length as f64 / settings.translation_context.factor) as usize;
        if estimated_tokens < token_size {
            continue;
        }
        if item
            .role
            .as_deref()
            .is_none_or(|role| role == NARRATION_ROLE)
        {
            batches.push(build_translation_batch(
                &settings.text_translation.system_prompt,
                current_items,
                current_bodies,
                terminology_index,
            ));
            current_items = Vec::new();
            current_bodies = Vec::new();
            current_length = 0;
            continue;
        }

        let anchor_role = item.role.clone();
        let mut appended_command_items = 0usize;
        while index < items.len()
            && appended_command_items < settings.translation_context.max_command_items
        {
            let next_item = &items[index];
            let same_context = next_item
                .role
                .as_ref()
                .is_none_or(|role| role == NARRATION_ROLE || Some(role) == anchor_role.as_ref());
            if !same_context {
                break;
            }
            append_item_to_batch(
                next_item,
                &mut current_items,
                &mut current_bodies,
                custom_rules,
            )?;
            index += 1;
            appended_command_items += 1;
        }
        batches.push(build_translation_batch(
            &settings.text_translation.system_prompt,
            current_items,
            current_bodies,
            terminology_index,
        ));
        current_items = Vec::new();
        current_bodies = Vec::new();
        current_length = 0;
    }
    if !current_items.is_empty() {
        batches.push(build_translation_batch(
            &settings.text_translation.system_prompt,
            current_items,
            current_bodies,
            terminology_index,
        ));
    }
    Ok(batches)
}

fn append_item_to_batch(
    item: &ActiveTextItem,
    current_items: &mut Vec<ActiveTextItem>,
    current_bodies: &mut Vec<String>,
    custom_rules: &[PlaceholderRule],
) -> Result<usize> {
    let context = build_placeholder_context(custom_rules, &item.original_lines)?;
    let masked_text = if item.item_type == "short_text" {
        context.text_for_model_lines.join("")
    } else {
        context.text_for_model_lines.join("\n")
    };
    let sequence = current_items.len() + 1;
    let formatted = format_translation_item(item, &masked_text, sequence);
    let length = formatted.len();
    current_bodies.push(formatted);
    current_items.push(item.clone());
    Ok(length)
}

fn format_translation_item(item: &ActiveTextItem, masked_text: &str, sequence: usize) -> String {
    let role = item.role.as_deref().unwrap_or_default();
    if item.item_type == "array" {
        return format!(
            "## {sequence}\n\nid: {}\ntype: {}\nrole: {}\nline_count: {}\n\n{masked_text}\n\n",
            item.location_path,
            item.item_type,
            role,
            item.original_lines.len()
        );
    }
    format!(
        "## {sequence}\n\nid: {}\ntype: {}\nrole: {}\n\n{masked_text}\n\n",
        item.location_path, item.item_type, role
    )
}

fn build_translation_batch(
    system_prompt: &str,
    current_items: Vec<ActiveTextItem>,
    current_bodies: Vec<String>,
    terminology_index: &TerminologyPromptIndex,
) -> TranslationBatch {
    let mut user_sections = vec![format_scene_section(&current_items)];
    let terminology_section = format_terminology_section(&current_items, terminology_index);
    if !terminology_section.is_empty() {
        user_sections.push(terminology_section);
    }
    user_sections.push(format!(
        "{BODY_PROMPT_HEADER}\n\n{}",
        current_bodies.join("")
    ));
    TranslationBatch {
        items: current_items,
        messages: vec![
            ChatMessage {
                role: "system",
                text: system_prompt.to_string(),
            },
            ChatMessage {
                role: "user",
                text: user_sections.join("\n\n"),
            },
        ],
    }
}

fn format_scene_section(items: &[ActiveTextItem]) -> String {
    let display_names = items
        .iter()
        .filter_map(|item| item.display_name.as_deref())
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .collect::<BTreeSet<_>>();
    let scene = if display_names.is_empty() {
        "未指定".to_string()
    } else {
        display_names.into_iter().collect::<Vec<_>>().join("、")
    };
    format!("{SCENE_PROMPT_HEADER}\n\n地图：{scene}")
}

fn format_terminology_section(
    items: &[ActiveTextItem],
    terminology_index: &TerminologyPromptIndex,
) -> String {
    let selected_entries = terminology_index.select_for_batch(items);
    if selected_entries.is_empty() {
        String::new()
    } else {
        let mut lines = vec![TERMINOLOGY_PROMPT_HEADER.to_string()];
        lines.extend(
            selected_entries
                .into_iter()
                .map(|entry| format!("{} => {}", entry.source_text, entry.translated_text)),
        );
        lines.join("\n")
    }
}

impl TerminologyPromptIndex {
    fn from_glossary(
        glossary: &BTreeMap<String, String>,
        data_files: &BTreeMap<String, Value>,
    ) -> Self {
        let entries = glossary
            .iter()
            .filter_map(|(source_text, translated_text)| {
                TerminologyPromptEntry::from_pair(source_text, translated_text)
            })
            .collect::<Vec<_>>();
        let mut entries_by_match_text: BTreeMap<String, Vec<TerminologyPromptEntry>> =
            BTreeMap::new();
        for entry in &entries {
            entries_by_match_text
                .entry(entry.source_text.clone())
                .or_default()
                .push(entry.clone());
        }
        Self {
            owner_entries: build_owner_terminology_entries(data_files, &entries_by_match_text),
            system_entries: build_system_terminology_entries(data_files, &entries_by_match_text),
            entries,
            entries_by_match_text,
        }
    }

    fn select_for_batch(&self, items: &[ActiveTextItem]) -> Vec<TerminologyPromptEntry> {
        let mut selected = Vec::new();
        let mut seen = BTreeSet::new();
        for display_name in items.iter().filter_map(|item| item.display_name.as_deref()) {
            self.extend_matching_text(display_name, &mut selected, &mut seen);
        }
        for item in items {
            if let Some(role) = item.role.as_deref() {
                self.extend_matching_text(role, &mut selected, &mut seen);
            }
            for entry in self.select_owner_entries(&item.location_path) {
                push_prompt_entry(entry, &mut selected, &mut seen);
            }
        }
        let joined_original_text = items
            .iter()
            .flat_map(|item| item.original_lines.iter())
            .map(String::as_str)
            .collect::<Vec<_>>()
            .join("\n");
        for entry in &self.entries {
            if joined_original_text.contains(&entry.source_text) {
                push_prompt_entry(entry, &mut selected, &mut seen);
            }
        }
        selected
    }

    fn extend_matching_text(
        &self,
        text: &str,
        selected: &mut Vec<TerminologyPromptEntry>,
        seen: &mut BTreeSet<(String, String)>,
    ) {
        let normalized_text = text.trim();
        if normalized_text.is_empty() {
            return;
        }
        if let Some(entries) = self.entries_by_match_text.get(normalized_text) {
            for entry in entries {
                push_prompt_entry(entry, selected, seen);
            }
        }
    }

    fn select_owner_entries(&self, location_path: &str) -> &[TerminologyPromptEntry] {
        if location_path.starts_with("System.json/") {
            return &self.system_entries;
        }
        let Some(owner_key) = owner_key_from_location_path(location_path) else {
            return &[];
        };
        self.owner_entries
            .get(owner_key)
            .map(Vec::as_slice)
            .unwrap_or(&[])
    }
}

impl TerminologyPromptEntry {
    fn from_pair(source_text: &str, translated_text: &str) -> Option<Self> {
        let source_text = source_text.trim();
        let translated_text = translated_text.trim();
        if source_text.is_empty()
            || translated_text.is_empty()
            || !source_text_has_prompt_content(source_text)
        {
            return None;
        }
        Some(Self {
            source_text: source_text.to_string(),
            translated_text: translated_text.to_string(),
        })
    }
}

fn build_owner_terminology_entries(
    data_files: &BTreeMap<String, Value>,
    entries_by_match_text: &BTreeMap<String, Vec<TerminologyPromptEntry>>,
) -> BTreeMap<String, Vec<TerminologyPromptEntry>> {
    let mut owner_entries = BTreeMap::new();
    for file_name in BASE_NAME_FILES {
        let Some(items) = data_files.get(*file_name).and_then(Value::as_array) else {
            continue;
        };
        for item in items {
            let Some(object) = item.as_object() else {
                continue;
            };
            let Some(id) = object.get("id").and_then(Value::as_i64) else {
                continue;
            };
            let owner_key = format!("{file_name}/{id}");
            extend_owner_entries(
                object.get("name").and_then(Value::as_str),
                entries_by_match_text,
                &owner_key,
                &mut owner_entries,
            );
            if *file_name == "Actors.json" {
                extend_owner_entries(
                    object.get("nickname").and_then(Value::as_str),
                    entries_by_match_text,
                    &owner_key,
                    &mut owner_entries,
                );
            }
        }
    }
    owner_entries
}

fn build_system_terminology_entries(
    data_files: &BTreeMap<String, Value>,
    entries_by_match_text: &BTreeMap<String, Vec<TerminologyPromptEntry>>,
) -> Vec<TerminologyPromptEntry> {
    let Some(system_object) = data_files.get("System.json").and_then(Value::as_object) else {
        return Vec::new();
    };
    let mut selected = Vec::new();
    let mut seen = BTreeSet::new();
    for field in SYSTEM_TERM_FIELDS {
        let Some(values) = system_object.get(*field).and_then(Value::as_array) else {
            continue;
        };
        for value in values {
            let Some(text) = value.as_str() else {
                continue;
            };
            if let Some(entries) = entries_by_match_text.get(text.trim()) {
                for entry in entries {
                    push_prompt_entry(entry, &mut selected, &mut seen);
                }
            }
        }
    }
    selected
}

fn extend_owner_entries(
    source_text: Option<&str>,
    entries_by_match_text: &BTreeMap<String, Vec<TerminologyPromptEntry>>,
    owner_key: &str,
    owner_entries: &mut BTreeMap<String, Vec<TerminologyPromptEntry>>,
) {
    let Some(source_text) = source_text.map(str::trim).filter(|value| !value.is_empty()) else {
        return;
    };
    let Some(entries) = entries_by_match_text.get(source_text) else {
        return;
    };
    let target_entries = owner_entries.entry(owner_key.to_string()).or_default();
    let mut seen = target_entries
        .iter()
        .map(|entry| (entry.source_text.clone(), entry.translated_text.clone()))
        .collect::<BTreeSet<_>>();
    for entry in entries {
        push_prompt_entry(entry, target_entries, &mut seen);
    }
}

fn owner_key_from_location_path(location_path: &str) -> Option<&str> {
    let mut slash_count = 0usize;
    for (index, char_value) in location_path.char_indices() {
        if char_value != '/' {
            continue;
        }
        slash_count += 1;
        if slash_count == 2 {
            return Some(&location_path[..index]);
        }
    }
    None
}

fn source_text_has_prompt_content(source_text: &str) -> bool {
    source_text.chars().any(|char_value| {
        char_value == '_'
            || char_value.is_alphanumeric()
            || ('\u{3040}'..='\u{30FF}').contains(&char_value)
            || ('\u{3400}'..='\u{9FFF}').contains(&char_value)
    })
}

fn push_prompt_entry(
    entry: &TerminologyPromptEntry,
    selected: &mut Vec<TerminologyPromptEntry>,
    seen: &mut BTreeSet<(String, String)>,
) {
    if seen.insert((entry.source_text.clone(), entry.translated_text.clone())) {
        selected.push(entry.clone());
    }
}

async fn process_translation_batches_concurrently(
    input: TranslationProcessingInput<'_>,
) -> Result<TranslationRunProgress> {
    let worker_count = input
        .settings
        .text_translation
        .worker_count
        .max(1)
        .min(input.batches.len());
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(input.settings.llm.timeout_seconds))
        .build()
        .map_err(|error| AttMzError::InvalidConfig(format!("创建模型请求客户端失败: {error}")))?;
    let settings = Arc::new(input.settings.clone());
    let task_context = BatchTaskContext {
        client: Arc::new(client),
        settings: Arc::clone(&settings),
        custom_rules: Arc::new(input.custom_rules),
        residual_rules: Arc::new(input.residual_rules),
        limiter: Arc::new(AsyncRateLimiter::new(settings.text_translation.rpm)),
    };
    let mut join_set = JoinSet::new();
    let mut batches = input.batches.into_iter().enumerate();
    let mut duplicate_items = input.duplicate_items;
    let mut success_count = 0usize;
    let mut quality_error_count = 0usize;
    let mut llm_failure_count = 0usize;
    let mut rate_limit_failure_count = 0usize;
    let mut stop_reason = None;
    let mut last_error = String::new();
    let deadline = input.limits.time_limit_seconds.map(|limit_seconds| {
        let limit = Duration::from_secs(limit_seconds);
        let remaining = limit.saturating_sub(input.started_at.elapsed());
        TokioInstant::now() + remaining
    });

    loop {
        while join_set.len() < worker_count {
            let Some((batch_index, batch)) = batches.next() else {
                break;
            };
            spawn_translation_batch_task(&mut join_set, batch_index, batch, task_context.clone());
        }
        if join_set.is_empty() {
            break;
        }
        let result = if let Some(deadline) = deadline {
            tokio::select! {
                _ = sleep_until(deadline) => {
                    stop_reason = Some(format!(
                        "达到本轮翻译时间上限: {} 秒",
                        input.limits.time_limit_seconds.unwrap_or_default()
                    ));
                    last_error = stop_reason.clone().unwrap_or_default();
                    join_set.abort_all();
                    break;
                }
                result = join_set.join_next() => result,
            }
        } else {
            join_set.join_next().await
        };
        let Some(result) = result else {
            break;
        };
        let batch_result = match result {
            Ok(batch_result) => batch_result,
            Err(error) => BatchProcessingResult::LlmFailure {
                batch_index: 0,
                failure: LlmFailureInfo {
                    category: "fatal".to_string(),
                    error_type: "JoinError".to_string(),
                    message: format!("翻译 worker 异常退出: {error}"),
                    retryable: false,
                    attempt_count: 1,
                },
            },
        };
        match batch_result {
            BatchProcessingResult::Success { batch_index, items } => {
                let expanded_items = expand_success_items(items, &mut duplicate_items);
                success_count += expanded_items.len();
                input
                    .registry
                    .write_translation_items(input.game_title, &expanded_items)?;
                tracing::info!(
                    batch_index = batch_index + 1,
                    count = expanded_items.len(),
                    "批次译文已保存"
                );
            }
            BatchProcessingResult::QualityErrors {
                batch_index,
                errors,
            } => {
                let expanded_errors = expand_error_items(errors, &mut duplicate_items);
                quality_error_count += expanded_errors.len();
                input.registry.write_translation_quality_errors(
                    input.game_title,
                    &input.run_record.run_id,
                    &expanded_errors,
                )?;
                tracing::warn!(
                    batch_index = batch_index + 1,
                    count = expanded_errors.len(),
                    "批次译文没有通过项目检查"
                );
            }
            BatchProcessingResult::LlmFailure {
                batch_index,
                failure,
            } => {
                let failure_record = LlmFailureRecord {
                    run_id: input.run_record.run_id.clone(),
                    category: failure.category.clone(),
                    error_type: failure.error_type.clone(),
                    error_message: failure.message.clone(),
                    retryable: failure.retryable,
                    attempt_count: failure.attempt_count,
                };
                input
                    .registry
                    .write_llm_failure(input.game_title, &failure_record)?;
                llm_failure_count += 1;
                if failure.category == "rate_limit" {
                    rate_limit_failure_count += 1;
                }
                last_error = failure.message.clone();
                tracing::warn!(
                    batch_index = batch_index + 1,
                    category = failure.category,
                    attempt_count = failure.attempt_count,
                    "批次模型请求失败"
                );
                let should_stop = if failure.category == "rate_limit" {
                    input
                        .limits
                        .stop_on_rate_limit_count
                        .is_none_or(|limit| rate_limit_failure_count >= limit)
                } else {
                    true
                };
                if should_stop {
                    stop_reason = Some(format!("模型请求失败: {}", failure.message));
                    join_set.abort_all();
                }
            }
        }
        input.run_record.success_count = success_count;
        input.run_record.quality_error_count = quality_error_count;
        input.run_record.llm_failure_count = llm_failure_count;
        input
            .registry
            .write_translation_run(input.game_title, input.run_record, false)?;

        if stop_reason.is_none()
            && let Some(stop_on_error_rate) = input.limits.stop_on_error_rate
        {
            let processed_count = success_count + quality_error_count;
            if processed_count > 0
                && (quality_error_count as f64 / processed_count as f64) >= stop_on_error_rate
            {
                stop_reason = Some(format!(
                    "检查没通过的译文比例达到停止阈值: {stop_on_error_rate}"
                ));
                last_error = stop_reason.clone().unwrap_or_default();
                join_set.abort_all();
            }
        }
        if stop_reason.is_some() {
            break;
        }
    }

    Ok(TranslationRunProgress {
        success_count,
        quality_error_count,
        llm_failure_count,
        stop_reason,
        last_error,
    })
}

fn spawn_translation_batch_task(
    join_set: &mut JoinSet<BatchProcessingResult>,
    batch_index: usize,
    batch: TranslationBatch,
    context: BatchTaskContext,
) {
    join_set.spawn(async move {
        context.limiter.wait().await;
        match request_with_retry(&context.client, &context.settings, &batch.messages).await {
            Ok(response) => match verify_translation_response(
                &batch.items,
                &response,
                &context.custom_rules,
                &context.settings.text_rules,
                &context.residual_rules,
            ) {
                Ok(items) => BatchProcessingResult::Success { batch_index, items },
                Err(errors) => BatchProcessingResult::QualityErrors {
                    batch_index,
                    errors,
                },
            },
            Err(failure) => BatchProcessingResult::LlmFailure {
                batch_index,
                failure,
            },
        }
    });
}

struct AsyncRateLimiter {
    interval: Option<Duration>,
    next_start: Mutex<TokioInstant>,
}

impl AsyncRateLimiter {
    fn new(rpm: Option<usize>) -> Self {
        let interval = rpm
            .filter(|value| *value > 0)
            .map(|value| Duration::from_secs_f64(60.0 / value as f64));
        Self {
            interval,
            next_start: Mutex::new(TokioInstant::now()),
        }
    }

    async fn wait(&self) {
        let Some(interval) = self.interval else {
            return;
        };
        let mut next_start = self.next_start.lock().await;
        let now = TokioInstant::now();
        if *next_start > now {
            sleep_until(*next_start).await;
        }
        *next_start = TokioInstant::now() + interval;
    }
}

async fn request_with_retry(
    client: &reqwest::Client,
    settings: &RuntimeSettings,
    messages: &[ChatMessage],
) -> std::result::Result<String, LlmFailureInfo> {
    let max_attempts = settings.text_translation.retry_count + 1;
    let mut last_failure: Option<LlmFailureInfo> = None;
    for attempt_index in 1..=max_attempts {
        match request_once(client, settings, messages).await {
            Ok(text) => return Ok(text),
            Err(mut failure) => {
                failure.attempt_count = attempt_index;
                let retryable = failure.retryable && attempt_index < max_attempts;
                last_failure = Some(failure.clone());
                if !retryable {
                    return Err(failure);
                }
                let delay_seconds = settings.text_translation.retry_delay * attempt_index as u64;
                if delay_seconds > 0 {
                    sleep(Duration::from_secs(delay_seconds)).await;
                }
            }
        }
    }
    match last_failure {
        Some(failure) => Err(failure),
        None => Err(LlmFailureInfo {
            category: "unknown".to_string(),
            error_type: "Unknown".to_string(),
            message: "模型请求未返回结果".to_string(),
            retryable: false,
            attempt_count: max_attempts,
        }),
    }
}

async fn request_once(
    client: &reqwest::Client,
    settings: &RuntimeSettings,
    messages: &[ChatMessage],
) -> std::result::Result<String, LlmFailureInfo> {
    let url = format!(
        "{}/chat/completions",
        settings.llm.base_url.trim_end_matches('/')
    );
    let request_messages = messages
        .iter()
        .map(|message| json!({"role": message.role, "content": message.text}))
        .collect::<Vec<_>>();
    let mut body = Map::new();
    body.insert("model".to_string(), json!(settings.llm.model));
    body.insert("messages".to_string(), Value::Array(request_messages));
    for (key, value) in &settings.llm.request_body_extra {
        body.insert(key.clone(), value.clone());
    }
    let response = client
        .post(url)
        .bearer_auth(&settings.llm.api_key)
        .json(&Value::Object(body))
        .send()
        .await
        .map_err(|error| classify_reqwest_error(error, 0))?;
    let status = response.status();
    let response_text = response
        .text()
        .await
        .map_err(|error| classify_reqwest_error(error, 0))?;
    if !status.is_success() {
        return Err(classify_http_error(status, &response_text, 0));
    }
    let value: Value = serde_json::from_str(&response_text).map_err(|source| LlmFailureInfo {
        category: "fatal".to_string(),
        error_type: "JsonParseError".to_string(),
        message: format!("模型响应不是有效 JSON: {source}"),
        retryable: false,
        attempt_count: 0,
    })?;
    let content = value
        .get("choices")
        .and_then(Value::as_array)
        .and_then(|choices| choices.first())
        .and_then(|choice| choice.get("message"))
        .and_then(|message| message.get("content"))
        .and_then(Value::as_str)
        .map(str::to_string)
        .filter(|text| !text.trim().is_empty())
        .ok_or_else(|| LlmFailureInfo {
            category: "fatal".to_string(),
            error_type: "EmptyLLMResponse".to_string(),
            message: "LLM 响应中未返回文本内容".to_string(),
            retryable: false,
            attempt_count: 0,
        })?;
    Ok(content)
}

fn classify_http_error(status: StatusCode, body: &str, attempt_count: usize) -> LlmFailureInfo {
    let retryable = matches!(status.as_u16(), 408 | 409 | 425 | 429) || status.as_u16() >= 500;
    let category = if status == StatusCode::TOO_MANY_REQUESTS {
        "rate_limit"
    } else if status.as_u16() >= 500 {
        "server"
    } else if retryable {
        "unknown"
    } else {
        "fatal"
    };
    LlmFailureInfo {
        category: category.to_string(),
        error_type: format!("HTTP {}", status.as_u16()),
        message: format!("HTTP {}: {}", status.as_u16(), compact_error_body(body)),
        retryable,
        attempt_count,
    }
}

fn classify_reqwest_error(error: reqwest::Error, attempt_count: usize) -> LlmFailureInfo {
    let category = if error.is_timeout() {
        "timeout"
    } else if error.is_connect() {
        "connection"
    } else {
        "unknown"
    };
    LlmFailureInfo {
        category: category.to_string(),
        error_type: "ReqwestError".to_string(),
        message: format!("ReqwestError: {error}"),
        retryable: true,
        attempt_count,
    }
}

fn compact_error_body(body: &str) -> String {
    let compact = body.split_whitespace().collect::<Vec<_>>().join(" ");
    let truncated = compact.chars().take(500).collect::<String>();
    if truncated.len() < compact.len() {
        format!("{truncated}...")
    } else {
        compact
    }
}

fn verify_translation_response(
    items: &[ActiveTextItem],
    ai_result: &str,
    custom_rules: &[PlaceholderRule],
    text_rules: &TextRuleOptions,
    residual_rules: &BTreeMap<String, JapaneseResidualRuleRecord>,
) -> std::result::Result<Vec<TranslationItemRecord>, Vec<TranslationErrorItemRecord>> {
    let width_pattern = compile_line_width_pattern(text_rules).map_err(|error| {
        items
            .iter()
            .map(|item| {
                translation_error(
                    item,
                    Vec::new(),
                    "文本规则配置不可用",
                    vec![error.to_string()],
                    ai_result,
                )
            })
            .collect::<Vec<_>>()
    })?;
    let response_items = match parse_translation_response(ai_result) {
        Ok(response_items) => response_items,
        Err(error) => {
            return Err(items
                .iter()
                .map(|item| {
                    translation_error(
                        item,
                        Vec::new(),
                        "模型返回不可解析",
                        vec![format!("模型返回无法解析为 JSON 数组: {error}")],
                        ai_result,
                    )
                })
                .collect());
        }
    };
    let translation_map = match build_translation_map(response_items, items) {
        Ok(map) => map,
        Err(error) => {
            return Err(items
                .iter()
                .map(|item| {
                    translation_error(
                        item,
                        Vec::new(),
                        "模型返回不可解析",
                        vec![error.clone()],
                        ai_result,
                    )
                })
                .collect());
        }
    };
    let mut right_items = Vec::new();
    let mut error_items = Vec::new();
    for item in items {
        let Some(model_lines) = translation_map.get(&item.location_path) else {
            error_items.push(translation_error(
                item,
                Vec::new(),
                "AI漏翻",
                vec![format!("AI漏翻: 未找到键 {}", item.location_path)],
                ai_result,
            ));
            continue;
        };
        if model_lines.is_empty() || !model_lines.iter().any(|line| !line.trim().is_empty()) {
            error_items.push(translation_error(
                item,
                model_lines.clone(),
                "AI漏翻",
                vec!["AI漏翻: 模型返回空译文".to_string()],
                ai_result,
            ));
            continue;
        }
        match verify_single_item(
            item,
            model_lines,
            custom_rules,
            text_rules,
            &width_pattern,
            residual_rules.get(&item.location_path),
        ) {
            Ok(record) => right_items.push(record),
            Err(error_item) => error_items.push(TranslationErrorItemRecord {
                model_response: ai_result.to_string(),
                ..*error_item
            }),
        }
    }
    if error_items.is_empty() {
        Ok(right_items)
    } else {
        Err(error_items)
    }
}

fn parse_translation_response(ai_result: &str) -> Result<Vec<TranslationResponseItem>> {
    let mut last_error = match serde_json::from_str::<Vec<TranslationResponseItem>>(ai_result) {
        Ok(items) => return Ok(items),
        Err(error) => error,
    };
    for candidate in translation_response_json_candidates(ai_result) {
        match serde_json::from_str::<Vec<TranslationResponseItem>>(&candidate) {
            Ok(items) => return Ok(items),
            Err(error) => last_error = error,
        }
        let Ok(repaired) = jsonrepair_rs::jsonrepair(&candidate) else {
            continue;
        };
        match serde_json::from_str::<Vec<TranslationResponseItem>>(&repaired) {
            Ok(items) => return Ok(items),
            Err(error) => last_error = error,
        }
    }
    Err(AttMzError::Json {
        context: "模型返回正文译文".to_string(),
        source: last_error,
    })
}

fn translation_response_json_candidates(ai_result: &str) -> Vec<String> {
    let mut candidates = Vec::new();
    push_candidate(ai_result.trim(), &mut candidates);
    if let Some(stripped) = strip_markdown_json_fence(ai_result) {
        push_candidate(stripped.trim(), &mut candidates);
    }
    if let Some(array_text) = extract_outer_json_array(ai_result) {
        push_candidate(array_text, &mut candidates);
    }
    candidates
}

fn strip_markdown_json_fence(text: &str) -> Option<&str> {
    let trimmed = text.trim();
    let without_opening = trimmed.strip_prefix("```")?;
    let content_start = without_opening
        .find('\n')
        .map(|index| index + 1)
        .unwrap_or(0);
    let content = &without_opening[content_start..];
    let closing_index = content.rfind("```")?;
    Some(&content[..closing_index])
}

fn extract_outer_json_array(text: &str) -> Option<&str> {
    let start_index = text.find('[')?;
    let end_index = text.rfind(']')?;
    (end_index > start_index).then_some(&text[start_index..=end_index])
}

fn push_candidate(candidate: &str, candidates: &mut Vec<String>) {
    if candidate.is_empty() || candidates.iter().any(|value| value == candidate) {
        return;
    }
    candidates.push(candidate.to_string());
}

fn build_translation_map(
    response_items: Vec<TranslationResponseItem>,
    items: &[ActiveTextItem],
) -> std::result::Result<BTreeMap<String, Vec<String>>, String> {
    let valid_ids = items
        .iter()
        .map(|item| item.location_path.clone())
        .collect::<BTreeSet<_>>();
    let mut translation_map = BTreeMap::new();
    for response_item in response_items {
        if !valid_ids.contains(&response_item.id) {
            continue;
        }
        if translation_map
            .insert(response_item.id.clone(), response_item.translation_lines)
            .is_some()
        {
            return Err(format!("模型返回重复 ID: {}", response_item.id));
        }
    }
    Ok(translation_map)
}

fn verify_single_item(
    item: &ActiveTextItem,
    model_lines: &[String],
    custom_rules: &[PlaceholderRule],
    text_rules: &TextRuleOptions,
    width_pattern: &regex::Regex,
    residual_rule: Option<&JapaneseResidualRuleRecord>,
) -> std::result::Result<TranslationItemRecord, Box<TranslationErrorItemRecord>> {
    if item.item_type == "short_text" && model_lines.len() != 1 {
        return Err(Box::new(translation_error(
            item,
            model_lines.to_vec(),
            "文本结构不匹配",
            vec![format!(
                "单字段文本必须只提供 1 条中文译文行，当前提供 {} 条",
                model_lines.len()
            )],
            "",
        )));
    }
    if item.item_type == "array" && model_lines.len() != item.original_lines.len() {
        return Err(Box::new(translation_error(
            item,
            model_lines.to_vec(),
            "选项行数不匹配",
            vec![format!(
                "选项行数不匹配: 期望 {} 行, 实际 {} 行",
                item.original_lines.len(),
                model_lines.len()
            )],
            "",
        )));
    }
    let normalized_lines = normalize_manual_translation_lines_with_pattern(
        item,
        model_lines,
        text_rules,
        width_pattern,
    )
    .map_err(|error| {
        Box::new(translation_error(
            item,
            model_lines.to_vec(),
            "文本结构不匹配",
            vec![error.to_string()],
            "",
        ))
    })?;
    let context =
        build_placeholder_context(custom_rules, &item.original_lines).map_err(|error| {
            Box::new(translation_error(
                item,
                normalized_lines.clone(),
                "控制符不匹配",
                vec![error.to_string()],
                "",
            ))
        })?;
    let masked_lines = mask_translation_controls(custom_rules, &context, &normalized_lines)
        .map_err(|error| {
            Box::new(translation_error(
                item,
                normalized_lines.clone(),
                "控制符不匹配",
                vec![error.to_string()],
                "",
            ))
        })?;
    validate_translation_text_structure(
        item,
        &context.text_for_model_lines,
        &normalized_lines,
        &masked_lines,
    )
    .map_err(|error| {
        Box::new(translation_error(
            item,
            normalized_lines.clone(),
            "文本结构不匹配",
            error.to_string().split(";\n").map(str::to_string).collect(),
            "",
        ))
    })?;
    verify_placeholder_counts(&context, &masked_lines).map_err(|error| {
        Box::new(translation_error(
            item,
            masked_lines.clone(),
            "控制符不匹配",
            error.to_string().split(";\n").map(str::to_string).collect(),
            "",
        ))
    })?;
    let restored_lines = restore_placeholder_lines(&context, &masked_lines).map_err(|error| {
        Box::new(translation_error(
            item,
            masked_lines.clone(),
            "控制符不匹配",
            vec![error.to_string()],
            "",
        ))
    })?;
    check_japanese_residual_for_item(item, &masked_lines, residual_rule, text_rules).map_err(
        |error| {
            Box::new(translation_error(
                item,
                restored_lines.clone(),
                "日文残留",
                vec![error.to_string()],
                "",
            ))
        },
    )?;
    Ok(TranslationItemRecord {
        location_path: item.location_path.clone(),
        item_type: item.item_type.clone(),
        role: item.role.clone(),
        original_lines: item.original_lines.clone(),
        source_line_paths: item.source_line_paths.clone(),
        translation_lines: restored_lines,
    })
}

fn translation_error(
    item: &ActiveTextItem,
    translation_lines: Vec<String>,
    error_type: &str,
    error_detail: Vec<String>,
    model_response: &str,
) -> TranslationErrorItemRecord {
    TranslationErrorItemRecord {
        location_path: item.location_path.clone(),
        item_type: item.item_type.clone(),
        role: item.role.clone(),
        original_lines: item.original_lines.clone(),
        translation_lines,
        error_type: error_type.to_string(),
        error_detail,
        model_response: model_response.to_string(),
    }
}

fn expand_success_items(
    items: Vec<TranslationItemRecord>,
    duplicate_items: &mut HashMap<TranslationCacheKey, Vec<ActiveTextItem>>,
) -> Vec<TranslationItemRecord> {
    let mut expanded_items = Vec::new();
    for item in items {
        let key = TranslationCacheKey {
            original_lines: item.original_lines.clone(),
            item_type: item.item_type.clone(),
            role: item.role.clone(),
        };
        let duplicates = duplicate_items.remove(&key).unwrap_or_default();
        let translation_lines = item.translation_lines.clone();
        expanded_items.push(item);
        for duplicate in duplicates {
            expanded_items.push(TranslationItemRecord {
                location_path: duplicate.location_path,
                item_type: duplicate.item_type,
                role: duplicate.role,
                original_lines: duplicate.original_lines,
                source_line_paths: duplicate.source_line_paths,
                translation_lines: translation_lines.clone(),
            });
        }
    }
    expanded_items
}

fn expand_error_items(
    items: Vec<TranslationErrorItemRecord>,
    duplicate_items: &mut HashMap<TranslationCacheKey, Vec<ActiveTextItem>>,
) -> Vec<TranslationErrorItemRecord> {
    let mut expanded_items = Vec::new();
    for item in items {
        let key = TranslationCacheKey {
            original_lines: item.original_lines.clone(),
            item_type: item.item_type.clone(),
            role: item.role.clone(),
        };
        let duplicates = duplicate_items.remove(&key).unwrap_or_default();
        let translation_lines = item.translation_lines.clone();
        let error_type = item.error_type.clone();
        let error_detail = item.error_detail.clone();
        let model_response = item.model_response.clone();
        expanded_items.push(item);
        for duplicate in duplicates {
            expanded_items.push(TranslationErrorItemRecord {
                location_path: duplicate.location_path,
                item_type: duplicate.item_type,
                role: duplicate.role,
                original_lines: duplicate.original_lines,
                translation_lines: translation_lines.clone(),
                error_type: error_type.clone(),
                error_detail: error_detail.clone(),
                model_response: model_response.clone(),
            });
        }
    }
    expanded_items
}

#[derive(Debug, Clone)]
struct TranslateSummary {
    run_id: Option<String>,
    total_extracted: usize,
    pending_count: usize,
    deduplicated_count: usize,
    batch_count: usize,
    success_count: usize,
    quality_error_count: usize,
    llm_failure_count: usize,
}

fn translate_summary_report(summary: TranslateSummary) -> AgentReport {
    let mut warnings = Vec::new();
    if summary.quality_error_count > 0 {
        warnings.push(issue(
            "translation_quality_errors",
            format!(
                "本轮翻译有 {} 条模型翻了但项目检查没通过的译文；可以继续运行 translate，或导出手动填写译文表修复",
                summary.quality_error_count
            ),
        ));
    }
    AgentReport::from_parts(
        Vec::new(),
        warnings,
        translate_summary_fields(summary),
        Map::new(),
    )
}

#[allow(clippy::too_many_arguments)]
fn blocked_translate_report(
    run_id: Option<String>,
    total_extracted: usize,
    pending_count: usize,
    deduplicated_count: usize,
    batch_count: usize,
    success_count: usize,
    quality_error_count: usize,
    llm_failure_count: usize,
    blocked_reason: impl AsRef<str>,
) -> AgentReport {
    let summary = TranslateSummary {
        run_id,
        total_extracted,
        pending_count,
        deduplicated_count,
        batch_count,
        success_count,
        quality_error_count,
        llm_failure_count,
    };
    AgentReport::from_parts(
        vec![issue(
            "translation_blocked",
            format!("正文翻译不能继续：{}", blocked_reason.as_ref()),
        )],
        Vec::new(),
        translate_summary_fields(summary),
        Map::new(),
    )
}

fn translate_summary_fields(summary: TranslateSummary) -> Map<String, Value> {
    let mut fields = Map::new();
    fields.insert("run_id".to_string(), json!(summary.run_id));
    fields.insert(
        "total_extracted_items".to_string(),
        json!(summary.total_extracted),
    );
    fields.insert("pending_count".to_string(), json!(summary.pending_count));
    fields.insert(
        "deduplicated_count".to_string(),
        json!(summary.deduplicated_count),
    );
    fields.insert("batch_count".to_string(), json!(summary.batch_count));
    fields.insert("success_count".to_string(), json!(summary.success_count));
    fields.insert(
        "quality_error_count".to_string(),
        json!(summary.quality_error_count),
    );
    fields.insert(
        "llm_failure_count".to_string(),
        json!(summary.llm_failure_count),
    );
    fields
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::Arc;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::thread;

    #[test]
    fn model_translation_restores_original_control_codes() {
        let item = ActiveTextItem {
            location_path: "Map001.json/1/0/0".to_string(),
            item_type: "short_text".to_string(),
            role: None,
            display_name: None,
            original_lines: vec![r"こんにちは\V[1]".to_string()],
            source_line_paths: Vec::new(),
        };

        let options = TextRuleOptions::default();
        let width_pattern = compile_line_width_pattern(&options).expect("行宽正则应可编译");
        let record = verify_single_item(
            &item,
            &["你好[RMMZ_VARIABLE_1]".to_string()],
            &[],
            &options,
            &width_pattern,
            None,
        )
        .expect("模型译文应通过校验");

        assert_eq!(record.translation_lines, vec![r"你好\V[1]".to_string()]);
    }

    #[test]
    fn translation_response_repairs_common_model_json_noise() {
        let repaired = parse_translation_response(
            r#"
```json
[
  {id:'Map001.json/1/0/0', translation_lines:['你好',],},
]
```
"#,
        )
        .expect("常见模型 JSON 噪音应可修复");

        assert_eq!(repaired.len(), 1);
        assert_eq!(repaired[0].id, "Map001.json/1/0/0");
        assert_eq!(repaired[0].translation_lines, vec!["你好".to_string()]);
    }

    #[test]
    fn translation_response_extracts_json_array_from_surrounding_text() {
        let repaired = parse_translation_response(
            r#"以下是结果：
[
  {"id":"Map001.json/1/0/0","translation_lines":["你好"]}
]
请查收。"#,
        )
        .expect("前后解释文字不应导致整批失败");

        assert_eq!(repaired[0].translation_lines, vec!["你好".to_string()]);
    }

    #[test]
    fn prompt_context_uses_map_display_name_and_role_terms() {
        let items = vec![ActiveTextItem {
            location_path: "Map001.json/1/0/0".to_string(),
            item_type: "long_text".to_string(),
            role: Some("アリス".to_string()),
            display_name: Some("始まりの町".to_string()),
            original_lines: vec!["こんにちは".to_string()],
            source_line_paths: Vec::new(),
        }];
        let glossary = BTreeMap::from([
            ("アリス".to_string(), "爱丽丝".to_string()),
            ("始まりの町".to_string(), "起始之镇".to_string()),
            ("未出现".to_string(), "不应出现".to_string()),
        ]);
        let terminology_index = TerminologyPromptIndex::from_glossary(&glossary, &BTreeMap::new());

        let batch = build_translation_batch(
            "系统提示词",
            items,
            vec!["正文".to_string()],
            &terminology_index,
        );
        let user_prompt = &batch.messages[1].text;

        assert!(user_prompt.contains("地图：始まりの町"));
        assert!(user_prompt.contains("アリス => 爱丽丝"));
        assert!(user_prompt.contains("始まりの町 => 起始之镇"));
        assert!(!user_prompt.contains("未出现 => 不应出现"));
    }

    #[test]
    fn prompt_context_uses_owner_and_system_terms() {
        let items = vec![
            ActiveTextItem {
                location_path: "Actors.json/1/profile".to_string(),
                item_type: "short_text".to_string(),
                role: None,
                display_name: None,
                original_lines: vec!["よろしく".to_string()],
                source_line_paths: Vec::new(),
            },
            ActiveTextItem {
                location_path: "System.json/terms/elements/1".to_string(),
                item_type: "short_text".to_string(),
                role: None,
                display_name: None,
                original_lines: vec!["属性説明".to_string()],
                source_line_paths: Vec::new(),
            },
        ];
        let glossary = BTreeMap::from([
            ("勇者".to_string(), "勇者".to_string()),
            ("雷".to_string(), "雷".to_string()),
            ("未关联".to_string(), "不应出现".to_string()),
        ]);
        let data_files = BTreeMap::from([
            (
                "Actors.json".to_string(),
                json!([null, {"id": 1, "name": "勇者", "nickname": "希望"}]),
            ),
            (
                "System.json".to_string(),
                json!({"elements": ["", "雷"], "skillTypes": [], "weaponTypes": [], "armorTypes": [], "equipTypes": []}),
            ),
        ]);
        let terminology_index = TerminologyPromptIndex::from_glossary(&glossary, &data_files);

        let batch = build_translation_batch(
            "系统提示词",
            items,
            vec!["正文".to_string()],
            &terminology_index,
        );
        let user_prompt = &batch.messages[1].text;

        assert!(user_prompt.contains("勇者 => 勇者"));
        assert!(user_prompt.contains("雷 => 雷"));
        assert!(!user_prompt.contains("未关联 => 不应出现"));
    }

    #[test]
    fn translation_scheduler_runs_requests_concurrently() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        let game_dir = temp.path().join("game");
        create_translate_test_game(&game_dir);
        let registry = GameRegistry::new(temp.path().join("db"));
        let game_record = registry.register_game(&game_dir).expect("游戏应注册成功");
        let mut run_record = registry
            .start_translation_run(&game_record.game_title, 2, 2, 2, 2)
            .expect("运行记录应创建成功");
        let (base_url, max_concurrent, server_handle) = start_parallel_llm_server(2);
        let settings = RuntimeSettings {
            llm: crate::config::LlmOptions {
                base_url,
                api_key: "TEST".to_string(),
                model: "demo".to_string(),
                timeout_seconds: 5,
                request_body_extra: Map::new(),
            },
            translation_context: crate::config::TranslationContextOptions {
                token_size: 1000,
                factor: 1.0,
                max_command_items: 0,
            },
            text_translation: crate::config::TextTranslationOptions {
                worker_count: 2,
                rpm: None,
                retry_count: 0,
                retry_delay: 0,
                system_prompt: String::new(),
            },
            text_rules: TextRuleOptions::default(),
            source_text_required_pattern: crate::config::DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN
                .to_string(),
            replacement_font_path: None,
        };
        let batches = vec![
            test_batch("Map001.json/1/0/0", "こんにちはA"),
            test_batch("Map001.json/1/0/1", "こんにちはB"),
        ];
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .enable_all()
            .build()
            .expect("测试运行时应创建成功");

        let progress = runtime
            .block_on(process_translation_batches_concurrently(
                TranslationProcessingInput {
                    registry: &registry,
                    game_title: &game_record.game_title,
                    run_record: &mut run_record,
                    batches,
                    duplicate_items: HashMap::new(),
                    settings: &settings,
                    custom_rules: Vec::new(),
                    residual_rules: BTreeMap::new(),
                    limits: &TranslationRunLimits::default(),
                    started_at: Instant::now(),
                },
            ))
            .expect("并发翻译调度应完成");
        server_handle.join().expect("测试服务应退出");

        assert_eq!(progress.success_count, 2);
        assert!(max_concurrent.load(Ordering::SeqCst) >= 2);
    }

    fn test_batch(location_path: &str, original_text: &str) -> TranslationBatch {
        TranslationBatch {
            items: vec![ActiveTextItem {
                location_path: location_path.to_string(),
                item_type: "short_text".to_string(),
                role: None,
                display_name: None,
                original_lines: vec![original_text.to_string()],
                source_line_paths: Vec::new(),
            }],
            messages: vec![ChatMessage {
                role: "user",
                text: location_path.to_string(),
            }],
        }
    }

    fn start_parallel_llm_server(
        expected_requests: usize,
    ) -> (String, Arc<AtomicUsize>, thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").expect("测试服务端口应可绑定");
        let address = listener.local_addr().expect("测试服务地址应可读取");
        let current = Arc::new(AtomicUsize::new(0));
        let max_concurrent = Arc::new(AtomicUsize::new(0));
        let server_current = Arc::clone(&current);
        let server_max = Arc::clone(&max_concurrent);
        let handle = thread::spawn(move || {
            let mut workers = Vec::new();
            for _ in 0..expected_requests {
                let (mut stream, _) = listener.accept().expect("测试请求应可接收");
                let current = Arc::clone(&server_current);
                let max_concurrent = Arc::clone(&server_max);
                workers.push(thread::spawn(move || {
                    let active = current.fetch_add(1, Ordering::SeqCst) + 1;
                    update_max_concurrent(&max_concurrent, active);
                    thread::sleep(Duration::from_millis(250));
                    let mut buffer = [0_u8; 4096];
                    let _ = stream.read(&mut buffer);
                    let content = serde_json::to_string(&json!([
                        {"id": "Map001.json/1/0/0", "translation_lines": ["你好A"]},
                        {"id": "Map001.json/1/0/1", "translation_lines": ["你好B"]}
                    ]))
                    .expect("响应内容应可序列化");
                    let body = json!({
                        "choices": [{"message": {"content": content}}]
                    })
                    .to_string();
                    let response = format!(
                        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                        body.len(),
                        body
                    );
                    stream
                        .write_all(response.as_bytes())
                        .expect("测试响应应可写入");
                    current.fetch_sub(1, Ordering::SeqCst);
                }));
            }
            for worker in workers {
                worker.join().expect("测试请求线程应退出");
            }
        });
        (format!("http://{address}"), max_concurrent, handle)
    }

    fn update_max_concurrent(max_concurrent: &AtomicUsize, active: usize) {
        let mut observed = max_concurrent.load(Ordering::SeqCst);
        while active > observed {
            match max_concurrent.compare_exchange(
                observed,
                active,
                Ordering::SeqCst,
                Ordering::SeqCst,
            ) {
                Ok(_) => break,
                Err(next) => observed = next,
            }
        }
    }

    fn create_translate_test_game(game_dir: &std::path::Path) {
        std::fs::create_dir_all(game_dir.join("data")).expect("data 目录应创建成功");
        std::fs::create_dir_all(game_dir.join("js")).expect("js 目录应创建成功");
        std::fs::write(
            game_dir.join("package.json"),
            r#"{"window":{"title":"并发测试"}}"#,
        )
        .expect("package.json 应写入成功");
        std::fs::write(game_dir.join("data/System.json"), "{}\n").expect("System.json 应写入成功");
        std::fs::write(game_dir.join("data/CommonEvents.json"), "[null]\n")
            .expect("CommonEvents.json 应写入成功");
        std::fs::write(game_dir.join("data/Troops.json"), "[null]\n")
            .expect("Troops.json 应写入成功");
        std::fs::write(game_dir.join("js/plugins.js"), "var $plugins = [];\n")
            .expect("plugins.js 应写入成功");
    }
}
