//! A.T.T MZ Rust CLI 入口。
//!
//! 本入口负责解析命令行参数、初始化日志、分发已迁移命令，并把业务错误
//! 转成稳定的终端输出或 Agent JSON 报告。

use std::ffi::OsString;
use std::fs;
use std::path::{Path, PathBuf};

use att_mz_core::{
    ActiveTextItem, AgentReport, DoctorOptions, GameRecord, GameRegistry, PlaceholderRule,
    RuntimeSettings, TranslationRunLimits, apply_font_replacement_to_active_game,
    build_event_command_rule_records_from_import, build_japanese_residual_rule_records_from_text,
    build_placeholder_rule_draft_report, build_plugin_rule_records_from_import,
    cleanup_agent_workspace, export_event_commands_json_file, export_note_tag_candidates_report,
    export_pending_translations_report, export_plugins_json_file,
    export_quality_fix_template_report, export_terminology_report, extract_active_text_items,
    import_manual_translations_report, import_terminology_report, issue,
    japanese_residual_rules_import_report, japanese_residual_rules_invalid_report,
    load_active_translation_items, load_event_command_default_codes, load_runtime_settings,
    load_source_text_required_pattern, load_text_rule_options,
    load_write_back_replacement_font_path, parse_custom_placeholder_rules_text,
    parse_event_command_rule_import_text, parse_note_tag_rule_import_text,
    parse_plugin_rule_import_text, prepare_agent_workspace, quality_report, read_data_json_files,
    read_event_command_snapshots, read_plugins_json, reset_translations_report,
    resolve_event_command_codes, restore_font_report, run_doctor,
    scan_placeholder_candidates_report, terminology_invalid_report, translate_report,
    translation_status_report, validate_agent_workspace, validate_event_command_rules_report,
    validate_japanese_residual_rules_report, validate_note_tag_rules_report,
    validate_plugin_rules_report, write_back_report, write_terminology_report,
};
use clap::{ArgAction, Args, Parser, Subcommand, error::ErrorKind};
use serde_json::{Map, json};
use tracing::{error, info};

/// A.T.T MZ 的 Rust 命令行参数。
#[derive(Debug, Parser)]
#[command(name = "att-mz", version, about = "RPG Maker 翻译工具命令行入口")]
struct Cli {
    /// 在终端显示 DEBUG 级别日志。
    #[arg(long)]
    debug: bool,

    /// 使用适合外部 Agent 读取的简洁日志。
    #[arg(long)]
    agent_mode: bool,

    /// 子命令。
    #[command(subcommand)]
    command: Commands,
}

/// 当前 CLI 支持的命令集合。
#[derive(Debug, Subcommand)]
enum Commands {
    /// 列出当前已注册游戏。
    List(JsonFlag),
    /// 检查项目配置、模型连接和目标游戏状态。
    Doctor(DoctorCommand),
    /// 注册新的 RPG Maker 游戏目录。
    #[command(name = "add-game")]
    AddGame(AddGameCommand),
    /// 导出插件配置 JSON。
    #[command(name = "export-plugins-json")]
    ExportPluginsJson(ExportPluginsJsonCommand),
    /// 导入插件规则。
    #[command(name = "import-plugin-rules")]
    ImportPluginRules(ImportPluginRulesCommand),
    /// 导出事件指令参数 JSON。
    #[command(name = "export-event-commands-json")]
    ExportEventCommandsJson(ExportEventCommandsJsonCommand),
    /// 导入事件指令规则。
    #[command(name = "import-event-command-rules")]
    ImportEventCommandRules(ImportEventCommandRulesCommand),
    /// 导出 Note 标签候选。
    #[command(name = "export-note-tag-candidates")]
    ExportNoteTagCandidates(ExportNoteTagCandidatesCommand),
    /// 校验 Note 标签规则。
    #[command(name = "validate-note-tag-rules")]
    ValidateNoteTagRules(ValidateNoteTagRulesCommand),
    /// 导入 Note 标签规则。
    #[command(name = "import-note-tag-rules")]
    ImportNoteTagRules(ImportNoteTagRulesCommand),
    /// 扫描自定义控制符候选。
    #[command(name = "scan-placeholder-candidates")]
    ScanPlaceholderCandidates(ScanPlaceholderCandidatesCommand),
    /// 校验自定义占位符规则。
    #[command(name = "validate-placeholder-rules")]
    ValidatePlaceholderRules(ValidatePlaceholderRulesCommand),
    /// 生成自定义占位符规则草稿。
    #[command(name = "build-placeholder-rules")]
    BuildPlaceholderRules(BuildPlaceholderRulesCommand),
    /// 导入自定义占位符规则。
    #[command(name = "import-placeholder-rules")]
    ImportPlaceholderRules(ImportPlaceholderRulesCommand),
    /// 校验插件规则。
    #[command(name = "validate-plugin-rules")]
    ValidatePluginRules(ValidatePluginRulesCommand),
    /// 校验事件指令规则。
    #[command(name = "validate-event-command-rules")]
    ValidateEventCommandRules(ValidateEventCommandRulesCommand),
    /// 准备 Agent 工作区。
    #[command(name = "prepare-agent-workspace")]
    PrepareAgentWorkspace(PrepareAgentWorkspaceCommand),
    /// 校验 Agent 工作区。
    #[command(name = "validate-agent-workspace")]
    ValidateAgentWorkspace(ValidateAgentWorkspaceCommand),
    /// 清理 Agent 工作区。
    #[command(name = "cleanup-agent-workspace")]
    CleanupAgentWorkspace(CleanupAgentWorkspaceCommand),
    /// 生成当前游戏翻译质量报告。
    #[command(name = "quality-report")]
    QualityReport(QualityReportCommand),
    /// 导出还没成功保存译文的正文条目。
    #[command(name = "export-pending-translations")]
    ExportPendingTranslations(ExportPendingTranslationsCommand),
    /// 导出全部未翻译正文。
    #[command(name = "export-untranslated-translations")]
    ExportUntranslatedTranslations(ExportUntranslatedTranslationsCommand),
    /// 导出质量修复模板。
    #[command(name = "export-quality-fix-template")]
    ExportQualityFixTemplate(ExportQualityFixTemplateCommand),
    /// 导入手动填写译文。
    #[command(name = "import-manual-translations")]
    ImportManualTranslations(ImportManualTranslationsCommand),
    /// 重置译文记录。
    #[command(name = "reset-translations")]
    ResetTranslations(ResetTranslationsCommand),
    /// 校验日文残留例外规则。
    #[command(name = "validate-japanese-residual-rules")]
    ValidateJapaneseResidualRules(ValidateJapaneseResidualRulesCommand),
    /// 导入日文残留例外规则。
    #[command(name = "import-japanese-residual-rules")]
    ImportJapaneseResidualRules(ImportJapaneseResidualRulesCommand),
    /// 查看最新正文翻译运行状态。
    #[command(name = "translation-status")]
    TranslationStatus(TranslationStatusCommand),
    /// 翻译指定游戏正文。
    Translate(Box<TranslateCommand>),
    /// 把译文写进游戏文件。
    #[command(name = "write-back")]
    WriteBack(Box<WriteBackCommand>),
    /// 按原件留档对比还原游戏数据中的字体引用。
    #[command(name = "restore-font")]
    RestoreFont(Box<RestoreFontCommand>),
    /// 导出术语表。
    #[command(name = "export-terminology")]
    ExportTerminology(ExportTerminologyCommand),
    /// 导入术语表。
    #[command(name = "import-terminology")]
    ImportTerminology(ImportTerminologyCommand),
    /// 根据数据库中的术语表直接写回稳定名词。
    #[command(name = "write-terminology")]
    WriteTerminology(Box<WriteTerminologyCommand>),
    /// 按固定顺序执行正文翻译和回写。
    #[command(name = "run-all")]
    RunAll(Box<RunAllCommand>),
}

/// 只包含 JSON 输出开关的命令参数。
#[derive(Debug, Args)]
struct JsonFlag {
    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

/// `doctor` 命令参数。
#[derive(Debug, Args)]
struct DoctorCommand {
    /// 目标游戏标题。
    #[arg(long, conflicts_with = "game_path")]
    game: Option<String>,

    /// 已注册目标游戏根目录。
    #[arg(long = "game-path", conflicts_with = "game")]
    game_path: Option<PathBuf>,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,

    /// 跳过模型连通性检查。
    #[arg(long)]
    no_check_llm: bool,
}

/// `add-game` 命令参数。
#[derive(Debug, Args)]
struct AddGameCommand {
    /// RPG Maker 游戏根目录。
    #[arg(long)]
    path: PathBuf,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

/// 需要目标游戏的命令参数。
#[derive(Debug, Args)]
#[group(id = "target_game", required = true, multiple = false, args = ["game", "game_path"])]
struct TargetGameArgs {
    /// 目标游戏标题。
    #[arg(long, conflicts_with = "game_path")]
    game: Option<String>,

    /// 已注册目标游戏根目录。
    #[arg(long = "game-path", conflicts_with = "game")]
    game_path: Option<PathBuf>,
}

/// 可选目标游戏参数。
#[derive(Debug, Args)]
struct OptionalTargetGameArgs {
    /// 目标游戏标题。
    #[arg(long, conflicts_with = "game_path")]
    game: Option<String>,

    /// 已注册目标游戏根目录。
    #[arg(long = "game-path", conflicts_with = "game")]
    game_path: Option<PathBuf>,
}

/// `export-plugins-json` 命令参数。
#[derive(Debug, Args)]
struct ExportPluginsJsonCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 导出的 plugins JSON 文件。
    #[arg(long)]
    output: PathBuf,
}

/// `import-plugin-rules` 命令参数。
#[derive(Debug, Args)]
struct ImportPluginRulesCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 外部插件规则 JSON 文件。
    #[arg(long)]
    input: PathBuf,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

/// `export-event-commands-json` 命令参数。
#[derive(Debug, Args)]
struct ExportEventCommandsJsonCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 导出的事件指令 JSON 文件。
    #[arg(long)]
    output: PathBuf,

    /// 需要导出的事件指令编码数组；传入后覆盖配置文件默认编码数组。
    #[arg(long = "code", num_args = 1.., value_name = "CODE")]
    codes: Vec<i64>,
}

/// `import-event-command-rules` 命令参数。
#[derive(Debug, Args)]
struct ImportEventCommandRulesCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 外部事件指令规则 JSON 文件。
    #[arg(long)]
    input: PathBuf,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

/// `export-note-tag-candidates` 命令参数。
#[derive(Debug, Args)]
struct ExportNoteTagCandidatesCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// Note 标签候选 JSON 输出文件。
    #[arg(long)]
    output: PathBuf,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

/// `validate-note-tag-rules` 命令参数。
#[derive(Debug, Args)]
struct ValidateNoteTagRulesCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// Note 标签规则 JSON 文件。
    #[arg(long)]
    input: PathBuf,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

/// `import-note-tag-rules` 命令参数。
#[derive(Debug, Args)]
struct ImportNoteTagRulesCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// Note 标签规则 JSON 文件。
    #[arg(long)]
    input: PathBuf,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

/// `import-placeholder-rules` 命令参数。
#[derive(Debug, Args)]
#[group(id = "placeholder_rule_source", required = true, multiple = false, args = ["rules", "input"])]
struct ImportPlaceholderRulesCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 占位符规则 JSON 字符串。
    #[arg(long, conflicts_with = "input")]
    rules: Option<String>,

    /// 占位符规则 JSON 文件。
    #[arg(long, conflicts_with = "rules")]
    input: Option<PathBuf>,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

/// `scan-placeholder-candidates` 命令参数。
#[derive(Debug, Args)]
struct ScanPlaceholderCandidatesCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 写出 JSON 报告文件。
    #[arg(long)]
    output: Option<PathBuf>,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,

    /// 本次扫描使用的自定义占位符规则 JSON 字符串。
    #[arg(long = "placeholder-rules", conflicts_with = "input")]
    placeholder_rules: Option<String>,

    /// 本次扫描使用的自定义占位符规则 JSON 文件。
    #[arg(long, conflicts_with = "placeholder_rules")]
    input: Option<PathBuf>,
}

