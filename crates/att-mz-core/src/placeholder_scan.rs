//! 自定义占位符候选扫描与规则草稿生成。
//!
//! 候选扫描只读取当前会进入正文翻译的原文行：标准 RMMZ data 文本，以及已
//! 导入数据库的插件、事件指令和 Note 标签规则命中文本。这样生成的草稿不会
//! 被未授权的插件参数或机器协议噪音污染。

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::path::Path;

use fancy_regex::Regex as FancyRegex;
use regex::Regex;
use serde_json::{Map, Value, json};

use crate::error::{AttMzError, Result};
use crate::event_command_rules::{EventCommandRuleRecord, extract_event_command_items};
use crate::note_tag_rules::{NoteTagRuleRecord, extract_note_tag_items};
use crate::placeholder::PlaceholderRule;
use crate::plugin_rules::PluginRuleRecord;
use crate::report::{AgentIssue, AgentReport, issue};
use crate::rmmz::EventCommandSnapshot;

const SYSTEM_FILE_NAME: &str = "System.json";
const COMMON_EVENTS_FILE_NAME: &str = "CommonEvents.json";
const TROOPS_FILE_NAME: &str = "Troops.json";

/// 当前活跃正文中的单个文本条目。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActiveTextItem {
    /// 正文在游戏里的内部位置。
    pub location_path: String,
    /// 正文条目类型，例如 `short_text`、`long_text` 或 `array`。
    pub item_type: String,
    /// 长文本角色；旁白或名字框文本。
    pub role: Option<String>,
    /// 当前会进入翻译流程的原文行。
    pub original_lines: Vec<String>,
    /// 原文行在游戏数据中的逐行内部位置。
    pub source_line_paths: Vec<String>,
}

/// 单个疑似控制符候选。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlaceholderCandidate {
    /// 扫描到的原始候选片段。
    pub marker: String,
    /// 出现次数。
    pub count: usize,
    /// 候选出现位置，格式为 `文本在游戏里的内部位置#行号`。
    pub sources: BTreeSet<String>,
    /// 是否已被内置 RMMZ 标准控制符覆盖。
    pub standard_covered: bool,
    /// 是否已被当前自定义占位符规则覆盖。
    pub custom_covered: bool,
}

/// 汇总当前活跃正文文本。
pub fn extract_active_text_items(
    data_files: &BTreeMap<String, Value>,
    command_snapshots: &[EventCommandSnapshot],
    plugins: &[Value],
    plugin_rules: &[PluginRuleRecord],
    event_rules: &[EventCommandRuleRecord],
    note_rules: &[NoteTagRuleRecord],
    source_text_required_pattern: &str,
) -> Result<Vec<ActiveTextItem>> {
    let source_pattern = compile_source_pattern(source_text_required_pattern)?;
    let mut items =
        extract_standard_data_text_items(data_files, command_snapshots, &source_pattern)?;

    for item in
        extract_event_command_items(command_snapshots, event_rules, source_text_required_pattern)?
    {
        items.push(ActiveTextItem {
            location_path: item.location_path,
            item_type: "short_text".to_string(),
            role: None,
            original_lines: vec![item.original_text],
            source_line_paths: Vec::new(),
        });
    }

    for item in extract_plugin_text_items(plugins, plugin_rules, &source_pattern)? {
        items.push(item);
    }

    for item in extract_note_tag_items(data_files, note_rules, source_text_required_pattern)? {
        items.push(ActiveTextItem {
            location_path: item.location_path,
            item_type: "short_text".to_string(),
            role: None,
            original_lines: vec![item.original_text],
            source_line_paths: Vec::new(),
        });
    }

    Ok(items)
}

/// 生成自定义控制符候选扫描报告。
pub fn scan_placeholder_candidates_report(
    items: &[ActiveTextItem],
    custom_rules: &[PlaceholderRule],
) -> Result<AgentReport> {
    let candidates = scan_placeholder_candidates(items, custom_rules)?;
    let uncovered_count = candidates
        .iter()
        .filter(|candidate| !candidate.standard_covered && !candidate.custom_covered)
        .count();
    let warnings = if uncovered_count > 0 {
        vec![issue(
            "uncovered_placeholder",
            format!("发现 {uncovered_count} 个未覆盖的疑似自定义控制符"),
        )]
    } else {
        Vec::new()
    };

    let mut summary = Map::new();
    summary.insert("candidate_count".to_string(), json!(candidates.len()));
    summary.insert("uncovered_count".to_string(), json!(uncovered_count));
    summary.insert("custom_rule_count".to_string(), json!(custom_rules.len()));
    let mut details = Map::new();
    details.insert(
        "candidates".to_string(),
        Value::Array(
            candidates
                .iter()
                .map(placeholder_candidate_detail)
                .collect(),
        ),
    );
    Ok(AgentReport::from_parts(
        Vec::new(),
        warnings,
        summary,
        details,
    ))
}

