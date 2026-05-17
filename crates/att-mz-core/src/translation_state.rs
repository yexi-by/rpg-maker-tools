//! 正文翻译状态与手动填写译文导出。
//!
//! 本模块只处理当前数据库已有状态和当前可提取正文，不调用模型，也不写入
//! 游戏文件。它为外部 Agent 提供“还没成功保存译文的文本表”和最近运行状态。

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;

use regex::Regex;
use serde_json::{Map, Value, json};

use crate::config::TextRuleOptions;
use crate::db::{
    JapaneseResidualRuleRecord, TranslationErrorItemRecord, TranslationItemRecord,
    TranslationQualityErrorSummary,
};
use crate::error::{AttMzError, Result};
use crate::native_core;
use crate::placeholder::{
    PlaceholderRule, build_placeholder_context, collect_placeholder_tokens,
    collect_unprotected_control_sequences, mask_translation_controls, restore_placeholder_lines,
    verify_placeholder_counts,
};
use crate::placeholder_scan::{
    ActiveTextExtractionInput, ActiveTextItem, extract_active_text_items,
};
use crate::plugin_rules::{PluginRuleRecord, build_plugin_hash};
use crate::report::{AgentReport, issue};
use crate::rmmz::{read_data_json_files, read_event_command_snapshots, read_plugins_json};
use crate::{GameRecord, GameRegistry};

const MANUAL_FILL_NOTE: &str = "只改 translation_lines；text_for_model_lines 只供对照。translation_lines 必须使用 original_lines 里的游戏原始控制符，不得保留 [RMMZ_...] 或 [CUSTOM_...]。";
const WRAPPING_CONTINUATION_INDENT: &str = "　";
const TRANSLATED_WRAPPING_LEFT_CHARS: &[char] =
    &['“', '‘', '「', '『', '《', '〈', '（', '(', '"', '\'', '＂'];
const TRANSLATED_WRAPPING_RIGHT_CHARS: &[char] =
    &['”', '’', '」', '』', '》', '〉', '）', ')', '"', '\'', '＂'];
const TRANSLATED_WRAPPING_QUOTE_PAIRS: &[(char, char)] = &[
    ('“', '”'),
    ('‘', '’'),
    ('"', '"'),
    ('\'', '\''),
    ('＂', '＂'),
    ('『', '』'),
    ('《', '》'),
    ('〈', '〉'),
];

