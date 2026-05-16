//! 多游戏 SQLite 注册表。
//!
//! 数据库目录和表结构保持现有 Python 版本兼容，确保 Rust CLI 能继续读取
//! 已注册游戏，也能被后续迁移命令复用。

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::{Connection, params};

use crate::error::{AttMzError, Result};
use crate::event_command_rules::{
    EventCommandParameterFilter, EventCommandRuleImportResult, EventCommandRuleRecord,
    event_command_group_key, event_command_rule_identity, event_command_rule_prefixes,
    should_refresh_event_command_translation_items,
};
use crate::game::{read_game_title, validate_game_directory};
use crate::note_tag_rules::{
    NoteTagRuleImportResult, NoteTagRuleRecord, stale_note_tag_translation_paths,
};
use crate::placeholder::PlaceholderRule;
use crate::plugin_rules::PluginRuleRecord;
use crate::rmmz::EventCommandSnapshot;

/// 当前项目的数据库目录。
pub const DB_DIRECTORY: &str = "data/db";

const INVALID_FILE_NAME_CHARS: &[char] = &['<', '>', ':', '"', '/', '\\', '|', '?', '*'];
const METADATA_KEY: &str = "current_game";

/// 单个已注册游戏记录。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GameRecord {
    /// 游戏标题。
    pub game_title: String,
    /// 游戏根目录。
    pub game_path: PathBuf,
    /// 游戏数据库路径。
    pub db_path: PathBuf,
}

/// 最近一次正文翻译运行摘要。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TranslationRunRecord {
    /// 本次翻译编号。
    pub run_id: String,
    /// 运行状态。
    pub status: String,
    /// 运行开始时提取到的正文数量。
    pub total_extracted: usize,
    /// 运行开始时还没成功保存译文的文本数量。
    pub pending_count: usize,
    /// 相同原文合并后的请求数量。
    pub deduplicated_count: usize,
    /// 模型请求批次数量。
    pub batch_count: usize,
    /// 成功保存译文的数量。
    pub success_count: usize,
    /// 运行记录中的质量失败数量。
    pub quality_error_count: usize,
    /// 模型运行故障数量。
    pub llm_failure_count: usize,
    /// 停止原因。
    pub stop_reason: String,
    /// 最后一条错误摘要。
    pub last_error: String,
}

/// 模型质量错误的轻量摘要。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TranslationQualityErrorSummary {
    /// 正文在游戏里的内部位置。
    pub location_path: String,
    /// 质量错误类型。
    pub error_type: String,
}

/// 主翻译表中的已保存正文译文。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TranslationItemRecord {
    /// 正文在游戏里的内部位置。
    pub location_path: String,
    /// 正文条目类型，例如 `short_text`、`long_text` 或 `array`。
    pub item_type: String,
    /// 长文本角色；旁白或名字框文本。
    pub role: Option<String>,
    /// 当前提取到的原文行。
    pub original_lines: Vec<String>,
    /// 原文行在游戏数据中的逐行内部位置。
    pub source_line_paths: Vec<String>,
    /// 已通过项目检查、可以写进游戏文件的中文译文行。
    pub translation_lines: Vec<String>,
}

/// 最新翻译运行中没通过项目检查的译文记录。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TranslationErrorItemRecord {
    /// 正文在游戏里的内部位置。
    pub location_path: String,
    /// 正文条目类型，例如 `short_text`、`long_text` 或 `array`。
    pub item_type: String,
    /// 长文本角色；旁白或名字框文本。
    pub role: Option<String>,
    /// 触发质量错误的原文行。
    pub original_lines: Vec<String>,
    /// 模型返回但未通过项目检查的译文行。
    pub translation_lines: Vec<String>,
    /// 质量错误类型。
    pub error_type: String,
    /// 质量错误说明。
    pub error_detail: Vec<String>,
    /// 模型原始响应，供排障和修复参考。
    pub model_response: String,
}

/// 一次模型请求最终失败的运行级记录。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LlmFailureRecord {
    /// 所属正文翻译运行编号。
    pub run_id: String,
    /// 稳定故障分类，例如 `rate_limit`、`timeout` 或 `fatal`。
    pub category: String,
    /// 底层错误类型。
    pub error_type: String,
    /// 面向排障的中文错误摘要。
    pub error_message: String,
    /// 是否属于可恢复错误。
    pub retryable: bool,
    /// 实际尝试次数。
    pub attempt_count: usize,
}

/// 日文残留例外规则记录。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JapaneseResidualRuleRecord {
    /// 正文在游戏里的内部位置。
    pub location_path: String,
    /// 允许保留的日文片段。
    pub allowed_terms: Vec<String>,
    /// 例外原因。
    pub reason: String,
}

/// 一次字体覆盖写回中被替换的字段记录。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FontReplacementRecord {
    /// 被替换字段所在的游戏文件名。
    pub file_name: String,
    /// 被替换字段的 JSON Pointer 路径。
    pub value_path: String,
    /// 替换前文本。
    pub original_text: String,
    /// 替换后文本。
    pub replaced_text: String,
    /// 本次覆盖使用的新字体文件名。
    pub replacement_font_name: String,
}

/// 游戏注册表。
#[derive(Debug, Clone)]
pub struct GameRegistry {
    /// SQLite 数据库存放目录。
    pub db_directory: PathBuf,
}

impl Default for GameRegistry {
    fn default() -> Self {
        Self {
            db_directory: PathBuf::from(DB_DIRECTORY),
        }
    }
}

impl GameRegistry {
    /// 使用指定数据库目录创建注册表。
    pub fn new(db_directory: impl Into<PathBuf>) -> Self {
        Self {
            db_directory: db_directory.into(),
        }
    }

    /// 确保数据库目录存在。
    pub fn ensure_db_directory(&self) -> Result<()> {
        fs::create_dir_all(&self.db_directory).map_err(|error| {
            AttMzError::io(
                format!("创建数据库目录 {}", self.db_directory.display()),
                error,
            )
        })
    }

    /// 扫描已注册游戏列表。
    pub fn list_games(&self) -> Result<Vec<GameRecord>> {
        self.ensure_db_directory()?;
        let mut records = Vec::new();
        let entries = fs::read_dir(&self.db_directory).map_err(|error| {
            AttMzError::io(
                format!("扫描数据库目录 {}", self.db_directory.display()),
                error,
            )
        })?;
        for entry in entries {
            let entry = entry.map_err(|error| AttMzError::io("读取数据库目录项", error))?;
            let path = entry.path();
            if path.extension().and_then(|value| value.to_str()) != Some("db") {
                continue;
            }
            let connection = open_connection(&path)?;
            create_static_tables(&connection)?;
            let Some((game_title, game_path)) = read_metadata(&connection, &path)? else {
                continue;
            };
            records.push(GameRecord {
                game_title,
                game_path,
                db_path: path,
            });
        }
        records.sort_by(|left, right| left.game_title.cmp(&right.game_title));
        Ok(records)
    }

    /// 创建或更新单个游戏数据库绑定。
    pub fn register_game(&self, game_path: impl AsRef<Path>) -> Result<GameRecord> {
        self.ensure_db_directory()?;
        let resolved_game_path = game_path
            .as_ref()
            .canonicalize()
            .map_err(|error| AttMzError::io("解析游戏目录", error))?;
        let game_title = validate_game_directory(&resolved_game_path)?;
        let db_path = build_db_path(&game_title, &self.db_directory)?;
        let connection = open_connection(&db_path)?;
        create_static_tables(&connection)?;
        write_metadata(&connection, &game_title, &resolved_game_path, &db_path)?;
        Ok(GameRecord {
            game_title,
            game_path: resolved_game_path,
            db_path,
        })
    }

    /// 根据游戏目录解析已注册标题。
    pub fn resolve_registered_title_by_path(&self, game_path: impl AsRef<Path>) -> Result<String> {
        let resolved_path = game_path
            .as_ref()
            .canonicalize()
            .map_err(|error| AttMzError::io("解析游戏目录", error))?;
        for record in self.list_games()? {
            if record.game_path == resolved_path {
                return Ok(record.game_title);
            }
        }
        let title = read_game_title(resolved_path)?;
        Err(AttMzError::InvalidGame(format!(
            "游戏目录尚未注册，请先执行 add-game: {title}"
        )))
    }

    /// 打开指定游戏数据库并返回记录。
    pub fn open_game_record(&self, game_title: &str) -> Result<GameRecord> {
        self.ensure_db_directory()?;
        let db_path = build_db_path(game_title, &self.db_directory)?;
        if !db_path.exists() {
            return Err(AttMzError::InvalidGame(format!(
                "未找到游戏数据库: {game_title}"
            )));
        }
        let connection = open_connection(&db_path)?;
        create_static_tables(&connection)?;
        let Some((metadata_title, game_path)) = read_metadata(&connection, &db_path)? else {
            return Err(AttMzError::InvalidGame(format!(
                "数据库缺少 metadata 元数据记录: {}",
                db_path.display()
            )));
        };
        if metadata_title != game_title {
            return Err(AttMzError::InvalidGame(format!(
                "数据库元数据标题不匹配: 期望 {game_title}，实际 {metadata_title}"
            )));
        }
        Ok(GameRecord {
            game_title: metadata_title,
            game_path,
            db_path,
        })
    }