/// `validate-placeholder-rules` 命令参数。
#[derive(Debug, Args)]
struct ValidatePlaceholderRulesCommand {
    /// 可选目标游戏定位参数。
    #[command(flatten)]
    target: OptionalTargetGameArgs,

    /// 写出 JSON 报告文件。
    #[arg(long)]
    output: Option<PathBuf>,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,

    /// 本次校验使用的自定义占位符规则 JSON 字符串。
    #[arg(long = "placeholder-rules", conflicts_with = "input")]
    placeholder_rules: Option<String>,

    /// 本次校验使用的自定义占位符规则 JSON 文件。
    #[arg(long, conflicts_with = "placeholder_rules")]
    input: Option<PathBuf>,

    /// 用于预览替换和还原效果的原文片段，可重复传入。
    #[arg(long = "sample")]
    sample_texts: Vec<String>,
}

/// `build-placeholder-rules` 命令参数。
#[derive(Debug, Args)]
struct BuildPlaceholderRulesCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 占位符规则草稿 JSON 输出文件。
    #[arg(long)]
    output: PathBuf,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

/// `validate-plugin-rules` 命令参数。
#[derive(Debug, Args)]
#[group(id = "plugin_rule_source", required = true, multiple = false, args = ["rules", "input"])]
struct ValidatePluginRulesCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 插件规则 JSON 字符串。
    #[arg(long, conflicts_with = "input")]
    rules: Option<String>,

    /// 插件规则 JSON 文件。
    #[arg(long, conflicts_with = "rules")]
    input: Option<PathBuf>,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

/// `validate-event-command-rules` 命令参数。
#[derive(Debug, Args)]
#[group(id = "event_command_rule_source", required = true, multiple = false, args = ["rules", "input"])]
struct ValidateEventCommandRulesCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 事件指令规则 JSON 字符串。
    #[arg(long, conflicts_with = "input")]
    rules: Option<String>,

    /// 事件指令规则 JSON 文件。
    #[arg(long, conflicts_with = "rules")]
    input: Option<PathBuf>,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

/// `prepare-agent-workspace` 命令参数。
#[derive(Debug, Args)]
struct PrepareAgentWorkspaceCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// Agent 临时工作区输出目录。
    #[arg(long = "output-dir")]
    output_dir: PathBuf,

    /// 需要导出的事件指令编码数组；传入后覆盖配置文件默认编码数组。
    #[arg(long = "code", num_args = 1.., value_name = "CODE")]
    codes: Vec<i64>,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

/// `validate-agent-workspace` 命令参数。
#[derive(Debug, Args)]
struct ValidateAgentWorkspaceCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// Agent 临时工作区目录。
    #[arg(long)]
    workspace: PathBuf,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

/// `cleanup-agent-workspace` 命令参数。
#[derive(Debug, Args)]
struct CleanupAgentWorkspaceCommand {
    /// Agent 临时工作区目录。
    #[arg(long)]
    workspace: PathBuf,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

/// `quality-report` 命令参数。
#[derive(Debug, Args)]
struct QualityReportCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 写出 JSON 报告文件。
    #[arg(long)]
    output: Option<PathBuf>,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

/// `export-pending-translations` 命令参数。
#[derive(Debug, Args)]
struct ExportPendingTranslationsCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 手动填写译文表输出文件。
    #[arg(long)]
    output: PathBuf,

    /// 最多导出的待填写条目数；省略则导出全部。
    #[arg(long)]
    limit: Option<i64>,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

/// `export-untranslated-translations` 命令参数。
#[derive(Debug, Args)]
struct ExportUntranslatedTranslationsCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 全部未翻译正文 JSON 输出文件。
    #[arg(long)]
    output: PathBuf,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

/// `export-quality-fix-template` 命令参数。
#[derive(Debug, Args)]
struct ExportQualityFixTemplateCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 质量问题修复 JSON 输出文件。
    #[arg(long)]
    output: PathBuf,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

/// `import-manual-translations` 命令参数。
#[derive(Debug, Args)]
struct ImportManualTranslationsCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 已填写的手动译文表 JSON 文件。
    #[arg(long)]
    input: PathBuf,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

/// `reset-translations` 命令参数。
#[derive(Debug, Args)]
#[group(id = "reset_translation_source", required = true, multiple = false, args = ["input", "reset_all"])]
struct ResetTranslationsCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 包含需要重置内部位置的 JSON 文件。
    #[arg(long, conflicts_with = "reset_all")]
    input: Option<PathBuf>,

    /// 重置当前提取范围内所有已保存译文。
    #[arg(long = "all", conflicts_with = "input")]
    reset_all: bool,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

/// `validate-japanese-residual-rules` 命令参数。
#[derive(Debug, Args)]
#[group(id = "japanese_residual_rule_source", required = true, multiple = false, args = ["rules", "input"])]
struct ValidateJapaneseResidualRulesCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 日文残留例外规则 JSON 字符串。
    #[arg(long, conflicts_with = "input")]
    rules: Option<String>,

    /// 日文残留例外规则 JSON 文件。
    #[arg(long, conflicts_with = "rules")]
    input: Option<PathBuf>,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

/// `import-japanese-residual-rules` 命令参数。
#[derive(Debug, Args)]
#[group(id = "japanese_residual_import_source", required = true, multiple = false, args = ["rules", "input"])]
struct ImportJapaneseResidualRulesCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 日文残留例外规则 JSON 字符串。
    #[arg(long, conflicts_with = "input")]
    rules: Option<String>,

    /// 日文残留例外规则 JSON 文件。
    #[arg(long, conflicts_with = "rules")]
    input: Option<PathBuf>,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

/// `export-terminology` 命令参数。
#[derive(Debug, Args)]
struct ExportTerminologyCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 临时导出目录；建议放在项目目录之外。
    #[arg(long = "output-dir")]
    output_dir: PathBuf,
}

/// `import-terminology` 命令参数。
#[derive(Debug, Args)]
struct ImportTerminologyCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 已填写的字段译名表 JSON 路径。
    #[arg(long)]
    input: PathBuf,

    /// 已填写的正文术语表 JSON 路径。
    #[arg(long = "glossary-input")]
    glossary_input: PathBuf,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

/// `translate` 命令参数。
#[derive(Debug, Args)]
struct TranslateCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 本次翻译使用的自定义占位符规则 JSON 字符串。
    #[arg(long = "placeholder-rules")]
    placeholder_rules: Option<String>,

    /// 输出本轮翻译摘要 JSON。
    #[arg(long = "json")]
    json_output: bool,

    /// 单次运行限制。
    #[command(flatten)]
    run_limits: TranslationLimitArgs,

    /// 与 Python CLI 保持兼容的配置覆盖参数。
    #[command(flatten)]
    setting_overrides: SettingOverrideArgs,
}

/// `write-back` 命令参数。
#[derive(Debug, Args)]
struct WriteBackCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 输出本轮回写摘要 JSON。
    #[arg(long = "json")]
    json_output: bool,

    /// 明确允许本次写回用配置字体覆盖游戏字体引用。
    #[arg(long = "confirm-font-overwrite")]
    confirm_font_overwrite: bool,

    /// 与 Python CLI 保持兼容的配置覆盖参数。
    #[command(flatten)]
    setting_overrides: SettingOverrideArgs,
}

/// `run-all` 命令参数。
#[derive(Debug, Args)]
struct RunAllCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 本次翻译使用的自定义占位符规则 JSON 字符串。
    #[arg(long = "placeholder-rules")]
    placeholder_rules: Option<String>,

    /// 跳过最终回写阶段。
    #[arg(long = "skip-write-back")]
    skip_write_back: bool,

    /// 明确允许最终写回用配置字体覆盖游戏字体引用。
    #[arg(long = "confirm-font-overwrite")]
    confirm_font_overwrite: bool,

    /// 单次运行限制。
    #[command(flatten)]
    run_limits: TranslationLimitArgs,

    /// 与 Python CLI 保持兼容的配置覆盖参数。
    #[command(flatten)]
    setting_overrides: SettingOverrideArgs,
}

/// 正文翻译运行控制参数。
#[derive(Debug, Args)]
struct TranslationLimitArgs {
    /// 本轮最多处理的还没成功保存译文条目数。
    #[arg(long = "max-items")]
    max_items: Option<usize>,

    /// 本轮最多处理的模型批次数。
    #[arg(long = "max-batches")]
    max_batches: Option<usize>,

    /// 本轮翻译最长运行秒数。
    #[arg(long = "time-limit-seconds")]
    time_limit_seconds: Option<u64>,

    /// 检查没通过的译文比例达到该值时停止本轮。
    #[arg(long = "stop-on-error-rate")]
    stop_on_error_rate: Option<f64>,

    /// 模型限流故障达到该次数时停止本轮。
    #[arg(long = "stop-on-rate-limit-count")]
    stop_on_rate_limit_count: Option<usize>,
}

impl TranslationLimitArgs {
    fn to_core_limits(&self) -> Result<TranslationRunLimits, String> {
        if self.max_items == Some(0) {
            return Err("--max-items 必须是正整数".to_string());
        }
        if self.max_batches == Some(0) {
            return Err("--max-batches 必须是正整数".to_string());
        }
        if self.time_limit_seconds == Some(0) {
            return Err("--time-limit-seconds 必须是正整数".to_string());
        }
        if self.stop_on_rate_limit_count == Some(0) {
            return Err("--stop-on-rate-limit-count 必须是正整数".to_string());
        }
        if let Some(rate) = self.stop_on_error_rate
            && !(rate > 0.0 && rate <= 1.0)
        {
            return Err("--stop-on-error-rate 必须大于 0 且小于等于 1".to_string());
        }
        Ok(TranslationRunLimits {
            max_items: self.max_items,
            max_batches: self.max_batches,
            time_limit_seconds: self.time_limit_seconds,
            stop_on_error_rate: self.stop_on_error_rate,
            stop_on_rate_limit_count: self.stop_on_rate_limit_count,
        })
    }
}

/// `restore-font` 命令参数。
#[derive(Debug, Args)]
struct RestoreFontCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,

    /// 与 Python CLI 保持兼容的配置覆盖参数。
    #[command(flatten)]
    setting_overrides: SettingOverrideArgs,
}

/// `write-terminology` 命令参数。
#[derive(Debug, Args)]
struct WriteTerminologyCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 明确允许本次写回用配置字体覆盖游戏字体引用。
    #[arg(long = "confirm-font-overwrite")]
    confirm_font_overwrite: bool,

    /// 与 Python CLI 保持兼容的配置覆盖参数。
    #[command(flatten)]
    setting_overrides: SettingOverrideArgs,
}

/// 与正文翻译相关的配置覆盖参数。
///
/// `write-terminology` 只需要接收这些参数来保持 CLI 形状兼容；正文翻译和字体
/// 覆盖迁移完成后，这个结构会被对应命令复用并传入完整设置加载流程。
#[derive(Debug, Args)]
struct SettingOverrideArgs {
    /// 正文模型名称。
    #[arg(long = "llm-model")]
    llm_model: Option<String>,

    /// 正文模型请求超时秒数。
    #[arg(long = "llm-timeout")]
    llm_timeout: Option<u64>,

    /// 每批目标 token 上限。
    #[arg(long = "translation-token-size")]
    translation_token_size: Option<u64>,

    /// 字符到 token 的换算系数。
    #[arg(long = "translation-factor")]
    translation_factor: Option<f64>,

    /// 同角色连续补充条目上限。
    #[arg(long = "translation-max-command-items")]
    translation_max_command_items: Option<u64>,

    /// 正文翻译并发 worker 数。
    #[arg(long = "translation-worker-count")]
    translation_worker_count: Option<u64>,

    /// 正文翻译 RPM；传 none 表示不限速。
    #[arg(long = "translation-rpm")]
    translation_rpm: Option<String>,

    /// 可恢复错误重试次数。
    #[arg(long = "translation-retry-count")]
    translation_retry_count: Option<u64>,

    /// 可恢复错误重试间隔秒数。
    #[arg(long = "translation-retry-delay")]
    translation_retry_delay: Option<u64>,

