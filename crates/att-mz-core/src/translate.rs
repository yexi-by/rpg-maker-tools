//! 正文翻译运行与模型请求。
//!
//! 本模块负责把当前游戏可提取正文组装成 OpenAI 兼容 Chat Completions
//! 请求，校验模型返回，并把成功译文或质量问题写入数据库。实现保持长期数据
//! 结构与 Python 版本兼容；内部调度采用稳定顺序执行，便于先完成单文件 CLI
//! 的可靠迁移。

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::thread;
use std::time::{Duration, Instant};

use reqwest::StatusCode;
use reqwest::blocking::Client;
use serde::Deserialize;
use serde_json::{Map, Value, json};

use crate::config::{RuntimeSettings, TextRuleOptions};
use crate::db::{
    JapaneseResidualRuleRecord, LlmFailureRecord, TranslationErrorItemRecord, TranslationItemRecord,
};
use crate::error::{AttMzError, Result};
use crate::placeholder::{
    PlaceholderRule, build_placeholder_context, mask_translation_controls,
    restore_placeholder_lines, verify_placeholder_counts,
};
use crate::placeholder_scan::ActiveTextItem;
use crate::report::{AgentReport, issue};
use crate::translation_state::{
    check_japanese_residual_for_item, load_active_translation_items,
    normalize_manual_translation_lines, validate_translation_text_structure,
};
use crate::{GameRecord, GameRegistry};

const SCENE_PROMPT_TEMPLATE: &str = "# 场景\n\n地图：";
const BODY_PROMPT_HEADER: &str = "# 正文";
const NARRATION_ROLE: &str = "旁白";

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
    /// 保留 CLI 兼容参数；当前稳定顺序执行会在首个模型故障处停止。
    pub stop_on_rate_limit_count: Option<usize>,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
struct TranslationCacheKey {
    original_lines: Vec<String>,
    item_type: String,
    role: Option<String>,
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

    let (deduplicated_items, mut duplicate_items) = deduplicate_items(limited_items);
    let glossary = registry
        .read_terminology_glossary(&game_record.game_title)?
        .unwrap_or_default();
    let mut batches =
        build_translation_batches(&deduplicated_items, settings, &custom_rules, &glossary)?;
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
    let mut success_count = 0usize;
    let mut quality_error_count = 0usize;
    let mut last_request_at: Option<Instant> = None;

