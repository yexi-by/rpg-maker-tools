//! 日文残留例外规则解析、校验和报告生成。
//!
//! 外部规则以“文本在游戏里的内部位置 -> 允许保留片段”的 JSON 对象表达。
//! 本模块只根据当前可提取正文和已保存译文校验规则，确保例外片段来自真实文本，
//! 避免把任意日文残留静默放行。

use std::collections::BTreeMap;

use serde::Deserialize;
use serde_json::{Map, Value, json};

use crate::db::{JapaneseResidualRuleRecord, TranslationItemRecord};
use crate::error::{AttMzError, Result};
use crate::placeholder_scan::ActiveTextItem;
use crate::report::{AgentReport, issue};

/// 单条日文残留例外规则。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JapaneseResidualRuleSpec {
    /// 允许在该条译文中保留的日文片段。
    pub allowed_terms: Vec<String>,
    /// 允许保留的业务原因。
    pub reason: String,
}

/// 外部日文残留例外规则文件结构。
pub type JapaneseResidualRuleImportFile = BTreeMap<String, JapaneseResidualRuleSpec>;

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct RawJapaneseResidualRuleSpec {
    #[serde(default)]
    allowed_terms: Vec<String>,
    reason: String,
}

/// 解析外部日文残留例外规则 JSON 文本。
pub fn parse_japanese_residual_rule_import_text(
    raw_text: &str,
) -> Result<JapaneseResidualRuleImportFile> {
    let raw_value: BTreeMap<String, RawJapaneseResidualRuleSpec> =
        serde_json::from_str(raw_text.trim_start_matches('\u{feff}')).map_err(|source| {
            AttMzError::Json {
                context: "日文残留例外规则 JSON".to_string(),
                source,
            }
        })?;
    let mut import_file = BTreeMap::new();
    for (location_path, spec) in raw_value {
        import_file.insert(location_path, normalize_rule_spec(spec)?);
    }
    Ok(import_file)
}

/// 从 JSON 文本直接构建可写入数据库的日文残留例外规则记录。
pub fn build_japanese_residual_rule_records_from_text(
    active_items: &[ActiveTextItem],
    translated_items: &[TranslationItemRecord],
    rules_text: &str,
) -> Result<Vec<JapaneseResidualRuleRecord>> {
    let import_file = parse_japanese_residual_rule_import_text(rules_text)?;
    build_japanese_residual_rule_records_from_import(&import_file, active_items, translated_items)
}

/// 根据当前游戏正文校验外部规则，并构建可写入数据库的记录。
pub fn build_japanese_residual_rule_records_from_import(
    import_file: &JapaneseResidualRuleImportFile,
    active_items: &[ActiveTextItem],
    translated_items: &[TranslationItemRecord],
) -> Result<Vec<JapaneseResidualRuleRecord>> {
    let active_items_by_path = active_items
        .iter()
        .map(|item| (item.location_path.as_str(), item))
        .collect::<BTreeMap<_, _>>();
    let translated_items_by_path = translated_items
        .iter()
        .map(|item| (item.location_path.as_str(), item))
        .collect::<BTreeMap<_, _>>();
    let mut records = Vec::new();
    for (location_path, spec) in import_file {
        let normalized_path = location_path.trim();
        if normalized_path.is_empty() {
            return Err(AttMzError::InvalidConfig(
                "日文残留例外规则不能包含空 location_path".to_string(),
            ));
        }
        let active_item = active_items_by_path.get(normalized_path).ok_or_else(|| {
            AttMzError::InvalidConfig(format!(
                "日文残留例外规则定位不在当前可提取文本范围内: {location_path}"
            ))
        })?;
        validate_allowed_terms_appear_in_item(
            normalized_path,
            &spec.allowed_terms,
            active_item,
            translated_items_by_path.get(normalized_path).copied(),
        )?;
        records.push(JapaneseResidualRuleRecord {
            location_path: normalized_path.to_string(),
            allowed_terms: spec.allowed_terms.clone(),
            reason: spec.reason.clone(),
        });
    }
    Ok(records)
}

/// 校验日文残留例外规则并生成 Agent 报告。
pub fn validate_japanese_residual_rules_report(
    active_items: &[ActiveTextItem],
    translated_items: &[TranslationItemRecord],
    rules_text: &str,
) -> AgentReport {
    let records = match build_japanese_residual_rule_records_from_text(
        active_items,
        translated_items,
        rules_text,
    ) {
        Ok(records) => records,
        Err(error) => return japanese_residual_rules_invalid_report(error.to_string(), true),
    };
    let mut warnings = Vec::new();
    if records.is_empty() {
        warnings.push(issue(
            "japanese_residual_rules_empty",
            "日文残留例外规则为空",
        ));
    }
    AgentReport::from_parts(
        Vec::new(),
        warnings,
        japanese_residual_rules_summary(&records),
        japanese_residual_rules_details(&records),
    )
}

/// 构建日文残留例外规则导入成功报告。
pub fn japanese_residual_rules_import_report(
    records: &[JapaneseResidualRuleRecord],
) -> AgentReport {
    let mut warnings = Vec::new();
    if records.is_empty() {
        warnings.push(issue(
            "japanese_residual_rules_empty",
            "已导入空日文残留例外规则",
        ));
    }
    AgentReport::from_parts(
        Vec::new(),
        warnings,
        japanese_residual_rules_summary(records),
        japanese_residual_rules_details(records),
    )
}