    /// 正文翻译系统提示词文本。
    #[arg(long = "system-prompt")]
    system_prompt: Option<String>,

    /// 用户确认覆盖字体后使用的候选字体路径。
    #[arg(long = "replacement-font-path")]
    replacement_font_path: Option<PathBuf>,

    /// 事件指令参数默认编码数组。
    #[arg(long = "event-command-default-code", action = ArgAction::Append, num_args = 1.., value_name = "CODE")]
    event_command_default_codes: Vec<i32>,

    /// 提取时剥离的成对包裹标点。
    #[arg(long = "strip-wrapping-punctuation-pair", action = ArgAction::Append, num_args = 2, value_names = ["LEFT", "RIGHT"])]
    strip_wrapping_punctuation_pairs: Vec<String>,

    /// 译文必须按源文保留的成对包裹标点。
    #[arg(long = "preserve-wrapping-punctuation-pair", action = ArgAction::Append, num_args = 2, value_names = ["LEFT", "RIGHT"])]
    preserve_wrapping_punctuation_pairs: Vec<String>,

    /// 日文残留检查允许保留的字符数组。
    #[arg(long = "allowed-japanese-char", action = ArgAction::Append, num_args = 1.., value_name = "CHAR")]
    allowed_japanese_chars: Vec<String>,

    /// 日文残留检查允许作为语气尾音的字符数组。
    #[arg(long = "allowed-japanese-tail-char", action = ArgAction::Append, num_args = 1.., value_name = "CHAR")]
    allowed_japanese_tail_chars: Vec<String>,

    /// 长文本优先切行标点数组。
    #[arg(long = "line-split-punctuation", action = ArgAction::Append, num_args = 1.., value_name = "PUNCT")]
    line_split_punctuations: Vec<String>,

    /// 长文本单行宽度上限。
    #[arg(long = "long-text-line-width-limit")]
    long_text_line_width_limit: Option<u64>,

    /// 长文本宽度计数字符正则。
    #[arg(long = "line-width-count-pattern")]
    line_width_count_pattern: Option<String>,

    /// 进入正文翻译的源语言字符正则。
    #[arg(long = "source-text-required-pattern")]
    source_text_required_pattern: Option<String>,

    /// 日文残留片段识别正则。
    #[arg(long = "japanese-segment-pattern")]
    japanese_segment_pattern: Option<String>,

    /// 残留检查前剥离的转义序列正则。
    #[arg(long = "residual-escape-sequence-pattern")]
    residual_escape_sequence_pattern: Option<String>,
}

impl SettingOverrideArgs {
    fn provided_count(&self) -> usize {
        option_presence(&self.llm_model)
            + option_presence(&self.llm_timeout)
            + option_presence(&self.translation_token_size)
            + option_presence(&self.translation_factor)
            + option_presence(&self.translation_max_command_items)
            + option_presence(&self.translation_worker_count)
            + option_presence(&self.translation_rpm)
            + option_presence(&self.translation_retry_count)
            + option_presence(&self.translation_retry_delay)
            + option_presence(&self.system_prompt)
            + option_presence(&self.replacement_font_path)
            + vector_presence(&self.event_command_default_codes)
            + vector_presence(&self.strip_wrapping_punctuation_pairs)
            + vector_presence(&self.preserve_wrapping_punctuation_pairs)
            + vector_presence(&self.allowed_japanese_chars)
            + vector_presence(&self.allowed_japanese_tail_chars)
            + vector_presence(&self.line_split_punctuations)
            + option_presence(&self.long_text_line_width_limit)
            + option_presence(&self.line_width_count_pattern)
            + option_presence(&self.source_text_required_pattern)
            + option_presence(&self.japanese_segment_pattern)
            + option_presence(&self.residual_escape_sequence_pattern)
    }

    fn apply_to_runtime_settings(&self, settings: &mut RuntimeSettings) -> Result<(), String> {
        if let Some(model) = self
            .llm_model
            .as_ref()
            .map(|value| value.trim())
            .filter(|value| !value.is_empty())
        {
            settings.llm.model = model.to_string();
        }
        if let Some(timeout) = self.llm_timeout {
            if timeout == 0 {
                return Err("--llm-timeout 必须是正整数".to_string());
            }
            settings.llm.timeout_seconds = timeout;
        }
        if let Some(token_size) = self.translation_token_size {
            settings.translation_context.token_size =
                positive_usize(token_size, "--translation-token-size")?;
        }
        if let Some(factor) = self.translation_factor {
            if factor <= 0.0 {
                return Err("--translation-factor 必须大于 0".to_string());
            }
            settings.translation_context.factor = factor;
        }
        if let Some(max_command_items) = self.translation_max_command_items {
            settings.translation_context.max_command_items =
                positive_usize(max_command_items, "--translation-max-command-items")?;
        }
        if let Some(worker_count) = self.translation_worker_count {
            settings.text_translation.worker_count =
                positive_usize(worker_count, "--translation-worker-count")?;
        }
        if let Some(rpm_text) = self.translation_rpm.as_ref() {
            let normalized = rpm_text.trim().to_ascii_lowercase();
            if ["none", "null", "off", "unlimited", "no", "不限"].contains(&normalized.as_str()) {
                settings.text_translation.rpm = None;
            } else {
                let rpm = normalized
                    .parse::<usize>()
                    .map_err(|error| format!("--translation-rpm 不是有效 RPM: {error}"))?;
                if rpm == 0 {
                    return Err("--translation-rpm 必须是正整数或 none".to_string());
                }
                settings.text_translation.rpm = Some(rpm);
            }
        }
        if let Some(retry_count) = self.translation_retry_count {
            settings.text_translation.retry_count = usize::try_from(retry_count)
                .map_err(|error| format!("--translation-retry-count 超出平台范围: {error}"))?;
        }
        if let Some(retry_delay) = self.translation_retry_delay {
            settings.text_translation.retry_delay = retry_delay;
        }
        if let Some(system_prompt) = self
            .system_prompt
            .as_ref()
            .map(|value| value.trim())
            .filter(|value| !value.is_empty())
        {
            settings.text_translation.system_prompt = system_prompt.to_string();
        }
        if let Some(path) = self.replacement_font_path.as_ref() {
            settings.replacement_font_path = Some(path.to_string_lossy().trim().to_string());
        }
        if let Some(limit) = self.long_text_line_width_limit {
            settings.text_rules.long_text_line_width_limit =
                positive_usize(limit, "--long-text-line-width-limit")?;
        }
        if let Some(pattern) = self
            .line_width_count_pattern
            .as_ref()
            .map(|value| value.trim())
            .filter(|value| !value.is_empty())
        {
            settings.text_rules.line_width_count_pattern = pattern.to_string();
        }
        if let Some(pattern) = self
            .source_text_required_pattern
            .as_ref()
            .map(|value| value.trim())
            .filter(|value| !value.is_empty())
        {
            settings.source_text_required_pattern = pattern.to_string();
        }
        if let Some(pattern) = self
            .japanese_segment_pattern
            .as_ref()
            .map(|value| value.trim())
            .filter(|value| !value.is_empty())
        {
            settings.text_rules.japanese_segment_pattern = pattern.to_string();
        }
        if let Some(pattern) = self
            .residual_escape_sequence_pattern
            .as_ref()
            .map(|value| value.trim())
            .filter(|value| !value.is_empty())
        {
            settings.text_rules.residual_escape_sequence_pattern = pattern.to_string();
        }
        if !self.allowed_japanese_chars.is_empty() {
            settings.text_rules.allowed_japanese_chars = self.allowed_japanese_chars.clone();
        }
        if !self.allowed_japanese_tail_chars.is_empty() {
            settings.text_rules.allowed_japanese_tail_chars =
                self.allowed_japanese_tail_chars.clone();
        }
        Ok(())
    }
}

fn positive_usize(value: u64, name: &str) -> Result<usize, String> {
    if value == 0 {
        return Err(format!("{name} 必须是正整数"));
    }
    usize::try_from(value).map_err(|error| format!("{name} 超出平台范围: {error}"))
}

fn build_runtime_settings(overrides: &SettingOverrideArgs) -> Result<RuntimeSettings, String> {
    let mut settings = load_runtime_settings(None).map_err(|error| error.to_string())?;
    overrides.apply_to_runtime_settings(&mut settings)?;
    Ok(settings)
}

fn option_presence<T>(value: &Option<T>) -> usize {
    if value.is_some() { 1 } else { 0 }
}

fn vector_presence<T>(value: &[T]) -> usize {
    if value.is_empty() { 0 } else { 1 }
}

/// `translation-status` 命令参数。
#[derive(Debug, Args)]
struct TranslationStatusCommand {
    /// 目标游戏定位参数。
    #[command(flatten)]
    target: TargetGameArgs,

    /// 输出机器可读 JSON。
    #[arg(long = "json")]
    json_output: bool,
}

fn main() -> anyhow::Result<()> {
    let raw_args: Vec<OsString> = std::env::args_os().collect();
    let raw_json_output = raw_args.iter().any(|arg| arg == "--json");
    let cli = match Cli::try_parse_from(&raw_args) {
        Ok(cli) => cli,
        Err(error) => {
            let exit_code = if matches!(
                error.kind(),
                ErrorKind::DisplayHelp | ErrorKind::DisplayVersion
            ) {
                0
            } else {
                2
            };
            if raw_json_output {
                print_json_error("argument_error", error.to_string(), "");
            } else {
                let _ = error.print();
            }
            std::process::exit(exit_code);
        }
    };

    setup_logging(cli.debug, cli.agent_mode);
    info!("CLI 运行开始");
    let exit_code = dispatch(cli);
    info!("CLI 运行结束 退出码 {}", exit_code);
    std::process::exit(exit_code);
}

fn dispatch(cli: Cli) -> i32 {
    let registry = GameRegistry::default();
    match cli.command {
        Commands::List(args) => run_list_command(&registry, args.json_output),
        Commands::Doctor(args) => run_doctor_command(&registry, args),
        Commands::AddGame(args) => run_add_game_command(&registry, args),
        Commands::ExportPluginsJson(args) => run_export_plugins_json_command(&registry, args),
        Commands::ImportPluginRules(args) => run_import_plugin_rules_command(&registry, args),
        Commands::ExportEventCommandsJson(args) => {
            run_export_event_commands_json_command(&registry, args)
        }
        Commands::ImportEventCommandRules(args) => {
            run_import_event_command_rules_command(&registry, args)
        }
        Commands::ExportNoteTagCandidates(args) => {
            run_export_note_tag_candidates_command(&registry, args)
        }
        Commands::ValidateNoteTagRules(args) => {
            run_validate_note_tag_rules_command(&registry, args)
        }
        Commands::ImportNoteTagRules(args) => run_import_note_tag_rules_command(&registry, args),
        Commands::ScanPlaceholderCandidates(args) => {
            run_scan_placeholder_candidates_command(&registry, args)
        }
        Commands::ImportPlaceholderRules(args) => {
            run_import_placeholder_rules_command(&registry, args)
        }
        Commands::ValidatePlaceholderRules(args) => {
            run_validate_placeholder_rules_command(&registry, args)
        }
        Commands::BuildPlaceholderRules(args) => {
            run_build_placeholder_rules_command(&registry, args)
        }
        Commands::ValidatePluginRules(args) => run_validate_plugin_rules_command(&registry, args),
        Commands::ValidateEventCommandRules(args) => {
            run_validate_event_command_rules_command(&registry, args)
        }
        Commands::PrepareAgentWorkspace(args) => {
            run_prepare_agent_workspace_command(&registry, args)
        }
        Commands::ValidateAgentWorkspace(args) => {
            run_validate_agent_workspace_command(&registry, args)
        }
        Commands::CleanupAgentWorkspace(args) => run_cleanup_agent_workspace_command(args),
        Commands::QualityReport(args) => run_quality_report_command(&registry, args),
        Commands::ExportPendingTranslations(args) => {
            run_export_pending_translations_command(&registry, args)
        }
        Commands::ExportUntranslatedTranslations(args) => {
            run_export_untranslated_translations_command(&registry, args)
        }
        Commands::ExportQualityFixTemplate(args) => {
            run_export_quality_fix_template_command(&registry, args)
        }
        Commands::ImportManualTranslations(args) => {
            run_import_manual_translations_command(&registry, args)
        }
        Commands::ResetTranslations(args) => run_reset_translations_command(&registry, args),
        Commands::ValidateJapaneseResidualRules(args) => {
            run_validate_japanese_residual_rules_command(&registry, args)
        }
        Commands::ImportJapaneseResidualRules(args) => {
            run_import_japanese_residual_rules_command(&registry, args)
        }
        Commands::Translate(args) => run_translate_command(&registry, *args),
        Commands::WriteBack(args) => run_write_back_command(&registry, *args),
        Commands::RestoreFont(args) => run_restore_font_command(&registry, *args),
        Commands::ExportTerminology(args) => run_export_terminology_command(&registry, args),
        Commands::ImportTerminology(args) => run_import_terminology_command(&registry, args),
        Commands::WriteTerminology(args) => run_write_terminology_command(&registry, *args),
        Commands::RunAll(args) => run_all_command(&registry, *args),
        Commands::TranslationStatus(args) => run_translation_status_command(&registry, args),
    }
}

