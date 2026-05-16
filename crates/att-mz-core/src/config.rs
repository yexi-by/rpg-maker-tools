//! 配置文件与环境变量读取。
//!
//! Rust CLI 默认读取当前工作目录下的 `setting.toml`。模型地址和 API Key
//! 继续支持现有环境变量覆盖，保证 Agent 工作流不需要改配置入口。

use std::env;
use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{Map, Value, json};
use toml::Value as TomlValue;

use crate::error::{AttMzError, Result};

/// 默认配置文件名。
pub const DEFAULT_SETTING_FILE_NAME: &str = "setting.toml";

/// 模型服务地址环境变量名。
pub const LLM_BASE_URL_ENV_NAME: &str = "RPG_MAKER_TOOLS_LLM_BASE_URL";

/// 模型 API Key 环境变量名。
pub const LLM_API_KEY_ENV_NAME: &str = "RPG_MAKER_TOOLS_LLM_API_KEY";

/// 缺省源语言识别正则，和现有 Python 配置模型默认值保持一致。
pub const DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN: &str =
    r"[\u{3040}-\u{309F}\u{30A0}-\u{30FF}\u{3400}-\u{4DBF}\u{4E00}-\u{9FFF}\u{F900}-\u{FAFF}]+";

const DEFAULT_JAPANESE_SEGMENT_PATTERN: &str = r"[\u{3040}-\u{309F}\u{30A0}-\u{30FF}]+";
const DEFAULT_RESIDUAL_ESCAPE_SEQUENCE_PATTERN: &str = r"\\[nrt]";
const DEFAULT_LINE_WIDTH_COUNT_PATTERN: &str = r"\S";

/// 环境变量覆盖结果。
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct EnvironmentOverrides {
    /// 覆盖后的模型服务地址。
    pub llm_base_url: Option<String>,
    /// 覆盖后的模型 API Key。
    pub llm_api_key: Option<String>,
}

impl EnvironmentOverrides {
    /// 当前是否提供了任意覆盖项。
    pub fn has_any(&self) -> bool {
        self.llm_base_url.is_some() || self.llm_api_key.is_some()
    }

    /// 返回已经生效的环境变量名。
    pub fn enabled_names(&self) -> Vec<String> {
        let mut names = Vec::new();
        if self.llm_base_url.is_some() {
            names.push(LLM_BASE_URL_ENV_NAME.to_string());
        }
        if self.llm_api_key.is_some() {
            names.push(LLM_API_KEY_ENV_NAME.to_string());
        }
        names
    }
}

/// 诊断命令需要展示的配置摘要。
#[derive(Debug, Clone, PartialEq)]
pub struct SettingSummary {
    /// 配置文件绝对路径。
    pub setting_path: PathBuf,
    /// 模型服务地址。
    pub llm_base_url: String,
    /// 模型 API Key。
    pub llm_api_key: String,
    /// 模型名称。
    pub llm_model: String,
    /// 模型请求超时时间。
    pub llm_timeout: i64,
    /// 模型请求额外参数数量。
    pub request_body_extra_count: usize,
    /// 正文翻译提示词文件路径。
    pub system_prompt_file: String,
    /// 已注入的提示词文本长度。
    pub system_prompt_length: usize,
    /// 环境变量覆盖项。
    pub environment_overrides: EnvironmentOverrides,
}

/// 文本处理规则配置。
///
/// 该结构只收敛 Rust 已迁移命令需要的文本规则字段，保持与 Python 配置
/// 默认值一致。后续翻译和写回迁移可以继续在这里补充需要的字段。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TextRuleOptions {
    /// 长文本自动切行时每行允许的计数字符数量。
    pub long_text_line_width_limit: usize,
    /// 参与行宽统计的字符正则。
    pub line_width_count_pattern: String,
    /// 判断译文中日文片段的正则。
    pub japanese_segment_pattern: String,
    /// 残留检查前剥离转义噪音的正则。
    pub residual_escape_sequence_pattern: String,
    /// 混排时允许保留的日文字符。
    pub allowed_japanese_chars: Vec<String>,
    /// 混排词尾可放行的日文语气字符。
    pub allowed_japanese_tail_chars: Vec<String>,
}

