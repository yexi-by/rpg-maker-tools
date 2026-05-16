//! 字体覆盖和还原。
//!
//! 本模块负责按原件留档对比还原字体引用。它只处理完整字体引用字段，避免
//! 把玩家可见正文中提到的字体名称误当成配置引用来改写。

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

use regex::Regex;
use serde_json::{Map, Value, json};

use crate::db::FontReplacementRecord;
use crate::error::{AttMzError, Result};
use crate::report::{AgentReport, issue};
use crate::{GameRecord, GameRegistry};

const DATA_DIRECTORY_NAME: &str = "data";
const DATA_ORIGIN_DIRECTORY_NAME: &str = "data_origin";
const JS_DIRECTORY_NAME: &str = "js";
const PLUGINS_FILE_NAME: &str = "plugins.js";
const PLUGINS_ORIGIN_FILE_NAME: &str = "plugins_origin.js";
const FONT_FILE_SUFFIXES: &[&str] = &[".ttf", ".otf", ".woff", ".woff2"];

/// 字体还原执行摘要。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FontRestoreSummary {
    /// 本次用于识别覆盖字体的新字体名称；没有候选时为 `None`。
    pub target_font_name: Option<String>,
    /// 被还原的字段数量。
    pub restored_field_count: usize,
    /// 被还原的完整字体引用数量。
    pub restored_reference_count: usize,
}

/// 字体覆盖执行摘要。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FontReplacementSummary {
    /// 本次写入游戏字体目录的新字体文件名；未执行覆盖时为 `None`。
    pub target_font_name: Option<String>,
    /// 游戏字体目录中被视为旧字体候选的字体文件数量。
    pub source_font_count: usize,
    /// 本轮可写数据中被替换的完整字体引用数量。
    pub replaced_reference_count: usize,
    /// 是否已经把新字体复制到游戏字体目录。
    pub copied: bool,
    /// 用于后续还原的字段级替换记录。
    pub records: Vec<FontReplacementRecord>,
}

struct OriginFontRestoreSummary {
    target_font_names: Vec<String>,
    restored_field_count: usize,
    restored_reference_count: usize,
}

/// 生成未执行字体覆盖时使用的空摘要。
pub fn empty_font_replacement_summary() -> FontReplacementSummary {
    FontReplacementSummary {
        target_font_name: None,
        source_font_count: 0,
        replaced_reference_count: 0,
        copied: false,
        records: Vec::new(),
    }
}

/// 复制目标字体，并在本轮可写数据中替换完整字体引用。
///
/// 函数只修改调用方传入的内存副本，不直接写回游戏数据文件。这样写回阶段可以
/// 继续沿用统一的原件留档与差异写入流程，避免字体覆盖绕过备份机制。
pub fn apply_font_replacement_to_writable_outputs(
    game_root: &Path,
    writable_data: &mut BTreeMap<String, Value>,
    writable_plugins: &mut [Value],
    replacement_font_path: Option<&str>,
) -> Result<FontReplacementSummary> {
    let Some(replacement_font_path) = replacement_font_path
        .map(str::trim)
        .filter(|path_text| !path_text.is_empty())
    else {
        return Ok(empty_font_replacement_summary());
    };

    let source_font_path = resolve_replacement_font_path(replacement_font_path)?;
    let target_font_name = source_font_path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| AttMzError::InvalidConfig("替换字体文件名不是有效文本".to_string()))?
        .to_string();
    let font_dir = game_root.join("fonts");
    let old_font_names = collect_existing_font_names(&font_dir, &target_font_name)?;
    copy_replacement_font(&source_font_path, &font_dir)?;
    let (replaced_reference_count, records) = replace_font_references(
        writable_data,
        writable_plugins,
        &old_font_names,
        &target_font_name,
    )?;

    Ok(FontReplacementSummary {
        target_font_name: Some(target_font_name),
        source_font_count: old_font_names.len(),
        replaced_reference_count,
        copied: true,
        records,
    })
}

/// 对当前激活游戏文件执行字体覆盖，并写入可供 `restore-font` 使用的记录。
///
/// 该入口服务于只写术语的流程：术语写回已经完成后，再基于当前激活文件替换字体
/// 引用，同时仍会在首次修改前保存原件留档。
pub fn apply_font_replacement_to_active_game(
    registry: &GameRegistry,
    game_record: &GameRecord,
    replacement_font_path: Option<&str>,
) -> Result<FontReplacementSummary> {
    let mut writable_data = read_active_data_json_files(&game_record.game_path)?;
    let source_data = writable_data.clone();
    let active_plugins_path = game_record
        .game_path
        .join(JS_DIRECTORY_NAME)
        .join(PLUGINS_FILE_NAME);
    let plugins_value = parse_plugins_js_file(&active_plugins_path)?;
    let Some(plugins_items) = plugins_value.as_array() else {
        return Err(AttMzError::InvalidGame(
            "plugins.js 中的 $plugins 必须是数组".to_string(),
        ));
    };
    let source_plugins = plugins_items.clone();
    let mut writable_plugins = source_plugins.clone();
    let summary = apply_font_replacement_to_writable_outputs(
        &game_record.game_path,
        &mut writable_data,
        &mut writable_plugins,
        replacement_font_path,
    )?;
    if summary.target_font_name.is_some() {
        write_font_replacement_outputs(
            &game_record.game_path,
            &source_data,
            &writable_data,
            &source_plugins,
            &writable_plugins,
        )?;
        registry.replace_font_replacement_records(&game_record.game_title, &summary.records)?;
    }
    Ok(summary)
}

