//! 事件指令文本规则校验、提取预览与入库记录构建。
//!
//! 外部规则以“事件指令编码 -> 规则组数组”的 JSON 对象表达。每个规则组用
//! `match` 限定参数值，用 `paths` 声明需要提取的字符串叶子。本模块只依赖
//! 当前游戏已经解析出的事件指令快照，确保校验和导入都基于真实游戏结构。

use std::collections::{BTreeMap, HashMap, HashSet};

use regex::Regex;
use serde::Deserialize;
use serde_json::{Map, Value, json};
use sha1::{Digest, Sha1};

use crate::error::{AttMzError, Result};
use crate::report::{AgentReport, issue};
use crate::rmmz::EventCommandSnapshot;

/// 单个事件指令参数过滤条件。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EventCommandParameterFilter {
    /// `parameters` 数组下标。
    pub index: usize,
    /// 必须完全相等的字符串值。
    pub value: String,
}

/// 同一类事件指令文本规则。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EventCommandRuleSpec {
    /// 外部 JSON 中的 `match` 条件。
    pub match_filters: BTreeMap<String, String>,
    /// 外部 JSON 中的 JSONPath 模板数组。
    pub paths: Vec<String>,
}

/// 外部事件指令规则文件结构。
pub type EventCommandRuleImportFile = BTreeMap<String, Vec<EventCommandRuleSpec>>;

/// 可写入数据库的事件指令规则记录。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EventCommandRuleRecord {
    /// RPG Maker MZ 事件指令编码。
    pub command_code: i64,
    /// 参数过滤条件，按参数下标升序排列。
    pub parameter_filters: Vec<EventCommandParameterFilter>,
    /// 已校验能命中字符串叶子的路径模板。
    pub path_templates: Vec<String>,
}

/// 事件指令规则导入摘要。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EventCommandRuleImportResult {
    /// 本次写入的规则组数量。
    pub imported_rule_group_count: usize,
    /// 本次写入的路径规则数量。
    pub imported_path_rule_count: usize,
    /// 因规则变化而删除的旧译文数量。
    pub deleted_translation_items: usize,
}

/// 事件指令规则命中的文本预览项。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EventCommandExtractedItem {
    /// 正文在游戏里的内部位置。
    pub location_path: String,
    /// 提取后准备交给翻译流程的原文。
    pub original_text: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct RawEventCommandRuleSpec {
    #[serde(default, rename = "match")]
    match_filters: BTreeMap<String, String>,
    #[serde(default)]
    paths: Vec<String>,
}

/// 解析事件指令规则 JSON 文本。
pub fn parse_event_command_rule_import_text(raw_text: &str) -> Result<EventCommandRuleImportFile> {
    let raw_value: BTreeMap<String, Vec<RawEventCommandRuleSpec>> =
        serde_json::from_str(raw_text.trim_start_matches('\u{feff}')).map_err(|source| {
            AttMzError::Json {
                context: "事件指令规则 JSON".to_string(),
                source,
            }
        })?;
    let mut import_file = BTreeMap::new();
    for (command_code, specs) in raw_value {
        import_file.insert(
            command_code,
            specs
                .into_iter()
                .map(|spec| EventCommandRuleSpec {
                    match_filters: spec.match_filters,
                    paths: spec.paths,
                })
                .collect(),
        );
    }
    Ok(import_file)
}

