//! Note 标签候选导出、规则校验与入库记录构建。
//!
//! RPG Maker 的 `note` 字段常被插件当作元标签容器。本模块只把外部明确授权
//! 的 `<标签:值>` 视为玩家可见文本，机器协议标签会被拒绝，避免翻译破坏插件
//! 参数协议。

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

use regex::Regex;
use serde_json::{Map, Value, json};

use crate::error::{AttMzError, Result};
use crate::report::{AgentReport, issue};

const MAP_NOTE_FILE_PATTERN: &str = "Map*.json";
const PLUGINS_FILE_NAME: &str = "plugins.js";
const MACHINE_NOTE_TAG_NAMES: &[&str] = &[
    "upgrade",
    "chainskill",
    "equipstate",
    "passivestate",
    "skillid",
    "itemid",
    "weaponid",
    "armorid",
    "stateid",
    "switch",
    "variable",
    "eval",
    "script",
    "formula",
];

/// 单个 `note` 字段来源。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NoteTagSource {
    /// 来源 data 文件名。
    pub file_name: String,
    /// 持有 `note` 字段的对象路径。
    pub owner_path: Vec<String>,
    /// `note` 字段原文。
    pub note_text: String,
    /// 正文定位路径前缀。
    pub location_prefix: String,
}

/// 数据库中的 Note 标签规则记录。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NoteTagRuleRecord {
    /// data 文件名或文件模式，例如 `Items.json` / `Map*.json`。
    pub file_name: String,
    /// 已授权作为玩家可见文本处理的标签名。
    pub tag_names: Vec<String>,
}

/// Note 标签规则导入摘要。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NoteTagRuleImportResult {
    /// 本次写入的文件规则数量。
    pub imported_file_count: usize,
    /// 本次写入的标签数量。
    pub imported_tag_count: usize,
    /// 因规则变化而删除的旧译文数量。
    pub deleted_translation_items: usize,
}

/// Note 标签提取预览项。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NoteTagExtractedItem {
    /// 正文在游戏里的内部位置。
    pub location_path: String,
    /// 提取后准备交给翻译流程的原文。
    pub original_text: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct NoteTagMatch {
    tag_name: String,
    value: String,
    value_span: Option<(usize, usize)>,
}

/// 解析外部 Note 标签规则 JSON 文本。
pub fn parse_note_tag_rule_import_text(raw_text: &str) -> Result<BTreeMap<String, Vec<String>>> {
    let value: Value =
        serde_json::from_str(raw_text.trim_start_matches('\u{feff}')).map_err(|source| {
            AttMzError::Json {
                context: "Note 标签规则 JSON".to_string(),
                source,
            }
        })?;
    let Some(object) = value.as_object() else {
        return Err(AttMzError::InvalidConfig(
            "Note 标签规则顶层必须是对象".to_string(),
        ));
    };
    let mut rules = BTreeMap::new();
    for (file_name, raw_tags) in object {
        let Some(tag_values) = raw_tags.as_array() else {
            return Err(AttMzError::InvalidConfig(format!(
                "Note 标签规则 {file_name} 的标签列表必须是数组"
            )));
        };
        let mut tags = Vec::new();
        for raw_tag in tag_values {
            let Some(tag_name) = raw_tag.as_str() else {
                return Err(AttMzError::InvalidConfig(format!(
                    "Note 标签规则 {file_name} 的标签名必须是字符串"
                )));
            };
            tags.push(tag_name.to_string());
        }
        rules.insert(file_name.clone(), tags);
    }
    Ok(rules)
}

/// 收集当前游戏中的 Note 标签候选并生成 Agent 报告。
pub fn export_note_tag_candidates_report(
    data_files: &BTreeMap<String, Value>,
    output_path: &std::path::Path,
    source_text_required_pattern: &str,
) -> Result<AgentReport> {
    let candidates = collect_note_tag_candidates(data_files, source_text_required_pattern)?;
    let candidate_tag_count = candidates.len();
    let candidate_value_count = candidate_count_sum(&candidates, "hit_count");
    let translatable_value_count = candidate_count_sum(&candidates, "translatable_hit_count");

    let mut summary = Map::new();
    summary.insert(
        "candidate_tag_count".to_string(),
        json!(candidate_tag_count),
    );
    summary.insert(
        "candidate_value_count".to_string(),
        json!(candidate_value_count),
    );
    summary.insert(
        "translatable_value_count".to_string(),
        json!(translatable_value_count),
    );
    summary.insert("output".to_string(), json!(output_path));

    let mut details = Map::new();
    details.insert("candidates".to_string(), Value::Array(candidates));
    let warnings = if candidate_tag_count == 0 {
        vec![issue(
            "note_tag_candidates_empty",
            "当前游戏没有发现 data Note 标签候选",
        )]
    } else {
        Vec::new()
    };
    Ok(AgentReport::from_parts(
        Vec::new(),
        warnings,
        summary,
        details,
    ))
}