    /// 用当前游戏专用规则替换数据库中的自定义占位符规则。
    pub fn replace_placeholder_rules(
        &self,
        game_title: &str,
        rules: &[PlaceholderRule],
    ) -> Result<usize> {
        let record = self.open_game_record(game_title)?;
        let mut connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let transaction = connection.transaction().map_err(|source| {
            AttMzError::sqlite(
                format!("开始占位符规则事务 {}", record.db_path.display()),
                source,
            )
        })?;
        transaction
            .execute("DELETE FROM placeholder_rules", [])
            .map_err(|source| AttMzError::sqlite("清空自定义占位符规则", source))?;
        for rule in rules {
            transaction
                .execute(
                    "INSERT OR REPLACE INTO placeholder_rules (pattern_text, placeholder_template) VALUES (?1, ?2)",
                    params![rule.pattern_text, rule.placeholder_template],
                )
                .map_err(|source| AttMzError::sqlite("写入自定义占位符规则", source))?;
        }
        transaction
            .commit()
            .map_err(|source| AttMzError::sqlite("提交自定义占位符规则", source))?;
        Ok(rules.len())
    }

    /// 读取当前游戏数据库中的自定义占位符规则。
    pub fn read_placeholder_rules(&self, game_title: &str) -> Result<Vec<PlaceholderRule>> {
        let record = self.open_game_record(game_title)?;
        let connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let mut statement = connection
            .prepare(
                "SELECT pattern_text, placeholder_template FROM placeholder_rules ORDER BY pattern_text",
            )
            .map_err(|source| AttMzError::sqlite("读取自定义占位符规则", source))?;
        let rows = statement
            .query_map([], |row| {
                Ok(PlaceholderRule {
                    pattern_text: row.get(0)?,
                    placeholder_template: row.get(1)?,
                })
            })
            .map_err(|source| AttMzError::sqlite("查询自定义占位符规则", source))?;
        let mut rules = Vec::new();
        for row in rows {
            rules.push(row.map_err(|source| AttMzError::sqlite("读取自定义占位符规则行", source))?);
        }
        Ok(rules)
    }

    /// 原子替换当前游戏的字体覆盖记录。
    pub fn replace_font_replacement_records(
        &self,
        game_title: &str,
        records: &[FontReplacementRecord],
    ) -> Result<()> {
        let record = self.open_game_record(game_title)?;
        let mut connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let transaction = connection.transaction().map_err(|source| {
            AttMzError::sqlite(
                format!("开始写入字体覆盖记录事务 {}", record.db_path.display()),
                source,
            )
        })?;
        transaction
            .execute("DELETE FROM font_replacement_records", [])
            .map_err(|source| AttMzError::sqlite("清空字体覆盖记录", source))?;
        {
            let mut statement = transaction
                .prepare(
                    "INSERT OR REPLACE INTO font_replacement_records (file_name, value_path, original_text, replaced_text, replacement_font_name) VALUES (?1, ?2, ?3, ?4, ?5)",
                )
                .map_err(|source| AttMzError::sqlite("准备写入字体覆盖记录", source))?;
            for item in records {
                statement
                    .execute(params![
                        item.file_name,
                        item.value_path,
                        item.original_text,
                        item.replaced_text,
                        item.replacement_font_name
                    ])
                    .map_err(|source| AttMzError::sqlite("写入字体覆盖记录", source))?;
            }
        }
        transaction
            .commit()
            .map_err(|source| AttMzError::sqlite("提交字体覆盖记录事务", source))
    }

    /// 读取当前游戏保存的字体覆盖记录。
    pub fn read_font_replacement_records(
        &self,
        game_title: &str,
    ) -> Result<Vec<FontReplacementRecord>> {
        let record = self.open_game_record(game_title)?;
        let connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let mut statement = connection
            .prepare(
                "SELECT file_name, value_path, original_text, replaced_text, replacement_font_name FROM font_replacement_records ORDER BY file_name, value_path",
            )
            .map_err(|source| AttMzError::sqlite("读取字体覆盖记录", source))?;
        let rows = statement
            .query_map([], |row| {
                Ok(FontReplacementRecord {
                    file_name: row.get(0)?,
                    value_path: row.get(1)?,
                    original_text: row.get(2)?,
                    replaced_text: row.get(3)?,
                    replacement_font_name: row.get(4)?,
                })
            })
            .map_err(|source| AttMzError::sqlite("查询字体覆盖记录", source))?;
        let mut records = Vec::new();
        for row in rows {
            records.push(row.map_err(|source| AttMzError::sqlite("读取字体覆盖记录行", source))?);
        }
        Ok(records)
    }

    /// 清空当前游戏保存的字体覆盖记录，返回删除数量。
    pub fn clear_font_replacement_records(&self, game_title: &str) -> Result<usize> {
        let record = self.open_game_record(game_title)?;
        let connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let deleted_count = connection
            .execute("DELETE FROM font_replacement_records", [])
            .map_err(|source| AttMzError::sqlite("清空字体覆盖记录", source))?;
        Ok(deleted_count)
    }

