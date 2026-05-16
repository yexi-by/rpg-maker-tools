//! 游戏文件写回。
//!
//! 本模块负责把数据库中已经通过检查的译文写入 RPG Maker MZ 游戏文件。
//! 写回以原件留档或原始激活文件为底稿重新套用译文，避免在多次运行中把
//! 旧激活文件里的临时状态当成新的原文。

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;

use regex::Regex;
use serde_json::{Map, Value, json};

use crate::db::TranslationItemRecord;
use crate::error::{AttMzError, Result};
use crate::font_replacement::{
    FontReplacementSummary, apply_font_replacement_to_writable_outputs,
    empty_font_replacement_summary,
};
use crate::report::AgentReport;
use crate::rmmz::{read_data_json_files, read_plugins_json};
use crate::terminology::apply_terminology_translations_from_source;
use crate::translation_state::load_active_translation_items;
use crate::{GameRecord, GameRegistry};

const DATA_DIRECTORY_NAME: &str = "data";
const DATA_ORIGIN_DIRECTORY_NAME: &str = "data_origin";
const JS_DIRECTORY_NAME: &str = "js";
const PLUGINS_FILE_NAME: &str = "plugins.js";
const PLUGINS_ORIGIN_FILE_NAME: &str = "plugins_origin.js";
const SYSTEM_FILE_NAME: &str = "System.json";
const COMMON_EVENTS_FILE_NAME: &str = "CommonEvents.json";
const TROOPS_FILE_NAME: &str = "Troops.json";
const CODE_NAME: i64 = 101;
const CODE_TEXT: i64 = 401;
const CODE_CHOICES: i64 = 102;
const CODE_SCROLL_TEXT: i64 = 405;