impl Default for TextRuleOptions {
    fn default() -> Self {
        Self {
            long_text_line_width_limit: 26,
            line_width_count_pattern: DEFAULT_LINE_WIDTH_COUNT_PATTERN.to_string(),
            japanese_segment_pattern: DEFAULT_JAPANESE_SEGMENT_PATTERN.to_string(),
            residual_escape_sequence_pattern: DEFAULT_RESIDUAL_ESCAPE_SEQUENCE_PATTERN.to_string(),
            allowed_japanese_chars: ["っ", "ッ", "ー", "・", "。", "～", "…"]
                .into_iter()
                .map(str::to_string)
                .collect(),
            allowed_japanese_tail_chars: [
                "あ", "い", "う", "え", "お", "っ", "ッ", "ん", "ー", "よ", "ね", "な", "か",
            ]
            .into_iter()
            .map(str::to_string)
            .collect(),
        }
    }
}

/// 正文翻译上下文切批配置。
#[derive(Debug, Clone, PartialEq)]
pub struct TranslationContextOptions {
    /// 每批目标 token 上限。
    pub token_size: usize,
    /// 字符数量到 token 数量的粗略换算系数。
    pub factor: f64,
    /// 为保持同角色上下文而额外带入的指令条目上限。
    pub max_command_items: usize,
}

/// 正文翻译请求调度配置。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TextTranslationOptions {
    /// 并发 worker 数。Rust 迁移入口当前会读取并保留该配置，实际请求按稳定顺序执行。
    pub worker_count: usize,
    /// 每分钟请求数限制；`None` 表示不限速。
    pub rpm: Option<usize>,
    /// 可恢复请求错误的重试次数，不包含首次请求。
    pub retry_count: usize,
    /// 可恢复请求错误的基础重试间隔秒数。
    pub retry_delay: u64,
    /// 系统提示词正文。
    pub system_prompt: String,
}

/// OpenAI 兼容模型请求配置。
#[derive(Debug, Clone, PartialEq)]
pub struct LlmOptions {
    /// OpenAI 兼容服务地址。
    pub base_url: String,
    /// 模型 API Key。
    pub api_key: String,
    /// 模型名称。
    pub model: String,
    /// 请求超时秒数。
    pub timeout_seconds: u64,
    /// 透传给模型服务请求体的额外 JSON 字段。
    pub request_body_extra: Map<String, Value>,
}

/// 正文翻译运行所需的完整配置。
#[derive(Debug, Clone, PartialEq)]
pub struct RuntimeSettings {
    /// 模型请求配置。
    pub llm: LlmOptions,
    /// 正文切批配置。
    pub translation_context: TranslationContextOptions,
    /// 正文请求与提示词配置。
    pub text_translation: TextTranslationOptions,
    /// 文本规则配置。
    pub text_rules: TextRuleOptions,
    /// 判断源文本是否需要进入正文翻译的正则。
    pub source_text_required_pattern: String,
    /// 可选字体覆盖路径。
    pub replacement_font_path: Option<String>,
}

/// 解析默认配置文件路径。
pub fn resolve_setting_path(setting_path: Option<&Path>) -> Result<PathBuf> {
    let path = match setting_path {
        Some(path) => path.to_path_buf(),
        None => env::current_dir()
            .map_err(|error| AttMzError::io("读取当前工作目录", error))?
            .join(DEFAULT_SETTING_FILE_NAME),
    };
    path.canonicalize()
        .or(Ok::<PathBuf, std::io::Error>(path))
        .map_err(|error| AttMzError::io("解析配置文件路径", error))
}

/// 读取模型连接相关环境变量。
pub fn load_environment_overrides() -> EnvironmentOverrides {
    EnvironmentOverrides {
        llm_base_url: read_non_empty_env(LLM_BASE_URL_ENV_NAME),
        llm_api_key: read_non_empty_env(LLM_API_KEY_ENV_NAME),
    }
}

