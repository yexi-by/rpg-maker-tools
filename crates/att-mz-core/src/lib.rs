//! A.T.T MZ 的 Rust 核心库。
//!
//! 本库承载 CLI 可复用的业务能力：配置读取、游戏注册表、RPG Maker MZ
//! 基础校验和面向 Agent 的 JSON 报告。CLI 层只负责参数解析和终端输出。

pub mod config;
pub mod db;
pub mod doctor;
pub mod error;
pub mod event_command_rules;
pub mod font_replacement;
pub mod game;
pub mod japanese_residual_rules;
#[allow(dead_code)]
pub(crate) mod native_core;
pub mod note_tag_rules;
pub mod placeholder;
pub mod placeholder_scan;
pub mod plugin_rules;
pub mod report;
pub mod rmmz;
pub mod terminology;
pub mod translate;
pub mod translation_state;
pub mod workspace;
pub mod write_back;

pub use config::{
    DEFAULT_SETTING_FILE_NAME, DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN, EnvironmentOverrides,
    RuntimeSettings, SettingSummary, TextRuleOptions, load_event_command_default_codes,
    load_runtime_settings, load_source_text_required_pattern, load_text_rule_options,
    load_write_back_replacement_font_path,
};
pub use db::{
    DB_DIRECTORY, FontReplacementRecord, GameRecord, GameRegistry, JapaneseResidualRuleRecord,
    TranslationErrorItemRecord, TranslationItemRecord, TranslationQualityErrorSummary,
    TranslationRunRecord,
};
pub use doctor::{DoctorOptions, run_doctor};
pub use error::{AttMzError, Result};
pub use event_command_rules::{
    EventCommandParameterFilter, EventCommandRuleImportResult, EventCommandRuleRecord,
    build_event_command_rule_records_from_import, event_command_rule_prefixes,
    parse_event_command_rule_import_text, should_refresh_event_command_translation_items,
    validate_event_command_rules_report,
};
pub use font_replacement::{
    FontReplacementSummary, FontRestoreSummary, apply_font_replacement_to_active_game,
    restore_font_report,
};
pub use game::{read_game_title, validate_game_directory};
pub use japanese_residual_rules::{
    JapaneseResidualRuleImportFile, JapaneseResidualRuleSpec,
    build_japanese_residual_rule_records_from_import,
    build_japanese_residual_rule_records_from_text, japanese_residual_rules_import_report,
    japanese_residual_rules_invalid_report, parse_japanese_residual_rule_import_text,
    validate_japanese_residual_rules_report,
};
pub use note_tag_rules::{
    NoteTagExtractedItem, NoteTagRuleImportResult, NoteTagRuleRecord,
    build_note_tag_rule_records_from_import, export_note_tag_candidates_report,
    extract_note_tag_items, parse_note_tag_rule_import_text, stale_note_tag_translation_paths,
    validate_note_tag_rules_report,
};
pub use placeholder::{
    PlaceholderRule, build_text_for_model_lines, parse_custom_placeholder_rules_text,
    validate_placeholder_rules_report,
};
pub use placeholder_scan::{
    ActiveTextItem, PlaceholderCandidate, build_placeholder_rule_draft_report,
    extract_active_text_items, scan_placeholder_candidates_report,
};
pub use plugin_rules::{
    PluginRuleRecord, build_plugin_hash, build_plugin_rule_records_from_import,
    parse_plugin_rule_import_text, validate_plugin_rules_report,
};
pub use report::{AgentIssue, AgentReport, issue};
pub use rmmz::{
    EventCommandSnapshot, export_event_commands_json_file, export_plugins_json_file,
    read_data_json_files, read_event_command_snapshots, read_plugins_json,
    resolve_event_command_codes,
};
pub use terminology::{
    TERMINOLOGY_CATEGORIES, TerminologyArtifacts, TerminologyImportResult,
    empty_terminology_registry, export_terminology_report, extract_terminology,
    import_terminology_report, read_glossary_file, read_terminology_registry_file,
    registry_entry_count, terminology_filled_count, terminology_invalid_report,
    validate_terminology_registry_shape, write_terminology_report,
};
pub use translate::{TranslationRunLimits, translate_report};
pub use translation_state::{
    export_pending_translations_report, export_quality_fix_template_report,
    import_manual_translations_report, load_active_translation_items, quality_report,
    reset_translations_report, translation_status_report,
};
pub use workspace::{cleanup_agent_workspace, prepare_agent_workspace, validate_agent_workspace};
pub use write_back::write_back_report;