#[derive(Debug, Clone)]
struct NativeQualityDetails {
    japanese_residual_items: Vec<Value>,
    text_structure_items: Vec<Value>,
    placeholder_risk_items: Vec<Value>,
    overwide_line_items: Vec<Value>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct BoundaryChar {
    line_index: usize,
    byte_index: usize,
    char_value: char,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct WrappingSpan {
    left: BoundaryChar,
    right: BoundaryChar,
    pair: (char, char),
}

/// 读取当前游戏实际会进入正文翻译流程的条目。
pub fn load_active_translation_items(
    registry: &GameRegistry,
    game_record: &GameRecord,
    source_text_required_pattern: &str,
    text_rules: &TextRuleOptions,
) -> Result<Vec<ActiveTextItem>> {
    let data_files = read_data_json_files(&game_record.game_path)?;
    let command_snapshots = read_event_command_snapshots(&game_record.game_path)?;
    let plugins = read_plugins_json(&game_record.game_path)?;
    let (plugin_rules, _stale_plugin_rule_count) =
        read_fresh_plugin_rules(registry, game_record, &plugins)?;
    let event_rules = registry.read_event_command_text_rules(&game_record.game_title)?;
    let note_rules = registry.read_note_tag_text_rules(&game_record.game_title)?;
    extract_active_text_items(ActiveTextExtractionInput {
        data_files: &data_files,
        command_snapshots: &command_snapshots,
        plugins: &plugins,
        plugin_rules: &plugin_rules,
        event_rules: &event_rules,
        note_rules: &note_rules,
        source_text_required_pattern,
        text_rules,
    })
}

/// 读取最新正文翻译运行状态，并补充当前还没成功保存译文的数量。
pub fn translation_status_report(
    registry: &GameRegistry,
    game_record: &GameRecord,
    source_text_required_pattern: &str,
    text_rules: &TextRuleOptions,
) -> Result<AgentReport> {
    let Some(latest_run) = registry.read_latest_translation_run(&game_record.game_title)? else {
        return Ok(AgentReport::from_parts(
            Vec::new(),
            vec![issue(
                "translation_run_missing",
                "当前游戏尚未产生正文翻译运行记录",
            )],
            Map::new(),
            Map::new(),
        ));
    };
    let active_items = load_active_translation_items(
        registry,
        game_record,
        source_text_required_pattern,
        text_rules,
    )?;
    let active_paths = active_items
        .iter()
        .map(|item| item.location_path.clone())
        .collect::<BTreeSet<_>>();
    let translated_paths = registry.read_translation_location_paths(&game_record.game_title)?;
    let current_pending_paths = active_paths
        .difference(&translated_paths)
        .cloned()
        .collect::<BTreeSet<_>>();
    let llm_failure_counts =
        registry.read_llm_failure_counts(&game_record.game_title, &latest_run.run_id)?;
    let llm_failure_count = llm_failure_counts.values().sum::<usize>();
    let quality_errors = registry
        .read_translation_quality_error_summaries(&game_record.game_title, &latest_run.run_id)?;
    let run_quality_error_count = quality_errors.len();
    let current_quality_errors = quality_errors
        .into_iter()
        .filter(|error| current_pending_paths.contains(&error.location_path))
        .collect::<Vec<_>>();

    let mut summary = Map::new();
    summary.insert("run_id".to_string(), json!(latest_run.run_id));
    summary.insert("status".to_string(), json!(latest_run.status));
    summary.insert(
        "total_extracted".to_string(),
        json!(latest_run.total_extracted),
    );
    summary.insert(
        "pending_count".to_string(),
        json!(current_pending_paths.len()),
    );
    summary.insert(
        "run_pending_count".to_string(),
        json!(latest_run.pending_count),
    );
    summary.insert(
        "translated_count".to_string(),
        json!(translated_paths.intersection(&active_paths).count()),
    );
    summary.insert("extractable_count".to_string(), json!(active_paths.len()));
    summary.insert(
        "deduplicated_count".to_string(),
        json!(latest_run.deduplicated_count),
    );
    summary.insert("batch_count".to_string(), json!(latest_run.batch_count));
    summary.insert("success_count".to_string(), json!(latest_run.success_count));
    summary.insert(
        "quality_error_count".to_string(),
        json!(current_quality_errors.len()),
    );
    summary.insert(
        "run_quality_error_count".to_string(),
        json!(run_quality_error_count),
    );
    summary.insert("llm_failure_count".to_string(), json!(llm_failure_count));
    summary.insert("stop_reason".to_string(), json!(latest_run.stop_reason));
    summary.insert("last_error".to_string(), json!(latest_run.last_error));

    let mut details = Map::new();
    details.insert("llm_failure_counts".to_string(), json!(llm_failure_counts));
    details.insert(
        "quality_error_counts".to_string(),
        json!(quality_error_counts(&current_quality_errors)),
    );
    Ok(AgentReport::from_parts(
        Vec::new(),
        Vec::new(),
        summary,
        details,
    ))
}

/// 导出还没成功保存译文的条目，供外部 Agent 手动填写中文译文行。
pub fn export_pending_translations_report(
    registry: &GameRegistry,
    game_record: &GameRecord,
    output_path: &Path,
    limit: Option<i64>,
    source_text_required_pattern: &str,
    text_rules: &TextRuleOptions,
) -> Result<AgentReport> {
    let custom_rules = registry.read_placeholder_rules(&game_record.game_title)?;
    let active_items = load_active_translation_items(
        registry,
        game_record,
        source_text_required_pattern,
        text_rules,
    )?;
    let translated_paths = registry.read_translation_location_paths(&game_record.game_title)?;
    let mut pending_items = active_items
        .into_iter()
        .filter(|item| !translated_paths.contains(&item.location_path))
        .collect::<Vec<_>>();
    if let Some(limit) = limit {
        let limit = usize::try_from(limit.max(0))
            .map_err(|error| AttMzError::InvalidConfig(format!("导出条目数量限制无效: {error}")))?;
        pending_items.truncate(limit);
    }

    let mut payload = Map::new();
    for item in &pending_items {
        payload.insert(
            item.location_path.clone(),
            manual_translation_template_entry(item, &custom_rules)?,
        );
    }
    write_json_file(output_path, &Value::Object(payload))?;

    let warnings = if pending_items.is_empty() {
        vec![issue("pending_empty", "当前没有需要手动填写译文的条目")]
    } else {
        Vec::new()
    };
    let mut summary = Map::new();
    summary.insert(
        "pending_exported_count".to_string(),
        json!(pending_items.len()),
    );
    summary.insert(
        "output".to_string(),
        json!(output_path.display().to_string()),
    );
    Ok(AgentReport::from_parts(
        Vec::new(),
        warnings,
        summary,
        Map::new(),
    ))
}

/// 生成当前游戏的翻译状态和质量风险报告。
pub fn quality_report(
    registry: &GameRegistry,
    game_record: &GameRecord,
    source_text_required_pattern: &str,
    text_rules: &TextRuleOptions,
) -> Result<AgentReport> {
    let data_files = read_data_json_files(&game_record.game_path)?;
    let command_snapshots = read_event_command_snapshots(&game_record.game_path)?;
    let plugins = read_plugins_json(&game_record.game_path)?;
    let (plugin_rules, stale_plugin_rule_count) =
        read_fresh_plugin_rules(registry, game_record, &plugins)?;
    let custom_rules = registry.read_placeholder_rules(&game_record.game_title)?;
    let event_rules = registry.read_event_command_text_rules(&game_record.game_title)?;
    let note_rules = registry.read_note_tag_text_rules(&game_record.game_title)?;
    let japanese_residual_rules = registry.read_japanese_residual_rules(&game_record.game_title)?;
    let terminology_registry = registry.read_terminology_registry(&game_record.game_title)?;
    let latest_run = registry.read_latest_translation_run(&game_record.game_title)?;
    let active_items = extract_active_text_items(ActiveTextExtractionInput {
        data_files: &data_files,
        command_snapshots: &command_snapshots,
        plugins: &plugins,
        plugin_rules: &plugin_rules,
        event_rules: &event_rules,
        note_rules: &note_rules,
        source_text_required_pattern,
        text_rules,
    })?;
    let active_paths = active_items
        .iter()
        .map(|item| item.location_path.clone())
        .collect::<BTreeSet<_>>();
    let translated_items = registry.read_translated_items(&game_record.game_title)?;
    let translated_paths = translated_items
        .iter()
        .map(|item| item.location_path.clone())
        .collect::<BTreeSet<_>>();
    let active_translated_items = translated_items
        .iter()
        .filter(|item| active_paths.contains(&item.location_path))
        .cloned()
        .collect::<Vec<_>>();
    let pending_paths = active_paths
        .difference(&translated_paths)
        .cloned()
        .collect::<BTreeSet<_>>();
    let stale_paths = translated_paths
        .difference(&active_paths)
        .cloned()
        .collect::<BTreeSet<_>>();
    let stale_japanese_residual_rule_paths = japanese_residual_rules
        .iter()
        .filter(|rule| !active_paths.contains(&rule.location_path))
        .map(|rule| rule.location_path.clone())
        .collect::<BTreeSet<_>>();

    let (quality_error_items, llm_failure_counts) = if let Some(latest_run) = latest_run.as_ref() {
        (
            registry
                .read_translation_quality_errors(&game_record.game_title, &latest_run.run_id)?
                .into_iter()
                .filter(|item| pending_paths.contains(&item.location_path))
                .collect::<Vec<_>>(),
            registry.read_llm_failure_counts(&game_record.game_title, &latest_run.run_id)?,
        )
    } else {
        (Vec::new(), BTreeMap::new())
    };
    let run_quality_error_count = if let Some(latest_run) = latest_run.as_ref() {
        registry
            .read_translation_quality_error_summaries(&game_record.game_title, &latest_run.run_id)?
            .len()
    } else {
        0
    };
    let llm_failure_count = llm_failure_counts.values().sum::<usize>();
    let quality_details = collect_native_quality_details(
        &active_translated_items,
        text_rules,
        &custom_rules,
        &japanese_residual_rules,
    )?;
    let write_back_protocol_items =
        collect_write_protocol_details(&data_files, &plugins, &active_translated_items)?;

    let error_type_counts = count_error_types(&quality_error_items);
    let model_response_count = quality_error_items
        .iter()
        .filter(|item| !item.model_response.trim().is_empty())
        .count();
    let (terminology_total_count, terminology_filled_count, terminology_empty_count) =
        terminology_counts(terminology_registry.as_ref());

    let mut errors = Vec::new();
    let mut warnings = Vec::new();
    if llm_failure_count > 0 && !pending_paths.is_empty() {
        errors.push(issue(
            "llm_failures",
            format!("最新翻译运行存在 {llm_failure_count} 条模型运行故障"),
        ));
    } else if llm_failure_count > 0 {
        warnings.push(issue(
            "historical_llm_failures",
            format!(
                "最新翻译运行记录过 {llm_failure_count} 条模型故障，但当前没有正文因此无法继续"
            ),
        ));
    }
    if !quality_error_items.is_empty() {
        errors.push(issue(
            "translation_quality_errors",
            format!(
                "最新翻译运行有 {} 条模型翻了但项目检查没通过的译文",
                quality_error_items.len()
            ),
        ));
    }
    if !pending_paths.is_empty() {
        errors.push(issue(
            "pending_translations",
            format!("存在 {} 条正文还没成功保存译文", pending_paths.len()),
        ));
    }
    if !quality_details.placeholder_risk_items.is_empty() {
        errors.push(issue(
            "placeholder_risk",
            format!(
                "发现 {} 条译文里的游戏控制符可能被改坏",
                quality_details.placeholder_risk_items.len()
            ),
        ));
    }
    if !quality_details.japanese_residual_items.is_empty() {
        errors.push(issue(
            "japanese_residual",
            format!(
                "发现 {} 条译文存在日文残留风险",
                quality_details.japanese_residual_items.len()
            ),
        ));
    }
    if !quality_details.text_structure_items.is_empty() {
        errors.push(issue(
            "text_structure",
            format!(
                "发现 {} 条译文改动了游戏文本结构",
                quality_details.text_structure_items.len()
            ),
        ));
    }
    if !quality_details.overwide_line_items.is_empty() {
        errors.push(issue(
            "overwide_line",
            format!(
                "发现 {} 行译文超过当前长文本宽度上限",
                quality_details.overwide_line_items.len()
            ),
        ));
    }
    if !write_back_protocol_items.is_empty() {
        errors.push(issue(
            "write_back_protocol",
            format!(
                "发现 {} 条译文写回后会破坏游戏或插件解析协议",
                write_back_protocol_items.len()
            ),
        ));
    }
    if terminology_registry.is_none() {
        errors.push(issue("terminology_missing", "当前游戏尚未导入术语表"));
    } else if terminology_empty_count > 0 {
        errors.push(issue(
            "terminology_empty_translation",
            format!("术语表还有 {terminology_empty_count} 个词条没有填写译名"),
        ));
    }
    if !stale_paths.is_empty() {
        warnings.push(issue(
            "stale_cache",
            format!(
                "发现 {} 条不在当前提取范围内的已保存译文",
                stale_paths.len()
            ),
        ));
    }
    if stale_plugin_rule_count > 0 {
        warnings.push(issue(
            "stale_plugin_rules",
            format!("发现 {stale_plugin_rule_count} 个过期插件规则，已从本轮质量统计中排除"),
        ));
    }
    if !stale_japanese_residual_rule_paths.is_empty() {
        warnings.push(issue(
            "stale_japanese_residual_rules",
            format!(
                "发现 {} 条不在当前提取范围内的日文残留例外规则",
                stale_japanese_residual_rule_paths.len()
            ),
        ));
    }

    let mut summary = Map::new();
    summary.insert("extractable_count".to_string(), json!(active_paths.len()));
    summary.insert(
        "translated_count".to_string(),
        json!(translated_paths.intersection(&active_paths).count()),
    );
    summary.insert("pending_count".to_string(), json!(pending_paths.len()));
    summary.insert("stale_cache_count".to_string(), json!(stale_paths.len()));
    summary.insert(
        "plugin_rule_count".to_string(),
        json!(
            plugin_rules
                .iter()
                .map(|rule| rule.path_templates.len())
                .sum::<usize>()
        ),
    );
    summary.insert(
        "stale_plugin_rule_count".to_string(),
        json!(stale_plugin_rule_count),
    );
    summary.insert(
        "event_command_rule_count".to_string(),
        json!(
            event_rules
                .iter()
                .map(|rule| rule.path_templates.len())
                .sum::<usize>()
        ),
    );
    summary.insert(
        "note_tag_rule_count".to_string(),
        json!(
            note_rules
                .iter()
                .map(|rule| rule.tag_names.len())
                .sum::<usize>()
        ),
    );
    summary.insert(
        "japanese_residual_rule_count".to_string(),
        json!(japanese_residual_rules.len()),
    );
    summary.insert(
        "stale_japanese_residual_rule_count".to_string(),
        json!(stale_japanese_residual_rule_paths.len()),
    );
    summary.insert(
        "terminology_total_count".to_string(),
        json!(terminology_total_count),
    );
    summary.insert(
        "terminology_filled_count".to_string(),
        json!(terminology_filled_count),
    );
    summary.insert(
        "terminology_empty_count".to_string(),
        json!(terminology_empty_count),
    );
    summary.insert(
        "latest_run_id".to_string(),
        json!(
            latest_run
                .as_ref()
                .map(|run| run.run_id.as_str())
                .unwrap_or("")
        ),
    );
    summary.insert(
        "latest_run_status".to_string(),
        json!(
            latest_run
                .as_ref()
                .map(|run| run.status.as_str())
                .unwrap_or("")
        ),
    );
    summary.insert("llm_failure_count".to_string(), json!(llm_failure_count));
    summary.insert(
        "quality_error_count".to_string(),
        json!(quality_error_items.len()),
    );
    summary.insert(
        "run_quality_error_count".to_string(),
        json!(run_quality_error_count),
    );
    summary.insert(
        "model_response_error_count".to_string(),
        json!(model_response_count),
    );
    summary.insert(
        "japanese_residual_count".to_string(),
        json!(quality_details.japanese_residual_items.len()),
    );
    summary.insert(
        "text_structure_count".to_string(),
        json!(quality_details.text_structure_items.len()),
    );
    summary.insert(
        "placeholder_risk_count".to_string(),
        json!(quality_details.placeholder_risk_items.len()),
    );
    summary.insert(
        "overwide_line_count".to_string(),
        json!(quality_details.overwide_line_items.len()),
    );
    summary.insert(
        "write_back_protocol_count".to_string(),
        json!(write_back_protocol_items.len()),
    );
    summary.insert(
        "writable_translation_count".to_string(),
        json!(translated_paths.intersection(&active_paths).count()),
    );

    let mut details = Map::new();
    details.insert("error_type_counts".to_string(), json!(error_type_counts));
    details.insert("llm_failure_counts".to_string(), json!(llm_failure_counts));
    details.insert(
        "quality_error_items".to_string(),
        Value::Array(
            quality_error_items
                .iter()
                .map(translation_error_quality_detail)
                .collect(),
        ),
    );
    details.insert(
        "japanese_residual_items".to_string(),
        Value::Array(quality_details.japanese_residual_items),
    );
    details.insert(
        "text_structure_items".to_string(),
        Value::Array(quality_details.text_structure_items),
    );
    details.insert(
        "placeholder_risk_items".to_string(),
        Value::Array(quality_details.placeholder_risk_items),
    );
    details.insert(
        "overwide_line_items".to_string(),
        Value::Array(quality_details.overwide_line_items),
    );
    details.insert(
        "write_back_protocol_items".to_string(),
        Value::Array(write_back_protocol_items),
    );
    Ok(AgentReport::from_parts(errors, warnings, summary, details))
}

/// 从当前质量问题导出可直接填写的译文修复表。
pub fn export_quality_fix_template_report(
    registry: &GameRegistry,
    game_record: &GameRecord,
    output_path: &Path,
    source_text_required_pattern: &str,
    text_rules: &TextRuleOptions,
) -> Result<AgentReport> {
    let data_files = read_data_json_files(&game_record.game_path)?;
    let command_snapshots = read_event_command_snapshots(&game_record.game_path)?;
    let plugins = read_plugins_json(&game_record.game_path)?;
    let (plugin_rules, _stale_plugin_rule_count) =
        read_fresh_plugin_rules(registry, game_record, &plugins)?;
    let custom_rules = registry.read_placeholder_rules(&game_record.game_title)?;
    let event_rules = registry.read_event_command_text_rules(&game_record.game_title)?;
    let note_rules = registry.read_note_tag_text_rules(&game_record.game_title)?;
    let japanese_residual_rules = registry.read_japanese_residual_rules(&game_record.game_title)?;
    let active_items = extract_active_text_items(ActiveTextExtractionInput {
        data_files: &data_files,
        command_snapshots: &command_snapshots,
        plugins: &plugins,
        plugin_rules: &plugin_rules,
        event_rules: &event_rules,
        note_rules: &note_rules,
        source_text_required_pattern,
        text_rules,
    })?;
    let active_items_by_path = active_items
        .iter()
        .map(|item| (item.location_path.clone(), item))
        .collect::<BTreeMap<_, _>>();
    let active_paths = active_items
        .iter()
        .map(|item| item.location_path.clone())
        .collect::<BTreeSet<_>>();
    let translated_items = registry.read_translated_items(&game_record.game_title)?;
    let translated_by_path = translated_items
        .iter()
        .map(|item| (item.location_path.clone(), item))
        .collect::<BTreeMap<_, _>>();
    let translated_paths = translated_by_path.keys().cloned().collect::<BTreeSet<_>>();
    let active_translated_items = translated_items
        .iter()
        .filter(|item| active_paths.contains(&item.location_path))
        .cloned()
        .collect::<Vec<_>>();
    let pending_paths = active_paths
        .difference(&translated_paths)
        .cloned()
        .collect::<BTreeSet<_>>();
    let latest_run = registry.read_latest_translation_run(&game_record.game_title)?;
    let quality_error_items = if let Some(latest_run) = latest_run.as_ref() {
        registry
            .read_translation_quality_errors(&game_record.game_title, &latest_run.run_id)?
            .into_iter()
            .filter(|item| pending_paths.contains(&item.location_path))
            .collect::<Vec<_>>()
    } else {
        Vec::new()
    };
    let quality_errors_by_path = quality_error_items
        .iter()
        .map(|item| (item.location_path.clone(), item))
        .collect::<BTreeMap<_, _>>();
    let quality_details = collect_native_quality_details(
        &active_translated_items,
        text_rules,
        &custom_rules,
        &japanese_residual_rules,
    )?;
    let write_back_protocol_items =
        collect_write_protocol_details(&data_files, &plugins, &active_translated_items)?;
    let problem_paths = collect_quality_fix_problem_paths(
        &quality_error_items,
        &quality_details.japanese_residual_items,
        &quality_details.text_structure_items,
        &quality_details.placeholder_risk_items,
        &quality_details.overwide_line_items,
        &write_back_protocol_items,
        &active_paths,
    );
    let categories_by_path = build_quality_fix_categories_by_path(
        &quality_error_items,
        &quality_details.japanese_residual_items,
        &quality_details.text_structure_items,
        &quality_details.placeholder_risk_items,
        &quality_details.overwide_line_items,
        &write_back_protocol_items,
        &active_paths,
    );

    let mut payload = Map::new();
    for location_path in &problem_paths {
        let Some(active_item) = active_items_by_path.get(location_path) else {
            continue;
        };
        let translation_lines = resolve_quality_fix_translation_lines(
            location_path,
            &quality_errors_by_path,
            &translated_by_path,
        );
        payload.insert(
            location_path.clone(),
            manual_translation_template_entry_with_translation_lines(
                active_item,
                &custom_rules,
                &translation_lines,
            )?,
        );
    }
    write_json_file(output_path, &Value::Object(payload))?;

    let mut warnings = Vec::new();
    if problem_paths.is_empty() {
        warnings.push(issue("quality_fix_empty", "当前没有可导出的质量修复条目"));
    }
    let mut summary = Map::new();
    summary.insert("exported_count".to_string(), json!(problem_paths.len()));
    summary.insert(
        "output".to_string(),
        json!(output_path.display().to_string()),
    );
    summary.insert(
        "quality_error_count".to_string(),
        json!(quality_error_items.len()),
    );
    summary.insert(
        "japanese_residual_count".to_string(),
        json!(count_active_quality_details(
            &quality_details.japanese_residual_items,
            &active_paths
        )),
    );
    summary.insert(
        "text_structure_count".to_string(),
        json!(count_active_quality_details(
            &quality_details.text_structure_items,
            &active_paths
        )),
    );
    summary.insert(
        "placeholder_risk_count".to_string(),
        json!(count_active_quality_details(
            &quality_details.placeholder_risk_items,
            &active_paths
        )),
    );
    summary.insert(
        "overwide_line_count".to_string(),
        json!(count_active_quality_details(
            &quality_details.overwide_line_items,
            &active_paths
        )),
    );
    summary.insert(
        "write_back_protocol_count".to_string(),
        json!(count_active_quality_details(
            &write_back_protocol_items,
            &active_paths
        )),
    );
    let mut details = Map::new();
    details.insert("location_paths".to_string(), json!(problem_paths));
    details.insert(
        "problem_categories_by_path".to_string(),
        Value::Object(categories_by_path),
    );
    Ok(AgentReport::from_parts(
        Vec::new(),
        warnings,
        summary,
        details,
    ))
}

/// 导入手动填写的中文译文行，检查通过后保存到当前游戏数据库。
pub fn import_manual_translations_report(
    registry: &GameRegistry,
    game_record: &GameRecord,
    input_path: &Path,
    source_text_required_pattern: &str,
    text_rules: &TextRuleOptions,
) -> Result<AgentReport> {
    let payload = match read_json_object_file(input_path, "manual-translations") {
        Ok(payload) => payload,
        Err(error) => {
            let mut summary = Map::new();
            summary.insert("input".to_string(), json!(input_path.display().to_string()));
            summary.insert("imported_count".to_string(), json!(0));
            return Ok(AgentReport::from_parts(
                vec![issue(
                    "manual_translation_file",
                    format!("手动填写译文表不可读: {error}"),
                )],
                Vec::new(),
                summary,
                Map::new(),
            ));
        }
    };

    let custom_rules = registry.read_placeholder_rules(&game_record.game_title)?;
    let active_items = load_active_translation_items(
        registry,
        game_record,
        source_text_required_pattern,
        text_rules,
    )?;
    let active_by_path = active_items
        .iter()
        .map(|item| (item.location_path.as_str(), item))
        .collect::<BTreeMap<_, _>>();
    let residual_rules = registry.read_japanese_residual_rules(&game_record.game_title)?;
    let residual_rule_map = residual_rules
        .iter()
        .map(|record| (record.location_path.as_str(), record))
        .collect::<BTreeMap<_, _>>();
    let width_pattern = compile_line_width_pattern(text_rules)?;

    let mut errors = Vec::new();
    let mut valid_items = Vec::new();
    for (location_path, raw_entry) in payload {
        let Some(entry) = raw_entry.as_object() else {
            errors.push(issue(
                "manual_translation_entry",
                format!("{location_path} 必须是 JSON 对象"),
            ));
            continue;
        };
        let Some(active_item) = active_by_path.get(location_path.as_str()) else {
            errors.push(issue(
                "manual_translation_location",
                format!("{location_path} 不在当前可提取文本范围内"),
            ));
            continue;
        };
        let raw_lines_value = entry.get("translation_lines");
        let result = raw_lines_value
            .ok_or_else(|| {
                AttMzError::InvalidConfig(format!(
                    "{location_path}.translation_lines 必须是字符串数组"
                ))
            })
            .and_then(|value| {
                json_string_array(value, &format!("{location_path}.translation_lines"))
            })
            .and_then(|translation_lines| {
                prepare_manual_translation_item(
                    active_item,
                    translation_lines,
                    &custom_rules,
                    residual_rule_map.get(location_path.as_str()).copied(),
                    text_rules,
                    &width_pattern,
                )
            });
        match result {
            Ok(item) => valid_items.push(item),
            Err(error) => errors.push(issue(
                "manual_translation_invalid",
                format!("{location_path} 手动填写译文不可用: {error}"),
            )),
        }
    }

    if !errors.is_empty() {
        let mut summary = Map::new();
        summary.insert("input".to_string(), json!(input_path.display().to_string()));
        summary.insert("imported_count".to_string(), json!(0));
        summary.insert("error_count".to_string(), json!(errors.len()));
        return Ok(AgentReport::from_parts(
            errors,
            Vec::new(),
            summary,
            Map::new(),
        ));
    }

    registry.write_translation_items(&game_record.game_title, &valid_items)?;
    let imported_paths = valid_items
        .iter()
        .map(|item| item.location_path.clone())
        .collect::<BTreeSet<_>>();
    let _deleted_quality_errors = registry
        .delete_translation_quality_errors_by_paths(&game_record.game_title, &imported_paths)?;
    if let Some(latest_run) = registry.read_latest_translation_run(&game_record.game_title)? {
        let remaining_quality_errors = registry.read_translation_quality_error_summaries(
            &game_record.game_title,
            &latest_run.run_id,
        )?;
        let llm_failure_counts =
            registry.read_llm_failure_counts(&game_record.game_title, &latest_run.run_id)?;
        let active_paths = active_items
            .iter()
            .map(|item| item.location_path.clone())
            .collect::<BTreeSet<_>>();
        let translated_paths = registry.read_translation_location_paths(&game_record.game_title)?;
        let current_pending_paths = active_paths
            .difference(&translated_paths)
            .cloned()
            .collect::<BTreeSet<_>>();
        if current_pending_paths.is_empty()
            && remaining_quality_errors.is_empty()
            && llm_failure_counts.values().all(|count| *count == 0)
        {
            registry.mark_translation_run_completed(&game_record.game_title, &latest_run.run_id)?;
        }
    }

    let warnings = if valid_items.is_empty() {
        vec![issue(
            "manual_translation_empty",
            "手动填写译文表没有可导入条目",
        )]
    } else {
        Vec::new()
    };
    let mut summary = Map::new();
    summary.insert("input".to_string(), json!(input_path.display().to_string()));
    summary.insert("imported_count".to_string(), json!(valid_items.len()));
    Ok(AgentReport::from_parts(
        Vec::new(),
        warnings,
        summary,
        Map::new(),
    ))
}

/// 删除已保存译文，使指定条目或当前提取范围全部条目重新交给翻译流程处理。
pub fn reset_translations_report(
    registry: &GameRegistry,
    game_record: &GameRecord,
    input_path: Option<&Path>,
    reset_all: bool,
    source_text_required_pattern: &str,
    text_rules: &TextRuleOptions,
) -> Result<AgentReport> {
    if input_path.is_some() && reset_all {
        let mut summary = reset_translation_summary(input_path, "invalid", 0, 0);
        return Ok(AgentReport::from_parts(
            vec![issue(
                "reset_translation_source",
                "--input 与 --all 不能同时使用",
            )],
            Vec::new(),
            std::mem::take(&mut summary),
            Map::new(),
        ));
    }
    if input_path.is_none() && !reset_all {
        return Ok(AgentReport::from_parts(
            vec![issue(
                "reset_translation_source",
                "必须通过 --input 或 --all 指定重置范围",
            )],
            Vec::new(),
            reset_translation_summary(None, "invalid", 0, 0),
            Map::new(),
        ));
    }

    let requested_paths = if let Some(input_path) = input_path {
        match read_reset_translation_location_paths(input_path) {
            Ok(paths) => paths,
            Err(error) => {
                return Ok(AgentReport::from_parts(
                    vec![issue(
                        "reset_translation_file",
                        format!("重置译文文件不可用: {error}"),
                    )],
                    Vec::new(),
                    reset_translation_summary(Some(input_path), "input", 0, 0),
                    Map::new(),
                ));
            }
        }
    } else {
        Vec::new()
    };

    let active_items = load_active_translation_items(
        registry,
        game_record,
        source_text_required_pattern,
        text_rules,
    )?;
    let active_location_paths = collect_active_translation_location_paths(&active_items);
    let active_paths = active_location_paths
        .iter()
        .cloned()
        .collect::<BTreeSet<_>>();
    let location_paths = if reset_all {
        active_location_paths
    } else {
        requested_paths
    };
    let invalid_paths = location_paths
        .iter()
        .filter(|path| !active_paths.contains(*path))
        .cloned()
        .collect::<Vec<_>>();
    if !invalid_paths.is_empty() {
        let mut details = Map::new();
        details.insert("invalid_location_paths".to_string(), json!(invalid_paths));
        return Ok(AgentReport::from_parts(
            vec![issue(
                "reset_translation_location",
                format!(
                    "存在 {} 个定位路径不在当前可提取文本范围内",
                    invalid_paths.len()
                ),
            )],
            Vec::new(),
            reset_translation_summary(
                input_path,
                if reset_all { "all" } else { "input" },
                location_paths.len(),
                0,
            ),
            details,
        ));
    }

    let reset_count =
        registry.delete_translation_items_by_paths(&game_record.game_title, &location_paths)?;
    let mut warnings = Vec::new();
    let already_pending_count = location_paths.len().saturating_sub(reset_count);
    if already_pending_count > 0 {
        warnings.push(issue(
            "reset_translation_already_pending",
            format!("{already_pending_count} 个定位路径当前没有已保存译文"),
        ));
    }
    if reset_all && location_paths.is_empty() {
        warnings.push(issue(
            "reset_translation_no_active_items",
            "当前提取范围没有可重置条目",
        ));
    }
    let requested_count = location_paths.len();
    let mut details = Map::new();
    if reset_all {
        let samples = location_paths.iter().take(20).cloned().collect::<Vec<_>>();
        details.insert(
            "location_path_count".to_string(),
            json!(location_paths.len()),
        );
        details.insert("location_path_samples".to_string(), json!(samples));
    } else {
        details.insert("location_paths".to_string(), json!(location_paths));
    }
    Ok(AgentReport::from_parts(
        Vec::new(),
        warnings,
        reset_translation_summary(
            input_path,
            if reset_all { "all" } else { "input" },
            requested_count,
            reset_count,
        ),
        details,
    ))
}

fn manual_translation_template_entry(
    item: &ActiveTextItem,
    custom_rules: &[PlaceholderRule],
) -> Result<Value> {
    manual_translation_template_entry_with_translation_lines(item, custom_rules, &[])
}

fn manual_translation_template_entry_with_translation_lines(
    item: &ActiveTextItem,
    custom_rules: &[PlaceholderRule],
    translation_lines: &[String],
) -> Result<Value> {
    let placeholder_context = build_placeholder_context(custom_rules, &item.original_lines)?;
    let restored_translation_lines = if translation_lines.is_empty() {
        Vec::new()
    } else {
        restore_placeholder_lines(&placeholder_context, translation_lines)?
    };
    Ok(json!({
        "item_type": item.item_type,
        "role": item.role,
        "original_lines": item.original_lines,
        "text_for_model_lines": placeholder_context.text_for_model_lines,
        "translation_lines": restored_translation_lines,
        "manual_fill_note": MANUAL_FILL_NOTE,
    }))
}

fn collect_native_quality_details(
    items: &[TranslationItemRecord],
    text_rules: &TextRuleOptions,
    custom_rules: &[PlaceholderRule],
    japanese_residual_rules: &[JapaneseResidualRuleRecord],
) -> Result<NativeQualityDetails> {
    let payload = json!({
        "items": items.iter().map(translation_item_payload).collect::<Vec<_>>(),
        "text_rules": {
            "custom_placeholder_rules": custom_rules.iter().map(|rule| json!({
                "pattern_text": rule.pattern_text,
                "placeholder_template": rule.placeholder_template,
            })).collect::<Vec<_>>(),
            "allowed_japanese_chars": text_rules.allowed_japanese_chars,
            "allowed_japanese_tail_chars": text_rules.allowed_japanese_tail_chars,
            "japanese_segment_pattern": text_rules.japanese_segment_pattern,
            "line_width_count_pattern": text_rules.line_width_count_pattern,
            "residual_escape_sequence_pattern": text_rules.residual_escape_sequence_pattern,
            "long_text_line_width_limit": text_rules.long_text_line_width_limit,
            "strip_wrapping_punctuation_pairs": text_rules.strip_wrapping_punctuation_pairs,
            "preserve_wrapping_punctuation_pairs": text_rules.preserve_wrapping_punctuation_pairs,
            "line_split_punctuations": text_rules.line_split_punctuations,
        },
        "japanese_residual_rules": japanese_residual_rules.iter().map(|rule| json!({
            "location_path": rule.location_path,
            "allowed_terms": rule.allowed_terms,
            "reason": rule.reason,
        })).collect::<Vec<_>>(),
    });
    let result_text = native_core::scan_quality_impl(&payload.to_string())
        .map_err(|message| AttMzError::InvalidConfig(format!("Rust 原生质检失败: {message}")))?;
    let value: Value = serde_json::from_str(&result_text).map_err(|source| AttMzError::Json {
        context: "Rust 原生质检输出".to_string(),
        source,
    })?;
    Ok(NativeQualityDetails {
        japanese_residual_items: object_array_field(&value, "japanese_residual_items")?,
        text_structure_items: object_array_field(&value, "text_structure_items")?,
        placeholder_risk_items: object_array_field(&value, "placeholder_risk_items")?,
        overwide_line_items: object_array_field(&value, "overwide_line_items")?,
    })
}

fn translation_item_payload(item: &TranslationItemRecord) -> Value {
    json!({
        "location_path": item.location_path,
        "item_type": item.item_type,
        "role": item.role,
        "original_lines": item.original_lines,
        "translation_lines": item.translation_lines,
    })
}

fn object_array_field(value: &Value, field: &str) -> Result<Vec<Value>> {
    let Some(array) = value.get(field).and_then(Value::as_array) else {
        return Err(AttMzError::InvalidConfig(format!(
            "Rust 原生质检输出缺少数组字段: {field}"
        )));
    };
    Ok(array.clone())
}

fn collect_write_protocol_details(
    data_files: &BTreeMap<String, Value>,
    plugins: &[Value],
    items: &[TranslationItemRecord],
) -> Result<Vec<Value>> {
    let entries = items
        .iter()
        .map(|item| build_protocol_entry(data_files, plugins, item))
        .collect::<Result<Vec<_>>>()?;
    let payload = json!({ "entries": entries });
    let result_text =
        native_core::scan_write_protocol_impl(&payload.to_string()).map_err(|message| {
            AttMzError::InvalidConfig(format!("Rust 写入协议检查失败: {message}"))
        })?;
    let value: Value = serde_json::from_str(&result_text).map_err(|source| AttMzError::Json {
        context: "Rust 写入协议输出".to_string(),
        source,
    })?;
    let Some(array) = value.as_array() else {
        return Err(AttMzError::InvalidConfig(
            "Rust 写入协议输出必须是数组".to_string(),
        ));
    };
    Ok(array.clone())
}

fn build_protocol_entry(
    data_files: &BTreeMap<String, Value>,
    plugins: &[Value],
    item: &TranslationItemRecord,
) -> Result<Value> {
    let parts = item.location_path.split('/').collect::<Vec<_>>();
    if item.location_path.starts_with("plugins.js/") {
        return build_plugin_protocol_entry(plugins, item, &parts);
    }
    if item.location_path.contains("/note/") {
        return build_note_protocol_entry(data_files, item, &parts);
    }
    if item.location_path.contains("/parameters/") {
        return build_event_parameter_protocol_entry(data_files, item, &parts);
    }
    Ok(empty_protocol_entry(item))
}

fn build_plugin_protocol_entry(
    plugins: &[Value],
    item: &TranslationItemRecord,
    parts: &[&str],
) -> Result<Value> {
    if parts.len() < 3 {
        return Ok(empty_protocol_entry(item));
    }
    let plugin_index = parse_usize(parts[1], &item.location_path)?;
    let Some(plugin) = plugins.get(plugin_index).and_then(Value::as_object) else {
        return Ok(empty_protocol_entry(item));
    };
    let Some(parameters) = plugin.get("parameters").and_then(Value::as_object) else {
        return Ok(empty_protocol_entry(item));
    };
    let Some(current_value) = parameters.get(parts[2]) else {
        return Ok(empty_protocol_entry(item));
    };
    Ok(json!({
        "item": translation_item_payload(item),
        "mode": "nested",
        "current_value": current_value,
        "path_parts": parts[3..],
        "note_text": null,
        "tag_name": null,
    }))
}

fn build_note_protocol_entry(
    data_files: &BTreeMap<String, Value>,
    item: &TranslationItemRecord,
    parts: &[&str],
) -> Result<Value> {
    let Some(tag_name) = parts.last() else {
        return Ok(empty_protocol_entry(item));
    };
    let Some(note_index) = parts.iter().position(|part| *part == "note") else {
        return Ok(empty_protocol_entry(item));
    };
    let Some(root) = data_files.get(parts[0]) else {
        return Ok(empty_protocol_entry(item));
    };
    let owner = locate_note_owner(root, &parts[1..note_index], &item.location_path)?;
    let Some(note_text) = owner.get("note").and_then(Value::as_str) else {
        return Ok(empty_protocol_entry(item));
    };
    Ok(json!({
        "item": translation_item_payload(item),
        "mode": "note",
        "current_value": null,
        "path_parts": Vec::<String>::new(),
        "note_text": note_text,
        "tag_name": tag_name,
    }))
}

fn build_event_parameter_protocol_entry(
    data_files: &BTreeMap<String, Value>,
    item: &TranslationItemRecord,
    parts: &[&str],
) -> Result<Value> {
    let Some((command, value_parts)) = locate_event_command_for_protocol(data_files, parts)? else {
        return Ok(empty_protocol_entry(item));
    };
    if value_parts.len() < 2 || value_parts[0] != "parameters" {
        return Ok(empty_protocol_entry(item));
    }
    let Some(parameters) = command.get("parameters").and_then(Value::as_array) else {
        return Ok(empty_protocol_entry(item));
    };
    let parameter_index = parse_usize(&value_parts[1], &item.location_path)?;
    let Some(current_value) = parameters.get(parameter_index) else {
        return Ok(empty_protocol_entry(item));
    };
    Ok(json!({
        "item": translation_item_payload(item),
        "mode": "nested",
        "current_value": current_value,
        "path_parts": value_parts[2..],
        "note_text": null,
        "tag_name": null,
    }))
}

fn empty_protocol_entry(item: &TranslationItemRecord) -> Value {
    json!({
        "item": translation_item_payload(item),
        "mode": "none",
        "current_value": null,
        "path_parts": Vec::<String>::new(),
        "note_text": null,
        "tag_name": null,
    })
}

fn locate_note_owner<'a>(
    value: &'a Value,
    owner_parts: &[&str],
    context: &str,
) -> Result<&'a Map<String, Value>> {
    let mut current = value;
    for part in owner_parts {
        if let Some(object) = current.as_object() {
            current = object.get(*part).ok_or_else(|| {
                AttMzError::InvalidConfig(format!("Note 路径对象键不存在: {context}"))
            })?;
            continue;
        }
        if let Some(array) = current.as_array() {
            let index = parse_usize(part, context)?;
            if let Some(value) = array.get(index).filter(|value| !value.is_null()) {
                current = value;
                continue;
            }
            current = array
                .iter()
                .find(|candidate| {
                    candidate
                        .as_object()
                        .and_then(|object| object.get("id"))
                        .and_then(Value::as_i64)
                        .is_some_and(|id| id == index as i64)
                })
                .ok_or_else(|| {
                    AttMzError::InvalidConfig(format!("Note 路径数组索引不存在: {context}"))
                })?;
            continue;
        }
        return Err(AttMzError::InvalidConfig(format!(
            "Note 路径无法继续定位: {context}"
        )));
    }
    current
        .as_object()
        .ok_or_else(|| AttMzError::InvalidConfig(format!("Note 持有者不是对象: {context}")))
}