/// 加载并校验当前配置摘要。
pub fn load_setting_summary(setting_path: Option<&Path>) -> Result<SettingSummary> {
    let resolved_path = resolve_setting_path(setting_path)?;
    if !resolved_path.exists() {
        return Err(AttMzError::MissingPath {
            kind: "配置文件",
            path: resolved_path,
        });
    }

    let root = load_toml_root(&resolved_path)?;

    let environment_overrides = load_environment_overrides();
    let llm = read_table(&root, "llm")?;
    let text_translation = read_table(&root, "text_translation")?;

    let llm_base_url = environment_overrides
        .llm_base_url
        .clone()
        .unwrap_or_else(|| read_string(llm, "base_url").unwrap_or_default());
    let llm_api_key = environment_overrides
        .llm_api_key
        .clone()
        .unwrap_or_else(|| read_string(llm, "api_key").unwrap_or_default());
    let llm_model = read_string(llm, "model")?;
    let llm_timeout = read_integer(llm, "timeout")?;
    if llm_timeout <= 0 {
        return Err(AttMzError::InvalidConfig(
            "llm.timeout 必须大于 0".to_string(),
        ));
    }
    let request_body_extra_count = read_request_body_extra_count(llm)?;

    let system_prompt_file = read_string(text_translation, "system_prompt_file")?;
    let prompt_path = resolve_prompt_path(&resolved_path, &system_prompt_file);
    let system_prompt = fs::read_to_string(&prompt_path).map_err(|error| {
        AttMzError::io(format!("读取提示词文件 {}", prompt_path.display()), error)
    })?;

    Ok(SettingSummary {
        setting_path: resolved_path,
        llm_base_url,
        llm_api_key,
        llm_model,
        llm_timeout,
        request_body_extra_count,
        system_prompt_file,
        system_prompt_length: system_prompt.len(),
        environment_overrides,
    })
}

/// 加载正文翻译和写回共用的运行时配置。
///
/// 该函数只读取配置文件和环境变量，不访问游戏数据库。CLI 参数覆盖由入口层在
/// 调用后显式应用，避免配置加载函数依赖具体命令解析库。
pub fn load_runtime_settings(setting_path: Option<&Path>) -> Result<RuntimeSettings> {
    let resolved_path = resolve_setting_path(setting_path)?;
    if !resolved_path.exists() {
        return Err(AttMzError::MissingPath {
            kind: "配置文件",
            path: resolved_path,
        });
    }
    let root = load_toml_root(&resolved_path)?;
    let environment_overrides = load_environment_overrides();
    let llm = read_table(&root, "llm")?;
    let translation_context = read_table(&root, "translation_context")?;
    let text_translation = read_table(&root, "text_translation")?;

    let llm_base_url = environment_overrides
        .llm_base_url
        .clone()
        .unwrap_or_else(|| read_string(llm, "base_url").unwrap_or_default());
    let llm_api_key = environment_overrides
        .llm_api_key
        .clone()
        .unwrap_or_else(|| read_string(llm, "api_key").unwrap_or_default());
    let llm_model = read_string(llm, "model")?;
    let llm_timeout = read_positive_integer(llm, "timeout")?;
    let request_body_extra = read_request_body_extra(llm)?;

    let token_size = read_positive_integer(translation_context, "token_size")?;
    let factor = read_positive_float(translation_context, "factor")?;
    let max_command_items = read_positive_integer(translation_context, "max_command_items")?;

    let worker_count = read_positive_integer(text_translation, "worker_count")?;
    let rpm = read_optional_positive_integer(text_translation, "rpm")?;
    let retry_count = read_non_negative_integer(text_translation, "retry_count")?;
    let retry_delay = read_non_negative_integer(text_translation, "retry_delay")?;
    let system_prompt_file = read_string(text_translation, "system_prompt_file")?;
    let prompt_path = resolve_prompt_path(&resolved_path, &system_prompt_file);
    let system_prompt = fs::read_to_string(&prompt_path).map_err(|error| {
        AttMzError::io(format!("读取提示词文件 {}", prompt_path.display()), error)
    })?;

    let text_rules = load_text_rule_options(Some(&resolved_path))?;
    let source_text_required_pattern = load_source_text_required_pattern(Some(&resolved_path))?;
    let replacement_font_path = load_write_back_replacement_font_path(Some(&resolved_path), None)?;

    Ok(RuntimeSettings {
        llm: LlmOptions {
            base_url: llm_base_url,
            api_key: llm_api_key,
            model: llm_model,
            timeout_seconds: u64::try_from(llm_timeout).map_err(|error| {
                AttMzError::InvalidConfig(format!("llm.timeout 超出平台范围: {error}"))
            })?,
            request_body_extra,
        },
        translation_context: TranslationContextOptions {
            token_size: usize::try_from(token_size).map_err(|error| {
                AttMzError::InvalidConfig(format!(
                    "translation_context.token_size 超出平台范围: {error}"
                ))
            })?,
            factor,
            max_command_items: usize::try_from(max_command_items).map_err(|error| {
                AttMzError::InvalidConfig(format!(
                    "translation_context.max_command_items 超出平台范围: {error}"
                ))
            })?,
        },
        text_translation: TextTranslationOptions {
            worker_count: usize::try_from(worker_count).map_err(|error| {
                AttMzError::InvalidConfig(format!(
                    "text_translation.worker_count 超出平台范围: {error}"
                ))
            })?,
            rpm: match rpm {
                Some(value) => Some(usize::try_from(value).map_err(|error| {
                    AttMzError::InvalidConfig(format!("text_translation.rpm 超出平台范围: {error}"))
                })?),
                None => None,
            },
            retry_count: usize::try_from(retry_count).map_err(|error| {
                AttMzError::InvalidConfig(format!(
                    "text_translation.retry_count 超出平台范围: {error}"
                ))
            })?,
            retry_delay: u64::try_from(retry_delay).map_err(|error| {
                AttMzError::InvalidConfig(format!(
                    "text_translation.retry_delay 超出平台范围: {error}"
                ))
            })?,
            system_prompt,
        },
        text_rules,
        source_text_required_pattern,
        replacement_font_path,
    })
}

