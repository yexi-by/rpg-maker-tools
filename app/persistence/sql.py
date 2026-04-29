"""
多游戏数据库管理器使用的 SQL 语句模块。

当前 schema 只保留核心 CLI 半成品需要的数据：游戏元数据、正文译文、错误表、
插件文本分析状态与插件规则。旧术语表相关表已经删除。
"""

TRANSLATION_TABLE_NAME = "translation_items"
METADATA_TABLE_NAME = "metadata"
PLUGIN_TEXT_ANALYSIS_STATE_TABLE_NAME = "plugin_text_analysis_state"
PLUGIN_TEXT_RULES_TABLE_NAME = "plugin_text_rules"
METADATA_KEY = "current_game"

CREATE_TRANSLATION_TABLE = f"""
--sql
    CREATE TABLE IF NOT EXISTS [{TRANSLATION_TABLE_NAME}] (
        location_path     TEXT PRIMARY KEY,
        item_type         TEXT NOT NULL,
        role              TEXT,
        original_lines    TEXT NOT NULL,
        translation_lines TEXT NOT NULL
    )
;
"""

CREATE_ERROR_TABLE = """
--sql
    CREATE TABLE IF NOT EXISTS [{table_name}] (
        location_path     TEXT PRIMARY KEY,
        item_type         TEXT NOT NULL,
        role              TEXT,
        original_lines    TEXT NOT NULL,
        translation_lines TEXT NOT NULL,
        error_type        TEXT NOT NULL,
        error_detail      TEXT NOT NULL
    )
;
"""

CREATE_METADATA_TABLE = f"""
--sql
    CREATE TABLE IF NOT EXISTS [{METADATA_TABLE_NAME}] (
        metadata_key TEXT PRIMARY KEY,
        game_title   TEXT NOT NULL,
        game_path    TEXT NOT NULL
    )
;
"""

CREATE_PLUGIN_TEXT_ANALYSIS_STATE_TABLE = f"""
--sql
    CREATE TABLE IF NOT EXISTS [{PLUGIN_TEXT_ANALYSIS_STATE_TABLE_NAME}] (
        state_key          TEXT PRIMARY KEY,
        plugins_file_hash  TEXT NOT NULL,
        prompt_hash        TEXT NOT NULL,
        total_plugins      INTEGER NOT NULL,
        success_plugins    INTEGER NOT NULL,
        failed_plugins     INTEGER NOT NULL,
        updated_at         TEXT NOT NULL
    )
;
"""

CREATE_PLUGIN_TEXT_RULES_TABLE = f"""
--sql
    CREATE TABLE IF NOT EXISTS [{PLUGIN_TEXT_RULES_TABLE_NAME}] (
        plugin_index         INTEGER PRIMARY KEY,
        plugin_name          TEXT NOT NULL,
        plugin_hash          TEXT NOT NULL,
        prompt_hash          TEXT NOT NULL,
        status               TEXT NOT NULL,
        plugin_reason        TEXT NOT NULL,
        translate_rules_json TEXT NOT NULL,
        last_error           TEXT,
        updated_at           TEXT NOT NULL
    )
;
"""

INSERT_TRANSLATION = f"""
--sql
    INSERT OR REPLACE INTO [{TRANSLATION_TABLE_NAME}]
    (location_path, item_type, role, original_lines, translation_lines)
    VALUES (?, ?, ?, ?, ?)
;
"""

INSERT_ERROR = """
--sql
    INSERT OR REPLACE INTO [{table_name}]
    (location_path, item_type, role, original_lines, translation_lines, error_type, error_detail)
    VALUES (?, ?, ?, ?, ?, ?, ?)
;
"""

UPSERT_METADATA = f"""
--sql
    INSERT OR REPLACE INTO [{METADATA_TABLE_NAME}]
    (metadata_key, game_title, game_path)
    VALUES (?, ?, ?)
;
"""