type ProtocolCommandLocation<'a> = (&'a Map<String, Value>, Vec<String>);

fn locate_event_command_for_protocol<'a>(
    data_files: &'a BTreeMap<String, Value>,
    parts: &[&str],
) -> Result<Option<ProtocolCommandLocation<'a>>> {
    if parts.is_empty() {
        return Ok(None);
    }
    let Some(data) = data_files.get(parts[0]) else {
        return Ok(None);
    };
    if parts[0].starts_with("Map") && parts[0].ends_with(".json") && parts.len() >= 5 {
        let Some(events) = data
            .as_object()
            .and_then(|object| object.get("events"))
            .and_then(Value::as_array)
        else {
            return Ok(None);
        };
        let event = events
            .get(parse_usize(parts[1], parts[0])?)
            .and_then(Value::as_object);
        let Some(page) = event
            .and_then(|object| object.get("pages"))
            .and_then(Value::as_array)
            .and_then(|pages| pages.get(parse_usize(parts[2], parts[0]).ok()?))
            .and_then(Value::as_object)
        else {
            return Ok(None);
        };
        let command = page
            .get("list")
            .and_then(Value::as_array)
            .and_then(|commands| commands.get(parse_usize(parts[3], parts[0]).ok()?))
            .and_then(Value::as_object);
        return Ok(command.map(|command| {
            (
                command,
                parts[4..].iter().map(|part| (*part).to_string()).collect(),
            )
        }));
    }
    if parts[0] == "CommonEvents.json" && parts.len() >= 4 {
        let command = data
            .as_array()
            .and_then(|events| events.get(parse_usize(parts[1], parts[0]).ok()?))
            .and_then(Value::as_object)
            .and_then(|event| event.get("list"))
            .and_then(Value::as_array)
            .and_then(|commands| commands.get(parse_usize(parts[2], parts[0]).ok()?))
            .and_then(Value::as_object);
        return Ok(command.map(|command| {
            (
                command,
                parts[3..].iter().map(|part| (*part).to_string()).collect(),
            )
        }));
    }
    if parts[0] == "Troops.json" && parts.len() >= 5 {
        let troop = data
            .as_array()
            .and_then(|troops| troops.get(parse_usize(parts[1], parts[0]).ok()?))
            .and_then(Value::as_object);
        let page = troop
            .and_then(|object| object.get("pages"))
            .and_then(Value::as_array)
            .and_then(|pages| pages.get(parse_usize(parts[2], parts[0]).ok()?))
            .and_then(Value::as_object);
        let command = page
            .and_then(|object| object.get("list"))
            .and_then(Value::as_array)
            .and_then(|commands| commands.get(parse_usize(parts[3], parts[0]).ok()?))
            .and_then(Value::as_object);
        return Ok(command.map(|command| {
            (
                command,
                parts[4..].iter().map(|part| (*part).to_string()).collect(),
            )
        }));
    }
    Ok(None)
}