/// 根据当前游戏事件指令快照构建可入库的规则记录。
pub fn build_event_command_rule_records_from_import(
    command_snapshots: &[EventCommandSnapshot],
    import_file: &EventCommandRuleImportFile,
) -> Result<Vec<EventCommandRuleRecord>> {
    let mut records = Vec::new();
    let mut record_indexes_by_key = HashMap::new();
    for (command_code_text, specs) in import_file {
        let command_code = parse_command_code(command_code_text)?;
        for spec in specs {
            let parameter_filters = parse_parameter_filters(&spec.match_filters)?;
            let record = build_event_command_rule_record(
                command_snapshots,
                command_code,
                parameter_filters,
                &spec.paths,
            )?;
            let record_key = event_command_rule_identity(&record);
            if let Some(index) = record_indexes_by_key.get(&record_key).copied() {
                let existing_record: &mut EventCommandRuleRecord =
                    records.get_mut(index).ok_or_else(|| {
                        AttMzError::InvalidConfig("事件指令规则合并索引失效".to_string())
                    })?;
                existing_record.path_templates = normalize_path_templates(
                    existing_record
                        .path_templates
                        .iter()
                        .chain(record.path_templates.iter())
                        .cloned()
                        .collect::<Vec<_>>()
                        .as_slice(),
                );
                continue;
            }
            record_indexes_by_key.insert(record_key, records.len());
            records.push(record);
        }
    }
    Ok(records)
}

/// 校验事件指令规则并生成 Agent 报告。
pub fn validate_event_command_rules_report(
    command_snapshots: &[EventCommandSnapshot],
    rules_text: &str,
    source_text_required_pattern: &str,
) -> AgentReport {
    let mut details = Map::new();
    details.insert("rules".to_string(), Value::Array(Vec::new()));

    let import_file = match parse_event_command_rule_import_text(rules_text) {
        Ok(import_file) => import_file,
        Err(error) => return event_command_rules_invalid_report(error.to_string()),
    };
    let records =
        match build_event_command_rule_records_from_import(command_snapshots, &import_file) {
            Ok(records) => records,
            Err(error) => return event_command_rules_invalid_report(error.to_string()),
        };
    let extracted_items = match extract_event_command_items(
        command_snapshots,
        &records,
        source_text_required_pattern,
    ) {
        Ok(items) => items,
        Err(error) => return event_command_rules_invalid_report(error.to_string()),
    };

    details.insert(
        "write_back_preview".to_string(),
        json!({
            "checked_item_count": extracted_items.len(),
            "status": "ok",
        }),
    );

    let mut rule_details = Vec::new();
    for record in &records {
        let record_items = match extract_event_command_items(
            command_snapshots,
            std::slice::from_ref(record),
            source_text_required_pattern,
        ) {
            Ok(items) => items,
            Err(error) => return event_command_rules_invalid_report(error.to_string()),
        };
        let samples = record_items
            .iter()
            .take(5)
            .map(|item| json!(item.original_text))
            .collect::<Vec<_>>();
        rule_details.push(json!({
            "command_code": record.command_code,
            "match_count": record.parameter_filters.len(),
            "path_count": record.path_templates.len(),
            "paths": record.path_templates,
            "hit_count": record_items.len(),
            "samples": samples,
        }));
    }
    details.insert("rules".to_string(), Value::Array(rule_details));

    let mut warnings = Vec::new();
    if records.is_empty() {
        warnings.push(issue("event_command_rules_empty", "事件指令规则为空"));
    }
    if !records.is_empty() && extracted_items.is_empty() {
        warnings.push(issue(
            "event_command_rules_no_hits",
            "事件指令规则没有提取到任何可翻译文本",
        ));
    }

    AgentReport::from_parts(
        Vec::new(),
        warnings,
        event_command_rules_summary(&records),
        details,
    )
}