/// 读取事件指令参数导出的默认编码数组。
///
/// 当 CLI 未传 `--code` 时，`export-event-commands-json` 必须从
/// `event_command_text.default_command_codes` 读取默认编码；配置缺失或数组为空
/// 都属于业务配置错误，不能静默使用内置默认值。
pub fn load_event_command_default_codes(setting_path: Option<&Path>) -> Result<Vec<i64>> {
    let resolved_path = resolve_setting_path(setting_path)?;
    if !resolved_path.exists() {
        return Err(AttMzError::MissingPath {
            kind: "配置文件",
            path: resolved_path,
        });
    }
    let root = load_toml_root(&resolved_path)?;
    let event_command_text = read_table(&root, "event_command_text")?;
    let raw_codes = event_command_text
        .get("default_command_codes")
        .and_then(TomlValue::as_array)
        .ok_or_else(|| {
            AttMzError::InvalidConfig(
                "event_command_text.default_command_codes 必须是整数数组".to_string(),
            )
        })?;
    let mut codes = Vec::new();
    for raw_code in raw_codes {
        let Some(code) = raw_code.as_integer() else {
            return Err(AttMzError::InvalidConfig(
                "event_command_text.default_command_codes 只能包含整数".to_string(),
            ));
        };
        codes.push(code);
    }
    if codes.is_empty() {
        return Err(AttMzError::InvalidConfig(
            "event_command_text.default_command_codes 不能为空".to_string(),
        ));
    }
    Ok(codes)
}

/// 读取提取阶段判断“是否值得翻译”的源语言识别正则。
///
/// 配置文件不存在时返回默认值，便于只做结构校验的 Agent 命令在轻量环境中运行；
/// 配置存在但字段类型错误时显式报错，避免吞掉项目配置问题。
pub fn load_source_text_required_pattern(setting_path: Option<&Path>) -> Result<String> {
    let resolved_path = resolve_setting_path(setting_path)?;
    if !resolved_path.exists() {
        return Ok(DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN.to_string());
    }
    let root = load_toml_root(&resolved_path)?;
    let Some(text_rules) = root.get("text_rules").and_then(TomlValue::as_table) else {
        return Ok(DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN.to_string());
    };
    let Some(value) = text_rules.get("source_text_required_pattern") else {
        return Ok(DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN.to_string());
    };
    let Some(pattern) = value
        .as_str()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    else {
        return Err(AttMzError::InvalidConfig(
            "text_rules.source_text_required_pattern 必须是非空字符串".to_string(),
        ));
    };
    Ok(pattern.to_string())
}