/// 执行 `write-back` 命令并生成 Agent 报告。
pub fn write_back_report(
    registry: &GameRegistry,
    game_record: &GameRecord,
    source_text_required_pattern: &str,
    confirm_font_overwrite: bool,
    replacement_font_path: Option<&str>,
) -> Result<AgentReport> {
    let active_items =
        load_active_translation_items(registry, game_record, source_text_required_pattern)?;
    let active_paths = active_items
        .iter()
        .map(|item| item.location_path.clone())
        .collect::<BTreeSet<_>>();
    registry.delete_translation_items_except_paths(&game_record.game_title, &active_paths)?;
    let translated_items = registry
        .read_translated_items(&game_record.game_title)?
        .into_iter()
        .filter(|item| active_paths.contains(&item.location_path))
        .collect::<Vec<_>>();
    let terminology_registry = registry.read_terminology_registry(&game_record.game_title)?;

    if translated_items.is_empty() && terminology_registry.is_none() {
        return Ok(write_back_summary_report(WriteBackSummary {
            data_item_count: 0,
            plugin_item_count: 0,
            terminology_written_count: 0,
            font_summary: empty_font_replacement_summary(),
        }));
    }

    let source_data_files = read_data_json_files(&game_record.game_path)?;
    let mut writable_data_files = source_data_files.clone();
    let source_plugins = read_plugins_json(&game_record.game_path)?;
    let mut writable_plugins = source_plugins.clone();

    let data_item_count = translated_items
        .iter()
        .filter(|item| !item.location_path.starts_with("plugins.js/"))
        .count();
    let plugin_item_count = translated_items.len().saturating_sub(data_item_count);
    if !translated_items.is_empty() {
        write_data_text(&mut writable_data_files, &translated_items)?;
        write_plugin_text(
            &mut writable_data_files,
            &mut writable_plugins,
            &translated_items,
        )?;
    }

    let terminology_written_count = if let Some(registry) = terminology_registry.as_ref() {
        apply_terminology_translations_from_source(
            &source_data_files,
            &mut writable_data_files,
            registry,
        )?
    } else {
        0
    };

    let font_summary = if confirm_font_overwrite {
        apply_font_replacement_to_writable_outputs(
            &game_record.game_path,
            &mut writable_data_files,
            &mut writable_plugins,
            replacement_font_path,
        )?
    } else {
        empty_font_replacement_summary()
    };
    if font_summary.target_font_name.is_some() {
        registry
            .replace_font_replacement_records(&game_record.game_title, &font_summary.records)?;
    }

    write_game_files(
        &game_record.game_path,
        &source_data_files,
        &writable_data_files,
        &source_plugins,
        &writable_plugins,
    )?;

    Ok(write_back_summary_report(WriteBackSummary {
        data_item_count,
        plugin_item_count,
        terminology_written_count,
        font_summary,
    }))
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct WriteBackSummary {
    data_item_count: usize,
    plugin_item_count: usize,
    terminology_written_count: usize,
    font_summary: FontReplacementSummary,
}

fn write_back_summary_report(summary: WriteBackSummary) -> AgentReport {
    let mut report_summary = Map::new();
    report_summary.insert(
        "data_item_count".to_string(),
        json!(summary.data_item_count),
    );
    report_summary.insert(
        "plugin_item_count".to_string(),
        json!(summary.plugin_item_count),
    );
    report_summary.insert(
        "terminology_written_count".to_string(),
        json!(summary.terminology_written_count),
    );
    report_summary.insert(
        "target_font_name".to_string(),
        json!(summary.font_summary.target_font_name.unwrap_or_default()),
    );
    report_summary.insert(
        "source_font_count".to_string(),
        json!(summary.font_summary.source_font_count),
    );
    report_summary.insert(
        "replaced_font_reference_count".to_string(),
        json!(summary.font_summary.replaced_reference_count),
    );
    report_summary.insert(
        "font_copied".to_string(),
        json!(summary.font_summary.copied),
    );
    AgentReport::from_parts(Vec::new(), Vec::new(), report_summary, Map::new())
}

fn write_data_text(
    writable_data: &mut BTreeMap<String, Value>,
    items: &[TranslationItemRecord],
) -> Result<()> {
    let mut command_items = Vec::new();
    for item in items {
        let Some(file_name) = item.location_path.split('/').next() else {
            continue;
        };
        if file_name == PLUGINS_FILE_NAME {
            continue;
        }
        if is_note_tag_location_path(&item.location_path) {
            write_note_tag_item(writable_data, item)?;
            continue;
        }
        if file_name == SYSTEM_FILE_NAME {
            write_system_item(writable_data, item)?;
            continue;
        }
        if is_map_file_name(file_name)
            || file_name == COMMON_EVENTS_FILE_NAME
            || file_name == TROOPS_FILE_NAME
        {
            command_items.push(item.clone());
            continue;
        }
        write_base_item(writable_data, item)?;
    }

    command_items.sort_by_key(command_item_sort_key);
    for item in command_items.into_iter().rev() {
        write_command_item(writable_data, &item)?;
    }
    Ok(())
}

fn command_item_sort_key(item: &TranslationItemRecord) -> (String, Vec<usize>, usize) {
    let anchor_path = if item.item_type == "long_text" && !item.source_line_paths.is_empty() {
        item.source_line_paths
            .last()
            .cloned()
            .unwrap_or_else(|| item.location_path.clone())
    } else {
        item.location_path.clone()
    };
    let parts = anchor_path.split('/').collect::<Vec<_>>();
    let file_name = parts.first().copied().unwrap_or_default().to_string();
    if is_map_file_name(&file_name) {
        return (
            file_name,
            vec![
                parse_usize_part(parts.get(1)),
                parse_usize_part(parts.get(2)),
            ],
            parse_usize_part(parts.get(3)),
        );
    }
    if file_name == COMMON_EVENTS_FILE_NAME {
        return (
            file_name,
            vec![parse_usize_part(parts.get(1))],
            parse_usize_part(parts.get(2)),
        );
    }
    if file_name == TROOPS_FILE_NAME {
        return (
            file_name,
            vec![
                parse_usize_part(parts.get(1)),
                parse_usize_part(parts.get(2)),
            ],
            parse_usize_part(parts.get(3)),
        );
    }
    (file_name, Vec::new(), 0)
}

fn parse_usize_part(part: Option<&&str>) -> usize {
    part.and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(0)
}

fn write_command_item(
    writable_data: &mut BTreeMap<String, Value>,
    item: &TranslationItemRecord,
) -> Result<()> {
    if item.item_type == "short_text" {
        let (commands, command_index) =
            locate_command_array_mut(writable_data, &item.location_path)?;
        let Some(command) = commands
            .get_mut(command_index)
            .and_then(Value::as_object_mut)
        else {
            return Err(invalid_config(format!(
                "事件指令不存在: {}",
                item.location_path
            )));
        };
        write_event_command_text_item(command, item)
    } else if item.item_type == "long_text" {
        let command_code = {
            let (commands, command_index) =
                locate_command_array_mut(writable_data, &item.location_path)?;
            commands
                .get(command_index)
                .and_then(Value::as_object)
                .and_then(|command| command.get("code"))
                .and_then(Value::as_i64)
        };
        match command_code {
            Some(CODE_NAME) => write_line_commands_by_paths(writable_data, item, CODE_TEXT),
            Some(CODE_SCROLL_TEXT) => {
                write_line_commands_by_paths(writable_data, item, CODE_SCROLL_TEXT)
            }
            _ => Err(invalid_config(format!(
                "无法识别的 long_text 指令类型: {}",
                item.location_path
            ))),
        }
    } else if item.item_type == "array" {
        let (commands, command_index) =
            locate_command_array_mut(writable_data, &item.location_path)?;
        let Some(command) = commands
            .get_mut(command_index)
            .and_then(Value::as_object_mut)
        else {
            return Err(invalid_config(format!(
                "事件指令不存在: {}",
                item.location_path
            )));
        };
        if command.get("code").and_then(Value::as_i64) != Some(CODE_CHOICES) {
            return Err(invalid_config(format!(
                "路径 {} 不是 CHOICES 指令",
                item.location_path
            )));
        }
        let parameters = ensure_command_parameters_mut(command, &item.location_path)?;
        let translation_values = Value::Array(
            prepare_text_write_lines(item)
                .into_iter()
                .map(Value::String)
                .collect(),
        );
        if parameters.is_empty() {
            parameters.push(translation_values);
        } else {
            parameters[0] = translation_values;
        }
        Ok(())
    } else {
        Err(invalid_config(format!(
            "事件指令 item_type 无法处理: {}",
            item.item_type
        )))
    }
}

fn write_line_commands_by_paths(
    writable_data: &mut BTreeMap<String, Value>,
    item: &TranslationItemRecord,
    expected_code: i64,
) -> Result<()> {
    if item.source_line_paths.is_empty() {
        return Err(invalid_config(format!(
            "长文本缺少逐行写回路径: {}",
            item.location_path
        )));
    }
    let translation_lines = strip_trailing_empty_lines(prepare_text_write_lines(item));
    let existing_line_count = item.source_line_paths.len();
    let write_line_count = existing_line_count.min(translation_lines.len());
    for (source_line_path, translated_text) in item
        .source_line_paths
        .iter()
        .take(write_line_count)
        .zip(translation_lines.iter())
    {
        let (commands, command_index) = locate_command_array_mut(writable_data, source_line_path)?;
        let Some(command) = commands
            .get_mut(command_index)
            .and_then(Value::as_object_mut)
        else {
            return Err(invalid_config(format!(
                "逐行路径不存在: {source_line_path}"
            )));
        };
        if command.get("code").and_then(Value::as_i64) != Some(expected_code) {
            return Err(invalid_config(format!(
                "逐行路径指向的指令类型错误: {source_line_path}"
            )));
        }
        write_first_parameter(command, translated_text);
    }

    if translation_lines.len() < existing_line_count {
        delete_surplus_line_commands(
            writable_data,
            item,
            expected_code,
            &item.source_line_paths[translation_lines.len()..],
        )?;
        return Ok(());
    }

    let extra_lines = &translation_lines[existing_line_count..];
    if !extra_lines.is_empty() {
        insert_extra_line_commands(writable_data, item, expected_code, extra_lines)?;
    }
    Ok(())
}

fn delete_surplus_line_commands(
    writable_data: &mut BTreeMap<String, Value>,
    item: &TranslationItemRecord,
    expected_code: i64,
    surplus_source_line_paths: &[String],
) -> Result<()> {
    if surplus_source_line_paths.is_empty() {
        return Ok(());
    }
    ensure_source_paths_share_command_list(&item.source_line_paths, &item.location_path)?;
    let mut indexes = Vec::new();
    let mut parent_path: Option<Vec<String>> = None;
    for source_line_path in surplus_source_line_paths {
        let current_parent = command_list_parent_path(source_line_path)?;
        if let Some(parent_path) = parent_path.as_ref()
            && parent_path != &current_parent
        {
            return Err(invalid_config(format!(
                "长文本逐行路径跨事件列表，无法删除多余行: {}",
                item.location_path
            )));
        }
        parent_path = Some(current_parent);
        let (commands, command_index) = locate_command_array_mut(writable_data, source_line_path)?;
        let Some(command) = commands.get(command_index).and_then(Value::as_object) else {
            return Err(invalid_config(format!(
                "多余行删除锚点不存在: {source_line_path}"
            )));
        };
        if command.get("code").and_then(Value::as_i64) != Some(expected_code) {
            return Err(invalid_config(format!(
                "多余行删除锚点指令类型错误: {source_line_path}"
            )));
        }
        indexes.push(command_index);
    }
    if let Some(first_path) = surplus_source_line_paths.first() {
        let (commands, _command_index) = locate_command_array_mut(writable_data, first_path)?;
        indexes.sort_unstable();
        for command_index in indexes.into_iter().rev() {
            if command_index < commands.len() {
                commands.remove(command_index);
            }
        }
    }
    Ok(())
}

fn insert_extra_line_commands(
    writable_data: &mut BTreeMap<String, Value>,
    item: &TranslationItemRecord,
    expected_code: i64,
    extra_lines: &[String],
) -> Result<()> {
    ensure_source_paths_share_command_list(&item.source_line_paths, &item.location_path)?;
    let last_source_path = item
        .source_line_paths
        .last()
        .ok_or_else(|| invalid_config(format!("长文本缺少逐行写回路径: {}", item.location_path)))?;
    let (commands, command_index) = locate_command_array_mut(writable_data, last_source_path)?;
    let Some(base_command) = commands.get(command_index).and_then(Value::as_object) else {
        return Err(invalid_config(format!(
            "额外行插入锚点不存在: {last_source_path}"
        )));
    };
    if base_command.get("code").and_then(Value::as_i64) != Some(expected_code) {
        return Err(invalid_config(format!(
            "额外行插入锚点指令类型错误: {last_source_path}"
        )));
    }
    let indent = base_command.get("indent").and_then(Value::as_i64);
    for (offset, translated_text) in extra_lines.iter().enumerate() {
        let mut command = Map::new();
        command.insert("code".to_string(), json!(expected_code));
        command.insert(
            "parameters".to_string(),
            Value::Array(vec![Value::String(translated_text.clone())]),
        );
        if let Some(indent) = indent {
            command.insert("indent".to_string(), json!(indent));
        }
        commands.insert(command_index + offset + 1, Value::Object(command));
    }
    Ok(())
}

fn write_event_command_text_item(
    command: &mut Map<String, Value>,
    item: &TranslationItemRecord,
) -> Result<()> {
    let path_parts = extract_command_value_path_parts(&item.location_path)?;
    if path_parts.len() < 2 || path_parts[0] != "parameters" {
        return Err(invalid_config(format!(
            "事件指令路径缺少 parameters 段: {}",
            item.location_path
        )));
    }
    let translated_text = prepare_single_text_write_value(item);
    let parameters = ensure_command_parameters_mut(command, &item.location_path)?;
    let param_index = parse_index(&path_parts[1], &item.location_path)?;
    if param_index >= parameters.len() {
        return Err(invalid_config(format!(
            "事件指令参数索引越界: {}",
            item.location_path
        )));
    }
    let current_value = parameters[param_index].clone();
    parameters[param_index] = set_nested_text_value(
        current_value,
        &path_parts[2..],
        &translated_text,
        &item.location_path,
    )?;
    Ok(())
}

fn write_system_item(
    writable_data: &mut BTreeMap<String, Value>,
    item: &TranslationItemRecord,
) -> Result<()> {
    let parts = split_path(&item.location_path);
    let translated_text = prepare_single_text_write_value(item);
    let system_data = writable_data
        .get_mut(SYSTEM_FILE_NAME)
        .and_then(Value::as_object_mut)
        .ok_or_else(|| invalid_config("System.json 顶层必须是对象"))?;
    if parts.len() == 2 {
        system_data.insert(parts[1].clone(), Value::String(translated_text));
        return Ok(());
    }
    if parts.len() == 3 {
        let key = &parts[1];
        if key == "variables" || key == "switches" {
            return Ok(());
        }
        let target_list = system_data
            .get_mut(key)
            .and_then(Value::as_array_mut)
            .ok_or_else(|| {
                invalid_config(format!("System 路径不是数组: {}", item.location_path))
            })?;
        let index = parse_index(&parts[2], &item.location_path)?;
        set_array_string(target_list, index, translated_text, &item.location_path)?;
        return Ok(());
    }
    if parts.len() == 4 && parts[1] == "terms" && parts[2] == "messages" {
        let messages = system_data
            .get_mut("terms")
            .and_then(Value::as_object_mut)
            .and_then(|terms| terms.get_mut("messages"))
            .and_then(Value::as_object_mut)
            .ok_or_else(|| invalid_config("System.json terms.messages 必须是对象"))?;
        messages.insert(parts[3].clone(), Value::String(translated_text));
        return Ok(());
    }
    if parts.len() == 4 && parts[1] == "terms" {
        let target_list = system_data
            .get_mut("terms")
            .and_then(Value::as_object_mut)
            .and_then(|terms| terms.get_mut(&parts[2]))
            .and_then(Value::as_array_mut)
            .ok_or_else(|| {
                invalid_config(format!("System terms 路径不是数组: {}", item.location_path))
            })?;
        let index = parse_index(&parts[3], &item.location_path)?;
        set_array_string(target_list, index, translated_text, &item.location_path)?;
        return Ok(());
    }
    Err(invalid_config(format!(
        "无法识别的 System 路径: {}",
        item.location_path
    )))
}

fn write_base_item(
    writable_data: &mut BTreeMap<String, Value>,
    item: &TranslationItemRecord,
) -> Result<()> {
    let parts = split_path(&item.location_path);
    if parts.len() != 3 {
        return Err(invalid_config(format!(
            "无法识别的基础数据库路径: {}",
            item.location_path
        )));
    }
    let file_name = &parts[0];
    let item_id = parse_index(&parts[1], &item.location_path)?;
    let key = &parts[2];
    let translated_text = prepare_single_text_write_value(item);
    let data = writable_data
        .get_mut(file_name)
        .and_then(Value::as_array_mut)
        .ok_or_else(|| invalid_config(format!("{file_name} 顶层必须是数组")))?;
    if let Some(target) = data.get_mut(item_id).and_then(Value::as_object_mut)
        && target.get("id").and_then(Value::as_u64) == Some(item_id as u64)
    {
        target.insert(key.clone(), Value::String(translated_text));
        return Ok(());
    }
    for target_value in data {
        let Some(target) = target_value.as_object_mut() else {
            continue;
        };
        if target.get("id").and_then(Value::as_u64) != Some(item_id as u64) {
            continue;
        }
        target.insert(key.clone(), Value::String(translated_text));
        return Ok(());
    }
    Err(invalid_config(format!(
        "基础数据库条目不存在: {}",
        item.location_path
    )))
}

fn write_note_tag_item(
    writable_data: &mut BTreeMap<String, Value>,
    item: &TranslationItemRecord,
) -> Result<()> {
    let parts = split_path(&item.location_path);
    let Some(tag_name) = parts.last() else {
        return Err(invalid_config(format!(
            "Note 标签路径无效: {}",
            item.location_path
        )));
    };
    let owner_parts = &parts[1..parts.len().saturating_sub(2)];
    let translated_text = prepare_single_text_write_value(item);
    let owner = locate_note_owner_mut(
        writable_data
            .get_mut(&parts[0])
            .ok_or_else(|| invalid_config(format!("Note 文件不存在: {}", parts[0])))?,
        owner_parts,
        &item.location_path,
    )?;
    let note_text = owner
        .get("note")
        .and_then(Value::as_str)
        .ok_or_else(|| invalid_config(format!("Note 字段不是字符串: {}", item.location_path)))?
        .to_string();
    let updated_note = replace_note_tag_value(&note_text, tag_name, &translated_text)?;
    owner.insert("note".to_string(), Value::String(updated_note));
    Ok(())
}

fn write_plugin_text(
    writable_data: &mut BTreeMap<String, Value>,
    writable_plugins: &mut [Value],
    items: &[TranslationItemRecord],
) -> Result<()> {
    let mut wrote_plugin_item = false;
    for item in items {
        let parts = split_path(&item.location_path);
        if parts.first().map(String::as_str) != Some(PLUGINS_FILE_NAME) || parts.len() < 3 {
            continue;
        }
        let plugin_index = parse_index(&parts[1], &item.location_path)?;
        let translated_text = prepare_single_text_write_value(item);
        let Some(plugin) = writable_plugins
            .get_mut(plugin_index)
            .and_then(Value::as_object_mut)
        else {
            return Err(invalid_config(format!(
                "插件不存在: {}",
                item.location_path
            )));
        };
        let parameters = plugin
            .get_mut("parameters")
            .and_then(Value::as_object_mut)
            .ok_or_else(|| invalid_config(format!("插件参数不是字典: {}", item.location_path)))?;
        let top_key = &parts[2];
        let current_value = parameters
            .get(top_key)
            .cloned()
            .ok_or_else(|| invalid_config(format!("插件参数不存在: {}", item.location_path)))?;
        parameters.insert(
            top_key.clone(),
            set_nested_text_value(
                current_value,
                &parts[3..],
                &translated_text,
                &item.location_path,
            )?,
        );
        wrote_plugin_item = true;
    }
    if wrote_plugin_item {
        writable_data.insert(
            PLUGINS_FILE_NAME.to_string(),
            Value::String(serialize_plugins_js(&Value::Array(
                writable_plugins.to_vec(),
            ))?),
        );
    }
    Ok(())
}

fn set_nested_text_value(
    current_value: Value,
    path_parts: &[String],
    translated_text: &str,
    context: &str,
) -> Result<Value> {
    if path_parts.is_empty() {
        let Some(original_text) = current_value.as_str() else {
            return Err(invalid_config("写回路径没有指向字符串叶子"));
        };
        return Ok(Value::String(encode_visible_text_like(
            original_text,
            translated_text,
        )?));
    }
    let key = &path_parts[0];
    let remain_parts = &path_parts[1..];
    match current_value {
        Value::Object(mut object) => {
            let child = object
                .remove(key)
                .ok_or_else(|| invalid_config(format!("参数键不存在: {key}")))?;
            object.insert(
                key.clone(),
                set_nested_text_value(child, remain_parts, translated_text, context)?,
            );
            Ok(Value::Object(object))
        }
        Value::Array(mut array) => {
            let index = parse_index(key, context)?;
            if index >= array.len() {
                return Err(invalid_config(format!("参数索引越界: {index}")));
            }
            let child = array[index].clone();
            array[index] = set_nested_text_value(child, remain_parts, translated_text, context)?;
            Ok(Value::Array(array))
        }
        Value::String(text) => {
            let Some((container, depth)) = decode_json_container_text(&text)? else {
                return Err(invalid_config(format!(
                    "写回路径无法继续下钻: {path_parts:?}"
                )));
            };
            let updated = set_nested_text_value(container, path_parts, translated_text, context)?;
            if !updated.is_array() && !updated.is_object() {
                return Err(invalid_config("JSON 容器写回结果不是数组或对象"));
            }
            Ok(Value::String(encode_json_container_like(&updated, depth)?))
        }
        _ => Err(invalid_config(format!("参数类型无法处理: {context}"))),
    }
}

fn locate_note_owner_mut<'a>(
    value: &'a mut Value,
    owner_parts: &[String],
    location_path: &str,
) -> Result<&'a mut Map<String, Value>> {
    let mut current_value = value;
    for part in owner_parts {
        if current_value.is_object() {
            current_value = current_value
                .as_object_mut()
                .and_then(|object| object.get_mut(part))
                .ok_or_else(|| invalid_config(format!("Note 路径对象键不存在: {location_path}")))?;
            continue;
        }
        if current_value.is_array() {
            let index = parse_index(part, location_path)?;
            let values = current_value
                .as_array_mut()
                .ok_or_else(|| invalid_config(format!("Note 路径不是数组: {location_path}")))?;
            if index < values.len() && !values[index].is_null() {
                current_value = &mut values[index];
                continue;
            }
            let Some(found_index) = values.iter().position(|value| {
                value
                    .as_object()
                    .and_then(|object| object.get("id"))
                    .and_then(Value::as_u64)
                    == Some(index as u64)
            }) else {
                return Err(invalid_config(format!(
                    "Note 路径数组索引不存在: {location_path}"
                )));
            };
            current_value = &mut values[found_index];
            continue;
        }
        return Err(invalid_config(format!(
            "Note 路径无法继续定位: {location_path}"
        )));
    }
    current_value
        .as_object_mut()
        .ok_or_else(|| invalid_config(format!("Note 持有者不是对象: {location_path}")))
}

