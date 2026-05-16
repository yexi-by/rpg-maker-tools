use fancy_regex::Regex as FancyRegex;
use rayon::prelude::*;
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use std::collections::{HashMap, HashSet};
use std::env;
use std::sync::{Arc, LazyLock};

static INDEXED_STANDARD_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\\(?P<code>V|N|P|C|I|PX|PY|FS)\[(?P<param>\d+)\]").unwrap());
static SYMBOL_STANDARD_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\\(?P<symbol>[{}\\$.\|!><^])").unwrap());
static TERMS_PERCENT_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"%(?P<param>\d+)").unwrap());
static LITERAL_DYNAMIC_UNICODE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\\(?:u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8})").unwrap());
static LITERAL_DYNAMIC_HEX_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\\x[0-9A-Fa-f]{2}").unwrap());
static LITERAL_DYNAMIC_OCTAL_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\\[0-7]{1,3}").unwrap());
static RAW_CONTROL_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"\\[A-Za-z]+\d*\[[A-Za-z0-9_./:-]{1,32}[^\]\w\s\[\]\\]|\\[A-Za-z]+\d*(?:\[[^\]\r\n]{0,64}\])?|\\[{}\\$.\|!><^]",
    )
    .unwrap()
});
static PLACEHOLDER_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?i)\[RMMZ_(?:(?:VARIABLE|ACTOR_NAME|PARTY_MEMBER_NAME|TEXT_COLOR|ICON|TEXT_X_POSITION|TEXT_Y_POSITION|FONT_SIZE|MESSAGE_ARGUMENT)_\d+|LITERAL_(?:UNICODE|HEX|OCTAL)_ESCAPE_[0-9A-F]+)\]|\[RMMZ_REAL_LINE_BREAK\]|\[RMMZ_LITERAL_(?:DOUBLE_QUOTE|SINGLE_QUOTE|SLASH|QUESTION_MARK|BELL|BACKSPACE|FORM_FEED|LINE_BREAK|CARRIAGE_RETURN|TAB|VERTICAL_TAB)\]|\[RMMZ_(?:CURRENCY_UNIT|FONT_LARGER|FONT_SMALLER|BACKSLASH|SHOW_GOLD_WINDOW|WAIT_SHORT|WAIT_LONG|WAIT_INPUT|INSTANT_TEXT_ON|INSTANT_TEXT_OFF|NO_WAIT)\]|\[CUSTOM_[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_\d+\]",
    )
    .unwrap()
});
static DOUBLED_CONTROL_LITERAL_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\\\\(?:[A-Za-z]+\d*(?:\[[^\]\r\n]{0,64}\])?|[{}\\$.\|!><^]|[nrt])").unwrap()
});
static NOTE_TAG_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?s)<(?P<tag>[^<>:\r\n]+)(?::(?P<value>[^<>]*))?>").unwrap());
static MAP_FILE_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^Map\d+\.json$").unwrap());

const REAL_LINE_BREAK_MARKER: &str = "\n";
const REAL_LINE_BREAK_PLACEHOLDER: &str = "[RMMZ_REAL_LINE_BREAK]";
const LITERAL_LINE_BREAK_MARKER: &str = r"\n";
const LITERAL_LINE_BREAK_PLACEHOLDER: &str = "[RMMZ_LITERAL_LINE_BREAK]";

#[derive(Debug, Deserialize)]
struct QualityPayload {
    items: Vec<NativeTranslationItem>,
    text_rules: NativeTextRules,
    japanese_residual_rules: Vec<NativeJapaneseResidualRule>,
}

#[derive(Debug, Deserialize)]
struct ProtocolPayload {
    entries: Vec<ProtocolEntry>,
}

#[derive(Debug, Deserialize)]
struct NoteSourcesPayload {
    data: HashMap<String, Value>,
    file_pattern: Option<String>,
}

#[derive(Debug, Serialize)]
struct NoteTagSourceOutput {
    file_name: String,
    owner_path: Vec<String>,
    note_text: String,
    location_prefix: String,
}

#[derive(Debug, Deserialize)]
struct FontReplacementPayload {
    data: HashMap<String, Value>,
    plugins: Vec<Value>,
    old_font_names: Vec<String>,
    replacement_font_name: String,
}

#[derive(Debug, Serialize)]
struct FontReplacementOutput {
    data_changes: Vec<FontReplacementChange>,
    plugin_changes: Vec<FontReplacementChange>,
    replaced_count: usize,
}

#[derive(Debug, Clone, Serialize)]
struct FontReplacementChange {
    file_name: String,
    value_path: String,
    original_text: String,
    replaced_text: String,
    count: usize,
}

#[derive(Debug, Deserialize)]
struct ProtocolEntry {
    item: NativeTranslationItem,
    mode: String,
    current_value: Option<Value>,
    path_parts: Vec<String>,
    note_text: Option<String>,
    tag_name: Option<String>,
}