fn run_list_command(registry: &GameRegistry, json_output: bool) -> i32 {
    match registry.list_games() {
        Ok(items) => {
            if json_output {
                let games: Vec<_> = items
                    .iter()
                    .map(|item| {
                        json!({
                            "game_title": item.game_title,
                            "game_path": item.game_path,
                            "db_path": item.db_path,
                        })
                    })
                    .collect();
                let mut summary = Map::new();
                summary.insert("game_count".to_string(), json!(items.len()));
                let mut details = Map::new();
                details.insert("games".to_string(), json!(games));
                println!(
                    "{}",
                    AgentReport::from_parts(Vec::new(), Vec::new(), summary, details)
                        .to_json_text()
                );
                return 0;
            }
            if items.is_empty() {
                info!("当前还没有注册任何游戏");
                return 0;
            }
            println!("已注册游戏");
            for item in items {
                println!(
                    "- {} | {} | {}",
                    item.game_title,
                    item.game_path.display(),
                    item.db_path.display()
                );
            }
            0
        }
        Err(error) => {
            if json_output {
                print_json_error("business_error", error.to_string(), "");
            } else {
                error!("命令执行失败：{}", error);
            }
            1
        }
    }
}

fn run_doctor_command(registry: &GameRegistry, args: DoctorCommand) -> i32 {
    let game_title = match resolve_optional_target_game_title(registry, args.game, args.game_path) {
        Ok(game_title) => game_title,
        Err(error) => {
            if args.json_output {
                print_json_error("business_error", error.to_string(), "");
            } else {
                error!("命令执行失败：{}", error);
            }
            return 1;
        }
    };
    let report = run_doctor(
        &DoctorOptions {
            game_title,
            check_llm: !args.no_check_llm,
            setting_path: None,
        },
        registry,
    );
    if args.json_output {
        println!("{}", report.to_json_text());
    } else {
        render_report("环境诊断报告", &report);
    }
    if report.has_errors() { 1 } else { 0 }
}

fn run_add_game_command(registry: &GameRegistry, args: AddGameCommand) -> i32 {
    match registry.register_game(&args.path) {
        Ok(record) => {
            if args.json_output {
                let mut summary = Map::new();
                summary.insert("game_title".to_string(), json!(record.game_title));
                let mut details = Map::new();
                details.insert("next_game_argument".to_string(), json!(record.game_title));
                println!(
                    "{}",
                    AgentReport::from_parts(Vec::new(), Vec::new(), summary, details)
                        .to_json_text()
                );
            } else {
                println!("游戏注册完成 标题 {}", record.game_title);
            }
            0
        }
        Err(error) => {
            if args.json_output {
                print_json_error("business_error", error.to_string(), "");
            } else {
                error!("命令执行失败：{}", error);
            }
            1
        }
    }
}

fn run_export_plugins_json_command(registry: &GameRegistry, args: ExportPluginsJsonCommand) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            error!("命令执行失败：{}", error);
            return 1;
        }
    };
    match export_plugins_json_file(&game_record.game_path, &args.output) {
        Ok(()) => 0,
        Err(error) => {
            error!("命令执行失败：{}", error);
            1
        }
    }
}

fn run_import_plugin_rules_command(registry: &GameRegistry, args: ImportPluginRulesCommand) -> i32 {
    let input_path = args.input.clone();
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            return report_plugin_import_error(args.json_output, "", &input_path, error);
        }
    };
    let rules_text = match fs::read_to_string(&input_path) {
        Ok(text) => text,
        Err(error) => {
            return report_plugin_import_error(
                args.json_output,
                &game_record.game_title,
                &input_path,
                format!("读取输入文件失败：{}: {error}", input_path.display()),
            );
        }
    };
    let import_file = match parse_plugin_rule_import_text(&rules_text) {
        Ok(import_file) => import_file,
        Err(error) => {
            return report_plugin_import_error(
                args.json_output,
                &game_record.game_title,
                &input_path,
                error.to_string(),
            );
        }
    };
    let plugins = match read_plugins_json(&game_record.game_path) {
        Ok(plugins) => plugins,
        Err(error) => {
            return report_plugin_import_error(
                args.json_output,
                &game_record.game_title,
                &input_path,
                error.to_string(),
            );
        }
    };
    let records = match build_plugin_rule_records_from_import(&plugins, &import_file) {
        Ok(records) => records,
        Err(error) => {
            return report_plugin_import_error(
                args.json_output,
                &game_record.game_title,
                &input_path,
                error.to_string(),
            );
        }
    };
    match registry.replace_plugin_text_rules(&game_record.game_title, &records) {
        Ok(summary) => {
            if args.json_output {
                let mut report_summary = Map::new();
                report_summary.insert("game".to_string(), json!(game_record.game_title));
                report_summary.insert("input".to_string(), json!(input_path));
                report_summary.insert(
                    "imported_plugin_count".to_string(),
                    json!(summary.imported_plugin_count),
                );
                report_summary.insert(
                    "imported_rule_count".to_string(),
                    json!(summary.imported_rule_count),
                );
                report_summary.insert(
                    "deleted_translation_items".to_string(),
                    json!(summary.deleted_translation_items),
                );
                println!(
                    "{}",
                    AgentReport::from_parts(Vec::new(), Vec::new(), report_summary, Map::new())
                        .to_json_text()
                );
            }
            0
        }
        Err(error) => report_plugin_import_error(
            args.json_output,
            &game_record.game_title,
            &input_path,
            error.to_string(),
        ),
    }
}

fn run_export_event_commands_json_command(
    registry: &GameRegistry,
    args: ExportEventCommandsJsonCommand,
) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            error!("命令执行失败：{}", error);
            return 1;
        }
    };
    let cli_codes = if args.codes.is_empty() {
        None
    } else {
        Some(args.codes)
    };
    let default_codes = if cli_codes.is_some() {
        None
    } else {
        match load_event_command_default_codes(None) {
            Ok(codes) => Some(codes),
            Err(error) => {
                error!("命令执行失败：{}", error);
                return 1;
            }
        }
    };
    let codes = match resolve_event_command_codes(cli_codes, default_codes) {
        Ok(codes) => codes,
        Err(error) => {
            error!("命令执行失败：{}", error);
            return 1;
        }
    };
    match export_event_commands_json_file(&game_record.game_path, &args.output, &codes) {
        Ok(_count) => 0,
        Err(error) => {
            error!("命令执行失败：{}", error);
            1
        }
    }
}

fn run_import_event_command_rules_command(
    registry: &GameRegistry,
    args: ImportEventCommandRulesCommand,
) -> i32 {
    let input_path = args.input.clone();
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            return report_event_command_import_error(args.json_output, "", &input_path, error);
        }
    };
    let rules_text = match fs::read_to_string(&input_path) {
        Ok(text) => text,
        Err(error) => {
            return report_event_command_import_error(
                args.json_output,
                &game_record.game_title,
                &input_path,
                format!("读取输入文件失败：{}: {error}", input_path.display()),
            );
        }
    };
    let import_file = match parse_event_command_rule_import_text(&rules_text) {
        Ok(import_file) => import_file,
        Err(error) => {
            return report_event_command_import_error(
                args.json_output,
                &game_record.game_title,
                &input_path,
                error.to_string(),
            );
        }
    };
    let command_snapshots = match read_event_command_snapshots(&game_record.game_path) {
        Ok(command_snapshots) => command_snapshots,
        Err(error) => {
            return report_event_command_import_error(
                args.json_output,
                &game_record.game_title,
                &input_path,
                error.to_string(),
            );
        }
    };
    let records =
        match build_event_command_rule_records_from_import(&command_snapshots, &import_file) {
            Ok(records) => records,
            Err(error) => {
                return report_event_command_import_error(
                    args.json_output,
                    &game_record.game_title,
                    &input_path,
                    error.to_string(),
                );
            }
        };
    match registry.replace_event_command_text_rules(
        &game_record.game_title,
        &records,
        &command_snapshots,
    ) {
        Ok(summary) => {
            if args.json_output {
                let mut report_summary = Map::new();
                report_summary.insert("game".to_string(), json!(game_record.game_title));
                report_summary.insert("input".to_string(), json!(input_path));
                report_summary.insert(
                    "imported_rule_group_count".to_string(),
                    json!(summary.imported_rule_group_count),
                );
                report_summary.insert(
                    "imported_path_rule_count".to_string(),
                    json!(summary.imported_path_rule_count),
                );
                report_summary.insert(
                    "deleted_translation_items".to_string(),
                    json!(summary.deleted_translation_items),
                );
                println!(
                    "{}",
                    AgentReport::from_parts(Vec::new(), Vec::new(), report_summary, Map::new())
                        .to_json_text()
                );
            }
            0
        }
        Err(error) => report_event_command_import_error(
            args.json_output,
            &game_record.game_title,
            &input_path,
            error.to_string(),
        ),
    }
}

fn run_export_note_tag_candidates_command(
    registry: &GameRegistry,
    args: ExportNoteTagCandidatesCommand,
) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            let report = note_tag_rules_invalid_report(error);
            return emit_report(report, None, args.json_output, "Note 标签候选导出报告");
        }
    };
    let data_files = match read_data_json_files(&game_record.game_path) {
        Ok(data_files) => data_files,
        Err(error) => {
            let report = note_tag_rules_invalid_report(error.to_string());
            return emit_report(report, None, args.json_output, "Note 标签候选导出报告");
        }
    };
    let source_text_required_pattern = match load_source_text_required_pattern(None) {
        Ok(pattern) => pattern,
        Err(error) => {
            let report = note_tag_rules_invalid_report(error.to_string());
            return emit_report(report, None, args.json_output, "Note 标签候选导出报告");
        }
    };
    let report = match export_note_tag_candidates_report(
        &data_files,
        &args.output,
        &source_text_required_pattern,
    ) {
        Ok(report) => report,
        Err(error) => {
            let report = note_tag_rules_invalid_report(error.to_string());
            return emit_report(report, None, args.json_output, "Note 标签候选导出报告");
        }
    };
    emit_report(
        report,
        Some(&args.output),
        args.json_output,
        "Note 标签候选导出报告",
    )
}

fn run_validate_note_tag_rules_command(
    registry: &GameRegistry,
    args: ValidateNoteTagRulesCommand,
) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            let report = note_tag_rules_invalid_report(error);
            return emit_report(report, None, args.json_output, "Note 标签规则校验报告");
        }
    };
    let rules_text = match fs::read_to_string(&args.input) {
        Ok(text) => text,
        Err(error) => {
            let report = note_tag_rules_invalid_report(format!(
                "读取输入文件失败：{}: {error}",
                args.input.display()
            ));
            return emit_report(report, None, args.json_output, "Note 标签规则校验报告");
        }
    };
    let data_files = match read_data_json_files(&game_record.game_path) {
        Ok(data_files) => data_files,
        Err(error) => {
            let report = note_tag_rules_invalid_report(error.to_string());
            return emit_report(report, None, args.json_output, "Note 标签规则校验报告");
        }
    };
    let source_text_required_pattern = match load_source_text_required_pattern(None) {
        Ok(pattern) => pattern,
        Err(error) => {
            let report = note_tag_rules_invalid_report(error.to_string());
            return emit_report(report, None, args.json_output, "Note 标签规则校验报告");
        }
    };
    let report =
        validate_note_tag_rules_report(&data_files, &rules_text, &source_text_required_pattern);
    emit_report(report, None, args.json_output, "Note 标签规则校验报告")
}