/// 根据外部规则构建可入库记录。
pub fn build_note_tag_rule_records_from_import(
    data_files: &BTreeMap<String, Value>,
    import_file: &BTreeMap<String, Vec<String>>,
    source_text_required_pattern: &str,
) -> Result<Vec<NoteTagRuleRecord>> {
    let source_pattern = compile_source_pattern(source_text_required_pattern)?;
    let mut records = Vec::new();
    for (file_name, tag_names) in import_file {
        let normalized_file_name = file_name.trim();
        if normalized_file_name.is_empty() {
            return Err(AttMzError::InvalidConfig(
                "Note 标签规则不能包含空文件名".to_string(),
            ));
        }
        if !normalized_file_name.ends_with(".json") {
            return Err(AttMzError::InvalidConfig(format!(
                "Note 标签规则文件模式必须指向 data JSON 文件: {normalized_file_name}"
            )));
        }
        if matched_note_file_names(data_files, normalized_file_name).is_empty() {
            return Err(AttMzError::InvalidConfig(format!(
                "Note 标签规则文件模式没有匹配当前 data 文件: {normalized_file_name}"
            )));
        }
        let normalized_tag_names = normalize_tag_names(tag_names)?;
        if normalized_tag_names.is_empty() {
            return Err(AttMzError::InvalidConfig(format!(
                "Note 标签规则不能为空: {normalized_file_name}"
            )));
        }
        for tag_name in &normalized_tag_names {
            validate_note_tag_rule_hit(
                data_files,
                normalized_file_name,
                tag_name,
                &source_pattern,
            )?;
        }
        records.push(NoteTagRuleRecord {
            file_name: normalized_file_name.to_string(),
            tag_names: normalized_tag_names,
        });
    }
    Ok(records)
}

/// 校验 Note 标签规则并生成 Agent 报告。
pub fn validate_note_tag_rules_report(
    data_files: &BTreeMap<String, Value>,
    rules_text: &str,
    source_text_required_pattern: &str,
) -> AgentReport {
    let import_file = match parse_note_tag_rule_import_text(rules_text) {
        Ok(import_file) => import_file,
        Err(error) => return note_tag_rules_invalid_report(error.to_string()),
    };
    let records = match build_note_tag_rule_records_from_import(
        data_files,
        &import_file,
        source_text_required_pattern,
    ) {
        Ok(records) => records,
        Err(error) => return note_tag_rules_invalid_report(error.to_string()),
    };
    let extracted_items =
        match extract_note_tag_items(data_files, &records, source_text_required_pattern) {
            Ok(items) => items,
            Err(error) => return note_tag_rules_invalid_report(error.to_string()),
        };

    let mut details = Map::new();
    details.insert(
        "write_back_preview".to_string(),
        json!({
            "checked_item_count": extracted_items.len(),
            "status": "ok",
        }),
    );
    details.insert(
        "rules".to_string(),
        Value::Array(
            records
                .iter()
                .map(|record| {
                    let matched_items = extracted_items
                        .iter()
                        .filter(|item| note_tag_item_matches_rule(item, record))
                        .collect::<Vec<_>>();
                    json!({
                        "file_name": record.file_name,
                        "tag_count": record.tag_names.len(),
                        "tag_names": record.tag_names,
                        "hit_count": matched_items.len(),
                        "samples": matched_items
                            .iter()
                            .take(5)
                            .map(|item| json!(item.original_text))
                            .collect::<Vec<_>>(),
                    })
                })
                .collect(),
        ),
    );
    let warnings = if records.is_empty() {
        vec![issue("note_tag_rules_empty", "Note 标签规则为空")]
    } else {
        Vec::new()
    };
    AgentReport::from_parts(
        Vec::new(),
        warnings,
        note_tag_rules_summary(&records, extracted_items.len(), None),
        details,
    )
}