    /// 读取当前游戏已导入的字段译名表。
    ///
    /// 返回 `None` 表示数据库从未导入过术语表；返回空映射表示曾导入过空术语表。
    pub fn read_terminology_registry(
        &self,
        game_title: &str,
    ) -> Result<Option<BTreeMap<String, BTreeMap<String, String>>>> {
        let record = self.open_game_record(game_title)?;
        let connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let mut statement = connection
            .prepare(
                "SELECT category, source_text, translated_text FROM terminology_terms ORDER BY category, source_text",
            )
            .map_err(|source| AttMzError::sqlite("读取字段译名表", source))?;
        let rows = statement
            .query_map([], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                ))
            })
            .map_err(|source| AttMzError::sqlite("查询字段译名表", source))?;
        let mut registry = BTreeMap::<String, BTreeMap<String, String>>::new();
        for row in rows {
            let (category, source_text, translated_text) =
                row.map_err(|source| AttMzError::sqlite("读取字段译名表行", source))?;
            registry
                .entry(category)
                .or_default()
                .insert(source_text, translated_text);
        }
        if registry.is_empty() && !terminology_import_state_exists(&connection)? {
            return Ok(None);
        }
        Ok(Some(registry))
    }

    /// 读取当前游戏已导入的正文术语表。
    ///
    /// 返回 `None` 表示数据库从未导入过术语表；返回空映射表示曾导入过空正文术语表。
    pub fn read_terminology_glossary(
        &self,
        game_title: &str,
    ) -> Result<Option<BTreeMap<String, String>>> {
        let record = self.open_game_record(game_title)?;
        let connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let mut statement = connection
            .prepare(
                "SELECT source_text, translated_text FROM terminology_glossary_terms ORDER BY source_text",
            )
            .map_err(|source| AttMzError::sqlite("读取正文术语表", source))?;
        let rows = statement
            .query_map([], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
            })
            .map_err(|source| AttMzError::sqlite("查询正文术语表", source))?;
        let mut glossary = BTreeMap::new();
        for row in rows {
            let (source_text, translated_text) =
                row.map_err(|source| AttMzError::sqlite("读取正文术语表行", source))?;
            glossary.insert(source_text, translated_text);
        }
        if glossary.is_empty() && !terminology_import_state_exists(&connection)? {
            return Ok(None);
        }
        Ok(Some(glossary))
    }

    /// 原子替换当前游戏的字段译名表和正文术语表。
    pub fn replace_terminology(
        &self,
        game_title: &str,
        registry: &BTreeMap<String, BTreeMap<String, String>>,
        glossary: &BTreeMap<String, String>,
    ) -> Result<()> {
        let record = self.open_game_record(game_title)?;
        let mut connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let transaction = connection.transaction().map_err(|source| {
            AttMzError::sqlite(
                format!("开始写入术语表事务 {}", record.db_path.display()),
                source,
            )
        })?;
        transaction
            .execute("DELETE FROM terminology_terms", [])
            .map_err(|source| AttMzError::sqlite("清空字段译名表", source))?;
        transaction
            .execute("DELETE FROM terminology_glossary_terms", [])
            .map_err(|source| AttMzError::sqlite("清空正文术语表", source))?;
        transaction
            .execute(
                "INSERT OR REPLACE INTO terminology_import_state (state_key, imported) VALUES ('terminology', 1)",
                [],
            )
            .map_err(|source| AttMzError::sqlite("写入术语表导入状态", source))?;
        for (category, entries) in registry {
            for (source_text, translated_text) in entries {
                transaction
                    .execute(
                        "INSERT OR REPLACE INTO terminology_terms (category, source_text, translated_text) VALUES (?1, ?2, ?3)",
                        params![category, source_text, translated_text],
                    )
                    .map_err(|source| AttMzError::sqlite("写入字段译名表", source))?;
            }
        }
        for (source_text, translated_text) in glossary {
            transaction
                .execute(
                    "INSERT OR REPLACE INTO terminology_glossary_terms (source_text, translated_text) VALUES (?1, ?2)",
                    params![source_text, translated_text],
                )
                .map_err(|source| AttMzError::sqlite("写入正文术语表", source))?;
        }
        transaction
            .commit()
            .map_err(|source| AttMzError::sqlite("提交术语表", source))
    }

    /// 读取主翻译表中已经成功保存译文的正文定位路径。
    pub fn read_translation_location_paths(&self, game_title: &str) -> Result<BTreeSet<String>> {
        let record = self.open_game_record(game_title)?;
        let connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let mut statement = connection
            .prepare("SELECT location_path FROM translation_items")
            .map_err(|source| AttMzError::sqlite("读取已保存译文定位路径", source))?;
        let rows = statement
            .query_map([], |row| row.get::<_, String>(0))
            .map_err(|source| AttMzError::sqlite("查询已保存译文定位路径", source))?;
        let mut paths = BTreeSet::new();
        for row in rows {
            paths.insert(row.map_err(|source| AttMzError::sqlite("读取已保存译文路径行", source))?);
        }
        Ok(paths)
    }

    /// 读取主翻译表中的全部已保存译文。
    pub fn read_translated_items(&self, game_title: &str) -> Result<Vec<TranslationItemRecord>> {
        let record = self.open_game_record(game_title)?;
        let connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let mut statement = connection
            .prepare(
                "SELECT location_path, item_type, role, original_lines, source_line_paths, translation_lines FROM translation_items ORDER BY location_path",
            )
            .map_err(|source| AttMzError::sqlite("读取已保存正文译文", source))?;
        let rows = statement
            .query_map([], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, Option<String>>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, String>(5)?,
                ))
            })
            .map_err(|source| AttMzError::sqlite("查询已保存正文译文", source))?;
        let mut items = Vec::new();
        for row in rows {
            let (
                location_path,
                item_type,
                role,
                original_lines_text,
                source_line_paths_text,
                translation_lines_text,
            ) = row.map_err(|source| AttMzError::sqlite("读取已保存正文译文行", source))?;
            items.push(TranslationItemRecord {
                location_path,
                item_type,
                role,
                original_lines: parse_json_string_array(
                    &original_lines_text,
                    "translation_items.original_lines",
                )?,
                source_line_paths: parse_json_string_array(
                    &source_line_paths_text,
                    "translation_items.source_line_paths",
                )?,
                translation_lines: parse_json_string_array(
                    &translation_lines_text,
                    "translation_items.translation_lines",
                )?,
            });
        }
        Ok(items)
    }

    /// 批量写入已经通过项目检查的正文译文。
    pub fn write_translation_items(
        &self,
        game_title: &str,
        items: &[TranslationItemRecord],
    ) -> Result<()> {
        let record = self.open_game_record(game_title)?;
        let mut connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let transaction = connection.transaction().map_err(|source| {
            AttMzError::sqlite(
                format!("开始写入正文译文事务 {}", record.db_path.display()),
                source,
            )
        })?;
        for item in items {
            let original_lines = serialize_json_lines(&item.original_lines, "original_lines")?;
            let source_line_paths =
                serialize_json_lines(&item.source_line_paths, "source_line_paths")?;
            let translation_lines =
                serialize_json_lines(&item.translation_lines, "translation_lines")?;
            transaction
                .execute(
                    "INSERT OR REPLACE INTO translation_items (location_path, item_type, role, original_lines, source_line_paths, translation_lines) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                    params![
                        item.location_path,
                        item.item_type,
                        item.role,
                        original_lines,
                        source_line_paths,
                        translation_lines
                    ],
                )
                .map_err(|source| AttMzError::sqlite("写入正文译文", source))?;
        }
        transaction
            .commit()
            .map_err(|source| AttMzError::sqlite("提交正文译文", source))
    }

    /// 按精确内部位置删除主翻译表记录。
    pub fn delete_translation_items_by_paths(
        &self,
        game_title: &str,
        location_paths: &[String],
    ) -> Result<usize> {
        let record = self.open_game_record(game_title)?;
        let mut connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let transaction = connection.transaction().map_err(|source| {
            AttMzError::sqlite(
                format!("开始删除正文译文事务 {}", record.db_path.display()),
                source,
            )
        })?;
        let mut deleted_rows = 0usize;
        for location_path in location_paths {
            let count = transaction
                .execute(
                    "DELETE FROM translation_items WHERE location_path = ?1",
                    params![location_path],
                )
                .map_err(|source| AttMzError::sqlite("删除正文译文", source))?;
            deleted_rows += count;
        }
        transaction
            .commit()
            .map_err(|source| AttMzError::sqlite("提交删除正文译文", source))?;
        Ok(deleted_rows)
    }

    /// 删除当前提取范围之外的已保存正文译文。
    pub fn delete_translation_items_except_paths(
        &self,
        game_title: &str,
        allowed_paths: &BTreeSet<String>,
    ) -> Result<usize> {
        let stored_paths = self.read_translation_location_paths(game_title)?;
        let stale_paths = stored_paths
            .difference(allowed_paths)
            .cloned()
            .collect::<Vec<_>>();
        self.delete_translation_items_by_paths(game_title, &stale_paths)
    }

    /// 清理已经被手动修好的模型质量错误明细。
    pub fn delete_translation_quality_errors_by_paths(
        &self,
        game_title: &str,
        location_paths: &BTreeSet<String>,
    ) -> Result<usize> {
        if location_paths.is_empty() {
            return Ok(0);
        }
        let record = self.open_game_record(game_title)?;
        let mut connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let transaction = connection.transaction().map_err(|source| {
            AttMzError::sqlite(
                format!("开始清理模型质量错误事务 {}", record.db_path.display()),
                source,
            )
        })?;
        let mut deleted_rows = 0usize;
        for location_path in location_paths {
            let count = transaction
                .execute(
                    "DELETE FROM translation_quality_errors WHERE location_path = ?1",
                    params![location_path],
                )
                .map_err(|source| AttMzError::sqlite("清理模型质量错误", source))?;
            deleted_rows += count;
        }
        transaction
            .commit()
            .map_err(|source| AttMzError::sqlite("提交清理模型质量错误", source))?;
        Ok(deleted_rows)
    }

    /// 读取当前游戏的日文残留例外规则。
    pub fn read_japanese_residual_rules(
        &self,
        game_title: &str,
    ) -> Result<Vec<JapaneseResidualRuleRecord>> {
        let record = self.open_game_record(game_title)?;
        let connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let mut statement = connection
            .prepare(
                "SELECT location_path, allowed_terms, reason FROM japanese_residual_rules ORDER BY location_path",
            )
            .map_err(|source| AttMzError::sqlite("读取日文残留例外规则", source))?;
        let rows = statement
            .query_map([], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                ))
            })
            .map_err(|source| AttMzError::sqlite("查询日文残留例外规则", source))?;
        let mut records = Vec::new();
        for row in rows {
            let (location_path, allowed_terms_text, reason) =
                row.map_err(|source| AttMzError::sqlite("读取日文残留例外规则行", source))?;
            let allowed_terms = parse_json_string_array(
                &allowed_terms_text,
                "japanese_residual_rules.allowed_terms",
            )?;
            records.push(JapaneseResidualRuleRecord {
                location_path,
                allowed_terms,
                reason,
            });
        }
        Ok(records)
    }

    /// 用当前游戏专用规则替换日文残留例外规则。
    pub fn replace_japanese_residual_rules(
        &self,
        game_title: &str,
        rules: &[JapaneseResidualRuleRecord],
    ) -> Result<()> {
        let record = self.open_game_record(game_title)?;
        let mut connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let transaction = connection.transaction().map_err(|source| {
            AttMzError::sqlite(
                format!("开始写入日文残留例外规则事务 {}", record.db_path.display()),
                source,
            )
        })?;
        transaction
            .execute("DELETE FROM japanese_residual_rules", [])
            .map_err(|source| AttMzError::sqlite("清空日文残留例外规则", source))?;
        for rule in rules {
            let allowed_terms = serialize_json_lines(&rule.allowed_terms, "allowed_terms")?;
            transaction
                .execute(
                    "INSERT OR REPLACE INTO japanese_residual_rules (location_path, allowed_terms, reason) VALUES (?1, ?2, ?3)",
                    params![rule.location_path, allowed_terms, rule.reason],
                )
                .map_err(|source| AttMzError::sqlite("写入日文残留例外规则", source))?;
        }
        transaction
            .commit()
            .map_err(|source| AttMzError::sqlite("提交日文残留例外规则", source))
    }

    /// 创建一条新的正文翻译运行记录。
    pub fn start_translation_run(
        &self,
        game_title: &str,
        total_extracted: usize,
        pending_count: usize,
        deduplicated_count: usize,
        batch_count: usize,
    ) -> Result<TranslationRunRecord> {
        let now = current_timestamp_text();
        let run_id = generate_run_id();
        let record = TranslationRunRecord {
            run_id,
            status: "running".to_string(),
            total_extracted,
            pending_count,
            deduplicated_count,
            batch_count,
            success_count: 0,
            quality_error_count: 0,
            llm_failure_count: 0,
            stop_reason: String::new(),
            last_error: String::new(),
        };
        self.write_translation_run_with_times(game_title, &record, &now, None)?;
        Ok(record)
    }

    /// 写入或更新正文翻译运行记录。
    pub fn write_translation_run(
        &self,
        game_title: &str,
        record: &TranslationRunRecord,
        finished: bool,
    ) -> Result<()> {
        let now = current_timestamp_text();
        let finished_at = if finished { Some(now.as_str()) } else { None };
        self.write_translation_run_with_times(game_title, record, &now, finished_at)
    }

    /// 记录一次模型请求最终失败。
    pub fn write_llm_failure(&self, game_title: &str, failure: &LlmFailureRecord) -> Result<()> {
        let record = self.open_game_record(game_title)?;
        let connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let now = current_timestamp_text();
        connection
            .execute(
                "INSERT INTO llm_failures (run_id, category, error_type, error_message, retryable, attempt_count, created_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                params![
                    failure.run_id,
                    failure.category,
                    failure.error_type,
                    failure.error_message,
                    if failure.retryable { 1_i64 } else { 0_i64 },
                    failure.attempt_count,
                    now,
                ],
            )
            .map_err(|source| AttMzError::sqlite("写入模型运行故障", source))?;
        Ok(())
    }

    /// 写入本轮模型翻了但项目检查没通过的译文记录。
    pub fn write_translation_quality_errors(
        &self,
        game_title: &str,
        run_id: &str,
        items: &[TranslationErrorItemRecord],
    ) -> Result<()> {
        if items.is_empty() {
            return Ok(());
        }
        let record = self.open_game_record(game_title)?;
        let mut connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let transaction = connection.transaction().map_err(|source| {
            AttMzError::sqlite(
                format!("开始写入模型质量错误事务 {}", record.db_path.display()),
                source,
            )
        })?;
        for item in items {
            let original_lines = serialize_json_lines(&item.original_lines, "original_lines")?;
            let translation_lines =
                serialize_json_lines(&item.translation_lines, "translation_lines")?;
            let error_detail = serialize_json_lines(&item.error_detail, "error_detail")?;
            transaction
                .execute(
                    "INSERT OR REPLACE INTO translation_quality_errors (run_id, location_path, item_type, role, original_lines, translation_lines, error_type, error_detail, model_response) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
                    params![
                        run_id,
                        item.location_path,
                        item.item_type,
                        item.role,
                        original_lines,
                        translation_lines,
                        item.error_type,
                        error_detail,
                        item.model_response,
                    ],
                )
                .map_err(|source| AttMzError::sqlite("写入模型质量错误", source))?;
        }
        transaction
            .commit()
            .map_err(|source| AttMzError::sqlite("提交模型质量错误", source))
    }

    fn write_translation_run_with_times(
        &self,
        game_title: &str,
        record: &TranslationRunRecord,
        now: &str,
        finished_at: Option<&str>,
    ) -> Result<()> {
        let game_record = self.open_game_record(game_title)?;
        let connection = open_connection(&game_record.db_path)?;
        create_static_tables(&connection)?;
        connection
            .execute(
                "INSERT INTO translation_runs (run_id, status, total_extracted, pending_count, deduplicated_count, batch_count, success_count, quality_error_count, llm_failure_count, started_at, updated_at, finished_at, stop_reason, last_error)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?10, ?11, ?12, ?13)
                 ON CONFLICT(run_id) DO UPDATE SET
                    status = excluded.status,
                    total_extracted = excluded.total_extracted,
                    pending_count = excluded.pending_count,
                    deduplicated_count = excluded.deduplicated_count,
                    batch_count = excluded.batch_count,
                    success_count = excluded.success_count,
                    quality_error_count = excluded.quality_error_count,
                    llm_failure_count = excluded.llm_failure_count,
                    updated_at = excluded.updated_at,
                    finished_at = COALESCE(excluded.finished_at, translation_runs.finished_at),
                    stop_reason = excluded.stop_reason,
                    last_error = excluded.last_error",
                params![
                    record.run_id,
                    record.status,
                    record.total_extracted,
                    record.pending_count,
                    record.deduplicated_count,
                    record.batch_count,
                    record.success_count,
                    record.quality_error_count,
                    record.llm_failure_count,
                    now,
                    finished_at,
                    record.stop_reason,
                    record.last_error,
                ],
            )
            .map_err(|source| AttMzError::sqlite("写入正文翻译运行记录", source))?;
        Ok(())
    }

    /// 读取最近一次正文翻译运行记录。
    pub fn read_latest_translation_run(
        &self,
        game_title: &str,
    ) -> Result<Option<TranslationRunRecord>> {
        let record = self.open_game_record(game_title)?;
        let connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let mut statement = connection
            .prepare(
                "SELECT run_id, status, total_extracted, pending_count, deduplicated_count, batch_count, success_count, quality_error_count, llm_failure_count, stop_reason, last_error FROM translation_runs ORDER BY started_at DESC, run_id DESC LIMIT 1",
            )
            .map_err(|source| AttMzError::sqlite("读取最近翻译运行", source))?;
        let mut rows = statement
            .query([])
            .map_err(|source| AttMzError::sqlite("查询最近翻译运行", source))?;
        let Some(row) = rows
            .next()
            .map_err(|source| AttMzError::sqlite("读取最近翻译运行行", source))?
        else {
            return Ok(None);
        };
        Ok(Some(TranslationRunRecord {
            run_id: row
                .get(0)
                .map_err(|source| AttMzError::sqlite("读取 run_id", source))?,
            status: row
                .get(1)
                .map_err(|source| AttMzError::sqlite("读取 status", source))?,
            total_extracted: row
                .get(2)
                .map_err(|source| AttMzError::sqlite("读取 total_extracted", source))?,
            pending_count: row
                .get(3)
                .map_err(|source| AttMzError::sqlite("读取 pending_count", source))?,
            deduplicated_count: row
                .get(4)
                .map_err(|source| AttMzError::sqlite("读取 deduplicated_count", source))?,
            batch_count: row
                .get(5)
                .map_err(|source| AttMzError::sqlite("读取 batch_count", source))?,
            success_count: row
                .get(6)
                .map_err(|source| AttMzError::sqlite("读取 success_count", source))?,
            quality_error_count: row
                .get(7)
                .map_err(|source| AttMzError::sqlite("读取 quality_error_count", source))?,
            llm_failure_count: row
                .get(8)
                .map_err(|source| AttMzError::sqlite("读取 llm_failure_count", source))?,
            stop_reason: row
                .get(9)
                .map_err(|source| AttMzError::sqlite("读取 stop_reason", source))?,
            last_error: row
                .get(10)
                .map_err(|source| AttMzError::sqlite("读取 last_error", source))?,
        }))
    }

    /// 按类别统计指定运行中的模型运行故障。
    pub fn read_llm_failure_counts(
        &self,
        game_title: &str,
        run_id: &str,
    ) -> Result<BTreeMap<String, usize>> {
        let record = self.open_game_record(game_title)?;
        let connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let mut statement = connection
            .prepare("SELECT category, COUNT(*) FROM llm_failures WHERE run_id = ?1 GROUP BY category ORDER BY category")
            .map_err(|source| AttMzError::sqlite("读取模型故障统计", source))?;
        let rows = statement
            .query_map(params![run_id], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, usize>(1)?))
            })
            .map_err(|source| AttMzError::sqlite("查询模型故障统计", source))?;
        let mut counts = BTreeMap::new();
        for row in rows {
            let (category, count) =
                row.map_err(|source| AttMzError::sqlite("读取模型故障统计行", source))?;
            counts.insert(category, count);
        }
        Ok(counts)
    }

    /// 读取指定运行中的模型质量错误摘要。
    pub fn read_translation_quality_error_summaries(
        &self,
        game_title: &str,
        run_id: &str,
    ) -> Result<Vec<TranslationQualityErrorSummary>> {
        let record = self.open_game_record(game_title)?;
        let connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let mut statement = connection
            .prepare(
                "SELECT location_path, error_type FROM translation_quality_errors WHERE run_id = ?1 ORDER BY location_path",
            )
            .map_err(|source| AttMzError::sqlite("读取模型质量错误", source))?;
        let rows = statement
            .query_map(params![run_id], |row| {
                Ok(TranslationQualityErrorSummary {
                    location_path: row.get(0)?,
                    error_type: row.get(1)?,
                })
            })
            .map_err(|source| AttMzError::sqlite("查询模型质量错误", source))?;
        let mut items = Vec::new();
        for row in rows {
            items.push(row.map_err(|source| AttMzError::sqlite("读取模型质量错误行", source))?);
        }
        Ok(items)
    }

    /// 读取指定翻译运行中没通过项目检查的译文完整明细。
    pub fn read_translation_quality_errors(
        &self,
        game_title: &str,
        run_id: &str,
    ) -> Result<Vec<TranslationErrorItemRecord>> {
        let record = self.open_game_record(game_title)?;
        let connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let mut statement = connection
            .prepare(
                "SELECT location_path, item_type, role, original_lines, translation_lines, error_type, error_detail, model_response FROM translation_quality_errors WHERE run_id = ?1 ORDER BY location_path",
            )
            .map_err(|source| AttMzError::sqlite("读取模型质量错误明细", source))?;
        let rows = statement
            .query_map(params![run_id], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, Option<String>>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, String>(6)?,
                    row.get::<_, String>(7)?,
                ))
            })
            .map_err(|source| AttMzError::sqlite("查询模型质量错误明细", source))?;
        let mut items = Vec::new();
        for row in rows {
            let (
                location_path,
                item_type,
                role,
                original_lines_text,
                translation_lines_text,
                error_type,
                error_detail_text,
                model_response,
            ) = row.map_err(|source| AttMzError::sqlite("读取模型质量错误明细行", source))?;
            items.push(TranslationErrorItemRecord {
                location_path,
                item_type,
                role,
                original_lines: parse_json_string_array(
                    &original_lines_text,
                    "translation_quality_errors.original_lines",
                )?,
                translation_lines: parse_json_string_array(
                    &translation_lines_text,
                    "translation_quality_errors.translation_lines",
                )?,
                error_type,
                error_detail: parse_json_string_array(
                    &error_detail_text,
                    "translation_quality_errors.error_detail",
                )?,
                model_response,
            });
        }
        Ok(items)
    }

    /// 当所有正文已经修好时，把最新运行记录标记为完成。
    pub fn mark_translation_run_completed(&self, game_title: &str, run_id: &str) -> Result<()> {
        let record = self.open_game_record(game_title)?;
        let connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let now = current_timestamp_text();
        connection
            .execute(
                "UPDATE translation_runs SET status = 'completed', quality_error_count = 0, llm_failure_count = 0, updated_at = ?1, finished_at = ?1, stop_reason = '', last_error = '' WHERE run_id = ?2",
                params![now, run_id],
            )
            .map_err(|source| AttMzError::sqlite("更新正文翻译运行完成状态", source))?;
        Ok(())
    }

    /// 用一次外部导入结果替换当前游戏的全部插件文本规则。
    ///
    /// 当同一插件旧规则的结构哈希或路径模板发生变化时，函数会删除该插件前缀
    /// 下已经保存的正文译文，避免后续流程继续使用过期译文。
    pub fn replace_plugin_text_rules(
        &self,
        game_title: &str,
        rules: &[PluginRuleRecord],
    ) -> Result<PluginRuleImportResult> {
        let record = self.open_game_record(game_title)?;
        let mut connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let old_rules = read_plugin_text_rules_from_connection(&connection)?;
        let transaction = connection.transaction().map_err(|source| {
            AttMzError::sqlite(
                format!("开始插件规则事务 {}", record.db_path.display()),
                source,
            )
        })?;
        let mut deleted_translation_items = 0usize;
        for rule in rules {
            let old_rule = old_rules
                .iter()
                .find(|old_rule| old_rule.plugin_index == rule.plugin_index);
            if should_refresh_plugin_translation_items(old_rule, rule) {
                let prefix = format!("plugins.js/{}/%", rule.plugin_index);
                let deleted_count = transaction
                    .execute(
                        "DELETE FROM translation_items WHERE location_path LIKE ?1",
                        params![prefix],
                    )
                    .map_err(|source| AttMzError::sqlite("删除过期插件译文", source))?;
                deleted_translation_items += deleted_count;
            }
        }
        transaction
            .execute("DELETE FROM plugin_text_rules", [])
            .map_err(|source| AttMzError::sqlite("清空插件文本规则", source))?;
        for rule in rules {
            for path_template in &rule.path_templates {
                transaction
                    .execute(
                        "INSERT OR REPLACE INTO plugin_text_rules (plugin_index, plugin_name, plugin_hash, path_template) VALUES (?1, ?2, ?3, ?4)",
                        params![
                            rule.plugin_index,
                            rule.plugin_name,
                            rule.plugin_hash,
                            path_template,
                        ],
                    )
                    .map_err(|source| AttMzError::sqlite("写入插件文本规则", source))?;
            }
        }
        transaction
            .commit()
            .map_err(|source| AttMzError::sqlite("提交插件文本规则", source))?;
        Ok(PluginRuleImportResult {
            imported_plugin_count: rules.len(),
            imported_rule_count: rules.iter().map(|record| record.path_templates.len()).sum(),
            deleted_translation_items,
        })
    }

    /// 读取当前游戏保存的全部插件文本规则。
    pub fn read_plugin_text_rules(&self, game_title: &str) -> Result<Vec<PluginRuleRecord>> {
        let record = self.open_game_record(game_title)?;
        let connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        read_plugin_text_rules_from_connection(&connection)
    }

    /// 用一次外部导入结果替换当前游戏的全部事件指令文本规则。
    ///
    /// 规则组变化时只删除对应事件指令路径前缀下的旧译文，避免影响其它正文。
    pub fn replace_event_command_text_rules(
        &self,
        game_title: &str,
        rules: &[EventCommandRuleRecord],
        command_snapshots: &[EventCommandSnapshot],
    ) -> Result<EventCommandRuleImportResult> {
        let record = self.open_game_record(game_title)?;
        let mut connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let old_rules = read_event_command_text_rules_from_connection(&connection)?;
        let old_rules_by_key = old_rules
            .iter()
            .map(|rule| (event_command_rule_identity(rule), rule))
            .collect::<HashMap<_, _>>();

        let transaction = connection.transaction().map_err(|source| {
            AttMzError::sqlite(
                format!("开始事件指令规则事务 {}", record.db_path.display()),
                source,
            )
        })?;
        let mut deleted_translation_items = 0usize;
        for rule in rules {
            let old_rule = old_rules_by_key
                .get(&event_command_rule_identity(rule))
                .copied();
            if should_refresh_event_command_translation_items(old_rule, rule) {
                for prefix in event_command_rule_prefixes(command_snapshots, rule) {
                    let like_pattern = format!("{prefix}%");
                    let deleted_count = transaction
                        .execute(
                            "DELETE FROM translation_items WHERE location_path LIKE ?1",
                            params![like_pattern],
                        )
                        .map_err(|source| AttMzError::sqlite("删除过期事件指令译文", source))?;
                    deleted_translation_items += deleted_count;
                }
            }
        }

        transaction
            .execute("DELETE FROM event_command_text_rule_paths", [])
            .map_err(|source| AttMzError::sqlite("清空事件指令路径规则", source))?;
        transaction
            .execute("DELETE FROM event_command_text_rule_filters", [])
            .map_err(|source| AttMzError::sqlite("清空事件指令参数过滤规则", source))?;
        transaction
            .execute("DELETE FROM event_command_text_rule_groups", [])
            .map_err(|source| AttMzError::sqlite("清空事件指令规则组", source))?;
        for rule in rules {
            let group_key = event_command_group_key(rule);
            transaction
                .execute(
                    "INSERT OR REPLACE INTO event_command_text_rule_groups (group_key, command_code) VALUES (?1, ?2)",
                    params![group_key, rule.command_code],
                )
                .map_err(|source| AttMzError::sqlite("写入事件指令规则组", source))?;
            for parameter_filter in &rule.parameter_filters {
                transaction
                    .execute(
                        "INSERT OR REPLACE INTO event_command_text_rule_filters (group_key, parameter_index, parameter_value) VALUES (?1, ?2, ?3)",
                        params![group_key, parameter_filter.index, parameter_filter.value],
                    )
                    .map_err(|source| AttMzError::sqlite("写入事件指令参数过滤规则", source))?;
            }
            for path_template in &rule.path_templates {
                transaction
                    .execute(
                        "INSERT OR REPLACE INTO event_command_text_rule_paths (group_key, path_template) VALUES (?1, ?2)",
                        params![group_key, path_template],
                    )
                    .map_err(|source| AttMzError::sqlite("写入事件指令路径规则", source))?;
            }
        }
        transaction
            .commit()
            .map_err(|source| AttMzError::sqlite("提交事件指令规则", source))?;

        Ok(EventCommandRuleImportResult {
            imported_rule_group_count: rules.len(),
            imported_path_rule_count: rules.iter().map(|record| record.path_templates.len()).sum(),
            deleted_translation_items,
        })
    }

    /// 读取当前游戏保存的全部事件指令文本规则。
    pub fn read_event_command_text_rules(
        &self,
        game_title: &str,
    ) -> Result<Vec<EventCommandRuleRecord>> {
        let record = self.open_game_record(game_title)?;
        let connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        read_event_command_text_rules_from_connection(&connection)
    }

    /// 用一次外部导入结果替换当前游戏的全部 Note 标签文本规则。
    pub fn replace_note_tag_text_rules(
        &self,
        game_title: &str,
        rules: &[NoteTagRuleRecord],
        data_files: &BTreeMap<String, serde_json::Value>,
        source_text_required_pattern: &str,
    ) -> Result<NoteTagRuleImportResult> {
        let record = self.open_game_record(game_title)?;
        let mut connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        let old_rules = read_note_tag_text_rules_from_connection(&connection)?;
        let stale_paths = stale_note_tag_translation_paths(
            data_files,
            &old_rules,
            rules,
            source_text_required_pattern,
        )?;

        let transaction = connection.transaction().map_err(|source| {
            AttMzError::sqlite(
                format!("开始 Note 标签规则事务 {}", record.db_path.display()),
                source,
            )
        })?;
        let mut deleted_translation_items = 0usize;
        for stale_path in stale_paths {
            let deleted_count = transaction
                .execute(
                    "DELETE FROM translation_items WHERE location_path = ?1",
                    params![stale_path],
                )
                .map_err(|source| AttMzError::sqlite("删除过期 Note 标签译文", source))?;
            deleted_translation_items += deleted_count;
        }
        transaction
            .execute("DELETE FROM note_tag_text_rules", [])
            .map_err(|source| AttMzError::sqlite("清空 Note 标签文本规则", source))?;
        for rule in rules {
            for tag_name in &rule.tag_names {
                transaction
                    .execute(
                        "INSERT OR REPLACE INTO note_tag_text_rules (file_name, tag_name) VALUES (?1, ?2)",
                        params![rule.file_name, tag_name],
                    )
                    .map_err(|source| AttMzError::sqlite("写入 Note 标签文本规则", source))?;
            }
        }
        transaction
            .commit()
            .map_err(|source| AttMzError::sqlite("提交 Note 标签文本规则", source))?;

        Ok(NoteTagRuleImportResult {
            imported_file_count: rules.len(),
            imported_tag_count: rules.iter().map(|record| record.tag_names.len()).sum(),
            deleted_translation_items,
        })
    }

    /// 读取当前游戏保存的全部 Note 标签文本规则。
    pub fn read_note_tag_text_rules(&self, game_title: &str) -> Result<Vec<NoteTagRuleRecord>> {
        let record = self.open_game_record(game_title)?;
        let connection = open_connection(&record.db_path)?;
        create_static_tables(&connection)?;
        read_note_tag_text_rules_from_connection(&connection)
    }
}

