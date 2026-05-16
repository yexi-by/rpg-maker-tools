//! 项目统一错误类型。
//!
//! 底层模块返回结构化错误，CLI 入口再把错误渲染成中文终端信息或 JSON 报告。

use std::path::PathBuf;

/// A.T.T MZ Rust 核心库的统一错误。
#[derive(Debug, thiserror::Error)]
pub enum AttMzError {
    /// 文件或目录不存在。
    #[error("{kind}不存在: {path}")]
    MissingPath {
        /// 缺失对象的中文类型。
        kind: &'static str,
        /// 缺失对象路径。
        path: PathBuf,
    },

    /// 路径类型不符合预期。
    #[error("{kind}不是目录: {path}")]
    NotDirectory {
        /// 当前检查对象的中文类型。
        kind: &'static str,
        /// 实际路径。
        path: PathBuf,
    },

    /// 游戏标题不能安全映射到数据库文件名。
    #[error("游戏标题包含非法文件名字，无法创建数据库: {chars}")]
    InvalidGameTitle {
        /// 命中的非法字符集合。
        chars: String,
    },

    /// 配置文件内容不合法。
    #[error("配置加载失败: {0}")]
    InvalidConfig(String),

    /// 游戏目录内容不合法。
    #[error("游戏目录校验失败: {0}")]
    InvalidGame(String),

    /// JSON 解析失败。
    #[error("JSON 解析失败: {context}: {source}")]
    Json {
        /// 当前正在解析的对象。
        context: String,
        /// serde_json 原始错误。
        source: serde_json::Error,
    },

    /// TOML 解析失败。
    #[error("TOML 解析失败: {context}: {source}")]
    Toml {
        /// 当前正在解析的对象。
        context: String,
        /// toml 原始错误。
        source: toml::de::Error,
    },

    /// SQLite 操作失败。
    #[error("数据库操作失败: {context}: {source}")]
    Sqlite {
        /// 当前数据库动作。
        context: String,
        /// rusqlite 原始错误。
        source: rusqlite::Error,
    },

    /// 文件系统 I/O 失败。
    #[error("文件操作失败: {context}: {source}")]
    Io {
        /// 当前文件系统动作。
        context: String,
        /// 标准库 I/O 原始错误。
        source: std::io::Error,
    },
}

/// 核心库统一返回类型。
pub type Result<T> = std::result::Result<T, AttMzError>;

impl AttMzError {
    /// 为 I/O 错误补充中文上下文。
    pub fn io(context: impl Into<String>, source: std::io::Error) -> Self {
        Self::Io {
            context: context.into(),
            source,
        }
    }

    /// 为 SQLite 错误补充中文上下文。
    pub fn sqlite(context: impl Into<String>, source: rusqlite::Error) -> Self {
        Self::Sqlite {
            context: context.into(),
            source,
        }
    }
}