fn run_import_note_tag_rules_command(
    registry: &GameRegistry,
    args: ImportNoteTagRulesCommand,
) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => return report_note_tag_import_error(args.json_output, error),
    };
    let rules_text = match fs::read_to_string(&args.input) {
        Ok(text) => text,
        Err(error) => {
            return report_note_tag_import_error(
                args.json_output,
                format!("读取输入文件失败：{}: {error}", args.input.display()),
            );
        }
    };
    let import_file = match parse_note_tag_rule_import_text(&rules_text) {
        Ok(import_file) => import_file,
        Err(error) => return report_note_tag_import_error(args.json_output, error.to_string()),
    };
    let data_files = match read_data_json_files(&game_record.game_path) {
        Ok(data_files) => data_files,
        Err(error) => return report_note_tag_import_error(args.json_output, error.to_string()),
    };
    let source_text_required_pattern = match load_source_text_required_pattern(None) {
        Ok(pattern) => pattern,
        Err(error) => return report_note_tag_import_error(args.json_output, error.to_string()),
    };
    let records = match att_mz_core::build_note_tag_rule_records_from_import(
        &data_files,
        &import_file,
        &source_text_required_pattern,
    ) {
        Ok(records) => records,
        Err(error) => return report_note_tag_import_error(args.json_output, error.to_string()),
    };
    match registry.replace_note_tag_text_rules(
        &game_record.game_title,
        &records,
        &data_files,
        &source_text_required_pattern,
    ) {
        Ok(summary) => {
            let warnings = if records.is_empty() {
                vec![issue("note_tag_rules_empty", "已导入空 Note 标签规则")]
            } else {
                Vec::new()
            };
            let mut report_summary = Map::new();
            report_summary.insert("file_count".to_string(), json!(summary.imported_file_count));
            report_summary.insert("tag_count".to_string(), json!(summary.imported_tag_count));
            report_summary.insert(
                "deleted_translation_items".to_string(),
                json!(summary.deleted_translation_items),
            );
            let mut details = Map::new();
            details.insert(
                "rules".to_string(),
                json!(
                    records
                        .iter()
                        .map(|record| json!({
                            "file_name": record.file_name,
                            "tag_names": record.tag_names,
                        }))
                        .collect::<Vec<_>>()
                ),
            );
            emit_report(
                AgentReport::from_parts(Vec::new(), warnings, report_summary, details),
                None,
                args.json_output,
                "Note 标签规则导入报告",
            )
        }
        Err(error) => report_note_tag_import_error(args.json_output, error.to_string()),
    }
}

fn run_import_placeholder_rules_command(
    registry: &GameRegistry,
    args: ImportPlaceholderRulesCommand,
) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => return report_placeholder_import_error(args.json_output, "", error),
    };
    let rules_text = match read_placeholder_rules_text(args.rules, args.input) {
        Ok(text) => text,
        Err(error) => {
            return report_placeholder_import_error(
                args.json_output,
                &game_record.game_title,
                error.to_string(),
            );
        }
    };
    let rules = match parse_custom_placeholder_rules_text(&rules_text) {
        Ok(rules) => rules,
        Err(error) => {
            return report_placeholder_import_error(
                args.json_output,
                &game_record.game_title,
                error.to_string(),
            );
        }
    };
    match registry.replace_placeholder_rules(&game_record.game_title, &rules) {
        Ok(imported_rule_count) => {
            if args.json_output {
                let warnings = if imported_rule_count == 0 {
                    vec![issue("placeholder_rules_empty", "已导入空自定义占位符规则")]
                } else {
                    Vec::new()
                };
                let mut summary = Map::new();
                summary.insert("game".to_string(), json!(game_record.game_title));
                summary.insert(
                    "imported_rule_count".to_string(),
                    json!(imported_rule_count),
                );
                println!(
                    "{}",
                    AgentReport::from_parts(Vec::new(), warnings, summary, Map::new())
                        .to_json_text()
                );
            }
            0
        }
        Err(error) => report_placeholder_import_error(
            args.json_output,
            &game_record.game_title,
            error.to_string(),
        ),
    }
}

fn run_scan_placeholder_candidates_command(
    registry: &GameRegistry,
    args: ScanPlaceholderCandidatesCommand,
) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            let report = placeholder_rules_invalid_report("当前游戏数据库", &[], error);
            return emit_report(
                report,
                args.output.as_ref(),
                args.json_output,
                "自定义控制符候选报告",
            );
        }
    };
    let custom_rules = match resolve_scan_placeholder_rules(
        registry,
        &game_record.game_title,
        args.placeholder_rules,
        args.input,
    ) {
        Ok(rules) => rules,
        Err(error) => {
            let report = placeholder_rules_invalid_report("自定义占位符规则", &[], error);
            return emit_report(
                report,
                args.output.as_ref(),
                args.json_output,
                "自定义控制符候选报告",
            );
        }
    };
    let items = match load_active_text_items(registry, &game_record) {
        Ok(items) => items,
        Err(error) => {
            let report = placeholder_scan_failed_report(error);
            return emit_report(
                report,
                args.output.as_ref(),
                args.json_output,
                "自定义控制符候选报告",
            );
        }
    };
    let report = match scan_placeholder_candidates_report(&items, &custom_rules) {
        Ok(report) => report,
        Err(error) => placeholder_scan_failed_report(error.to_string()),
    };
    emit_report(
        report,
        args.output.as_ref(),
        args.json_output,
        "自定义控制符候选报告",
    )
}

fn run_validate_placeholder_rules_command(
    registry: &GameRegistry,
    args: ValidatePlaceholderRulesCommand,
) -> i32 {
    let source_text = match read_optional_rules_text(args.placeholder_rules, args.input) {
        Ok(text) => text,
        Err(error) => {
            let report =
                placeholder_rules_invalid_report("--placeholder-rules", &args.sample_texts, error);
            return emit_report(
                report,
                args.output.as_ref(),
                args.json_output,
                "自定义占位符规则校验报告",
            );
        }
    };
    let game_title = match resolve_optional_game_title(registry, args.target) {
        Ok(game_title) => game_title,
        Err(error) => {
            let report =
                placeholder_rules_invalid_report("当前游戏数据库", &args.sample_texts, error);
            return emit_report(
                report,
                args.output.as_ref(),
                args.json_output,
                "自定义占位符规则校验报告",
            );
        }
    };
    let (rules, source_label) = match source_text {
        Some(text) => match parse_custom_placeholder_rules_text(&text) {
            Ok(rules) => (rules, "--placeholder-rules".to_string()),
            Err(error) => {
                let report = placeholder_rules_invalid_report(
                    "--placeholder-rules",
                    &args.sample_texts,
                    error.to_string(),
                );
                return emit_report(
                    report,
                    args.output.as_ref(),
                    args.json_output,
                    "自定义占位符规则校验报告",
                );
            }
        },
        None => match game_title {
            Some(game_title) => match registry.read_placeholder_rules(&game_title) {
                Ok(rules) => (rules, "当前游戏数据库".to_string()),
                Err(error) => {
                    let report = placeholder_rules_invalid_report(
                        "当前游戏数据库",
                        &args.sample_texts,
                        error.to_string(),
                    );
                    return emit_report(
                        report,
                        args.output.as_ref(),
                        args.json_output,
                        "自定义占位符规则校验报告",
                    );
                }
            },
            None => (Vec::new(), "空规则".to_string()),
        },
    };
    let report =
        att_mz_core::validate_placeholder_rules_report(&rules, &args.sample_texts, &source_label);
    emit_report(
        report,
        args.output.as_ref(),
        args.json_output,
        "自定义占位符规则校验报告",
    )
}

fn run_build_placeholder_rules_command(
    registry: &GameRegistry,
    args: BuildPlaceholderRulesCommand,
) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            let report = placeholder_scan_failed_report(error);
            return emit_report(report, None, args.json_output, "占位符规则草稿报告");
        }
    };
    let items = match load_active_text_items(registry, &game_record) {
        Ok(items) => items,
        Err(error) => {
            let report = placeholder_scan_failed_report(error);
            return emit_report(report, None, args.json_output, "占位符规则草稿报告");
        }
    };
    let (report, draft_rules) = match build_placeholder_rule_draft_report(&items, &args.output) {
        Ok(result) => result,
        Err(error) => {
            let report = placeholder_scan_failed_report(error.to_string());
            return emit_report(report, None, args.json_output, "占位符规则草稿报告");
        }
    };
    if let Some(parent) = args.output.parent()
        && let Err(error) = fs::create_dir_all(parent)
    {
        let report = placeholder_scan_failed_report(format!("创建输出目录失败：{error}"));
        return emit_report(report, None, args.json_output, "占位符规则草稿报告");
    }
    let draft_text = match serde_json::to_string_pretty(&draft_rules) {
        Ok(text) => format!("{text}\n"),
        Err(error) => {
            let report = placeholder_scan_failed_report(format!("生成规则草稿 JSON 失败：{error}"));
            return emit_report(report, None, args.json_output, "占位符规则草稿报告");
        }
    };
    if let Err(error) = fs::write(&args.output, draft_text) {
        let report = placeholder_scan_failed_report(format!(
            "写出占位符规则草稿失败：{}: {error}",
            args.output.display()
        ));
        return emit_report(report, None, args.json_output, "占位符规则草稿报告");
    }
    emit_report(report, None, args.json_output, "占位符规则草稿报告")
}

fn run_validate_plugin_rules_command(
    registry: &GameRegistry,
    args: ValidatePluginRulesCommand,
) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            let report = plugin_rules_invalid_report(error);
            return emit_report(report, None, args.json_output, "插件规则校验报告");
        }
    };
    let rules_text = match read_placeholder_rules_text(args.rules, args.input) {
        Ok(text) => text,
        Err(error) => {
            let report = plugin_rules_invalid_report(error);
            return emit_report(report, None, args.json_output, "插件规则校验报告");
        }
    };
    let plugins = match read_plugins_json(&game_record.game_path) {
        Ok(plugins) => plugins,
        Err(error) => {
            let report = plugin_rules_invalid_report(error.to_string());
            return emit_report(report, None, args.json_output, "插件规则校验报告");
        }
    };
    let report = validate_plugin_rules_report(&plugins, &rules_text);
    emit_report(report, None, args.json_output, "插件规则校验报告")
}

fn run_validate_event_command_rules_command(
    registry: &GameRegistry,
    args: ValidateEventCommandRulesCommand,
) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            let report = event_command_rules_invalid_report(error);
            return emit_report(report, None, args.json_output, "事件指令规则校验报告");
        }
    };
    let rules_text = match read_placeholder_rules_text(args.rules, args.input) {
        Ok(text) => text,
        Err(error) => {
            let report = event_command_rules_invalid_report(error);
            return emit_report(report, None, args.json_output, "事件指令规则校验报告");
        }
    };
    let command_snapshots = match read_event_command_snapshots(&game_record.game_path) {
        Ok(command_snapshots) => command_snapshots,
        Err(error) => {
            let report = event_command_rules_invalid_report(error.to_string());
            return emit_report(report, None, args.json_output, "事件指令规则校验报告");
        }
    };
    let source_text_required_pattern = match load_source_text_required_pattern(None) {
        Ok(pattern) => pattern,
        Err(error) => {
            let report = event_command_rules_invalid_report(error.to_string());
            return emit_report(report, None, args.json_output, "事件指令规则校验报告");
        }
    };
    let report = validate_event_command_rules_report(
        &command_snapshots,
        &rules_text,
        &source_text_required_pattern,
    );
    emit_report(report, None, args.json_output, "事件指令规则校验报告")
}