fn locate_command_array_mut<'a>(
    writable_data: &'a mut BTreeMap<String, Value>,
    location_path: &str,
) -> Result<(&'a mut Vec<Value>, usize)> {
    let parts = split_path(location_path);
    let Some(file_name) = parts.first() else {
        return Err(invalid_config(format!(
            "无法识别的事件定位路径: {location_path}"
        )));
    };
    let data = writable_data
        .get_mut(file_name)
        .ok_or_else(|| invalid_config(format!("事件数据文件不存在: {file_name}")))?;
    if is_map_file_name(file_name) {
        let event_id = parse_index_part(&parts, 1, location_path)?;
        let page_index = parse_index_part(&parts, 2, location_path)?;
        let command_index = parse_index_part(&parts, 3, location_path)?;
        let commands = data
            .as_object_mut()
            .and_then(|map| map.get_mut("events"))
            .and_then(Value::as_array_mut)
            .and_then(|events| events.get_mut(event_id))
            .and_then(Value::as_object_mut)
            .and_then(|event| event.get_mut("pages"))
            .and_then(Value::as_array_mut)
            .and_then(|pages| pages.get_mut(page_index))
            .and_then(Value::as_object_mut)
            .and_then(|page| page.get_mut("list"))
            .and_then(Value::as_array_mut)
            .ok_or_else(|| invalid_config(format!("无法定位事件列表: {location_path}")))?;
        return Ok((commands, command_index));
    }
    if file_name == COMMON_EVENTS_FILE_NAME {
        let event_id = parse_index_part(&parts, 1, location_path)?;
        let command_index = parse_index_part(&parts, 2, location_path)?;
        let commands = data
            .as_array_mut()
            .and_then(|events| events.get_mut(event_id))
            .and_then(Value::as_object_mut)
            .and_then(|event| event.get_mut("list"))
            .and_then(Value::as_array_mut)
            .ok_or_else(|| invalid_config(format!("无法定位公共事件列表: {location_path}")))?;
        return Ok((commands, command_index));
    }
    if file_name == TROOPS_FILE_NAME {
        let troop_id = parse_index_part(&parts, 1, location_path)?;
        let page_index = parse_index_part(&parts, 2, location_path)?;
        let command_index = parse_index_part(&parts, 3, location_path)?;
        let commands = data
            .as_array_mut()
            .and_then(|troops| troops.get_mut(troop_id))
            .and_then(Value::as_object_mut)
            .and_then(|troop| troop.get_mut("pages"))
            .and_then(Value::as_array_mut)
            .and_then(|pages| pages.get_mut(page_index))
            .and_then(Value::as_object_mut)
            .and_then(|page| page.get_mut("list"))
            .and_then(Value::as_array_mut)
            .ok_or_else(|| invalid_config(format!("无法定位敌群事件列表: {location_path}")))?;
        return Ok((commands, command_index));
    }
    Err(invalid_config(format!(
        "无法识别的事件定位路径: {location_path}"
    )))
}

