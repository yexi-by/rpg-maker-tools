//! Agent 临时工作区维护能力。
//!
//! 本模块负责按工作区 `manifest.json` 清理由工具生成的临时文件。删除前会把
//! 目标路径规范化并确认它仍位于工作区内部，避免外部路径被 manifest 误删。

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{Map, Value, json};

use crate::error::{AttMzError, Result};
use crate::event_command_rules::{EventCommandRuleRecord, validate_event_command_rules_report};
use crate::note_tag_rules::{
    NoteTagRuleRecord, export_note_tag_candidates_report, validate_note_tag_rules_report,
};
use crate::placeholder::{
    PlaceholderRule, parse_custom_placeholder_rules_text, validate_placeholder_rules_report,
};
use crate::placeholder_scan::{
    ActiveTextItem, build_placeholder_rule_draft_report, extract_active_text_items,
    scan_placeholder_candidates_report,
};
use crate::plugin_rules::{PluginRuleRecord, build_plugin_hash, validate_plugin_rules_report};
use crate::rmmz::{
    EventCommandSnapshot, export_event_commands_json_file, read_data_json_files,
    read_event_command_snapshots, read_plugins_json, resolve_event_command_codes,
};
use crate::{AgentReport, GameRecord, GameRegistry, issue};

const TERMINOLOGY_CATEGORIES: &[&str] = &[
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
const TERMINOLOGY_SUBTASK_GROUPS: &[(&str, &[&str])] = &[
    (
        "speaker_and_actor_terms",
        &[
            "speaker_names",
            "actor_names",
            "actor_nicknames",
            "class_names",
            "enemy_names",
        ],
    ),
    (
        "map_and_system_terms",
        &[
            "map_display_names",
            "system_elements",
            "system_skill_types",
            "system_weapon_types",
            "system_armor_types",
            "system_equip_types",
        ],
    ),
    ("skill_and_state_terms", &["skill_names", "state_names"]),
    ("item_terms", &["item_names"]),
    ("equipment_terms", &["weapon_names", "armor_names"]),
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

/// 导出外部 Agent 分析所需的临时工作区。
///
/// 该函数生成术语表、插件规则、事件指令规则、Note 标签规则和占位符规则
/// 草稿，并写入 `manifest.json` 供后续校验和清理命令使用。
pub fn prepare_agent_workspace(
    registry: &GameRegistry,
    game_record: &GameRecord,
    output_dir: &Path,
    command_codes: Option<BTreeSet<i64>>,
    default_command_codes: Option<Vec<i64>>,
    source_text_required_pattern: &str,
) -> Result<AgentReport> {
    let target_dir = absolute_path(output_dir);
    fs::create_dir_all(&target_dir)
        .map_err(|source| AttMzError::io(format!("创建工作区 {}", target_dir.display()), source))?;
    let data_files = read_data_json_files(&game_record.game_path)?;
    let command_snapshots = read_event_command_snapshots(&game_record.game_path)?;
    let plugins = read_plugins_json(&game_record.game_path)?;
    let (plugin_rules, stale_plugin_rule_count) =
        read_fresh_plugin_rules(registry, game_record, &plugins)?;
    let note_tag_rules = registry.read_note_tag_text_rules(&game_record.game_title)?;
    let event_rules = registry.read_event_command_text_rules(&game_record.game_title)?;
    let placeholder_rules = registry.read_placeholder_rules(&game_record.game_title)?;
    let stored_registry = registry.read_terminology_registry(&game_record.game_title)?;
    let stored_glossary = registry.read_terminology_glossary(&game_record.game_title)?;
    let active_items = extract_active_text_items(
        &data_files,
        &command_snapshots,
        &plugins,
        &plugin_rules,
        &event_rules,
        &note_tag_rules,
        source_text_required_pattern,
    )?;

    let terminology_dir = target_dir.join("terminology");
    let terminology = extract_terminology(&data_files, &command_snapshots);
    let merged_registry = merge_terminology_registry(terminology.registry.clone(), stored_registry);
    write_terminology_artifacts(&terminology_dir, &merged_registry, &terminology)?;
    let glossary = json!({
        "terms": stored_glossary.unwrap_or_default(),
    });
    write_json_file(&terminology_dir.join("glossary.json"), &glossary)?;
    let terminology_subtasks_dir = terminology_dir.join("subtasks");
    let terminology_subtask_summary =
        write_terminology_subtask_files(&merged_registry, &terminology_subtasks_dir)?;

    let plugins_path = target_dir.join("plugins.json");
    write_json_file(&plugins_path, &Value::Array(plugins.clone()))?;

    let plugin_rules_path = target_dir.join("plugin-rules.json");
    write_json_file(&plugin_rules_path, &plugin_rules_import_json(&plugin_rules))?;

    let note_tag_candidates_path = target_dir.join("note-tag-candidates.json");
    let note_tag_report = export_note_tag_candidates_report(
        &data_files,
        &note_tag_candidates_path,
        source_text_required_pattern,
    )?;
    write_report_file(&note_tag_candidates_path, &note_tag_report)?;

    let note_tag_rules_path = target_dir.join("note-tag-rules.json");
    write_json_file(
        &note_tag_rules_path,
        &note_tag_rules_import_json(&note_tag_rules),
    )?;

    let effective_codes =
        resolve_event_command_codes(command_codes.map(Vec::from_iter), default_command_codes)?;
    let event_commands_path = target_dir.join("event-commands.json");
    let event_command_count = export_event_commands_json_file(
        &game_record.game_path,
        &event_commands_path,
        &effective_codes,
    )?;
    let event_rules_path = target_dir.join("event-command-rules.json");
    write_json_file(&event_rules_path, &event_rules_import_json(&event_rules))?;

    let placeholder_candidates_report =
        scan_placeholder_candidates_report(&active_items, &placeholder_rules)?;
    let placeholder_candidates_path = target_dir.join("placeholder-candidates.json");
    write_report_file(&placeholder_candidates_path, &placeholder_candidates_report)?;
    let placeholder_rules_path = target_dir.join("placeholder-rules.json");
    let (_draft_report, placeholder_rule_drafts) =
        build_placeholder_rule_draft_report(&active_items, &placeholder_rules_path)?;
    let placeholder_rules_payload = if placeholder_rules.is_empty() {
        placeholder_rules_import_json_from_map(&placeholder_rule_drafts)
    } else {
        placeholder_rules_import_json(&placeholder_rules)
    };
    write_json_file(&placeholder_rules_path, &placeholder_rules_payload)?;

    let generated_summary = generated_workspace_summary(GeneratedSummaryInput {
        terminology: &terminology,
        registry: &merged_registry,
        glossary: &glossary,
        plugin_count: plugins.len(),
        plugin_rules: &plugin_rules,
        stale_plugin_rule_count,
        note_tag_report: &note_tag_report,
        note_tag_rules: &note_tag_rules,
        event_command_count,
        event_rules: &event_rules,
        placeholder_rules: &placeholder_rules,
        placeholder_rule_draft_count: placeholder_rule_drafts.len(),
    });
    let manifest_path = target_dir.join("manifest.json");
    let manifest_files = vec![
        terminology_dir.join("field-terms.json"),
        terminology_dir.join("glossary.json"),
        terminology_dir.join("contexts"),
        terminology_subtasks_dir,
        plugins_path,
        plugin_rules_path,
        note_tag_candidates_path,
        note_tag_rules_path,
        event_commands_path,
        event_rules_path,
        placeholder_candidates_path,
        placeholder_rules_path,
    ];
    let manifest = json!({
        "files": manifest_files.iter().map(|path| path_json(path)).collect::<Vec<_>>(),
        "generated": generated_summary,
        "workflow": agent_workflow_manifest(&terminology_subtask_summary),
    });
    write_json_file(&manifest_path, &manifest)?;

    let mut summary = json_object(generated_summary);
    summary.insert("workspace".to_string(), path_json(&target_dir));
    summary.insert("manifest".to_string(), path_json(&manifest_path));
    let mut details = Map::new();
    details.insert("manifest".to_string(), manifest);
    Ok(AgentReport::from_parts(
        Vec::new(),
        Vec::new(),
        summary,
        details,
    ))
}

/// 校验 Agent 临时工作区中的可导入文件。
pub fn validate_agent_workspace(
    registry: &GameRegistry,
    game_record: &GameRecord,
    workspace: &Path,
    source_text_required_pattern: &str,
) -> AgentReport {
    let workspace = absolute_path(workspace);
    let mut errors = Vec::new();
    let mut warnings = Vec::new();
    let mut details = Map::new();
    let data_files = match read_data_json_files(&game_record.game_path) {
        Ok(data_files) => data_files,
        Err(error) => {
            return workspace_error_report(&workspace, "workspace_game_data", error.to_string());
        }
    };
    let command_snapshots = match read_event_command_snapshots(&game_record.game_path) {
        Ok(command_snapshots) => command_snapshots,
        Err(error) => {
            return workspace_error_report(&workspace, "workspace_game_data", error.to_string());
        }
    };
    let plugins = match read_plugins_json(&game_record.game_path) {
        Ok(plugins) => plugins,
        Err(error) => {
            return workspace_error_report(&workspace, "workspace_game_data", error.to_string());
        }
    };

    validate_workspace_terminology(
        &workspace,
        &data_files,
        &command_snapshots,
        &mut errors,
        &mut warnings,
        &mut details,
    );
    validate_workspace_plugin_rules(
        &workspace,
        &plugins,
        &mut errors,
        &mut warnings,
        &mut details,
    );
    validate_workspace_note_tag_rules(
        &workspace,
        &data_files,
        source_text_required_pattern,
        &mut errors,
        &mut warnings,
        &mut details,
    );
    validate_workspace_event_rules(
        &workspace,
        &command_snapshots,
        source_text_required_pattern,
        &mut errors,
        &mut warnings,
        &mut details,
    );
    validate_workspace_placeholder_rules(
        registry,
        game_record,
        &workspace,
        source_text_required_pattern,
        &mut errors,
        &mut warnings,
        &mut details,
    );

    AgentReport::from_parts(
        errors,
        warnings,
        summary_with_workspace(&workspace),
        details,
    )
}

#[derive(Debug, Clone)]
struct TerminologyArtifacts {
    registry: BTreeMap<String, BTreeMap<String, String>>,
    speaker_contexts: Vec<Value>,
    database_contexts: Vec<Value>,
    speaker_entry_count: usize,
    map_entry_count: usize,
    database_entry_count: usize,
}

struct GeneratedSummaryInput<'a> {
    terminology: &'a TerminologyArtifacts,
    registry: &'a BTreeMap<String, BTreeMap<String, String>>,
    glossary: &'a Value,
    plugin_count: usize,
    plugin_rules: &'a [PluginRuleRecord],
    stale_plugin_rule_count: usize,
    note_tag_report: &'a AgentReport,
    note_tag_rules: &'a [NoteTagRuleRecord],
    event_command_count: usize,
    event_rules: &'a [EventCommandRuleRecord],
    placeholder_rules: &'a [PlaceholderRule],
    placeholder_rule_draft_count: usize,
}

fn extract_terminology(
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

fn empty_terminology_registry() -> BTreeMap<String, BTreeMap<String, String>> {
    TERMINOLOGY_CATEGORIES
        .iter()
        .map(|category| ((*category).to_string(), BTreeMap::new()))
        .collect()
}

fn merge_terminology_registry(
    mut exported_registry: BTreeMap<String, BTreeMap<String, String>>,
    stored_registry: Option<BTreeMap<String, BTreeMap<String, String>>>,
) -> BTreeMap<String, BTreeMap<String, String>> {
    let Some(stored_registry) = stored_registry else {
        return exported_registry;
    };
    for category in TERMINOLOGY_CATEGORIES {
        let Some(exported_entries) = exported_registry.get_mut(*category) else {
            continue;
        };
        let Some(stored_entries) = stored_registry.get(*category) else {
            continue;
        };
        for (source_text, translated_text) in exported_entries.iter_mut() {
            if let Some(stored_text) = stored_entries.get(source_text) {
                *translated_text = stored_text.clone();
            }
        }
    }
    exported_registry
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

fn write_terminology_subtask_files(
    registry: &BTreeMap<String, BTreeMap<String, String>>,
    subtasks_dir: &Path,
) -> Result<Map<String, Value>> {
    let sources_dir = subtasks_dir.join("sources");
    let candidates_dir = subtasks_dir.join("candidates");
    let mut summary = Map::new();
    for (group_name, categories) in TERMINOLOGY_SUBTASK_GROUPS {
        let mut payload = Map::new();
        let mut entry_count = 0usize;
        for category in *categories {
            let entries = registry.get(*category).cloned().unwrap_or_default();
            entry_count += entries.len();
            payload.insert((*category).to_string(), json!(entries));
        }
        let source_path = sources_dir.join(format!("{group_name}.json"));
        let candidate_path = candidates_dir.join(format!("{group_name}.json"));
        let payload = Value::Object(payload);
        write_json_file(&source_path, &payload)?;
        write_json_file(&candidate_path, &payload)?;
        summary.insert(
            (*group_name).to_string(),
            json!({
                "categories": categories,
                "entry_count": entry_count,
                "source": source_path,
                "candidate": candidate_path,
            }),
        );
    }
    Ok(summary)
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

fn read_fresh_plugin_rules(
    registry: &GameRegistry,
    game_record: &GameRecord,
    plugins: &[Value],
) -> Result<(Vec<PluginRuleRecord>, usize)> {
    let rules = registry.read_plugin_text_rules(&game_record.game_title)?;
    let mut fresh_rules = Vec::new();
    let mut stale_count = 0usize;
    for rule in rules {
        let Some(plugin) = plugins.get(rule.plugin_index) else {
            stale_count += 1;
            continue;
        };
        if rule.plugin_hash != build_plugin_hash(plugin)? {
            stale_count += 1;
            continue;
        }
        fresh_rules.push(rule);
    }
    Ok((fresh_rules, stale_count))
}

fn plugin_rules_import_json(records: &[PluginRuleRecord]) -> Value {
    let mut payload = Map::new();
    let mut records = records.to_vec();
    records.sort_by(|left, right| {
        (left.plugin_index, left.plugin_name.as_str())
            .cmp(&(right.plugin_index, right.plugin_name.as_str()))
    });
    for record in records {
        payload.insert(record.plugin_name, json!(record.path_templates));
    }
    Value::Object(payload)
}

fn note_tag_rules_import_json(records: &[NoteTagRuleRecord]) -> Value {
    let mut payload = Map::new();
    let mut records = records.to_vec();
    records.sort_by(|left, right| left.file_name.cmp(&right.file_name));
    for record in records {
        payload.insert(record.file_name, json!(record.tag_names));
    }
    Value::Object(payload)
}

fn event_rules_import_json(records: &[EventCommandRuleRecord]) -> Value {
    let mut payload = Map::new();
    let mut records = records.to_vec();
    records.sort_by(|left, right| {
        (left.command_code, event_filter_sort_key(left))
            .cmp(&(right.command_code, event_filter_sort_key(right)))
    });
    for record in records {
        let command_key = record.command_code.to_string();
        let entry = payload
            .entry(command_key)
            .or_insert_with(|| Value::Array(Vec::new()));
        if let Some(array) = entry.as_array_mut() {
            let match_filters = record
                .parameter_filters
                .iter()
                .map(|filter| (filter.index.to_string(), json!(filter.value)))
                .collect::<Map<_, _>>();
            array.push(json!({
                "match": match_filters,
                "paths": record.path_templates,
            }));
        }
    }
    Value::Object(payload)
}

fn event_filter_sort_key(record: &EventCommandRuleRecord) -> Vec<(usize, String)> {
    record
        .parameter_filters
        .iter()
        .map(|filter| (filter.index, filter.value.clone()))
        .collect()
}

fn placeholder_rules_import_json(records: &[PlaceholderRule]) -> Value {
    let mut payload = Map::new();
    for record in records {
        payload.insert(
            record.pattern_text.clone(),
            json!(record.placeholder_template),
        );
    }
    Value::Object(payload)
}

fn placeholder_rules_import_json_from_map(records: &BTreeMap<String, String>) -> Value {
    let mut payload = Map::new();
    for (pattern_text, placeholder_template) in records {
        payload.insert(pattern_text.clone(), json!(placeholder_template));
    }
    Value::Object(payload)
}

fn generated_workspace_summary(input: GeneratedSummaryInput<'_>) -> Value {
    let glossary_term_count = input
        .glossary
        .get("terms")
        .and_then(Value::as_object)
        .map(Map::len)
        .unwrap_or(0);
    json!({
        "speaker_entry_count": input.terminology.speaker_entry_count,
        "map_entry_count": input.terminology.map_entry_count,
        "terminology_entry_count": registry_entry_count(input.registry),
        "terminology_database_entry_count": input.terminology.database_entry_count,
        "terminology_subtask_count": TERMINOLOGY_SUBTASK_GROUPS.len(),
        "glossary_term_count": glossary_term_count,
        "plugin_count": input.plugin_count,
        "plugin_rule_count": input.plugin_rules.iter().map(|rule| rule.path_templates.len()).sum::<usize>(),
        "stale_plugin_rule_count": input.stale_plugin_rule_count,
        "note_tag_candidate_count": input.note_tag_report.summary.get("candidate_tag_count").cloned().unwrap_or(json!(0)),
        "note_tag_rule_count": input.note_tag_rules.iter().map(|rule| rule.tag_names.len()).sum::<usize>(),
        "event_command_count": input.event_command_count,
        "event_command_rule_count": input.event_rules.iter().map(|rule| rule.path_templates.len()).sum::<usize>(),
        "placeholder_rule_count": input.placeholder_rules.len(),
        "placeholder_rule_draft_count": input.placeholder_rule_draft_count,
    })
}

fn registry_entry_count(registry: &BTreeMap<String, BTreeMap<String, String>>) -> usize {
    registry.values().map(BTreeMap::len).sum()
}

fn agent_workflow_manifest(terminology_subtask_summary: &Map<String, Value>) -> Value {
    json!({
        "subagent_rounds": [
            {
                "round": 1,
                "name": "terminology_candidates",
                "owner": "主代理",
                "description": "主代理按字段译名类别拆分任务，子代理只写候选文件；主代理必须逐项审查、统一译名、亲自修改并合并回 terminology/field-terms.json，同时维护 terminology/glossary.json 后才能导入数据库。",
                "subtasks": terminology_subtask_summary,
                "final_file": "terminology/field-terms.json",
                "glossary_file": "terminology/glossary.json",
                "import_command": "import-terminology --game <游戏标题> --input <工作区>/terminology/field-terms.json --glossary-input <工作区>/terminology/glossary.json --json",
            },
            {
                "round": 2,
                "name": "external_text_rules",
                "owner": "主代理",
                "description": "术语表导入后，主代理再派发插件规则、事件指令规则和 Note 标签规则三个子代理，并逐项 validate/import。",
                "subtasks": {
                    "plugin-rules": "plugin-rules.json",
                    "event-command-rules": "event-command-rules.json",
                    "note-tag-rules": "note-tag-rules.json",
                },
            },
        ],
        "placeholder_phase": {
            "owner": "主代理",
            "description": "两轮子代理任务全部完成并导入后，主代理才能亲自生成、审查、覆盖扫描、校验并导入占位符规则。",
        },
    })
}

fn validate_workspace_terminology(
    workspace: &Path,
    data_files: &BTreeMap<String, Value>,
    command_snapshots: &[EventCommandSnapshot],
    errors: &mut Vec<crate::AgentIssue>,
    warnings: &mut Vec<crate::AgentIssue>,
    details: &mut Map<String, Value>,
) {
    let field_terms_path = workspace.join("terminology").join("field-terms.json");
    if !field_terms_path.exists() {
        errors.push(issue(
            "terminology_missing",
            "工作区缺少 terminology/field-terms.json",
        ));
    } else {
        match read_terminology_registry_file(&field_terms_path) {
            Ok(registry) => {
                let expected = extract_terminology(data_files, command_snapshots).registry;
                validate_terminology_shape(&registry, &expected, errors);
                validate_terminology_entries(&registry, errors, warnings);
                details.insert(
                    "terminology".to_string(),
                    json!({
                        "entry_count": registry_entry_count(&registry),
                        "filled_count": terminology_filled_count(&registry),
                        "speaker_count": registry.get("speaker_names").map(BTreeMap::len).unwrap_or(0),
                        "map_count": registry.get("map_display_names").map(BTreeMap::len).unwrap_or(0),
                    }),
                );
            }
            Err(error) => errors.push(issue(
                "terminology_validate_failed",
                format!("术语表结构校验失败: {error}"),
            )),
        }
    }
    let glossary_path = workspace.join("terminology").join("glossary.json");
    if !glossary_path.exists() {
        errors.push(issue(
            "glossary_missing",
            "工作区缺少 terminology/glossary.json",
        ));
    } else {
        match read_glossary_file(&glossary_path) {
            Ok(glossary) => {
                details.insert(
                    "glossary".to_string(),
                    json!({ "term_count": glossary.len() }),
                );
            }
            Err(error) => errors.push(issue(
                "glossary_validate_failed",
                format!("正文术语表结构校验失败: {error}"),
            )),
        }
    }
}

fn validate_workspace_plugin_rules(
    workspace: &Path,
    plugins: &[Value],
    errors: &mut Vec<crate::AgentIssue>,
    warnings: &mut Vec<crate::AgentIssue>,
    details: &mut Map<String, Value>,
) {
    let path = workspace.join("plugin-rules.json");
    if !path.exists() {
        warnings.push(issue(
            "plugin_rules_missing",
            "工作区缺少 plugin-rules.json",
        ));
        return;
    }
    match fs::read_to_string(&path) {
        Ok(text) => {
            let report = validate_plugin_rules_report(plugins, &text);
            errors.extend(report.errors);
            warnings.extend(report.warnings);
            details.insert("plugin_rules".to_string(), Value::Object(report.details));
        }
        Err(error) => errors.push(issue(
            "plugin_rules_invalid",
            format!("读取插件规则失败: {error}"),
        )),
    }
}

fn validate_workspace_note_tag_rules(
    workspace: &Path,
    data_files: &BTreeMap<String, Value>,
    source_text_required_pattern: &str,
    errors: &mut Vec<crate::AgentIssue>,
    warnings: &mut Vec<crate::AgentIssue>,
    details: &mut Map<String, Value>,
) {
    let path = workspace.join("note-tag-rules.json");
    if !path.exists() {
        errors.push(issue(
            "note_tag_rules_missing",
            "工作区缺少 note-tag-rules.json",
        ));
        return;
    }
    match fs::read_to_string(&path) {
        Ok(text) => {
            let report =
                validate_note_tag_rules_report(data_files, &text, source_text_required_pattern);
            errors.extend(report.errors);
            warnings.extend(report.warnings);
            details.insert("note_tag_rules".to_string(), Value::Object(report.details));
        }
        Err(error) => errors.push(issue(
            "note_tag_rules_invalid",
            format!("读取 Note 标签规则失败: {error}"),
        )),
    }
}

fn validate_workspace_event_rules(
    workspace: &Path,
    command_snapshots: &[EventCommandSnapshot],
    source_text_required_pattern: &str,
    errors: &mut Vec<crate::AgentIssue>,
    warnings: &mut Vec<crate::AgentIssue>,
    details: &mut Map<String, Value>,
) {
    let path = workspace.join("event-command-rules.json");
    if !path.exists() {
        warnings.push(issue(
            "event_command_rules_missing",
            "工作区缺少 event-command-rules.json",
        ));
        return;
    }
    match fs::read_to_string(&path) {
        Ok(text) => {
            let report = validate_event_command_rules_report(
                command_snapshots,
                &text,
                source_text_required_pattern,
            );
            errors.extend(report.errors);
            warnings.extend(report.warnings);
            details.insert(
                "event_command_rules".to_string(),
                Value::Object(report.details),
            );
        }
        Err(error) => errors.push(issue(
            "event_command_rules_invalid",
            format!("读取事件指令规则失败: {error}"),
        )),
    }
}

fn validate_workspace_placeholder_rules(
    registry: &GameRegistry,
    game_record: &GameRecord,
    workspace: &Path,
    source_text_required_pattern: &str,
    errors: &mut Vec<crate::AgentIssue>,
    warnings: &mut Vec<crate::AgentIssue>,
    details: &mut Map<String, Value>,
) {
    let path = workspace.join("placeholder-rules.json");
    if !path.exists() {
        warnings.push(issue(
            "placeholder_rules_missing",
            "工作区缺少 placeholder-rules.json",
        ));
        return;
    }
    let rules_text = match fs::read_to_string(&path) {
        Ok(text) => text,
        Err(error) => {
            errors.push(issue(
                "placeholder_rules_invalid",
                format!("读取占位符规则失败: {error}"),
            ));
            return;
        }
    };
    let parsed_rules = match parse_custom_placeholder_rules_text(&rules_text) {
        Ok(rules) => {
            let report = validate_placeholder_rules_report(&rules, &[], "--placeholder-rules");
            errors.extend(report.errors);
            warnings.extend(report.warnings);
            details.insert(
                "placeholder_rules".to_string(),
                Value::Object(report.details),
            );
            Some(rules)
        }
        Err(error) => {
            errors.push(issue(
                "placeholder_rules_invalid",
                format!("自定义占位符规则不可用: {error}"),
            ));
            None
        }
    };
    let Some(rules) = parsed_rules else {
        errors.push(issue(
            "placeholder_coverage_scan_failed",
            "占位符覆盖扫描失败: 自定义占位符规则不可用",
        ));
        return;
    };
    match load_workspace_active_items(registry, game_record, source_text_required_pattern) {
        Ok(items) => match scan_placeholder_candidates_report(&items, &rules) {
            Ok(report) => {
                errors.extend(report.errors.clone());
                let uncovered_count = report
                    .summary
                    .get("uncovered_count")
                    .and_then(Value::as_u64);
                details.insert(
                    "placeholder_coverage".to_string(),
                    json!({
                        "summary": report.summary,
                        "details": report.details,
                    }),
                );
                match uncovered_count {
                    Some(0) => {}
                    Some(count) => errors.push(issue(
                        "placeholder_coverage_uncovered",
                        format!("还有 {count} 个当前正文会使用但未被规则覆盖的游戏控制符"),
                    )),
                    None => errors.push(issue(
                        "placeholder_coverage_invalid",
                        "占位符候选扫描缺少有效的 uncovered_count",
                    )),
                }
            }
            Err(error) => errors.push(issue(
                "placeholder_coverage_scan_failed",
                format!("占位符覆盖扫描失败: {error}"),
            )),
        },
        Err(error) => errors.push(issue(
            "placeholder_coverage_scan_failed",
            format!("占位符覆盖扫描失败: {error}"),
        )),
    }
}

fn load_workspace_active_items(
    registry: &GameRegistry,
    game_record: &GameRecord,
    source_text_required_pattern: &str,
) -> Result<Vec<ActiveTextItem>> {
    let data_files = read_data_json_files(&game_record.game_path)?;
    let command_snapshots = read_event_command_snapshots(&game_record.game_path)?;
    let plugins = read_plugins_json(&game_record.game_path)?;
    let (plugin_rules, _stale_count) = read_fresh_plugin_rules(registry, game_record, &plugins)?;
    let event_rules = registry.read_event_command_text_rules(&game_record.game_title)?;
    let note_rules = registry.read_note_tag_text_rules(&game_record.game_title)?;
    extract_active_text_items(
        &data_files,
        &command_snapshots,
        &plugins,
        &plugin_rules,
        &event_rules,
        &note_rules,
        source_text_required_pattern,
    )
}

fn read_terminology_registry_file(
    path: &Path,
) -> std::result::Result<BTreeMap<String, BTreeMap<String, String>>, String> {
    let value = read_json_file(path)?;
    let Some(object) = value.as_object() else {
        return Err("字段译名表顶层必须是对象".to_string());
    };
    let actual_categories = object.keys().cloned().collect::<BTreeSet<_>>();
    let expected_categories = TERMINOLOGY_CATEGORIES
        .iter()
        .map(|category| (*category).to_string())
        .collect::<BTreeSet<_>>();
    if actual_categories != expected_categories {
        return Err("术语表类别不完整".to_string());
    }
    let mut registry = BTreeMap::new();
    for category in TERMINOLOGY_CATEGORIES {
        let Some(entries) = object.get(*category).and_then(Value::as_object) else {
            return Err(format!("{category} 必须是对象"));
        };
        let mut normalized_entries = BTreeMap::new();
        for (source_text, translated_text) in entries {
            if source_text.trim().is_empty() {
                return Err(format!("{category} 不能包含空原文"));
            }
            let Some(translated_text) = translated_text.as_str() else {
                return Err(format!("{category}.{source_text} 的译名必须是字符串"));
            };
            normalized_entries.insert(source_text.clone(), translated_text.to_string());
        }
        registry.insert((*category).to_string(), normalized_entries);
    }
    Ok(registry)
}

fn read_glossary_file(path: &Path) -> std::result::Result<BTreeMap<String, String>, String> {
    let value = read_json_file(path)?;
    let Some(object) = value.as_object() else {
        return Err("正文术语表顶层必须是对象".to_string());
    };
    if object.len() != 1 || !object.contains_key("terms") {
        return Err("正文术语表必须只包含 terms 字段".to_string());
    }
    let Some(terms) = object.get("terms").and_then(Value::as_object) else {
        return Err("正文术语表 terms 必须是对象".to_string());
    };
    let mut glossary = BTreeMap::new();
    for (source_text, translated_text) in terms {
        let source_text = source_text.trim();
        if source_text.is_empty() {
            return Err("terms 不能包含空原文".to_string());
        }
        let Some(translated_text) = translated_text.as_str().map(str::trim) else {
            return Err(format!("terms.{source_text} 的译名必须是字符串"));
        };
        if translated_text.is_empty() {
            return Err(format!("terms.{source_text} 不能包含空值"));
        }
        glossary.insert(source_text.to_string(), translated_text.to_string());
    }
    Ok(glossary)
}

fn validate_terminology_shape(
    registry: &BTreeMap<String, BTreeMap<String, String>>,
    expected: &BTreeMap<String, BTreeMap<String, String>>,
    errors: &mut Vec<crate::AgentIssue>,
) {
    for category in TERMINOLOGY_CATEGORIES {
        let current_keys = registry
            .get(*category)
            .map(|entries| entries.keys().cloned().collect::<BTreeSet<_>>())
            .unwrap_or_default();
        let expected_keys = expected
            .get(*category)
            .map(|entries| entries.keys().cloned().collect::<BTreeSet<_>>())
            .unwrap_or_default();
        if current_keys != expected_keys {
            errors.push(issue(
                "terminology_validate_failed",
                format!("术语表类别 {category} 与当前游戏数据不一致"),
            ));
        }
    }
}

fn validate_terminology_entries(
    registry: &BTreeMap<String, BTreeMap<String, String>>,
    errors: &mut Vec<crate::AgentIssue>,
    warnings: &mut Vec<crate::AgentIssue>,
) {
    let empty_count = registry_entry_count(registry) - terminology_filled_count(registry);
    if empty_count > 0 {
        errors.push(issue(
            "terminology_empty_translation",
            format!("术语表存在 {empty_count} 个空译名"),
        ));
    }
    let mut translated_counts = BTreeMap::<String, usize>::new();
    for translated_text in registry
        .values()
        .flat_map(BTreeMap::values)
        .map(|value| value.trim())
        .filter(|value| !value.is_empty())
    {
        *translated_counts
            .entry(translated_text.to_string())
            .or_default() += 1;
    }
    let duplicate_count = translated_counts
        .values()
        .filter(|count| **count > 1)
        .count();
    if duplicate_count > 0 {
        warnings.push(issue(
            "terminology_duplicate_translation",
            format!("术语表存在 {duplicate_count} 组重复译名，需要确认是否合理"),
        ));
    }
}

fn terminology_filled_count(registry: &BTreeMap<String, BTreeMap<String, String>>) -> usize {
    registry
        .values()
        .flat_map(BTreeMap::values)
        .filter(|translated_text| !translated_text.trim().is_empty())
        .count()
}

fn workspace_error_report(workspace: &Path, code: &str, message: String) -> AgentReport {
    AgentReport::from_parts(
        vec![issue(code, message)],
        Vec::new(),
        summary_with_workspace(workspace),
        Map::new(),
    )
}

fn read_json_file(path: &Path) -> std::result::Result<Value, String> {
    let text = fs::read_to_string(path).map_err(|error| format!("读取文件失败: {error}"))?;
    serde_json::from_str(text.trim_start_matches('\u{feff}'))
        .map_err(|error| format!("JSON 解析失败: {error}"))
}

fn write_report_file(path: &Path, report: &AgentReport) -> Result<()> {
    write_text_file(path, &format!("{}\n", report.to_json_text()))
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

fn command_location_parent(location_path: &str) -> String {
    location_path
        .rsplit_once('/')
        .map(|(parent, _index)| parent.to_string())
        .unwrap_or_default()
}

fn command_location_index(location_path: &str) -> Option<i64> {
    location_path
        .rsplit_once('/')
        .and_then(|(_parent, index)| index.parse::<i64>().ok())
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

fn path_json(path: &Path) -> Value {
    json!(path.display().to_string())
}

fn json_object(value: Value) -> Map<String, Value> {
    match value {
        Value::Object(object) => object,
        _ => Map::new(),
    }
}

/// 按 manifest 清理 Agent 临时工作区文件。
///
/// 参数 `workspace` 应指向 Agent 临时工作区目录。函数只删除 `manifest.files`
/// 中位于该目录内部的现存文件或目录，最后删除 manifest 本身；缺少 manifest
/// 或 manifest 结构不合法时返回错误报告，不执行删除。
pub fn cleanup_agent_workspace(workspace: &Path) -> AgentReport {
    let workspace = absolute_path(workspace);
    let manifest_path = workspace.join("manifest.json");
    if !manifest_path.exists() {
        return AgentReport::from_parts(
            vec![issue(
                "manifest_missing",
                "工作区缺少 manifest.json，拒绝自动清理",
            )],
            Vec::new(),
            summary_with_workspace(&workspace),
            Map::new(),
        );
    }

    let manifest_text = match fs::read_to_string(&manifest_path) {
        Ok(text) => text,
        Err(error) => {
            return AgentReport::from_parts(
                vec![issue(
                    "manifest_invalid",
                    format!("读取 manifest.json 失败: {error}"),
                )],
                Vec::new(),
                summary_with_workspace(&workspace),
                Map::new(),
            );
        }
    };
    let manifest: Value = match serde_json::from_str(&manifest_text) {
        Ok(value) => value,
        Err(error) => {
            return AgentReport::from_parts(
                vec![issue(
                    "manifest_invalid",
                    format!("解析 manifest.json 失败: {error}"),
                )],
                Vec::new(),
                summary_with_workspace(&workspace),
                Map::new(),
            );
        }
    };
    let Some(files) = manifest.get("files").and_then(Value::as_array) else {
        return AgentReport::from_parts(
            vec![issue("manifest_invalid", "manifest.files 必须是数组")],
            Vec::new(),
            summary_with_workspace(&workspace),
            Map::new(),
        );
    };

    let workspace_root = match workspace.canonicalize() {
        Ok(path) => path,
        Err(error) => {
            return AgentReport::from_parts(
                vec![issue(
                    "manifest_invalid",
                    format!("解析工作区路径失败: {error}"),
                )],
                Vec::new(),
                summary_with_workspace(&workspace),
                Map::new(),
            );
        }
    };
    let mut deleted_count = 0usize;
    let mut warnings = Vec::new();
    for raw_path in files {
        let Some(raw_path) = raw_path.as_str() else {
            continue;
        };
        let path = absolute_path(Path::new(raw_path));
        if !path.exists() {
            continue;
        }
        let Ok(canonical_path) = path.canonicalize() else {
            continue;
        };
        if !canonical_path.starts_with(&workspace_root) || canonical_path == workspace_root {
            continue;
        }
        match remove_existing_path(&canonical_path) {
            Ok(()) => deleted_count += 1,
            Err(error) => warnings.push(issue(
                "cleanup_failed",
                format!("清理工作区文件失败: {}: {error}", canonical_path.display()),
            )),
        }
    }

    if manifest_path.exists() {
        match fs::remove_file(&manifest_path) {
            Ok(()) => deleted_count += 1,
            Err(error) => warnings.push(issue(
                "cleanup_failed",
                format!("删除 manifest.json 失败: {error}"),
            )),
        }
    }

    let mut summary = summary_with_workspace(&workspace);
    summary.insert("deleted_count".to_string(), json!(deleted_count));
    AgentReport::from_parts(Vec::new(), warnings, summary, Map::new())
}

fn remove_existing_path(path: &Path) -> std::io::Result<()> {
    if path.is_dir() {
        fs::remove_dir_all(path)
    } else {
        fs::remove_file(path)
    }
}

fn summary_with_workspace(workspace: &Path) -> Map<String, Value> {
    let mut summary = Map::new();
    summary.insert("workspace".to_string(), json!(workspace));
    summary
}

fn absolute_path(path: &Path) -> PathBuf {
    if path.is_absolute() {
        return path.to_path_buf();
    }
    match std::env::current_dir() {
        Ok(current_dir) => current_dir.join(path),
        Err(_) => path.to_path_buf(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN;

    #[test]
    fn cleanup_workspace_deletes_only_manifest_files_inside_workspace() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        let workspace = temp.path().join("workspace");
        fs::create_dir_all(workspace.join("nested")).expect("测试工作区应创建成功");
        let kept_file = temp.path().join("outside.txt");
        fs::write(&kept_file, "保留").expect("外部文件应写入成功");
        let removed_file = workspace.join("nested/item.txt");
        fs::write(&removed_file, "删除").expect("工作区文件应写入成功");
        fs::write(
            workspace.join("manifest.json"),
            serde_json::to_string_pretty(&json!({
                "files": [
                    removed_file,
                    kept_file,
                    workspace,
                    1
                ]
            }))
            .expect("manifest 应序列化成功"),
        )
        .expect("manifest 应写入成功");

        let report = cleanup_agent_workspace(&workspace);

        assert_eq!(report.status, "ok");
        assert_eq!(report.summary.get("deleted_count"), Some(&json!(2)));
        assert!(!removed_file.exists());
        assert!(!workspace.join("manifest.json").exists());
        assert!(kept_file.exists());
        assert!(workspace.exists());
    }

    #[test]
    fn cleanup_workspace_reports_missing_manifest() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        let report = cleanup_agent_workspace(temp.path());

        assert_eq!(report.status, "error");
        assert_eq!(report.errors[0].code, "manifest_missing");
    }

    #[test]
    fn prepare_workspace_writes_rule_inputs_and_placeholder_draft() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        let game_dir = temp.path().join("game");
        write_minimal_game(&game_dir);
        let registry = GameRegistry::new(temp.path().join("db"));
        let record = registry.register_game(&game_dir).expect("游戏应注册成功");
        let workspace = temp.path().join("workspace");

        let report = prepare_agent_workspace(
            &registry,
            &record,
            &workspace,
            None,
            Some(vec![357]),
            DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN,
        )
        .expect("工作区应准备成功");

        let placeholder_rules =
            read_json_file(&workspace.join("placeholder-rules.json")).expect("占位符规则应可读取");
        let manifest = read_json_file(&workspace.join("manifest.json")).expect("manifest 应可读取");
        assert_eq!(report.status, "ok");
        assert!(workspace.join("terminology/field-terms.json").exists());
        assert!(
            workspace
                .join("terminology/subtasks/sources/item_terms.json")
                .exists()
        );
        assert!(workspace.join("note-tag-candidates.json").exists());
        assert_eq!(
            placeholder_rules
                .as_object()
                .and_then(|rules| rules.get(r"(?i)\\F\d*\[[^\]\r\n]+\]")),
            Some(&json!("[CUSTOM_FACE_PORTRAIT_{index}]"))
        );
        assert!(manifest.get("workflow").is_some());
    }

    #[test]
    fn validate_workspace_blocks_uncovered_placeholder_rules() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        let game_dir = temp.path().join("game");
        write_minimal_game(&game_dir);
        let registry = GameRegistry::new(temp.path().join("db"));
        let record = registry.register_game(&game_dir).expect("游戏应注册成功");
        let workspace = temp.path().join("workspace");
        prepare_agent_workspace(
            &registry,
            &record,
            &workspace,
            None,
            Some(vec![357]),
            DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN,
        )
        .expect("工作区应准备成功");
        fs::write(workspace.join("placeholder-rules.json"), "{}\n").expect("规则应写入成功");

        let report = validate_agent_workspace(
            &registry,
            &record,
            &workspace,
            DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN,
        );

        let error_codes = report
            .errors
            .iter()
            .map(|error| error.code.as_str())
            .collect::<Vec<_>>();
        assert_eq!(report.status, "error");
        assert!(error_codes.contains(&"placeholder_coverage_uncovered"));
    }

    fn write_minimal_game(game_dir: &Path) {
        fs::create_dir_all(game_dir.join("data")).expect("data 目录应创建成功");
        fs::create_dir_all(game_dir.join("js")).expect("js 目录应创建成功");
        write_json_test_file(
            &game_dir.join("package.json"),
            &json!({"window": {"title": "测试游戏"}}),
        );
        write_json_test_file(
            &game_dir.join("data/System.json"),
            &json!({
                "gameTitle": "测试游戏",
                "terms": {"basic": [], "commands": [], "params": [], "messages": {}},
                "elements": ["", "炎"],
                "skillTypes": [],
                "weaponTypes": [],
                "armorTypes": [],
                "equipTypes": [],
            }),
        );
        write_json_test_file(
            &game_dir.join("data/CommonEvents.json"),
            &json!([
                null,
                {
                    "id": 1,
                    "list": [
                        {"code": 101, "parameters": [0, 0, 0, 2, "村人"]},
                        {"code": 401, "parameters": [r"\F[GuideA]こんにちは\!"]},
                        {"code": 357, "parameters": ["Plugin", "Show", 0, {"message": "外部本文"}]},
                        {"code": 0, "parameters": []}
                    ]
                }
            ]),
        );
        write_json_test_file(&game_dir.join("data/Troops.json"), &json!([]));
        write_json_test_file(
            &game_dir.join("data/Map001.json"),
            &json!({"displayName": "村", "events": []}),
        );
        write_json_test_file(
            &game_dir.join("data/Items.json"),
            &json!([null, {"id": 1, "name": "回復薬", "description": "体力を回復する。", "note": ""}]),
        );
        fs::write(game_dir.join("js/plugins.js"), "var $plugins = [];\n")
            .expect("插件文件应写入成功");
    }

    fn write_json_test_file(path: &Path, value: &Value) {
        let text = serde_json::to_string_pretty(value).expect("JSON 应序列化成功");
        fs::write(path, format!("{text}\n")).expect("JSON 文件应写入成功");
    }
}
