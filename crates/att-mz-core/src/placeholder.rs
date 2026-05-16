//! 自定义占位符规则解析与校验。
//!
//! 外部规则 JSON 必须是对象，键为正则表达式，值为自定义占位符模板。这里在
//! 入库前完成正则编译、空匹配检查和模板形状检查，避免无效规则污染长期数据库。

use std::collections::{BTreeMap, BTreeSet};

use fancy_regex::Regex as FancyRegex;
use regex::Regex;
use serde_json::{Map, Value, json};

use crate::error::{AttMzError, Result};
use crate::report::{AgentIssue, AgentReport, issue};

/// 单条自定义占位符规则。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlaceholderRule {
    /// 正则表达式文本。
    pub pattern_text: String,
    /// 占位符模板。
    pub placeholder_template: String,
}

/// 单个翻译条目的占位符上下文。
///
/// 上下文记录原文控制符和程序占位符之间的稳定映射，供手动译文导入、
/// 质量修复模板和后续模型译文校验复用。调用方应按条目而不是全局复用，
/// 因为自定义占位符编号只在单个条目内稳定。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlaceholderContext {
    /// 发送给模型或外部 Agent 对照时使用的占位符视图。
    pub text_for_model_lines: Vec<String>,
    /// 原始控制符到程序占位符的反向映射。
    pub original_to_placeholder: BTreeMap<String, String>,
    /// 程序占位符到原始控制符的恢复映射。
    pub placeholder_to_original: BTreeMap<String, String>,
    /// 原文中每个程序占位符应该出现的次数。
    pub placeholder_counts: BTreeMap<String, usize>,
}

/// 从 JSON 字符串解析自定义占位符规则。
///
/// JSON 顶层必须是对象；空对象合法，表示清空当前游戏数据库中的自定义规则。
pub fn parse_custom_placeholder_rules_text(rules_text: &str) -> Result<Vec<PlaceholderRule>> {
    let stripped_text = rules_text.trim_start_matches('\u{feff}').trim();
    if stripped_text.is_empty() {
        return Err(AttMzError::InvalidConfig(
            "自定义占位符规则 JSON 字符串不能为空".to_string(),
        ));
    }
    let value: Value = serde_json::from_str(stripped_text).map_err(|source| AttMzError::Json {
        context: "自定义占位符规则".to_string(),
        source,
    })?;
    let Some(object) = value.as_object() else {
        return Err(AttMzError::InvalidConfig(
            "自定义占位符规则顶层必须是对象".to_string(),
        ));
    };
    let mut rules = Vec::new();
    for (pattern_text, placeholder_value) in object {
        let Some(placeholder_template) = placeholder_value.as_str() else {
            return Err(AttMzError::InvalidConfig(format!(
                "自定义占位符规则 {pattern_text} 的值必须是字符串"
            )));
        };
        validate_custom_placeholder_rule(pattern_text, placeholder_template)?;
        rules.push(PlaceholderRule {
            pattern_text: pattern_text.clone(),
            placeholder_template: placeholder_template.to_string(),
        });
    }
    Ok(rules)
}

/// 校验自定义占位符规则并生成 Agent 报告。
///
/// 报告包含规则预览、样本文本替换/还原预览，以及常见误匹配风险。该函数不
/// 访问数据库，调用方负责决定规则来源是 CLI 文本、输入文件还是当前游戏数据库。
pub fn validate_placeholder_rules_report(
    rules: &[PlaceholderRule],
    sample_texts: &[String],
    source_label: &str,
) -> AgentReport {
    let mut errors = Vec::new();
    let mut warnings = Vec::new();
    let mut rule_details = Vec::new();
    for rule in rules {
        append_placeholder_rule_safety_issues(rule, &mut errors, &mut warnings);
        rule_details.push(json!({
            "pattern": rule.pattern_text,
            "placeholder_template": rule.placeholder_template,
            "placeholder_preview": format_placeholder_template(&rule.placeholder_template).unwrap_or_default(),
        }));
    }

    let mut sample_details = Vec::new();
    for sample_text in sample_texts {
        match preview_placeholder_sample(rules, sample_text) {
            Ok(preview) => sample_details.push(preview),
            Err(error) => errors.push(issue(
                "placeholder_preview",
                format!("样本文本预览失败: {error}"),
            )),
        }
    }
    warnings.extend(build_unprotected_control_warnings(rules, sample_texts));
    if rules.is_empty() {
        warnings.push(issue("placeholder_rules_empty", "当前没有自定义占位符规则"));
    }

    let mut summary = Map::new();
    summary.insert("source".to_string(), json!(source_label));
    summary.insert("rule_count".to_string(), json!(rules.len()));
    summary.insert("sample_count".to_string(), json!(sample_texts.len()));
    let mut details = Map::new();
    details.insert("rules".to_string(), Value::Array(rule_details));
    details.insert("samples".to_string(), Value::Array(sample_details));
    AgentReport::from_parts(errors, warnings, summary, details)
}