/// 插件规则导入摘要。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PluginRuleImportResult {
    /// 本次写入的插件数量。
    pub imported_plugin_count: usize,
    /// 本次写入的路径规则数量。
    pub imported_rule_count: usize,
    /// 因规则变化而删除的旧译文数量。
    pub deleted_translation_items: usize,
}

/// 根据游戏标题生成数据库文件路径。
pub fn build_db_path(game_title: &str, db_directory: &Path) -> Result<PathBuf> {
    let invalid_chars: String = game_title
        .chars()
        .filter(|char_value| INVALID_FILE_NAME_CHARS.contains(char_value))
        .collect();
    if !invalid_chars.is_empty() {
        return Err(AttMzError::InvalidGameTitle {
            chars: invalid_chars,
        });
    }
    Ok(db_directory.join(format!("{game_title}.db")))
}

fn open_connection(db_path: &Path) -> Result<Connection> {
    let connection = Connection::open(db_path).map_err(|source| {
        AttMzError::sqlite(format!("打开数据库 {}", db_path.display()), source)
    })?;
    connection
        .pragma_update(None, "foreign_keys", "ON")
        .map_err(|source| AttMzError::sqlite("启用 SQLite 外键", source))?;
    Ok(connection)
}

fn create_static_tables(connection: &Connection) -> Result<()> {
    connection
        .execute_batch(STATIC_SCHEMA_SQL)
        .map_err(|source| AttMzError::sqlite("初始化数据库静态表", source))
}