/// 提取规则命中的事件指令文本预览项。
pub fn extract_event_command_items(
    command_snapshots: &[EventCommandSnapshot],
    records: &[EventCommandRuleRecord],
    source_text_required_pattern: &str,
) -> Result<Vec<EventCommandExtractedItem>> {
    let source_pattern = Regex::new(source_text_required_pattern).map_err(|error| {
        AttMzError::InvalidConfig(format!(
            "text_rules.source_text_required_pattern 不是有效正则: {error}"
        ))
    })?;
    let mut items = Vec::new();
    let mut seen_location_paths = HashSet::new();
    for snapshot in command_snapshots {
        let matched_rules = records
            .iter()
            .filter(|record| {
                record.command_code == snapshot.code
                    && command_matches_filters(&snapshot.parameters, &record.parameter_filters)
            })
            .collect::<Vec<_>>();
        if matched_rules.is_empty() {
            continue;
        }
        let leaves = resolve_event_command_leaves(&snapshot.parameters);
        let string_leaf_values = leaves
            .iter()
            .filter(|leaf| leaf.value_type == "string")
            .map(|leaf| (leaf.path.clone(), leaf.value.clone()))
            .collect::<HashMap<_, _>>();
        for rule in matched_rules {
            for path_template in &rule.path_templates {
                for leaf_path in expand_rule_to_leaf_paths(path_template, &leaves)? {
                    let location_path = jsonpath_to_event_command_location_path(
                        &leaf_path,
                        &snapshot.location_path,
                    )?;
                    if !seen_location_paths.insert(location_path.clone()) {
                        continue;
                    }
                    let Some(leaf_value) = string_leaf_values.get(&leaf_path) else {
                        continue;
                    };
                    let normalized_value = normalize_visible_text_for_extraction(leaf_value);
                    if !should_translate_source_text(&normalized_value, &source_pattern) {
                        continue;
                    }
                    items.push(EventCommandExtractedItem {
                        location_path,
                        original_text: normalized_value,
                    });
                }
            }
        }
    }
    Ok(items)
}

/// 判断事件指令参数是否满足过滤条件。
pub fn command_matches_filters(
    parameters: &Value,
    filters: &[EventCommandParameterFilter],
) -> bool {
    let Some(parameters) = parameters.as_array() else {
        return false;
    };
    for parameter_filter in filters {
        let Some(value) = parameters
            .get(parameter_filter.index)
            .and_then(Value::as_str)
        else {
            return false;
        };
        if value != parameter_filter.value {
            return false;
        }
    }
    true
}

/// 生成事件指令规则组主键，保持和现有 Python 数据库兼容。
pub fn event_command_group_key(rule_record: &EventCommandRuleRecord) -> String {
    let filter_text = rule_record
        .parameter_filters
        .iter()
        .map(|parameter_filter| format!("{}={}", parameter_filter.index, parameter_filter.value))
        .collect::<Vec<_>>()
        .join("|");
    let payload = format!("{}:{filter_text}", rule_record.command_code);
    let digest = format!("{:x}", Sha1::digest(payload.as_bytes()));
    format!("event_{}_{}", rule_record.command_code, &digest[..16])
}

/// 生成事件指令规则的业务身份键。
pub fn event_command_rule_identity(rule_record: &EventCommandRuleRecord) -> String {
    let filter_text = rule_record
        .parameter_filters
        .iter()
        .map(|parameter_filter| format!("{}={}", parameter_filter.index, parameter_filter.value))
        .collect::<Vec<_>>()
        .join("|");
    format!("{}:{filter_text}", rule_record.command_code)
}

/// 判断规则变化后是否需要删除旧译文。
pub fn should_refresh_event_command_translation_items(
    old_rule: Option<&EventCommandRuleRecord>,
    new_rule: &EventCommandRuleRecord,
) -> bool {
    let Some(old_rule) = old_rule else {
        return false;
    };
    old_rule.command_code != new_rule.command_code
        || old_rule.parameter_filters != new_rule.parameter_filters
        || old_rule.path_templates != new_rule.path_templates
}

/// 根据规则命中的事件指令计算需要清理的正文路径前缀。
pub fn event_command_rule_prefixes(
    command_snapshots: &[EventCommandSnapshot],
    rule_record: &EventCommandRuleRecord,
) -> Vec<String> {
    command_snapshots
        .iter()
        .filter(|snapshot| {
            snapshot.code == rule_record.command_code
                && command_matches_filters(&snapshot.parameters, &rule_record.parameter_filters)
        })
        .map(|snapshot| snapshot.location_path.clone())
        .collect()
}