/// 提取规则命中的 Note 标签文本项。
pub fn extract_note_tag_items(
    data_files: &BTreeMap<String, Value>,
    records: &[NoteTagRuleRecord],
    source_text_required_pattern: &str,
) -> Result<Vec<NoteTagExtractedItem>> {
    let source_pattern = compile_source_pattern(source_text_required_pattern)?;
    let all_sources = collect_note_tag_sources(data_files, None);
    let mut items = Vec::new();
    let mut seen_location_paths = HashSet::new();
    for record in records {
        let tag_names = record.tag_names.iter().collect::<HashSet<_>>();
        for source in &all_sources {
            if !note_file_pattern_matches(&source.file_name, &record.file_name) {
                continue;
            }
            let mut matches_by_tag: HashMap<String, Vec<String>> = HashMap::new();
            for note_match in iter_note_tag_matches(&source.note_text)? {
                if !tag_names.contains(&note_match.tag_name) || note_match.value_span.is_none() {
                    continue;
                }
                matches_by_tag
                    .entry(note_match.tag_name)
                    .or_default()
                    .push(note_match.value);
            }
            for tag_name in &record.tag_names {
                let values = matches_by_tag.remove(tag_name).unwrap_or_default();
                if values.is_empty() {
                    continue;
                }
                if values.len() > 1 {
                    return Err(AttMzError::InvalidConfig(format!(
                        "{}/note/{tag_name} 标签重复，无法生成唯一定位路径",
                        source.location_prefix
                    )));
                }
                let normalized_value = normalize_visible_text_for_extraction(&values[0]);
                if !should_translate_source_text(&normalized_value, &source_pattern) {
                    continue;
                }
                let location_path = format!("{}/note/{tag_name}", source.location_prefix);
                if !seen_location_paths.insert(location_path.clone()) {
                    continue;
                }
                items.push(NoteTagExtractedItem {
                    location_path,
                    original_text: normalized_value,
                });
            }
        }
    }
    Ok(items)
}

/// 计算旧规则变更后需要清理的旧正文定位路径。
pub fn stale_note_tag_translation_paths(
    data_files: &BTreeMap<String, Value>,
    old_records: &[NoteTagRuleRecord],
    new_records: &[NoteTagRuleRecord],
    source_text_required_pattern: &str,
) -> Result<Vec<String>> {
    let old_paths = extract_note_tag_items(data_files, old_records, source_text_required_pattern)?
        .into_iter()
        .map(|item| item.location_path)
        .collect::<BTreeSet<_>>();
    let new_paths = extract_note_tag_items(data_files, new_records, source_text_required_pattern)?
        .into_iter()
        .map(|item| item.location_path)
        .collect::<BTreeSet<_>>();
    Ok(old_paths.difference(&new_paths).cloned().collect())
}

