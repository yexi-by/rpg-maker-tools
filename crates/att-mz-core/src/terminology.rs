//! 术语表导出、读取、校验和导入报告。
//!
//! 字段译名表只负责稳定字段写回，正文术语表只负责翻译提示词命中。本模块
//! 明确保持两份文件的职责拆分，并在导入时按当前游戏可提取术语做结构校验。

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{Map, Value, json};

use crate::error::{AttMzError, Result};
use crate::report::{AgentReport, issue};
use crate::rmmz::{EventCommandSnapshot, read_data_json_files, read_event_command_snapshots};
use crate::{GameRecord, GameRegistry};

/// 字段译名表的固定类别顺序。
pub const TERMINOLOGY_CATEGORIES: &[&str] = &[
    "speaker_names",
    "map_display_names",
    "actor_names",
    "actor_nicknames",
    "class_names",
    "skill_names",
    "item_names",
    "weapon_names",
    "armor_names",
    "enemy_names",
    "state_names",
    "system_elements",
    "system_skill_types",
    "system_weapon_types",
    "system_armor_types",
    "system_equip_types",
];

const BASE_NAME_CATEGORIES: &[(&str, &str)] = &[
    ("Actors.json", "actor_names"),
    ("Classes.json", "class_names"),
    ("Skills.json", "skill_names"),
    ("Items.json", "item_names"),
    ("Weapons.json", "weapon_names"),
    ("Armors.json", "armor_names"),
    ("Enemies.json", "enemy_names"),
    ("States.json", "state_names"),
];

const SYSTEM_TERM_CATEGORIES: &[(&str, &str)] = &[
    ("elements", "system_elements"),
    ("skillTypes", "system_skill_types"),
    ("weaponTypes", "system_weapon_types"),
    ("armorTypes", "system_armor_types"),
    ("equipTypes", "system_equip_types"),
];

/// 当前游戏可导出的术语表工程内容。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TerminologyArtifacts {
    /// 字段译名表，类别到“原文 -> 译名”的映射。
    pub registry: BTreeMap<String, BTreeMap<String, String>>,
    /// 名字框对白上下文。
    pub speaker_contexts: Vec<Value>,
    /// 数据库字段上下文。
    pub database_contexts: Vec<Value>,
    /// 名字框条目数量。
    pub speaker_entry_count: usize,
    /// 地图显示名条目数量。
    pub map_entry_count: usize,
    /// 数据库字段术语数量。
    pub database_entry_count: usize,
}

/// 术语表导入结果。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TerminologyImportResult {
    /// 字段译名表全部条目数量。
    pub imported_entry_count: usize,
    /// 已填写译名的字段条目数量。
    pub filled_entry_count: usize,
    /// 正文术语表条目数量。
    pub glossary_term_count: usize,
}

/// 导出字段译名表、正文术语表和只读上下文。
pub fn export_terminology_report(
    game_record: &GameRecord,
    output_dir: &Path,
) -> Result<AgentReport> {
    let target_dir = absolute_path(output_dir);
    fs::create_dir_all(&target_dir).map_err(|source| {
        AttMzError::io(format!("创建术语表目录 {}", target_dir.display()), source)
    })?;
    let data_files = read_data_json_files(&game_record.game_path)?;
    let command_snapshots = read_event_command_snapshots(&game_record.game_path)?;
    let artifacts = extract_terminology(&data_files, &command_snapshots);
    write_terminology_artifacts(&target_dir, &artifacts.registry, &artifacts)?;
    let glossary_path = target_dir.join("glossary.json");
    write_json_file(&glossary_path, &json!({ "terms": {} }))?;

    let summary = terminology_export_summary(&target_dir, &artifacts, &glossary_path);
    Ok(AgentReport::from_parts(
        Vec::new(),
        Vec::new(),
        summary,
        Map::new(),
    ))
}

/// 导入字段译名表和正文术语表，校验通过后写入当前游戏数据库。
pub fn import_terminology_report(
    registry: &GameRegistry,
    game_record: &GameRecord,
    input_path: &Path,
    glossary_input_path: &Path,
) -> Result<AgentReport> {
    let data_files = read_data_json_files(&game_record.game_path)?;
    let command_snapshots = read_event_command_snapshots(&game_record.game_path)?;
    let expected_registry = extract_terminology(&data_files, &command_snapshots).registry;
    let imported_registry = read_terminology_registry_file(input_path)?;
    let glossary = read_glossary_file(glossary_input_path)?;
    validate_terminology_registry_shape(&imported_registry, &expected_registry)?;
    registry.replace_terminology(&game_record.game_title, &imported_registry, &glossary)?;

    let result = TerminologyImportResult {
        imported_entry_count: registry_entry_count(&imported_registry),
        filled_entry_count: terminology_filled_count(&imported_registry),
        glossary_term_count: glossary.len(),
    };
    let mut summary = Map::new();
    summary.insert("game".to_string(), json!(game_record.game_title));
    summary.insert("input".to_string(), json!(input_path.display().to_string()));
    summary.insert(
        "glossary_input".to_string(),
        json!(glossary_input_path.display().to_string()),
    );
    summary.insert(
        "imported_entry_count".to_string(),
        json!(result.imported_entry_count),
    );
    summary.insert(
        "filled_entry_count".to_string(),
        json!(result.filled_entry_count),
    );
    summary.insert(
        "glossary_term_count".to_string(),
        json!(result.glossary_term_count),
    );
    Ok(AgentReport::from_parts(
        Vec::new(),
        Vec::new(),
        summary,
        Map::new(),
    ))
}

/// 根据数据库中的字段译名表直接写回稳定名词。
pub fn write_terminology_report(
    registry: &GameRegistry,
    game_record: &GameRecord,
) -> Result<AgentReport> {
    let Some(terminology_registry) = registry.read_terminology_registry(&game_record.game_title)?
    else {
        return Err(AttMzError::InvalidConfig(
            "当前游戏数据库中没有已导入术语表，请先执行 import-terminology".to_string(),
        ));
    };
    let source_data_files = read_data_json_files(&game_record.game_path)?;
    let mut writable_data_files = read_active_data_json_files(&game_record.game_path)?;
    let original_files = writable_data_files.clone();
    let written_count = apply_terminology_translations_from_source(
        &source_data_files,
        &mut writable_data_files,
        &terminology_registry,
    )?;
    let changed_file_names = changed_data_file_names(&original_files, &writable_data_files);
    write_changed_data_files(
        &game_record.game_path,
        &writable_data_files,
        &changed_file_names,
    )?;

    let mut summary = Map::new();
    summary.insert("written_count".to_string(), json!(written_count));
    summary.insert(
        "changed_file_count".to_string(),
        json!(changed_file_names.len()),
    );
    summary.insert("changed_files".to_string(), json!(changed_file_names));
    Ok(AgentReport::from_parts(
        Vec::new(),
        Vec::new(),
        summary,
        Map::new(),
    ))
}