/// 根据未覆盖候选生成可编辑的自定义占位符规则草稿。
pub fn build_placeholder_rule_draft_report(
    items: &[ActiveTextItem],
    output_path: &Path,
) -> Result<(AgentReport, BTreeMap<String, String>)> {
    let candidates = scan_placeholder_candidates(items, &[])?;
    let draft_rules = build_custom_placeholder_rule_draft(&candidates);
    let mut warnings = build_unprotected_control_warnings(items, &[])?;
    if draft_rules.is_empty() {
        warnings.push(issue(
            "placeholder_draft_empty",
            "没有发现需要生成草稿的自定义控制符候选",
        ));
    }
    let mut summary = Map::new();
    summary.insert("candidate_count".to_string(), json!(candidates.len()));
    summary.insert("draft_rule_count".to_string(), json!(draft_rules.len()));
    summary.insert(
        "output".to_string(),
        json!(output_path.display().to_string()),
    );
    let mut details = Map::new();
    details.insert("rules".to_string(), json!(draft_rules));
    Ok((
        AgentReport::from_parts(Vec::new(), warnings, summary, details),
        draft_rules,
    ))
}

fn extract_standard_data_text_items(
    data_files: &BTreeMap<String, Value>,
    command_snapshots: &[EventCommandSnapshot],
    source_pattern: &Regex,
) -> Result<Vec<ActiveTextItem>> {
    let mut items = extract_standard_command_text_items(command_snapshots, source_pattern);
    if let Some(system) = data_files.get(SYSTEM_FILE_NAME).and_then(Value::as_object) {
        if let Some(game_title) = normalize_optional_text(system.get("gameTitle"))
            && should_translate_source_lines(std::slice::from_ref(&game_title), source_pattern)
        {
            items.push(ActiveTextItem {
                location_path: format!("{SYSTEM_FILE_NAME}/gameTitle"),
                item_type: "short_text".to_string(),
                role: None,
                original_lines: vec![game_title],
                source_line_paths: Vec::new(),
            });
        }
        if let Some(terms) = system.get("terms").and_then(Value::as_object) {
            for key in ["basic", "commands", "params"] {
                if let Some(values) = terms.get(key).and_then(Value::as_array) {
                    for (index, value) in values.iter().enumerate() {
                        if let Some(text) = normalize_optional_text(Some(value))
                            && should_translate_source_lines(
                                std::slice::from_ref(&text),
                                source_pattern,
                            )
                        {
                            items.push(ActiveTextItem {
                                location_path: format!("{SYSTEM_FILE_NAME}/terms/{key}/{index}"),
                                item_type: "short_text".to_string(),
                                role: None,
                                original_lines: vec![text],
                                source_line_paths: Vec::new(),
                            });
                        }
                    }
                }
            }
            if let Some(messages) = terms.get("messages").and_then(Value::as_object) {
                for (key, value) in messages {
                    if let Some(text) = normalize_optional_text(Some(value))
                        && should_translate_source_lines(
                            std::slice::from_ref(&text),
                            source_pattern,
                        )
                    {
                        items.push(ActiveTextItem {
                            location_path: format!("{SYSTEM_FILE_NAME}/terms/messages/{key}"),
                            item_type: "short_text".to_string(),
                            role: None,
                            original_lines: vec![text],
                            source_line_paths: Vec::new(),
                        });
                    }
                }
            }
        }
    }

    for (file_name, value) in data_files {
        if file_name == SYSTEM_FILE_NAME
            || file_name == COMMON_EVENTS_FILE_NAME
            || file_name == TROOPS_FILE_NAME
            || is_map_file_name(file_name)
        {
            continue;
        }
        let Some(array) = value.as_array() else {
            continue;
        };
        for item in array.iter().filter_map(Value::as_object) {
            let Some(id) = item.get("id").and_then(Value::as_i64) else {
                continue;
            };
            for key in [
                "profile",
                "description",
                "message1",
                "message2",
                "message3",
                "message4",
            ] {
                if let Some(text) = normalize_optional_text(item.get(key))
                    && should_translate_source_lines(std::slice::from_ref(&text), source_pattern)
                {
                    items.push(ActiveTextItem {
                        location_path: format!("{file_name}/{id}/{key}"),
                        item_type: "short_text".to_string(),
                        role: None,
                        original_lines: vec![text],
                        source_line_paths: Vec::new(),
                    });
                }
            }
        }
    }
    Ok(items)
}