/// 为原文行生成发送给模型时使用的占位符视图。
///
/// 同一个翻译条目内重复出现的自定义控制符会复用同一个 `[CUSTOM_...]`
/// 占位符，保持和 Python 版 `TranslationItem.build_placeholders` 相同的
/// 外部可见行为。
pub fn build_text_for_model_lines(
    rules: &[PlaceholderRule],
    original_lines: &[String],
) -> Result<Vec<String>> {
    Ok(build_placeholder_context(rules, original_lines)?.text_for_model_lines)
}

/// 为原文行生成占位符上下文。
///
/// 该函数保持和 Python 版 `TranslationItem.build_placeholders` 相同的外部
/// 行为：标准 RMMZ 控制符、自定义控制符、数据库消息 `%1` 以及字面量转义
/// 都会转换成稳定的 `[RMMZ_...]` 或 `[CUSTOM_...]` 占位符。
pub fn build_placeholder_context(
    rules: &[PlaceholderRule],
    original_lines: &[String],
) -> Result<PlaceholderContext> {
    let mut custom_counter = 0usize;
    let mut custom_originals: BTreeMap<String, String> = BTreeMap::new();
    let mut text_for_model_lines = Vec::new();
    let mut original_to_placeholder = BTreeMap::new();
    let mut placeholder_to_original = BTreeMap::new();
    let mut placeholder_counts = BTreeMap::new();
    for original_line in original_lines {
        let spans = select_control_spans(rules, original_line)?;
        let mut text_for_model = String::new();
        let mut last_end = 0usize;
        for span in spans {
            text_for_model.push_str(&original_line[last_end..span.start]);
            let placeholder = match span.kind {
                SpanKind::Standard(placeholder) => placeholder,
                SpanKind::Custom(template) => {
                    if let Some(placeholder) = custom_originals.get(&span.original) {
                        placeholder.clone()
                    } else {
                        custom_counter += 1;
                        let placeholder =
                            format_placeholder_template_with_index(&template, custom_counter)?;
                        custom_originals.insert(span.original.to_string(), placeholder.clone());
                        placeholder
                    }
                }
            };
            register_placeholder_mapping(
                &mut original_to_placeholder,
                &mut placeholder_to_original,
                &mut placeholder_counts,
                &span.original,
                &placeholder,
            )?;
            text_for_model.push_str(&placeholder);
            last_end = span.end;
        }
        text_for_model.push_str(&original_line[last_end..]);
        if text_for_model.contains('\n') {
            register_placeholder_mapping(
                &mut original_to_placeholder,
                &mut placeholder_to_original,
                &mut placeholder_counts,
                "\n",
                REAL_LINE_BREAK_PLACEHOLDER,
            )?;
            text_for_model = text_for_model.replace('\n', REAL_LINE_BREAK_PLACEHOLDER);
        }
        text_for_model_lines.push(text_for_model);
    }
    Ok(PlaceholderContext {
        text_for_model_lines,
        original_to_placeholder,
        placeholder_to_original,
        placeholder_counts,
    })
}

/// 收集文本行中仍可见的程序占位符。
pub fn collect_placeholder_tokens(lines: &[String]) -> Result<BTreeSet<String>> {
    let pattern = all_placeholder_pattern()?;
    let mut placeholders = BTreeSet::new();
    for line in lines {
        for matched in pattern.find_iter(line) {
            placeholders.insert(matched.as_str().to_string());
        }
    }
    Ok(placeholders)
}

/// 把手动译文中的游戏原始控制符遮蔽成对应程序占位符。
///
/// 只有原文里已出现过的控制符会被映射回原占位符；译文新增的未知控制符会
/// 映射成 `[CUSTOM_UNEXPECTED_1]`，随后由占位符计数校验明确报错。
pub fn mask_translation_controls(
    rules: &[PlaceholderRule],
    context: &PlaceholderContext,
    translation_lines: &[String],
) -> Result<Vec<String>> {
    let mut masked_lines = Vec::new();
    for line in translation_lines {
        let spans = select_control_spans(rules, line)?;
        let mut masked_line = String::new();
        let mut last_end = 0usize;
        for span in spans {
            masked_line.push_str(&line[last_end..span.start]);
            let placeholder = context
                .original_to_placeholder
                .get(&span.original)
                .map(String::as_str)
                .unwrap_or("[CUSTOM_UNEXPECTED_1]");
            masked_line.push_str(placeholder);
            last_end = span.end;
        }
        masked_line.push_str(&line[last_end..]);
        if context
            .original_to_placeholder
            .get("\n")
            .is_some_and(|placeholder| placeholder == REAL_LINE_BREAK_PLACEHOLDER)
        {
            masked_line = masked_line.replace('\n', REAL_LINE_BREAK_PLACEHOLDER);
        }
        masked_lines.push(masked_line);
    }
    Ok(masked_lines)
}