/// 构建术语表导入失败报告。
pub fn terminology_invalid_report(
    game_title: &str,
    input_path: &Path,
    glossary_input_path: &Path,
    message: String,
) -> AgentReport {
    let mut summary = Map::new();
    summary.insert("game".to_string(), json!(game_title));
    summary.insert("input".to_string(), json!(input_path.display().to_string()));
    summary.insert(
        "glossary_input".to_string(),
        json!(glossary_input_path.display().to_string()),
    );
    AgentReport::from_parts(
        vec![issue(
            "terminology_invalid",
            format!("术语表导入失败: {message}"),
        )],
        Vec::new(),
        summary,
        Map::new(),
    )
}

/// 从当前游戏数据提取字段术语和上下文。
pub fn extract_terminology(
    data_files: &BTreeMap<String, Value>,
    command_snapshots: &[EventCommandSnapshot],
) -> TerminologyArtifacts {
    let mut registry = empty_terminology_registry();
    let speaker_contexts = collect_speaker_contexts(command_snapshots);
    for context in &speaker_contexts {
        if let Some(name) = context.get("name").and_then(Value::as_str) {
            insert_term(&mut registry, "speaker_names", name);
        }
    }
    let map_names = collect_map_display_names(data_files);
    for map_name in &map_names {
        insert_term(&mut registry, "map_display_names", map_name);
    }
    let database_contexts = collect_database_terms(data_files, &mut registry);
    let speaker_entry_count = speaker_contexts.len();
    let database_entry_count = registry_entry_count(&registry)
        .saturating_sub(speaker_entry_count)
        .saturating_sub(map_names.len());
    TerminologyArtifacts {
        registry,
        speaker_contexts,
        database_contexts,
        speaker_entry_count,
        map_entry_count: map_names.len(),
        database_entry_count,
    }
}

/// 生成包含全部固定类别的空字段译名表。
pub fn empty_terminology_registry() -> BTreeMap<String, BTreeMap<String, String>> {
    TERMINOLOGY_CATEGORIES
        .iter()
        .map(|category| ((*category).to_string(), BTreeMap::new()))
        .collect()
}

/// 读取字段译名表 JSON 文件。
pub fn read_terminology_registry_file(
    path: &Path,
) -> Result<BTreeMap<String, BTreeMap<String, String>>> {
    if !path.exists() {
        return Err(AttMzError::InvalidConfig(format!(
            "术语表导入文件不存在: {}",
            path.display()
        )));
    }
    let value = read_json_file(path, "字段译名表 JSON")?;
    let Some(object) = value.as_object() else {
        return Err(AttMzError::InvalidConfig(
            "字段译名表顶层必须是对象".to_string(),
        ));
    };
    validate_terms_json_category_keys(object, path)?;
    let mut registry = BTreeMap::new();
    for category in TERMINOLOGY_CATEGORIES {
        let Some(entries) = object.get(*category).and_then(Value::as_object) else {
            return Err(AttMzError::InvalidConfig(format!("{category} 必须是对象")));
        };
        let mut normalized_entries = BTreeMap::new();
        for (source_text, translated_text) in entries {
            if source_text.trim().is_empty() {
                return Err(AttMzError::InvalidConfig(format!(
                    "{category} 不能包含空原文"
                )));
            }
            let Some(translated_text) = translated_text.as_str() else {
                return Err(AttMzError::InvalidConfig(format!(
                    "{category}.{source_text} 的译名必须是字符串"
                )));
            };
            normalized_entries.insert(source_text.clone(), translated_text.to_string());
        }
        registry.insert((*category).to_string(), normalized_entries);
    }
    Ok(registry)
}

/// 读取正文术语表 JSON 文件。
pub fn read_glossary_file(path: &Path) -> Result<BTreeMap<String, String>> {
    if !path.exists() {
        return Err(AttMzError::InvalidConfig(format!(
            "正文术语表导入文件不存在: {}",
            path.display()
        )));
    }
    let value = read_json_file(path, "正文术语表 JSON")?;
    let Some(object) = value.as_object() else {
        return Err(AttMzError::InvalidConfig(
            "正文术语表顶层必须是对象".to_string(),
        ));
    };
    if object.len() != 1 || !object.contains_key("terms") {
        return Err(AttMzError::InvalidConfig(
            "正文术语表必须只包含 terms 字段".to_string(),
        ));
    }
    let Some(terms) = object.get("terms").and_then(Value::as_object) else {
        return Err(AttMzError::InvalidConfig(
            "正文术语表 terms 必须是对象".to_string(),
        ));
    };
    let mut glossary = BTreeMap::new();
    for (source_text, translated_text) in terms {
        let normalized_source = source_text.trim();
        if normalized_source.is_empty() {
            return Err(AttMzError::InvalidConfig(
                "terms 不能包含空原文".to_string(),
            ));
        }
        let Some(normalized_translation) = translated_text.as_str().map(str::trim) else {
            return Err(AttMzError::InvalidConfig(format!(
                "terms.{normalized_source} 的译名必须是字符串"
            )));
        };
        if normalized_translation.is_empty() {
            return Err(AttMzError::InvalidConfig(format!(
                "terms.{normalized_source} 不能包含空值"
            )));
        }
        if glossary
            .insert(
                normalized_source.to_string(),
                normalized_translation.to_string(),
            )
            .is_some()
        {
            return Err(AttMzError::InvalidConfig(format!(
                "terms 清理首尾空白后存在重复原文: {normalized_source}"
            )));
        }
    }
    Ok(glossary)
}