fn serialize_json_lines(lines: &[String], context: &str) -> Result<String> {
    serde_json::to_string(lines).map_err(|source| AttMzError::Json {
        context: format!("序列化 {context}"),
        source,
    })
}

fn parse_json_string_array(text: &str, context: &str) -> Result<Vec<String>> {
    let value: serde_json::Value =
        serde_json::from_str(text).map_err(|source| AttMzError::Json {
            context: context.to_string(),
            source,
        })?;
    let Some(array) = value.as_array() else {
        return Err(AttMzError::InvalidConfig(format!(
            "{context} 必须是字符串数组"
        )));
    };
    let mut lines = Vec::new();
    for item in array {
        let Some(text) = item.as_str() else {
            return Err(AttMzError::InvalidConfig(format!(
                "{context} 只能包含字符串"
            )));
        };
        lines.push(text.to_string());
    }
    Ok(lines)
}

fn current_timestamp_text() -> String {
    let total_seconds = match SystemTime::now().duration_since(UNIX_EPOCH) {
        Ok(duration) => i64::try_from(duration.as_secs()).map_or(i64::MAX, |seconds| seconds),
        Err(_error) => 0,
    };
    let days = total_seconds.div_euclid(86_400);
    let seconds_of_day = total_seconds.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    let hour = seconds_of_day / 3_600;
    let minute = seconds_of_day % 3_600 / 60;
    let second = seconds_of_day % 60;
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}")
}