fn extract_command_value_path_parts(location_path: &str) -> Result<Vec<String>> {
    let parts = split_path(location_path);
    let Some(file_name) = parts.first() else {
        return Err(invalid_config(format!(
            "无法识别的事件值路径: {location_path}"
        )));
    };
    if is_map_file_name(file_name) {
        return Ok(parts.into_iter().skip(4).collect());
    }
    if file_name == COMMON_EVENTS_FILE_NAME {
        return Ok(parts.into_iter().skip(3).collect());
    }
    if file_name == TROOPS_FILE_NAME {
        return Ok(parts.into_iter().skip(4).collect());
    }
    Err(invalid_config(format!(
        "无法识别的事件值路径: {location_path}"
    )))
}

fn command_list_parent_path(location_path: &str) -> Result<Vec<String>> {
    let parts = split_path(location_path);
    let Some(file_name) = parts.first() else {
        return Err(invalid_config(format!(
            "无法识别的事件定位路径: {location_path}"
        )));
    };
    if is_map_file_name(file_name) {
        return Ok(parts.into_iter().take(3).collect());
    }
    if file_name == COMMON_EVENTS_FILE_NAME {
        return Ok(parts.into_iter().take(2).collect());
    }
    if file_name == TROOPS_FILE_NAME {
        return Ok(parts.into_iter().take(3).collect());
    }
    Err(invalid_config(format!(
        "无法识别的事件定位路径: {location_path}"
    )))
}