/// 读取文本处理规则配置。
///
/// 配置文件不存在或缺少 `[text_rules]` 时使用默认值；字段存在但类型错误时
/// 显式报错，避免文本校验悄悄使用与项目不一致的规则。
pub fn load_text_rule_options(setting_path: Option<&Path>) -> Result<TextRuleOptions> {
    let resolved_path = resolve_setting_path(setting_path)?;
    if !resolved_path.exists() {
        return Ok(TextRuleOptions::default());
    }
    let root = load_toml_root(&resolved_path)?;
    let Some(text_rules) = root.get("text_rules").and_then(TomlValue::as_table) else {
        return Ok(TextRuleOptions::default());
    };
    let mut options = TextRuleOptions::default();
    if let Some(value) = text_rules.get("long_text_line_width_limit") {
        let Some(limit) = value.as_integer() else {
            return Err(AttMzError::InvalidConfig(
                "text_rules.long_text_line_width_limit 必须是正整数".to_string(),
            ));
        };
        if limit <= 0 {
            return Err(AttMzError::InvalidConfig(
                "text_rules.long_text_line_width_limit 必须大于 0".to_string(),
            ));
        }
        options.long_text_line_width_limit = usize::try_from(limit).map_err(|error| {
            AttMzError::InvalidConfig(format!(
                "text_rules.long_text_line_width_limit 超出平台范围: {error}"
            ))
        })?;
    }
    options.line_width_count_pattern = read_optional_text_rule_string(
        text_rules,
        "line_width_count_pattern",
        &options.line_width_count_pattern,
    )?;
    options.japanese_segment_pattern = read_optional_text_rule_string(
        text_rules,
        "japanese_segment_pattern",
        &options.japanese_segment_pattern,
    )?;
    options.residual_escape_sequence_pattern = read_optional_text_rule_string(
        text_rules,
        "residual_escape_sequence_pattern",
        &options.residual_escape_sequence_pattern,
    )?;
    options.allowed_japanese_chars =
        read_optional_text_rule_string_array(text_rules, "allowed_japanese_chars")?
            .unwrap_or(options.allowed_japanese_chars);
    options.allowed_japanese_tail_chars =
        read_optional_text_rule_string_array(text_rules, "allowed_japanese_tail_chars")?
            .unwrap_or(options.allowed_japanese_tail_chars);
    Ok(options)
}

/// 读取写回阶段的候选覆盖字体路径。
///
/// CLI 显式传入 `--replacement-font-path` 时使用 CLI 值；否则读取
/// `setting.toml` 的 `[write_back].replacement_font_path`。配置文件缺失会显式
/// 报错，避免字体还原命令在错误工作目录下静默退化。
pub fn load_write_back_replacement_font_path(
    setting_path: Option<&Path>,
    cli_replacement_font_path: Option<&Path>,
) -> Result<Option<String>> {
    let resolved_path = resolve_setting_path(setting_path)?;
    if !resolved_path.exists() {
        return Err(AttMzError::MissingPath {
            kind: "配置文件",
            path: resolved_path,
        });
    }
    let root = load_toml_root(&resolved_path)?;
    let config_value = match root.get("write_back") {
        Some(raw_write_back) => {
            let Some(write_back) = raw_write_back.as_table() else {
                return Err(AttMzError::InvalidConfig(
                    "write_back 必须是配置表".to_string(),
                ));
            };
            read_optional_string(write_back, "replacement_font_path")?
        }
        None => None,
    };
    let selected_value = cli_replacement_font_path
        .map(|path| path.to_string_lossy().trim().to_string())
        .filter(|value| !value.is_empty())
        .or(config_value);
    Ok(selected_value)
}