fn extract_standard_command_text_items(
    command_snapshots: &[EventCommandSnapshot],
    source_pattern: &Regex,
) -> Vec<ActiveTextItem> {
    let mut items = Vec::new();
    let mut pending_text: Option<ActiveTextItem> = None;
    let mut pending_scroll: Option<ActiveTextItem> = None;
    let mut pending_scroll_parent: Option<String> = None;
    let mut pending_scroll_index: Option<i64> = None;

    for snapshot in command_snapshots {
        let command_index = command_location_index(&snapshot.location_path);
        let command_parent = command_location_parent(&snapshot.location_path);
        if snapshot.code != 405 {
            flush_pending(&mut pending_scroll, &mut items, source_pattern);
            pending_scroll_parent = None;
            pending_scroll_index = None;
        }

        match snapshot.code {
            101 => {
                flush_pending(&mut pending_text, &mut items, source_pattern);
                let role = snapshot
                    .parameters
                    .as_array()
                    .and_then(|parameters| parameters.get(4))
                    .and_then(Value::as_str)
                    .map(str::trim)
                    .filter(|value| !value.is_empty())
                    .unwrap_or("旁白");
                pending_text = Some(ActiveTextItem {
                    location_path: snapshot.location_path.clone(),
                    item_type: "long_text".to_string(),
                    role: Some(role.to_string()),
                    original_lines: Vec::new(),
                    source_line_paths: Vec::new(),
                });
            }
            401 => {
                if let Some(item) = pending_text.as_mut()
                    && let Some(text) = first_string_parameter(&snapshot.parameters)
                {
                    item.original_lines.push(text);
                    item.source_line_paths.push(snapshot.location_path.clone());
                }
            }
            102 => {
                flush_pending(&mut pending_text, &mut items, source_pattern);
                if let Some(parameters) = snapshot.parameters.as_array()
                    && let Some(choices) = parameters.first().and_then(Value::as_array)
                {
                    let lines = choices
                        .iter()
                        .filter_map(normalize_optional_text_value)
                        .collect::<Vec<_>>();
                    if should_translate_source_lines(&lines, source_pattern) {
                        items.push(ActiveTextItem {
                            location_path: snapshot.location_path.clone(),
                            item_type: "array".to_string(),
                            role: Some("旁白".to_string()),
                            original_lines: lines,
                            source_line_paths: Vec::new(),
                        });
                    }
                }
            }
            405 => {
                flush_pending(&mut pending_text, &mut items, source_pattern);
                let text = first_string_parameter(&snapshot.parameters);
                let adjacent = pending_scroll_parent.as_deref() == Some(&command_parent)
                    && pending_scroll_index
                        .zip(command_index)
                        .is_some_and(|(last_index, current_index)| current_index == last_index + 1);
                if !adjacent {
                    flush_pending(&mut pending_scroll, &mut items, source_pattern);
                    pending_scroll = None;
                }
                if let Some(text) = text {
                    if let Some(item) = pending_scroll.as_mut() {
                        item.original_lines.push(text);
                        item.source_line_paths.push(snapshot.location_path.clone());
                    } else {
                        pending_scroll = Some(ActiveTextItem {
                            location_path: snapshot.location_path.clone(),
                            item_type: "long_text".to_string(),
                            role: Some("旁白".to_string()),
                            original_lines: vec![text],
                            source_line_paths: vec![snapshot.location_path.clone()],
                        });
                    }
                    pending_scroll_parent = Some(command_parent);
                    pending_scroll_index = command_index;
                } else {
                    flush_pending(&mut pending_scroll, &mut items, source_pattern);
                    pending_scroll_parent = None;
                    pending_scroll_index = None;
                }
            }
            _ => {
                flush_pending(&mut pending_text, &mut items, source_pattern);
            }
        }
    }
    flush_pending(&mut pending_text, &mut items, source_pattern);
    flush_pending(&mut pending_scroll, &mut items, source_pattern);
    items
}

