//! 字体引用替换扫描。
//!
//! 本模块负责在游戏数据和插件参数中并行查找可替换字体引用并生成变更清单。

use rayon::prelude::*;
use serde_json::{Map, Value};

use super::models::{FontReplacementChange, FontReplacementOutput, FontReplacementPayload};
use super::pool::run_with_optional_pool;

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

pub(crate) fn collect_font_replacements_in_value(
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

pub(crate) fn replace_font_names_in_text(
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

pub(crate) fn replace_complete_font_reference_text(
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

pub(crate) fn replace_font_references_in_encoded_json_text(
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

pub(crate) fn replace_font_names_in_json_value(
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

pub(crate) fn serialize_python_style_json(value: &Value) -> String {
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

pub(crate) fn append_json_pointer_part(value_path: &str, part: &str) -> String {
    format!("{value_path}/{}", escape_json_pointer_part(part))
}

pub(crate) fn escape_json_pointer_part(part: &str) -> String {
    part.replace('~', "~0").replace('/', "~1")
}
