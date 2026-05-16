//! 面向终端和外部 Agent 的统一报告模型。
//!
//! 该模型保持现有 Python CLI 的外层 JSON 形状，便于外部 Agent 继续按
//! `status/errors/warnings/summary/details` 读取命令结果。

use serde::Serialize;
use serde_json::{Map, Value};

/// 报告中的单条错误或告警。
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct AgentIssue {
    /// 稳定的问题代码，供 Agent 或测试判断。
    pub code: String,
    /// 给用户阅读的中文说明。
    pub message: String,
}

/// 统一 JSON 报告。
#[derive(Debug, Clone, Serialize)]
pub struct AgentReport {
    /// 当前报告状态：`ok`、`warning` 或 `error`。
    pub status: String,
    /// 必须处理的错误集合。
    pub errors: Vec<AgentIssue>,
    /// 不阻止继续执行但需要关注的告警集合。
    pub warnings: Vec<AgentIssue>,
    /// 适合扫读和机器判断的摘要字段。
    pub summary: Map<String, Value>,
    /// 详细上下文，通常只给 Agent 或排障使用。
    pub details: Map<String, Value>,
}

impl AgentReport {
    /// 根据错误和告警集合构造报告，并自动计算状态。
    pub fn from_parts(
        errors: Vec<AgentIssue>,
        warnings: Vec<AgentIssue>,
        summary: Map<String, Value>,
        details: Map<String, Value>,
    ) -> Self {
        let status = if !errors.is_empty() {
            "error"
        } else if !warnings.is_empty() {
            "warning"
        } else {
            "ok"
        };
        Self {
            status: status.to_string(),
            errors,
            warnings,
            summary,
            details,
        }
    }

    /// 序列化为和现有 CLI 一致的缩进 JSON 文本。
    pub fn to_json_text(&self) -> String {
        serde_json::to_string_pretty(self).unwrap_or_else(|error| {
            format!(
                "{{\"status\":\"error\",\"errors\":[{{\"code\":\"json_encode\",\"message\":\"报告序列化失败: {error}\"}}],\"warnings\":[],\"summary\":{{}},\"details\":{{}}}}"
            )
        })
    }

    /// 判断报告是否包含阻断错误。
    pub fn has_errors(&self) -> bool {
        !self.errors.is_empty()
    }
}

/// 创建一条报告问题。
pub fn issue(code: impl Into<String>, message: impl Into<String>) -> AgentIssue {
    AgentIssue {
        code: code.into(),
        message: message.into(),
    }
}