/// 校验译文占位符数量和原文完全一致。
pub fn verify_placeholder_counts(
    context: &PlaceholderContext,
    masked_translation_lines: &[String],
) -> Result<()> {
    let translated_placeholders = collect_placeholder_tokens(masked_translation_lines)?;
    let mut errors = Vec::new();
    if context.placeholder_counts.is_empty() && !translated_placeholders.is_empty() {
        let joined_placeholders = translated_placeholders
            .into_iter()
            .collect::<Vec<_>>()
            .join("、");
        errors.push(format!(
            "原文不包含任何占位符，但译文新增了以下占位符: {joined_placeholders}"
        ));
    }

    let combined_text = masked_translation_lines.join("").to_lowercase();
    for (placeholder, expected_count) in &context.placeholder_counts {
        let actual_count = combined_text.matches(&placeholder.to_lowercase()).count();
        if placeholder.eq_ignore_ascii_case(LITERAL_LINE_BREAK_PLACEHOLDER) {
            if actual_count < *expected_count {
                errors.push(format!(
                    "占位符 {placeholder} 数量不足 (至少需要: {expected_count}, 实际: {actual_count})"
                ));
            }
            continue;
        }
        if actual_count != *expected_count {
            errors.push(format!(
                "占位符 {placeholder} 数量错误 (期望: {expected_count}, 实际: {actual_count})"
            ));
        }
    }

    if errors.is_empty() {
        Ok(())
    } else {
        Err(AttMzError::InvalidConfig(errors.join(";\n")))
    }
}

/// 把程序占位符还原成游戏原始控制符。
pub fn restore_placeholder_lines(
    context: &PlaceholderContext,
    translation_lines: &[String],
) -> Result<Vec<String>> {
    if context.placeholder_to_original.is_empty() {
        return Ok(translation_lines.to_vec());
    }
    let mut placeholders = context
        .placeholder_to_original
        .keys()
        .cloned()
        .collect::<Vec<_>>();
    placeholders.sort_by_key(|placeholder| std::cmp::Reverse(placeholder.len()));
    let mut restored_lines = Vec::new();
    for line in translation_lines {
        let mut restored_line = line.clone();
        for placeholder in &placeholders {
            let Some(original) = context.placeholder_to_original.get(placeholder) else {
                continue;
            };
            restored_line = replace_case_insensitive(&restored_line, placeholder, original)?;
        }
        restored_lines.push(restored_line);
    }
    Ok(restored_lines)
}

/// 统计没有被标准或自定义占位符规则覆盖的疑似反斜杠控制片段。
pub fn collect_unprotected_control_sequences(
    rules: &[PlaceholderRule],
    lines: &[String],
) -> Result<BTreeMap<String, usize>> {
    let mut counts = BTreeMap::new();
    for line in lines {
        let protected_spans = select_control_spans(rules, line)?;
        for candidate in raw_control_sequence_candidates(line) {
            if protected_spans
                .iter()
                .any(|span| candidate.start < span.end && span.start < candidate.end)
            {
                continue;
            }
            *counts.entry(candidate.original).or_default() += 1;
        }
    }
    Ok(counts)
}

fn validate_custom_placeholder_rule(pattern_text: &str, placeholder_template: &str) -> Result<()> {
    if pattern_text.trim().is_empty() {
        return Err(AttMzError::InvalidConfig(
            "自定义占位符规则的正则表达式不能为空".to_string(),
        ));
    }
    if placeholder_template.trim().is_empty() {
        return Err(AttMzError::InvalidConfig(
            "自定义占位符规则的占位符模板不能为空".to_string(),
        ));
    }
    let pattern = compile_custom_placeholder_regex(pattern_text)?;
    let empty_matched = pattern.is_match("").map_err(|error| {
        AttMzError::InvalidConfig(format!("自定义占位符正则匹配失败: {pattern_text}: {error}"))
    })?;
    if empty_matched {
        return Err(AttMzError::InvalidConfig(format!(
            "自定义占位符正则不能匹配空字符串: {pattern_text}"
        )));
    }
    let preview = format_placeholder_template(placeholder_template)?;
    let standard_pattern = Regex::new(r"(?i)^\[RMMZ_[A-Z0-9_]+\]$")
        .map_err(|error| AttMzError::InvalidConfig(format!("标准占位符检查正则不可用: {error}")))?;
    if standard_pattern.is_match(&preview) {
        return Err(AttMzError::InvalidConfig(format!(
            "自定义占位符模板不能生成 RMMZ 标准占位符: {placeholder_template}"
        )));
    }
    let custom_pattern = Regex::new(r"(?i)^\[CUSTOM_[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_\d+\]$")
        .map_err(|error| {
            AttMzError::InvalidConfig(format!("自定义占位符检查正则不可用: {error}"))
        })?;
    if !custom_pattern.is_match(&preview) {
        return Err(AttMzError::InvalidConfig(format!(
            "自定义占位符模板必须生成形如 [CUSTOM_NAME_1] 的方括号占位符，当前生成: {preview}"
        )));
    }
    Ok(())
}