#[derive(Debug, Deserialize)]
struct NativeTranslationItem {
    location_path: String,
    item_type: String,
    role: Option<String>,
    original_lines: Vec<String>,
    translation_lines: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct NativeTextRules {
    custom_placeholder_rules: Vec<NativeCustomPlaceholderRule>,
    allowed_japanese_chars: Vec<String>,
    allowed_japanese_tail_chars: Vec<String>,
    japanese_segment_pattern: String,
    line_width_count_pattern: String,
    residual_escape_sequence_pattern: String,
    long_text_line_width_limit: usize,
}

#[derive(Debug, Deserialize)]
struct NativeCustomPlaceholderRule {
    pattern_text: String,
    placeholder_template: String,
}

#[derive(Debug, Deserialize)]
struct NativeJapaneseResidualRule {
    location_path: String,
    allowed_terms: Vec<String>,
    reason: String,
}

#[derive(Debug, Serialize)]
struct QualityScanOutput {
    japanese_residual_items: Vec<Value>,
    text_structure_items: Vec<Value>,
    placeholder_risk_items: Vec<Value>,
    overwide_line_items: Vec<Value>,
}

#[derive(Debug, Clone)]
struct CompiledRules {
    custom_placeholder_rules: Vec<CompiledCustomRule>,
    allowed_japanese_chars: HashSet<char>,
    allowed_japanese_tail_chars: HashSet<char>,
    japanese_segment_re: Regex,
    line_width_count_re: Regex,
    residual_escape_sequence_re: Regex,
    long_text_line_width_limit: usize,
}

#[derive(Debug, Clone)]
struct CompiledCustomRule {
    pattern: FancyRegex,
    placeholder_template: String,
}

#[derive(Debug, Clone)]
struct ControlSpan {
    start: usize,
    end: usize,
    original: String,
    placeholder: Option<String>,
    custom_template: Option<String>,
    source: SpanSource,
    priority: i32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum SpanSource {
    Standard,
    Custom,
}

#[derive(Debug)]
struct PlaceholderBuild {
    original_lines_with_placeholders: Vec<String>,
    placeholder_map: HashMap<String, String>,
    placeholder_counts: HashMap<String, usize>,
}

pub fn scan_quality_impl(payload_json: &str) -> Result<String, String> {
    let payload: QualityPayload = serde_json::from_str(payload_json)
        .map_err(|error| format!("Rust 质检输入 JSON 解析失败: {error}"))?;
    let rules = Arc::new(compile_rules(payload.text_rules)?);
    let residual_rules = Arc::new(index_residual_rules(payload.japanese_residual_rules));
    let items = Arc::new(payload.items);

    let output = run_with_optional_pool(|| {
        let japanese_residual_items = collect_sorted_details(
            items
                .par_iter()
                .filter_map(|item| collect_residual_detail(item, &rules, &residual_rules))
                .collect(),
        );
        let text_structure_items = collect_sorted_details(
            items
                .par_iter()
                .filter_map(|item| collect_text_structure_detail(item, &rules))
                .collect(),
        );
        let placeholder_risk_items = collect_sorted_details(
            items
                .par_iter()
                .filter_map(|item| collect_placeholder_detail(item, &rules))
                .collect(),
        );
        let overwide_line_items = collect_sorted_details(
            items
                .par_iter()
                .flat_map(|item| collect_overwide_details(item, &rules))
                .collect(),
        );

        QualityScanOutput {
            japanese_residual_items,
            text_structure_items,
            placeholder_risk_items,
            overwide_line_items,
        }
    });

    serde_json::to_string(&output)
        .map_err(|error| format!("Rust 质检输出 JSON 序列化失败: {error}"))
}

pub fn scan_write_protocol_impl(payload_json: &str) -> Result<String, String> {
    let payload: ProtocolPayload = serde_json::from_str(payload_json)
        .map_err(|error| format!("Rust 写入协议输入 JSON 解析失败: {error}"))?;
    let details = run_with_optional_pool(|| {
        collect_sorted_details(
            payload
                .entries
                .par_iter()
                .filter_map(|entry| match probe_protocol_entry(entry) {
                    Ok(()) => None,
                    Err(reason) => {
                        let mut detail = base_detail(&entry.item);
                        detail.insert("reason".to_string(), json!(reason));
                        Some(Value::Object(detail))
                    }
                })
                .collect(),
        )
    });
    serde_json::to_string(&details)
        .map_err(|error| format!("Rust 写入协议输出 JSON 序列化失败: {error}"))
}

pub fn collect_note_tag_sources_impl(payload_json: &str) -> Result<String, String> {
    let payload: NoteSourcesPayload = serde_json::from_str(payload_json)
        .map_err(|error| format!("Rust Note 标签输入 JSON 解析失败: {error}"))?;
    let file_pattern = payload.file_pattern.as_deref();
    let mut file_names: Vec<String> = payload
        .data
        .iter()
        .filter_map(|(file_name, value)| {
            if file_name == "plugins.js" || !file_name.ends_with(".json") || value.is_string() {
                return None;
            }
            if let Some(pattern) = file_pattern
                && !file_pattern_matches(file_name, pattern)
            {
                return None;
            }
            Some(file_name.clone())
        })
        .collect();
    file_names.sort();

    let mut sources = Vec::new();
    for file_name in file_names {
        if let Some(value) = payload.data.get(&file_name) {
            collect_note_tag_sources_in_value(&file_name, value, &mut Vec::new(), &mut sources);
        }
    }
    serde_json::to_string(&sources)
        .map_err(|error| format!("Rust Note 标签输出 JSON 序列化失败: {error}"))
}

pub fn scan_font_replacements_impl(payload_json: &str) -> Result<String, String> {
    let payload: FontReplacementPayload = serde_json::from_str(payload_json)
        .map_err(|error| format!("Rust 字体替换输入 JSON 解析失败: {error}"))?;
    let output = run_with_optional_pool(|| {
        let mut data_changes: Vec<FontReplacementChange> = payload
            .data
            .par_iter()
            .filter(|(file_name, value)| file_name.as_str() != "plugins.js" && !value.is_string())
            .flat_map(|(file_name, value)| {
                let mut changes = Vec::new();
                collect_font_replacements_in_value(
                    file_name,
                    value,
                    "",
                    &payload.old_font_names,
                    &payload.replacement_font_name,
                    &mut changes,
                );
                changes
            })
            .collect();
        data_changes.sort_by(|left, right| {
            (left.file_name.as_str(), left.value_path.as_str())
                .cmp(&(right.file_name.as_str(), right.value_path.as_str()))
        });

        let mut plugin_changes: Vec<FontReplacementChange> = payload
            .plugins
            .par_iter()
            .enumerate()
            .flat_map(|(index, value)| {
                let mut changes = Vec::new();
                collect_font_replacements_in_value(
                    "plugins.js",
                    value,
                    &format!("/{index}"),
                    &payload.old_font_names,
                    &payload.replacement_font_name,
                    &mut changes,
                );
                changes
            })
            .collect();
        plugin_changes.sort_by(|left, right| left.value_path.cmp(&right.value_path));

        let replaced_count = data_changes
            .iter()
            .chain(plugin_changes.iter())
            .map(|change| change.count)
            .sum();
        FontReplacementOutput {
            data_changes,
            plugin_changes,
            replaced_count,
        }
    });
    serde_json::to_string(&output)
        .map_err(|error| format!("Rust 字体替换输出 JSON 序列化失败: {error}"))
}

fn run_with_optional_pool<F, R>(job: F) -> R
where
    F: FnOnce() -> R + Send,
    R: Send,
{
    if let Some(thread_count) = read_configured_thread_count() {
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(thread_count)
            .build()
            .expect("Rust 线程池创建失败");
        return pool.install(job);
    }
    job()
}

pub fn read_configured_thread_count() -> Option<usize> {
    let raw_value = env::var("ATT_MZ_RUST_THREADS").ok()?;
    let parsed = raw_value.trim().parse::<usize>().ok()?;
    if parsed == 0 {
        return None;
    }
    Some(parsed)
}

fn collect_note_tag_sources_in_value(
    file_name: &str,
    value: &Value,
    owner_path: &mut Vec<String>,
    sources: &mut Vec<NoteTagSourceOutput>,
) {
    if let Some(object) = value.as_object() {
        if let Some(note_value) = object.get("note").and_then(Value::as_str)
            && !note_value.is_empty()
        {
            sources.push(NoteTagSourceOutput {
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

fn format_location_prefix(file_name: &str, owner_path: &[String]) -> String {
    if owner_path.is_empty() {
        return file_name.to_string();
    }
    format!("{}/{}", file_name, owner_path.join("/"))
}

fn file_pattern_matches(file_name: &str, file_pattern: &str) -> bool {
    if file_pattern == "Map*.json" {
        return MAP_FILE_RE.is_match(file_name);
    }
    wildcard_match(file_name.as_bytes(), file_pattern.as_bytes())
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

fn collect_font_replacements_in_value(
    file_name: &str,
    value: &Value,
    value_path: &str,
    old_font_names: &[String],
    replacement_font_name: &str,
    changes: &mut Vec<FontReplacementChange>,
) {
    if let Some(text) = value.as_str() {
        if let Some((replaced_text, count)) =
            replace_font_names_in_text(text, old_font_names, replacement_font_name)
        {
            changes.push(FontReplacementChange {
                file_name: file_name.to_string(),
                value_path: value_path.to_string(),
                original_text: text.to_string(),
                replaced_text,
                count,
            });
        }
        return;
    }

    if let Some(array) = value.as_array() {
        for (index, child) in array.iter().enumerate() {
            let child_path = append_json_pointer_part(value_path, &index.to_string());
            collect_font_replacements_in_value(
                file_name,
                child,
                &child_path,
                old_font_names,
                replacement_font_name,
                changes,
            );
        }
        return;
    }

    if let Some(object) = value.as_object() {
        for (key, child) in object {
            let child_path = append_json_pointer_part(value_path, key);
            collect_font_replacements_in_value(
                file_name,
                child,
                &child_path,
                old_font_names,
                replacement_font_name,
                changes,
            );
        }
    }
}

fn replace_font_names_in_text(
    text: &str,
    old_font_names: &[String],
    replacement_font_name: &str,
) -> Option<(String, usize)> {
    if !old_font_names
        .iter()
        .any(|old_font_name| text.contains(old_font_name))
    {
        return None;
    }
    if let Some(replaced_text) =
        replace_complete_font_reference_text(text, old_font_names, replacement_font_name)
    {
        return Some((replaced_text, 1));
    }
    replace_font_references_in_encoded_json_text(text, old_font_names, replacement_font_name)
}

fn replace_complete_font_reference_text(
    text: &str,
    old_font_names: &[String],
    replacement_font_name: &str,
) -> Option<String> {
    let stripped_text = text.trim();
    if stripped_text.is_empty() {
        return None;
    }

    let leading_len = text.len() - text.trim_start().len();
    let trailing_start = text.trim_end().len();
    let leading_text = &text[..leading_len];
    let trailing_text = &text[trailing_start..];
    for old_font_name in old_font_names {
        if stripped_text == old_font_name {
            return Some(format!(
                "{leading_text}{replacement_font_name}{trailing_text}"
            ));
        }
        let slash_index = stripped_text.rfind('/');
        let backslash_index = stripped_text.rfind('\\');
        let separator_index = match (slash_index, backslash_index) {
            (Some(left), Some(right)) => Some(left.max(right)),
            (Some(left), None) => Some(left),
            (None, Some(right)) => Some(right),
            (None, None) => None,
        };
        if let Some(index) = separator_index {
            let reference_name = &stripped_text[index + 1..];
            if reference_name == old_font_name {
                return Some(format!(
                    "{}{}{}{}",
                    leading_text,
                    &stripped_text[..index + 1],
                    replacement_font_name,
                    trailing_text
                ));
            }
        }
    }
    None
}

fn replace_font_references_in_encoded_json_text(
    text: &str,
    old_font_names: &[String],
    replacement_font_name: &str,
) -> Option<(String, usize)> {
    let parsed_value = serde_json::from_str::<Value>(text).ok()?;
    if !parsed_value.is_array() && !parsed_value.is_object() {
        return None;
    }
    let (replaced_value, count) =
        replace_font_names_in_json_value(parsed_value, old_font_names, replacement_font_name);
    if count == 0 {
        return None;
    }
    Some((serialize_python_style_json(&replaced_value), count))
}

fn replace_font_names_in_json_value(
    value: Value,
    old_font_names: &[String],
    replacement_font_name: &str,
) -> (Value, usize) {
    match value {
        Value::String(text) => {
            if let Some((replaced_text, count)) =
                replace_font_names_in_text(&text, old_font_names, replacement_font_name)
            {
                (Value::String(replaced_text), count)
            } else {
                (Value::String(text), 0)
            }
        }
        Value::Array(items) => {
            let mut replaced_items = Vec::with_capacity(items.len());
            let mut replaced_count = 0usize;
            for item in items {
                let (replaced_item, count) =
                    replace_font_names_in_json_value(item, old_font_names, replacement_font_name);
                replaced_items.push(replaced_item);
                replaced_count += count;
            }
            (Value::Array(replaced_items), replaced_count)
        }
        Value::Object(object) => {
            let mut replaced_object = Map::new();
            let mut replaced_count = 0usize;
            for (key, item) in object {
                let (replaced_item, count) =
                    replace_font_names_in_json_value(item, old_font_names, replacement_font_name);
                replaced_object.insert(key, replaced_item);
                replaced_count += count;
            }
            (Value::Object(replaced_object), replaced_count)
        }
        other => (other, 0),
    }
}

fn serialize_python_style_json(value: &Value) -> String {
    match value {
        Value::Array(items) => {
            let serialized_items: Vec<String> =
                items.iter().map(serialize_python_style_json).collect();
            format!("[{}]", serialized_items.join(", "))
        }
        Value::Object(object) => {
            let serialized_items: Vec<String> = object
                .iter()
                .map(|(key, item)| {
                    format!(
                        "{}: {}",
                        serde_json::to_string(key).unwrap_or_else(|_| "\"\"".to_string()),
                        serialize_python_style_json(item)
                    )
                })
                .collect();
            format!("{{{}}}", serialized_items.join(", "))
        }
        _ => serde_json::to_string(value).unwrap_or_else(|_| "null".to_string()),
    }
}

fn append_json_pointer_part(value_path: &str, part: &str) -> String {
    format!("{value_path}/{}", escape_json_pointer_part(part))
}

fn escape_json_pointer_part(part: &str) -> String {
    part.replace('~', "~0").replace('/', "~1")
}

fn compile_rules(rules: NativeTextRules) -> Result<CompiledRules, String> {
    let mut custom_placeholder_rules = Vec::new();
    for rule in rules.custom_placeholder_rules {
        let pattern = FancyRegex::new(&rule.pattern_text).map_err(|error| {
            format!(
                "Rust 自定义游戏控制符正则无效: {}: {error}",
                rule.pattern_text
            )
        })?;
        custom_placeholder_rules.push(CompiledCustomRule {
            pattern,
            placeholder_template: rule.placeholder_template,
        });
    }

    Ok(CompiledRules {
        custom_placeholder_rules,
        allowed_japanese_chars: collect_chars(rules.allowed_japanese_chars),
        allowed_japanese_tail_chars: collect_chars(rules.allowed_japanese_tail_chars),
        japanese_segment_re: Regex::new(&rules.japanese_segment_pattern)
            .map_err(|error| format!("Rust 日文残留正则无效: {error}"))?,
        line_width_count_re: Regex::new(&rules.line_width_count_pattern)
            .map_err(|error| format!("Rust 行宽正则无效: {error}"))?,
        residual_escape_sequence_re: Regex::new(&rules.residual_escape_sequence_pattern)
            .map_err(|error| format!("Rust 残留转义正则无效: {error}"))?,
        long_text_line_width_limit: rules.long_text_line_width_limit,
    })
}

fn collect_chars(values: Vec<String>) -> HashSet<char> {
    values
        .into_iter()
        .flat_map(|value| value.chars().collect::<Vec<char>>())
        .collect()
}

fn index_residual_rules(
    records: Vec<NativeJapaneseResidualRule>,
) -> HashMap<String, NativeJapaneseResidualRule> {
    records
        .into_iter()
        .map(|record| (record.location_path.clone(), record))
        .collect()
}

fn collect_sorted_details(mut details: Vec<Value>) -> Vec<Value> {
    details.sort_by_key(detail_sort_key);
    details
}

fn detail_sort_key(value: &Value) -> (String, i64) {
    let location_path = value
        .get("location_path")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let line_index = value
        .get("line_index")
        .and_then(Value::as_i64)
        .unwrap_or(-1);
    (location_path, line_index)
}

fn collect_residual_detail(
    item: &NativeTranslationItem,
    rules: &CompiledRules,
    residual_rules: &HashMap<String, NativeJapaneseResidualRule>,
) -> Option<Value> {
    let allowed_terms = residual_rules
        .get(&item.location_path)
        .map(|rule| rule.allowed_terms.as_slice())
        .unwrap_or(&[]);
    let checked_lines = mask_allowed_terms(&item.translation_lines, allowed_terms);
    match check_japanese_residual(&checked_lines, rules) {
        Ok(()) => None,
        Err(reason) => {
            let mut detail = base_detail(item);
            detail.insert("reason".to_string(), json!(reason));
            if let Some(rule) = residual_rules.get(&item.location_path)
                && !rule.allowed_terms.is_empty()
            {
                detail.insert("allowed_terms".to_string(), json!(rule.allowed_terms));
                detail.insert("exception_reason".to_string(), json!(rule.reason));
            }
            Some(Value::Object(detail))
        }
    }
}

fn mask_allowed_terms(lines: &[String], allowed_terms: &[String]) -> Vec<String> {
    if allowed_terms.is_empty() {
        return lines.to_vec();
    }
    let mut sorted_terms = allowed_terms.to_vec();
    sorted_terms.sort_by_key(|term| usize::MAX - term.chars().count());
    lines
        .iter()
        .map(|line| {
            let mut masked = line.clone();
            for term in &sorted_terms {
                masked = masked.replace(term, " ");
            }
            masked
        })
        .collect()
}

fn check_japanese_residual(lines: &[String], rules: &CompiledRules) -> Result<(), String> {
    for (index, line) in lines.iter().enumerate() {
        let cleaned_line = strip_non_content_for_residual(line, rules);
        let segments: Vec<String> = rules
            .japanese_segment_re
            .find_iter(&cleaned_line)
            .map(|matched| matched.as_str().to_string())
            .collect();
        if segments.is_empty() {
            continue;
        }

        let has_non_japanese_content = has_non_japanese_content(&cleaned_line, rules);
        let mut real_residual = Vec::new();
        for segment in segments {
            let filtered: Vec<char> = segment
                .chars()
                .filter(|char_value| !rules.allowed_japanese_chars.contains(char_value))
                .collect();
            if filtered.is_empty() {
                if !has_non_japanese_content {
                    real_residual.extend(segment.chars());
                }
                continue;
            }
            if has_non_japanese_content
                && filtered
                    .iter()
                    .all(|char_value| rules.allowed_japanese_tail_chars.contains(char_value))
            {
                continue;
            }
            real_residual.extend(filtered);
        }

        if !real_residual.is_empty() {
            return Err(format!(
                "发现日文残留(第 {} 行): {:?}",
                index + 1,
                real_residual
            ));
        }
    }
    Ok(())
}

fn strip_non_content_for_residual(text: &str, rules: &CompiledRules) -> String {
    let stripped_controls = replace_control_sequences(text, rules, |_| String::new());
    let stripped_placeholders = PLACEHOLDER_RE.replace_all(&stripped_controls, "");
    rules
        .residual_escape_sequence_re
        .replace_all(&stripped_placeholders, " ")
        .to_string()
}

fn has_non_japanese_content(text: &str, rules: &CompiledRules) -> bool {
    let text_without_japanese = rules.japanese_segment_re.replace_all(text, "");
    text_without_japanese.chars().any(char::is_alphanumeric)
}

fn collect_text_structure_detail(
    item: &NativeTranslationItem,
    rules: &CompiledRules,
) -> Option<Value> {
    match build_placeholders(item, rules).and_then(|placeholder_build| {
        let translation_lines_with_placeholders =
            mask_translation_controls(item, rules, &placeholder_build.placeholder_map);
        collect_text_structure_errors(
            item,
            &item.translation_lines,
            &translation_lines_with_placeholders,
            &placeholder_build.original_lines_with_placeholders,
        )
    }) {
        Ok(errors) if errors.is_empty() => None,
        Ok(errors) => {
            let mut detail = base_detail(item);
            detail.insert("reason".to_string(), json!(errors.join(";\n")));
            Some(Value::Object(detail))
        }
        Err(reason) => {
            let mut detail = base_detail(item);
            detail.insert("reason".to_string(), json!(reason));
            Some(Value::Object(detail))
        }
    }
}

fn collect_placeholder_detail(
    item: &NativeTranslationItem,
    rules: &CompiledRules,
) -> Option<Value> {
    let leaked_tokens = collect_placeholder_tokens(&item.translation_lines);
    if !leaked_tokens.is_empty() {
        let mut sorted_tokens: Vec<String> = leaked_tokens.into_iter().collect();
        sorted_tokens.sort();
        let mut detail = base_detail(item);
        detail.insert(
            "reason".to_string(),
            json!(format!(
                "译文残留项目内部占位符，不能写进游戏文件: {}",
                sorted_tokens.join("、")
            )),
        );
        return Some(Value::Object(detail));
    }

    match build_placeholders(item, rules).and_then(|placeholder_build| {
        let translation_lines_with_placeholders =
            mask_translation_controls(item, rules, &placeholder_build.placeholder_map);
        verify_placeholders(
            item,
            rules,
            &placeholder_build,
            &translation_lines_with_placeholders,
        )
    }) {
        Ok(()) => None,
        Err(reason) => {
            let mut detail = base_detail(item);
            detail.insert("reason".to_string(), json!(reason));
            Some(Value::Object(detail))
        }
    }
}

fn collect_overwide_details(item: &NativeTranslationItem, rules: &CompiledRules) -> Vec<Value> {
    let original_text_width_limit = original_short_text_width_limit(item, rules);
    let mut details = Vec::new();
    for (line_index, line, original_line) in iter_line_width_check_lines(item) {
        if line.is_empty() {
            continue;
        }
        let mut effective_limit = rules.long_text_line_width_limit;
        let mut original_width = None;
        if let Some(original_line_text) = original_line {
            let width = count_line_width_chars(&original_line_text, rules);
            effective_limit = effective_limit.max(width);
            original_width = Some(width);
        }
        if let Some(width_limit) = original_text_width_limit {
            effective_limit = effective_limit.max(width_limit);
        }
        let line_width = count_line_width_chars(&line, rules);
        if line_width <= effective_limit {
            continue;
        }
        let mut detail = base_detail(item);
        detail.insert("line_index".to_string(), json!(line_index));
        detail.insert("line".to_string(), json!(line));
        detail.insert("line_width".to_string(), json!(line_width));
        detail.insert("line_width_limit".to_string(), json!(effective_limit));
        if let Some(width) = original_width {
            detail.insert("original_line_width".to_string(), json!(width));
            detail.insert(
                "configured_line_width_limit".to_string(),
                json!(rules.long_text_line_width_limit),
            );
        }
        if let Some(width_limit) = original_text_width_limit {
            detail.insert("original_text_width_limit".to_string(), json!(width_limit));
        }
        details.push(Value::Object(detail));
    }
    details
}

fn collect_text_structure_errors(
    item: &NativeTranslationItem,
    translation_lines: &[String],
    translation_lines_with_placeholders: &[String],
    original_lines_with_placeholders: &[String],
) -> Result<Vec<String>, String> {
    let mut errors = collect_artifact_errors(item, translation_lines);
    if item.item_type != "short_text" {
        return Ok(errors);
    }
    if translation_lines.len() != 1 {
        errors.push(format!(
            "单字段文本必须只提供 1 条中文译文行，当前提供 {} 条",
            translation_lines.len()
        ));
        return Ok(errors);
    }

    let original_real_break_count = count_real_line_breaks(original_lines_with_placeholders);
    let translation_real_break_count = count_real_line_breaks(translation_lines_with_placeholders);
    if original_real_break_count != translation_real_break_count {
        errors.push(format!(
            "译文真实换行数量不一致（原文 {} 个，译文 {} 个）",
            original_real_break_count, translation_real_break_count
        ));
    }

    let original_literal_break_count = count_literal_line_breaks(original_lines_with_placeholders);
    let translation_literal_break_count =
        count_literal_line_breaks(translation_lines_with_placeholders);
    if original_literal_break_count != translation_literal_break_count {
        errors.push(format!(
            "译文字面量换行标记数量不一致（原文 {} 个，译文 {} 个）",
            original_literal_break_count, translation_literal_break_count
        ));
    }
    Ok(errors)
}

fn collect_artifact_errors(
    item: &NativeTranslationItem,
    translation_lines: &[String],
) -> Vec<String> {
    let mut errors = Vec::new();
    let joined_text = translation_lines.join("\n");
    if !item.location_path.is_empty() && joined_text.contains(&item.location_path) {
        errors.push("译文包含文本在游戏里的内部位置，不能写进游戏文件".to_string());
    }

    for line in translation_lines {
        let stripped = line.trim();
        let lowered = stripped.to_lowercase();
        if stripped.starts_with("译文：")
            || stripped.starts_with("译文:")
            || stripped.starts_with("翻译：")
            || stripped.starts_with("翻译:")
        {
            errors.push("译文包含明显解释性前缀，不是可写入游戏的正文".to_string());
            break;
        }
        if stripped.contains("以下是翻译") {
            errors.push("译文包含明显解释性说明，不是可写入游戏的正文".to_string());
            break;
        }
        if lowered.starts_with("id:")
            || lowered.starts_with("id：")
            || lowered.starts_with("\"id\":")
            || lowered.starts_with("source_lines:")
            || lowered.starts_with("source_lines：")
            || lowered.starts_with("\"source_lines\":")
            || lowered.starts_with("translation_lines:")
            || lowered.starts_with("translation_lines：")
            || lowered.starts_with("\"translation_lines\":")
        {
            errors.push("译文包含模型输出协议字段，不是可写入游戏的正文".to_string());
            break;
        }
    }
    errors
}

fn count_real_line_breaks(lines: &[String]) -> usize {
    if lines.is_empty() {
        return 0;
    }
    lines.join("\n").matches('\n').count()
        + lines
            .iter()
            .map(|line| line.matches(REAL_LINE_BREAK_PLACEHOLDER).count())
            .sum::<usize>()
}

fn count_literal_line_breaks(lines: &[String]) -> usize {
    lines
        .iter()
        .map(|line| {
            line.matches(LITERAL_LINE_BREAK_MARKER).count()
                + line.matches(LITERAL_LINE_BREAK_PLACEHOLDER).count()
        })
        .sum()
}

fn original_short_text_width_limit(
    item: &NativeTranslationItem,
    rules: &CompiledRules,
) -> Option<usize> {
    if item.item_type != "short_text" || item.original_lines.is_empty() {
        return None;
    }
    let original_lines = split_display_line_breaks(&item.original_lines[0]);
    if original_lines.is_empty() {
        return None;
    }
    original_lines
        .iter()
        .map(|line| count_line_width_chars(line, rules))
        .max()
}

fn iter_line_width_check_lines(
    item: &NativeTranslationItem,
) -> Vec<(usize, String, Option<String>)> {
    if item.item_type == "long_text" {
        return item
            .translation_lines
            .iter()
            .enumerate()
            .map(|(index, line)| (index, line.clone(), None))
            .collect();
    }
    if item.item_type != "short_text" || item.translation_lines.is_empty() {
        return Vec::new();
    }
    let original_has_line_break = has_display_line_break(&item.original_lines);
    let translated_text = &item.translation_lines[0];
    if !has_display_line_break(std::slice::from_ref(translated_text)) && !original_has_line_break {
        return Vec::new();
    }
    let translated_lines = split_display_line_breaks(translated_text);
    let original_text = item
        .original_lines
        .first()
        .map(String::as_str)
        .unwrap_or("");
    let original_lines = split_display_line_breaks(original_text);
    translated_lines
        .into_iter()
        .enumerate()
        .map(|(index, line)| {
            let original_line = original_lines.get(index).cloned();
            (index, line, original_line)
        })
        .collect()
}

fn has_display_line_break(lines: &[String]) -> bool {
    lines
        .iter()
        .any(|line| line.contains('\n') || line.contains(LITERAL_LINE_BREAK_MARKER))
}

fn split_display_line_breaks(text: &str) -> Vec<String> {
    text.replace(LITERAL_LINE_BREAK_MARKER, "\n")
        .split('\n')
        .map(str::to_string)
        .collect()
}

fn count_line_width_chars(text: &str, rules: &CompiledRules) -> usize {
    let mut protected_spans: Vec<(usize, usize)> = PLACEHOLDER_RE
        .find_iter(text)
        .map(|matched| (matched.start(), matched.end()))
        .collect();
    protected_spans.extend(
        iter_control_sequence_spans(text, rules)
            .into_iter()
            .map(|span| (span.start, span.end)),
    );
    text.char_indices()
        .filter(|(byte_index, char_value)| {
            !protected_spans
                .iter()
                .any(|(start, end)| *start <= *byte_index && *byte_index < *end)
                && rules.line_width_count_re.is_match(&char_value.to_string())
        })
        .count()
}

fn build_placeholders(
    item: &NativeTranslationItem,
    rules: &CompiledRules,
) -> Result<PlaceholderBuild, String> {
    let mut original_lines_with_placeholders = Vec::new();
    let mut placeholder_map: HashMap<String, String> = HashMap::new();
    let mut placeholder_sources: HashMap<String, SpanSource> = HashMap::new();
    let mut placeholder_counts: HashMap<String, usize> = HashMap::new();
    let mut custom_placeholder_counter = 0usize;
    let mut custom_placeholder_map: HashMap<String, String> = HashMap::new();

    for line in &item.original_lines {
        let spans = iter_control_sequence_spans(line, rules);
        let mut output = String::new();
        let mut last_end = 0usize;
        for span in spans {
            output.push_str(&line[last_end..span.start]);
            let placeholder = if let Some(standard_placeholder) = &span.placeholder {
                standard_placeholder.clone()
            } else if let Some(custom_template) = &span.custom_template {
                if let Some(existing_placeholder) = custom_placeholder_map.get(&span.original) {
                    existing_placeholder.clone()
                } else {
                    custom_placeholder_counter += 1;
                    let formatted =
                        format_custom_placeholder(custom_template, custom_placeholder_counter);
                    custom_placeholder_map.insert(span.original.clone(), formatted.clone());
                    formatted
                }
            } else {
                return Err(format!("无法为控制符生成占位符: {}", span.original));
            };

            if let Some(existing_original) = placeholder_map.get(&placeholder) {
                let existing_source = placeholder_sources.get(&placeholder);
                if existing_original != &span.original
                    && (existing_source == Some(&SpanSource::Custom)
                        || span.source == SpanSource::Custom)
                {
                    return Err(format!(
                        "自定义占位符 {} 同时匹配了多个不同片段: {} / {}",
                        placeholder, existing_original, span.original
                    ));
                }
            } else {
                placeholder_map.insert(placeholder.clone(), span.original.clone());
                placeholder_sources.insert(placeholder.clone(), span.source.clone());
            }
            *placeholder_counts.entry(placeholder.clone()).or_insert(0) += 1;
            output.push_str(&placeholder);
            last_end = span.end;
        }
        output.push_str(&line[last_end..]);
        original_lines_with_placeholders.push(replace_real_line_breaks(
            &output,
            &mut placeholder_map,
            &mut placeholder_sources,
            &mut placeholder_counts,
        )?);
    }

    Ok(PlaceholderBuild {
        original_lines_with_placeholders,
        placeholder_map,
        placeholder_counts,
    })
}

fn replace_real_line_breaks(
    line: &str,
    placeholder_map: &mut HashMap<String, String>,
    placeholder_sources: &mut HashMap<String, SpanSource>,
    placeholder_counts: &mut HashMap<String, usize>,
) -> Result<String, String> {
    let real_break_count = line.matches(REAL_LINE_BREAK_MARKER).count();
    if real_break_count == 0 {
        return Ok(line.to_string());
    }
    if let Some(existing_original) = placeholder_map.get(REAL_LINE_BREAK_PLACEHOLDER) {
        if existing_original != REAL_LINE_BREAK_MARKER {
            return Err(format!(
                "占位符 {} 同时匹配了多个不同片段",
                REAL_LINE_BREAK_PLACEHOLDER
            ));
        }
    } else {
        placeholder_map.insert(
            REAL_LINE_BREAK_PLACEHOLDER.to_string(),
            REAL_LINE_BREAK_MARKER.to_string(),
        );
        placeholder_sources.insert(
            REAL_LINE_BREAK_PLACEHOLDER.to_string(),
            SpanSource::Standard,
        );
    }
    *placeholder_counts
        .entry(REAL_LINE_BREAK_PLACEHOLDER.to_string())
        .or_insert(0) += real_break_count;
    Ok(line.replace(REAL_LINE_BREAK_MARKER, REAL_LINE_BREAK_PLACEHOLDER))
}

fn verify_placeholders(
    item: &NativeTranslationItem,
    rules: &CompiledRules,
    placeholder_build: &PlaceholderBuild,
    translation_lines_with_placeholders: &[String],
) -> Result<(), String> {
    let mut errors = Vec::new();
    let original_placeholders =
        collect_placeholder_tokens(&placeholder_build.original_lines_with_placeholders);
    let translated_placeholders = collect_placeholder_tokens(translation_lines_with_placeholders);

    if original_placeholders.is_empty() && !translated_placeholders.is_empty() {
        let mut sorted: Vec<String> = translated_placeholders.into_iter().collect();
        sorted.sort();
        errors.push(format!(
            "原文不包含任何占位符，但译文新增了以下占位符: {}",
            sorted.join("、")
        ));
    }

    if !placeholder_build.placeholder_map.is_empty() {
        let combined_text = translation_lines_with_placeholders.join("").to_lowercase();
        for (placeholder, expected_count) in &placeholder_build.placeholder_counts {
            let actual_count = combined_text.matches(&placeholder.to_lowercase()).count();
            if placeholder.eq_ignore_ascii_case(LITERAL_LINE_BREAK_PLACEHOLDER) {
                if actual_count < *expected_count {
                    errors.push(format!(
                        "占位符 {} 数量不足 (至少需要: {}, 实际: {})",
                        placeholder, expected_count, actual_count
                    ));
                }
                continue;
            }
            if actual_count != *expected_count {
                errors.push(format!(
                    "占位符 {} 数量错误 (期望: {}, 实际: {})",
                    placeholder, expected_count, actual_count
                ));
            }
        }
    }

    let original_raw_controls = collect_unprotected_control_sequences(&item.original_lines, rules);
    let translated_raw_controls =
        collect_unprotected_control_sequences(translation_lines_with_placeholders, rules);
    if original_raw_controls != translated_raw_controls {
        errors.push(format!(
            "疑似控制符不一致，未被占位符规则覆盖的反斜杠控制片段必须原样保留 (原文: {}; 译文: {})",
            format_control_counts(&original_raw_controls),
            format_control_counts(&translated_raw_controls)
        ));
    }

    if errors.is_empty() {
        Ok(())
    } else {
        Err(errors.join(";\n"))
    }
}

fn collect_placeholder_tokens(lines: &[String]) -> HashSet<String> {
    let mut tokens = HashSet::new();
    for line in lines {
        for matched in PLACEHOLDER_RE.find_iter(line) {
            tokens.insert(matched.as_str().to_string());
        }
    }
    tokens
}

fn mask_translation_controls(
    item: &NativeTranslationItem,
    rules: &CompiledRules,
    placeholder_map: &HashMap<String, String>,
) -> Vec<String> {
    let reverse_map: HashMap<String, String> = placeholder_map
        .iter()
        .map(|(placeholder, original)| (original.clone(), placeholder.clone()))
        .collect();
    item.translation_lines
        .iter()
        .map(|line| {
            let mut masked = replace_control_sequences(line, rules, |span| {
                reverse_map
                    .get(&span.original)
                    .cloned()
                    .unwrap_or_else(|| "[CUSTOM_UNEXPECTED_1]".to_string())
            });
            if reverse_map
                .get(REAL_LINE_BREAK_MARKER)
                .is_some_and(|placeholder| placeholder == REAL_LINE_BREAK_PLACEHOLDER)
            {
                masked = masked.replace(REAL_LINE_BREAK_MARKER, REAL_LINE_BREAK_PLACEHOLDER);
            }
            masked
        })
        .collect()
}

fn replace_control_sequences<F>(text: &str, rules: &CompiledRules, mut replacer: F) -> String
where
    F: FnMut(&ControlSpan) -> String,
{
    let spans = iter_control_sequence_spans(text, rules);
    if spans.is_empty() {
        return text.to_string();
    }
    let mut output = String::new();
    let mut last_end = 0usize;
    for span in spans {
        output.push_str(&text[last_end..span.start]);
        output.push_str(&replacer(&span));
        last_end = span.end;
    }
    output.push_str(&text[last_end..]);
    output
}

fn iter_control_sequence_spans(text: &str, rules: &CompiledRules) -> Vec<ControlSpan> {
    let mut spans = Vec::new();
    spans.extend(iter_indexed_standard_spans(text));
    spans.extend(iter_no_param_standard_spans(text));
    spans.extend(iter_symbol_standard_spans(text));
    spans.extend(iter_terms_percent_spans(text));
    spans.extend(iter_literal_escape_spans(text));
    spans.extend(iter_custom_placeholder_spans(text, rules));
    select_non_overlapping_spans(spans)
}

fn iter_indexed_standard_spans(text: &str) -> Vec<ControlSpan> {
    INDEXED_STANDARD_RE
        .captures_iter(text)
        .filter_map(|captures| {
            let matched = captures.get(0)?;
            let code = captures.name("code")?.as_str().to_uppercase();
            let param = captures.name("param")?.as_str();
            let code_name = match code.as_str() {
                "V" => "VARIABLE",
                "N" => "ACTOR_NAME",
                "P" => "PARTY_MEMBER_NAME",
                "C" => "TEXT_COLOR",
                "I" => "ICON",
                "PX" => "TEXT_X_POSITION",
                "PY" => "TEXT_Y_POSITION",
                "FS" => "FONT_SIZE",
                _ => return None,
            };
            Some(ControlSpan {
                start: matched.start(),
                end: matched.end(),
                original: matched.as_str().to_string(),
                placeholder: Some(format!("[RMMZ_{}_{}]", code_name, param)),
                custom_template: None,
                source: SpanSource::Standard,
                priority: 0,
            })
        })
        .collect()
}

fn iter_no_param_standard_spans(text: &str) -> Vec<ControlSpan> {
    let mut spans = Vec::new();
    let chars: Vec<(usize, char)> = text.char_indices().collect();
    for (index, (byte_index, char_value)) in chars.iter().enumerate() {
        if *char_value != '\\' {
            continue;
        }
        let Some((next_byte, next_char)) = chars.get(index + 1) else {
            continue;
        };
        if !next_char.eq_ignore_ascii_case(&'G') {
            continue;
        }
        let after = chars.get(index + 2).map(|(_, value)| *value);
        if after.is_some_and(|value| value.is_ascii_alphabetic() || value == '[') {
            continue;
        }
        spans.push(ControlSpan {
            start: *byte_index,
            end: *next_byte + next_char.len_utf8(),
            original: text[*byte_index..*next_byte + next_char.len_utf8()].to_string(),
            placeholder: Some("[RMMZ_CURRENCY_UNIT]".to_string()),
            custom_template: None,
            source: SpanSource::Standard,
            priority: 0,
        });
    }
    spans
}

fn iter_symbol_standard_spans(text: &str) -> Vec<ControlSpan> {
    SYMBOL_STANDARD_RE
        .captures_iter(text)
        .filter_map(|captures| {
            let matched = captures.get(0)?;
            let symbol = captures.name("symbol")?.as_str();
            let placeholder = match symbol {
                "{" => "[RMMZ_FONT_LARGER]",
                "}" => "[RMMZ_FONT_SMALLER]",
                "\\" => "[RMMZ_BACKSLASH]",
                "$" => "[RMMZ_SHOW_GOLD_WINDOW]",
                "." => "[RMMZ_WAIT_SHORT]",
                "|" => "[RMMZ_WAIT_LONG]",
                "!" => "[RMMZ_WAIT_INPUT]",
                ">" => "[RMMZ_INSTANT_TEXT_ON]",
                "<" => "[RMMZ_INSTANT_TEXT_OFF]",
                "^" => "[RMMZ_NO_WAIT]",
                _ => return None,
            };
            Some(ControlSpan {
                start: matched.start(),
                end: matched.end(),
                original: matched.as_str().to_string(),
                placeholder: Some(placeholder.to_string()),
                custom_template: None,
                source: SpanSource::Standard,
                priority: 0,
            })
        })
        .collect()
}

fn iter_terms_percent_spans(text: &str) -> Vec<ControlSpan> {
    TERMS_PERCENT_RE
        .captures_iter(text)
        .filter_map(|captures| {
            let matched = captures.get(0)?;
            let param = captures.name("param")?.as_str();
            Some(ControlSpan {
                start: matched.start(),
                end: matched.end(),
                original: matched.as_str().to_string(),
                placeholder: Some(format!("[RMMZ_MESSAGE_ARGUMENT_{}]", param)),
                custom_template: None,
                source: SpanSource::Standard,
                priority: 0,
            })
        })
        .collect()
}

fn iter_literal_escape_spans(text: &str) -> Vec<ControlSpan> {
    let literal_placeholders = [
        ("\\\"", "[RMMZ_LITERAL_DOUBLE_QUOTE]"),
        ("\\'", "[RMMZ_LITERAL_SINGLE_QUOTE]"),
        ("\\/", "[RMMZ_LITERAL_SLASH]"),
        ("\\?", "[RMMZ_LITERAL_QUESTION_MARK]"),
        ("\\a", "[RMMZ_LITERAL_BELL]"),
        ("\\b", "[RMMZ_LITERAL_BACKSPACE]"),
        ("\\f", "[RMMZ_LITERAL_FORM_FEED]"),
        ("\\n", LITERAL_LINE_BREAK_PLACEHOLDER),
        ("\\r", "[RMMZ_LITERAL_CARRIAGE_RETURN]"),
        ("\\t", "[RMMZ_LITERAL_TAB]"),
        ("\\v", "[RMMZ_LITERAL_VERTICAL_TAB]"),
    ];
    let mut spans = Vec::new();
    for (literal, placeholder) in literal_placeholders {
        let mut offset = 0usize;
        while let Some(index) = text[offset..].find(literal) {
            let start = offset + index;
            let end = start + literal.len();
            spans.push(ControlSpan {
                start,
                end,
                original: literal.to_string(),
                placeholder: Some(placeholder.to_string()),
                custom_template: None,
                source: SpanSource::Standard,
                priority: 0,
            });
            offset = end;
        }
    }
    spans.extend(iter_dynamic_literal_escape_spans(
        text,
        "UNICODE",
        &LITERAL_DYNAMIC_UNICODE_RE,
    ));
    spans.extend(iter_dynamic_literal_escape_spans(
        text,
        "HEX",
        &LITERAL_DYNAMIC_HEX_RE,
    ));
    spans.extend(iter_dynamic_literal_escape_spans(
        text,
        "OCTAL",
        &LITERAL_DYNAMIC_OCTAL_RE,
    ));
    spans
}

fn iter_dynamic_literal_escape_spans(
    text: &str,
    escape_name: &str,
    regex: &Regex,
) -> Vec<ControlSpan> {
    regex
        .find_iter(text)
        .filter(|matched| {
            if escape_name != "OCTAL" {
                return true;
            }
            !text[matched.end()..].starts_with('[')
        })
        .map(|matched| ControlSpan {
            start: matched.start(),
            end: matched.end(),
            original: matched.as_str().to_string(),
            placeholder: Some(format!(
                "[RMMZ_LITERAL_{}_ESCAPE_{}]",
                escape_name,
                encode_upper_hex(matched.as_str())
            )),
            custom_template: None,
            source: SpanSource::Standard,
            priority: 0,
        })
        .collect()
}

fn iter_custom_placeholder_spans(text: &str, rules: &CompiledRules) -> Vec<ControlSpan> {
    let mut spans = Vec::new();
    for rule in &rules.custom_placeholder_rules {
        for matched in rule.pattern.find_iter(text).flatten() {
            spans.push(ControlSpan {
                start: matched.start(),
                end: matched.end(),
                original: matched.as_str().to_string(),
                placeholder: None,
                custom_template: Some(rule.placeholder_template.clone()),
                source: SpanSource::Custom,
                priority: 1,
            });
        }
    }
    spans
}

fn select_non_overlapping_spans(mut spans: Vec<ControlSpan>) -> Vec<ControlSpan> {
    spans.sort_by(|left, right| {
        (
            left.start,
            -left.priority,
            -(left.end as isize - left.start as isize),
        )
            .cmp(&(
                right.start,
                -right.priority,
                -(right.end as isize - right.start as isize),
            ))
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
    selected
}

fn collect_unprotected_control_sequences(
    lines: &[String],
    rules: &CompiledRules,
) -> HashMap<String, usize> {
    let mut counts = HashMap::new();
    for line in lines {
        let protected_spans = iter_control_sequence_spans(line, rules);
        for matched in RAW_CONTROL_RE.find_iter(line) {
            let overlaps = protected_spans
                .iter()
                .any(|span| matched.start() < span.end && matched.end() > span.start);
            if overlaps {
                continue;
            }
            *counts.entry(matched.as_str().to_string()).or_insert(0) += 1;
        }
    }
    counts
}

fn format_control_counts(counts: &HashMap<String, usize>) -> String {
    if counts.is_empty() {
        return "无".to_string();
    }
    let mut markers: Vec<&String> = counts.keys().collect();
    markers.sort();
    markers
        .into_iter()
        .map(|marker| format!("{}×{}", marker, counts.get(marker).unwrap_or(&0)))
        .collect::<Vec<String>>()
        .join("、")
}

fn format_custom_placeholder(template: &str, index: usize) -> String {
    template
        .replace("{code}", "")
        .replace("{param}", "")
        .replace("{index}", &index.to_string())
}

fn encode_upper_hex(text: &str) -> String {
    text.as_bytes()
        .iter()
        .map(|byte| format!("{:02X}", byte))
        .collect::<String>()
}

fn probe_protocol_entry(entry: &ProtocolEntry) -> Result<(), String> {
    if entry.mode == "none" {
        return Ok(());
    }
    let translated_text = protocol_translation_text(entry);
    if entry.mode == "note" {
        let note_text = entry
            .note_text
            .as_deref()
            .ok_or_else(|| format!("写入协议缺少 Note 文本: {}", entry.item.location_path))?;
        let tag_name = entry
            .tag_name
            .as_deref()
            .ok_or_else(|| format!("写入协议缺少 Note 标签: {}", entry.item.location_path))?;
        return validate_note_tag_replacement(note_text, tag_name, translated_text);
    }
    let current_value = entry
        .current_value
        .as_ref()
        .ok_or_else(|| format!("写入协议缺少目标值: {}", entry.item.location_path))?;
    let path_parts: Vec<&str> = entry.path_parts.iter().map(String::as_str).collect();
    validate_set_nested_value(
        current_value,
        &path_parts,
        translated_text,
        &entry.item.location_path,
    )
}

fn protocol_translation_text(entry: &ProtocolEntry) -> &str {
    entry
        .item
        .translation_lines
        .first()
        .map(String::as_str)
        .unwrap_or("")
}

fn validate_note_tag_replacement(
    note_text: &str,
    tag_name: &str,
    translated_text: &str,
) -> Result<(), String> {
    let matches: Vec<regex::Captures<'_>> = NOTE_TAG_RE
        .captures_iter(note_text)
        .filter(|captures| {
            captures
                .name("tag")
                .is_some_and(|matched| matched.as_str().trim() == tag_name)
                && captures.name("value").is_some()
        })
        .collect();
    if matches.is_empty() {
        return Err(format!("Note 标签不存在或没有值: {tag_name}"));
    }
    if matches.len() > 1 {
        return Err(format!("Note 标签重复，无法按唯一定位路径回写: {tag_name}"));
    }
    let value = matches[0]
        .name("value")
        .map(|matched| matched.as_str())
        .unwrap_or("");
    let written_text = encode_visible_text_like(value, translated_text)?;
    ensure_encoded_text_valid(value, &written_text, &format!("Note 标签 {tag_name}"))
}

fn validate_set_nested_value(
    current_value: &Value,
    path_parts: &[&str],
    translated_text: &str,
    context: &str,
) -> Result<(), String> {
    if path_parts.is_empty() {
        let original_raw_text = current_value
            .as_str()
            .ok_or_else(|| "路径没有指向字符串叶子".to_string())?;
        let written_text = encode_visible_text_like(original_raw_text, translated_text)?;
        return ensure_encoded_text_valid(original_raw_text, &written_text, context);
    }

    let key = path_parts[0];
    let remain_parts = &path_parts[1..];
    if let Some(object) = current_value.as_object() {
        let child = object
            .get(key)
            .ok_or_else(|| format!("参数键不存在: {key}"))?;
        return validate_set_nested_value(child, remain_parts, translated_text, context);
    }
    if let Some(array) = current_value.as_array() {
        let index = parse_usize(key, context)?;
        let child = array
            .get(index)
            .ok_or_else(|| format!("参数索引越界: {index}"))?;
        return validate_set_nested_value(child, remain_parts, translated_text, context);
    }
    if let Some(text) = current_value.as_str() {
        let (mut container, shell_depth) = decode_json_container_text(text)
            .ok_or_else(|| format!("路径无法继续下钻: {path_parts:?}"))?;
        validate_set_nested_value_mut(&mut container, path_parts, translated_text, context)?;
        let _encoded = encode_json_container_like(&container, shell_depth)?;
        return Ok(());
    }
    Err(format!("路径无法继续下钻: {path_parts:?}"))
}

fn validate_set_nested_value_mut(
    current_value: &mut Value,
    path_parts: &[&str],
    translated_text: &str,
    context: &str,
) -> Result<(), String> {
    if path_parts.is_empty() {
        let original_raw_text = current_value
            .as_str()
            .ok_or_else(|| "路径没有指向字符串叶子".to_string())?
            .to_string();
        let written_text = encode_visible_text_like(&original_raw_text, translated_text)?;
        ensure_encoded_text_valid(&original_raw_text, &written_text, context)?;
        *current_value = Value::String(written_text);
        return Ok(());
    }
    let key = path_parts[0];
    let remain_parts = &path_parts[1..];
    if let Some(object) = current_value.as_object_mut() {
        let child = object
            .get_mut(key)
            .ok_or_else(|| format!("参数键不存在: {key}"))?;
        return validate_set_nested_value_mut(child, remain_parts, translated_text, context);
    }
    if let Some(array) = current_value.as_array_mut() {
        let index = parse_usize(key, context)?;
        let child = array
            .get_mut(index)
            .ok_or_else(|| format!("参数索引越界: {index}"))?;
        return validate_set_nested_value_mut(child, remain_parts, translated_text, context);
    }
    if let Some(text) = current_value.as_str() {
        let (mut container, shell_depth) = decode_json_container_text(text)
            .ok_or_else(|| format!("路径无法继续下钻: {path_parts:?}"))?;
        validate_set_nested_value_mut(&mut container, path_parts, translated_text, context)?;
        *current_value = Value::String(encode_json_container_like(&container, shell_depth)?);
        return Ok(());
    }
    Err(format!("路径无法继续下钻: {path_parts:?}"))
}

fn inspect_visible_text(raw_text: &str) -> (String, usize) {
    let mut current_text = raw_text.to_string();
    let mut shell_depth = 0usize;
    loop {
        let Ok(decoded) = serde_json::from_str::<Value>(&current_text) else {
            break;
        };
        let Value::String(decoded_text) = decoded else {
            break;
        };
        shell_depth += 1;
        current_text = decoded_text;
    }
    (current_text, shell_depth)
}

fn decode_json_container_text(raw_text: &str) -> Option<(Value, usize)> {
    let mut current_text = raw_text.to_string();
    let mut shell_depth = 0usize;
    loop {
        let decoded = serde_json::from_str::<Value>(&current_text).ok()?;
        match decoded {
            Value::Array(_) | Value::Object(_) => return Some((decoded, shell_depth)),
            Value::String(decoded_text) => {
                shell_depth += 1;
                current_text = decoded_text;
            }
            _ => return None,
        }
    }
}

fn encode_visible_text_like(
    original_raw_text: &str,
    translated_visible_text: &str,
) -> Result<String, String> {
    let (_visible_text, shell_depth) = inspect_visible_text(original_raw_text);
    let mut encoded_text = translated_visible_text.to_string();
    for _index in 0..shell_depth {
        encoded_text = serde_json::to_string(&encoded_text)
            .map_err(|error| format!("JSON 字符串封装失败: {error}"))?;
    }
    Ok(encoded_text)
}

fn encode_json_container_like(updated_value: &Value, shell_depth: usize) -> Result<String, String> {
    let mut encoded_text = serde_json::to_string(updated_value)
        .map_err(|error| format!("JSON 容器封装失败: {error}"))?;
    for _index in 0..shell_depth {
        encoded_text = serde_json::to_string(&encoded_text)
            .map_err(|error| format!("JSON 容器外壳封装失败: {error}"))?;
    }
    Ok(encoded_text)
}

fn ensure_encoded_text_valid(
    original_raw_text: &str,
    written_raw_text: &str,
    context: &str,
) -> Result<(), String> {
    let (_original_text, original_shell_depth) = inspect_visible_text(original_raw_text);
    let (written_text, written_shell_depth) = inspect_visible_text(written_raw_text);
    let mut errors = Vec::new();
    if original_shell_depth != written_shell_depth {
        errors.push(format!(
            "JSON 字符串外壳层数不一致 (原文: {}, 写回: {})",
            original_shell_depth, written_shell_depth
        ));
    }
    if original_shell_depth > 0 {
        let mut doubled_literals: Vec<String> = DOUBLED_CONTROL_LITERAL_RE
            .find_iter(&written_text)
            .map(|matched| matched.as_str().to_string())
            .collect::<HashSet<String>>()
            .into_iter()
            .collect();
        doubled_literals.sort();
        if !doubled_literals.is_empty() {
            errors.push(format!(
                "控制符被写成会直接显示的字面量: {}",
                doubled_literals.join("、")
            ));
        }
    }
    if errors.is_empty() {
        Ok(())
    } else {
        Err(format!(
            "{} 文本协议写回失败: {}",
            context,
            errors.join("; ")
        ))
    }
}

fn parse_usize(text: &str, context: &str) -> Result<usize, String> {
    text.parse::<usize>()
        .map_err(|error| format!("路径索引不是整数: {context}: {error}"))
}

fn base_detail(item: &NativeTranslationItem) -> Map<String, Value> {
    let mut detail = Map::new();
    detail.insert("location_path".to_string(), json!(item.location_path));
    detail.insert("item_type".to_string(), json!(item.item_type));
    detail.insert("role".to_string(), json!(item.role));
    detail.insert("original_lines".to_string(), json!(item.original_lines));
    detail.insert(
        "translation_lines".to_string(),
        json!(item.translation_lines),
    );
    detail
}

#[cfg(test)]
mod tests {
    use super::*;

    fn minimal_text_rules() -> Value {
        json!({
            "custom_placeholder_rules": [],
            "allowed_japanese_chars": [],
            "allowed_japanese_tail_chars": [],
            "japanese_segment_pattern": r"[\p{Hiragana}\p{Katakana}\p{Han}ー]+",
            "line_width_count_pattern": r"[^\s]",
            "residual_escape_sequence_pattern": r"\\[A-Za-z0-9_]+\[[^\]]*\]",
            "long_text_line_width_limit": 999
        })
    }

    #[test]
    fn quality_scan_keeps_real_line_breaks_inside_short_text() {
        let payload = json!({
            "items": [
                {
                    "location_path": "Items.json/1/description",
                    "item_type": "short_text",
                    "role": null,
                    "original_lines": ["武器スキル\n\\C[14]敵単体に毒を付与\\C[0]"],
                    "translation_lines": ["武器技能\n\\C[14]对敌方单体施加毒\\C[0]"]
                }
            ],
            "text_rules": minimal_text_rules(),
            "japanese_residual_rules": []
        });
        let output = scan_quality_impl(&payload.to_string()).expect("质检应成功");
        let value: Value = serde_json::from_str(&output).expect("输出应是 JSON");
        assert_eq!(value["text_structure_items"], json!([]));
        assert_eq!(value["placeholder_risk_items"], json!([]));
    }

    #[test]
    fn protocol_scan_skips_empty_entry() {
        let payload = json!({
            "entries": [
                {
                    "item": {
                        "location_path": "plugins.js",
                        "item_type": "short_text",
                        "role": null,
                        "original_lines": ["旧"],
                        "translation_lines": ["新"]
                    },
                    "mode": "none",
                    "current_value": null,
                    "path_parts": [],
                    "note_text": null,
                    "tag_name": null
                }
            ]
        });
        let output = scan_write_protocol_impl(&payload.to_string()).expect("协议检查应成功");
        let value: Value = serde_json::from_str(&output).expect("输出应是 JSON");
        assert_eq!(value, json!([]));
    }

    #[test]
    fn protocol_scan_uses_real_plugin_translation_text() {
        let payload = json!({
            "entries": [
                {
                    "item": {
                        "location_path": "plugins.js/0/Message",
                        "item_type": "short_text",
                        "role": null,
                        "original_lines": ["原文"],
                        "translation_lines": [r"\\V[1]"]
                    },
                    "mode": "nested",
                    "current_value": "\"原文\"",
                    "path_parts": [],
                    "note_text": null,
                    "tag_name": null
                }
            ]
        });
        let output = scan_write_protocol_impl(&payload.to_string()).expect("协议检查应成功");
        let value: Value = serde_json::from_str(&output).expect("输出应是 JSON");
        assert_eq!(value.as_array().map(Vec::len), Some(1));
        assert_eq!(value[0]["location_path"], json!("plugins.js/0/Message"));
        assert!(
            value[0]["reason"]
                .as_str()
                .is_some_and(|reason| reason.contains("控制符被写成会直接显示的字面量"))
        );
    }

    #[test]
    fn protocol_scan_uses_real_note_translation_text() {
        let payload = json!({
            "entries": [
                {
                    "item": {
                        "location_path": "Items.json/1/note/说明",
                        "item_type": "short_text",
                        "role": null,
                        "original_lines": ["原文"],
                        "translation_lines": [r"\\V[1]"]
                    },
                    "mode": "note",
                    "current_value": null,
                    "path_parts": [],
                    "note_text": r#"<说明:"原文">"#,
                    "tag_name": "说明"
                }
            ]
        });
        let output = scan_write_protocol_impl(&payload.to_string()).expect("协议检查应成功");
        let value: Value = serde_json::from_str(&output).expect("输出应是 JSON");
        assert_eq!(value.as_array().map(Vec::len), Some(1));
        assert_eq!(value[0]["location_path"], json!("Items.json/1/note/说明"));
        assert!(
            value[0]["reason"]
                .as_str()
                .is_some_and(|reason| reason.contains("控制符被写成会直接显示的字面量"))
        );
    }

    #[test]
    fn note_source_scan_collects_nested_note_fields() {
        let payload = json!({
            "data": {
                "Items.json": [
                    null,
                    {
                        "id": 1,
                        "note": "<说明:旧文本>",
                        "effects": [
                            {"note": "<效果:旧文本>"}
                        ]
                    }
                ],
                "plugins.js": "var $plugins = [];"
            },
            "file_pattern": null
        });
        let output = collect_note_tag_sources_impl(&payload.to_string()).expect("扫描应成功");
        let value: Value = serde_json::from_str(&output).expect("输出应是 JSON");
        assert_eq!(value.as_array().map(Vec::len), Some(2));
        assert_eq!(value[0]["location_prefix"], "Items.json/1");
        assert_eq!(value[1]["location_prefix"], "Items.json/1/effects/0");
    }

    #[test]
    fn font_scan_reports_direct_and_encoded_json_changes() {
        let payload = json!({
            "data": {
                "System.json": {
                    "advanced": {
                        "mainFontFilename": "OldFont.woff",
                        "nested": "{\"font\": \"AnotherFont.woff\", \"text\": \"正文\"}"
                    }
                },
                "plugins.js": "var $plugins = [];"
            },
            "plugins": [
                {
                    "parameters": {
                        "FontFace": "fonts/OldFont",
                        "HelpText": "请选择 OldFont 字体"
                    }
                }
            ],
            "old_font_names": ["AnotherFont.woff", "OldFont.woff", "OldFont"],
            "replacement_font_name": "NotoSansSC-Regular.ttf"
        });
        let output = scan_font_replacements_impl(&payload.to_string()).expect("扫描应成功");
        let value: Value = serde_json::from_str(&output).expect("输出应是 JSON");
        assert_eq!(value["replaced_count"], 3);
        assert_eq!(value["data_changes"].as_array().map(Vec::len), Some(2));
        assert_eq!(value["plugin_changes"].as_array().map(Vec::len), Some(1));
        assert_eq!(
            value["plugin_changes"][0]["replaced_text"],
            "fonts/NotoSansSC-Regular.ttf"
        );
    }
}