fn ensure_source_paths_share_command_list(
    source_line_paths: &[String],
    location_path: &str,
) -> Result<()> {
    let mut parent_paths = BTreeSet::new();
    for path in source_line_paths {
        parent_paths.insert(command_list_parent_path(path)?);
    }
    if parent_paths.len() != 1 {
        return Err(invalid_config(format!(
            "长文本逐行路径跨事件列表，无法插入额外行: {location_path}"
        )));
    }
    Ok(())
}

fn ensure_command_parameters_mut<'a>(
    command: &'a mut Map<String, Value>,
    location_path: &str,
) -> Result<&'a mut Vec<Value>> {
    command
        .get_mut("parameters")
        .and_then(Value::as_array_mut)
        .ok_or_else(|| invalid_config(format!("事件指令 parameters 不是数组: {location_path}")))
}

fn write_first_parameter(command: &mut Map<String, Value>, text: &str) {
    if let Some(parameters) = command.get_mut("parameters").and_then(Value::as_array_mut) {
        if parameters.is_empty() {
            parameters.push(Value::String(text.to_string()));
        } else {
            parameters[0] = Value::String(text.to_string());
        }
    } else {
        command.insert(
            "parameters".to_string(),
            Value::Array(vec![Value::String(text.to_string())]),
        );
    }
}