/// 执行 `restore-font` 命令并生成 Agent 报告。
pub fn restore_font_report(
    registry: &GameRegistry,
    game_record: &GameRecord,
    replacement_font_path: Option<&str>,
) -> Result<AgentReport> {
    let records = registry.read_font_replacement_records(&game_record.game_title)?;
    let target_font_names = collect_replacement_font_names(replacement_font_path, &records);
    if target_font_names.is_empty() {
        return Ok(font_restore_summary_report(FontRestoreSummary {
            target_font_name: None,
            restored_field_count: 0,
            restored_reference_count: 0,
        }));
    }

    let restore_summary =
        restore_font_references_from_origin_backups(&game_record.game_path, &target_font_names)?;
    if !records.is_empty() {
        registry.clear_font_replacement_records(&game_record.game_title)?;
    }
    Ok(font_restore_summary_report(FontRestoreSummary {
        target_font_name: Some(restore_summary.target_font_names.join("、")),
        restored_field_count: restore_summary.restored_field_count,
        restored_reference_count: restore_summary.restored_reference_count,
    }))
}

/// 收集本次字体还原应识别的新字体文件名。
pub fn collect_replacement_font_names(
    replacement_font_path: Option<&str>,
    records: &[FontReplacementRecord],
) -> Vec<String> {
    let mut font_names = Vec::new();
    if let Some(path_text) = replacement_font_path
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        font_names.push(file_name_text(path_text));
    }
    font_names.extend(
        records
            .iter()
            .map(|record| record.replacement_font_name.clone()),
    );
    normalize_font_name_list(font_names)
}

fn resolve_replacement_font_path(font_path_text: &str) -> Result<PathBuf> {
    let raw_path = Path::new(font_path_text.trim());
    let candidate_path = if raw_path.is_absolute() {
        raw_path.to_path_buf()
    } else {
        std::env::current_dir()
            .map_err(|source| AttMzError::io("读取当前工作目录", source))?
            .join(raw_path)
    };
    let resolved_path = candidate_path.canonicalize().map_err(|source| {
        AttMzError::io(
            format!("解析替换字体路径 {}", candidate_path.display()),
            source,
        )
    })?;
    if !resolved_path.is_file() {
        return Err(AttMzError::InvalidConfig(format!(
            "替换字体路径不是文件: {}",
            resolved_path.display()
        )));
    }
    if !is_supported_font_file_name(&file_name_text(&resolved_path.display().to_string())) {
        return Err(AttMzError::InvalidConfig(format!(
            "替换字体文件扩展名不受支持: {}",
            resolved_path.display()
        )));
    }
    Ok(resolved_path)
}

fn collect_existing_font_names(
    font_dir: &Path,
    replacement_font_name: &str,
) -> Result<Vec<String>> {
    if !font_dir.exists() {
        return Ok(Vec::new());
    }
    if !font_dir.is_dir() {
        return Err(AttMzError::NotDirectory {
            kind: "游戏字体目录",
            path: font_dir.to_path_buf(),
        });
    }
    let replacement_font_name_lower = replacement_font_name.to_ascii_lowercase();
    let mut font_names = Vec::new();
    for entry in fs::read_dir(font_dir)
        .map_err(|source| AttMzError::io(format!("扫描字体目录 {}", font_dir.display()), source))?
    {
        let entry = entry.map_err(|source| AttMzError::io("读取字体目录项", source))?;
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        let Some(font_name) = path.file_name().and_then(|value| value.to_str()) else {
            continue;
        };
        if !is_supported_font_file_name(font_name) {
            continue;
        }
        if font_name.to_ascii_lowercase() == replacement_font_name_lower {
            continue;
        }
        font_names.push(font_name.to_string());
    }
    font_names.sort_by_key(|name| name.to_ascii_lowercase());
    Ok(font_names)
}

fn copy_replacement_font(source_font_path: &Path, font_dir: &Path) -> Result<()> {
    fs::create_dir_all(font_dir)
        .map_err(|source| AttMzError::io(format!("创建字体目录 {}", font_dir.display()), source))?;
    let Some(file_name) = source_font_path.file_name() else {
        return Err(AttMzError::InvalidConfig("替换字体文件名为空".to_string()));
    };
    let target_path = font_dir.join(file_name);
    if target_path.exists()
        && source_font_path.canonicalize().map_err(|source| {
            AttMzError::io(
                format!("解析替换字体 {}", source_font_path.display()),
                source,
            )
        })? == target_path.canonicalize().map_err(|source| {
            AttMzError::io(format!("解析目标字体 {}", target_path.display()), source)
        })?
    {
        return Ok(());
    }
    fs::copy(source_font_path, &target_path).map_err(|source| {
        AttMzError::io(
            format!(
                "复制替换字体 {} 到 {}",
                source_font_path.display(),
                target_path.display()
            ),
            source,
        )
    })?;
    Ok(())
}

fn is_supported_font_file_name(font_name: &str) -> bool {
    let lower_name = font_name.to_ascii_lowercase();
    FONT_FILE_SUFFIXES
        .iter()
        .any(|suffix| lower_name.ends_with(suffix) && font_name.len() > suffix.len())
}