fn generate_run_id() -> String {
    match SystemTime::now().duration_since(UNIX_EPOCH) {
        Ok(duration) => format!("run-{}-{}", duration.as_secs(), duration.subsec_nanos()),
        Err(_error) => "run-0-0".to_string(),
    }
}

fn civil_from_days(days_since_epoch: i64) -> (i64, i64, i64) {
    let shifted_days = days_since_epoch + 719_468;
    let era = shifted_days.div_euclid(146_097);
    let day_of_era = shifted_days - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let mut year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_part = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_part + 2) / 5 + 1;
    let month = month_part + if month_part < 10 { 3 } else { -9 };
    if month <= 2 {
        year += 1;
    }
    (year, month, day)
}

fn write_metadata(
    connection: &Connection,
    game_title: &str,
    game_path: &Path,
    db_path: &Path,
) -> Result<()> {
    connection
        .execute(
            "INSERT OR REPLACE INTO metadata (metadata_key, game_title, game_path) VALUES (?1, ?2, ?3)",
            params![METADATA_KEY, game_title, game_path.to_string_lossy().as_ref()],
        )
        .map_err(|source| AttMzError::sqlite(format!("写入数据库元数据 {}", db_path.display()), source))?;
    Ok(())
}

fn read_metadata(connection: &Connection, db_path: &Path) -> Result<Option<(String, PathBuf)>> {
    let mut statement = connection
        .prepare("SELECT game_title, game_path FROM metadata WHERE metadata_key = ?1 LIMIT 1")
        .map_err(|source| {
            AttMzError::sqlite(format!("读取数据库元数据 {}", db_path.display()), source)
        })?;
    let result = statement.query_row(params![METADATA_KEY], |row| {
        let game_title: String = row.get(0)?;
        let game_path: String = row.get(1)?;
        Ok((game_title, PathBuf::from(game_path)))
    });
    match result {
        Ok((game_title, game_path)) => Ok(Some((game_title, game_path))),
        Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
        Err(source) => Err(AttMzError::sqlite(
            format!("读取数据库元数据 {}", db_path.display()),
            source,
        )),
    }
}

fn read_plugin_text_rules_from_connection(
    connection: &Connection,
) -> Result<Vec<PluginRuleRecord>> {
    let mut statement = connection
        .prepare(
            "SELECT plugin_index, plugin_name, plugin_hash, path_template FROM plugin_text_rules ORDER BY plugin_index, path_template",
        )
        .map_err(|source| AttMzError::sqlite("读取插件文本规则", source))?;
    let rows = statement
        .query_map([], |row| {
            let plugin_index: usize = row.get(0)?;
            let plugin_name: String = row.get(1)?;
            let plugin_hash: String = row.get(2)?;
            let path_template: String = row.get(3)?;
            Ok((plugin_index, plugin_name, plugin_hash, path_template))
        })
        .map_err(|source| AttMzError::sqlite("查询插件文本规则", source))?;
    let mut records: Vec<PluginRuleRecord> = Vec::new();
    for row in rows {
        let (plugin_index, plugin_name, plugin_hash, path_template) =
            row.map_err(|source| AttMzError::sqlite("读取插件文本规则行", source))?;
        if let Some(record) = records
            .iter_mut()
            .find(|record| record.plugin_index == plugin_index)
        {
            record.path_templates.push(path_template);
        } else {
            records.push(PluginRuleRecord {
                plugin_index,
                plugin_name,
                plugin_hash,
                path_templates: vec![path_template],
            });
        }
    }
    Ok(records)
}

fn should_refresh_plugin_translation_items(
    old_rule: Option<&PluginRuleRecord>,
    new_rule: &PluginRuleRecord,
) -> bool {
    let Some(old_rule) = old_rule else {
        return false;
    };
    old_rule.plugin_hash != new_rule.plugin_hash
        || old_rule.path_templates != new_rule.path_templates
}

fn terminology_import_state_exists(connection: &Connection) -> Result<bool> {
    let mut statement = connection
        .prepare("SELECT imported FROM terminology_import_state WHERE state_key = 'terminology'")
        .map_err(|source| AttMzError::sqlite("读取术语表导入状态", source))?;
    let mut rows = statement
        .query([])
        .map_err(|source| AttMzError::sqlite("查询术语表导入状态", source))?;
    rows.next()
        .map_err(|source| AttMzError::sqlite("读取术语表导入状态行", source))
        .map(|row| row.is_some())
}

fn read_note_tag_text_rules_from_connection(
    connection: &Connection,
) -> Result<Vec<NoteTagRuleRecord>> {
    let mut statement = connection
        .prepare("SELECT file_name, tag_name FROM note_tag_text_rules ORDER BY file_name, tag_name")
        .map_err(|source| AttMzError::sqlite("读取 Note 标签文本规则", source))?;
    let rows = statement
        .query_map([], |row| {
            let file_name: String = row.get(0)?;
            let tag_name: String = row.get(1)?;
            Ok((file_name, tag_name))
        })
        .map_err(|source| AttMzError::sqlite("查询 Note 标签文本规则", source))?;
    let mut records: Vec<NoteTagRuleRecord> = Vec::new();
    for row in rows {
        let (file_name, tag_name) =
            row.map_err(|source| AttMzError::sqlite("读取 Note 标签文本规则行", source))?;
        if let Some(record) = records
            .iter_mut()
            .find(|record| record.file_name == file_name)
        {
            record.tag_names.push(tag_name);
        } else {
            records.push(NoteTagRuleRecord {
                file_name,
                tag_names: vec![tag_name],
            });
        }
    }
    Ok(records)
}