fn prepare_text_write_lines(item: &TranslationItemRecord) -> Vec<String> {
    item.translation_lines
        .iter()
        .map(|line| line.trim().to_string())
        .collect()
}

fn prepare_single_text_write_value(item: &TranslationItemRecord) -> String {
    prepare_text_write_lines(item)
        .into_iter()
        .next()
        .unwrap_or_default()
}

fn strip_trailing_empty_lines(lines: Vec<String>) -> Vec<String> {
    let mut stripped_lines = lines;
    while stripped_lines
        .last()
        .map(|line| line.is_empty())
        .unwrap_or(false)
    {
        stripped_lines.pop();
    }
    stripped_lines
}

fn replace_note_tag_value(
    note_text: &str,
    tag_name: &str,
    translated_text: &str,
) -> Result<String> {
    let pattern = Regex::new(r"(?s)<(?P<tag>[^<>:\r\n]+)(?::(?P<value>[^<>]*))?>")
        .map_err(|error| invalid_config(format!("Note 标签正则不可用: {error}")))?;
    let matches = pattern
        .captures_iter(note_text)
        .filter_map(|captures| {
            let tag = captures.name("tag")?.as_str().trim();
            let value_match = captures.name("value")?;
            (tag == tag_name).then_some(value_match.range())
        })
        .collect::<Vec<_>>();
    if matches.is_empty() {
        return Err(invalid_config(format!(
            "Note 标签不存在或没有值: {tag_name}"
        )));
    }
    if matches.len() > 1 {
        return Err(invalid_config(format!(
            "Note 标签重复，无法按唯一定位路径回写: {tag_name}"
        )));
    }
    let range = &matches[0];
    let original_raw_text = &note_text[range.clone()];
    let written_text = encode_visible_text_like(original_raw_text, translated_text)?;
    Ok(format!(
        "{}{}{}",
        &note_text[..range.start],
        written_text,
        &note_text[range.end..]
    ))
}

fn encode_visible_text_like(
    original_raw_text: &str,
    translated_visible_text: &str,
) -> Result<String> {
    let (_text, shell_depth) = inspect_visible_text(original_raw_text)?;
    let mut encoded_text = translated_visible_text.to_string();
    for _index in 0..shell_depth {
        encoded_text = serde_json::to_string(&encoded_text).map_err(|source| AttMzError::Json {
            context: "序列化 JSON 字符串外壳".to_string(),
            source,
        })?;
    }
    Ok(encoded_text)
}

fn inspect_visible_text(raw_text: &str) -> Result<(String, usize)> {
    let mut current_text = raw_text.to_string();
    let mut shell_depth = 0usize;
    while is_json_string_shell(&current_text) {
        let decoded = serde_json::from_str::<Value>(&current_text);
        let Ok(Value::String(decoded_text)) = decoded else {
            break;
        };
        shell_depth += 1;
        current_text = decoded_text;
    }
    Ok((current_text, shell_depth))
}