fn replace_font_references(
    writable_data: &mut BTreeMap<String, Value>,
    writable_plugins: &mut [Value],
    old_font_names: &[String],
    replacement_font_name: &str,
) -> Result<(usize, Vec<FontReplacementRecord>)> {
    let old_font_reference_tokens = build_font_reference_tokens(old_font_names);
    if old_font_reference_tokens.is_empty() {
        return Ok((0, Vec::new()));
    }

    let mut records = Vec::new();
    let mut replaced_count = 0usize;
    for (file_name, value) in writable_data {
        if file_name == PLUGINS_FILE_NAME {
            continue;
        }
        replaced_count += replace_font_references_in_json_value(
            value,
            &old_font_reference_tokens,
            replacement_font_name,
            file_name,
            "",
            &mut records,
        )?;
    }

    let mut plugins_value = Value::Array(writable_plugins.to_vec());
    let plugin_replaced_count = replace_font_references_in_json_value(
        &mut plugins_value,
        &old_font_reference_tokens,
        replacement_font_name,
        PLUGINS_FILE_NAME,
        "",
        &mut records,
    )?;
    if plugin_replaced_count > 0 {
        let Some(plugin_items) = plugins_value.as_array() else {
            return Err(AttMzError::InvalidGame(
                "字体替换后的插件配置不是数组".to_string(),
            ));
        };
        for (target, updated) in writable_plugins.iter_mut().zip(plugin_items.iter()) {
            *target = updated.clone();
        }
        replaced_count += plugin_replaced_count;
    }

    Ok((replaced_count, records))
}

fn replace_font_references_in_json_value(
    value: &mut Value,
    old_font_names: &[String],
    replacement_font_name: &str,
    file_name: &str,
    value_path: &str,
    records: &mut Vec<FontReplacementRecord>,
) -> Result<usize> {
    match value {
        Value::String(text) => {
            let original_text = text.clone();
            let (replaced_text, replaced_count) =
                replace_font_names_in_text(&original_text, old_font_names, replacement_font_name)?;
            if replaced_count == 0 {
                return Ok(0);
            }
            *text = replaced_text.clone();
            records.push(FontReplacementRecord {
                file_name: file_name.to_string(),
                value_path: value_path.to_string(),
                original_text,
                replaced_text,
                replacement_font_name: replacement_font_name.to_string(),
            });
            Ok(replaced_count)
        }
        Value::Array(items) => {
            let mut replaced_count = 0usize;
            for (index, item) in items.iter_mut().enumerate() {
                let child_path = append_json_pointer_segment(value_path, &index.to_string());
                replaced_count += replace_font_references_in_json_value(
                    item,
                    old_font_names,
                    replacement_font_name,
                    file_name,
                    &child_path,
                    records,
                )?;
            }
            Ok(replaced_count)
        }
        Value::Object(object) => {
            let mut replaced_count = 0usize;
            for (key, item) in object.iter_mut() {
                let child_path = append_json_pointer_segment(value_path, key);
                replaced_count += replace_font_references_in_json_value(
                    item,
                    old_font_names,
                    replacement_font_name,
                    file_name,
                    &child_path,
                    records,
                )?;
            }
            Ok(replaced_count)
        }
        _ => Ok(0),
    }
}

fn replace_font_references_in_json_value_without_records(
    value: &mut Value,
    old_font_names: &[String],
    replacement_font_name: &str,
) -> Result<usize> {
    match value {
        Value::String(text) => {
            let (replaced_text, replaced_count) =
                replace_font_names_in_text(text, old_font_names, replacement_font_name)?;
            if replaced_count > 0 {
                *text = replaced_text;
            }
            Ok(replaced_count)
        }
        Value::Array(items) => {
            let mut replaced_count = 0usize;
            for item in items {
                replaced_count += replace_font_references_in_json_value_without_records(
                    item,
                    old_font_names,
                    replacement_font_name,
                )?;
            }
            Ok(replaced_count)
        }
        Value::Object(object) => {
            let mut replaced_count = 0usize;
            for item in object.values_mut() {
                replaced_count += replace_font_references_in_json_value_without_records(
                    item,
                    old_font_names,
                    replacement_font_name,
                )?;
            }
            Ok(replaced_count)
        }
        _ => Ok(0),
    }
}

fn replace_font_names_in_text(
    text: &str,
    old_font_names: &[String],
    replacement_font_name: &str,
) -> Result<(String, usize)> {
    if !old_font_names
        .iter()
        .any(|old_font_name| text.contains(old_font_name))
    {
        return Ok((text.to_string(), 0));
    }
    let (replaced_text, replaced_count) =
        replace_complete_font_reference_text(text, old_font_names, replacement_font_name);
    if replaced_count > 0 {
        return Ok((replaced_text, replaced_count));
    }
    replace_font_references_in_encoded_json_text(text, old_font_names, replacement_font_name)
}

fn replace_complete_font_reference_text(
    text: &str,
    old_font_names: &[String],
    replacement_font_name: &str,
) -> (String, usize) {
    let stripped_text = text.trim();
    if stripped_text.is_empty() {
        return (text.to_string(), 0);
    }
    let leading_text = &text[..text.len() - text.trim_start().len()];
    let trailing_text = &text[text.trim_end().len()..];
    for old_font_name in old_font_names {
        if let Some(replaced_reference) = replace_complete_font_reference_core(
            stripped_text,
            old_font_name,
            replacement_font_name,
        ) {
            return (
                format!("{leading_text}{replaced_reference}{trailing_text}"),
                1,
            );
        }
    }
    (text.to_string(), 0)
}

fn replace_complete_font_reference_core(
    text: &str,
    old_font_name: &str,
    replacement_font_name: &str,
) -> Option<String> {
    if text == old_font_name {
        return Some(replacement_font_name.to_string());
    }
    let separator_index = match (text.rfind('/'), text.rfind('\\')) {
        (Some(left), Some(right)) => left.max(right),
        (Some(index), None) | (None, Some(index)) => index,
        (None, None) => return None,
    };
    let reference_name = &text[separator_index + 1..];
    if reference_name != old_font_name {
        return None;
    }
    Some(format!(
        "{}{}",
        &text[..separator_index + 1],
        replacement_font_name
    ))
}