fn compile_custom_placeholder_regex(pattern_text: &str) -> Result<FancyRegex> {
    FancyRegex::new(pattern_text).map_err(|error| {
        AttMzError::InvalidConfig(format!("自定义占位符正则无效: {pattern_text}: {error}"))
    })
}

fn append_placeholder_rule_safety_issues(
    rule: &PlaceholderRule,
    errors: &mut Vec<AgentIssue>,
    warnings: &mut Vec<AgentIssue>,
) {
    let Ok(pattern) = compile_custom_placeholder_regex(&rule.pattern_text) else {
        errors.push(issue(
            "placeholder_rules_invalid",
            format!("自定义占位符正则无效: {}", rule.pattern_text),
        ));
        return;
    };
    for (sample_text, label) in common_escape_samples() {
        match pattern.is_match(sample_text) {
            Ok(true) => {}
            Ok(false) => continue,
            Err(error) => {
                errors.push(issue(
                    "placeholder_rules_invalid",
                    format!("自定义占位符正则匹配失败: {}: {error}", rule.pattern_text),
                ));
                return;
            }
        }
        errors.push(issue(
            "placeholder_rule_matches_common_escape",
            format!(
                "规则 {} 会匹配{}，容易把合法文本误判为占位符",
                rule.pattern_text, label
            ),
        ));
    }
    for sample_text in ["普通中文文本", "日本語本文", "plain visible text"] {
        match pattern.is_match(sample_text) {
            Ok(true) => {}
            Ok(false) => continue,
            Err(error) => {
                errors.push(issue(
                    "placeholder_rules_invalid",
                    format!("自定义占位符正则匹配失败: {}: {error}", rule.pattern_text),
                ));
                return;
            }
        }
        warnings.push(issue(
            "placeholder_rule_matches_plain_text",
            format!(
                "规则 {} 会匹配普通正文样例 `{sample_text}`，请确认没有过宽吞掉玩家可见文本",
                rule.pattern_text
            ),
        ));
        return;
    }
}

fn common_escape_samples() -> &'static [(&'static str, &'static str)] {
    &[
        ("\\\"", "裸 \\\" 双引号转义"),
        ("\\'", "裸 \\' 单引号转义"),
        ("\\/", "裸 \\/ 斜杠转义"),
        ("\\?", "裸 \\? 问号转义"),
        ("\\a", "裸 \\a 响铃转义"),
        ("\\b", "裸 \\b 退格转义"),
        ("\\f", "裸 \\f 换页转义"),
        ("\\n", "裸 \\n 换行标记"),
        ("\\r", "裸 \\r 回车标记"),
        ("\\t", "裸 \\t 制表标记"),
        ("\\v", "裸 \\v 垂直制表转义"),
        ("\\x41", "裸 \\xHH 十六进制转义"),
        ("\\u3042", "裸 \\uXXXX Unicode 转义"),
        ("\\U0001F600", "裸 \\UXXXXXXXX Unicode 转义"),
        ("\\012", "裸八进制转义"),
    ]
}

fn preview_placeholder_sample(rules: &[PlaceholderRule], sample_text: &str) -> Result<Value> {
    let spans = select_control_spans(rules, sample_text)?;
    let mut text_for_model = String::new();
    let mut placeholder_map = Map::new();
    let mut custom_counter = 0usize;
    let mut custom_originals: Vec<(String, String)> = Vec::new();
    let mut last_end = 0usize;

    for span in spans {
        text_for_model.push_str(&sample_text[last_end..span.start]);
        let placeholder = match span.kind {
            SpanKind::Standard(placeholder) => placeholder,
            SpanKind::Custom(template) => {
                if let Some((_, placeholder)) = custom_originals
                    .iter()
                    .find(|(original, _placeholder)| original == &span.original)
                {
                    placeholder.clone()
                } else {
                    custom_counter += 1;
                    let placeholder =
                        format_placeholder_template_with_index(&template, custom_counter)?;
                    custom_originals.push((span.original.to_string(), placeholder.clone()));
                    placeholder
                }
            }
        };
        placeholder_map.insert(placeholder.clone(), json!(span.original));
        text_for_model.push_str(&placeholder);
        last_end = span.end;
    }
    text_for_model.push_str(&sample_text[last_end..]);

    let mut restored_text = text_for_model.clone();
    let mut placeholders: Vec<String> = placeholder_map.keys().cloned().collect();
    placeholders.sort_by_key(|placeholder| std::cmp::Reverse(placeholder.len()));
    for placeholder in placeholders {
        if let Some(Value::String(original)) = placeholder_map.get(&placeholder) {
            restored_text = replace_case_insensitive(&restored_text, &placeholder, original)?;
        }
    }

    Ok(json!({
        "original_text": sample_text,
        "text_for_model": text_for_model,
        "restored_text": restored_text,
        "roundtrip_ok": restored_text == sample_text,
        "placeholder_map": placeholder_map,
    }))
}