fn extract_plugin_text_items(
    plugins: &[Value],
    plugin_rules: &[PluginRuleRecord],
    source_pattern: &Regex,
) -> Result<Vec<ActiveTextItem>> {
    let mut items = Vec::new();
    for rule in plugin_rules {
        let Some(plugin) = plugins.get(rule.plugin_index) else {
            continue;
        };
        let leaves = resolve_plugin_leaves(plugin);
        let string_leaf_values = leaves
            .iter()
            .filter(|leaf| leaf.value_type == "string")
            .map(|leaf| (leaf.path.clone(), leaf.value.clone()))
            .collect::<HashMap<_, _>>();
        let mut seen_paths = HashSet::new();
        for path_template in &rule.path_templates {
            for leaf_path in expand_rule_to_leaf_paths(path_template, &leaves)? {
                if !seen_paths.insert(leaf_path.clone()) {
                    continue;
                }
                let Some(leaf_value) = string_leaf_values.get(&leaf_path) else {
                    continue;
                };
                let normalized_value = normalize_visible_text_for_extraction(leaf_value);
                if !should_translate_source_lines(
                    std::slice::from_ref(&normalized_value),
                    source_pattern,
                ) {
                    continue;
                }
                items.push(ActiveTextItem {
                    location_path: jsonpath_to_plugin_location_path(&leaf_path, rule.plugin_index)?,
                    item_type: "short_text".to_string(),
                    role: None,
                    original_lines: vec![normalized_value],
                    source_line_paths: Vec::new(),
                });
            }
        }
    }
    Ok(items)
}

fn scan_placeholder_candidates(
    items: &[ActiveTextItem],
    custom_rules: &[PlaceholderRule],
) -> Result<Vec<PlaceholderCandidate>> {
    let candidate_pattern = Regex::new(r"\\(?:[A-Za-z]+\d*(?:\[[^\]\r\n]*\])?|[{}\\$.\|!><^])")
        .map_err(|error| AttMzError::InvalidConfig(format!("控制符候选正则不可用: {error}")))?;
    let mut candidates: BTreeMap<String, PlaceholderCandidate> = BTreeMap::new();
    for item in items {
        for (line_index, text) in item.original_lines.iter().enumerate() {
            for matched in candidate_pattern.find_iter(text) {
                let marker = matched.as_str().to_string();
                let candidate =
                    candidates
                        .entry(marker.clone())
                        .or_insert_with(|| PlaceholderCandidate {
                            marker,
                            count: 0,
                            sources: BTreeSet::new(),
                            standard_covered: false,
                            custom_covered: false,
                        });
                candidate.count += 1;
                candidate
                    .sources
                    .insert(format!("{}#{line_index}", item.location_path));
            }
        }
    }
    let custom_patterns = compile_full_custom_patterns(custom_rules)?;
    for candidate in candidates.values_mut() {
        candidate.standard_covered = is_standard_covered(&candidate.marker)?;
        candidate.custom_covered = false;
        for pattern in &custom_patterns {
            let matched = pattern.is_match(&candidate.marker).map_err(|error| {
                AttMzError::InvalidConfig(format!(
                    "自定义占位符正则匹配失败: {}: {error}",
                    candidate.marker
                ))
            })?;
            if matched {
                candidate.custom_covered = true;
                break;
            }
        }
    }
    let mut candidates = candidates.into_values().collect::<Vec<_>>();
    candidates.sort_by(|left, right| {
        (
            left.standard_covered,
            left.custom_covered,
            left.marker.to_lowercase(),
        )
            .cmp(&(
                right.standard_covered,
                right.custom_covered,
                right.marker.to_lowercase(),
            ))
    });
    Ok(candidates)
}

fn build_custom_placeholder_rule_draft(
    candidates: &[PlaceholderCandidate],
) -> BTreeMap<String, String> {
    let mut draft_rules = BTreeMap::new();
    for candidate in candidates {
        if candidate.standard_covered || candidate.custom_covered {
            continue;
        }
        let (pattern_text, placeholder_template) = draft_custom_placeholder_rule(&candidate.marker);
        draft_rules
            .entry(pattern_text)
            .or_insert(placeholder_template);
    }
    draft_rules
}