fn replace_font_references_in_encoded_json_text(
    text: &str,
    old_font_names: &[String],
    replacement_font_name: &str,
) -> Result<(String, usize)> {
    let Ok(mut value) = serde_json::from_str::<Value>(text) else {
        return Ok((text.to_string(), 0));
    };
    if !is_json_container(&value) {
        return Ok((text.to_string(), 0));
    }
    let replaced_count = replace_font_references_in_json_value_without_records(
        &mut value,
        old_font_names,
        replacement_font_name,
    )?;
    if replaced_count == 0 {
        return Ok((text.to_string(), 0));
    }
    Ok((serialize_python_style_json(&value), replaced_count))
}

fn append_json_pointer_segment(base_path: &str, segment: &str) -> String {
    format!("{base_path}/{}", escape_json_pointer_segment(segment))
}

fn escape_json_pointer_segment(segment: &str) -> String {
    segment.replace('~', "~0").replace('/', "~1")
}

fn font_restore_summary_report(summary: FontRestoreSummary) -> AgentReport {
    let mut warnings = Vec::new();
    if summary.target_font_name.is_none() {
        warnings.push(issue(
            "font_restore",
            "没有候选覆盖字体名称，无法判断需要还原哪个新字体引用",
        ));
    } else if summary.restored_reference_count == 0 {
        warnings.push(issue("font_restore", "没有找到需要还原的覆盖字体引用"));
    }
    let mut report_summary = Map::new();
    report_summary.insert(
        "restored_field_count".to_string(),
        json!(summary.restored_field_count),
    );
    report_summary.insert(
        "restored_reference_count".to_string(),
        json!(summary.restored_reference_count),
    );
    report_summary.insert(
        "target_font_name".to_string(),
        json!(summary.target_font_name.unwrap_or_default()),
    );
    AgentReport::from_parts(Vec::new(), warnings, report_summary, Map::new())
}

fn restore_font_references_from_origin_backups(
    game_root: &Path,
    replacement_font_names: &[String],
) -> Result<OriginFontRestoreSummary> {
    let target_font_names =
        normalize_font_name_list(build_font_reference_tokens(replacement_font_names));
    if target_font_names.is_empty() {
        return Err(AttMzError::InvalidConfig(
            "字体还原缺少候选覆盖字体名称".to_string(),
        ));
    }

    let active_data_dir = game_root.join(DATA_DIRECTORY_NAME);
    let origin_data_dir = game_root.join(DATA_ORIGIN_DIRECTORY_NAME);
    let active_plugins_path = game_root.join(JS_DIRECTORY_NAME).join(PLUGINS_FILE_NAME);
    let origin_plugins_path = game_root
        .join(JS_DIRECTORY_NAME)
        .join(PLUGINS_ORIGIN_FILE_NAME);
    if !origin_data_dir.exists() && !origin_plugins_path.exists() {
        return Err(AttMzError::InvalidConfig(
            "字体还原需要 data_origin 或 plugins_origin.js 原件留档".to_string(),
        ));
    }

    let mut restored_field_count = 0usize;
    let mut restored_reference_count = 0usize;
    if origin_data_dir.exists() {
        if !origin_data_dir.is_dir() {
            return Err(AttMzError::NotDirectory {
                kind: "原件数据留档",
                path: origin_data_dir,
            });
        }
        for origin_file_path in sorted_json_files(&origin_data_dir)? {
            let Some(file_name) = origin_file_path.file_name() else {
                continue;
            };
            let active_file_path = active_data_dir.join(file_name);
            if !active_file_path.exists() {
                return Err(AttMzError::MissingPath {
                    kind: "激活数据文件",
                    path: active_file_path,
                });
            }
            let active_value = read_json_file(&active_file_path)?;
            let origin_value = read_json_file(&origin_file_path)?;
            let (updated_value, field_count, reference_count) =
                restore_font_references_in_json_value_by_origin(
                    active_value,
                    &origin_value,
                    &target_font_names,
                )?;
            if field_count == 0 {
                continue;
            }
            write_json_file(&active_file_path, &updated_value)?;
            restored_field_count += field_count;
            restored_reference_count += reference_count;
        }
    }

    if origin_plugins_path.exists() {
        if !active_plugins_path.exists() {
            return Err(AttMzError::MissingPath {
                kind: "激活插件配置",
                path: active_plugins_path,
            });
        }
        let active_plugins = parse_plugins_js_file(&active_plugins_path)?;
        let origin_plugins = parse_plugins_js_file(&origin_plugins_path)?;
        let (updated_plugins, field_count, reference_count) =
            restore_font_references_in_json_value_by_origin(
                active_plugins,
                &origin_plugins,
                &target_font_names,
            )?;
        if field_count > 0 {
            if !updated_plugins.is_array() {
                return Err(AttMzError::InvalidGame(
                    "字体还原后的插件配置不是数组".to_string(),
                ));
            }
            write_plugins_js_file(&active_plugins_path, &updated_plugins)?;
            restored_field_count += field_count;
            restored_reference_count += reference_count;
        }
    }

    Ok(OriginFontRestoreSummary {
        target_font_names,
        restored_field_count,
        restored_reference_count,
    })
}