fn read_event_command_text_rules_from_connection(
    connection: &Connection,
) -> Result<Vec<EventCommandRuleRecord>> {
    let mut group_statement = connection
        .prepare(
            "SELECT group_key, command_code FROM event_command_text_rule_groups ORDER BY group_key",
        )
        .map_err(|source| AttMzError::sqlite("读取事件指令规则组", source))?;
    let group_rows = group_statement
        .query_map([], |row| {
            let group_key: String = row.get(0)?;
            let command_code: i64 = row.get(1)?;
            Ok((group_key, command_code))
        })
        .map_err(|source| AttMzError::sqlite("查询事件指令规则组", source))?;
    let mut groups = Vec::new();
    for row in group_rows {
        groups.push(row.map_err(|source| AttMzError::sqlite("读取事件指令规则组行", source))?);
    }

    let mut filters_by_group: HashMap<String, Vec<EventCommandParameterFilter>> = HashMap::new();
    let mut filter_statement = connection
        .prepare(
            "SELECT group_key, parameter_index, parameter_value FROM event_command_text_rule_filters ORDER BY group_key, parameter_index",
        )
        .map_err(|source| AttMzError::sqlite("读取事件指令参数过滤规则", source))?;
    let filter_rows = filter_statement
        .query_map([], |row| {
            let group_key: String = row.get(0)?;
            let index: usize = row.get(1)?;
            let value: String = row.get(2)?;
            Ok((group_key, EventCommandParameterFilter { index, value }))
        })
        .map_err(|source| AttMzError::sqlite("查询事件指令参数过滤规则", source))?;
    for row in filter_rows {
        let (group_key, parameter_filter) =
            row.map_err(|source| AttMzError::sqlite("读取事件指令参数过滤规则行", source))?;
        filters_by_group
            .entry(group_key)
            .or_default()
            .push(parameter_filter);
    }

    let mut paths_by_group: HashMap<String, Vec<String>> = HashMap::new();
    let mut path_statement = connection
        .prepare(
            "SELECT group_key, path_template FROM event_command_text_rule_paths ORDER BY group_key, path_template",
        )
        .map_err(|source| AttMzError::sqlite("读取事件指令路径规则", source))?;
    let path_rows = path_statement
        .query_map([], |row| {
            let group_key: String = row.get(0)?;
            let path_template: String = row.get(1)?;
            Ok((group_key, path_template))
        })
        .map_err(|source| AttMzError::sqlite("查询事件指令路径规则", source))?;
    for row in path_rows {
        let (group_key, path_template) =
            row.map_err(|source| AttMzError::sqlite("读取事件指令路径规则行", source))?;
        paths_by_group
            .entry(group_key)
            .or_default()
            .push(path_template);
    }

    let mut records = Vec::new();
    for (group_key, command_code) in groups {
        records.push(EventCommandRuleRecord {
            command_code,
            parameter_filters: filters_by_group.remove(&group_key).unwrap_or_default(),
            path_templates: paths_by_group.remove(&group_key).unwrap_or_default(),
        });
    }
    Ok(records)
}

