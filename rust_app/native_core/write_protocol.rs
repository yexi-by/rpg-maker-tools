//! 写入协议预演检查。
//!
//! 本模块负责在真正写入游戏文件前验证插件、事件指令和 Note 标签文本能安全编码。

use rayon::prelude::*;
use serde_json::{Value, json};
use std::collections::HashSet;

use super::details::{base_detail, collect_sorted_details};
use super::models::{ProtocolEntry, ProtocolPayload};
use super::pool::run_with_optional_pool;
use super::rules::{DOUBLED_CONTROL_LITERAL_RE, NOTE_TAG_RE};

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

pub(crate) fn probe_protocol_entry(entry: &ProtocolEntry) -> Result<(), String> {
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

pub(crate) fn protocol_translation_text(entry: &ProtocolEntry) -> &str {
    entry
        .item
        .translation_lines
        .first()
        .map(String::as_str)
        .unwrap_or("")
}

pub(crate) fn validate_note_tag_replacement(
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

pub(crate) fn validate_set_nested_value(
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

pub(crate) fn validate_set_nested_value_mut(
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

pub(crate) fn inspect_visible_text(raw_text: &str) -> (String, usize) {
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

pub(crate) fn decode_json_container_text(raw_text: &str) -> Option<(Value, usize)> {
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

pub(crate) fn encode_visible_text_like(
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

pub(crate) fn encode_json_container_like(
    updated_value: &Value,
    shell_depth: usize,
) -> Result<String, String> {
    let mut encoded_text = serde_json::to_string(updated_value)
        .map_err(|error| format!("JSON 容器封装失败: {error}"))?;
    for _index in 0..shell_depth {
        encoded_text = serde_json::to_string(&encoded_text)
            .map_err(|error| format!("JSON 容器外壳封装失败: {error}"))?;
    }
    Ok(encoded_text)
}

pub(crate) fn ensure_encoded_text_valid(
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

pub(crate) fn parse_usize(text: &str, context: &str) -> Result<usize, String> {
    text.parse::<usize>()
        .map_err(|error| format!("路径索引不是整数: {context}: {error}"))
}