fn restore_font_references_in_json_value_by_origin(
    active_value: Value,
    origin_value: &Value,
    target_font_names: &[String],
) -> Result<(Value, usize, usize)> {
    match (active_value, origin_value) {
        (Value::String(active_text), Value::String(origin_text)) => {
            let (restored_text, reference_count) = restore_font_references_in_text_by_origin(
                &active_text,
                origin_text,
                target_font_names,
            )?;
            let field_count = if reference_count > 0 { 1 } else { 0 };
            Ok((Value::String(restored_text), field_count, reference_count))
        }
        (Value::Array(active_items), Value::Array(origin_items)) => {
            let mut restored_items = Vec::with_capacity(active_items.len());
            let mut restored_field_count = 0usize;
            let mut restored_reference_count = 0usize;
            for (index, active_item) in active_items.into_iter().enumerate() {
                let Some(origin_item) = origin_items.get(index) else {
                    restored_items.push(active_item);
                    continue;
                };
                let (restored_item, field_count, reference_count) =
                    restore_font_references_in_json_value_by_origin(
                        active_item,
                        origin_item,
                        target_font_names,
                    )?;
                restored_items.push(restored_item);
                restored_field_count += field_count;
                restored_reference_count += reference_count;
            }
            Ok((
                Value::Array(restored_items),
                restored_field_count,
                restored_reference_count,
            ))
        }
        (Value::Object(active_object), Value::Object(origin_object)) => {
            let mut restored_object = Map::new();
            let mut restored_field_count = 0usize;
            let mut restored_reference_count = 0usize;
            for (key, active_item) in active_object {
                let Some(origin_item) = origin_object.get(&key) else {
                    restored_object.insert(key, active_item);
                    continue;
                };
                let (restored_item, field_count, reference_count) =
                    restore_font_references_in_json_value_by_origin(
                        active_item,
                        origin_item,
                        target_font_names,
                    )?;
                restored_object.insert(key, restored_item);
                restored_field_count += field_count;
                restored_reference_count += reference_count;
            }
            Ok((
                Value::Object(restored_object),
                restored_field_count,
                restored_reference_count,
            ))
        }
        (active_value, _) => Ok((active_value, 0, 0)),
    }
}

fn restore_font_references_in_text_by_origin(
    active_text: &str,
    origin_text: &str,
    target_font_names: &[String],
) -> Result<(String, usize)> {
    let (restored_text, reference_count) =
        restore_complete_font_reference_text(active_text, origin_text, target_font_names);
    if reference_count > 0 {
        return Ok((restored_text, reference_count));
    }
    restore_font_references_in_encoded_json_text(active_text, origin_text, target_font_names)
}

fn restore_complete_font_reference_text(
    active_text: &str,
    origin_text: &str,
    target_font_names: &[String],
) -> (String, usize) {
    let stripped_active_text = active_text.trim();
    if stripped_active_text.is_empty() {
        return (active_text.to_string(), 0);
    }
    let Some(origin_font_reference) = collect_origin_font_reference(origin_text) else {
        return (active_text.to_string(), 0);
    };
    let leading_text = &active_text[..active_text.len() - active_text.trim_start().len()];
    let trailing_text = &active_text[active_text.trim_end().len()..];
    for target_font_name in target_font_names {
        if !is_complete_reference_to_font(stripped_active_text, target_font_name) {
            continue;
        }
        return (
            format!("{leading_text}{origin_font_reference}{trailing_text}"),
            1,
        );
    }
    (active_text.to_string(), 0)
}

fn restore_font_references_in_encoded_json_text(
    active_text: &str,
    origin_text: &str,
    target_font_names: &[String],
) -> Result<(String, usize)> {
    let Ok(active_value) = serde_json::from_str::<Value>(active_text) else {
        return Ok((active_text.to_string(), 0));
    };
    let Ok(origin_value) = serde_json::from_str::<Value>(origin_text) else {
        return Ok((active_text.to_string(), 0));
    };
    if !is_json_container(&active_value) || !is_json_container(&origin_value) {
        return Ok((active_text.to_string(), 0));
    }
    let (restored_value, _field_count, reference_count) =
        restore_font_references_in_json_value_by_origin(
            active_value,
            &origin_value,
            target_font_names,
        )?;
    if reference_count == 0 {
        return Ok((active_text.to_string(), 0));
    }
    Ok((
        serialize_python_style_json(&restored_value),
        reference_count,
    ))
}

fn collect_origin_font_reference(text: &str) -> Option<String> {
    let stripped_text = text.trim();
    if stripped_text.is_empty() {
        return None;
    }
    if is_complete_font_file_reference(stripped_text)
        || is_complete_bare_font_reference(stripped_text)
    {
        Some(stripped_text.to_string())
    } else {
        None
    }
}

fn is_complete_reference_to_font(text: &str, font_name: &str) -> bool {
    text == font_name || extract_font_reference_name(text) == font_name
}

fn is_complete_font_file_reference(text: &str) -> bool {
    let reference_name = extract_font_reference_name(text);
    let lower_reference_name = reference_name.to_ascii_lowercase();
    let has_supported_suffix = FONT_FILE_SUFFIXES.iter().any(|suffix| {
        lower_reference_name.ends_with(suffix) && reference_name.len() > suffix.len()
    });
    has_supported_suffix && reference_name.chars().all(is_font_file_reference_char)
}

fn is_complete_bare_font_reference(text: &str) -> bool {
    let reference_name = extract_font_reference_name(text);
    !reference_name.is_empty()
        && reference_name.len() <= 128
        && reference_name.chars().all(|char_value| {
            char_value.is_ascii_alphanumeric() || matches!(char_value, '_' | ' ' | '.' | '+' | '-')
        })
}