fn decode_json_container_text(raw_text: &str) -> Result<Option<(Value, usize)>> {
    let mut current_text = raw_text.to_string();
    let mut shell_depth = 0usize;
    loop {
        let decoded = serde_json::from_str::<Value>(&current_text);
        let Ok(parsed) = decoded else {
            return Ok(None);
        };
        if parsed.is_array() || parsed.is_object() {
            return Ok(Some((parsed, shell_depth)));
        }
        let Value::String(next_text) = parsed else {
            return Ok(None);
        };
        shell_depth += 1;
        current_text = next_text;
    }
}

fn encode_json_container_like(updated_value: &Value, shell_depth: usize) -> Result<String> {
    let mut encoded_text = serialize_python_style_json(updated_value);
    for _index in 0..shell_depth {
        encoded_text = serde_json::to_string(&encoded_text).map_err(|source| AttMzError::Json {
            context: "序列化 JSON 容器外壳".to_string(),
            source,
        })?;
    }
    Ok(encoded_text)
}

fn is_json_string_shell(text: &str) -> bool {
    let trimmed_text = text.trim();
    trimmed_text.starts_with('"') && trimmed_text.ends_with('"')
}

fn write_game_files(
    game_root: &Path,
    source_data_files: &BTreeMap<String, Value>,
    writable_data_files: &BTreeMap<String, Value>,
    source_plugins: &[Value],
    writable_plugins: &[Value],
) -> Result<()> {
    let changed_data_files = writable_data_files
        .iter()
        .filter_map(|(file_name, writable_value)| {
            if file_name == PLUGINS_FILE_NAME {
                return None;
            }
            (source_data_files.get(file_name) != Some(writable_value)).then(|| file_name.clone())
        })
        .collect::<Vec<_>>();
    let plugins_changed = source_plugins != writable_plugins;
    if changed_data_files.is_empty() && !plugins_changed {
        return Ok(());
    }

    let active_data_dir = game_root.join(DATA_DIRECTORY_NAME);
    let origin_data_dir = game_root.join(DATA_ORIGIN_DIRECTORY_NAME);
    let active_plugins_path = game_root.join(JS_DIRECTORY_NAME).join(PLUGINS_FILE_NAME);
    let origin_plugins_path = game_root
        .join(JS_DIRECTORY_NAME)
        .join(PLUGINS_ORIGIN_FILE_NAME);
    if !active_data_dir.exists() {
        return Err(AttMzError::MissingPath {
            kind: "激活数据目录",
            path: active_data_dir,
        });
    }
    if !active_plugins_path.exists() {
        return Err(AttMzError::MissingPath {
            kind: "激活插件配置文件",
            path: active_plugins_path,
        });
    }

    if !changed_data_files.is_empty() {
        fs::create_dir_all(&origin_data_dir).map_err(|source| {
            AttMzError::io(
                format!("创建原件留档目录 {}", origin_data_dir.display()),
                source,
            )
        })?;
        for file_name in &changed_data_files {
            let source_path = game_root.join(DATA_DIRECTORY_NAME).join(file_name);
            let target_path = origin_data_dir.join(file_name);
            if !target_path.exists() {
                fs::copy(&source_path, &target_path).map_err(|source| {
                    AttMzError::io(
                        format!("备份原始 data 文件 {}", source_path.display()),
                        source,
                    )
                })?;
            }
        }
    }
    if plugins_changed && !origin_plugins_path.exists() {
        if let Some(parent) = origin_plugins_path.parent() {
            fs::create_dir_all(parent).map_err(|source| {
                AttMzError::io(format!("创建插件原件留档目录 {}", parent.display()), source)
            })?;
        }
        fs::copy(&active_plugins_path, &origin_plugins_path).map_err(|source| {
            AttMzError::io(
                format!("备份原始插件配置 {}", active_plugins_path.display()),
                source,
            )
        })?;
    }

    for file_name in &changed_data_files {
        let value = writable_data_files
            .get(file_name)
            .ok_or_else(|| invalid_config(format!("待写回 data 文件不存在: {file_name}")))?;
        write_json_file(&game_root.join(DATA_DIRECTORY_NAME).join(file_name), value)?;
    }
    if plugins_changed {
        write_text_file(
            &active_plugins_path,
            &serialize_plugins_js(&Value::Array(writable_plugins.to_vec()))?,
        )?;
    }
    Ok(())
}

fn set_array_string(
    values: &mut [Value],
    index: usize,
    text: String,
    location_path: &str,
) -> Result<()> {
    let Some(value) = values.get_mut(index) else {
        return Err(invalid_config(format!("数组索引越界: {location_path}")));
    };
    *value = Value::String(text);
    Ok(())
}

fn parse_index_part(parts: &[String], index: usize, location_path: &str) -> Result<usize> {
    let Some(part) = parts.get(index) else {
        return Err(invalid_config(format!("路径缺少下标: {location_path}")));
    };
    parse_index(part, location_path)
}

fn parse_index(part: &str, location_path: &str) -> Result<usize> {
    part.parse::<usize>()
        .map_err(|error| invalid_config(format!("路径下标无效 {location_path}: {error}")))
}

fn is_note_tag_location_path(location_path: &str) -> bool {
    let parts = split_path(location_path);
    parts.len() >= 3 && parts.get(parts.len() - 2).map(String::as_str) == Some("note")
}

fn is_map_file_name(file_name: &str) -> bool {
    let Some(rest) = file_name
        .strip_prefix("Map")
        .and_then(|value| value.strip_suffix(".json"))
    else {
        return false;
    };
    !rest.is_empty() && rest.chars().all(|char_value| char_value.is_ascii_digit())
}

fn split_path(path: &str) -> Vec<String> {
    path.split('/').map(str::to_string).collect()
}