/// 校验导入字段译名表和当前游戏可提取术语完全一致。
pub fn validate_terminology_registry_shape(
    imported_registry: &BTreeMap<String, BTreeMap<String, String>>,
    expected_registry: &BTreeMap<String, BTreeMap<String, String>>,
) -> Result<()> {
    let mut errors = Vec::new();
    for category in TERMINOLOGY_CATEGORIES {
        let imported_entries = imported_registry
            .get(*category)
            .cloned()
            .unwrap_or_default();
        let expected_entries = expected_registry
            .get(*category)
            .cloned()
            .unwrap_or_default();
        let imported_keys = imported_entries.keys().cloned().collect::<BTreeSet<_>>();
        let expected_keys = expected_entries.keys().cloned().collect::<BTreeSet<_>>();
        let missing_count = expected_keys.difference(&imported_keys).count();
        let extra_count = imported_keys.difference(&expected_keys).count();
        if missing_count > 0 {
            errors.push(format!("{category} 缺少 {missing_count} 个术语"));
        }
        if extra_count > 0 {
            errors.push(format!("{category} 多出 {extra_count} 个术语"));
        }
    }
    if errors.is_empty() {
        Ok(())
    } else {
        Err(AttMzError::InvalidConfig(errors.join("；")))
    }
}

/// 统计字段译名表全部条目数量。
pub fn registry_entry_count(registry: &BTreeMap<String, BTreeMap<String, String>>) -> usize {
    registry.values().map(BTreeMap::len).sum()
}

/// 统计已经填写译名的字段条目数量。
pub fn terminology_filled_count(registry: &BTreeMap<String, BTreeMap<String, String>>) -> usize {
    registry
        .values()
        .flat_map(BTreeMap::values)
        .filter(|translated_text| !translated_text.trim().is_empty())
        .count()
}

/// 把字段译名表写入可变游戏数据，返回实际改写的字段数量。
pub fn apply_terminology_translations(
    data_files: &mut BTreeMap<String, Value>,
    registry: &BTreeMap<String, BTreeMap<String, String>>,
) -> Result<usize> {
    let source_data_files = data_files.clone();
    apply_terminology_translations_from_source(&source_data_files, data_files, registry)
}

pub(crate) fn apply_terminology_translations_from_source(
    source_data_files: &BTreeMap<String, Value>,
    writable_data_files: &mut BTreeMap<String, Value>,
    registry: &BTreeMap<String, BTreeMap<String, String>>,
) -> Result<usize> {
    let mut written_count = 0usize;
    written_count += write_map_display_names(
        source_data_files,
        writable_data_files,
        category_translations(registry, "map_display_names"),
    )?;
    written_count += write_speaker_names(
        source_data_files,
        writable_data_files,
        category_translations(registry, "speaker_names"),
    )?;
    written_count += write_base_database_terms(source_data_files, writable_data_files, registry)?;
    written_count += write_system_terms(source_data_files, writable_data_files, registry)?;
    Ok(written_count)
}

fn write_map_display_names(
    source_data_files: &BTreeMap<String, Value>,
    writable_data_files: &mut BTreeMap<String, Value>,
    translations: BTreeMap<String, String>,
) -> Result<usize> {
    if translations.is_empty() {
        return Ok(0);
    }
    let mut written_count = 0usize;
    for (file_name, source_value) in source_data_files {
        if !is_map_file_name(file_name) {
            continue;
        }
        let Some(source_map_object) = source_value.as_object() else {
            return Err(AttMzError::InvalidConfig(format!(
                "{file_name} 顶层必须是对象"
            )));
        };
        let Some(display_name) = source_map_object.get("displayName").and_then(Value::as_str)
        else {
            continue;
        };
        if !is_translatable_terminology_source(display_name) {
            continue;
        }
        let Some(translated_text) = translations.get(display_name.trim()) else {
            continue;
        };
        let Some(writable_map_object) = writable_data_files
            .get_mut(file_name)
            .and_then(Value::as_object_mut)
        else {
            return Err(AttMzError::InvalidConfig(format!(
                "{file_name} 顶层必须是对象"
            )));
        };
        writable_map_object.insert("displayName".to_string(), json!(translated_text));
        written_count += 1;
    }
    Ok(written_count)
}

fn write_speaker_names(
    source_data_files: &BTreeMap<String, Value>,
    writable_data_files: &mut BTreeMap<String, Value>,
    translations: BTreeMap<String, String>,
) -> Result<usize> {
    if translations.is_empty() {
        return Ok(0);
    }
    let mut written_count = 0usize;
    for (file_name, source_value) in source_data_files {
        let Some(writable_value) = writable_data_files.get_mut(file_name) else {
            continue;
        };
        if is_map_file_name(file_name) {
            written_count +=
                write_map_speaker_names(file_name, source_value, writable_value, &translations)?;
            continue;
        }
        if file_name == "CommonEvents.json" {
            written_count +=
                write_common_event_speaker_names(source_value, writable_value, &translations)?;
            continue;
        }
        if file_name == "Troops.json" {
            written_count +=
                write_troop_speaker_names(source_value, writable_value, &translations)?;
        }
    }
    Ok(written_count)
}

fn write_map_speaker_names(
    file_name: &str,
    source_value: &Value,
    writable_value: &mut Value,
    translations: &BTreeMap<String, String>,
) -> Result<usize> {
    let Some(source_map_object) = source_value.as_object() else {
        return Err(AttMzError::InvalidConfig(format!(
            "{file_name} 顶层必须是对象"
        )));
    };
    let Some(writable_map_object) = writable_value.as_object_mut() else {
        return Err(AttMzError::InvalidConfig(format!(
            "{file_name} 顶层必须是对象"
        )));
    };
    let Some(source_events) = source_map_object.get("events").and_then(Value::as_array) else {
        return Ok(0);
    };
    let Some(writable_events) = writable_map_object
        .get_mut("events")
        .and_then(Value::as_array_mut)
    else {
        return Ok(0);
    };
    let mut written_count = 0usize;
    for (source_event, writable_event) in source_events.iter().zip(writable_events.iter_mut()) {
        if source_event.is_null() {
            continue;
        }
        let Some(source_event_object) = source_event.as_object() else {
            continue;
        };
        let Some(writable_event_object) = writable_event.as_object_mut() else {
            continue;
        };
        let Some(source_pages) = source_event_object.get("pages").and_then(Value::as_array) else {
            continue;
        };
        let Some(writable_pages) = writable_event_object
            .get_mut("pages")
            .and_then(Value::as_array_mut)
        else {
            continue;
        };
        for (source_page, writable_page) in source_pages.iter().zip(writable_pages.iter_mut()) {
            let Some(source_page_object) = source_page.as_object() else {
                continue;
            };
            let Some(writable_page_object) = writable_page.as_object_mut() else {
                continue;
            };
            let Some(source_commands) = source_page_object.get("list").and_then(Value::as_array)
            else {
                continue;
            };
            let Some(writable_commands) = writable_page_object
                .get_mut("list")
                .and_then(Value::as_array_mut)
            else {
                continue;
            };
            written_count +=
                write_speaker_names_to_commands(source_commands, writable_commands, translations);
        }
    }
    Ok(written_count)
}