fn select_control_spans(rules: &[PlaceholderRule], text: &str) -> Result<Vec<ControlSpan>> {
    let mut spans = standard_control_spans(text)?;
    spans.extend(custom_control_spans(rules, text)?);
    spans.sort_by(|left, right| {
        left.start
            .cmp(&right.start)
            .then_with(|| right.priority.cmp(&left.priority))
            .then_with(|| (right.end - right.start).cmp(&(left.end - left.start)))
    });
    let mut selected = Vec::new();
    let mut protected_until = 0usize;
    for span in spans {
        if span.start < protected_until {
            continue;
        }
        protected_until = span.end;
        selected.push(span);
    }
    Ok(selected)
}

fn standard_control_spans(text: &str) -> Result<Vec<ControlSpan>> {
    let mut spans = Vec::new();
    let indexed = Regex::new(r"\\(?P<code>PX|PY|FS|V|N|P|C|I)\[(?P<param>\d+)\]")
        .map_err(|error| AttMzError::InvalidConfig(format!("标准控制符正则不可用: {error}")))?;
    for captures in indexed.captures_iter(text) {
        let Some(matched) = captures.get(0) else {
            continue;
        };
        let code = captures
            .name("code")
            .map(|value| value.as_str().to_ascii_uppercase())
            .unwrap_or_default();
        let param = captures
            .name("param")
            .map(|value| value.as_str())
            .unwrap_or_default();
        let Some(name) = indexed_code_name(&code) else {
            continue;
        };
        spans.push(ControlSpan {
            start: matched.start(),
            end: matched.end(),
            original: matched.as_str().to_string(),
            priority: 0,
            kind: SpanKind::Standard(format!("[RMMZ_{name}_{param}]")),
        });
    }
    let no_param = Regex::new(r"\\G")
        .map_err(|error| AttMzError::InvalidConfig(format!("标准控制符正则不可用: {error}")))?;
    for matched in no_param.find_iter(text) {
        if next_char_after(text, matched.end())
            .is_some_and(|char_value| char_value.is_ascii_alphabetic() || char_value == '[')
        {
            continue;
        }
        spans.push(ControlSpan {
            start: matched.start(),
            end: matched.end(),
            original: matched.as_str().to_string(),
            priority: 0,
            kind: SpanKind::Standard("[RMMZ_CURRENCY_UNIT]".to_string()),
        });
    }
    let symbol = Regex::new(r"\\(?P<symbol>[{}\\$.\|!><^])")
        .map_err(|error| AttMzError::InvalidConfig(format!("标准控制符正则不可用: {error}")))?;
    for captures in symbol.captures_iter(text) {
        let Some(matched) = captures.get(0) else {
            continue;
        };
        let symbol = captures
            .name("symbol")
            .map(|value| value.as_str())
            .unwrap_or_default();
        let Some(placeholder) = symbol_placeholder(symbol) else {
            continue;
        };
        spans.push(ControlSpan {
            start: matched.start(),
            end: matched.end(),
            original: matched.as_str().to_string(),
            priority: 0,
            kind: SpanKind::Standard(placeholder.to_string()),
        });
    }
    let percent = Regex::new(r"%(?P<param>\d+)")
        .map_err(|error| AttMzError::InvalidConfig(format!("标准控制符正则不可用: {error}")))?;
    for captures in percent.captures_iter(text) {
        let Some(matched) = captures.get(0) else {
            continue;
        };
        let param = captures
            .name("param")
            .map(|value| value.as_str())
            .unwrap_or_default();
        spans.push(ControlSpan {
            start: matched.start(),
            end: matched.end(),
            original: matched.as_str().to_string(),
            priority: 0,
            kind: SpanKind::Standard(format!("[RMMZ_MESSAGE_ARGUMENT_{param}]")),
        });
    }
    spans.extend(literal_escape_spans(text)?);
    Ok(spans)
}

fn custom_control_spans(rules: &[PlaceholderRule], text: &str) -> Result<Vec<ControlSpan>> {
    let mut spans = Vec::new();
    for rule in rules {
        let pattern = compile_custom_placeholder_regex(&rule.pattern_text)?;
        for matched in pattern.find_iter(text) {
            let matched = matched.map_err(|error| {
                AttMzError::InvalidConfig(format!(
                    "自定义占位符正则匹配失败: {}: {error}",
                    rule.pattern_text
                ))
            })?;
            spans.push(ControlSpan {
                start: matched.start(),
                end: matched.end(),
                original: matched.as_str().to_string(),
                priority: 1,
                kind: SpanKind::Custom(rule.placeholder_template.clone()),
            });
        }
    }
    Ok(spans)
}