    for batch in batches {
        if let Some(limit_seconds) = limits.time_limit_seconds
            && started_at.elapsed() >= Duration::from_secs(limit_seconds)
        {
            run_record.status = "blocked".to_string();
            run_record.success_count = success_count;
            run_record.quality_error_count = quality_error_count;
            run_record.stop_reason = format!("达到本轮翻译时间上限: {limit_seconds} 秒");
            run_record.last_error = run_record.stop_reason.clone();
            registry.write_translation_run(&game_record.game_title, &run_record, true)?;
            return Ok(blocked_translate_report(
                Some(run_record.run_id),
                total_extracted,
                pending_count,
                deduplicated_count,
                run_record.batch_count,
                success_count,
                quality_error_count,
                &run_record.stop_reason,
            ));
        }
        wait_for_rpm(settings.text_translation.rpm, &mut last_request_at);
        let response = match request_with_retry(settings, &batch.messages) {
            Ok(response) => response,
            Err(failure) => {
                let failure_record = LlmFailureRecord {
                    run_id: run_record.run_id.clone(),
                    category: failure.category,
                    error_type: failure.error_type,
                    error_message: failure.message.clone(),
                    retryable: failure.retryable,
                    attempt_count: failure.attempt_count,
                };
                registry.write_llm_failure(&game_record.game_title, &failure_record)?;
                run_record.status = "blocked".to_string();
                run_record.success_count = success_count;
                run_record.quality_error_count = quality_error_count;
                run_record.llm_failure_count = 1;
                run_record.stop_reason = format!("模型请求失败: {}", failure.message);
                run_record.last_error = failure.message;
                registry.write_translation_run(&game_record.game_title, &run_record, true)?;
                return Ok(blocked_translate_report(
                    Some(run_record.run_id),
                    total_extracted,
                    pending_count,
                    deduplicated_count,
                    run_record.batch_count,
                    success_count,
                    quality_error_count,
                    &run_record.stop_reason,
                ));
            }
        };

        match verify_translation_response(
            &batch.items,
            &response,
            &custom_rules,
            &settings.text_rules,
            registry,
            game_record,
        ) {
            Ok(items) => {
                let expanded_items = expand_success_items(items, &mut duplicate_items);
                success_count += expanded_items.len();
                registry.write_translation_items(&game_record.game_title, &expanded_items)?;
            }
            Err(errors) => {
                let expanded_errors = expand_error_items(errors, &mut duplicate_items);
                quality_error_count += expanded_errors.len();
                registry.write_translation_quality_errors(
                    &game_record.game_title,
                    &run_record.run_id,
                    &expanded_errors,
                )?;
            }
        }

        run_record.success_count = success_count;
        run_record.quality_error_count = quality_error_count;
        registry.write_translation_run(&game_record.game_title, &run_record, false)?;

        if let Some(stop_on_error_rate) = limits.stop_on_error_rate {
            let processed_count = success_count + quality_error_count;
            if processed_count > 0
                && (quality_error_count as f64 / processed_count as f64) >= stop_on_error_rate
            {
                run_record.status = "blocked".to_string();
                run_record.stop_reason =
                    format!("检查没通过的译文比例达到停止阈值: {stop_on_error_rate}");
                run_record.last_error = run_record.stop_reason.clone();
                registry.write_translation_run(&game_record.game_title, &run_record, true)?;
                return Ok(blocked_translate_report(
                    Some(run_record.run_id),
                    total_extracted,
                    pending_count,
                    deduplicated_count,
                    run_record.batch_count,
                    success_count,
                    quality_error_count,
                    &run_record.stop_reason,
                ));
            }
        }
    }

    run_record.status = if quality_error_count == 0 {
        "completed".to_string()
    } else {
        "blocked".to_string()
    };
    run_record.success_count = success_count;
    run_record.quality_error_count = quality_error_count;
    run_record.stop_reason = if quality_error_count == 0 {
        String::new()
    } else {
        "存在模型翻了但项目检查没通过的译文".to_string()
    };
    run_record.last_error = if quality_error_count == 0 {
        String::new()
    } else {
        "quality_errors".to_string()
    };
    registry.write_translation_run(&game_record.game_title, &run_record, true)?;