fn write_common_event_speaker_names(
    source_value: &Value,
    writable_value: &mut Value,
    translations: &BTreeMap<String, String>,
) -> Result<usize> {
    let Some(source_events) = source_value.as_array() else {
        return Err(AttMzError::InvalidConfig(
            "CommonEvents.json 顶层必须是数组".to_string(),
        ));
    };
    let Some(writable_events) = writable_value.as_array_mut() else {
        return Err(AttMzError::InvalidConfig(
            "CommonEvents.json 顶层必须是数组".to_string(),
        ));
    };
    let mut written_count = 0usize;
    for (source_event, writable_event) in source_events.iter().zip(writable_events.iter_mut()) {
        if source_event.is_null() {
            continue;
        }
        let Some(source_event_object) = source_event.as_object() else {
            continue;
        };
        let Some(writable_event_object) = writable_event.as_object_mut() else {
            continue;
        };
        let Some(source_commands) = source_event_object.get("list").and_then(Value::as_array)
        else {
            continue;
        };
        let Some(writable_commands) = writable_event_object
            .get_mut("list")
            .and_then(Value::as_array_mut)
        else {
            continue;
        };
        written_count +=
            write_speaker_names_to_commands(source_commands, writable_commands, translations);
    }
    Ok(written_count)
}

fn write_troop_speaker_names(
    source_value: &Value,
    writable_value: &mut Value,
    translations: &BTreeMap<String, String>,
) -> Result<usize> {
    let Some(source_troops) = source_value.as_array() else {
        return Err(AttMzError::InvalidConfig(
            "Troops.json 顶层必须是数组".to_string(),
        ));
    };
    let Some(writable_troops) = writable_value.as_array_mut() else {
        return Err(AttMzError::InvalidConfig(
            "Troops.json 顶层必须是数组".to_string(),
        ));
    };
    let mut written_count = 0usize;
    for (source_troop, writable_troop) in source_troops.iter().zip(writable_troops.iter_mut()) {
        if source_troop.is_null() {
            continue;
        }
        let Some(source_troop_object) = source_troop.as_object() else {
            continue;
        };
        let Some(writable_troop_object) = writable_troop.as_object_mut() else {
            continue;
        };
        let Some(source_pages) = source_troop_object.get("pages").and_then(Value::as_array) else {
            continue;
        };
        let Some(writable_pages) = writable_troop_object
            .get_mut("pages")
            .and_then(Value::as_array_mut)
        else {
            continue;
        };
        for (source_page, writable_page) in source_pages.iter().zip(writable_pages.iter_mut()) {
            let Some(source_page_object) = source_page.as_object() else {
                continue;
            };
            let Some(writable_page_object) = writable_page.as_object_mut() else {
                continue;
            };
            let Some(source_commands) = source_page_object.get("list").and_then(Value::as_array)
            else {
                continue;
            };
            let Some(writable_commands) = writable_page_object
                .get_mut("list")
                .and_then(Value::as_array_mut)
            else {
                continue;
            };
            written_count +=
                write_speaker_names_to_commands(source_commands, writable_commands, translations);
        }
    }
    Ok(written_count)
}

fn write_speaker_names_to_commands(
    source_commands: &[Value],
    writable_commands: &mut [Value],
    translations: &BTreeMap<String, String>,
) -> usize {
    let mut written_count = 0usize;
    for (source_command, writable_command) in
        source_commands.iter().zip(writable_commands.iter_mut())
    {
        let Some(source_command_object) = source_command.as_object() else {
            continue;
        };
        if source_command_object.get("code").and_then(Value::as_i64) != Some(101) {
            continue;
        }
        let Some(source_text) = source_command_object
            .get("parameters")
            .and_then(Value::as_array)
            .and_then(|parameters| parameters.get(4))
            .and_then(Value::as_str)
            .map(str::trim)
        else {
            continue;
        };
        if !is_translatable_terminology_source(source_text) {
            continue;
        }
        let Some(translated_text) = translations.get(source_text) else {
            continue;
        };
        let Some(writable_command_object) = writable_command.as_object_mut() else {
            continue;
        };
        let parameters = writable_command_object
            .entry("parameters".to_string())
            .or_insert_with(|| Value::Array(Vec::new()));
        if !parameters.is_array() {
            *parameters = Value::Array(Vec::new());
        }
        let Some(parameters) = parameters.as_array_mut() else {
            continue;
        };
        while parameters.len() <= 4 {
            parameters.push(json!(""));
        }
        parameters[4] = json!(translated_text);
        written_count += 1;
    }
    written_count
}

fn write_base_database_terms(
    source_data_files: &BTreeMap<String, Value>,
    writable_data_files: &mut BTreeMap<String, Value>,
    registry: &BTreeMap<String, BTreeMap<String, String>>,
) -> Result<usize> {
    let mut written_count = 0usize;
    for (file_name, category) in BASE_NAME_CATEGORIES {
        written_count += write_base_item_field(
            source_data_files,
            writable_data_files,
            file_name,
            "name",
            category_translations(registry, category),
        )?;
    }
    written_count += write_base_item_field(
        source_data_files,
        writable_data_files,
        "Actors.json",
        "nickname",
        category_translations(registry, "actor_nicknames"),
    )?;
    Ok(written_count)
}