fn collect_note_tag_candidates(
    data_files: &BTreeMap<String, Value>,
    source_text_required_pattern: &str,
) -> Result<Vec<Value>> {
    let source_pattern = compile_source_pattern(source_text_required_pattern)?;
    let mut stats: BTreeMap<(String, String), Map<String, Value>> = BTreeMap::new();
    let mut samples_by_key: HashMap<(String, String), Vec<String>> = HashMap::new();
    let mut locations_by_key: HashMap<(String, String), Vec<String>> = HashMap::new();
    let mut files_by_key: HashMap<(String, String), BTreeSet<String>> = HashMap::new();

    for source in collect_note_tag_sources(data_files, None) {
        let file_pattern = candidate_file_pattern(&source.file_name);
        for note_match in iter_note_tag_matches(&source.note_text)? {
            let key = (file_pattern.clone(), note_match.tag_name.clone());
            let stat = stats.entry(key.clone()).or_insert_with(|| {
                let mut stat = Map::new();
                stat.insert("file_name".to_string(), json!(file_pattern));
                stat.insert("tag_name".to_string(), json!(note_match.tag_name));
                stat.insert("hit_count".to_string(), json!(0));
                stat.insert("value_hit_count".to_string(), json!(0));
                stat.insert("translatable_hit_count".to_string(), json!(0));
                stat.insert("matched_file_count".to_string(), json!(0));
                stat.insert("sample_locations".to_string(), json!([]));
                stat.insert("sample_values".to_string(), json!([]));
                stat
            });
            let files = files_by_key.entry(key.clone()).or_default();
            files.insert(source.file_name.clone());
            stat.insert("matched_file_count".to_string(), json!(files.len()));
            increment_json_count(stat, "hit_count");
            if note_match.value_span.is_none() {
                continue;
            }
            increment_json_count(stat, "value_hit_count");
            let normalized_value = normalize_visible_text_for_extraction(&note_match.value);
            if should_translate_source_text(&normalized_value, &source_pattern) {
                increment_json_count(stat, "translatable_hit_count");
            }
            let samples = samples_by_key.entry(key.clone()).or_default();
            if !normalized_value.is_empty()
                && !samples.iter().any(|sample| sample == &normalized_value)
                && samples.len() < 5
            {
                samples.push(normalized_value.clone());
                stat.insert("sample_values".to_string(), json!(samples));
            }
            let locations = locations_by_key.entry(key).or_default();
            let location = format!("{}/note/{}", source.location_prefix, note_match.tag_name);
            if !locations.iter().any(|item| item == &location) && locations.len() < 5 {
                locations.push(location);
                stat.insert("sample_locations".to_string(), json!(locations));
            }
        }
    }
    Ok(stats.into_values().map(Value::Object).collect())
}

fn collect_note_tag_sources(
    data_files: &BTreeMap<String, Value>,
    file_pattern: Option<&str>,
) -> Vec<NoteTagSource> {
    let mut sources = Vec::new();
    for (file_name, value) in data_files {
        if file_name == PLUGINS_FILE_NAME || !file_name.ends_with(".json") || value.is_string() {
            continue;
        }
        if let Some(pattern) = file_pattern
            && !note_file_pattern_matches(file_name, pattern)
        {
            continue;
        }
        collect_note_tag_sources_in_value(file_name, value, &mut Vec::new(), &mut sources);
    }
    sources
}

fn collect_note_tag_sources_in_value(
    file_name: &str,
    value: &Value,
    owner_path: &mut Vec<String>,
    sources: &mut Vec<NoteTagSource>,
) {
    if let Some(object) = value.as_object() {
        if let Some(note_value) = object.get("note").and_then(Value::as_str)
            && !note_value.is_empty()
        {
            sources.push(NoteTagSource {
                file_name: file_name.to_string(),
                owner_path: owner_path.clone(),
                note_text: note_value.to_string(),
                location_prefix: format_location_prefix(file_name, owner_path),
            });
        }
        for (key, child_value) in object {
            if key == "note" {
                continue;
            }
            owner_path.push(key.clone());
            collect_note_tag_sources_in_value(file_name, child_value, owner_path, sources);
            let _ = owner_path.pop();
        }
        return;
    }
    if let Some(array) = value.as_array() {
        for (index, child_value) in array.iter().enumerate() {
            if child_value.is_null() {
                continue;
            }
            owner_path.push(index.to_string());
            collect_note_tag_sources_in_value(file_name, child_value, owner_path, sources);
            let _ = owner_path.pop();
        }
    }
}

fn validate_note_tag_rule_hit(
    data_files: &BTreeMap<String, Value>,
    file_name: &str,
    tag_name: &str,
    source_pattern: &Regex,
) -> Result<()> {
    let mut hit_count = 0usize;
    let mut translatable_hit_count = 0usize;
    for source in collect_note_tag_sources(data_files, Some(file_name)) {
        let matches = iter_note_tag_matches(&source.note_text)?
            .into_iter()
            .filter(|note_match| note_match.tag_name == tag_name && note_match.value_span.is_some())
            .collect::<Vec<_>>();
        if matches.len() > 1 {
            return Err(AttMzError::InvalidConfig(format!(
                "{}/note/{tag_name} 标签重复，无法生成唯一定位路径",
                source.location_prefix
            )));
        }
        let Some(note_match) = matches.first() else {
            continue;
        };
        hit_count += 1;
        let normalized_value = normalize_visible_text_for_extraction(&note_match.value);
        if !normalized_value.is_empty()
            && should_translate_source_text(&normalized_value, source_pattern)
        {
            translatable_hit_count += 1;
        }
    }
    if hit_count == 0 {
        return Err(AttMzError::InvalidConfig(format!(
            "Note 标签规则没有命中当前游戏 Note 标签: {file_name}/{tag_name}"
        )));
    }
    if translatable_hit_count == 0 {
        return Err(AttMzError::InvalidConfig(format!(
            "Note 标签规则没有命中玩家可见可翻译文本: {file_name}/{tag_name}"
        )));
    }
    Ok(())
}