fn build_event_command_rule_record(
    command_snapshots: &[EventCommandSnapshot],
    command_code: i64,
    parameter_filters: Vec<EventCommandParameterFilter>,
    path_templates: &[String],
) -> Result<EventCommandRuleRecord> {
    let matched_commands = command_snapshots
        .iter()
        .filter(|snapshot| {
            snapshot.code == command_code
                && command_matches_filters(&snapshot.parameters, &parameter_filters)
        })
        .collect::<Vec<_>>();
    if matched_commands.is_empty() {
        return Err(AttMzError::InvalidConfig(format!(
            "事件指令规则没有命中当前游戏指令: {command_code}"
        )));
    }

    let normalized_paths = normalize_path_templates(path_templates);
    if normalized_paths.is_empty() {
        return Err(AttMzError::InvalidConfig("paths 不能为空".to_string()));
    }

    let mut accepted_paths = Vec::new();
    for path_template in normalized_paths {
        let mut matched_any_command = false;
        for snapshot in &matched_commands {
            let leaves = resolve_event_command_leaves(&snapshot.parameters);
            if !expand_rule_to_leaf_paths(&path_template, &leaves)?.is_empty() {
                matched_any_command = true;
                break;
            }
        }
        if !matched_any_command {
            return Err(AttMzError::InvalidConfig(format!(
                "事件指令 {command_code} 的路径没有命中字符串叶子: {path_template}"
            )));
        }
        accepted_paths.push(path_template);
    }

    Ok(EventCommandRuleRecord {
        command_code,
        parameter_filters,
        path_templates: accepted_paths,
    })
}

fn parse_command_code(value: &str) -> Result<i64> {
    let normalized_value = value.trim();
    if normalized_value.is_empty()
        || !normalized_value
            .chars()
            .all(|char_value| char_value.is_ascii_digit())
    {
        return Err(AttMzError::InvalidConfig(format!(
            "事件指令编码必须是非负整数: {value}"
        )));
    }
    normalized_value.parse::<i64>().map_err(|error| {
        AttMzError::InvalidConfig(format!("事件指令编码超出范围: {value}: {error}"))
    })
}

fn parse_parameter_filters(
    match_filters: &BTreeMap<String, String>,
) -> Result<Vec<EventCommandParameterFilter>> {
    let mut filters = Vec::new();
    for (index_text, expected_value) in match_filters {
        if index_text.is_empty()
            || !index_text
                .chars()
                .all(|char_value| char_value.is_ascii_digit())
        {
            return Err(AttMzError::InvalidConfig(format!(
                "match 的键必须是参数索引: {index_text}"
            )));
        }
        let index = index_text.parse::<usize>().map_err(|error| {
            AttMzError::InvalidConfig(format!("match 参数索引超出范围: {index_text}: {error}"))
        })?;
        filters.push(EventCommandParameterFilter {
            index,
            value: expected_value.clone(),
        });
    }
    filters.sort_by_key(|filter| filter.index);
    Ok(filters)
}

fn normalize_path_templates(path_templates: &[String]) -> Vec<String> {
    let mut normalized_paths = Vec::new();
    for path_template in path_templates {
        let normalized_path = path_template.trim();
        if normalized_path.is_empty()
            || normalized_paths
                .iter()
                .any(|existing: &String| existing == normalized_path)
        {
            continue;
        }
        normalized_paths.push(normalized_path.to_string());
    }
    normalized_paths
}