fn is_font_file_reference_char(char_value: char) -> bool {
    char_value.is_alphanumeric()
        || matches!(char_value, '_' | ' ' | '.' | '+' | '-')
        || ('\u{0080}'..='\u{ffff}').contains(&char_value)
}

fn extract_font_reference_name(text: &str) -> &str {
    match (text.rfind('/'), text.rfind('\\')) {
        (Some(left), Some(right)) => {
            let index = left.max(right);
            &text[index + 1..]
        }
        (Some(index), None) | (None, Some(index)) => &text[index + 1..],
        (None, None) => text,
    }
}

fn build_font_reference_tokens(font_names: &[String]) -> Vec<String> {
    let mut tokens = BTreeSet::new();
    for font_name in font_names {
        let normalized_font_name = font_name.trim();
        if normalized_font_name.is_empty() {
            continue;
        }
        tokens.insert(normalized_font_name.to_string());
        if let Some(stem) = font_stem(normalized_font_name) {
            tokens.insert(stem);
        }
    }
    let mut sorted_tokens = tokens.into_iter().collect::<Vec<_>>();
    sorted_tokens.sort_by(|left, right| right.len().cmp(&left.len()).then_with(|| left.cmp(right)));
    sorted_tokens
}

fn font_stem(font_name: &str) -> Option<String> {
    let reference_name = extract_font_reference_name(font_name);
    let (stem, _) = reference_name.rsplit_once('.')?;
    let trimmed_stem = stem.trim();
    if trimmed_stem.is_empty() {
        None
    } else {
        Some(trimmed_stem.to_string())
    }
}

fn normalize_font_name_list(font_names: Vec<String>) -> Vec<String> {
    let mut normalized_names = Vec::new();
    let mut seen_names = BTreeSet::new();
    for font_name in font_names {
        let normalized_name = font_name.trim();
        if normalized_name.is_empty() || seen_names.contains(normalized_name) {
            continue;
        }
        normalized_names.push(normalized_name.to_string());
        seen_names.insert(normalized_name.to_string());
    }
    normalized_names
}

fn file_name_text(path_text: &str) -> String {
    extract_font_reference_name(path_text).to_string()
}

fn sorted_json_files(directory: &Path) -> Result<Vec<PathBuf>> {
    let mut paths = Vec::new();
    for entry in fs::read_dir(directory)
        .map_err(|source| AttMzError::io(format!("扫描目录 {}", directory.display()), source))?
    {
        let entry = entry.map_err(|source| AttMzError::io("读取目录项", source))?;
        let path = entry.path();
        if path.is_file() && path.extension().and_then(|value| value.to_str()) == Some("json") {
            paths.push(path);
        }
    }
    paths.sort_by(|left, right| left.file_name().cmp(&right.file_name()));
    Ok(paths)
}

fn read_active_data_json_files(game_root: &Path) -> Result<BTreeMap<String, Value>> {
    let active_data_dir = game_root.join(DATA_DIRECTORY_NAME);
    if !active_data_dir.exists() {
        return Err(AttMzError::MissingPath {
            kind: "激活数据目录",
            path: active_data_dir,
        });
    }
    if !active_data_dir.is_dir() {
        return Err(AttMzError::NotDirectory {
            kind: "激活数据目录",
            path: active_data_dir,
        });
    }
    let mut files = BTreeMap::new();
    for path in sorted_json_files(&active_data_dir)? {
        let Some(file_name) = path.file_name().and_then(|value| value.to_str()) else {
            continue;
        };
        files.insert(file_name.to_string(), read_json_file(&path)?);
    }
    Ok(files)
}

fn write_font_replacement_outputs(
    game_root: &Path,
    source_data: &BTreeMap<String, Value>,
    writable_data: &BTreeMap<String, Value>,
    source_plugins: &[Value],
    writable_plugins: &[Value],
) -> Result<()> {
    let changed_data_files = writable_data
        .iter()
        .filter(|(file_name, writable_value)| source_data.get(*file_name) != Some(*writable_value))
        .map(|(file_name, _writable_value)| file_name.clone())
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

    if !changed_data_files.is_empty() {
        fs::create_dir_all(&origin_data_dir).map_err(|source| {
            AttMzError::io(
                format!("创建原件留档目录 {}", origin_data_dir.display()),
                source,
            )
        })?;
        for file_name in &changed_data_files {
            let source_path = active_data_dir.join(file_name);
            let target_path = origin_data_dir.join(file_name);
            if target_path.exists() {
                continue;
            }
            fs::copy(&source_path, &target_path).map_err(|source| {
                AttMzError::io(
                    format!("备份原始 data 文件 {}", source_path.display()),
                    source,
                )
            })?;
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
        let Some(value) = writable_data.get(file_name) else {
            return Err(AttMzError::InvalidConfig(format!(
                "待写回 data 文件不存在: {file_name}"
            )));
        };
        write_json_file(&active_data_dir.join(file_name), value)?;
    }
    if plugins_changed {
        write_plugins_js_file(
            &active_plugins_path,
            &Value::Array(writable_plugins.to_vec()),
        )?;
    }
    Ok(())
}

fn parse_plugins_js_file(path: &Path) -> Result<Value> {
    let content = fs::read_to_string(path)
        .map_err(|source| AttMzError::io(format!("读取插件配置 {}", path.display()), source))?;
    let pattern = Regex::new(r#"(?s)var\s+\$plugins\s*=\s*(\[.*?\])\s*;\s*$"#)
        .map_err(|error| AttMzError::InvalidGame(format!("插件解析正则不可用: {error}")))?;
    let captures = pattern.captures(&content).ok_or_else(|| {
        AttMzError::InvalidGame("plugins.js 中未找到标准 $plugins 数组".to_string())
    })?;
    let plugins_text = captures
        .get(1)
        .map(|matched| matched.as_str())
        .ok_or_else(|| AttMzError::InvalidGame("plugins.js 中的 $plugins 数组为空".to_string()))?;
    let plugins: Value = serde_json::from_str(plugins_text).map_err(|source| AttMzError::Json {
        context: path.display().to_string(),
        source,
    })?;
    if !plugins.is_array() {
        return Err(AttMzError::InvalidGame(
            "plugins.js 中的 $plugins 必须是数组".to_string(),
        ));
    }
    Ok(plugins)
}

fn write_plugins_js_file(path: &Path, value: &Value) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|source| AttMzError::io(format!("创建目录 {}", parent.display()), source))?;
    }
    let plugins_text = serde_json::to_string_pretty(value).map_err(|source| AttMzError::Json {
        context: format!("序列化插件配置 {}", path.display()),
        source,
    })?;
    fs::write(path, format!("var $plugins = {plugins_text};\n"))
        .map_err(|source| AttMzError::io(format!("写入插件配置 {}", path.display()), source))
}