fn parse_usize(value: &str, context: &str) -> Result<usize> {
    value
        .parse::<usize>()
        .map_err(|error| AttMzError::InvalidConfig(format!("路径索引不是整数: {context}: {error}")))
}

fn count_error_types(items: &[TranslationErrorItemRecord]) -> BTreeMap<String, usize> {
    let mut counts = BTreeMap::new();
    for item in items {
        *counts.entry(item.error_type.clone()).or_default() += 1;
    }
    counts
}

fn terminology_counts(
    registry: Option<&BTreeMap<String, BTreeMap<String, String>>>,
) -> (usize, usize, usize) {
    let Some(registry) = registry else {
        return (0, 0, 0);
    };
    let total = registry.values().map(BTreeMap::len).sum::<usize>();
    let filled = registry
        .values()
        .flat_map(BTreeMap::values)
        .filter(|value| !value.trim().is_empty())
        .count();
    (total, filled, total.saturating_sub(filled))
}

fn translation_error_quality_detail(item: &TranslationErrorItemRecord) -> Value {
    json!({
        "location_path": item.location_path,
        "item_type": item.item_type,
        "role": item.role,
        "original_lines": item.original_lines,
        "translation_lines": item.translation_lines,
        "error_type": item.error_type,
        "error_detail": item.error_detail,
        "model_response": item.model_response,
    })
}

fn collect_quality_fix_problem_paths(
    quality_error_items: &[TranslationErrorItemRecord],
    residual_details: &[Value],
    text_structure_details: &[Value],
    placeholder_details: &[Value],
    overwide_details: &[Value],
    write_back_protocol_details: &[Value],
    active_paths: &BTreeSet<String>,
) -> Vec<String> {
    let mut location_paths = Vec::new();
    for item in quality_error_items {
        append_unique_active_path(&mut location_paths, &item.location_path, active_paths);
    }
    for details in [
        residual_details,
        text_structure_details,
        placeholder_details,
        overwide_details,
        write_back_protocol_details,
    ] {
        for location_path in location_paths_from_quality_details(details) {
            append_unique_active_path(&mut location_paths, &location_path, active_paths);
        }
    }
    location_paths
}

fn build_quality_fix_categories_by_path(
    quality_error_items: &[TranslationErrorItemRecord],
    residual_details: &[Value],
    text_structure_details: &[Value],
    placeholder_details: &[Value],
    overwide_details: &[Value],
    write_back_protocol_details: &[Value],
    active_paths: &BTreeSet<String>,
) -> Map<String, Value> {
    let mut categories = BTreeMap::<String, Vec<String>>::new();
    for item in quality_error_items {
        if active_paths.contains(&item.location_path) {
            categories
                .entry(item.location_path.clone())
                .or_default()
                .push("quality_error".to_string());
        }
    }
    append_quality_detail_categories(
        &mut categories,
        residual_details,
        active_paths,
        "japanese_residual",
    );
    append_quality_detail_categories(
        &mut categories,
        text_structure_details,
        active_paths,
        "text_structure",
    );
    append_quality_detail_categories(
        &mut categories,
        placeholder_details,
        active_paths,
        "placeholder_risk",
    );
    append_quality_detail_categories(
        &mut categories,
        overwide_details,
        active_paths,
        "overwide_line",
    );
    append_quality_detail_categories(
        &mut categories,
        write_back_protocol_details,
        active_paths,
        "write_back_protocol",
    );
    categories
        .into_iter()
        .map(|(location_path, path_categories)| (location_path, json!(path_categories)))
        .collect()
}

fn append_quality_detail_categories(
    categories: &mut BTreeMap<String, Vec<String>>,
    details: &[Value],
    active_paths: &BTreeSet<String>,
    category: &str,
) {
    for location_path in location_paths_from_quality_details(details) {
        if !active_paths.contains(&location_path) {
            continue;
        }
        let path_categories = categories.entry(location_path).or_default();
        if !path_categories.iter().any(|item| item == category) {
            path_categories.push(category.to_string());
        }
    }
}

fn append_unique_active_path(
    location_paths: &mut Vec<String>,
    location_path: &str,
    active_paths: &BTreeSet<String>,
) {
    if !active_paths.contains(location_path) {
        return;
    }
    if location_paths
        .iter()
        .any(|existing| existing == location_path)
    {
        return;
    }
    location_paths.push(location_path.to_string());
}