fn resolve_event_command_leaves(parameters: &Value) -> Vec<ResolvedLeaf> {
    let mut leaves = Vec::new();
    let root = json!({ "parameters": parameters });
    walk_json_value(&root, "$".to_string(), &mut leaves);
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

fn jsonpath_to_event_command_location_path(
    json_path: &str,
    command_location_path: &str,
) -> Result<String> {
    let path_parts = jsonpath_to_path_parts(json_path)?;
    if path_parts.first().map(String::as_str) != Some("parameters") {
        return Err(AttMzError::InvalidConfig(format!(
            "事件指令路径必须从 parameters 开始: {json_path}"
        )));
    }
    let mut normalized_parts = vec![command_location_path.to_string()];
    normalized_parts.extend(path_parts);
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

fn normalize_visible_text_for_extraction(raw_text: &str) -> String {
    let mut current_text = raw_text.to_string();
    while let Ok(Value::String(decoded_text)) = serde_json::from_str::<Value>(&current_text) {
        current_text = decoded_text;
    }
    current_text.trim().to_string()
}

fn should_translate_source_text(text: &str, source_pattern: &Regex) -> bool {
    let normalized_text = text.trim();
    !normalized_text.is_empty() && source_pattern.is_match(normalized_text)
}

fn event_command_rules_summary(records: &[EventCommandRuleRecord]) -> Map<String, Value> {
    let mut summary = Map::new();
    summary.insert("rule_group_count".to_string(), json!(records.len()));
    summary.insert(
        "path_rule_count".to_string(),
        json!(
            records
                .iter()
                .map(|record| record.path_templates.len())
                .sum::<usize>()
        ),
    );
    summary
}

fn event_command_rules_invalid_report(message: String) -> AgentReport {
    AgentReport::from_parts(
        vec![issue(
            "event_command_rules_invalid",
            format!("事件指令规则不可导入: {message}"),
        )],
        Vec::new(),
        event_command_rules_summary(&[]),
        {
            let mut details = Map::new();
            details.insert("rules".to_string(), json!([]));
            details
        },
    )
}

#[derive(Debug, Clone)]
struct ResolvedLeaf {
    path: String,
    value: String,
    value_type: &'static str,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn snapshots() -> Vec<EventCommandSnapshot> {
        vec![
            EventCommandSnapshot {
                location_path: "CommonEvents.json/1/4".to_string(),
                display_name: "CommonEvents.json".to_string(),
                code: 357,
                parameters: json!(["TestPlugin", "Show", 0, {"message": "プラグイン台詞"}]),
            },
            EventCommandSnapshot {
                location_path: "Map001.json/1/0/0".to_string(),
                display_name: "始まりの町".to_string(),
                code: 357,
                parameters: json!([
                    "ComplexPlugin",
                    "ShowWindow",
                    0,
                    {"window": {"title": "見出し"}, "choices": ["はい", "いいえ"]}
                ]),
            },
        ]
    }

    #[test]
    fn event_command_rules_report_counts_hits_per_rule() {
        let report = validate_event_command_rules_report(
            &snapshots(),
            r#"{"357":[{"match":{"0":"TestPlugin","1":"Show"},"paths":["$['parameters'][3]['message']"]},{"match":{"0":"ComplexPlugin","1":"ShowWindow"},"paths":["$['parameters'][3]['window']['title']","$['parameters'][3]['choices'][*]"]}]}"#,
            crate::config::DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN,
        );

        assert_eq!(report.status, "ok");
        let rules = report
            .details
            .get("rules")
            .and_then(Value::as_array)
            .expect("规则详情应存在");
        assert_eq!(rules[0]["hit_count"], 1);
        assert_eq!(rules[1]["hit_count"], 3);
    }

    #[test]
    fn event_command_rule_group_key_matches_python_shape() {
        let import_file = parse_event_command_rule_import_text(
            r#"{"357":[{"match":{"0":"TestPlugin","1":"Show"},"paths":["$['parameters'][3]['message']"]}]}"#,
        )
        .expect("规则应解析成功");
        let records = build_event_command_rule_records_from_import(&snapshots(), &import_file)
            .expect("规则应构建成功");

        assert!(event_command_group_key(&records[0]).starts_with("event_357_"));
        assert_eq!(event_command_group_key(&records[0]).len(), 26);
    }
}