UPSERT_PLUGIN_TEXT_ANALYSIS_STATE = f"""
--sql
    INSERT OR REPLACE INTO [{PLUGIN_TEXT_ANALYSIS_STATE_TABLE_NAME}]
    (state_key, plugins_file_hash, prompt_hash, total_plugins, success_plugins, failed_plugins, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
;
"""

UPSERT_PLUGIN_TEXT_RULE = f"""
--sql
    INSERT OR REPLACE INTO [{PLUGIN_TEXT_RULES_TABLE_NAME}]
    (plugin_index, plugin_name, plugin_hash, prompt_hash, status, plugin_reason, translate_rules_json, last_error, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
;
"""

SELECT_ALL = """
--sql
    SELECT * FROM [{table_name}]
;
"""

SELECT_TRANSLATION_PATHS = f"""
--sql
    SELECT location_path
    FROM [{TRANSLATION_TABLE_NAME}]
;
"""

SELECT_TRANSLATED_ITEMS = f"""
--sql
    SELECT location_path, item_type, role, original_lines, translation_lines
    FROM [{TRANSLATION_TABLE_NAME}]
    ORDER BY location_path
;
"""

SELECT_TABLE_NAMES_BY_PREFIX = """
--sql
    SELECT name
    FROM sqlite_master
    WHERE type = 'table' AND name LIKE ?
    ORDER BY name
;
"""

SELECT_METADATA = f"""
--sql
    SELECT game_title, game_path
    FROM [{METADATA_TABLE_NAME}]
    WHERE metadata_key = ?
    LIMIT 1
;
"""

SELECT_PLUGIN_TEXT_ANALYSIS_STATE = f"""
--sql
    SELECT plugins_file_hash, prompt_hash, total_plugins, success_plugins, failed_plugins, updated_at
    FROM [{PLUGIN_TEXT_ANALYSIS_STATE_TABLE_NAME}]
    WHERE state_key = ?
    LIMIT 1
;
"""

SELECT_PLUGIN_TEXT_RULES = f"""
--sql
    SELECT plugin_index, plugin_name, plugin_hash, prompt_hash, status, plugin_reason, translate_rules_json, last_error, updated_at
    FROM [{PLUGIN_TEXT_RULES_TABLE_NAME}]
    ORDER BY plugin_index
;
"""

DELETE_TRANSLATION_ITEMS_BY_PREFIX = f"""
--sql
    DELETE FROM [{TRANSLATION_TABLE_NAME}]
    WHERE location_path LIKE ?
;
"""

DROP_TABLE = """
--sql
    DROP TABLE IF EXISTS [{table_name}]
;
"""

CHECK_CONNECTION_READABLE = """
--sql
    SELECT 1
;
"""

__all__: list[str] = [
    "CHECK_CONNECTION_READABLE",
    "CREATE_ERROR_TABLE",
    "CREATE_METADATA_TABLE",
    "CREATE_PLUGIN_TEXT_ANALYSIS_STATE_TABLE",
    "CREATE_PLUGIN_TEXT_RULES_TABLE",
    "CREATE_TRANSLATION_TABLE",
    "DELETE_TRANSLATION_ITEMS_BY_PREFIX",
    "DROP_TABLE",
    "INSERT_ERROR",
    "INSERT_TRANSLATION",
    "METADATA_KEY",
    "METADATA_TABLE_NAME",
    "PLUGIN_TEXT_ANALYSIS_STATE_TABLE_NAME",
    "PLUGIN_TEXT_RULES_TABLE_NAME",
    "SELECT_ALL",
    "SELECT_METADATA",
    "SELECT_PLUGIN_TEXT_ANALYSIS_STATE",
    "SELECT_PLUGIN_TEXT_RULES",
    "SELECT_TABLE_NAMES_BY_PREFIX",
    "SELECT_TRANSLATED_ITEMS",
    "SELECT_TRANSLATION_PATHS",
    "TRANSLATION_TABLE_NAME",
    "UPSERT_METADATA",
    "UPSERT_PLUGIN_TEXT_ANALYSIS_STATE",
    "UPSERT_PLUGIN_TEXT_RULE",
]