fn draft_custom_placeholder_rule(marker: &str) -> (String, String) {
    if let Some((code, remainder)) = marker_code_and_remainder(marker) {
        let digit_end = remainder
            .char_indices()
            .take_while(|(_index, char_value)| char_value.is_ascii_digit())
            .map(|(index, char_value)| index + char_value.len_utf8())
            .last()
            .unwrap_or(0);
        let tail = &remainder[digit_end..];
        if marker_parameter_tail_is_valid(tail) {
            let pattern_text = format!(r"(?i)\\{code}\d*\[[^\]\r\n]+\]");
            return (pattern_text, custom_placeholder_template_for_code(&code));
        }
        if tail.is_empty() {
            let pattern_text = format!(r"(?i)\\{code}\d*(?![A-Za-z\[])");
            return (pattern_text, custom_placeholder_template_for_code(&code));
        }
    }
    (
        regex::escape(marker),
        "[CUSTOM_UNKNOWN_CONTROL_MARKER_{index}]".to_string(),
    )
}

fn marker_code_and_remainder(marker: &str) -> Option<(String, &str)> {
    let remainder = marker.strip_prefix('\\')?;
    let code_end = remainder
        .char_indices()
        .take_while(|(_index, char_value)| char_value.is_ascii_alphabetic())
        .map(|(index, char_value)| index + char_value.len_utf8())
        .last()?;
    let code = remainder[..code_end].to_ascii_uppercase();
    Some((code, &remainder[code_end..]))
}

fn marker_parameter_tail_is_valid(tail: &str) -> bool {
    let Some(parameter_text) = tail
        .strip_prefix('[')
        .and_then(|value| value.strip_suffix(']'))
    else {
        return false;
    };
    !parameter_text.is_empty()
        && parameter_text
            .chars()
            .all(|char_value| char_value != ']' && char_value != '\r' && char_value != '\n')
}

fn custom_placeholder_template_for_code(code: &str) -> String {
    let semantic_name = match code {
        "F" => "FACE_PORTRAIT",
        "FH" => "FACE_PORTRAIT_HIDE",
        "AA" => "PLUGIN_AA_MARKER",
        "AC" => "PLUGIN_AC_MARKER",
        "AN" => "PLUGIN_ACTOR_NAME_MARKER",
        "MT" => "PLUGIN_MESSAGE_TAG",
        _ => return format!("[CUSTOM_PLUGIN_{code}_MARKER_{{index}}]"),
    };
    format!("[CUSTOM_{semantic_name}_{{index}}]")
}

fn placeholder_candidate_detail(candidate: &PlaceholderCandidate) -> Value {
    json!({
        "marker": candidate.marker,
        "count": candidate.count,
        "sources": candidate.sources.iter().cloned().collect::<Vec<_>>(),
        "standard_covered": candidate.standard_covered,
        "custom_covered": candidate.custom_covered,
        "covered": candidate.standard_covered || candidate.custom_covered,
    })
}

fn flush_pending(
    pending: &mut Option<ActiveTextItem>,
    items: &mut Vec<ActiveTextItem>,
    source_pattern: &Regex,
) {
    let Some(item) = pending.take() else {
        return;
    };
    if should_translate_source_lines(&item.original_lines, source_pattern) {
        items.push(item);
    }
}

fn first_string_parameter(parameters: &Value) -> Option<String> {
    parameters
        .as_array()
        .and_then(|parameters| parameters.first())
        .and_then(normalize_optional_text_value)
}

fn normalize_optional_text(value: Option<&Value>) -> Option<String> {
    value.and_then(normalize_optional_text_value)
}

fn normalize_optional_text_value(value: &Value) -> Option<String> {
    value.as_str().and_then(|text| {
        let normalized = text.trim().to_string();
        (!normalized.is_empty()).then_some(normalized)
    })
}

fn should_translate_source_lines(lines: &[String], source_pattern: &Regex) -> bool {
    lines.iter().any(|line| {
        let normalized = line.trim();
        !normalized.is_empty() && source_pattern.is_match(normalized)
    })
}