fn iter_note_tag_matches(note_text: &str) -> Result<Vec<NoteTagMatch>> {
    let pattern = Regex::new(r"(?s)<(?P<tag>[^<>:\r\n]+)(?::(?P<value>[^<>]*))?>")
        .map_err(|error| AttMzError::InvalidConfig(format!("Note 标签解析正则不可用: {error}")))?;
    let mut matches = Vec::new();
    for captures in pattern.captures_iter(note_text) {
        let Some(tag_match) = captures.name("tag") else {
            continue;
        };
        let tag_name = tag_match.as_str().trim();
        if tag_name.is_empty() {
            continue;
        }
        let value_match = captures.name("value");
        matches.push(NoteTagMatch {
            tag_name: tag_name.to_string(),
            value: value_match
                .map(|matched| matched.as_str().to_string())
                .unwrap_or_default(),
            value_span: value_match.map(|matched| (matched.start(), matched.end())),
        });
    }
    Ok(matches)
}

fn matched_note_file_names(
    data_files: &BTreeMap<String, Value>,
    file_pattern: &str,
) -> Vec<String> {
    data_files
        .iter()
        .filter_map(|(file_name, value)| {
            if file_name == PLUGINS_FILE_NAME || !file_name.ends_with(".json") || value.is_string()
            {
                return None;
            }
            note_file_pattern_matches(file_name, file_pattern).then(|| file_name.clone())
        })
        .collect()
}

fn normalize_tag_names(tag_names: &[String]) -> Result<Vec<String>> {
    let mut normalized_tag_names = Vec::new();
    for tag_name in tag_names {
        let normalized_tag_name = tag_name.trim();
        if normalized_tag_name.is_empty()
            || normalized_tag_names
                .iter()
                .any(|existing: &String| existing == normalized_tag_name)
        {
            continue;
        }
        if normalized_tag_name.contains('/') {
            return Err(AttMzError::InvalidConfig(format!(
                "Note 标签名不能包含定位路径分隔符 `/`: {normalized_tag_name}"
            )));
        }
        if MACHINE_NOTE_TAG_NAMES
            .iter()
            .any(|name| name.eq_ignore_ascii_case(normalized_tag_name))
        {
            return Err(AttMzError::InvalidConfig(format!(
                "Note 标签属于机器协议，不能作为玩家可见文本导入: {normalized_tag_name}"
            )));
        }
        normalized_tag_names.push(normalized_tag_name.to_string());
    }
    Ok(normalized_tag_names)
}

fn note_tag_item_matches_rule(item: &NoteTagExtractedItem, record: &NoteTagRuleRecord) -> bool {
    if !record
        .tag_names
        .iter()
        .any(|tag_name| item.location_path.ends_with(&format!("/note/{tag_name}")))
    {
        return false;
    }
    let Some(file_name) = item.location_path.split('/').next() else {
        return false;
    };
    note_file_pattern_matches(file_name, &record.file_name)
}

fn candidate_file_pattern(file_name: &str) -> String {
    if is_map_file_name(file_name) {
        MAP_NOTE_FILE_PATTERN.to_string()
    } else {
        file_name.to_string()
    }
}