/// 把配置摘要转换成诊断报告字段。
pub fn setting_summary_json(summary: &SettingSummary) -> Map<String, Value> {
    let mut fields = Map::new();
    fields.insert("setting_path".to_string(), json!(summary.setting_path));
    fields.insert("llm_model".to_string(), json!(summary.llm_model));
    fields.insert(
        "request_body_extra_count".to_string(),
        json!(summary.request_body_extra_count),
    );
    fields.insert(
        "environment_overrides".to_string(),
        json!(summary.environment_overrides.enabled_names()),
    );
    fields
}

fn load_toml_root(resolved_path: &Path) -> Result<TomlValue> {
    let raw_text = fs::read_to_string(resolved_path).map_err(|error| {
        AttMzError::io(format!("读取配置文件 {}", resolved_path.display()), error)
    })?;
    let raw_text = raw_text.trim_start_matches('\u{feff}');
    toml::from_str(raw_text).map_err(|source| AttMzError::Toml {
        context: resolved_path.display().to_string(),
        source,
    })
}

fn read_non_empty_env(name: &str) -> Option<String> {
    let value = env::var(name).ok()?;
    let trimmed = value.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
}

fn read_table<'a>(
    root: &'a TomlValue,
    name: &str,
) -> Result<&'a toml::map::Map<String, TomlValue>> {
    root.get(name)
        .and_then(TomlValue::as_table)
        .ok_or_else(|| AttMzError::InvalidConfig(format!("配置文件中缺少 {name} 配置段")))
}

fn read_string(table: &toml::map::Map<String, TomlValue>, key: &str) -> Result<String> {
    let value = table
        .get(key)
        .and_then(TomlValue::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| AttMzError::InvalidConfig(format!("配置文件中缺少有效字符串: {key}")))?;
    Ok(value.to_string())
}

fn read_optional_string(
    table: &toml::map::Map<String, TomlValue>,
    key: &str,
) -> Result<Option<String>> {
    let Some(value) = table.get(key) else {
        return Ok(None);
    };
    let Some(raw_text) = value.as_str() else {
        return Err(AttMzError::InvalidConfig(format!(
            "配置项 {key} 必须是字符串"
        )));
    };
    let trimmed_text = raw_text.trim();
    if trimmed_text.is_empty() {
        Ok(None)
    } else {
        Ok(Some(trimmed_text.to_string()))
    }
}

fn read_integer(table: &toml::map::Map<String, TomlValue>, key: &str) -> Result<i64> {
    table
        .get(key)
        .and_then(TomlValue::as_integer)
        .ok_or_else(|| AttMzError::InvalidConfig(format!("配置文件中缺少有效整数: {key}")))
}

fn read_positive_integer(table: &toml::map::Map<String, TomlValue>, key: &str) -> Result<i64> {
    let value = read_integer(table, key)?;
    if value <= 0 {
        return Err(AttMzError::InvalidConfig(format!(
            "配置项 {key} 必须大于 0"
        )));
    }
    Ok(value)
}

fn read_non_negative_integer(table: &toml::map::Map<String, TomlValue>, key: &str) -> Result<i64> {
    let value = read_integer(table, key)?;
    if value < 0 {
        return Err(AttMzError::InvalidConfig(format!(
            "配置项 {key} 不能小于 0"
        )));
    }
    Ok(value)
}

fn read_optional_positive_integer(
    table: &toml::map::Map<String, TomlValue>,
    key: &str,
) -> Result<Option<i64>> {
    let Some(value) = table.get(key) else {
        return Ok(None);
    };
    if let Some(text) = value.as_str()
        && text.trim().eq_ignore_ascii_case("none")
    {
        return Ok(None);
    }
    let Some(number) = value.as_integer() else {
        return Err(AttMzError::InvalidConfig(format!(
            "配置项 {key} 必须是正整数或 none"
        )));
    };
    if number <= 0 {
        return Err(AttMzError::InvalidConfig(format!(
            "配置项 {key} 必须大于 0"
        )));
    }
    Ok(Some(number))
}

fn read_positive_float(table: &toml::map::Map<String, TomlValue>, key: &str) -> Result<f64> {
    let Some(value) = table.get(key) else {
        return Err(AttMzError::InvalidConfig(format!(
            "配置文件中缺少有效浮点数: {key}"
        )));
    };
    let number = value
        .as_float()
        .or_else(|| value.as_integer().map(|item| item as f64));
    let Some(number) = number else {
        return Err(AttMzError::InvalidConfig(format!(
            "配置文件中缺少有效浮点数: {key}"
        )));
    };
    if number <= 0.0 {
        return Err(AttMzError::InvalidConfig(format!(
            "配置项 {key} 必须大于 0"
        )));
    }
    Ok(number)
}

