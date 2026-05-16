//! 插件文本规则校验与记录构建。
//!
//! 外部插件规则文件使用“插件名 -> JSONPath 数组”的最小结构。这里会把规则
//! 对照当前游戏的 `plugins.js` 展开，确认每条路径能命中字符串叶子，并生成可
//! 写入数据库的稳定规则记录。

use std::collections::{BTreeMap, HashMap};

use regex::Regex;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

use crate::error::{AttMzError, Result};
use crate::report::{AgentReport, issue};

/// 单个插件的规则记录。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PluginRuleRecord {
    /// 插件在 `$plugins` 数组中的索引。
    pub plugin_index: usize,
    /// 插件名称。
    pub plugin_name: String,
    /// 插件对象的稳定结构哈希。
    pub plugin_hash: String,
    /// 外部确认的 JSONPath 模板。
    pub path_templates: Vec<String>,
}

/// 解析插件规则 JSON 文本。
pub fn parse_plugin_rule_import_text(raw_text: &str) -> Result<BTreeMap<String, Vec<String>>> {
    let value: Value =
        serde_json::from_str(raw_text.trim_start_matches('\u{feff}')).map_err(|source| {
            AttMzError::Json {
                context: "插件规则 JSON".to_string(),
                source,
            }
        })?;
    let Some(object) = value.as_object() else {
        return Err(AttMzError::InvalidConfig(
            "插件规则顶层必须是对象".to_string(),
        ));
    };
    let mut rules = BTreeMap::new();
    for (plugin_name, raw_paths) in object {
        let Some(path_values) = raw_paths.as_array() else {
            return Err(AttMzError::InvalidConfig(format!(
                "插件 {plugin_name} 的规则路径必须是数组"
            )));
        };
        let mut paths = Vec::new();
        for raw_path in path_values {
            let Some(path) = raw_path.as_str() else {
                return Err(AttMzError::InvalidConfig(format!(
                    "插件 {plugin_name} 的规则路径必须是字符串"
                )));
            };
            paths.push(path.to_string());
        }
        rules.insert(plugin_name.clone(), paths);
    }
    Ok(rules)
}

/// 根据当前插件配置构建可入库的插件规则记录。
pub fn build_plugin_rule_records_from_import(
    plugins: &[Value],
    import_file: &BTreeMap<String, Vec<String>>,
) -> Result<Vec<PluginRuleRecord>> {
    let plugin_index = build_plugin_name_index(plugins)?;
    let mut records = Vec::new();
    for (plugin_name, path_templates) in import_file {
        let normalized_plugin_name = plugin_name.trim();
        if normalized_plugin_name.is_empty() {
            return Err(AttMzError::InvalidConfig(
                "插件规则不能包含空插件名".to_string(),
            ));
        }
        let Some((index, plugin)) = plugin_index.get(normalized_plugin_name) else {
            return Err(AttMzError::InvalidConfig(format!(
                "插件规则没有命中当前 plugins.js: {normalized_plugin_name}"
            )));
        };
        let normalized_paths = normalize_path_templates(path_templates);
        if normalized_paths.is_empty() {
            return Err(AttMzError::InvalidConfig(format!(
                "插件规则路径不能为空: {normalized_plugin_name}"
            )));
        }
        let leaves = resolve_plugin_leaves(plugin);
        for path_template in &normalized_paths {
            let matched_paths = expand_rule_to_leaf_paths(path_template, &leaves)?;
            if matched_paths.is_empty() {
                return Err(AttMzError::InvalidConfig(format!(
                    "插件 {normalized_plugin_name} 的路径没有命中当前插件字符串叶子: {path_template}"
                )));
            }
        }
        records.push(PluginRuleRecord {
            plugin_index: *index,
            plugin_name: normalized_plugin_name.to_string(),
            plugin_hash: build_plugin_hash(plugin)?,
            path_templates: normalized_paths,
        });
    }
    Ok(records)
}