    Ok(translate_summary_report(TranslateSummary {
        run_id: Some(run_record.run_id),
        total_extracted,
        pending_count,
        deduplicated_count,
        batch_count: run_record.batch_count,
        success_count,
        quality_error_count,
        llm_failure_count: 0,
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
    glossary: &BTreeMap<String, String>,
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
                glossary,
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
            glossary,
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
            glossary,
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
    glossary: &BTreeMap<String, String>,
) -> TranslationBatch {
    let mut user_sections = vec![SCENE_PROMPT_TEMPLATE.to_string()];
    let terminology_section = format_terminology_section(&current_items, glossary);
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

fn format_terminology_section(
    items: &[ActiveTextItem],
    glossary: &BTreeMap<String, String>,
) -> String {
    if glossary.is_empty() {
        return String::new();
    }
    let mut selected_entries = Vec::new();
    let mut seen = BTreeSet::new();
    let combined_text = items
        .iter()
        .flat_map(|item| item.original_lines.iter())
        .cloned()
        .collect::<Vec<_>>()
        .join("\n");
    for (source_text, translated_text) in glossary {
        if source_text.trim().is_empty() || translated_text.trim().is_empty() {
            continue;
        }
        if !combined_text.contains(source_text) {
            continue;
        }
        let entry = format!("{source_text} => {translated_text}");
        if seen.insert(entry.clone()) {
            selected_entries.push(entry);
        }
    }
    if selected_entries.is_empty() {
        String::new()
    } else {
        let mut lines = vec!["[[术语表]]".to_string()];
        lines.extend(selected_entries);
        lines.join("\n")
    }
}

fn wait_for_rpm(rpm: Option<usize>, last_request_at: &mut Option<Instant>) {
    let Some(rpm) = rpm else {
        *last_request_at = Some(Instant::now());
        return;
    };
    if rpm == 0 {
        *last_request_at = Some(Instant::now());
        return;
    }
    let interval = Duration::from_secs_f64(60.0 / rpm as f64);
    if let Some(last_started) = last_request_at {
        let elapsed = last_started.elapsed();
        if elapsed < interval {
            thread::sleep(interval - elapsed);
        }
    }
    *last_request_at = Some(Instant::now());
}

fn request_with_retry(
    settings: &RuntimeSettings,
    messages: &[ChatMessage],
) -> std::result::Result<String, LlmFailureInfo> {
    let max_attempts = settings.text_translation.retry_count + 1;
    let mut last_failure: Option<LlmFailureInfo> = None;
    for attempt_index in 1..=max_attempts {
        match request_once(settings, messages) {
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
                    thread::sleep(Duration::from_secs(delay_seconds));
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

fn request_once(
    settings: &RuntimeSettings,
    messages: &[ChatMessage],
) -> std::result::Result<String, LlmFailureInfo> {
    let client = Client::builder()
        .timeout(Duration::from_secs(settings.llm.timeout_seconds))
        .build()
        .map_err(|error| classify_reqwest_error(error, 0))?;
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
        .map_err(|error| classify_reqwest_error(error, 0))?;
    let status = response.status();
    let response_text = response
        .text()
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
    registry: &GameRegistry,
    game_record: &GameRecord,
) -> std::result::Result<Vec<TranslationItemRecord>, Vec<TranslationErrorItemRecord>> {
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
    let residual_rules = registry
        .read_japanese_residual_rules(&game_record.game_title)
        .map(|records| {
            records
                .into_iter()
                .map(|record| (record.location_path.clone(), record))
                .collect::<BTreeMap<_, _>>()
        });
    let residual_rules = match residual_rules {
        Ok(rules) => rules,
        Err(error) => {
            return Err(items
                .iter()
                .map(|item| {
                    translation_error(
                        item,
                        Vec::new(),
                        "模型返回不可解析",
                        vec![format!("读取日文残留规则失败: {error}")],
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
    match serde_json::from_str::<Vec<TranslationResponseItem>>(ai_result) {
        Ok(items) => Ok(items),
        Err(first_error) => {
            let Some(start_index) = ai_result.find('[') else {
                return Err(AttMzError::Json {
                    context: "模型返回正文译文".to_string(),
                    source: first_error,
                });
            };
            let Some(end_index) = ai_result.rfind(']') else {
                return Err(AttMzError::Json {
                    context: "模型返回正文译文".to_string(),
                    source: first_error,
                });
            };
            if end_index <= start_index {
                return Err(AttMzError::Json {
                    context: "模型返回正文译文".to_string(),
                    source: first_error,
                });
            }
            serde_json::from_str::<Vec<TranslationResponseItem>>(
                &ai_result[start_index..=end_index],
            )
            .map_err(|source| AttMzError::Json {
                context: "模型返回正文译文".to_string(),
                source,
            })
        }
    }
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
    let normalized_lines = normalize_manual_translation_lines(item, model_lines, text_rules)
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
    check_japanese_residual_for_item(item, &restored_lines, residual_rule, text_rules).map_err(
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
        llm_failure_count: 0,
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

    #[test]
    fn model_translation_restores_original_control_codes() {
        let item = ActiveTextItem {
            location_path: "Map001.json/1/0/0".to_string(),
            item_type: "short_text".to_string(),
            role: None,
            original_lines: vec![r"こんにちは\V[1]".to_string()],
            source_line_paths: Vec::new(),
        };

        let record = verify_single_item(
            &item,
            &["你好[RMMZ_VARIABLE_1]".to_string()],
            &[],
            &TextRuleOptions::default(),
            None,
        )
        .expect("模型译文应通过校验");

        assert_eq!(record.translation_lines, vec![r"你好\V[1]".to_string()]);
    }
}