fn indexed_code_name(code: &str) -> Option<&'static str> {
    match code {
        "V" => Some("VARIABLE"),
        "N" => Some("ACTOR_NAME"),
        "P" => Some("PARTY_MEMBER_NAME"),
        "C" => Some("TEXT_COLOR"),
        "I" => Some("ICON"),
        "PX" => Some("TEXT_X_POSITION"),
        "PY" => Some("TEXT_Y_POSITION"),
        "FS" => Some("FONT_SIZE"),
        _ => None,
    }
}

fn symbol_placeholder(symbol: &str) -> Option<&'static str> {
    match symbol {
        "{" => Some("[RMMZ_FONT_LARGER]"),
        "}" => Some("[RMMZ_FONT_SMALLER]"),
        "\\" => Some("[RMMZ_BACKSLASH]"),
        "$" => Some("[RMMZ_SHOW_GOLD_WINDOW]"),
        "." => Some("[RMMZ_WAIT_SHORT]"),
        "|" => Some("[RMMZ_WAIT_LONG]"),
        "!" => Some("[RMMZ_WAIT_INPUT]"),
        ">" => Some("[RMMZ_INSTANT_TEXT_ON]"),
        "<" => Some("[RMMZ_INSTANT_TEXT_OFF]"),
        "^" => Some("[RMMZ_NO_WAIT]"),
        _ => None,
    }
}

fn literal_escape_spans(text: &str) -> Result<Vec<ControlSpan>> {
    let mut spans = Vec::new();
    for (original, placeholder) in literal_escape_placeholders() {
        let pattern = Regex::new(&regex::escape(original))
            .map_err(|error| AttMzError::InvalidConfig(format!("字面量转义正则不可用: {error}")))?;
        for matched in pattern.find_iter(text) {
            spans.push(ControlSpan {
                start: matched.start(),
                end: matched.end(),
                original: matched.as_str().to_string(),
                priority: 0,
                kind: SpanKind::Standard(placeholder.to_string()),
            });
        }
    }
    for (escape_name, pattern_text) in [
        ("UNICODE", r"\\(?:u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8})"),
        ("HEX", r"\\x[0-9A-Fa-f]{2}"),
        ("OCTAL", r"\\[0-7]{1,3}"),
    ] {
        let pattern = Regex::new(pattern_text).map_err(|error| {
            AttMzError::InvalidConfig(format!("字面量动态转义正则不可用: {error}"))
        })?;
        for matched in pattern.find_iter(text) {
            if escape_name == "OCTAL"
                && next_char_after(text, matched.end()).is_some_and(|char_value| char_value == '[')
            {
                continue;
            }
            spans.push(ControlSpan {
                start: matched.start(),
                end: matched.end(),
                original: matched.as_str().to_string(),
                priority: 0,
                kind: SpanKind::Standard(format!(
                    "[RMMZ_LITERAL_{escape_name}_ESCAPE_{}]",
                    hex_upper(matched.as_str())
                )),
            });
        }
    }
    Ok(spans)
}

fn literal_escape_placeholders() -> &'static [(&'static str, &'static str)] {
    &[
        ("\\\"", "[RMMZ_LITERAL_DOUBLE_QUOTE]"),
        ("\\'", "[RMMZ_LITERAL_SINGLE_QUOTE]"),
        ("\\/", "[RMMZ_LITERAL_SLASH]"),
        ("\\?", "[RMMZ_LITERAL_QUESTION_MARK]"),
        ("\\a", "[RMMZ_LITERAL_BELL]"),
        ("\\b", "[RMMZ_LITERAL_BACKSPACE]"),
        ("\\f", "[RMMZ_LITERAL_FORM_FEED]"),
        (r"\n", LITERAL_LINE_BREAK_PLACEHOLDER),
        ("\\r", "[RMMZ_LITERAL_CARRIAGE_RETURN]"),
        ("\\t", "[RMMZ_LITERAL_TAB]"),
        ("\\v", "[RMMZ_LITERAL_VERTICAL_TAB]"),
    ]
}

fn hex_upper(text: &str) -> String {
    text.as_bytes()
        .iter()
        .map(|byte| format!("{byte:02X}"))
        .collect::<Vec<_>>()
        .join("")
}

fn next_char_after(text: &str, byte_index: usize) -> Option<char> {
    text.get(byte_index..)?.chars().next()
}