fn write_base_item_field(
    source_data_files: &BTreeMap<String, Value>,
    writable_data_files: &mut BTreeMap<String, Value>,
    file_name: &str,
    key: &str,
    translations: BTreeMap<String, String>,
) -> Result<usize> {
    if translations.is_empty() {
        return Ok(0);
    }
    let Some(source_value) = source_data_files.get(file_name) else {
        return Ok(0);
    };
    let Some(writable_value) = writable_data_files.get_mut(file_name) else {
        return Ok(0);
    };
    let Some(source_items) = source_value.as_array() else {
        return Err(AttMzError::InvalidConfig(format!(
            "{file_name} 顶层必须是数组"
        )));
    };
    let Some(writable_items) = writable_value.as_array_mut() else {
        return Err(AttMzError::InvalidConfig(format!(
            "{file_name} 顶层必须是数组"
        )));
    };
    let mut written_count = 0usize;
    for (source_item, writable_item) in source_items.iter().zip(writable_items.iter_mut()) {
        if source_item.is_null() {
            continue;
        }
        let Some(source_item_object) = source_item.as_object() else {
            continue;
        };
        let Some(source_text) = source_item_object
            .get(key)
            .and_then(Value::as_str)
            .map(str::trim)
        else {
            continue;
        };
        let Some(translated_text) = translations.get(source_text) else {
            continue;
        };
        let Some(writable_item_object) = writable_item.as_object_mut() else {
            continue;
        };
        writable_item_object.insert(key.to_string(), json!(translated_text));
        written_count += 1;
    }
    Ok(written_count)
}

fn write_system_terms(
    source_data_files: &BTreeMap<String, Value>,
    writable_data_files: &mut BTreeMap<String, Value>,
    registry: &BTreeMap<String, BTreeMap<String, String>>,
) -> Result<usize> {
    let Some(source_system_data) = source_data_files
        .get("System.json")
        .and_then(Value::as_object)
    else {
        return Err(AttMzError::InvalidConfig(
            "System.json 顶层必须是对象".to_string(),
        ));
    };
    let Some(writable_system_data) = writable_data_files
        .get_mut("System.json")
        .and_then(Value::as_object_mut)
    else {
        return Err(AttMzError::InvalidConfig(
            "System.json 顶层必须是对象".to_string(),
        ));
    };
    let mut written_count = 0usize;
    for (field_name, category) in SYSTEM_TERM_CATEGORIES {
        written_count += write_system_array(
            source_system_data,
            writable_system_data,
            field_name,
            category_translations(registry, category),
        )?;
    }
    Ok(written_count)
}

fn write_system_array(
    source_system_data: &Map<String, Value>,
    writable_system_data: &mut Map<String, Value>,
    field_name: &str,
    translations: BTreeMap<String, String>,
) -> Result<usize> {
    if translations.is_empty() {
        return Ok(0);
    }
    let Some(source_values) = source_system_data.get(field_name).and_then(Value::as_array) else {
        return Ok(0);
    };
    let Some(writable_values) = writable_system_data
        .get_mut(field_name)
        .and_then(Value::as_array_mut)
    else {
        return Ok(0);
    };
    let mut written_count = 0usize;
    for (source_value, writable_value) in source_values.iter().zip(writable_values.iter_mut()) {
        let Some(source_text) = source_value.as_str().map(str::trim) else {
            continue;
        };
        let Some(translated_text) = translations.get(source_text) else {
            continue;
        };
        *writable_value = json!(translated_text);
        written_count += 1;
    }
    Ok(written_count)
}

fn category_translations(
    registry: &BTreeMap<String, BTreeMap<String, String>>,
    category: &str,
) -> BTreeMap<String, String> {
    registry
        .get(category)
        .into_iter()
        .flat_map(BTreeMap::iter)
        .filter_map(|(source_text, translated_text)| {
            let source_text = source_text.trim();
            let translated_text = translated_text.trim();
            if is_translatable_terminology_source(source_text) && !translated_text.is_empty() {
                Some((source_text.to_string(), translated_text.to_string()))
            } else {
                None
            }
        })
        .collect()
}

fn changed_data_file_names(
    original_files: &BTreeMap<String, Value>,
    data_files: &BTreeMap<String, Value>,
) -> Vec<String> {
    data_files
        .iter()
        .filter_map(|(file_name, value)| {
            let changed = original_files.get(file_name) != Some(value);
            changed.then(|| file_name.clone())
        })
        .collect()
}

fn read_active_data_json_files(game_path: &Path) -> Result<BTreeMap<String, Value>> {
    let active_data_dir = game_path.join("data");
    if !active_data_dir.is_dir() {
        return Err(AttMzError::InvalidConfig(format!(
            "激活数据目录不存在: {}",
            active_data_dir.display()
        )));
    }
    let mut files = BTreeMap::new();
    for entry in fs::read_dir(&active_data_dir).map_err(|source| {
        AttMzError::io(
            format!("扫描数据目录 {}", active_data_dir.display()),
            source,
        )
    })? {
        let entry = entry.map_err(|source| AttMzError::io("读取数据目录项", source))?;
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        let Some(file_name) = path.file_name().and_then(|value| value.to_str()) else {
            continue;
        };
        if !file_name.ends_with(".json") {
            continue;
        }
        files.insert(file_name.to_string(), read_json_file(&path, file_name)?);
    }
    Ok(files)
}

fn write_changed_data_files(
    game_path: &Path,
    data_files: &BTreeMap<String, Value>,
    changed_file_names: &[String],
) -> Result<()> {
    if changed_file_names.is_empty() {
        return Ok(());
    }
    let active_data_dir = game_path.join("data");
    let origin_data_dir = game_path.join("data_origin");
    if !active_data_dir.exists() {
        return Err(AttMzError::InvalidConfig(format!(
            "激活数据目录不存在: {}",
            active_data_dir.display()
        )));
    }
    fs::create_dir_all(&origin_data_dir).map_err(|source| {
        AttMzError::io(
            format!("创建原件留档目录 {}", origin_data_dir.display()),
            source,
        )
    })?;
    for file_name in changed_file_names {
        let active_path = active_data_dir.join(file_name);
        let origin_path = origin_data_dir.join(file_name);
        if !origin_path.exists() {
            if !active_path.exists() {
                return Err(AttMzError::InvalidConfig(format!(
                    "待备份原始 data 文件不存在: {}",
                    active_path.display()
                )));
            }
            fs::copy(&active_path, &origin_path).map_err(|source| {
                AttMzError::io(
                    format!(
                        "备份原始 data 文件 {} -> {}",
                        active_path.display(),
                        origin_path.display()
                    ),
                    source,
                )
            })?;
        }
        let Some(value) = data_files.get(file_name) else {
            continue;
        };
        write_json_file(&active_path, value)?;
    }
    Ok(())
}