const STATIC_SCHEMA_SQL: &str = r#"
CREATE TABLE IF NOT EXISTS translation_items (
    location_path      TEXT PRIMARY KEY,
    item_type          TEXT NOT NULL,
    role               TEXT,
    original_lines     TEXT NOT NULL,
    source_line_paths  TEXT NOT NULL,
    translation_lines  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metadata (
    metadata_key TEXT PRIMARY KEY,
    game_title   TEXT NOT NULL,
    game_path    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plugin_text_rules (
    plugin_index  INTEGER NOT NULL,
    plugin_name   TEXT NOT NULL,
    plugin_hash   TEXT NOT NULL,
    path_template TEXT NOT NULL,
    PRIMARY KEY (plugin_index, path_template)
);
CREATE TABLE IF NOT EXISTS note_tag_text_rules (
    file_name TEXT NOT NULL,
    tag_name  TEXT NOT NULL,
    PRIMARY KEY (file_name, tag_name)
);
CREATE TABLE IF NOT EXISTS event_command_text_rule_groups (
    group_key    TEXT PRIMARY KEY,
    command_code INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS event_command_text_rule_filters (
    group_key       TEXT NOT NULL,
    parameter_index INTEGER NOT NULL,
    parameter_value TEXT NOT NULL,
    PRIMARY KEY (group_key, parameter_index),
    FOREIGN KEY (group_key) REFERENCES event_command_text_rule_groups(group_key) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS event_command_text_rule_paths (
    group_key     TEXT NOT NULL,
    path_template TEXT NOT NULL,
    PRIMARY KEY (group_key, path_template),
    FOREIGN KEY (group_key) REFERENCES event_command_text_rule_groups(group_key) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS terminology_terms (
    category        TEXT NOT NULL,
    source_text     TEXT NOT NULL,
    translated_text TEXT NOT NULL,
    PRIMARY KEY (category, source_text)
);
CREATE TABLE IF NOT EXISTS terminology_glossary_terms (
    source_text     TEXT PRIMARY KEY,
    translated_text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS terminology_import_state (
    state_key TEXT PRIMARY KEY,
    imported  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS placeholder_rules (
    pattern_text         TEXT PRIMARY KEY,
    placeholder_template TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS japanese_residual_rules (
    location_path TEXT PRIMARY KEY,
    allowed_terms TEXT NOT NULL,
    reason        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS font_replacement_records (
    file_name             TEXT NOT NULL,
    value_path            TEXT NOT NULL,
    original_text         TEXT NOT NULL,
    replaced_text         TEXT NOT NULL,
    replacement_font_name TEXT NOT NULL,
    PRIMARY KEY (file_name, value_path)
);
CREATE TABLE IF NOT EXISTS translation_runs (
    run_id              TEXT PRIMARY KEY,
    status              TEXT NOT NULL,
    total_extracted     INTEGER NOT NULL,
    pending_count       INTEGER NOT NULL,
    deduplicated_count  INTEGER NOT NULL,
    batch_count         INTEGER NOT NULL,
    success_count       INTEGER NOT NULL,
    quality_error_count INTEGER NOT NULL,
    llm_failure_count   INTEGER NOT NULL,
    started_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    finished_at         TEXT,
    stop_reason         TEXT NOT NULL,
    last_error          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_failures (
    failure_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL,
    category      TEXT NOT NULL,
    error_type    TEXT NOT NULL,
    error_message TEXT NOT NULL,
    retryable     INTEGER NOT NULL,
    attempt_count INTEGER NOT NULL,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES translation_runs(run_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS translation_quality_errors (
    run_id            TEXT NOT NULL,
    location_path     TEXT NOT NULL,
    item_type         TEXT NOT NULL,
    role              TEXT,
    original_lines    TEXT NOT NULL,
    translation_lines TEXT NOT NULL,
    error_type        TEXT NOT NULL,
    error_detail      TEXT NOT NULL,
    model_response    TEXT NOT NULL,
    PRIMARY KEY (run_id, location_path),
    FOREIGN KEY (run_id) REFERENCES translation_runs(run_id) ON DELETE CASCADE
);
"#;

#[cfg(test)]
mod tests {
    use std::fs;

    use super::*;

    fn write_json(path: &Path, text: &str) {
        fs::write(path, text).expect("测试 JSON 应写入成功");
    }

    fn create_minimal_game(root: &Path, title: &str) -> PathBuf {
        let game = root.join("game");
        fs::create_dir_all(game.join("data")).expect("data 目录应创建成功");
        fs::create_dir_all(game.join("js")).expect("js 目录应创建成功");
        write_json(
            &game.join("package.json"),
            &format!(r#"{{"window":{{"title":"{title}"}}}}"#),
        );
        write_json(&game.join("data/System.json"), "{}");
        write_json(&game.join("data/CommonEvents.json"), "[]");
        write_json(&game.join("data/Troops.json"), "[]");
        fs::write(game.join("js/plugins.js"), "var $plugins = [];\n")
            .expect("plugins.js 应写入成功");
        game
    }

    #[test]
    fn registry_registers_and_lists_game() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        let game = create_minimal_game(temp.path(), "テストゲーム");

        let registry = GameRegistry::new(temp.path().join("db"));
        let record = registry.register_game(&game).expect("游戏应注册成功");
        assert_eq!(record.game_title, "テストゲーム");

        let games = registry.list_games().expect("游戏列表应读取成功");
        assert_eq!(games.len(), 1);
        assert_eq!(games[0].game_title, "テストゲーム");

        let rules = vec![PlaceholderRule {
            pattern_text: r"\\F\[[^\]]+\]".to_string(),
            placeholder_template: "[CUSTOM_FACE_{index}]".to_string(),
        }];
        let imported_count = registry
            .replace_placeholder_rules("テストゲーム", &rules)
            .expect("占位符规则应写入成功");
        assert_eq!(imported_count, 1);
        assert_eq!(
            registry
                .read_placeholder_rules("テストゲーム")
                .expect("占位符规则应读取成功"),
            rules
        );

        let residual_rules = vec![JapaneseResidualRuleRecord {
            location_path: "CommonEvents.json/1/0".to_string(),
            allowed_terms: vec!["こんにちは".to_string()],
            reason: "proper_noun".to_string(),
        }];
        registry
            .replace_japanese_residual_rules("テストゲーム", &residual_rules)
            .expect("日文残留例外规则应写入成功");
        assert_eq!(
            registry
                .read_japanese_residual_rules("テストゲーム")
                .expect("日文残留例外规则应读取成功"),
            residual_rules
        );
    }

    #[test]
    fn plugin_rule_replace_cleans_stale_plugin_translations() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        let game = create_minimal_game(temp.path(), "插件规则测试");
        let registry = GameRegistry::new(temp.path().join("db"));
        let record = registry.register_game(&game).expect("游戏应注册成功");

        let initial_rules = vec![PluginRuleRecord {
            plugin_index: 0,
            plugin_name: "MessagePlugin".to_string(),
            plugin_hash: "old_hash".to_string(),
            path_templates: vec!["$['parameters']['Message']".to_string()],
        }];
        let initial_result = registry
            .replace_plugin_text_rules("插件规则测试", &initial_rules)
            .expect("插件规则应写入成功");
        assert_eq!(initial_result.deleted_translation_items, 0);

        let connection = open_connection(&record.db_path).expect("数据库应打开成功");
        connection
            .execute(
                "INSERT INTO translation_items (location_path, item_type, role, original_lines, source_line_paths, translation_lines) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![
                    "plugins.js/0/Message",
                    "plugin_text",
                    Option::<String>::None,
                    "[\"本文\"]",
                    "[\"plugins.js/0/Message\"]",
                    "[\"正文\"]",
                ],
            )
            .expect("插件译文记录应写入成功");
        connection
            .execute(
                "INSERT INTO translation_items (location_path, item_type, role, original_lines, source_line_paths, translation_lines) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![
                    "Map001.json/0",
                    "dialogue",
                    Option::<String>::None,
                    "[\"本文\"]",
                    "[\"Map001.json/0\"]",
                    "[\"正文\"]",
                ],
            )
            .expect("非插件译文记录应写入成功");
        drop(connection);

        let changed_rules = vec![PluginRuleRecord {
            plugin_index: 0,
            plugin_name: "MessagePlugin".to_string(),
            plugin_hash: "new_hash".to_string(),
            path_templates: vec!["$['parameters']['Message']".to_string()],
        }];
        let changed_result = registry
            .replace_plugin_text_rules("插件规则测试", &changed_rules)
            .expect("插件规则应替换成功");
        assert_eq!(changed_result.deleted_translation_items, 1);
        assert_eq!(
            registry
                .read_plugin_text_rules("插件规则测试")
                .expect("插件规则应读取成功"),
            changed_rules
        );

        let connection = open_connection(&record.db_path).expect("数据库应重新打开成功");
        let remaining_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM translation_items", [], |row| {
                row.get(0)
            })
            .expect("剩余译文数量应读取成功");
        assert_eq!(remaining_count, 1);
    }

    #[test]
    fn event_command_rule_replace_cleans_stale_command_translations() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        let game = create_minimal_game(temp.path(), "事件规则测试");
        let registry = GameRegistry::new(temp.path().join("db"));
        let record = registry.register_game(&game).expect("游戏应注册成功");
        let command_snapshots = vec![EventCommandSnapshot {
            location_path: "CommonEvents.json/1/4".to_string(),
            display_name: "CommonEvents.json".to_string(),
            code: 357,
            parameters: serde_json::json!(["TestPlugin", "Show", 0, {"message": "本文"}]),
        }];

        let initial_rules = vec![EventCommandRuleRecord {
            command_code: 357,
            parameter_filters: vec![EventCommandParameterFilter {
                index: 0,
                value: "TestPlugin".to_string(),
            }],
            path_templates: vec!["$['parameters'][3]['message']".to_string()],
        }];
        let initial_result = registry
            .replace_event_command_text_rules("事件规则测试", &initial_rules, &command_snapshots)
            .expect("事件指令规则应写入成功");
        assert_eq!(initial_result.deleted_translation_items, 0);

        let connection = open_connection(&record.db_path).expect("数据库应打开成功");
        connection
            .execute(
                "INSERT INTO translation_items (location_path, item_type, role, original_lines, source_line_paths, translation_lines) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![
                    "CommonEvents.json/1/4/parameters/3/message",
                    "short_text",
                    Option::<String>::None,
                    "[\"本文\"]",
                    "[\"CommonEvents.json/1/4/parameters/3/message\"]",
                    "[\"正文\"]",
                ],
            )
            .expect("事件指令译文记录应写入成功");
        connection
            .execute(
                "INSERT INTO translation_items (location_path, item_type, role, original_lines, source_line_paths, translation_lines) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![
                    "Map001.json/0",
                    "dialogue",
                    Option::<String>::None,
                    "[\"本文\"]",
                    "[\"Map001.json/0\"]",
                    "[\"正文\"]",
                ],
            )
            .expect("非事件指令译文记录应写入成功");
        drop(connection);

        let changed_rules = vec![EventCommandRuleRecord {
            command_code: 357,
            parameter_filters: vec![EventCommandParameterFilter {
                index: 0,
                value: "TestPlugin".to_string(),
            }],
            path_templates: vec!["$['parameters'][3]['title']".to_string()],
        }];
        let changed_result = registry
            .replace_event_command_text_rules("事件规则测试", &changed_rules, &command_snapshots)
            .expect("事件指令规则应替换成功");
        assert_eq!(changed_result.deleted_translation_items, 1);
        assert_eq!(
            registry
                .read_event_command_text_rules("事件规则测试")
                .expect("事件指令规则应读取成功"),
            changed_rules
        );

        let connection = open_connection(&record.db_path).expect("数据库应重新打开成功");
        let remaining_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM translation_items", [], |row| {
                row.get(0)
            })
            .expect("剩余译文数量应读取成功");
        assert_eq!(remaining_count, 1);
    }

    #[test]
    fn note_tag_rule_replace_cleans_removed_note_translations() {
        let temp = tempfile::tempdir().expect("临时目录应创建成功");
        let game = create_minimal_game(temp.path(), "Note规则测试");
        let registry = GameRegistry::new(temp.path().join("db"));
        let record = registry.register_game(&game).expect("游戏应注册成功");
        let data_files = BTreeMap::from([(
            "Items.json".to_string(),
            serde_json::json!([
                null,
                {
                    "id": 1,
                    "note": "<拡張説明:一行目>\n<ExtendDesc:別説明>"
                }
            ]),
        )]);

        let initial_rules = vec![NoteTagRuleRecord {
            file_name: "Items.json".to_string(),
            tag_names: vec!["拡張説明".to_string(), "ExtendDesc".to_string()],
        }];
        let initial_result = registry
            .replace_note_tag_text_rules(
                "Note规则测试",
                &initial_rules,
                &data_files,
                crate::config::DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN,
            )
            .expect("Note 标签规则应写入成功");
        assert_eq!(initial_result.deleted_translation_items, 0);

        let connection = open_connection(&record.db_path).expect("数据库应打开成功");
        connection
            .execute(
                "INSERT INTO translation_items (location_path, item_type, role, original_lines, source_line_paths, translation_lines) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![
                    "Items.json/1/note/ExtendDesc",
                    "short_text",
                    Option::<String>::None,
                    "[\"別説明\"]",
                    "[\"Items.json/1/note/ExtendDesc\"]",
                    "[\"别说明\"]",
                ],
            )
            .expect("Note 标签译文记录应写入成功");
        connection
            .execute(
                "INSERT INTO translation_items (location_path, item_type, role, original_lines, source_line_paths, translation_lines) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![
                    "Items.json/1/description",
                    "short_text",
                    Option::<String>::None,
                    "[\"説明\"]",
                    "[\"Items.json/1/description\"]",
                    "[\"说明\"]",
                ],
            )
            .expect("普通译文记录应写入成功");
        drop(connection);

        let changed_rules = vec![NoteTagRuleRecord {
            file_name: "Items.json".to_string(),
            tag_names: vec!["拡張説明".to_string()],
        }];
        let changed_result = registry
            .replace_note_tag_text_rules(
                "Note规则测试",
                &changed_rules,
                &data_files,
                crate::config::DEFAULT_SOURCE_TEXT_REQUIRED_PATTERN,
            )
            .expect("Note 标签规则应替换成功");
        assert_eq!(changed_result.deleted_translation_items, 1);
        assert_eq!(
            registry
                .read_note_tag_text_rules("Note规则测试")
                .expect("Note 标签规则应读取成功"),
            changed_rules
        );

        let connection = open_connection(&record.db_path).expect("数据库应重新打开成功");
        let remaining_count: i64 = connection
            .query_row("SELECT COUNT(*) FROM translation_items", [], |row| {
                row.get(0)
            })
            .expect("剩余译文数量应读取成功");
        assert_eq!(remaining_count, 1);
    }
}