/// 构建日文残留例外规则不可导入报告。
pub fn japanese_residual_rules_invalid_report(
    message: String,
    include_rule_details: bool,
) -> AgentReport {
    let mut details = Map::new();
    if include_rule_details {
        details.insert("rules".to_string(), json!([]));
    }
    AgentReport::from_parts(
        vec![issue(
            "japanese_residual_rules_invalid",
            format!("日文残留例外规则不可导入: {message}"),
        )],
        Vec::new(),
        japanese_residual_rules_summary(&[]),
        details,
    )
}

fn normalize_rule_spec(raw_spec: RawJapaneseResidualRuleSpec) -> Result<JapaneseResidualRuleSpec> {
    let mut allowed_terms = Vec::new();
    for term in raw_spec.allowed_terms {
        let normalized_term = term.trim();
        if normalized_term.is_empty()
            || allowed_terms
                .iter()
                .any(|existing: &String| existing == normalized_term)
        {
            continue;
        }
        allowed_terms.push(normalized_term.to_string());
    }
    if allowed_terms.is_empty() {
        return Err(AttMzError::InvalidConfig(
            "allowed_terms 不能为空".to_string(),
        ));
    }
    let reason = raw_spec.reason.trim();
    if reason.is_empty() {
        return Err(AttMzError::InvalidConfig("reason 不能为空".to_string()));
    }
    Ok(JapaneseResidualRuleSpec {
        allowed_terms,
        reason: reason.to_string(),
    })
}

fn validate_allowed_terms_appear_in_item(
    location_path: &str,
    allowed_terms: &[String],
    active_item: &ActiveTextItem,
    translated_item: Option<&TranslationItemRecord>,
) -> Result<()> {
    let mut visible_text_parts = active_item.original_lines.clone();
    if let Some(translated_item) = translated_item {
        visible_text_parts.extend(translated_item.translation_lines.clone());
    }
    let visible_text = visible_text_parts.join("\n");
    let missing_terms = allowed_terms
        .iter()
        .filter(|term| !visible_text.contains(term.as_str()))
        .cloned()
        .collect::<Vec<_>>();
    if !missing_terms.is_empty() {
        return Err(AttMzError::InvalidConfig(format!(
            "{location_path} 的 allowed_terms 未出现在当前条目原文或译文中: {}",
            missing_terms.join("、")
        )));
    }
    Ok(())
}

fn japanese_residual_rules_summary(records: &[JapaneseResidualRuleRecord]) -> Map<String, Value> {
    let mut summary = Map::new();
    summary.insert("rule_count".to_string(), json!(records.len()));
    summary.insert(
        "term_count".to_string(),
        json!(
            records
                .iter()
                .map(|record| record.allowed_terms.len())
                .sum::<usize>()
        ),
    );
    summary
}

fn japanese_residual_rules_details(records: &[JapaneseResidualRuleRecord]) -> Map<String, Value> {
    let mut details = Map::new();
    details.insert(
        "rules".to_string(),
        Value::Array(
            records
                .iter()
                .map(|record| {
                    json!({
                        "location_path": record.location_path,
                        "allowed_terms": record.allowed_terms,
                        "reason": record.reason,
                    })
                })
                .collect(),
        ),
    );
    details
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn japanese_residual_rules_normalize_and_validate_terms() {
        let active_items = vec![ActiveTextItem {
            location_path: "CommonEvents.json/1/0".to_string(),
            item_type: "long_text".to_string(),
            role: Some("旁白".to_string()),
            original_lines: vec!["こんにちは".to_string()],
            source_line_paths: Vec::new(),
        }];
        let rules_text = r#"{" CommonEvents.json/1/0 ":{"allowed_terms":[" こんにちは ","こんにちは"],"reason":" proper_noun "}}"#;

        let records =
            build_japanese_residual_rule_records_from_text(&active_items, &[], rules_text)
                .expect("日文残留例外规则应构建成功");

        assert_eq!(records.len(), 1);
        assert_eq!(records[0].location_path, "CommonEvents.json/1/0");
        assert_eq!(records[0].allowed_terms, vec!["こんにちは"]);
        assert_eq!(records[0].reason, "proper_noun");
    }

    #[test]
    fn japanese_residual_rules_reject_missing_term() {
        let active_items = vec![ActiveTextItem {
            location_path: "CommonEvents.json/1/0".to_string(),
            item_type: "long_text".to_string(),
            role: Some("旁白".to_string()),
            original_lines: vec!["こんにちは".to_string()],
            source_line_paths: Vec::new(),
        }];

        let error = build_japanese_residual_rule_records_from_text(
            &active_items,
            &[],
            r#"{"CommonEvents.json/1/0":{"allowed_terms":["世界"],"reason":"proper_noun"}}"#,
        )
        .expect_err("不存在于当前条目的片段必须拒绝");

        assert!(error.to_string().contains("未出现在当前条目原文或译文中"));
    }
}