fn terminology_export_summary(
    target_dir: &Path,
    artifacts: &TerminologyArtifacts,
    glossary_path: &Path,
) -> Map<String, Value> {
    let mut summary = Map::new();
    summary.insert(
        "field_terms_path".to_string(),
        json!(target_dir.join("field-terms.json").display().to_string()),
    );
    summary.insert(
        "glossary_path".to_string(),
        json!(glossary_path.display().to_string()),
    );
    summary.insert(
        "contexts_dir".to_string(),
        json!(target_dir.join("contexts").display().to_string()),
    );
    summary.insert(
        "entry_count".to_string(),
        json!(registry_entry_count(&artifacts.registry)),
    );
    summary.insert(
        "speaker_entry_count".to_string(),
        json!(artifacts.speaker_entry_count),
    );
    summary.insert(
        "map_entry_count".to_string(),
        json!(artifacts.map_entry_count),
    );
    summary.insert(
        "database_entry_count".to_string(),
        json!(artifacts.database_entry_count),
    );
    summary.insert(
        "sample_file_count".to_string(),
        json!(artifacts.speaker_contexts.len()),
    );
    summary
}

fn write_terminology_artifacts(
    terminology_dir: &Path,
    registry: &BTreeMap<String, BTreeMap<String, String>>,
    artifacts: &TerminologyArtifacts,
) -> Result<()> {
    write_json_file(&terminology_dir.join("field-terms.json"), &json!(registry))?;
    let speaker_dir = terminology_dir.join("contexts").join("speakers");
    fs::create_dir_all(&speaker_dir).map_err(|source| {
        AttMzError::io(
            format!("创建名字样本目录 {}", speaker_dir.display()),
            source,
        )
    })?;
    let mut file_names = BTreeMap::new();
    for context in &artifacts.speaker_contexts {
        let name = context
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or("speaker");
        let file_name = build_speaker_sample_file_name(name);
        if file_names
            .insert(file_name.clone(), name.to_string())
            .is_some()
        {
            return Err(AttMzError::InvalidConfig(format!(
                "对白样本文件名冲突: {file_name}"
            )));
        }
        write_json_file(&speaker_dir.join(file_name), context)?;
    }
    write_json_file(
        &terminology_dir.join("contexts").join("database_terms.json"),
        &Value::Array(artifacts.database_contexts.clone()),
    )
}

fn collect_speaker_contexts(command_snapshots: &[EventCommandSnapshot]) -> Vec<Value> {
    let mut dialogue_map: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for (index, snapshot) in command_snapshots.iter().enumerate() {
        if snapshot.code != 101 {
            continue;
        }
        let Some(name) = snapshot
            .parameters
            .as_array()
            .and_then(|parameters| parameters.get(4))
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|name| is_translatable_terminology_source(name))
        else {
            continue;
        };
        let lines = collect_following_dialogue_lines(command_snapshots, index);
        dialogue_map
            .entry(name.to_string())
            .or_default()
            .extend(lines);
    }
    dialogue_map
        .into_iter()
        .map(|(name, dialogue_lines)| json!({ "name": name, "dialogue_lines": dialogue_lines }))
        .collect()
}

fn collect_following_dialogue_lines(
    command_snapshots: &[EventCommandSnapshot],
    command_index: usize,
) -> Vec<String> {
    let parent = command_location_parent(&command_snapshots[command_index].location_path);
    let mut expected_index =
        command_location_index(&command_snapshots[command_index].location_path)
            .map(|index| index + 1);
    let mut lines = Vec::new();
    for snapshot in command_snapshots.iter().skip(command_index + 1) {
        if snapshot.code != 401 || command_location_parent(&snapshot.location_path) != parent {
            break;
        }
        if expected_index.is_some()
            && command_location_index(&snapshot.location_path) != expected_index
        {
            break;
        }
        if let Some(text) = snapshot
            .parameters
            .as_array()
            .and_then(|parameters| parameters.first())
            .and_then(Value::as_str)
        {
            lines.push(text.to_string());
        }
        expected_index = expected_index.map(|index| index + 1);
    }
    lines
}

fn collect_map_display_names(data_files: &BTreeMap<String, Value>) -> Vec<String> {
    let mut names = BTreeSet::new();
    for (file_name, value) in data_files {
        if !is_map_file_name(file_name) {
            continue;
        }
        if let Some(name) = value
            .get("displayName")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|name| is_translatable_terminology_source(name))
        {
            names.insert(name.to_string());
        }
    }
    names.into_iter().collect()
}

fn collect_database_terms(
    data_files: &BTreeMap<String, Value>,
    registry: &mut BTreeMap<String, BTreeMap<String, String>>,
) -> Vec<Value> {
    let mut contexts = Vec::new();
    for (file_name, category) in BASE_NAME_CATEGORIES {
        let Some(items) = data_files.get(*file_name).and_then(Value::as_array) else {
            continue;
        };
        for item in items.iter().filter_map(Value::as_object) {
            if let Some(name) = item
                .get("name")
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|name| is_translatable_terminology_source(name))
            {
                insert_term(registry, category, name);
                contexts.push(json!({
                    "category": category,
                    "source_text": name,
                    "context_lines": database_context_lines(file_name, item),
                }));
            }
            if *file_name == "Actors.json"
                && let Some(nickname) = item
                    .get("nickname")
                    .and_then(Value::as_str)
                    .map(str::trim)
                    .filter(|nickname| is_translatable_terminology_source(nickname))
            {
                insert_term(registry, "actor_nicknames", nickname);
                contexts.push(json!({
                    "category": "actor_nicknames",
                    "source_text": nickname,
                    "context_lines": database_context_lines(file_name, item),
                }));
            }
        }
    }
    if let Some(system) = data_files.get("System.json") {
        for (field_name, category) in SYSTEM_TERM_CATEGORIES {
            let Some(values) = system.get(*field_name).and_then(Value::as_array) else {
                continue;
            };
            for value in values {
                if let Some(text) = value
                    .as_str()
                    .map(str::trim)
                    .filter(|text| is_translatable_terminology_source(text))
                {
                    insert_term(registry, category, text);
                    contexts.push(json!({
                        "category": category,
                        "source_text": text,
                        "context_lines": [],
                    }));
                }
            }
        }
    }
    contexts
}

fn database_context_lines(file_name: &str, item: &Map<String, Value>) -> Vec<String> {
    let fields: &[&str] = match file_name {
        "Actors.json" => &["nickname", "profile"],
        "Skills.json" => &["description", "message1", "message2"],
        "Items.json" | "Weapons.json" | "Armors.json" => &["description"],
        "States.json" => &["message1", "message2", "message3", "message4"],
        _ => &[],
    };
    fields
        .iter()
        .filter_map(|field| item.get(*field).and_then(Value::as_str).map(str::trim))
        .filter(|text| !text.is_empty())
        .map(str::to_string)
        .collect()
}