fn register_placeholder_mapping(
    original_to_placeholder: &mut BTreeMap<String, String>,
    placeholder_to_original: &mut BTreeMap<String, String>,
    placeholder_counts: &mut BTreeMap<String, usize>,
    original: &str,
    placeholder: &str,
) -> Result<()> {
    if let Some(existing_original) = placeholder_to_original.get(placeholder)
        && existing_original != original
    {
        return Err(AttMzError::InvalidConfig(format!(
            "占位符 {placeholder} 同时匹配了多个不同片段: {existing_original} / {original}"
        )));
    }
    original_to_placeholder.insert(original.to_string(), placeholder.to_string());
    placeholder_to_original.insert(placeholder.to_string(), original.to_string());
    *placeholder_counts
        .entry(placeholder.to_string())
        .or_default() += 1;
    Ok(())
}

fn all_placeholder_pattern() -> Result<Regex> {
    Regex::new(
        r"(?i)\[RMMZ_(?:VARIABLE|ACTOR_NAME|PARTY_MEMBER_NAME|TEXT_COLOR|ICON|TEXT_X_POSITION|TEXT_Y_POSITION|FONT_SIZE)_\d+\]|\[RMMZ_MESSAGE_ARGUMENT_\d+\]|\[RMMZ_LITERAL_(?:UNICODE|HEX|OCTAL)_ESCAPE_[0-9A-F]+\]|\[RMMZ_REAL_LINE_BREAK\]|\[RMMZ_LITERAL_LINE_BREAK\]|\[RMMZ_LITERAL_DOUBLE_QUOTE\]|\[RMMZ_LITERAL_SINGLE_QUOTE\]|\[RMMZ_LITERAL_SLASH\]|\[RMMZ_LITERAL_QUESTION_MARK\]|\[RMMZ_LITERAL_BELL\]|\[RMMZ_LITERAL_BACKSPACE\]|\[RMMZ_LITERAL_FORM_FEED\]|\[RMMZ_LITERAL_CARRIAGE_RETURN\]|\[RMMZ_LITERAL_TAB\]|\[RMMZ_LITERAL_VERTICAL_TAB\]|\[RMMZ_CURRENCY_UNIT\]|\[RMMZ_FONT_LARGER\]|\[RMMZ_FONT_SMALLER\]|\[RMMZ_BACKSLASH\]|\[RMMZ_SHOW_GOLD_WINDOW\]|\[RMMZ_WAIT_SHORT\]|\[RMMZ_WAIT_LONG\]|\[RMMZ_WAIT_INPUT\]|\[RMMZ_INSTANT_TEXT_ON\]|\[RMMZ_INSTANT_TEXT_OFF\]|\[RMMZ_NO_WAIT\]|\[CUSTOM_[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_\d+\]",
    )
    .map_err(|error| AttMzError::InvalidConfig(format!("占位符识别正则不可用: {error}")))
}

fn build_unprotected_control_warnings(
    rules: &[PlaceholderRule],
    sample_texts: &[String],
) -> Vec<AgentIssue> {
    let mut suspicious_candidates = Vec::new();
    for sample_text in sample_texts {
        let Ok(protected_spans) = select_control_spans(rules, sample_text) else {
            continue;
        };
        let candidates = raw_control_sequence_candidates(sample_text);
        for candidate in candidates {
            if protected_spans
                .iter()
                .any(|span| candidate.start < span.end && span.start < candidate.end)
            {
                continue;
            }
            if !is_suspicious_unprotected_control(&candidate.original) {
                continue;
            }
            if suspicious_candidates.contains(&candidate.original) {
                continue;
            }
            suspicious_candidates.push(candidate.original);
            if suspicious_candidates.len() >= 5 {
                break;
            }
        }
        if suspicious_candidates.len() >= 5 {
            break;
        }
    }
    if suspicious_candidates.is_empty() {
        return Vec::new();
    }
    let formatted = suspicious_candidates
        .iter()
        .map(|candidate| format!("{candidate} ({})", format_code_points(candidate)))
        .collect::<Vec<_>>()
        .join("；");
    vec![issue(
        "unprotected_control_unicode_boundary",
        format!(
            "发现疑似非 ASCII 括号或未闭合控制片段，请核验 Unicode code point 后使用精确规则，禁止猜成 ASCII ]：{formatted}"
        ),
    )]
}

