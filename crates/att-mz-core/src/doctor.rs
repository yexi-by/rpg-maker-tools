//! 环境诊断命令。
//!
//! 该模块输出和现有 Agent 报告兼容的 JSON 结构，先覆盖配置、固定目录、
//! 终端编码和已注册游戏的轻量检查。

use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::{Map, Value, json};

use crate::config::{load_runtime_settings, load_setting_summary, setting_summary_json};
use crate::db::GameRegistry;
use crate::error::Result;
use crate::game::validate_game_directory;
use crate::plugin_rules::{PluginRuleRecord, build_plugin_hash};
use crate::report::{AgentIssue, AgentReport, issue};
use crate::rmmz::read_plugins_json;

/// `doctor` 命令输入参数。
#[derive(Debug, Clone)]
pub struct DoctorOptions {
    /// 可选目标游戏标题。
    pub game_title: Option<String>,
    /// 是否检查模型连接。
    pub check_llm: bool,
    /// 可选配置路径。
    pub setting_path: Option<PathBuf>,
}

/// 执行环境诊断。
pub fn run_doctor(options: &DoctorOptions, registry: &GameRegistry) -> AgentReport {
    let mut errors: Vec<AgentIssue> = Vec::new();
    let mut warnings: Vec<AgentIssue> = Vec::new();
    let mut summary = Map::new();
    let mut details = Map::new();
    details.insert("environment_overrides".to_string(), json!([]));
    details.insert("checks".to_string(), json!([]));

    summary.insert("runtime".to_string(), json!("rust"));
    summary.insert(
        "platform".to_string(),
        json!(format!("{}-{}", env::consts::OS, env::consts::ARCH)),
    );

    let setting_result = load_setting_summary(options.setting_path.as_deref());
    match setting_result {
        Ok(setting) => {
            append_check(&mut details, "setting", "ok");
            for (key, value) in setting_summary_json(&setting) {
                summary.insert(key, value);
            }
            details.insert(
                "environment_overrides".to_string(),
                json!(setting.environment_overrides.enabled_names()),
            );
            if setting.llm_base_url.trim().is_empty() {
                errors.push(issue("llm_base_url", "模型服务地址为空"));
            }
            if setting.llm_api_key.trim().is_empty() {
                errors.push(issue("llm_api_key", "模型 API Key 为空"));
            }
            if options.check_llm {
                check_llm_connection(options.setting_path.as_deref(), &mut errors, &mut details);
            } else {
                warnings.push(issue("llm_skipped", "已跳过模型连通性检查"));
            }
        }
        Err(error) => {
            errors.push(issue("setting", error.to_string()));
        }
    }

    check_static_paths(registry, &mut errors, &mut warnings, &mut details);

    if let Some(game_title) = &options.game_title {
        check_game(
            registry,
            game_title,
            &mut errors,
            &mut warnings,
            &mut summary,
        );
    }

    AgentReport::from_parts(errors, warnings, summary, details)
}

/// 把检查项追加到报告明细。
pub fn append_check(details: &mut Map<String, Value>, name: &str, status: &str) {
    let entry = details
        .entry("checks".to_string())
        .or_insert_with(|| Value::Array(Vec::new()));
    if !entry.is_array() {
        *entry = Value::Array(Vec::new());
    }
    if let Value::Array(checks) = entry {
        checks.push(json!({"name": name, "status": status}));
    }
}

fn check_static_paths(
    registry: &GameRegistry,
    errors: &mut Vec<AgentIssue>,
    warnings: &mut Vec<AgentIssue>,
    details: &mut Map<String, Value>,
) {
    let db_dir_already_exists = registry.db_directory.exists();
    match registry.ensure_db_directory() {
        Ok(()) => append_check(
            details,
            "db_dir",
            if db_dir_already_exists {
                "ok"
            } else {
                "created"
            },
        ),
        Err(error) => errors.push(issue("db_dir", error.to_string())),
    }

    if let Err(error) = fs::create_dir_all("logs") {
        warnings.push(issue("logs", format!("日志目录创建失败: {error}")));
    }

    details.insert("stdout_encoding".to_string(), json!("utf-8"));
    append_check(details, "stdout_encoding", "ok");
}

fn check_game(
    registry: &GameRegistry,
    game_title: &str,
    errors: &mut Vec<AgentIssue>,
    warnings: &mut Vec<AgentIssue>,
    summary: &mut Map<String, Value>,
) {
    match registry.open_game_record(game_title) {
        Ok(record) => {
            summary.insert("game_registered".to_string(), json!(true));
            summary.insert("game_path".to_string(), json!(record.game_path));
            summary.insert("db_path".to_string(), json!(record.db_path));
            if let Err(error) = validate_game_directory(&record.game_path) {
                errors.push(issue("game", error.to_string()));
            }
            if let Err(error) =
                check_game_rules(registry, game_title, &record.game_path, warnings, summary)
            {
                errors.push(issue("game", format!("目标游戏深度检查失败: {error}")));
            }
        }
        Err(error) => errors.push(issue("game", format!("目标游戏检查失败: {error}"))),
    }
}