fn note_file_pattern_matches(file_name: &str, file_pattern: &str) -> bool {
    if file_pattern == MAP_NOTE_FILE_PATTERN {
        return is_map_file_name(file_name);
    }
    wildcard_match(file_name.as_bytes(), file_pattern.as_bytes())
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

fn wildcard_match(text: &[u8], pattern: &[u8]) -> bool {
    let mut previous = vec![false; text.len() + 1];
    previous[0] = true;
    for pattern_byte in pattern {
        let mut current = vec![false; text.len() + 1];
        if *pattern_byte == b'*' {
            current[0] = previous[0];
        }
        for text_index in 1..=text.len() {
            current[text_index] = match *pattern_byte {
                b'*' => current[text_index - 1] || previous[text_index],
                b'?' => previous[text_index - 1],
                _ => previous[text_index - 1] && *pattern_byte == text[text_index - 1],
            };
        }
        previous = current;
    }
    previous[text.len()]
}

fn format_location_prefix(file_name: &str, owner_path: &[String]) -> String {
    if owner_path.is_empty() {
        file_name.to_string()
    } else {
        format!("{}/{}", file_name, owner_path.join("/"))
    }
}

fn compile_source_pattern(source_text_required_pattern: &str) -> Result<Regex> {
    Regex::new(source_text_required_pattern).map_err(|error| {
        AttMzError::InvalidConfig(format!(
            "text_rules.source_text_required_pattern 不是有效正则: {error}"
        ))
    })
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

fn increment_json_count(object: &mut Map<String, Value>, key: &str) {
    let next_value = object.get(key).and_then(Value::as_u64).unwrap_or(0) + 1;
    object.insert(key.to_string(), json!(next_value));
}

fn candidate_count_sum(candidates: &[Value], key: &str) -> usize {
    candidates
        .iter()
        .filter_map(Value::as_object)
        .filter_map(|object| object.get(key).and_then(Value::as_u64))
        .map(|value| value as usize)
        .sum()
}

fn note_tag_rules_summary(
    records: &[NoteTagRuleRecord],
    hit_count: usize,
    deleted_translation_items: Option<usize>,
) -> Map<String, Value> {
    let mut summary = Map::new();
    summary.insert("file_count".to_string(), json!(records.len()));
    summary.insert(
        "tag_count".to_string(),
        json!(
            records
                .iter()
                .map(|record| record.tag_names.len())
                .sum::<usize>()
        ),
    );
    summary.insert("hit_count".to_string(), json!(hit_count));
    if let Some(deleted_translation_items) = deleted_translation_items {
        summary.insert(
            "deleted_translation_items".to_string(),
            json!(deleted_translation_items),
        );
    }
    summary
}

fn note_tag_rules_invalid_report(message: String) -> AgentReport {
    let mut details = Map::new();
    details.insert("rules".to_string(), json!([]));
    AgentReport::from_parts(
        vec![issue(
            "note_tag_rules_invalid",
            format!("Note 标签规则不可导入: {message}"),
        )],
        Vec::new(),
        note_tag_rules_summary(&[], 0, None),
        details,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn data_files() -> BTreeMap<String, Value> {
        BTreeMap::from([
            (
                "Items.json".to_string(),
                json!([
                    null,
                    {
                        "id": 1,
                        "name": "Potion",
                        "note": "<拡張説明:一行目\n二行目>\n<upgrade:1,2,3>\n<ExtendDesc:別説明>"
                    }
                ]),
            ),
            (
                "Map001.json".to_string(),
                json!({
                    "events": [
                        null,
                        {"id": 1, "note": "<namePop:導き手>\n<machine:1>"}
                    ]
                }),
            ),
        ])
    }

    #[test]
    fn note_tag_candidates_group_map_files() {
        let report = export_note_tag_candidates_report(
            &data_files(),
            std::path::Path::new("note-tag-candidates.json"),
            crate::config::DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN,
        )
        .expect("候选应导出成功");

        assert_eq!(report.status, "ok");
        let candidates = report
            .details
            .get("candidates")
            .and_then(Value::as_array)
            .expect("候选详情应存在");
        assert!(candidates.iter().any(|candidate| {
            candidate["file_name"] == "Map*.json" && candidate["tag_name"] == "namePop"
        }));
    }

    #[test]
    fn note_tag_rules_reject_machine_protocol_tags() {
        let report = validate_note_tag_rules_report(
            &data_files(),
            r#"{"Items.json":["upgrade"]}"#,
            crate::config::DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN,
        );

        assert_eq!(report.status, "error");
        assert!(report.errors[0].message.contains("机器协议"));
    }

    #[test]
    fn note_tag_rules_extract_visible_values() {
        let report = validate_note_tag_rules_report(
            &data_files(),
            r#"{"Items.json":["拡張説明","ExtendDesc"],"Map*.json":["namePop"]}"#,
            crate::config::DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN,
        );

        assert_eq!(report.status, "ok");
        assert_eq!(report.summary.get("hit_count"), Some(&json!(3)));
    }
}