fn read_request_body_extra_count(table: &toml::map::Map<String, TomlValue>) -> Result<usize> {
    Ok(read_request_body_extra(table)?.len())
}

fn read_request_body_extra(
    table: &toml::map::Map<String, TomlValue>,
) -> Result<Map<String, Value>> {
    let Some(value) = table.get("request_body_extra") else {
        return Ok(Map::new());
    };
    let Some(text) = value.as_str() else {
        return Err(AttMzError::InvalidConfig(
            "llm.request_body_extra 必须是 JSON 对象字符串".to_string(),
        ));
    };
    let parsed: Value = serde_json::from_str(text).map_err(|source| AttMzError::Json {
        context: "llm.request_body_extra".to_string(),
        source,
    })?;
    let Some(object) = parsed.as_object() else {
        return Err(AttMzError::InvalidConfig(
            "llm.request_body_extra 必须解析为 JSON 对象".to_string(),
        ));
    };
    if object.contains_key("stream") || object.contains_key("stream_options") {
        return Err(AttMzError::InvalidConfig(
            "当前流程不支持 stream=true 或 stream_options".to_string(),
        ));
    }
    Ok(object.clone())
}

fn read_optional_text_rule_string(
    table: &toml::map::Map<String, TomlValue>,
    key: &str,
    default_value: &str,
) -> Result<String> {
    let Some(value) = table.get(key) else {
        return Ok(default_value.to_string());
    };
    let Some(text) = value
        .as_str()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    else {
        return Err(AttMzError::InvalidConfig(format!(
            "text_rules.{key} 必须是非空字符串"
        )));
    };
    Ok(text.to_string())
}

fn read_optional_text_rule_string_array(
    table: &toml::map::Map<String, TomlValue>,
    key: &str,
) -> Result<Option<Vec<String>>> {
    let Some(value) = table.get(key) else {
        return Ok(None);
    };
    let Some(array) = value.as_array() else {
        return Err(AttMzError::InvalidConfig(format!(
            "text_rules.{key} 必须是字符串数组"
        )));
    };
    let mut items = Vec::new();
    for item in array {
        let Some(text) = item
            .as_str()
            .map(str::trim)
            .filter(|value| !value.is_empty())
        else {
            return Err(AttMzError::InvalidConfig(format!(
                "text_rules.{key} 只能包含非空字符串"
            )));
        };
        items.push(text.to_string());
    }
    Ok(Some(items))
}

fn resolve_prompt_path(setting_path: &Path, prompt_file: &str) -> PathBuf {
    let prompt_path = PathBuf::from(prompt_file);
    if prompt_path.is_absolute() {
        prompt_path
    } else {
        setting_path
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join(prompt_path)
    }
}

#[cfg(test)]
mod tests {
    use std::fs;

    use super::*;

    #[test]
    fn setting_summary_reads_prompt_file() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        fs::create_dir(temp.path().join("prompts")).expect("提示词目录应创建成功");
        fs::write(temp.path().join("prompts/text.md"), "提示词").expect("提示词应写入成功");
        fs::write(
            temp.path().join("setting.toml"),
            r#"
[llm]
base_url = "https://example.test/v1"
api_key = "KEY"
model = "demo"
timeout = 60

[text_translation]
system_prompt_file = "prompts/text.md"

[event_command_text]
default_command_codes = [357, 999]
"#,
        )
        .expect("配置应写入成功");

        let summary =
            load_setting_summary(Some(&temp.path().join("setting.toml"))).expect("配置应加载成功");
        assert_eq!(summary.llm_model, "demo");
        assert_eq!(summary.system_prompt_length, "提示词".len());

        let codes = load_event_command_default_codes(Some(&temp.path().join("setting.toml")))
            .expect("事件指令默认编码应加载成功");
        assert_eq!(codes, vec![357, 999]);
    }
}