fn serialize_plugins_js(value: &Value) -> Result<String> {
    let plugins_text = serde_json::to_string_pretty(value).map_err(|source| AttMzError::Json {
        context: "序列化插件配置".to_string(),
        source,
    })?;
    Ok(format!("var $plugins = {plugins_text};\n"))
}

fn serialize_python_style_json(value: &Value) -> String {
    match value {
        Value::Array(items) => {
            let serialized_items = items
                .iter()
                .map(serialize_python_style_json)
                .collect::<Vec<_>>();
            format!("[{}]", serialized_items.join(", "))
        }
        Value::Object(object) => {
            let serialized_items = object
                .iter()
                .map(|(key, item)| match serde_json::to_string(key) {
                    Ok(serialized_key) => {
                        format!("{serialized_key}: {}", serialize_python_style_json(item))
                    }
                    Err(_) => format!("\"\": {}", serialize_python_style_json(item)),
                })
                .collect::<Vec<_>>();
            format!("{{{}}}", serialized_items.join(", "))
        }
        _ => match serde_json::to_string(value) {
            Ok(serialized_value) => serialized_value,
            Err(_) => "null".to_string(),
        },
    }
}

fn write_json_file(path: &Path, value: &Value) -> Result<()> {
    let text = serde_json::to_string_pretty(value).map_err(|source| AttMzError::Json {
        context: format!("序列化 JSON {}", path.display()),
        source,
    })?;
    write_text_file(path, &format!("{text}\n"))
}

fn write_text_file(path: &Path, text: &str) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|source| AttMzError::io(format!("创建目录 {}", parent.display()), source))?;
    }
    fs::write(path, text)
        .map_err(|source| AttMzError::io(format!("写入 {}", path.display()), source))
}

fn invalid_config(message: impl Into<String>) -> AttMzError {
    AttMzError::InvalidConfig(message.into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::GameRegistry;
    use crate::config::DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN;
    use crate::db::TranslationItemRecord;

    #[test]
    fn write_back_writes_saved_data_translations_and_backups_origin() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        let game = create_write_back_test_game(temp.path(), "WriteBackData");
        let registry = GameRegistry {
            db_directory: temp.path().join("db"),
        };
        let game_record = registry.register_game(&game).expect("游戏应注册成功");
        registry
            .write_translation_items(
                &game_record.game_title,
                &[
                    TranslationItemRecord {
                        location_path: "System.json/gameTitle".to_string(),
                        item_type: "short_text".to_string(),
                        role: None,
                        original_lines: vec!["原題".to_string()],
                        source_line_paths: Vec::new(),
                        translation_lines: vec!["中文标题".to_string()],
                    },
                    TranslationItemRecord {
                        location_path: "Actors.json/1/profile".to_string(),
                        item_type: "short_text".to_string(),
                        role: None,
                        original_lines: vec!["アリスの紹介".to_string()],
                        source_line_paths: Vec::new(),
                        translation_lines: vec!["爱丽丝的介绍".to_string()],
                    },
                    TranslationItemRecord {
                        location_path: "CommonEvents.json/1/0".to_string(),
                        item_type: "long_text".to_string(),
                        role: Some("旁白".to_string()),
                        original_lines: vec!["こんにちは".to_string()],
                        source_line_paths: vec!["CommonEvents.json/1/1".to_string()],
                        translation_lines: vec!["你好".to_string()],
                    },
                ],
            )
            .expect("译文应写入数据库");

        let report = write_back_report(
            &registry,
            &game_record,
            DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN,
            false,
            None,
        )
        .expect("写回应成功");

        assert_eq!(report.summary["data_item_count"], json!(3));
        let system = read_json_test(&game.join("data/System.json"));
        assert_eq!(system["gameTitle"], json!("中文标题"));
        let actors = read_json_test(&game.join("data/Actors.json"));
        assert_eq!(actors[1]["profile"], json!("爱丽丝的介绍"));
        let common_events = read_json_test(&game.join("data/CommonEvents.json"));
        assert_eq!(common_events[1]["list"][1]["parameters"][0], json!("你好"));
        let origin_system = read_json_test(&game.join("data_origin/System.json"));
        assert_eq!(origin_system["gameTitle"], json!("原題"));
    }

    fn create_write_back_test_game(root: &Path, title: &str) -> std::path::PathBuf {
        let game = root.join("game");
        fs::create_dir_all(game.join("data")).expect("data 目录应创建成功");
        fs::create_dir_all(game.join("js")).expect("js 目录应创建成功");
        fs::write(
            game.join("package.json"),
            json!({"window": {"title": title}}).to_string(),
        )
        .expect("package.json 应写入成功");
        write_json_file(
            &game.join("data/System.json"),
            &json!({"gameTitle": "原題"}),
        )
        .expect("System.json 应写入成功");
        write_json_file(
            &game.join("data/Actors.json"),
            &json!([null, {"id": 1, "name": "アリス", "profile": "アリスの紹介"}]),
        )
        .expect("Actors.json 应写入成功");
        write_json_file(
            &game.join("data/CommonEvents.json"),
            &json!([null, {
                "id": 1,
                "name": "event",
                "list": [
                    {"code": 101, "parameters": [0, 0, 0, 2, ""]},
                    {"code": 401, "parameters": ["こんにちは"]},
                    {"code": 0, "parameters": []}
                ]
            }]),
        )
        .expect("CommonEvents.json 应写入成功");
        write_json_file(&game.join("data/Troops.json"), &json!([]))
            .expect("Troops.json 应写入成功");
        fs::write(game.join("js/plugins.js"), "var $plugins = [];\n")
            .expect("plugins.js 应写入成功");
        game
    }

    fn read_json_test(path: &Path) -> Value {
        let text = fs::read_to_string(path).expect("JSON 文件应读取成功");
        serde_json::from_str(&text).expect("JSON 文件应解析成功")
    }
}