fn run_prepare_agent_workspace_command(
    registry: &GameRegistry,
    args: PrepareAgentWorkspaceCommand,
) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            let report = workspace_invalid_report(error);
            return emit_report(report, None, args.json_output, "Agent 工作区准备报告");
        }
    };
    let source_text_required_pattern = match load_source_text_required_pattern(None) {
        Ok(pattern) => pattern,
        Err(error) => {
            let report = workspace_invalid_report(error.to_string());
            return emit_report(report, None, args.json_output, "Agent 工作区准备报告");
        }
    };
    let default_codes = if args.codes.is_empty() {
        match load_event_command_default_codes(None) {
            Ok(codes) => Some(codes),
            Err(error) => {
                let report = workspace_invalid_report(error.to_string());
                return emit_report(report, None, args.json_output, "Agent 工作区准备报告");
            }
        }
    } else {
        None
    };
    let command_codes = (!args.codes.is_empty()).then(|| args.codes.into_iter().collect());
    let report = match prepare_agent_workspace(
        registry,
        &game_record,
        &args.output_dir,
        command_codes,
        default_codes,
        &source_text_required_pattern,
    ) {
        Ok(report) => report,
        Err(error) => workspace_invalid_report(error.to_string()),
    };
    emit_report(report, None, args.json_output, "Agent 工作区准备报告")
}

fn run_validate_agent_workspace_command(
    registry: &GameRegistry,
    args: ValidateAgentWorkspaceCommand,
) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            let report = workspace_invalid_report(error);
            return emit_report(report, None, args.json_output, "Agent 工作区校验报告");
        }
    };
    let source_text_required_pattern = match load_source_text_required_pattern(None) {
        Ok(pattern) => pattern,
        Err(error) => {
            let report = workspace_invalid_report(error.to_string());
            return emit_report(report, None, args.json_output, "Agent 工作区校验报告");
        }
    };
    let report = validate_agent_workspace(
        registry,
        &game_record,
        &args.workspace,
        &source_text_required_pattern,
    );
    emit_report(report, None, args.json_output, "Agent 工作区校验报告")
}

fn run_cleanup_agent_workspace_command(args: CleanupAgentWorkspaceCommand) -> i32 {
    let report = cleanup_agent_workspace(&args.workspace);
    if args.json_output {
        println!("{}", report.to_json_text());
    } else {
        render_report("Agent 工作区清理报告", &report);
    }
    if report.has_errors() { 1 } else { 0 }
}

fn run_quality_report_command(registry: &GameRegistry, args: QualityReportCommand) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            let report = quality_report_failed_report(error);
            return emit_report(
                report,
                args.output.as_ref(),
                args.json_output,
                "翻译质量报告",
            );
        }
    };
    let source_text_required_pattern = match load_source_text_required_pattern(None) {
        Ok(pattern) => pattern,
        Err(error) => {
            let report = quality_report_failed_report(error.to_string());
            return emit_report(
                report,
                args.output.as_ref(),
                args.json_output,
                "翻译质量报告",
            );
        }
    };
    let text_rule_options = match load_text_rule_options(None) {
        Ok(options) => options,
        Err(error) => {
            let report = quality_report_failed_report(error.to_string());
            return emit_report(
                report,
                args.output.as_ref(),
                args.json_output,
                "翻译质量报告",
            );
        }
    };
    let report = match quality_report(
        registry,
        &game_record,
        &source_text_required_pattern,
        &text_rule_options,
    ) {
        Ok(report) => report,
        Err(error) => quality_report_failed_report(error.to_string()),
    };
    emit_report(
        report,
        args.output.as_ref(),
        args.json_output,
        "翻译质量报告",
    )
}

fn run_export_pending_translations_command(
    registry: &GameRegistry,
    args: ExportPendingTranslationsCommand,
) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            let report = manual_translation_export_failed_report(error);
            return emit_report(report, None, args.json_output, "手动填写译文表导出报告");
        }
    };
    run_export_pending_translations_for_record(
        registry,
        &game_record,
        &args.output,
        args.limit,
        args.json_output,
        "手动填写译文表导出报告",
    )
}

fn run_export_untranslated_translations_command(
    registry: &GameRegistry,
    args: ExportUntranslatedTranslationsCommand,
) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            let report = manual_translation_export_failed_report(error);
            return emit_report(report, None, args.json_output, "全部未翻译正文导出报告");
        }
    };
    run_export_pending_translations_for_record(
        registry,
        &game_record,
        &args.output,
        None,
        args.json_output,
        "全部未翻译正文导出报告",
    )
}

fn run_export_pending_translations_for_record(
    registry: &GameRegistry,
    game_record: &GameRecord,
    output: &Path,
    limit: Option<i64>,
    json_output: bool,
    title: &str,
) -> i32 {
    let source_text_required_pattern = match load_source_text_required_pattern(None) {
        Ok(pattern) => pattern,
        Err(error) => {
            let report = manual_translation_export_failed_report(error.to_string());
            return emit_report(report, None, json_output, title);
        }
    };
    let report = match export_pending_translations_report(
        registry,
        game_record,
        output,
        limit,
        &source_text_required_pattern,
    ) {
        Ok(report) => report,
        Err(error) => manual_translation_export_failed_report(error.to_string()),
    };
    emit_report(report, None, json_output, title)
}

fn run_export_quality_fix_template_command(
    registry: &GameRegistry,
    args: ExportQualityFixTemplateCommand,
) -> i32 {
    let output_path = args.output.clone();
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            let report = quality_fix_export_failed_report(&output_path, error);
            return emit_report(report, None, args.json_output, "质量修复模板导出报告");
        }
    };
    let source_text_required_pattern = match load_source_text_required_pattern(None) {
        Ok(pattern) => pattern,
        Err(error) => {
            let report = quality_fix_export_failed_report(&output_path, error.to_string());
            return emit_report(report, None, args.json_output, "质量修复模板导出报告");
        }
    };
    let text_rule_options = match load_text_rule_options(None) {
        Ok(options) => options,
        Err(error) => {
            let report = quality_fix_export_failed_report(&output_path, error.to_string());
            return emit_report(report, None, args.json_output, "质量修复模板导出报告");
        }
    };
    let report = match export_quality_fix_template_report(
        registry,
        &game_record,
        &output_path,
        &source_text_required_pattern,
        &text_rule_options,
    ) {
        Ok(report) => report,
        Err(error) => quality_fix_export_failed_report(&output_path, error.to_string()),
    };
    emit_report(report, None, args.json_output, "质量修复模板导出报告")
}

fn run_import_manual_translations_command(
    registry: &GameRegistry,
    args: ImportManualTranslationsCommand,
) -> i32 {
    let input_path = args.input.clone();
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            let report = manual_translation_import_failed_report(&input_path, error);
            return emit_report(report, None, args.json_output, "手动填写译文导入报告");
        }
    };
    let source_text_required_pattern = match load_source_text_required_pattern(None) {
        Ok(pattern) => pattern,
        Err(error) => {
            let report = manual_translation_import_failed_report(&input_path, error.to_string());
            return emit_report(report, None, args.json_output, "手动填写译文导入报告");
        }
    };
    let text_rule_options = match load_text_rule_options(None) {
        Ok(options) => options,
        Err(error) => {
            let report = manual_translation_import_failed_report(&input_path, error.to_string());
            return emit_report(report, None, args.json_output, "手动填写译文导入报告");
        }
    };
    let report = match import_manual_translations_report(
        registry,
        &game_record,
        &input_path,
        &source_text_required_pattern,
        &text_rule_options,
    ) {
        Ok(report) => report,
        Err(error) => manual_translation_import_failed_report(&input_path, error.to_string()),
    };
    emit_report(report, None, args.json_output, "手动填写译文导入报告")
}

fn run_reset_translations_command(registry: &GameRegistry, args: ResetTranslationsCommand) -> i32 {
    let input_path = args.input.clone();
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            let report = reset_translation_failed_report(input_path.as_deref(), error);
            return emit_report(report, None, args.json_output, "重置译文报告");
        }
    };
    let source_text_required_pattern = match load_source_text_required_pattern(None) {
        Ok(pattern) => pattern,
        Err(error) => {
            let report = reset_translation_failed_report(input_path.as_deref(), error.to_string());
            return emit_report(report, None, args.json_output, "重置译文报告");
        }
    };
    let report = match reset_translations_report(
        registry,
        &game_record,
        input_path.as_deref(),
        args.reset_all,
        &source_text_required_pattern,
    ) {
        Ok(report) => report,
        Err(error) => reset_translation_failed_report(input_path.as_deref(), error.to_string()),
    };
    emit_report(report, None, args.json_output, "重置译文报告")
}

fn run_validate_japanese_residual_rules_command(
    registry: &GameRegistry,
    args: ValidateJapaneseResidualRulesCommand,
) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            let report = japanese_residual_rules_invalid_report(error, true);
            return emit_report(report, None, args.json_output, "日文残留例外规则校验报告");
        }
    };
    let rules_text = match read_rules_text(args.rules, args.input) {
        Ok(text) => text,
        Err(error) => {
            let report = japanese_residual_rules_invalid_report(error, true);
            return emit_report(report, None, args.json_output, "日文残留例外规则校验报告");
        }
    };
    let source_text_required_pattern = match load_source_text_required_pattern(None) {
        Ok(pattern) => pattern,
        Err(error) => {
            let report = japanese_residual_rules_invalid_report(error.to_string(), true);
            return emit_report(report, None, args.json_output, "日文残留例外规则校验报告");
        }
    };
    let active_items = match load_active_translation_items(
        registry,
        &game_record,
        &source_text_required_pattern,
    ) {
        Ok(items) => items,
        Err(error) => {
            let report = japanese_residual_rules_invalid_report(error.to_string(), true);
            return emit_report(report, None, args.json_output, "日文残留例外规则校验报告");
        }
    };
    let translated_items = match registry.read_translated_items(&game_record.game_title) {
        Ok(items) => items,
        Err(error) => {
            let report = japanese_residual_rules_invalid_report(error.to_string(), true);
            return emit_report(report, None, args.json_output, "日文残留例外规则校验报告");
        }
    };
    let report =
        validate_japanese_residual_rules_report(&active_items, &translated_items, &rules_text);
    emit_report(report, None, args.json_output, "日文残留例外规则校验报告")
}

fn run_import_japanese_residual_rules_command(
    registry: &GameRegistry,
    args: ImportJapaneseResidualRulesCommand,
) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            let report = japanese_residual_rules_invalid_report(error, false);
            return emit_report(report, None, args.json_output, "日文残留例外规则导入报告");
        }
    };
    let rules_text = match read_rules_text(args.rules, args.input) {
        Ok(text) => text,
        Err(error) => {
            let report = japanese_residual_rules_invalid_report(error, false);
            return emit_report(report, None, args.json_output, "日文残留例外规则导入报告");
        }
    };
    let source_text_required_pattern = match load_source_text_required_pattern(None) {
        Ok(pattern) => pattern,
        Err(error) => {
            let report = japanese_residual_rules_invalid_report(error.to_string(), false);
            return emit_report(report, None, args.json_output, "日文残留例外规则导入报告");
        }
    };
    let active_items = match load_active_translation_items(
        registry,
        &game_record,
        &source_text_required_pattern,
    ) {
        Ok(items) => items,
        Err(error) => {
            let report = japanese_residual_rules_invalid_report(error.to_string(), false);
            return emit_report(report, None, args.json_output, "日文残留例外规则导入报告");
        }
    };
    let translated_items = match registry.read_translated_items(&game_record.game_title) {
        Ok(items) => items,
        Err(error) => {
            let report = japanese_residual_rules_invalid_report(error.to_string(), false);
            return emit_report(report, None, args.json_output, "日文残留例外规则导入报告");
        }
    };
    let records = match build_japanese_residual_rule_records_from_text(
        &active_items,
        &translated_items,
        &rules_text,
    ) {
        Ok(records) => records,
        Err(error) => {
            let report = japanese_residual_rules_invalid_report(error.to_string(), false);
            return emit_report(report, None, args.json_output, "日文残留例外规则导入报告");
        }
    };
    let report = match registry.replace_japanese_residual_rules(&game_record.game_title, &records) {
        Ok(()) => japanese_residual_rules_import_report(&records),
        Err(error) => japanese_residual_rules_invalid_report(error.to_string(), false),
    };
    emit_report(report, None, args.json_output, "日文残留例外规则导入报告")
}