/// 校验插件规则并生成 Agent 报告。
pub fn validate_plugin_rules_report(plugins: &[Value], rules_text: &str) -> AgentReport {
    let mut details = Map::new();
    details.insert("rules".to_string(), Value::Array(Vec::new()));
    let import_file = match parse_plugin_rule_import_text(rules_text) {
        Ok(import_file) => import_file,
        Err(error) => {
            return AgentReport::from_parts(
                vec![issue(
                    "plugin_rules_invalid",
                    format!("插件规则不可导入: {error}"),
                )],
                Vec::new(),
                plugin_summary(&[]),
                details,
            );
        }
    };
    let records = match build_plugin_rule_records_from_import(plugins, &import_file) {
        Ok(records) => records,
        Err(error) => {
            return AgentReport::from_parts(
                vec![issue(
                    "plugin_rules_invalid",
                    format!("插件规则不可导入: {error}"),
                )],
                Vec::new(),
                plugin_summary(&[]),
                details,
            );
        }
    };

    let mut rule_details = Vec::new();
    let mut total_hits = 0usize;
    for record in &records {
        let Some(plugin) = plugins.get(record.plugin_index) else {
            continue;
        };
        let leaves = resolve_plugin_leaves(plugin);
        let mut samples = Vec::new();
        let mut hit_count = 0usize;
        for path_template in &record.path_templates {
            if let Ok(matched_paths) = expand_rule_to_leaf_paths(path_template, &leaves) {
                for matched_path in matched_paths {
                    if let Some(leaf) = leaves.iter().find(|leaf| leaf.path == matched_path) {
                        hit_count += 1;
                        if samples.len() < 5 {
                            samples.push(json!(leaf.value));
                        }
                    }
                }
            }
        }
        total_hits += hit_count;
        rule_details.push(json!({
            "plugin_name": record.plugin_name,
            "path_count": record.path_templates.len(),
            "paths": record.path_templates,
            "hit_count": hit_count,
            "samples": samples,
        }));
    }
    details.insert("rules".to_string(), Value::Array(rule_details));

    let mut warnings = Vec::new();
    if records.is_empty() {
        warnings.push(issue("plugin_rules_empty", "插件规则为空"));
    }
    if !records.is_empty() && total_hits == 0 {
        warnings.push(issue(
            "plugin_rules_no_hits",
            "插件规则没有提取到任何可翻译文本",
        ));
    }
    AgentReport::from_parts(Vec::new(), warnings, plugin_summary(&records), details)
}

fn build_plugin_name_index(plugins: &[Value]) -> Result<HashMap<String, (usize, Value)>> {
    let mut plugin_index = HashMap::new();
    for (index, plugin) in plugins.iter().enumerate() {
        let plugin_name = extract_plugin_name(plugin, index);
        if plugin_index.contains_key(&plugin_name) {
            return Err(AttMzError::InvalidConfig(format!(
                "plugins.js 中存在重复插件名，无法按名称导入规则: {plugin_name}"
            )));
        }
        plugin_index.insert(plugin_name, (index, plugin.clone()));
    }
    Ok(plugin_index)
}

fn extract_plugin_name(plugin: &Value, plugin_index: usize) -> String {
    plugin
        .get("name")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|name| !name.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| format!("unnamed_plugin_{plugin_index}"))
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

/// 计算插件对象的稳定结构哈希。
///
/// 哈希用于判断数据库中保存的插件文本规则是否仍匹配当前 `plugins.js`，
/// 键顺序会先规范化，避免 JSON 对象字段顺序变化造成误判。
pub fn build_plugin_hash(plugin: &Value) -> Result<String> {
    let payload = serde_json::to_string(&canonical_json_value(plugin)).map_err(|source| {
        AttMzError::Json {
            context: "插件结构哈希".to_string(),
            source,
        }
    })?;
    Ok(format!("{:x}", Sha256::digest(payload.as_bytes())))
}

fn canonical_json_value(value: &Value) -> Value {
    match value {
        Value::Array(items) => Value::Array(items.iter().map(canonical_json_value).collect()),
        Value::Object(object) => {
            let mut sorted_keys: Vec<&String> = object.keys().collect();
            sorted_keys.sort();
            let mut sorted_object = Map::new();
            for key in sorted_keys {
                if let Some(item) = object.get(key) {
                    sorted_object.insert(key.clone(), canonical_json_value(item));
                }
            }
            Value::Object(sorted_object)
        }
        other => other.clone(),
    }
}

fn plugin_summary(records: &[PluginRuleRecord]) -> Map<String, Value> {
    let mut summary = Map::new();
    summary.insert("plugin_count".to_string(), json!(records.len()));
    summary.insert(
        "rule_count".to_string(),
        json!(
            records
                .iter()
                .map(|record| record.path_templates.len())
                .sum::<usize>()
        ),
    );
    summary
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

    #[test]
    fn plugin_rules_validate_hits_nested_string_leaves() {
        let plugins = vec![json!({
            "name": "TestPlugin",
            "status": true,
            "parameters": {
                "Message": "プラグイン本文",
                "Nested": "{\"text\":\"入れ子本文\"}",
                "List": "[{\"text\":\"配列本文\"},{\"text\":\"二つ目\"}]",
                "Count": "123"
            }
        })];
        let report = validate_plugin_rules_report(
            &plugins,
            r#"{"TestPlugin":["$['parameters']['Message']","$['parameters']['Nested']['text']","$['parameters']['List'][*]['text']"]}"#,
        );

        assert_eq!(report.status, "ok");
        assert_eq!(report.summary.get("plugin_count"), Some(&json!(1)));
        let rules = report
            .details
            .get("rules")
            .and_then(Value::as_array)
            .expect("报告应包含规则详情");
        assert_eq!(rules[0]["hit_count"], 4);
    }

    #[test]
    fn plugin_rules_reject_unknown_plugin() {
        let plugins = vec![json!({"name": "Known", "parameters": {"Message": "本文"}})];
        let report =
            validate_plugin_rules_report(&plugins, r#"{"Missing":["$['parameters']['Message']"]}"#);

        assert_eq!(report.status, "error");
        assert_eq!(report.errors[0].code, "plugin_rules_invalid");
    }
}