fn command_location_parent(location_path: &str) -> String {
    location_path
        .rsplit_once('/')
        .map(|(parent, _index)| parent.to_string())
        .unwrap_or_default()
}

fn command_location_index(location_path: &str) -> Option<i64> {
    location_path
        .rsplit_once('/')
        .and_then(|(_parent, index)| index.parse::<i64>().ok())
}

fn compile_source_pattern(source_text_required_pattern: &str) -> Result<Regex> {
    Regex::new(source_text_required_pattern).map_err(|error| {
        AttMzError::InvalidConfig(format!(
            "text_rules.source_text_required_pattern 不是有效正则: {error}"
        ))
    })
}

fn is_standard_covered(marker: &str) -> Result<bool> {
    let indexed = Regex::new(r"\\(?P<code>PX|PY|FS|V|N|P|C|I)\[(?P<param>\d+)\]")
        .map_err(|error| AttMzError::InvalidConfig(format!("标准控制符正则不可用: {error}")))?;
    if indexed
        .find(marker)
        .is_some_and(|matched| matched.as_str() == marker)
    {
        return Ok(true);
    }
    let no_param = Regex::new(r"\\G")
        .map_err(|error| AttMzError::InvalidConfig(format!("标准控制符正则不可用: {error}")))?;
    if no_param
        .find(marker)
        .is_some_and(|matched| matched.as_str() == marker)
    {
        return Ok(true);
    }
    let symbol = Regex::new(r"\\[{}\\$.\|!><^]")
        .map_err(|error| AttMzError::InvalidConfig(format!("标准控制符正则不可用: {error}")))?;
    Ok(symbol
        .find(marker)
        .is_some_and(|matched| matched.as_str() == marker))
}

fn is_map_file_name(file_name: &str) -> bool {
    let Some(number_part) = file_name
        .strip_prefix("Map")
        .and_then(|value| value.strip_suffix(".json"))
    else {
        return false;
    };
    !number_part.is_empty()
        && number_part
            .chars()
            .all(|char_value| char_value.is_ascii_digit())
}

fn normalize_visible_text_for_extraction(raw_text: &str) -> String {
    let mut current_text = raw_text.to_string();
    while let Ok(Value::String(decoded_text)) = serde_json::from_str::<Value>(&current_text) {
        current_text = decoded_text;
    }
    current_text.trim().to_string()
}

#[derive(Debug, Clone)]
struct ResolvedLeaf {
    path: String,
    value: String,
    value_type: &'static str,
}

fn resolve_plugin_leaves(plugin: &Value) -> Vec<ResolvedLeaf> {
    let mut leaves = Vec::new();
    let Some(parameters) = plugin.get("parameters") else {
        return leaves;
    };
    walk_json_value(parameters, "$['parameters']".to_string(), &mut leaves);
    leaves
}

fn walk_json_value(value: &Value, current_path: String, leaves: &mut Vec<ResolvedLeaf>) {
    match value {
        Value::Object(object) => {
            for (key, child) in object {
                walk_json_value(
                    child,
                    format!("{current_path}[{}]", quote_jsonpath_key(key)),
                    leaves,
                );
            }
        }
        Value::Array(items) => {
            for (index, child) in items.iter().enumerate() {
                walk_json_value(child, format!("{current_path}[{index}]"), leaves);
            }
        }
        Value::String(text) => {
            if let Ok(container @ (Value::Object(_) | Value::Array(_))) =
                serde_json::from_str::<Value>(text)
            {
                walk_json_value(&container, current_path, leaves);
                return;
            }
            leaves.push(ResolvedLeaf {
                path: current_path,
                value: text.clone(),
                value_type: "string",
            });
        }
        Value::Bool(value) => leaves.push(ResolvedLeaf {
            path: current_path,
            value: value.to_string(),
            value_type: "boolean",
        }),
        Value::Number(value) => leaves.push(ResolvedLeaf {
            path: current_path,
            value: value.to_string(),
            value_type: "number",
        }),
        Value::Null => leaves.push(ResolvedLeaf {
            path: current_path,
            value: "null".to_string(),
            value_type: "null",
        }),
    }
}

fn expand_rule_to_leaf_paths(path_template: &str, leaves: &[ResolvedLeaf]) -> Result<Vec<String>> {
    let mut matched_paths = Vec::new();
    for leaf in leaves {
        if leaf.value_type != "string" {
            continue;
        }
        if jsonpath_matches_template(path_template, &leaf.path)? {
            matched_paths.push(leaf.path.clone());
        }
    }
    matched_paths.sort();
    Ok(matched_paths)
}