fn run_export_terminology_command(registry: &GameRegistry, args: ExportTerminologyCommand) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            error!("命令执行失败：{}", error);
            return 1;
        }
    };
    let report = match export_terminology_report(&game_record, &args.output_dir) {
        Ok(report) => report,
        Err(error) => {
            error!("命令执行失败：{}", error);
            return 1;
        }
    };
    render_report("术语表导出报告", &report);
    0
}

fn run_import_terminology_command(registry: &GameRegistry, args: ImportTerminologyCommand) -> i32 {
    let input_path = args.input.clone();
    let glossary_input_path = args.glossary_input.clone();
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            let report = terminology_invalid_report("", &input_path, &glossary_input_path, error);
            return emit_report(report, None, args.json_output, "术语表导入报告");
        }
    };
    let report = match import_terminology_report(
        registry,
        &game_record,
        &input_path,
        &glossary_input_path,
    ) {
        Ok(report) => report,
        Err(error) => terminology_invalid_report(
            &game_record.game_title,
            &input_path,
            &glossary_input_path,
            error.to_string(),
        ),
    };
    emit_report(report, None, args.json_output, "术语表导入报告")
}

fn run_translate_command(registry: &GameRegistry, args: TranslateCommand) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            if args.json_output {
                print_json_error("translate_failed", format!("正文翻译失败: {error}"), "");
            } else {
                error!("命令执行失败：{}", error);
            }
            return 1;
        }
    };
    let settings = match build_runtime_settings(&args.setting_overrides) {
        Ok(settings) => settings,
        Err(error) => {
            if args.json_output {
                print_json_error("translate_failed", format!("正文翻译失败: {error}"), "");
            } else {
                error!("命令执行失败：{}", error);
            }
            return 1;
        }
    };
    let limits = match args.run_limits.to_core_limits() {
        Ok(limits) => limits,
        Err(error) => {
            if args.json_output {
                print_json_error("translate_failed", format!("正文翻译失败: {error}"), "");
            } else {
                error!("命令执行失败：{}", error);
            }
            return 1;
        }
    };
    let report = match translate_report(
        registry,
        &game_record,
        &settings,
        args.placeholder_rules.as_deref(),
        &limits,
    ) {
        Ok(report) => report,
        Err(error) => {
            if args.json_output {
                print_json_error("translate_failed", format!("正文翻译失败: {error}"), "");
            } else {
                error!("命令执行失败：{}", error);
            }
            return 1;
        }
    };
    emit_report(report, None, args.json_output, "正文翻译摘要")
}

fn run_write_back_command(registry: &GameRegistry, args: WriteBackCommand) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            if args.json_output {
                print_json_error(
                    "write_back_failed",
                    format!("写进游戏文件失败: {error}"),
                    "",
                );
            } else {
                error!("命令执行失败：{}", error);
            }
            return 1;
        }
    };
    let settings = match build_runtime_settings(&args.setting_overrides) {
        Ok(settings) => settings,
        Err(error) => {
            if args.json_output {
                print_json_error(
                    "write_back_failed",
                    format!("写进游戏文件失败: {error}"),
                    "",
                );
            } else {
                error!("命令执行失败：{}", error);
            }
            return 1;
        }
    };
    execute_write_back(
        registry,
        &game_record,
        &settings,
        args.confirm_font_overwrite,
        args.json_output,
    )
}

fn execute_write_back(
    registry: &GameRegistry,
    game_record: &GameRecord,
    settings: &RuntimeSettings,
    confirm_font_overwrite: bool,
    json_output: bool,
) -> i32 {
    let gate_report = match quality_report(
        registry,
        game_record,
        &settings.source_text_required_pattern,
        &settings.text_rules,
    ) {
        Ok(report) => report,
        Err(error) => {
            if json_output {
                print_json_error(
                    "write_back_failed",
                    format!("写进游戏文件失败: {error}"),
                    "",
                );
            } else {
                error!("命令执行失败：{}", error);
            }
            return 1;
        }
    };
    if gate_report.has_errors() {
        return emit_report(gate_report, None, json_output, "写入前检查");
    }
    let report = match write_back_report(
        registry,
        game_record,
        &settings.source_text_required_pattern,
        confirm_font_overwrite,
        settings.replacement_font_path.as_deref(),
    ) {
        Ok(report) => report,
        Err(error) => {
            if json_output {
                print_json_error(
                    "write_back_failed",
                    format!("写进游戏文件失败: {error}"),
                    "",
                );
            } else {
                error!("命令执行失败：{}", error);
            }
            return 1;
        }
    };
    emit_report(report, None, json_output, "游戏文件回写报告")
}

fn run_all_command(registry: &GameRegistry, args: RunAllCommand) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            error!("命令执行失败：{}", error);
            return 1;
        }
    };
    let settings = match build_runtime_settings(&args.setting_overrides) {
        Ok(settings) => settings,
        Err(error) => {
            error!("命令执行失败：{}", error);
            return 1;
        }
    };
    let limits = match args.run_limits.to_core_limits() {
        Ok(limits) => limits,
        Err(error) => {
            error!("命令执行失败：{}", error);
            return 1;
        }
    };
    let translation_report = match translate_report(
        registry,
        &game_record,
        &settings,
        args.placeholder_rules.as_deref(),
        &limits,
    ) {
        Ok(report) => report,
        Err(error) => {
            error!("命令执行失败：{}", error);
            return 1;
        }
    };
    if translation_report.has_errors() {
        render_report("正文翻译摘要", &translation_report);
        return 1;
    }
    let quality_error_count = translation_report
        .summary
        .get("quality_error_count")
        .and_then(serde_json::Value::as_u64)
        .map_or(0, |value| value);
    if quality_error_count > 0 {
        render_report("正文翻译摘要", &translation_report);
        error!(
            "正文翻译产生错误条目，已停止后续流程：失败 {} 条",
            quality_error_count
        );
        return 1;
    }
    if args.skip_write_back {
        info!("已按参数跳过把译文写进游戏文件");
        return 0;
    }
    execute_write_back(
        registry,
        &game_record,
        &settings,
        args.confirm_font_overwrite,
        false,
    )
}

fn run_restore_font_command(registry: &GameRegistry, args: RestoreFontCommand) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            if args.json_output {
                print_json_error("font_restore_failed", format!("字体还原失败: {error}"), "");
            } else {
                error!("命令执行失败：{}", error);
            }
            return 1;
        }
    };
    let replacement_font_path = match load_write_back_replacement_font_path(
        None,
        args.setting_overrides.replacement_font_path.as_deref(),
    ) {
        Ok(path) => path,
        Err(error) => {
            if args.json_output {
                print_json_error("font_restore_failed", format!("字体还原失败: {error}"), "");
            } else {
                error!("命令执行失败：{}", error);
            }
            return 1;
        }
    };
    let report = match restore_font_report(registry, &game_record, replacement_font_path.as_deref())
    {
        Ok(report) => report,
        Err(error) => {
            if args.json_output {
                print_json_error("font_restore_failed", format!("字体还原失败: {error}"), "");
            } else {
                error!("命令执行失败：{}", error);
            }
            return 1;
        }
    };
    emit_report(report, None, args.json_output, "字体还原报告")
}

fn run_write_terminology_command(registry: &GameRegistry, args: WriteTerminologyCommand) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            error!("命令执行失败：{}", error);
            return 1;
        }
    };
    let settings = match build_runtime_settings(&args.setting_overrides) {
        Ok(settings) => settings,
        Err(error) => {
            error!("命令执行失败：{}", error);
            return 1;
        }
    };
    let setting_override_count = args.setting_overrides.provided_count();
    if setting_override_count > 0 {
        info!(
            "已接收配置覆盖 {} 项；本次术语字段写回会使用其中的字体覆盖配置",
            setting_override_count
        );
    }
    let mut report = match write_terminology_report(registry, &game_record) {
        Ok(report) => report,
        Err(error) => {
            error!("命令执行失败：{}", error);
            return 1;
        }
    };
    if args.confirm_font_overwrite {
        let font_summary = match apply_font_replacement_to_active_game(
            registry,
            &game_record,
            settings.replacement_font_path.as_deref(),
        ) {
            Ok(summary) => summary,
            Err(error) => {
                error!("命令执行失败：{}", error);
                return 1;
            }
        };
        report.summary.insert(
            "target_font_name".to_string(),
            json!(font_summary.target_font_name.unwrap_or_default()),
        );
        report.summary.insert(
            "source_font_count".to_string(),
            json!(font_summary.source_font_count),
        );
        report.summary.insert(
            "replaced_font_reference_count".to_string(),
            json!(font_summary.replaced_reference_count),
        );
        report
            .summary
            .insert("font_copied".to_string(), json!(font_summary.copied));
    } else if settings.replacement_font_path.is_some() {
        info!("未确认覆盖字体，已跳过字体替换");
    }
    render_report("术语写回报告", &report);
    0
}

fn run_translation_status_command(registry: &GameRegistry, args: TranslationStatusCommand) -> i32 {
    let game_record = match resolve_target_game_record(registry, args.target) {
        Ok(record) => record,
        Err(error) => {
            let report = translation_status_failed_report(error);
            return emit_report(report, None, args.json_output, "正文翻译状态");
        }
    };
    let source_text_required_pattern = match load_source_text_required_pattern(None) {
        Ok(pattern) => pattern,
        Err(error) => {
            let report = translation_status_failed_report(error.to_string());
            return emit_report(report, None, args.json_output, "正文翻译状态");
        }
    };
    let report =
        match translation_status_report(registry, &game_record, &source_text_required_pattern) {
            Ok(report) => report,
            Err(error) => translation_status_failed_report(error.to_string()),
        };
    emit_report(report, None, args.json_output, "正文翻译状态")
}

fn read_placeholder_rules_text(
    rules: Option<String>,
    input: Option<PathBuf>,
) -> Result<String, String> {
    read_rules_text(rules, input)
}

fn read_rules_text(rules: Option<String>, input: Option<PathBuf>) -> Result<String, String> {
    if let Some(rules) = rules {
        return Ok(rules);
    }
    if let Some(input) = input {
        return fs::read_to_string(&input)
            .map_err(|error| format!("读取输入文件失败：{}: {error}", input.display()));
    }
    Err("命令参数必须提供 rules 或 input".to_string())
}

fn read_optional_rules_text(
    rules: Option<String>,
    input: Option<PathBuf>,
) -> Result<Option<String>, String> {
    if let Some(rules) = rules {
        return Ok(Some(rules));
    }
    if let Some(input) = input {
        let text = fs::read_to_string(&input)
            .map_err(|error| format!("读取输入文件失败：{}: {error}", input.display()))?;
        return Ok(Some(text));
    }
    Ok(None)
}

fn resolve_scan_placeholder_rules(
    registry: &GameRegistry,
    game_title: &str,
    rules: Option<String>,
    input: Option<PathBuf>,
) -> Result<Vec<PlaceholderRule>, String> {
    match read_optional_rules_text(rules, input)? {
        Some(text) => parse_custom_placeholder_rules_text(&text).map_err(|error| error.to_string()),
        None => registry
            .read_placeholder_rules(game_title)
            .map_err(|error| error.to_string()),
    }
}

fn load_active_text_items(
    registry: &GameRegistry,
    game_record: &GameRecord,
) -> Result<Vec<ActiveTextItem>, String> {
    let data_files =
        read_data_json_files(&game_record.game_path).map_err(|error| error.to_string())?;
    let command_snapshots =
        read_event_command_snapshots(&game_record.game_path).map_err(|error| error.to_string())?;
    let plugins = read_plugins_json(&game_record.game_path).map_err(|error| error.to_string())?;
    let plugin_rules = registry
        .read_plugin_text_rules(&game_record.game_title)
        .map_err(|error| error.to_string())?;
    let event_rules = registry
        .read_event_command_text_rules(&game_record.game_title)
        .map_err(|error| error.to_string())?;
    let note_rules = registry
        .read_note_tag_text_rules(&game_record.game_title)
        .map_err(|error| error.to_string())?;
    let source_text_required_pattern =
        load_source_text_required_pattern(None).map_err(|error| error.to_string())?;
    extract_active_text_items(
        &data_files,
        &command_snapshots,
        &plugins,
        &plugin_rules,
        &event_rules,
        &note_rules,
        &source_text_required_pattern,
    )
    .map_err(|error| error.to_string())
}