fn location_paths_from_quality_details(details: &[Value]) -> Vec<String> {
    details
        .iter()
        .filter_map(|detail| {
            detail
                .as_object()
                .and_then(|object| object.get("location_path"))
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .collect()
}

fn resolve_quality_fix_translation_lines(
    location_path: &str,
    quality_errors_by_path: &BTreeMap<String, &TranslationErrorItemRecord>,
    translated_by_path: &BTreeMap<String, &TranslationItemRecord>,
) -> Vec<String> {
    if let Some(quality_error) = quality_errors_by_path.get(location_path) {
        return quality_error.translation_lines.clone();
    }
    translated_by_path
        .get(location_path)
        .map(|item| item.translation_lines.clone())
        .unwrap_or_default()
}

fn count_active_quality_details(details: &[Value], active_paths: &BTreeSet<String>) -> usize {
    location_paths_from_quality_details(details)
        .into_iter()
        .filter(|location_path| active_paths.contains(location_path))
        .count()
}

pub(crate) fn prepare_manual_translation_item(
    item: &ActiveTextItem,
    translation_lines: Vec<String>,
    custom_rules: &[PlaceholderRule],
    residual_rule: Option<&JapaneseResidualRuleRecord>,
    text_rules: &TextRuleOptions,
    width_pattern: &Regex,
) -> Result<TranslationItemRecord> {
    if translation_lines.is_empty() || !translation_lines.iter().any(|line| !line.trim().is_empty())
    {
        return Err(AttMzError::InvalidConfig(
            "translation_lines 不能为空".to_string(),
        ));
    }
    if item.item_type == "short_text" && translation_lines.len() != 1 {
        return Err(AttMzError::InvalidConfig(
            "short_text 必须提供 1 行译文".to_string(),
        ));
    }
    if item.item_type == "array" && translation_lines.len() != item.original_lines.len() {
        return Err(AttMzError::InvalidConfig(format!(
            "array 必须提供 {} 行译文",
            item.original_lines.len()
        )));
    }
    let visible_placeholders = collect_placeholder_tokens(&translation_lines)?;
    if !visible_placeholders.is_empty() {
        let joined_placeholders = visible_placeholders
            .into_iter()
            .collect::<Vec<_>>()
            .join("、");
        return Err(AttMzError::InvalidConfig(format!(
            "translation_lines 必须使用游戏原始控制符，不得保留程序占位符: {joined_placeholders}"
        )));
    }

    let normalized_translation_lines = normalize_manual_translation_lines_with_pattern(
        item,
        &translation_lines,
        text_rules,
        width_pattern,
    )?;
    let placeholder_context = build_placeholder_context(custom_rules, &item.original_lines)?;
    let masked_translation_lines = mask_translation_controls(
        custom_rules,
        &placeholder_context,
        &normalized_translation_lines,
    )?;
    validate_translation_text_structure(
        item,
        &placeholder_context.text_for_model_lines,
        &normalized_translation_lines,
        &masked_translation_lines,
    )?;
    verify_placeholder_counts(&placeholder_context, &masked_translation_lines)?;
    let original_raw_controls =
        collect_unprotected_control_sequences(custom_rules, &item.original_lines)?;
    let translated_raw_controls =
        collect_unprotected_control_sequences(custom_rules, &masked_translation_lines)?;
    if original_raw_controls != translated_raw_controls {
        return Err(AttMzError::InvalidConfig(format!(
            "疑似控制符不一致，未被占位符规则覆盖的反斜杠控制片段必须原样保留 (原文: {}; 译文: {})",
            format_control_counts(&original_raw_controls),
            format_control_counts(&translated_raw_controls),
        )));
    }
    check_japanese_residual_for_item(item, &masked_translation_lines, residual_rule, text_rules)?;

    Ok(TranslationItemRecord {
        location_path: item.location_path.clone(),
        item_type: item.item_type.clone(),
        role: item.role.clone(),
        original_lines: item.original_lines.clone(),
        source_line_paths: item.source_line_paths.clone(),
        translation_lines: normalized_translation_lines,
    })
}

#[cfg(test)]
pub(crate) fn normalize_manual_translation_lines(
    item: &ActiveTextItem,
    translation_lines: &[String],
    text_rules: &TextRuleOptions,
) -> Result<Vec<String>> {
    let width_pattern = compile_line_width_pattern(text_rules)?;
    normalize_manual_translation_lines_with_pattern(
        item,
        translation_lines,
        text_rules,
        &width_pattern,
    )
}

pub(crate) fn normalize_manual_translation_lines_with_pattern(
    item: &ActiveTextItem,
    translation_lines: &[String],
    text_rules: &TextRuleOptions,
    width_pattern: &Regex,
) -> Result<Vec<String>> {
    let cleaned_lines = translation_lines
        .iter()
        .map(|line| line.trim().to_string())
        .collect::<Vec<_>>();
    let normalized_lines =
        normalize_translated_wrapping_punctuation(&item.original_lines, &cleaned_lines, text_rules);
    if item.item_type != "long_text" {
        return Ok(normalized_lines);
    }
    split_overwide_lines(&normalized_lines, text_rules, width_pattern)
}

pub(crate) fn compile_line_width_pattern(text_rules: &TextRuleOptions) -> Result<Regex> {
    Regex::new(&text_rules.line_width_count_pattern).map_err(|error| {
        AttMzError::InvalidConfig(format!("text_rules.line_width_count_pattern 无效: {error}"))
    })
}

fn split_overwide_lines(
    lines: &[String],
    text_rules: &TextRuleOptions,
    width_pattern: &Regex,
) -> Result<Vec<String>> {
    let mut output = Vec::new();
    let mut active_wrapping_pair: Option<(String, String)> = None;
    for line in lines {
        if line.is_empty() {
            output.push(line.clone());
            continue;
        }
        let opening_pair = find_opening_wrapping_pair(line, text_rules);
        let current_wrapping_pair = active_wrapping_pair.clone().or(opening_pair);
        let first_line_prefix = if active_wrapping_pair.is_some() {
            WRAPPING_CONTINUATION_INDENT
        } else {
            ""
        };
        let wrapped_tail_prefix = if current_wrapping_pair.is_some() {
            WRAPPING_CONTINUATION_INDENT
        } else {
            ""
        };
        output.extend(split_single_overwide_line(
            line,
            text_rules,
            width_pattern,
            first_line_prefix,
            wrapped_tail_prefix,
        ));
        if let Some(pair) = current_wrapping_pair {
            active_wrapping_pair = (!closes_wrapping_pair(line, &pair, text_rules)).then_some(pair);
        } else {
            active_wrapping_pair = None;
        }
    }
    Ok(output)
}

fn split_single_overwide_line(
    line: &str,
    text_rules: &TextRuleOptions,
    width_pattern: &Regex,
    first_line_prefix: &str,
    wrapped_tail_prefix: &str,
) -> Vec<String> {
    let mut result = Vec::new();
    let mut pending_line = prepend_continuation_prefix(line, first_line_prefix);
    while count_line_width(&pending_line, width_pattern) > text_rules.long_text_line_width_limit {
        let split_index = find_preferred_split_position(&pending_line, text_rules, width_pattern)
            .or_else(|| find_hard_split_position(&pending_line, text_rules, width_pattern));
        let Some(split_index) = split_index else {
            break;
        };
        if split_index == 0 || split_index >= pending_line.len() {
            break;
        }
        let head = pending_line[..split_index].trim_end().to_string();
        let tail = pending_line[split_index..].trim_start().to_string();
        if head.is_empty() || tail.is_empty() {
            break;
        }
        result.push(head);
        pending_line = prepend_continuation_prefix(&tail, wrapped_tail_prefix);
    }
    result.push(pending_line);
    result
}

fn count_line_width(text: &str, width_pattern: &Regex) -> usize {
    let protected_spans = protected_control_spans(text);
    text.char_indices()
        .filter(|(byte_index, char_value)| {
            !is_inside_span(*byte_index, &protected_spans)
                && is_line_width_counted_char(*char_value, width_pattern)
        })
        .count()
}

fn normalize_translated_wrapping_punctuation(
    original_lines: &[String],
    translation_lines: &[String],
    text_rules: &TextRuleOptions,
) -> Vec<String> {
    if !has_preserved_wrapping_chars(original_lines, text_rules) {
        return translation_lines.to_vec();
    }
    let normalized_lines = normalize_translated_outer_wrapping_punctuation(
        original_lines,
        translation_lines,
        text_rules,
    );
    normalize_aligned_wrapping_spans(original_lines, &normalized_lines, text_rules)
}

fn normalize_translated_outer_wrapping_punctuation(
    original_lines: &[String],
    translation_lines: &[String],
    text_rules: &TextRuleOptions,
) -> Vec<String> {
    let Some((source_left, source_right)) =
        find_source_outer_wrapping_pair(original_lines, text_rules)
    else {
        return translation_lines.to_vec();
    };
    let mut normalized_lines = translation_lines.to_vec();
    let first_boundary = find_first_visible_boundary(&normalized_lines);
    let last_boundary = find_last_visible_boundary(&normalized_lines);
    let (Some(first_boundary), Some(last_boundary)) = (first_boundary, last_boundary) else {
        return normalized_lines;
    };
    if first_boundary.char_value != source_left
        && TRANSLATED_WRAPPING_LEFT_CHARS.contains(&first_boundary.char_value)
    {
        normalized_lines[first_boundary.line_index] = replace_char_at(
            &normalized_lines[first_boundary.line_index],
            first_boundary.byte_index,
            &source_left.to_string(),
        );
    }
    if last_boundary.char_value != source_right
        && TRANSLATED_WRAPPING_RIGHT_CHARS.contains(&last_boundary.char_value)
    {
        normalized_lines[last_boundary.line_index] = replace_char_at(
            &normalized_lines[last_boundary.line_index],
            last_boundary.byte_index,
            &source_right.to_string(),
        );
    }
    normalized_lines
}

fn normalize_aligned_wrapping_spans(
    original_lines: &[String],
    translation_lines: &[String],
    text_rules: &TextRuleOptions,
) -> Vec<String> {
    let source_pairs = single_char_wrapping_pairs(&text_rules.preserve_wrapping_punctuation_pairs);
    let source_spans = collect_wrapping_spans(original_lines, &source_pairs);
    if source_spans.is_empty() {
        return translation_lines.to_vec();
    }
    let translated_source_spans = collect_wrapping_spans(translation_lines, &source_pairs);
    let alternative_pairs = build_alternative_wrapping_pairs(&source_pairs);
    let translated_alternative_spans =
        collect_wrapping_spans(translation_lines, &alternative_pairs);
    if has_unpaired_wrapping_chars(translation_lines, &source_pairs, &translated_source_spans)
        || has_unpaired_wrapping_chars(
            translation_lines,
            &alternative_pairs,
            &translated_alternative_spans,
        )
    {
        return translation_lines.to_vec();
    }
    let mut translated_spans = translated_source_spans;
    translated_spans.extend(translated_alternative_spans);
    translated_spans.sort_by_key(|span| {
        (
            span.left.line_index,
            span.left.byte_index,
            span.right.line_index,
            span.right.byte_index,
        )
    });
    if source_spans.len() != translated_spans.len() {
        return translation_lines.to_vec();
    }
    let mut normalized_lines = translation_lines.to_vec();
    for (source_span, translated_span) in source_spans.iter().zip(translated_spans.iter()) {
        let (source_left, source_right) = source_span.pair;
        if translated_span.left.char_value != source_left {
            normalized_lines[translated_span.left.line_index] = replace_char_at(
                &normalized_lines[translated_span.left.line_index],
                translated_span.left.byte_index,
                &source_left.to_string(),
            );
        }
        if translated_span.right.char_value != source_right {
            normalized_lines[translated_span.right.line_index] = replace_char_at(
                &normalized_lines[translated_span.right.line_index],
                translated_span.right.byte_index,
                &source_right.to_string(),
            );
        }
    }
    normalized_lines
}

fn find_preferred_split_position(
    text: &str,
    text_rules: &TextRuleOptions,
    width_pattern: &Regex,
) -> Option<usize> {
    let protected_spans = protected_control_spans(text);
    let limit = text_rules.long_text_line_width_limit;
    let min_preferred_width = ((limit as f64) * 0.45).floor().max(1.0) as usize;
    let mut width = 0usize;
    let mut before_limit_positions = Vec::new();
    let mut preferred_before_limit_positions = Vec::new();
    for (byte_index, char_value) in text.char_indices() {
        if is_inside_span(byte_index, &protected_spans) {
            continue;
        }
        if is_line_width_counted_char(char_value, width_pattern) {
            width += 1;
        }
        let next_index = byte_index + char_value.len_utf8();
        let is_split_punctuation = text_rules
            .line_split_punctuations
            .iter()
            .any(|punctuation| punctuation == &char_value.to_string());
        if is_split_punctuation && width >= min_preferred_width && width <= limit {
            preferred_before_limit_positions.push(next_index);
        }
        if is_split_punctuation && width <= limit {
            before_limit_positions.push(next_index);
        }
        if width > limit {
            break;
        }
    }
    select_split_position_with_readable_tail(
        text,
        if preferred_before_limit_positions.is_empty() {
            &before_limit_positions
        } else {
            &preferred_before_limit_positions
        },
        text_rules,
        width_pattern,
    )
}

fn select_split_position_with_readable_tail(
    text: &str,
    candidates: &[usize],
    text_rules: &TextRuleOptions,
    width_pattern: &Regex,
) -> Option<usize> {
    if candidates.is_empty() {
        return None;
    }
    let min_tail_width = 4usize.min((text_rules.long_text_line_width_limit / 4).max(1));
    for position in candidates.iter().rev() {
        let tail = text[*position..].trim_start();
        if count_line_width(tail, width_pattern) >= min_tail_width {
            return Some(*position);
        }
    }
    candidates.last().copied()
}

fn find_hard_split_position(
    text: &str,
    text_rules: &TextRuleOptions,
    width_pattern: &Regex,
) -> Option<usize> {
    let protected_spans = protected_control_spans(text);
    let mut width = 0usize;
    for (byte_index, char_value) in text.char_indices() {
        if is_inside_span(byte_index, &protected_spans)
            || !is_line_width_counted_char(char_value, width_pattern)
        {
            continue;
        }
        width += 1;
        if width < text_rules.long_text_line_width_limit {
            continue;
        }
        let split_position = move_split_position_outside_protected_span(
            byte_index + char_value.len_utf8(),
            &protected_spans,
        );
        let extended_position = extend_split_position_through_trailing_punctuation(
            text,
            split_position,
            text_rules,
            &protected_spans,
        );
        if extended_position >= text.len()
            && count_line_width(text, width_pattern) > text_rules.long_text_line_width_limit
        {
            return find_readable_hard_split_position(
                text,
                split_position,
                text_rules,
                width_pattern,
                &protected_spans,
            );
        }
        return Some(extended_position);
    }
    None
}

fn find_readable_hard_split_position(
    text: &str,
    max_position: usize,
    text_rules: &TextRuleOptions,
    width_pattern: &Regex,
    protected_spans: &[(usize, usize)],
) -> Option<usize> {
    let min_tail_width = 4usize.min((text_rules.long_text_line_width_limit / 4).max(1));
    let mut candidates = Vec::new();
    for (byte_index, char_value) in text.char_indices() {
        let position = byte_index + char_value.len_utf8();
        if position > max_position || position >= text.len() {
            break;
        }
        if is_inside_span(byte_index, protected_spans)
            || !is_line_width_counted_char(char_value, width_pattern)
        {
            continue;
        }
        let tail = text[position..].trim_start();
        if tail.is_empty() || starts_with_split_punctuation(tail, text_rules) {
            continue;
        }
        if count_line_width(tail, width_pattern) >= min_tail_width {
            candidates.push(position);
        }
    }
    candidates.last().copied()
}

fn extend_split_position_through_trailing_punctuation(
    text: &str,
    position: usize,
    text_rules: &TextRuleOptions,
    protected_spans: &[(usize, usize)],
) -> usize {
    let mut next_position = position;
    while next_position < text.len() {
        if is_inside_span(next_position, protected_spans) {
            break;
        }
        let Some(char_value) = text[next_position..].chars().next() else {
            break;
        };
        if !text_rules
            .line_split_punctuations
            .iter()
            .any(|punctuation| punctuation == &char_value.to_string())
        {
            break;
        }
        next_position += char_value.len_utf8();
    }
    next_position
}

fn starts_with_split_punctuation(text: &str, text_rules: &TextRuleOptions) -> bool {
    text.chars().next().is_some_and(|char_value| {
        text_rules
            .line_split_punctuations
            .iter()
            .any(|punctuation| punctuation == &char_value.to_string())
    })
}

fn is_line_width_counted_char(char_value: char, width_pattern: &Regex) -> bool {
    let text = char_value.to_string();
    width_pattern
        .find(&text)
        .is_some_and(|matched| matched.start() == 0 && matched.end() == text.len())
}

fn has_preserved_wrapping_chars(lines: &[String], text_rules: &TextRuleOptions) -> bool {
    lines.iter().any(|line| {
        text_rules
            .preserve_wrapping_punctuation_pairs
            .iter()
            .any(|(left, right)| line.contains(left) || line.contains(right))
    })
}

fn find_source_outer_wrapping_pair(
    original_lines: &[String],
    text_rules: &TextRuleOptions,
) -> Option<(char, char)> {
    let first_boundary = find_first_visible_boundary(original_lines)?;
    let last_boundary = find_last_visible_boundary(original_lines)?;
    for (left, right) in &text_rules.preserve_wrapping_punctuation_pairs {
        let (Some(left_char), Some(right_char)) = (single_char(left), single_char(right)) else {
            continue;
        };
        if first_boundary.char_value == left_char && last_boundary.char_value == right_char {
            return Some((left_char, right_char));
        }
    }
    None
}

fn find_first_visible_boundary(lines: &[String]) -> Option<BoundaryChar> {
    for (line_index, line) in lines.iter().enumerate() {
        if let Some(boundary) = find_visible_boundary_in_line(line, false) {
            return Some(BoundaryChar {
                line_index,
                byte_index: boundary.byte_index,
                char_value: boundary.char_value,
            });
        }
    }
    None
}

fn find_last_visible_boundary(lines: &[String]) -> Option<BoundaryChar> {
    for (reverse_index, line) in lines.iter().rev().enumerate() {
        if let Some(boundary) = find_visible_boundary_in_line(line, true) {
            return Some(BoundaryChar {
                line_index: lines.len() - reverse_index - 1,
                byte_index: boundary.byte_index,
                char_value: boundary.char_value,
            });
        }
    }
    None
}

fn find_visible_boundary_in_line(line: &str, reverse: bool) -> Option<BoundaryChar> {
    let protected_spans = protected_control_spans(line);
    if reverse {
        return find_visible_boundary_from_right(line, &protected_spans);
    }
    find_visible_boundary_from_left(line, &protected_spans)
}

fn find_visible_boundary_from_left(
    line: &str,
    protected_spans: &[(usize, usize)],
) -> Option<BoundaryChar> {
    let mut index = 0usize;
    while index < line.len() {
        if let Some((_, end)) = find_containing_span(index, protected_spans) {
            index = end;
            continue;
        }
        let Some(char_value) = line[index..].chars().next() else {
            break;
        };
        if !char_value.is_whitespace() {
            return Some(BoundaryChar {
                line_index: 0,
                byte_index: index,
                char_value,
            });
        }
        index += char_value.len_utf8();
    }
    None
}

fn find_visible_boundary_from_right(
    line: &str,
    protected_spans: &[(usize, usize)],
) -> Option<BoundaryChar> {
    let mut index = line.len();
    while index > 0 {
        let Some((byte_index, char_value)) = previous_char(line, index) else {
            break;
        };
        if let Some((start, _)) = find_containing_span(byte_index, protected_spans) {
            index = start;
            continue;
        }
        if !char_value.is_whitespace() {
            return Some(BoundaryChar {
                line_index: 0,
                byte_index,
                char_value,
            });
        }
        index = byte_index;
    }
    None
}

fn collect_visible_chars(lines: &[String]) -> Vec<BoundaryChar> {
    let mut visible_chars = Vec::new();
    for (line_index, line) in lines.iter().enumerate() {
        let protected_spans = protected_control_spans(line);
        let mut index = 0usize;
        while index < line.len() {
            if let Some((_, end)) = find_containing_span(index, &protected_spans) {
                index = end;
                continue;
            }
            let Some(char_value) = line[index..].chars().next() else {
                break;
            };
            if !char_value.is_whitespace() {
                visible_chars.push(BoundaryChar {
                    line_index,
                    byte_index: index,
                    char_value,
                });
            }
            index += char_value.len_utf8();
        }
    }
    visible_chars
}

fn collect_wrapping_spans(
    lines: &[String],
    pair_definitions: &[(char, char)],
) -> Vec<WrappingSpan> {
    let visible_chars = collect_visible_chars(lines);
    let different_pairs = pair_definitions
        .iter()
        .copied()
        .filter(|(left, right)| left != right)
        .collect::<Vec<_>>();
    let same_pairs = pair_definitions
        .iter()
        .copied()
        .filter(|(left, right)| left == right)
        .collect::<Vec<_>>();
    let mut spans = collect_different_char_wrapping_spans(&visible_chars, &different_pairs);
    spans.extend(collect_same_char_wrapping_spans(
        &visible_chars,
        &same_pairs,
    ));
    spans.sort_by_key(|span| {
        (
            span.left.line_index,
            span.left.byte_index,
            span.right.line_index,
            span.right.byte_index,
        )
    });
    spans
}

fn collect_different_char_wrapping_spans(
    visible_chars: &[BoundaryChar],
    pair_definitions: &[(char, char)],
) -> Vec<WrappingSpan> {
    let mut spans = Vec::new();
    let mut stack: Vec<(BoundaryChar, (char, char))> = Vec::new();
    for boundary in visible_chars {
        if let Some(pair) = pair_definitions
            .iter()
            .copied()
            .find(|(left, _)| *left == boundary.char_value)
        {
            stack.push((*boundary, pair));
            continue;
        }
        if !pair_definitions
            .iter()
            .any(|(_, right)| *right == boundary.char_value)
        {
            continue;
        }
        let Some((left_boundary, expected_pair)) = stack.last().copied() else {
            continue;
        };
        if expected_pair.1 != boundary.char_value {
            continue;
        }
        let _ = stack.pop();
        spans.push(WrappingSpan {
            left: left_boundary,
            right: *boundary,
            pair: expected_pair,
        });
    }
    spans
}

fn collect_same_char_wrapping_spans(
    visible_chars: &[BoundaryChar],
    pair_definitions: &[(char, char)],
) -> Vec<WrappingSpan> {
    let quote_chars = pair_definitions
        .iter()
        .map(|(left, _)| *left)
        .collect::<BTreeSet<_>>();
    let mut open_boundaries: BTreeMap<char, BoundaryChar> = BTreeMap::new();
    let mut spans = Vec::new();
    for boundary in visible_chars {
        if !quote_chars.contains(&boundary.char_value) {
            continue;
        }
        if let Some(open_boundary) = open_boundaries.remove(&boundary.char_value) {
            spans.push(WrappingSpan {
                left: open_boundary,
                right: *boundary,
                pair: (boundary.char_value, boundary.char_value),
            });
        } else {
            open_boundaries.insert(boundary.char_value, *boundary);
        }
    }
    spans
}

fn has_unpaired_wrapping_chars(
    lines: &[String],
    pair_definitions: &[(char, char)],
    spans: &[WrappingSpan],
) -> bool {
    let wrapping_chars = pair_definitions
        .iter()
        .flat_map(|(left, right)| [*left, *right])
        .collect::<BTreeSet<_>>();
    if wrapping_chars.is_empty() {
        return false;
    }
    let paired_positions = spans
        .iter()
        .flat_map(|span| {
            [
                (span.left.line_index, span.left.byte_index),
                (span.right.line_index, span.right.byte_index),
            ]
        })
        .collect::<BTreeSet<_>>();
    collect_visible_chars(lines).into_iter().any(|boundary| {
        wrapping_chars.contains(&boundary.char_value)
            && !paired_positions.contains(&(boundary.line_index, boundary.byte_index))
    })
}

fn find_opening_wrapping_pair(
    line: &str,
    text_rules: &TextRuleOptions,
) -> Option<(String, String)> {
    let stripped_line = build_wrapping_check_line(line);
    text_rules
        .preserve_wrapping_punctuation_pairs
        .iter()
        .find(|(left, _)| stripped_line.starts_with(left))
        .cloned()
}

fn closes_wrapping_pair(
    line: &str,
    wrapping_pair: &(String, String),
    _text_rules: &TextRuleOptions,
) -> bool {
    let stripped_line = build_wrapping_check_line(line);
    stripped_line.ends_with(&wrapping_pair.1)
}

fn build_wrapping_check_line(line: &str) -> String {
    strip_protected_controls(line).trim().to_string()
}

fn strip_protected_controls(text: &str) -> String {
    let spans = protected_control_spans(text);
    if spans.is_empty() {
        return text.to_string();
    }
    let mut output = String::new();
    let mut last_end = 0usize;
    for (start, end) in spans {
        if start >= last_end {
            output.push_str(&text[last_end..start]);
            last_end = end;
        }
    }
    output.push_str(&text[last_end..]);
    output
}

fn prepend_continuation_prefix(line: &str, prefix: &str) -> String {
    if prefix.is_empty() || line.is_empty() || line.starts_with(prefix) {
        return line.to_string();
    }
    if line.chars().next().is_some_and(char::is_whitespace) {
        return line.to_string();
    }
    format!("{prefix}{line}")
}

fn build_alternative_wrapping_pairs(source_pairs: &[(char, char)]) -> Vec<(char, char)> {
    TRANSLATED_WRAPPING_QUOTE_PAIRS
        .iter()
        .copied()
        .filter(|pair| !source_pairs.contains(pair))
        .collect()
}

fn single_char_wrapping_pairs(pairs: &[(String, String)]) -> Vec<(char, char)> {
    pairs
        .iter()
        .filter_map(|(left, right)| Some((single_char(left)?, single_char(right)?)))
        .collect()
}

fn single_char(text: &str) -> Option<char> {
    let mut chars = text.chars();
    let char_value = chars.next()?;
    chars.next().is_none().then_some(char_value)
}

fn replace_char_at(text: &str, byte_index: usize, replacement: &str) -> String {
    let Some(char_value) = text[byte_index..].chars().next() else {
        return text.to_string();
    };
    let next_index = byte_index + char_value.len_utf8();
    format!(
        "{}{}{}",
        &text[..byte_index],
        replacement,
        &text[next_index..]
    )
}

fn protected_control_spans(text: &str) -> Vec<(usize, usize)> {
    let Ok(pattern) = Regex::new(
        r"\\[A-Za-z]+\d*(?:\[[^\]\r\n]{0,64}\])?|\\[{}\\$.\|!><^]|\[(?:RMMZ|CUSTOM)_[A-Z0-9_]+(?:_\d+)?\]",
    ) else {
        return Vec::new();
    };
    pattern
        .find_iter(text)
        .map(|matched| (matched.start(), matched.end()))
        .collect()
}

fn is_inside_span(byte_index: usize, spans: &[(usize, usize)]) -> bool {
    spans
        .iter()
        .any(|(start, end)| byte_index >= *start && byte_index < *end)
}

fn find_containing_span(byte_index: usize, spans: &[(usize, usize)]) -> Option<(usize, usize)> {
    spans
        .iter()
        .copied()
        .find(|(start, end)| byte_index >= *start && byte_index < *end)
}

fn previous_char(text: &str, end_index: usize) -> Option<(usize, char)> {
    text[..end_index].char_indices().next_back()
}

fn move_split_position_outside_protected_span(
    split_index: usize,
    spans: &[(usize, usize)],
) -> usize {
    spans
        .iter()
        .find_map(|(start, end)| (split_index > *start && split_index < *end).then_some(*end))
        .unwrap_or(split_index)
}

pub(crate) fn validate_translation_text_structure(
    item: &ActiveTextItem,
    original_lines_with_placeholders: &[String],
    translation_lines: &[String],
    translation_lines_with_placeholders: &[String],
) -> Result<()> {
    let mut errors = Vec::new();
    let joined_text = translation_lines.join("\n");
    if joined_text.contains(&item.location_path) {
        errors.push("译文包含文本在游戏里的内部位置，不能写进游戏文件".to_string());
    }
    for line in translation_lines {
        let stripped_line = line.trim();
        let lowered_line = stripped_line.to_lowercase();
        if ["译文：", "译文:", "翻译：", "翻译:"]
            .iter()
            .any(|prefix| stripped_line.starts_with(prefix))
        {
            errors.push("译文包含明显解释性前缀，不是可写入游戏的正文".to_string());
            break;
        }
        if stripped_line.contains("以下是翻译") {
            errors.push("译文包含明显解释性说明，不是可写入游戏的正文".to_string());
            break;
        }
        if [
            "id:",
            "id：",
            "\"id\":",
            "source_lines:",
            "source_lines：",
            "\"source_lines\":",
            "translation_lines:",
            "translation_lines：",
            "\"translation_lines\":",
        ]
        .iter()
        .any(|prefix| lowered_line.starts_with(prefix))
        {
            errors.push("译文包含模型输出协议字段，不是可写入游戏的正文".to_string());
            break;
        }
    }

    if item.item_type == "short_text" {
        if translation_lines.len() != 1 {
            errors.push(format!(
                "单字段文本必须只提供 1 条中文译文行，当前提供 {} 条",
                translation_lines.len()
            ));
        } else {
            let original_real_break_count =
                count_real_line_breaks(original_lines_with_placeholders);
            let translation_real_break_count =
                count_real_line_breaks(translation_lines_with_placeholders);
            if original_real_break_count != translation_real_break_count {
                errors.push(format!(
                    "译文真实换行数量不一致（原文 {original_real_break_count} 个，译文 {translation_real_break_count} 个）"
                ));
            }
            let original_literal_break_count =
                count_literal_line_breaks(original_lines_with_placeholders);
            let translation_literal_break_count =
                count_literal_line_breaks(translation_lines_with_placeholders);
            if original_literal_break_count != translation_literal_break_count {
                errors.push(format!(
                    "译文字面量换行标记数量不一致（原文 {original_literal_break_count} 个，译文 {translation_literal_break_count} 个）"
                ));
            }
        }
    }

    if errors.is_empty() {
        Ok(())
    } else {
        Err(AttMzError::InvalidConfig(errors.join(";\n")))
    }
}

fn count_real_line_breaks(lines: &[String]) -> usize {
    if lines.is_empty() {
        return 0;
    }
    lines.join("\n").matches('\n').count()
        + lines
            .iter()
            .map(|line| line.matches("[RMMZ_REAL_LINE_BREAK]").count())
            .sum::<usize>()
}

fn count_literal_line_breaks(lines: &[String]) -> usize {
    lines
        .iter()
        .map(|line| line.matches(r"\n").count() + line.matches("[RMMZ_LITERAL_LINE_BREAK]").count())
        .sum()
}

pub(crate) fn check_japanese_residual_for_item(
    item: &ActiveTextItem,
    translation_lines: &[String],
    residual_rule: Option<&JapaneseResidualRuleRecord>,
    text_rules: &TextRuleOptions,
) -> Result<()> {
    let allowed_terms = residual_rule
        .map(|record| record.allowed_terms.as_slice())
        .unwrap_or(&[]);
    let checked_lines = mask_japanese_residual_allowed_terms(translation_lines, allowed_terms);
    let japanese_segment_pattern =
        Regex::new(&text_rules.japanese_segment_pattern).map_err(|error| {
            AttMzError::InvalidConfig(format!("text_rules.japanese_segment_pattern 无效: {error}"))
        })?;
    let residual_escape_pattern = Regex::new(&text_rules.residual_escape_sequence_pattern)
        .map_err(|error| {
            AttMzError::InvalidConfig(format!(
                "text_rules.residual_escape_sequence_pattern 无效: {error}"
            ))
        })?;
    let placeholder_pattern =
        Regex::new(r"(?i)\[RMMZ_[A-Z0-9_]+\]|\[CUSTOM_[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_\d+\]")
            .map_err(|error| {
                AttMzError::InvalidConfig(format!("日文残留占位符正则无效: {error}"))
            })?;
    let allowed_chars = text_rules
        .allowed_japanese_chars
        .iter()
        .flat_map(|value| value.chars())
        .collect::<BTreeSet<_>>();
    let allowed_tail_chars = text_rules
        .allowed_japanese_tail_chars
        .iter()
        .flat_map(|value| value.chars())
        .collect::<BTreeSet<_>>();

    for (line_index, line) in checked_lines.iter().enumerate() {
        let placeholder_cleaned = placeholder_pattern.replace_all(line, "");
        let cleaned_line = residual_escape_pattern.replace_all(&placeholder_cleaned, " ");
        let segments = japanese_segment_pattern
            .find_iter(&cleaned_line)
            .map(|matched| matched.as_str().to_string())
            .collect::<Vec<_>>();
        if segments.is_empty() {
            continue;
        }
        let text_without_japanese = japanese_segment_pattern.replace_all(&cleaned_line, "");
        let has_non_japanese_content = text_without_japanese.chars().any(char::is_alphanumeric);
        let mut real_residual = Vec::new();
        for segment in segments {
            let filtered_segment = segment
                .chars()
                .filter(|char_value| !allowed_chars.contains(char_value))
                .collect::<Vec<_>>();
            if filtered_segment.is_empty() {
                if !has_non_japanese_content {
                    real_residual.extend(segment.chars());
                }
                continue;
            }
            if has_non_japanese_content
                && filtered_segment
                    .iter()
                    .all(|char_value| allowed_tail_chars.contains(char_value))
            {
                continue;
            }
            real_residual.extend(filtered_segment);
        }
        if !real_residual.is_empty() {
            return Err(AttMzError::InvalidConfig(format!(
                "{} 发现日文残留(第 {} 行): {:?}",
                item.location_path,
                line_index + 1,
                real_residual
            )));
        }
    }
    Ok(())
}

fn mask_japanese_residual_allowed_terms(lines: &[String], allowed_terms: &[String]) -> Vec<String> {
    if allowed_terms.is_empty() {
        return lines.to_vec();
    }
    let mut sorted_terms = allowed_terms.to_vec();
    sorted_terms.sort_by_key(|term| std::cmp::Reverse(term.len()));
    lines
        .iter()
        .map(|line| {
            let mut masked_line = line.clone();
            for term in &sorted_terms {
                masked_line = masked_line.replace(term, " ");
            }
            masked_line
        })
        .collect()
}

fn format_control_counts(counts: &BTreeMap<String, usize>) -> String {
    if counts.is_empty() {
        return "无".to_string();
    }
    counts
        .iter()
        .map(|(marker, count)| format!("{marker}×{count}"))
        .collect::<Vec<_>>()
        .join("、")
}

fn read_reset_translation_location_paths(input_path: &Path) -> Result<Vec<String>> {
    let payload = read_json_object_file(input_path, "reset-translations")?;
    let Some(raw_paths) = payload.get("location_paths") else {
        return Err(AttMzError::InvalidConfig(
            "reset-translations.location_paths 必须是字符串数组".to_string(),
        ));
    };
    let location_paths = json_string_array(raw_paths, "reset-translations.location_paths")?;
    if location_paths.is_empty() {
        return Err(AttMzError::InvalidConfig(
            "location_paths 不能为空".to_string(),
        ));
    }
    let mut counts = BTreeMap::new();
    for path in &location_paths {
        *counts.entry(path.clone()).or_insert(0usize) += 1;
    }
    let duplicate_paths = counts
        .into_iter()
        .filter_map(|(path, count)| if count > 1 { Some(path) } else { None })
        .collect::<Vec<_>>();
    if !duplicate_paths.is_empty() {
        return Err(AttMzError::InvalidConfig(format!(
            "location_paths 不得重复: {}",
            duplicate_paths.join("、")
        )));
    }
    Ok(location_paths)
}

fn collect_active_translation_location_paths(items: &[ActiveTextItem]) -> Vec<String> {
    let mut location_paths = Vec::new();
    let mut seen_paths = BTreeSet::new();
    for item in items {
        if seen_paths.insert(item.location_path.clone()) {
            location_paths.push(item.location_path.clone());
        }
    }
    location_paths
}

fn reset_translation_summary(
    input_path: Option<&Path>,
    mode: &str,
    requested_count: usize,
    reset_count: usize,
) -> Map<String, Value> {
    let mut summary = Map::new();
    summary.insert(
        "input".to_string(),
        json!(
            input_path
                .map(|path| path.display().to_string())
                .unwrap_or_default()
        ),
    );
    summary.insert("mode".to_string(), json!(mode));
    summary.insert("requested_count".to_string(), json!(requested_count));
    summary.insert("reset_count".to_string(), json!(reset_count));
    summary
}

fn read_json_object_file(path: &Path, context: &str) -> Result<Map<String, Value>> {
    let raw_text = fs::read_to_string(path)
        .map_err(|source| AttMzError::io(format!("读取 {}", path.display()), source))?;
    let value: Value =
        serde_json::from_str(raw_text.trim_start_matches('\u{feff}')).map_err(|source| {
            AttMzError::Json {
                context: context.to_string(),
                source,
            }
        })?;
    let Some(object) = value.as_object() else {
        return Err(AttMzError::InvalidConfig(format!(
            "{context} 顶层必须是对象"
        )));
    };
    Ok(object.clone())
}

fn json_string_array(value: &Value, context: &str) -> Result<Vec<String>> {
    let Some(array) = value.as_array() else {
        return Err(AttMzError::InvalidConfig(format!(
            "{context} 必须是字符串数组"
        )));
    };
    let mut lines = Vec::new();
    for item in array {
        let Some(text) = item.as_str() else {
            return Err(AttMzError::InvalidConfig(format!(
                "{context} 只能包含字符串"
            )));
        };
        lines.push(text.to_string());
    }
    Ok(lines)
}

fn read_fresh_plugin_rules(
    registry: &GameRegistry,
    game_record: &GameRecord,
    plugins: &[Value],
) -> Result<(Vec<PluginRuleRecord>, usize)> {
    let rules = registry.read_plugin_text_rules(&game_record.game_title)?;
    let mut fresh_rules = Vec::new();
    let mut stale_count = 0usize;
    for rule in rules {
        let Some(plugin) = plugins.get(rule.plugin_index) else {
            stale_count += 1;
            continue;
        };
        if rule.plugin_hash != build_plugin_hash(plugin)? {
            stale_count += 1;
            continue;
        }
        fresh_rules.push(rule);
    }
    Ok((fresh_rules, stale_count))
}

fn quality_error_counts(
    quality_errors: &[TranslationQualityErrorSummary],
) -> BTreeMap<String, usize> {
    let mut counts = BTreeMap::new();
    for error in quality_errors {
        *counts.entry(error.error_type.clone()).or_default() += 1;
    }
    counts
}

fn write_json_file(path: &Path, value: &Value) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|source| AttMzError::io(format!("创建目录 {}", parent.display()), source))?;
    }
    let text = serde_json::to_string_pretty(value).map_err(|source| AttMzError::Json {
        context: format!("序列化 JSON {}", path.display()),
        source,
    })?;
    fs::write(path, format!("{text}\n"))
        .map_err(|source| AttMzError::io(format!("写入 {}", path.display()), source))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN;

    #[test]
    fn manual_template_replaces_control_codes_for_model_view() {
        let item = ActiveTextItem {
            location_path: "Map001.json/1/0/0".to_string(),
            item_type: "long_text".to_string(),
            role: Some("旁白".to_string()),
            display_name: None,
            original_lines: vec![r"\F[GuideA]こんにちは\!".to_string()],
            source_line_paths: Vec::new(),
        };
        let rules = vec![PlaceholderRule {
            pattern_text: r"\\F\[[^\]]+\]".to_string(),
            placeholder_template: "[CUSTOM_FACE_PORTRAIT_{index}]".to_string(),
        }];

        let entry = manual_translation_template_entry(&item, &rules).expect("模板应生成成功");

        assert_eq!(
            entry["text_for_model_lines"][0],
            "[CUSTOM_FACE_PORTRAIT_1]こんにちは[RMMZ_WAIT_INPUT]"
        );
        assert_eq!(entry["translation_lines"], json!([]));
    }

    #[test]
    fn manual_template_restores_prefilled_translation_controls() {
        let item = ActiveTextItem {
            location_path: "Map001.json/1/0/0".to_string(),
            item_type: "long_text".to_string(),
            role: Some("旁白".to_string()),
            display_name: None,
            original_lines: vec![r"\F[GuideA]こんにちは\!".to_string()],
            source_line_paths: Vec::new(),
        };
        let rules = vec![PlaceholderRule {
            pattern_text: r"\\F\[[^\]]+\]".to_string(),
            placeholder_template: "[CUSTOM_FACE_PORTRAIT_{index}]".to_string(),
        }];

        let entry = manual_translation_template_entry_with_translation_lines(
            &item,
            &rules,
            &["[CUSTOM_FACE_PORTRAIT_1]你好[RMMZ_WAIT_INPUT]".to_string()],
        )
        .expect("带预填译文的模板应生成成功");

        assert_eq!(entry["translation_lines"][0], r"\F[GuideA]你好\!");
    }

    #[test]
    fn long_text_split_prefers_configured_punctuation_and_preserves_wrapping() {
        let item = ActiveTextItem {
            location_path: "Map001.json/1/0/0".to_string(),
            item_type: "long_text".to_string(),
            role: Some("旁白".to_string()),
            display_name: None,
            original_lines: vec!["「これは長い原文」".to_string()],
            source_line_paths: Vec::new(),
        };
        let options = TextRuleOptions {
            long_text_line_width_limit: 6,
            line_split_punctuations: vec!["，".to_string()],
            preserve_wrapping_punctuation_pairs: vec![("「".to_string(), "」".to_string())],
            ..TextRuleOptions::default()
        };

        let lines = normalize_manual_translation_lines(
            &item,
            &["“这是很长，必须切行”".to_string()],
            &options,
        )
        .expect("长文本应可规范化");

        assert_eq!(lines, vec!["「这是很长，", "　必须切行」"]);
    }

    #[test]
    fn wrapping_normalization_repairs_outer_and_inner_quote_pairs() {
        let item = ActiveTextItem {
            location_path: "Map001.json/1/0/0".to_string(),
            item_type: "long_text".to_string(),
            role: Some("旁白".to_string()),
            display_name: None,
            original_lines: vec!["『これは「名前」です』".to_string()],
            source_line_paths: Vec::new(),
        };
        let options = TextRuleOptions {
            preserve_wrapping_punctuation_pairs: vec![
                ("『".to_string(), "』".to_string()),
                ("「".to_string(), "」".to_string()),
            ],
            ..TextRuleOptions::default()
        };

        let lines =
            normalize_manual_translation_lines(&item, &["“这是『名字』”".to_string()], &options)
                .expect("包裹标点应可规范化");

        assert_eq!(lines, vec!["『这是「名字」』"]);
    }

    #[test]
    fn long_text_split_keeps_tail_readable_and_protects_control_spans() {
        let item = ActiveTextItem {
            location_path: "Map001.json/1/0/0".to_string(),
            item_type: "long_text".to_string(),
            role: Some("旁白".to_string()),
            display_name: None,
            original_lines: vec!["長い原文".to_string()],
            source_line_paths: Vec::new(),
        };
        let options = TextRuleOptions {
            long_text_line_width_limit: 4,
            line_split_punctuations: vec!["，".to_string()],
            ..TextRuleOptions::default()
        };

        let lines = normalize_manual_translation_lines(
            &item,
            &[r"\V[123]甲乙丙丁戊，己".to_string()],
            &options,
        )
        .expect("长文本应可规范化");

        assert_eq!(lines, vec![r"\V[123]甲乙丙丁", "戊，己"]);
    }

    #[test]
    fn quality_report_and_fix_template_include_residual_problem() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        let game = create_manual_test_game(temp.path(), "QualityReport");
        let registry = GameRegistry {
            db_directory: temp.path().join("db"),
        };
        let game_record = registry.register_game(&game).expect("游戏应注册成功");
        let location_path = "CommonEvents.json/1/0".to_string();
        registry
            .write_translation_items(
                &game_record.game_title,
                &[TranslationItemRecord {
                    location_path: location_path.clone(),
                    item_type: "long_text".to_string(),
                    role: Some("旁白".to_string()),
                    original_lines: vec![r"\F3[66」「ふーん……？」".to_string()],
                    source_line_paths: vec!["CommonEvents.json/1/1/parameters/0".to_string()],
                    translation_lines: vec![r"\F3[66」「まだ日本語？」".to_string()],
                }],
            )
            .expect("已保存译文应写入成功");

        let report = quality_report(
            &registry,
            &game_record,
            DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN,
            &TextRuleOptions::default(),
        )
        .expect("质量报告应生成成功");
        assert_eq!(report.status, "error");
        assert_eq!(report.summary["translated_count"], json!(1));
        assert_eq!(report.summary["japanese_residual_count"], json!(1));
        assert!(
            report
                .errors
                .iter()
                .any(|error| error.code == "japanese_residual")
        );

        let output_path = temp.path().join("quality-fix.json");
        let fix_report = export_quality_fix_template_report(
            &registry,
            &game_record,
            &output_path,
            DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN,
            &TextRuleOptions::default(),
        )
        .expect("质量修复模板应导出成功");
        assert_eq!(fix_report.status, "ok");
        assert_eq!(fix_report.summary["exported_count"], json!(1));
        assert_eq!(
            fix_report.details["problem_categories_by_path"][&location_path][0],
            "japanese_residual"
        );

        let raw_payload = fs::read_to_string(output_path).expect("质量修复模板应读取成功");
        let payload: Value = serde_json::from_str(&raw_payload).expect("质量修复模板应是 JSON");
        assert_eq!(
            payload[&location_path]["translation_lines"][0],
            r"\F3[66」「まだ日本語？」"
        );
    }

    #[test]
    fn manual_import_and_reset_roundtrip() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        let game = create_manual_test_game(temp.path(), "ManualRoundtrip");
        let registry = GameRegistry {
            db_directory: temp.path().join("db"),
        };
        let game_record = registry.register_game(&game).expect("游戏应注册成功");
        let pending_path = temp.path().join("pending.json");
        let pending_after_import_path = temp.path().join("pending-after-import.json");

        let export_report = export_pending_translations_report(
            &registry,
            &game_record,
            &pending_path,
            Some(1),
            DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN,
            &TextRuleOptions::default(),
        )
        .expect("待填表应导出成功");
        assert_eq!(export_report.status, "ok");

        let raw_payload = fs::read_to_string(&pending_path).expect("待填表应读取成功");
        let mut payload = serde_json::from_str::<Value>(&raw_payload)
            .expect("待填表 JSON 应解析成功")
            .as_object()
            .cloned()
            .expect("待填表顶层应是对象");
        let first_path = payload.keys().next().cloned().expect("应至少导出一条文本");
        let entry = payload
            .get_mut(&first_path)
            .and_then(Value::as_object_mut)
            .expect("待填表条目应是对象");
        entry.insert(
            "translation_lines".to_string(),
            json!([r"\F3[66」「你好？」"]),
        );
        write_json_file(&pending_path, &Value::Object(payload)).expect("待填表应写回成功");

        let import_report = import_manual_translations_report(
            &registry,
            &game_record,
            &pending_path,
            DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN,
            &TextRuleOptions::default(),
        )
        .expect("手动译文应导入成功");
        assert_eq!(import_report.status, "ok");
        assert_eq!(import_report.summary["imported_count"], json!(1));

        let pending_after_import = export_pending_translations_report(
            &registry,
            &game_record,
            &pending_after_import_path,
            None,
            DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN,
            &TextRuleOptions::default(),
        )
        .expect("导入后待填表应导出成功");
        assert_eq!(
            pending_after_import.summary["pending_exported_count"],
            json!(0)
        );

        let reset_report = reset_translations_report(
            &registry,
            &game_record,
            None,
            true,
            DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN,
            &TextRuleOptions::default(),
        )
        .expect("译文应可重置");
        assert_eq!(reset_report.summary["reset_count"], json!(1));
    }

    #[test]
    fn manual_import_ignores_japanese_inside_protected_control_parameters() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        let game = create_manual_test_game(temp.path(), "ManualControlParam");
        fs::write(
            game.join("data/CommonEvents.json"),
            json!([
                null,
                {
                    "id": 1,
                    "name": "event",
                    "list": [
                        {"code": 101, "parameters": [0, 0, 0, 2, ""]},
                        {"code": 401, "parameters": [r"\fn[うずら]\C[27]「ふーん……？」\fn"]},
                        {"code": 0, "parameters": []}
                    ]
                }
            ])
            .to_string(),
        )
        .expect("CommonEvents.json 应写入成功");
        let registry = GameRegistry {
            db_directory: temp.path().join("db"),
        };
        let game_record = registry.register_game(&game).expect("游戏应注册成功");
        registry
            .replace_placeholder_rules(
                &game_record.game_title,
                &[PlaceholderRule {
                    pattern_text: r"(?i)\\FN\d*\[[^\]\r\n]+\]".to_string(),
                    placeholder_template: "[CUSTOM_FONT_NAME_{index}]".to_string(),
                }],
            )
            .expect("字体名控制符规则应写入成功");
        let input_path = temp.path().join("manual-control-param.json");
        let payload = json!({
            "CommonEvents.json/1/0": {
                "translation_lines": [r"\fn[うずら]\C[27]「嗯……？」\fn"]
            }
        });
        write_json_file(&input_path, &payload).expect("手动译文表应写入成功");

        let report = import_manual_translations_report(
            &registry,
            &game_record,
            &input_path,
            DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN,
            &TextRuleOptions::default(),
        )
        .expect("报告应生成成功");

        assert_eq!(report.status, "ok");
        assert_eq!(report.summary["imported_count"], json!(1));
    }

    #[test]
    fn manual_import_rejects_changed_unprotected_control_sequence() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        let game = create_manual_test_game(temp.path(), "ManualReject");
        let registry = GameRegistry {
            db_directory: temp.path().join("db"),
        };
        let game_record = registry.register_game(&game).expect("游戏应注册成功");
        let input_path = temp.path().join("manual.json");
        let payload = json!({
            "CommonEvents.json/1/0": {
                "translation_lines": [r"\F3[60」「唔——嗯……？」"]
            }
        });
        write_json_file(&input_path, &payload).expect("手动译文表应写入成功");

        let report = import_manual_translations_report(
            &registry,
            &game_record,
            &input_path,
            DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN,
            &TextRuleOptions::default(),
        )
        .expect("报告应生成成功");

        assert_eq!(report.status, "error");
        assert_eq!(report.summary["imported_count"], json!(0));
        assert!(report.errors[0].message.contains("疑似控制符不一致"));
    }

    fn create_manual_test_game(root: &Path, title: &str) -> std::path::PathBuf {
        let game = root.join("game");
        fs::create_dir_all(game.join("data")).expect("data 目录应创建成功");
        fs::create_dir_all(game.join("js")).expect("js 目录应创建成功");
        fs::write(
            game.join("package.json"),
            json!({"window": {"title": title}}).to_string(),
        )
        .expect("package.json 应写入成功");
        fs::write(game.join("data/System.json"), "{}").expect("System.json 应写入成功");
        fs::write(game.join("data/Troops.json"), "[]").expect("Troops.json 应写入成功");
        fs::write(
            game.join("data/CommonEvents.json"),
            json!([
                null,
                {
                    "id": 1,
                    "name": "event",
                    "list": [
                        {"code": 101, "parameters": [0, 0, 0, 2, ""]},
                        {"code": 401, "parameters": [r"\F3[66」「ふーん……？」"]},
                        {"code": 0, "parameters": []}
                    ]
                }
            ])
            .to_string(),
        )
        .expect("CommonEvents.json 应写入成功");
        fs::write(game.join("js/plugins.js"), "var $plugins = [];").expect("plugins.js 应写入成功");
        game
    }
}