fn insert_term(
    registry: &mut BTreeMap<String, BTreeMap<String, String>>,
    category: &str,
    source_text: &str,
) {
    registry
        .entry(category.to_string())
        .or_default()
        .entry(source_text.to_string())
        .or_default();
}

fn is_translatable_terminology_source(source_text: &str) -> bool {
    let normalized = source_text.trim();
    !normalized.is_empty() && !normalized.to_ascii_uppercase().contains(r"\N[")
}

fn build_speaker_sample_file_name(name: &str) -> String {
    let translated = name
        .trim()
        .chars()
        .map(|char_value| match char_value {
            '<' => '＜',
            '>' => '＞',
            ':' => '：',
            '"' => '＂',
            '/' => '／',
            '\\' => '＼',
            '|' => '｜',
            '?' => '？',
            '*' => '＊',
            char_value if char_value.is_whitespace() => '_',
            char_value => char_value,
        })
        .collect::<String>()
        .trim_matches(['.', '_'])
        .to_string();
    if translated.is_empty() {
        "speaker.json".to_string()
    } else {
        format!("{translated}.json")
    }
}

fn command_location_parent(location_path: &str) -> String {
    location_path
        .rsplit_once('/')
        .map(|(parent, _)| parent.to_string())
        .unwrap_or_default()
}