fn check_llm_connection(
    setting_path: Option<&Path>,
    errors: &mut Vec<AgentIssue>,
    details: &mut Map<String, Value>,
) {
    let settings = match load_runtime_settings(setting_path) {
        Ok(settings) => settings,
        Err(error) => {
            errors.push(issue("llm", format!("模型连通性检查失败: {error}")));
            return;
        }
    };
    if settings.llm.base_url.trim().is_empty() || settings.llm.api_key.trim().is_empty() {
        return;
    }
    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(settings.llm.timeout_seconds))
        .build()
    {
        Ok(client) => client,
        Err(error) => {
            errors.push(issue(
                "llm",
                format!("模型连通性检查失败: HTTP 客户端创建失败: {error}"),
            ));
            return;
        }
    };
    let endpoint = format!(
        "{}/chat/completions",
        settings.llm.base_url.trim_end_matches('/')
    );
    let mut body = Map::new();
    body.insert("model".to_string(), json!(settings.llm.model));
    body.insert(
        "messages".to_string(),
        json!([{"role": "user", "content": "ping"}]),
    );
    body.insert("max_tokens".to_string(), json!(1));
    for (key, value) in settings.llm.request_body_extra {
        body.insert(key, value);
    }
    let response = match client
        .post(endpoint)
        .bearer_auth(settings.llm.api_key)
        .json(&Value::Object(body))
        .send()
    {
        Ok(response) => response,
        Err(error) => {
            errors.push(issue(
                "llm",
                format!("模型连通性检查失败: 请求模型服务失败: {error}"),
            ));
            return;
        }
    };
    if response.status().is_success() {
        append_check(details, "llm", "ok");
        return;
    }
    let status = response.status();
    let body_text = response
        .text()
        .unwrap_or_else(|error| format!("读取模型服务错误响应失败: {error}"));
    errors.push(issue(
        "llm",
        format!("模型连通性检查失败: HTTP {status}: {body_text}"),
    ));
}

fn check_game_rules(
    registry: &GameRegistry,
    game_title: &str,
    game_path: &Path,
    warnings: &mut Vec<AgentIssue>,
    summary: &mut Map<String, Value>,
) -> Result<()> {
    let plugins = read_plugins_json(game_path)?;
    let plugin_rules = registry.read_plugin_text_rules(game_title)?;
    let (plugin_rule_count, stale_plugin_rule_count) =
        fresh_plugin_rule_counts(plugin_rules, &plugins)?;
    let event_rules = registry.read_event_command_text_rules(game_title)?;
    let note_tag_rules = registry.read_note_tag_text_rules(game_title)?;
    let placeholder_rules = registry.read_placeholder_rules(game_title)?;
    let terminology_registry = registry.read_terminology_registry(game_title)?;
    let terminology_glossary = registry.read_terminology_glossary(game_title)?;

    let event_command_rule_count = event_rules
        .iter()
        .map(|rule| rule.path_templates.len())
        .sum::<usize>();
    let note_tag_rule_count = note_tag_rules
        .iter()
        .map(|rule| rule.tag_names.len())
        .sum::<usize>();
    summary.insert("plugin_rule_count".to_string(), json!(plugin_rule_count));
    summary.insert(
        "stale_plugin_rule_count".to_string(),
        json!(stale_plugin_rule_count),
    );
    summary.insert(
        "event_command_rule_count".to_string(),
        json!(event_command_rule_count),
    );
    summary.insert(
        "note_tag_rule_count".to_string(),
        json!(note_tag_rule_count),
    );
    summary.insert(
        "placeholder_rule_count".to_string(),
        json!(placeholder_rules.len()),
    );
    summary.insert(
        "terminology_imported".to_string(),
        json!(terminology_registry.is_some()),
    );
    summary.insert(
        "glossary_imported".to_string(),
        json!(terminology_glossary.is_some()),
    );

    if plugin_rule_count == 0 && stale_plugin_rule_count == 0 {
        warnings.push(issue("plugin_rules", "当前游戏尚未导入插件文本规则"));
    }
    if stale_plugin_rule_count > 0 {
        warnings.push(issue(
            "stale_plugin_rules",
            format!("发现 {stale_plugin_rule_count} 个过期插件规则，请重新导出并导入插件规则"),
        ));
    }
    if event_command_rule_count == 0 {
        warnings.push(issue(
            "event_command_rules",
            "当前游戏尚未导入事件指令文本规则",
        ));
    }
    if note_tag_rule_count == 0 {
        warnings.push(issue(
            "note_tag_rules",
            "当前游戏尚未导入 Note 标签文本规则",
        ));
    }
    if terminology_registry.is_none() {
        warnings.push(issue("terminology", "当前游戏尚未导入字段译名表"));
    }
    if terminology_glossary.is_none() {
        warnings.push(issue("glossary", "当前游戏尚未导入正文术语表"));
    }
    if placeholder_rules.is_empty() {
        warnings.push(issue(
            "placeholder_rules",
            "当前游戏尚未导入自定义占位符规则",
        ));
    }
    Ok(())
}

fn fresh_plugin_rule_counts(
    rules: Vec<PluginRuleRecord>,
    plugins: &[Value],
) -> Result<(usize, usize)> {
    let mut fresh_rule_count = 0usize;
    let mut stale_rule_count = 0usize;
    for rule in rules {
        let Some(plugin) = plugins.get(rule.plugin_index) else {
            stale_rule_count += 1;
            continue;
        };
        if rule.plugin_hash != build_plugin_hash(plugin)? {
            stale_rule_count += 1;
            continue;
        }
        fresh_rule_count += rule.path_templates.len();
    }
    Ok((fresh_rule_count, stale_rule_count))
}

#[allow(dead_code)]
fn path_exists(path: &Path) -> bool {
    path.exists()
}