fn raw_control_sequence_candidates(text: &str) -> Vec<RawCandidate> {
    let Ok(pattern) = Regex::new(
        r"\\[A-Za-z]+\d*\[[A-Za-z0-9_./:-]{1,32}[^\]\w\s\[\]\\]|\\[A-Za-z]+\d*(?:\[[^\]\r\n]{0,64}\])?|\\[{}\$\.\|!><\^]",
    ) else {
        return Vec::new();
    };
    pattern
        .find_iter(text)
        .map(|matched| RawCandidate {
            start: matched.start(),
            end: matched.end(),
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

fn format_placeholder_template(template: &str) -> Result<String> {
    format_placeholder_template_with_index(template, 1)
}

fn format_placeholder_template_with_index(template: &str, index: usize) -> Result<String> {
    let mut output = String::new();
    let mut chars = template.chars().peekable();
    while let Some(char_value) = chars.next() {
        if char_value == '{' {
            let mut name = String::new();
            loop {
                let Some(next_char) = chars.next() else {
                    return Err(AttMzError::InvalidConfig(format!(
                        "占位符模板格式无效，仅支持 code、param、index 变量: {template}"
                    )));
                };
                if next_char == '}' {
                    break;
                }
                name.push(next_char);
            }
            match name.as_str() {
                "code" | "param" => {}
                "index" => output.push_str(&index.to_string()),
                _ => {
                    return Err(AttMzError::InvalidConfig(format!(
                        "占位符模板格式无效，仅支持 code、param、index 变量: {template}"
                    )));
                }
            }
            continue;
        }
        if char_value == '}' {
            return Err(AttMzError::InvalidConfig(format!(
                "占位符模板格式无效，仅支持 code、param、index 变量: {template}"
            )));
        }
        output.push(char_value);
    }
    Ok(output)
}

fn replace_case_insensitive(text: &str, pattern: &str, replacement: &str) -> Result<String> {
    let regex = Regex::new(&format!("(?i){}", regex::escape(pattern))).map_err(|error| {
        AttMzError::InvalidConfig(format!("占位符还原正则不可用: {pattern}: {error}"))
    })?;
    Ok(regex.replace_all(text, replacement).to_string())
}

#[derive(Debug, Clone)]
struct ControlSpan {
    start: usize,
    end: usize,
    original: String,
    priority: i32,
    kind: SpanKind,
}

#[derive(Debug, Clone)]
enum SpanKind {
    Standard(String),
    Custom(String),
}

const LITERAL_LINE_BREAK_PLACEHOLDER: &str = "[RMMZ_LITERAL_LINE_BREAK]";
const REAL_LINE_BREAK_PLACEHOLDER: &str = "[RMMZ_REAL_LINE_BREAK]";

struct RawCandidate {
    start: usize,
    end: usize,
    original: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn placeholder_rules_parse_valid_object() {
        let rules = parse_custom_placeholder_rules_text(
            r#"{"\\\\F\\[[^\\]]+\\]":"[CUSTOM_FACE_{index}]"}"#,
        )
        .expect("规则应解析成功");

        assert_eq!(rules.len(), 1);
        assert_eq!(rules[0].placeholder_template, "[CUSTOM_FACE_{index}]");
    }

    #[test]
    fn placeholder_rules_reject_empty_matching_regex() {
        let error = parse_custom_placeholder_rules_text(r#"{".*":"[CUSTOM_ANY_{index}]"}"#)
            .expect_err("空匹配规则应失败");

        assert!(error.to_string().contains("不能匹配空字符串"));
    }

    #[test]
    fn validate_placeholder_rules_previews_roundtrip() {
        let rules = parse_custom_placeholder_rules_text(
            r#"{"\\\\F\\[[^\\]]+\\]":"[CUSTOM_FACE_PORTRAIT_{index}]"}"#,
        )
        .expect("规则应解析成功");
        let samples = vec![r"\F[GuideA]こんにちは\V[1]".to_string()];

        let report = validate_placeholder_rules_report(&rules, &samples, "--placeholder-rules");

        assert_eq!(report.status, "ok");
        assert_eq!(report.summary.get("rule_count"), Some(&json!(1)));
        let samples = report
            .details
            .get("samples")
            .and_then(Value::as_array)
            .expect("报告应包含样本预览");
        assert_eq!(
            samples[0]["text_for_model"],
            "[CUSTOM_FACE_PORTRAIT_1]こんにちは[RMMZ_VARIABLE_1]"
        );
        assert_eq!(samples[0]["restored_text"], r"\F[GuideA]こんにちは\V[1]");
        assert_eq!(samples[0]["roundtrip_ok"], true);
    }

    #[test]
    fn validate_placeholder_rules_blocks_common_escape_match() {
        let rules =
            parse_custom_placeholder_rules_text(r#"{"(?i)\\\\N\\d*":"[CUSTOM_PLUGIN_N_{index}]"}"#)
                .expect("规则语法本身应解析成功");
        let samples = vec![r"\n".to_string()];

        let report = validate_placeholder_rules_report(&rules, &samples, "--placeholder-rules");

        assert_eq!(report.status, "error");
        assert_eq!(
            report.errors[0].code,
            "placeholder_rule_matches_common_escape"
        );
    }

    #[test]
    fn validate_placeholder_rules_warns_unicode_control_boundary() {
        let rules = parse_custom_placeholder_rules_text("{}").expect("空规则应合法");
        let samples = vec![r"\F3[66」「ふーん……？」".to_string()];

        let report = validate_placeholder_rules_report(&rules, &samples, "空规则");

        let warning_codes: Vec<&str> = report
            .warnings
            .iter()
            .map(|warning| warning.code.as_str())
            .collect();
        assert!(warning_codes.contains(&"unprotected_control_unicode_boundary"));
    }
}