fn read_json_file(path: &Path) -> Result<Value> {
    let text = fs::read_to_string(path)
        .map_err(|source| AttMzError::io(format!("读取 JSON 文件 {}", path.display()), source))?;
    serde_json::from_str(text.trim_start_matches('\u{feff}')).map_err(|source| AttMzError::Json {
        context: path.display().to_string(),
        source,
    })
}

fn write_json_file(path: &Path, value: &Value) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|source| AttMzError::io(format!("创建目录 {}", parent.display()), source))?;
    }
    let text = serde_json::to_string_pretty(value).map_err(|source| AttMzError::Json {
        context: format!("序列化 JSON {}", path.display()),
        source,
    })?;
    fs::write(path, format!("{text}\n"))
        .map_err(|source| AttMzError::io(format!("写入 JSON 文件 {}", path.display()), source))
}

fn is_json_container(value: &Value) -> bool {
    value.is_array() || value.is_object()
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::GameRegistry;

    #[test]
    fn apply_font_replacement_updates_only_writable_outputs() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        let game = create_font_test_game(temp.path(), "FontApply");
        let fonts_dir = game.join("fonts");
        fs::create_dir_all(&fonts_dir).expect("字体目录应创建成功");
        fs::write(fonts_dir.join("OldFont.woff"), b"old font").expect("旧字体应写入成功");
        fs::write(fonts_dir.join("AnotherFont.woff"), b"another font")
            .expect("另一个旧字体应写入成功");
        let replacement_font = temp.path().join("NotoSansSC-Regular.ttf");
        fs::write(&replacement_font, b"new font").expect("新字体应写入成功");

        let mut writable_data = BTreeMap::from([(
            "System.json".to_string(),
            json!({
                "advanced": {
                    "mainFontFilename": "OldFont.woff",
                    "numberFontFilename": "AnotherFont.woff"
                },
                "gameTitle": "原始标题"
            }),
        )]);
        let mut writable_plugins = vec![json!({
            "name": "FontPlugin",
            "status": true,
            "description": "",
            "parameters": {
                "FontFace": "OldFont.woff",
                "FontStem": "OldFont",
                "Nested": "{\"font\":\"AnotherFont.woff\",\"text\":\"プラグイン本文\"}",
                "HelpText": "请在设置中选择 OldFont 字体。"
            }
        })];
        let replacement_font_text = replacement_font.to_string_lossy().to_string();

        let summary = apply_font_replacement_to_writable_outputs(
            &game,
            &mut writable_data,
            &mut writable_plugins,
            Some(&replacement_font_text),
        )
        .expect("字体覆盖应执行成功");

        assert!(fonts_dir.join("NotoSansSC-Regular.ttf").exists());
        assert_eq!(
            summary.target_font_name,
            Some("NotoSansSC-Regular.ttf".to_string())
        );
        assert_eq!(summary.source_font_count, 2);
        assert_eq!(summary.replaced_reference_count, 5);
        assert_eq!(summary.records.len(), 5);
        let system = writable_data
            .get("System.json")
            .expect("System.json 应存在");
        assert_eq!(
            system["advanced"]["mainFontFilename"],
            json!("NotoSansSC-Regular.ttf")
        );
        assert_eq!(
            system["advanced"]["numberFontFilename"],
            json!("NotoSansSC-Regular.ttf")
        );
        let parameters = &writable_plugins[0]["parameters"];
        assert_eq!(parameters["FontFace"], json!("NotoSansSC-Regular.ttf"));
        assert_eq!(parameters["FontStem"], json!("NotoSansSC-Regular.ttf"));
        let nested_text = parameters["Nested"].as_str().expect("嵌套 JSON 应是字符串");
        let nested_value: Value = serde_json::from_str(nested_text).expect("嵌套 JSON 应可解析");
        assert_eq!(nested_value["font"], json!("NotoSansSC-Regular.ttf"));
        assert_eq!(nested_value["text"], json!("プラグイン本文"));
        assert_eq!(
            parameters["HelpText"],
            json!("请在设置中选择 OldFont 字体。")
        );
    }

    #[test]
    fn restore_font_uses_origin_backups_without_rolling_back_text() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        let game = create_font_test_game(temp.path(), "FontRestore");
        let registry = GameRegistry {
            db_directory: temp.path().join("db"),
        };
        let game_record = registry.register_game(&game).expect("游戏应注册成功");
        registry
            .replace_font_replacement_records(
                &game_record.game_title,
                &[FontReplacementRecord {
                    file_name: "System.json".to_string(),
                    value_path: "/advanced/mainFontFilename".to_string(),
                    original_text: "OldFont.woff".to_string(),
                    replaced_text: "NotoSansSC-Regular.ttf".to_string(),
                    replacement_font_name: "NotoSansSC-Regular.ttf".to_string(),
                }],
            )
            .expect("字体覆盖记录应写入成功");

        let report =
            restore_font_report(&registry, &game_record, None).expect("字体还原应执行成功");

        assert_eq!(report.summary["restored_field_count"], json!(5));
        assert_eq!(report.summary["restored_reference_count"], json!(5));
        assert_eq!(
            registry
                .read_font_replacement_records(&game_record.game_title)
                .expect("字体覆盖记录应读取成功"),
            Vec::new()
        );
        let system = read_json_file(&game.join("data/System.json")).expect("系统文件应读取成功");
        assert_eq!(
            system["advanced"]["mainFontFilename"],
            json!("OldFont.woff")
        );
        assert_eq!(
            system["advanced"]["numberFontFilename"],
            json!("AnotherFont.woff")
        );
        assert_eq!(system["gameTitle"], json!("已有中文标题"));
        let plugins = parse_plugins_js_file(&game.join("js/plugins.js")).expect("插件应读取成功");
        let parameters = &plugins[0]["parameters"];
        assert_eq!(parameters["FontFace"], json!("OldFont.woff"));
        assert_eq!(parameters["FontStem"], json!("OldFont"));
        let nested_text = parameters["Nested"].as_str().expect("嵌套 JSON 应是字符串");
        let nested_value: Value = serde_json::from_str(nested_text).expect("嵌套 JSON 应可解析");
        assert_eq!(nested_value["font"], json!("AnotherFont.woff"));
        assert_eq!(
            parameters["HelpText"],
            json!("请在设置中选择 NotoSansSC-Regular.ttf 字体。")
        );
    }

    #[test]
    fn restore_font_reports_warning_without_target_names() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        let game = create_font_test_game(temp.path(), "FontNoTarget");
        let registry = GameRegistry {
            db_directory: temp.path().join("db"),
        };
        let game_record = registry.register_game(&game).expect("游戏应注册成功");

        let report =
            restore_font_report(&registry, &game_record, None).expect("空候选应返回告警报告");

        assert_eq!(report.summary["target_font_name"], json!(""));
        assert_eq!(report.warnings[0].code, "font_restore");
    }

    fn create_font_test_game(root: &Path, title: &str) -> PathBuf {
        let game = root.join("game");
        fs::create_dir_all(game.join("data")).expect("data 目录应创建成功");
        fs::create_dir_all(game.join("data_origin")).expect("data_origin 目录应创建成功");
        fs::create_dir_all(game.join("js")).expect("js 目录应创建成功");
        fs::write(
            game.join("package.json"),
            json!({"window": {"title": title}}).to_string(),
        )
        .expect("package.json 应写入成功");
        write_json_file(
            &game.join("data_origin/System.json"),
            &json!({
                "advanced": {
                    "mainFontFilename": "OldFont.woff",
                    "numberFontFilename": "AnotherFont.woff"
                },
                "gameTitle": "原始日文标题"
            }),
        )
        .expect("System 原件应写入成功");
        write_json_file(
            &game.join("data/System.json"),
            &json!({
                "advanced": {
                    "mainFontFilename": "NotoSansSC-Regular.ttf",
                    "numberFontFilename": "NotoSansSC-Regular.ttf"
                },
                "gameTitle": "已有中文标题"
            }),
        )
        .expect("System 当前文件应写入成功");
        fs::write(game.join("data/CommonEvents.json"), "[]").expect("CommonEvents.json 应写入成功");
        fs::write(game.join("data/Troops.json"), "[]").expect("Troops.json 应写入成功");
        fs::write(
            game.join("js/plugins_origin.js"),
            format!(
                "var $plugins = {};\n",
                serde_json::to_string_pretty(&json!([{
                    "name": "FontPlugin",
                    "status": true,
                    "description": "",
                    "parameters": {
                        "FontFace": "OldFont.woff",
                        "FontStem": "OldFont",
                        "Nested": "{\"font\":\"AnotherFont.woff\",\"text\":\"プラグイン本文\"}",
                        "HelpText": "请在设置中选择 OldFont.woff 字体。"
                    }
                }]))
                .expect("插件原件应可序列化")
            ),
        )
        .expect("插件原件应写入成功");
        fs::write(
            game.join("js/plugins.js"),
            format!(
                "var $plugins = {};\n",
                serde_json::to_string_pretty(&json!([{
                    "name": "FontPlugin",
                    "status": true,
                    "description": "",
                    "parameters": {
                        "FontFace": "NotoSansSC-Regular.ttf",
                        "FontStem": "NotoSansSC-Regular",
                        "Nested": "{\"font\":\"NotoSansSC-Regular.ttf\",\"text\":\"插件正文\"}",
                        "HelpText": "请在设置中选择 NotoSansSC-Regular.ttf 字体。"
                    }
                }]))
                .expect("插件当前文件应可序列化")
            ),
        )
        .expect("插件当前文件应写入成功");
        game
    }
}