fn command_location_index(location_path: &str) -> Option<usize> {
    location_path
        .rsplit_once('/')
        .and_then(|(_, index)| index.parse::<usize>().ok())
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

fn validate_terms_json_category_keys(object: &Map<String, Value>, path: &Path) -> Result<()> {
    let actual_categories = object.keys().cloned().collect::<BTreeSet<_>>();
    let expected_categories = TERMINOLOGY_CATEGORIES
        .iter()
        .map(|category| (*category).to_string())
        .collect::<BTreeSet<_>>();
    let missing = expected_categories
        .difference(&actual_categories)
        .cloned()
        .collect::<Vec<_>>();
    let extra = actual_categories
        .difference(&expected_categories)
        .cloned()
        .collect::<Vec<_>>();
    let mut errors = Vec::new();
    if !missing.is_empty() {
        errors.push(format!("缺少类别: {}", missing.join(", ")));
    }
    if !extra.is_empty() {
        errors.push(format!("未知类别: {}", extra.join(", ")));
    }
    if errors.is_empty() {
        Ok(())
    } else {
        Err(AttMzError::InvalidConfig(format!(
            "术语表类别不完整: {}: {}",
            path.display(),
            errors.join("; ")
        )))
    }
}

fn read_json_file(path: &Path, context: &str) -> Result<Value> {
    let text = fs::read_to_string(path)
        .map_err(|source| AttMzError::io(format!("读取 {}", path.display()), source))?;
    serde_json::from_str(text.trim_start_matches('\u{feff}')).map_err(|source| AttMzError::Json {
        context: context.to_string(),
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
        .map_err(|source| AttMzError::io(format!("写入 {}", path.display()), source))
}

fn absolute_path(path: &Path) -> PathBuf {
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .map(|cwd| cwd.join(path))
            .unwrap_or_else(|_| path.to_path_buf())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::GameRegistry;

    #[test]
    fn terminology_extracts_speaker_map_and_database_terms() {
        let data_files = BTreeMap::from([
            (
                "Map001.json".to_string(),
                json!({"displayName": "始まりの町"}),
            ),
            (
                "Actors.json".to_string(),
                json!([null, {"id": 1, "name": "アリス", "nickname": "勇者", "profile": "炎の剣士"}]),
            ),
            (
                "System.json".to_string(),
                json!({"elements": ["", "火"], "skillTypes": [], "weaponTypes": [], "armorTypes": [], "equipTypes": []}),
            ),
        ]);
        let command_snapshots = vec![
            EventCommandSnapshot {
                location_path: "CommonEvents.json/1/0".to_string(),
                display_name: "公共事件".to_string(),
                code: 101,
                parameters: json!([0, 0, 0, 2, "案内人"]),
            },
            EventCommandSnapshot {
                location_path: "CommonEvents.json/1/1".to_string(),
                display_name: "公共事件".to_string(),
                code: 401,
                parameters: json!(["こんにちは"]),
            },
        ];

        let artifacts = extract_terminology(&data_files, &command_snapshots);

        assert!(artifacts.registry["speaker_names"].contains_key("案内人"));
        assert!(artifacts.registry["map_display_names"].contains_key("始まりの町"));
        assert!(artifacts.registry["actor_names"].contains_key("アリス"));
        assert!(artifacts.registry["actor_nicknames"].contains_key("勇者"));
        assert!(artifacts.registry["system_elements"].contains_key("火"));
        assert_eq!(
            artifacts.speaker_contexts[0]["dialogue_lines"],
            json!(["こんにちは"])
        );
    }

    #[test]
    fn glossary_rejects_empty_translation() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        let path = temp.path().join("glossary.json");
        fs::write(&path, r#"{"terms":{"火":""}}"#).expect("正文术语表应写入成功");

        let error = read_glossary_file(&path).expect_err("空译名必须拒绝");

        assert!(error.to_string().contains("不能包含空值"));
    }

    #[test]
    fn terminology_export_and_import_roundtrip() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        let game = create_terminology_test_game(temp.path(), "TerminologyRoundtrip");
        let registry = GameRegistry {
            db_directory: temp.path().join("db"),
        };
        let game_record = registry.register_game(&game).expect("游戏应注册成功");
        let terminology_dir = temp.path().join("terminology");

        let export_report =
            export_terminology_report(&game_record, &terminology_dir).expect("术语表应导出成功");
        assert_eq!(export_report.status, "ok");
        assert_eq!(export_report.summary["entry_count"], json!(3));

        let field_terms_path = terminology_dir.join("field-terms.json");
        let glossary_path = terminology_dir.join("glossary.json");
        let mut field_terms =
            read_json_file(&field_terms_path, "测试字段译名表").expect("字段译名表应读取成功");
        field_terms["actor_names"]["アリス"] = json!("爱丽丝");
        write_json_file(&field_terms_path, &field_terms).expect("字段译名表应写回成功");
        write_json_file(&glossary_path, &json!({"terms": {"火の術": "火术"}}))
            .expect("正文术语表应写回成功");

        let import_report =
            import_terminology_report(&registry, &game_record, &field_terms_path, &glossary_path)
                .expect("术语表应导入成功");
        assert_eq!(import_report.summary["imported_entry_count"], json!(3));
        assert_eq!(import_report.summary["filled_entry_count"], json!(1));
        assert_eq!(import_report.summary["glossary_term_count"], json!(1));

        let stored_registry = registry
            .read_terminology_registry("TerminologyRoundtrip")
            .expect("字段译名表应读取成功")
            .expect("字段译名表应已导入");
        let stored_glossary = registry
            .read_terminology_glossary("TerminologyRoundtrip")
            .expect("正文术语表应读取成功")
            .expect("正文术语表应已导入");
        assert_eq!(stored_registry["actor_names"]["アリス"], "爱丽丝");
        assert_eq!(stored_glossary["火の術"], "火术");
    }

    #[test]
    fn terminology_write_uses_origin_terms_and_preserves_active_text() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        let game = create_terminology_test_game(temp.path(), "TerminologyWrite");
        let registry = GameRegistry {
            db_directory: temp.path().join("db"),
        };
        let game_record = registry.register_game(&game).expect("游戏应注册成功");
        fs::create_dir_all(game.join("data_origin")).expect("原件留档目录应创建成功");
        write_json_file(
            &game.join("data_origin/Map001.json"),
            &json!({
                "displayName": "始まりの町",
                "events": [null, {
                    "pages": [{
                        "list": [
                            {"code": 101, "parameters": [0, 0, 0, 2, "案内人"]},
                            {"code": 401, "parameters": ["こんにちは"]}
                        ]
                    }]
                }]
            }),
        )
        .expect("Map001 原件应写入成功");
        write_json_file(
            &game.join("data/Map001.json"),
            &json!({
                "displayName": "旧镇名",
                "events": [null, {
                    "pages": [{
                        "list": [
                            {"code": 101, "parameters": [0, 0, 0, 2, "旧向导"]},
                            {"code": 401, "parameters": ["已有中文正文"]}
                        ]
                    }]
                }]
            }),
        )
        .expect("Map001 当前文件应写入成功");
        write_json_file(
            &game.join("data_origin/Actors.json"),
            &json!([null, {"id": 1, "name": "アリス", "nickname": "勇者", "profile": ""}]),
        )
        .expect("Actors 原件应写入成功");
        write_json_file(
            &game.join("data/Actors.json"),
            &json!([null, {"id": 1, "name": "旧爱丽丝", "nickname": "旧勇者", "profile": ""}]),
        )
        .expect("Actors 当前文件应写入成功");
        let mut registry_terms = empty_terminology_registry();
        registry_terms
            .get_mut("map_display_names")
            .expect("类别应存在")
            .insert("始まりの町".to_string(), "初始之镇".to_string());
        registry_terms
            .get_mut("speaker_names")
            .expect("类别应存在")
            .insert("案内人".to_string(), "向导".to_string());
        registry_terms
            .get_mut("actor_names")
            .expect("类别应存在")
            .insert("アリス".to_string(), "爱丽丝".to_string());
        registry_terms
            .get_mut("actor_nicknames")
            .expect("类别应存在")
            .insert("勇者".to_string(), "勇者称号".to_string());
        registry
            .replace_terminology(&game_record.game_title, &registry_terms, &BTreeMap::new())
            .expect("术语表应写入数据库");

        let report = write_terminology_report(&registry, &game_record).expect("术语写回应执行成功");

        assert_eq!(report.summary["written_count"], json!(5));
        let active_map =
            read_json_file(&game.join("data/Map001.json"), "Map001").expect("当前地图应读取成功");
        assert_eq!(active_map["displayName"], json!("初始之镇"));
        assert_eq!(
            active_map["events"][1]["pages"][0]["list"][0]["parameters"][4],
            json!("向导")
        );
        assert_eq!(
            active_map["events"][1]["pages"][0]["list"][1]["parameters"][0],
            json!("已有中文正文")
        );
        let active_actors =
            read_json_file(&game.join("data/Actors.json"), "Actors").expect("当前角色应读取成功");
        assert_eq!(active_actors[1]["name"], json!("爱丽丝"));
        assert_eq!(active_actors[1]["nickname"], json!("勇者称号"));
        let active_common_events = read_json_file(&game.join("data/CommonEvents.json"), "公共事件")
            .expect("当前公共事件应读取成功");
        assert_eq!(
            active_common_events[1]["list"][0]["parameters"][4],
            json!("向导")
        );
        let origin_actors = read_json_file(&game.join("data_origin/Actors.json"), "Actors 原件")
            .expect("原件角色应读取成功");
        assert_eq!(origin_actors[1]["name"], json!("アリス"));
    }

    fn create_terminology_test_game(root: &Path, title: &str) -> PathBuf {
        let game = root.join("game");
        fs::create_dir_all(game.join("data")).expect("data 目录应创建成功");
        fs::create_dir_all(game.join("js")).expect("js 目录应创建成功");
        fs::write(
            game.join("package.json"),
            json!({"window": {"title": title}}).to_string(),
        )
        .expect("package.json 应写入成功");
        fs::write(
            game.join("data/System.json"),
            json!({"elements": ["", "火"], "skillTypes": [], "weaponTypes": [], "armorTypes": [], "equipTypes": []}).to_string(),
        )
        .expect("System.json 应写入成功");
        fs::write(
            game.join("data/Actors.json"),
            json!([null, {"id": 1, "name": "アリス", "nickname": "", "profile": ""}]).to_string(),
        )
        .expect("Actors.json 应写入成功");
        fs::write(
            game.join("data/CommonEvents.json"),
            json!([null, {
                "id": 1,
                "name": "event",
                "list": [
                    {"code": 101, "parameters": [0, 0, 0, 2, "案内人"]},
                    {"code": 401, "parameters": ["こんにちは"]},
                    {"code": 0, "parameters": []}
                ]
            }])
            .to_string(),
        )
        .expect("CommonEvents.json 应写入成功");
        fs::write(game.join("data/Troops.json"), "[]").expect("Troops.json 应写入成功");
        fs::write(game.join("js/plugins.js"), "var $plugins = [];").expect("plugins.js 应写入成功");
        game
    }
}