fn jsonpath_matches_template(template_path: &str, actual_path: &str) -> Result<bool> {
    let template_parts = jsonpath_to_path_parts(template_path)?;
    let actual_parts = jsonpath_to_path_parts(actual_path)?;
    if template_parts.len() != actual_parts.len() {
        return Ok(false);
    }
    for (template_part, actual_part) in template_parts.iter().zip(actual_parts.iter()) {
        if template_part == "*" {
            if actual_part.parse::<usize>().is_err() {
                return Ok(false);
            }
            continue;
        }
        if template_part != actual_part {
            return Ok(false);
        }
    }
    Ok(true)
}

fn jsonpath_to_plugin_location_path(json_path: &str, plugin_index: usize) -> Result<String> {
    let path_parts = jsonpath_to_path_parts(json_path)?;
    if path_parts.first().map(String::as_str) != Some("parameters") {
        return Err(AttMzError::InvalidConfig(format!(
            "插件路径必须从 parameters 开始: {json_path}"
        )));
    }
    let mut normalized_parts = vec!["plugins.js".to_string(), plugin_index.to_string()];
    normalized_parts.extend(path_parts.into_iter().skip(1));
    Ok(normalized_parts.join("/"))
}

fn jsonpath_to_path_parts(path: &str) -> Result<Vec<String>> {
    let pattern = Regex::new(r"\['((?:[^'\\]|\\.)+)'\]|\[(\d+|\*)\]")
        .map_err(|error| AttMzError::InvalidConfig(format!("JSONPath 解析正则不可用: {error}")))?;
    if !path.starts_with('$') {
        return Err(AttMzError::InvalidConfig(format!(
            "JSONPath 超出当前规则范围: {path}"
        )));
    }
    let mut cursor = 1usize;
    let mut parts = Vec::new();
    for captures in pattern.captures_iter(path) {
        let Some(matched) = captures.get(0) else {
            continue;
        };
        if matched.start() != cursor {
            return Err(AttMzError::InvalidConfig(format!(
                "JSONPath 超出当前规则范围: {path}"
            )));
        }
        cursor = matched.end();
        if let Some(key) = captures.get(1) {
            parts.push(unescape_jsonpath_key(key.as_str()));
        } else if let Some(index) = captures.get(2) {
            parts.push(index.as_str().to_string());
        }
    }
    if cursor != path.len() || parts.is_empty() {
        return Err(AttMzError::InvalidConfig(format!(
            "JSONPath 超出当前规则范围: {path}"
        )));
    }
    Ok(parts)
}

fn quote_jsonpath_key(key: &str) -> String {
    format!("'{}'", key.replace('\\', "\\\\").replace('\'', "\\'"))
}

fn unescape_jsonpath_key(key: &str) -> String {
    key.replace("\\'", "'").replace("\\\\", "\\")
}

fn build_unprotected_control_warnings(
    items: &[ActiveTextItem],
    custom_rules: &[PlaceholderRule],
) -> Result<Vec<AgentIssue>> {
    let custom_patterns = compile_full_custom_patterns(custom_rules)?;
    let mut suspicious_candidates = Vec::new();
    'items: for item in items {
        for text in &item.original_lines {
            for candidate in warning_raw_control_sequence_candidates(text) {
                if is_standard_covered(&candidate.original)? {
                    continue;
                }
                let mut custom_covered = false;
                for pattern in &custom_patterns {
                    if pattern.is_match(&candidate.original).map_err(|error| {
                        AttMzError::InvalidConfig(format!(
                            "自定义占位符正则匹配失败: {}: {error}",
                            candidate.original
                        ))
                    })? {
                        custom_covered = true;
                        break;
                    }
                }
                if custom_covered || !is_suspicious_unprotected_control(&candidate.original) {
                    continue;
                }
                if suspicious_candidates.contains(&candidate.original) {
                    continue;
                }
                suspicious_candidates.push(candidate.original);
                if suspicious_candidates.len() >= 5 {
                    break 'items;
                }
            }
        }
    }
    if suspicious_candidates.is_empty() {
        return Ok(Vec::new());
    }
    let formatted = suspicious_candidates
        .iter()
        .map(|candidate| format!("{candidate} ({})", format_code_points(candidate)))
        .collect::<Vec<_>>()
        .join("；");
    Ok(vec![issue(
        "unprotected_control_unicode_boundary",
        format!(
            "发现疑似非 ASCII 括号或未闭合控制片段，请核验 Unicode code point 后使用精确规则，禁止猜成 ASCII ]：{formatted}"
        ),
    )])
}