fn placeholder_rules_invalid_report(
    source_label: &str,
    sample_texts: &[String],
    message: String,
) -> AgentReport {
    let mut summary = Map::new();
    summary.insert("source".to_string(), json!(source_label));
    summary.insert("rule_count".to_string(), json!(0));
    summary.insert("sample_count".to_string(), json!(sample_texts.len()));
    AgentReport::from_parts(
        vec![issue(
            "placeholder_rules_invalid",
            format!("自定义占位符规则不可用: {message}"),
        )],
        Vec::new(),
        summary,
        Map::new(),
    )
}

fn placeholder_scan_failed_report(message: String) -> AgentReport {
    let mut summary = Map::new();
    summary.insert("candidate_count".to_string(), json!(0));
    summary.insert("uncovered_count".to_string(), json!(0));
    summary.insert("custom_rule_count".to_string(), json!(0));
    let mut details = Map::new();
    details.insert("candidates".to_string(), json!([]));
    AgentReport::from_parts(
        vec![issue(
            "placeholder_scan_failed",
            format!("自定义控制符候选扫描失败: {message}"),
        )],
        Vec::new(),
        summary,
        details,
    )
}

fn plugin_rules_invalid_report(message: String) -> AgentReport {
    AgentReport::from_parts(
        vec![issue(
            "plugin_rules_invalid",
            format!("插件规则不可导入: {message}"),
        )],
        Vec::new(),
        plugin_empty_summary(),
        plugin_empty_details(),
    )
}

fn plugin_empty_summary() -> Map<String, serde_json::Value> {
    let mut summary = Map::new();
    summary.insert("plugin_count".to_string(), json!(0));
    summary.insert("rule_count".to_string(), json!(0));
    summary
}

fn plugin_empty_details() -> Map<String, serde_json::Value> {
    let mut details = Map::new();
    details.insert("rules".to_string(), json!([]));
    details
}

fn event_command_rules_invalid_report(message: String) -> AgentReport {
    let mut summary = Map::new();
    summary.insert("rule_group_count".to_string(), json!(0));
    summary.insert("path_rule_count".to_string(), json!(0));
    let mut details = Map::new();
    details.insert("rules".to_string(), json!([]));
    AgentReport::from_parts(
        vec![issue(
            "event_command_rules_invalid",
            format!("事件指令规则不可导入: {message}"),
        )],
        Vec::new(),
        summary,
        details,
    )
}

fn note_tag_rules_invalid_report(message: String) -> AgentReport {
    let mut summary = Map::new();
    summary.insert("file_count".to_string(), json!(0));
    summary.insert("tag_count".to_string(), json!(0));
    summary.insert("hit_count".to_string(), json!(0));
    let mut details = Map::new();
    details.insert("rules".to_string(), json!([]));
    AgentReport::from_parts(
        vec![issue(
            "note_tag_rules_invalid",
            format!("Note 标签规则不可导入: {message}"),
        )],
        Vec::new(),
        summary,
        details,
    )
}

fn workspace_invalid_report(message: String) -> AgentReport {
    AgentReport::from_parts(
        vec![issue(
            "workspace_failed",
            format!("Agent 工作区处理失败: {message}"),
        )],
        Vec::new(),
        Map::new(),
        Map::new(),
    )
}

fn manual_translation_export_failed_report(message: String) -> AgentReport {
    let mut summary = Map::new();
    summary.insert("pending_exported_count".to_string(), json!(0));
    AgentReport::from_parts(
        vec![issue(
            "manual_translation_export_failed",
            format!("手动填写译文表导出失败: {message}"),
        )],
        Vec::new(),
        summary,
        Map::new(),
    )
}

fn quality_report_failed_report(message: String) -> AgentReport {
    AgentReport::from_parts(
        vec![issue(
            "quality_report_failed",
            format!("翻译质量报告生成失败: {message}"),
        )],
        Vec::new(),
        Map::new(),
        Map::new(),
    )
}

fn quality_fix_export_failed_report(output_path: &Path, message: String) -> AgentReport {
    let mut summary = Map::new();
    summary.insert("exported_count".to_string(), json!(0));
    summary.insert(
        "output".to_string(),
        json!(output_path.display().to_string()),
    );
    AgentReport::from_parts(
        vec![issue(
            "quality_fix_export_failed",
            format!("质量修复模板导出失败: {message}"),
        )],
        Vec::new(),
        summary,
        Map::new(),
    )
}

fn manual_translation_import_failed_report(input_path: &Path, message: String) -> AgentReport {
    let mut summary = Map::new();
    summary.insert("input".to_string(), json!(input_path.display().to_string()));
    summary.insert("imported_count".to_string(), json!(0));
    AgentReport::from_parts(
        vec![issue(
            "manual_translation_import_failed",
            format!("手动填写译文导入失败: {message}"),
        )],
        Vec::new(),
        summary,
        Map::new(),
    )
}

fn reset_translation_failed_report(input_path: Option<&Path>, message: String) -> AgentReport {
    let mut summary = Map::new();
    summary.insert(
        "input".to_string(),
        json!(
            input_path
                .map(|path| path.display().to_string())
                .unwrap_or_default()
        ),
    );
    summary.insert("mode".to_string(), json!("invalid"));
    summary.insert("requested_count".to_string(), json!(0));
    summary.insert("reset_count".to_string(), json!(0));
    AgentReport::from_parts(
        vec![issue(
            "reset_translation_failed",
            format!("重置译文失败: {message}"),
        )],
        Vec::new(),
        summary,
        Map::new(),
    )
}

fn translation_status_failed_report(message: String) -> AgentReport {
    AgentReport::from_parts(
        vec![issue(
            "translation_status_failed",
            format!("正文翻译状态读取失败: {message}"),
        )],
        Vec::new(),
        Map::new(),
        Map::new(),
    )
}

fn emit_report(
    report: AgentReport,
    output_path: Option<&PathBuf>,
    json_output: bool,
    title: &str,
) -> i32 {
    if let Some(output_path) = output_path {
        if let Some(parent) = output_path.parent()
            && let Err(error) = fs::create_dir_all(parent)
        {
            error!("写出 JSON 报告失败：{}", error);
            return 1;
        }
        if let Err(error) = fs::write(output_path, format!("{}\n", report.to_json_text())) {
            error!("写出 JSON 报告失败：{}", error);
            return 1;
        }
    }
    if json_output {
        println!("{}", report.to_json_text());
    } else {
        render_report(title, &report);
    }
    if report.has_errors() { 1 } else { 0 }
}

fn report_placeholder_import_error(json_output: bool, game_title: &str, message: String) -> i32 {
    if json_output {
        let mut summary = Map::new();
        if !game_title.is_empty() {
            summary.insert("game".to_string(), json!(game_title));
        }
        println!(
            "{}",
            AgentReport::from_parts(
                vec![issue(
                    "placeholder_rules_invalid",
                    format!("自定义占位符规则导入失败: {message}")
                )],
                Vec::new(),
                summary,
                Map::new(),
            )
            .to_json_text()
        );
    } else {
        error!("命令执行失败：{}", message);
    }
    1
}

fn report_plugin_import_error(
    json_output: bool,
    game_title: &str,
    input_path: &PathBuf,
    message: String,
) -> i32 {
    if json_output {
        let mut summary = Map::new();
        if !game_title.is_empty() {
            summary.insert("game".to_string(), json!(game_title));
        }
        summary.insert("input".to_string(), json!(input_path));
        println!(
            "{}",
            AgentReport::from_parts(
                vec![issue(
                    "plugin_rules_invalid",
                    format!("插件规则导入失败: {message}")
                )],
                Vec::new(),
                summary,
                Map::new(),
            )
            .to_json_text()
        );
    } else {
        error!("命令执行失败：{}", message);
    }
    1
}

fn report_event_command_import_error(
    json_output: bool,
    game_title: &str,
    input_path: &PathBuf,
    message: String,
) -> i32 {
    if json_output {
        let mut summary = Map::new();
        if !game_title.is_empty() {
            summary.insert("game".to_string(), json!(game_title));
        }
        summary.insert("input".to_string(), json!(input_path));
        println!(
            "{}",
            AgentReport::from_parts(
                vec![issue(
                    "event_command_rules_invalid",
                    format!("事件指令规则导入失败: {message}")
                )],
                Vec::new(),
                summary,
                Map::new(),
            )
            .to_json_text()
        );
    } else {
        error!("命令执行失败：{}", message);
    }
    1
}

fn report_note_tag_import_error(json_output: bool, message: String) -> i32 {
    if json_output {
        let mut summary = Map::new();
        summary.insert("file_count".to_string(), json!(0));
        summary.insert("tag_count".to_string(), json!(0));
        summary.insert("deleted_translation_items".to_string(), json!(0));
        println!(
            "{}",
            AgentReport::from_parts(
                vec![issue(
                    "note_tag_rules_invalid",
                    format!("Note 标签规则不可导入: {message}")
                )],
                Vec::new(),
                summary,
                Map::new(),
            )
            .to_json_text()
        );
    } else {
        error!("命令执行失败：{}", message);
    }
    1
}

fn resolve_optional_target_game_title(
    registry: &GameRegistry,
    game: Option<String>,
    game_path: Option<PathBuf>,
) -> Result<Option<String>, String> {
    if let Some(game) = game {
        return Ok(Some(game));
    }
    if let Some(game_path) = game_path {
        return registry
            .resolve_registered_title_by_path(game_path)
            .map(Some)
            .map_err(|error| error.to_string());
    }
    Ok(None)
}

fn resolve_target_game_record(
    registry: &GameRegistry,
    target: TargetGameArgs,
) -> Result<att_mz_core::GameRecord, String> {
    if let Some(game) = target.game {
        return registry
            .open_game_record(&game)
            .map_err(|error| error.to_string());
    }
    if let Some(game_path) = target.game_path {
        let game_title = registry
            .resolve_registered_title_by_path(game_path)
            .map_err(|error| error.to_string())?;
        return registry
            .open_game_record(&game_title)
            .map_err(|error| error.to_string());
    }
    Err("命令必须提供 --game 或 --game-path".to_string())
}

fn resolve_optional_game_title(
    registry: &GameRegistry,
    target: OptionalTargetGameArgs,
) -> Result<Option<String>, String> {
    if let Some(game) = target.game {
        return Ok(Some(game));
    }
    if let Some(game_path) = target.game_path {
        return registry
            .resolve_registered_title_by_path(game_path)
            .map(Some)
            .map_err(|error| error.to_string());
    }
    Ok(None)
}

fn print_json_error(code: &str, message: impl Into<String>, detail: impl Into<String>) {
    let detail = detail.into();
    let mut details = Map::new();
    if !detail.is_empty() {
        details.insert("detail".to_string(), json!(detail));
    }
    let report = AgentReport::from_parts(
        vec![issue(code, message.into())],
        Vec::new(),
        Map::new(),
        details,
    );
    println!("{}", report.to_json_text());
}

fn render_report(title: &str, report: &AgentReport) {
    println!("{title}");
    println!("状态: {}", report.status);
    if !report.summary.is_empty() {
        println!("摘要:");
        for (key, value) in &report.summary {
            println!("- {key}: {value}");
        }
    }
    if !report.errors.is_empty() {
        println!("必须先处理的错误:");
        for item in &report.errors {
            println!("- {}: {}", item.code, item.message);
        }
    }
    if !report.warnings.is_empty() {
        println!("告警:");
        for item in &report.warnings {
            println!("- {}: {}", item.code, item.message);
        }
    }
}

fn setup_logging(debug: bool, agent_mode: bool) {
    let level = if debug { "debug" } else { "info" };
    let builder = tracing_subscriber::fmt()
        .with_env_filter(level)
        .with_target(false)
        .with_writer(std::io::stderr)
        .with_ansi(!agent_mode);
    let _ = builder.try_init();
}