fn warning_raw_control_sequence_candidates(text: &str) -> Vec<RawCandidate> {
    let Ok(pattern) = Regex::new(
        r"\\[A-Za-z]+\d*\[[A-Za-z0-9_./:-]{1,32}[^\]\w\s\[\]\\]|\\[A-Za-z]+\d*(?:\[[^\]\r\n]{0,64}\])?|\\[{}\\$.\|!><^]",
    ) else {
        return Vec::new();
    };
    pattern
        .find_iter(text)
        .map(|matched| RawCandidate {
            original: matched.as_str().to_string(),
        })
        .collect()
}

fn is_suspicious_unprotected_control(candidate: &str) -> bool {
    if candidate.contains('[') && !candidate.contains(']') {
        return true;
    }
    candidate
        .chars()
        .any(|char_value| "」』】）〕〉》".contains(char_value))
}

fn format_code_points(text: &str) -> String {
    text.chars()
        .map(|char_value| format!("U+{:04X}", char_value as u32))
        .collect::<Vec<_>>()
        .join(" ")
}

struct RawCandidate {
    original: String,
}

fn compile_full_custom_patterns(custom_rules: &[PlaceholderRule]) -> Result<Vec<FancyRegex>> {
    let mut patterns = Vec::new();
    for rule in custom_rules {
        let pattern_text = format!("^(?:{})$", rule.pattern_text);
        let pattern = FancyRegex::new(&pattern_text)
            .map_err(|error| AttMzError::InvalidConfig(format!("自定义占位符正则无效: {error}")))?;
        patterns.push(pattern);
    }
    Ok(patterns)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_item(lines: Vec<String>) -> ActiveTextItem {
        ActiveTextItem {
            location_path: "Map001.json/1/0/0".to_string(),
            item_type: "long_text".to_string(),
            role: Some("旁白".to_string()),
            original_lines: lines,
            source_line_paths: Vec::new(),
        }
    }

    #[test]
    fn placeholder_scan_marks_custom_coverage() {
        let items = vec![test_item(vec![r"\F[GuideA]こんにちは\C[4]\!".to_string()])];
        let uncovered = scan_placeholder_candidates_report(&items, &[]).expect("扫描应成功");
        let rules = vec![PlaceholderRule {
            pattern_text: r"\\F\[[^\]]+\]".to_string(),
            placeholder_template: "[CUSTOM_FACE_PORTRAIT_{index}]".to_string(),
        }];
        let covered = scan_placeholder_candidates_report(&items, &rules).expect("扫描应成功");

        assert_eq!(uncovered.summary.get("uncovered_count"), Some(&json!(1)));
        assert_eq!(covered.summary.get("uncovered_count"), Some(&json!(0)));
    }

    #[test]
    fn placeholder_draft_groups_similar_markers() {
        let items = vec![test_item(vec![
            r"\F[GuideA]こんにちは".to_string(),
            r"\F[GuideB]こんばんは".to_string(),
        ])];

        let (_report, draft) = build_placeholder_rule_draft_report(&items, Path::new("<规则文件>"))
            .expect("草稿应生成成功");

        assert_eq!(
            draft.get(r"(?i)\\F\d*\[[^\]\r\n]+\]"),
            Some(&"[CUSTOM_FACE_PORTRAIT_{index}]".to_string())
        );
        assert_eq!(draft.len(), 1);
    }

    #[test]
    fn placeholder_draft_keeps_python_compatible_no_param_boundary() {
        let items = vec![test_item(vec![r"\MTこんにちは".to_string()])];

        let (_report, draft) = build_placeholder_rule_draft_report(&items, Path::new("<规则文件>"))
            .expect("草稿应生成成功");

        assert_eq!(
            draft.get(r"(?i)\\MT\d*(?![A-Za-z\[])"),
            Some(&"[CUSTOM_PLUGIN_MESSAGE_TAG_{index}]".to_string())
        );
    }
}
