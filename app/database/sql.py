"""
多游戏数据库管理器使用的 SQL 语句模块。

本模块集中定义静态表结构和通用查询语句，避免 SQL 字符串散落在业务层。
当前除了原有的术语、正文和错误表，还新增了插件文本分析结果相关表。
"""

TRANSLATION_TABLE_NAME: str = "translation_items"
GLOSSARY_ROLE_TABLE_NAME: str = "glossary_roles"
GLOSSARY_PLACE_TABLE_NAME: str = "glossary_places"
GLOSSARY_STATE_TABLE_NAME: str = "glossary_state"
METADATA_TABLE_NAME: str = "metadata"
PLUGIN_TEXT_ANALYSIS_STATE_TABLE_NAME: str = "plugin_text_analysis_state"
PLUGIN_TEXT_RULES_TABLE_NAME: str = "plugin_text_rules"
METADATA_KEY: str = "current_game"

CREATE_TRANSLATION_TABLE: str = f"""
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

CREATE_ERROR_TABLE: str = """
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

CREATE_ROLE_GLOSSARY_TABLE: str = f"""
--sql
    CREATE TABLE IF NOT EXISTS [{GLOSSARY_ROLE_TABLE_NAME}] (
        name            TEXT PRIMARY KEY,
        translated_name TEXT NOT NULL,
        gender          TEXT NOT NULL
    )
;
"""

CREATE_PLACE_GLOSSARY_TABLE: str = f"""
--sql
    CREATE TABLE IF NOT EXISTS [{GLOSSARY_PLACE_TABLE_NAME}] (
        name            TEXT PRIMARY KEY,
        translated_name TEXT NOT NULL
    )
;
"""

CREATE_GLOSSARY_STATE_TABLE: str = f"""
--sql
    CREATE TABLE IF NOT EXISTS [{GLOSSARY_STATE_TABLE_NAME}] (
        state_key TEXT PRIMARY KEY,
        is_ready  INTEGER NOT NULL
    )
;
"""

CREATE_METADATA_TABLE: str = f"""
--sql
    CREATE TABLE IF NOT EXISTS [{METADATA_TABLE_NAME}] (
        metadata_key    TEXT PRIMARY KEY,
        game_title      TEXT NOT NULL,
        game_path       TEXT NOT NULL,
        source_language TEXT NOT NULL
    )
;
"""

CREATE_PLUGIN_TEXT_ANALYSIS_STATE_TABLE: str = f"""
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

CREATE_PLUGIN_TEXT_RULES_TABLE: str = f"""
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

INSERT_TRANSLATION: str = f"""
--sql
    INSERT OR REPLACE INTO [{TRANSLATION_TABLE_NAME}]
    (location_path, item_type, role, original_lines, translation_lines)
    VALUES (?, ?, ?, ?, ?)
;
"""

INSERT_ERROR: str = """
--sql
    INSERT OR REPLACE INTO [{table_name}]
    (location_path, item_type, role, original_lines, translation_lines, error_type, error_detail)
    VALUES (?, ?, ?, ?, ?, ?, ?)
;
"""

INSERT_ROLE_GLOSSARY_ITEM: str = f"""
--sql
    INSERT OR REPLACE INTO [{GLOSSARY_ROLE_TABLE_NAME}]
    (name, translated_name, gender)
    VALUES (?, ?, ?)
;
"""

INSERT_PLACE_GLOSSARY_ITEM: str = f"""
--sql
    INSERT OR REPLACE INTO [{GLOSSARY_PLACE_TABLE_NAME}]
    (name, translated_name)
    VALUES (?, ?)
;
"""

UPSERT_GLOSSARY_STATE: str = f"""
--sql
    INSERT OR REPLACE INTO [{GLOSSARY_STATE_TABLE_NAME}]
    (state_key, is_ready)
    VALUES (?, ?)
;
"""

UPSERT_METADATA: str = f"""
--sql
    INSERT OR REPLACE INTO [{METADATA_TABLE_NAME}]
    (metadata_key, game_title, game_path, source_language)
    VALUES (?, ?, ?, ?)
;
"""

UPSERT_PLUGIN_TEXT_ANALYSIS_STATE: str = f"""
--sql
    INSERT OR REPLACE INTO [{PLUGIN_TEXT_ANALYSIS_STATE_TABLE_NAME}]
    (state_key, plugins_file_hash, prompt_hash, total_plugins, success_plugins, failed_plugins, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
;
"""

UPSERT_PLUGIN_TEXT_RULE: str = f"""
--sql
    INSERT OR REPLACE INTO [{PLUGIN_TEXT_RULES_TABLE_NAME}]
    (plugin_index, plugin_name, plugin_hash, prompt_hash, status, plugin_reason, translate_rules_json, last_error, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
;
"""

UPDATE_METADATA_SOURCE_LANGUAGE: str = f"""
--sql
    UPDATE [{METADATA_TABLE_NAME}]
    SET source_language = ?
    WHERE metadata_key = ?
;
"""

SELECT_ALL: str = """
--sql
    SELECT * FROM [{table_name}]
;
"""

SELECT_ROLE_GLOSSARY_ITEMS: str = f"""
--sql
    SELECT name, translated_name, gender
    FROM [{GLOSSARY_ROLE_TABLE_NAME}]
    ORDER BY name
;
"""

SELECT_PLACE_GLOSSARY_ITEMS: str = f"""
--sql
    SELECT name, translated_name
    FROM [{GLOSSARY_PLACE_TABLE_NAME}]
    ORDER BY name
;
"""

SELECT_GLOSSARY_STATE: str = f"""
--sql
    SELECT is_ready
    FROM [{GLOSSARY_STATE_TABLE_NAME}]
    WHERE state_key = ?
    LIMIT 1
;
"""

SELECT_TRANSLATION_PATHS: str = f"""
--sql
    SELECT location_path
    FROM [{TRANSLATION_TABLE_NAME}]
;
"""

SELECT_TRANSLATED_ITEMS: str = f"""
--sql
    SELECT location_path, item_type, role, original_lines, translation_lines
    FROM [{TRANSLATION_TABLE_NAME}]
    ORDER BY location_path
;
"""

SELECT_TABLE_NAMES_BY_PREFIX: str = """
--sql
    SELECT name
    FROM sqlite_master
    WHERE type = 'table' AND name LIKE ?
    ORDER BY name
;
"""

SELECT_METADATA: str = f"""
--sql
    SELECT game_title, game_path, source_language
    FROM [{METADATA_TABLE_NAME}]
    WHERE metadata_key = ?
    LIMIT 1
;
"""

SELECT_PLUGIN_TEXT_ANALYSIS_STATE: str = f"""
--sql
    SELECT plugins_file_hash, prompt_hash, total_plugins, success_plugins, failed_plugins, updated_at
    FROM [{PLUGIN_TEXT_ANALYSIS_STATE_TABLE_NAME}]
    WHERE state_key = ?
    LIMIT 1
;
"""

SELECT_PLUGIN_TEXT_RULES: str = f"""
--sql
    SELECT plugin_index, plugin_name, plugin_hash, prompt_hash, status, plugin_reason, translate_rules_json, last_error, updated_at
    FROM [{PLUGIN_TEXT_RULES_TABLE_NAME}]
    ORDER BY plugin_index
;
"""

DELETE_ALL_ROWS: str = """
--sql
    DELETE FROM [{table_name}]
;
"""

DELETE_TRANSLATION_ITEMS_BY_PREFIX: str = f"""
--sql
    DELETE FROM [{TRANSLATION_TABLE_NAME}]
    WHERE location_path LIKE ?
;
"""

DROP_TABLE: str = """
--sql
    DROP TABLE IF EXISTS [{table_name}]
;
"""

CHECK_CONNECTION_READABLE: str = """
--sql
    SELECT 1
;
"""

__all__: list[str] = [
    "CHECK_CONNECTION_READABLE",
    "CREATE_ERROR_TABLE",
    "CREATE_GLOSSARY_STATE_TABLE",
    "CREATE_METADATA_TABLE",
    "CREATE_PLACE_GLOSSARY_TABLE",
    "CREATE_PLUGIN_TEXT_ANALYSIS_STATE_TABLE",
    "CREATE_PLUGIN_TEXT_RULES_TABLE",
    "CREATE_ROLE_GLOSSARY_TABLE",
    "CREATE_TRANSLATION_TABLE",
    "DELETE_ALL_ROWS",
    "DELETE_TRANSLATION_ITEMS_BY_PREFIX",
    "DROP_TABLE",
    "GLOSSARY_PLACE_TABLE_NAME",
    "GLOSSARY_ROLE_TABLE_NAME",
    "GLOSSARY_STATE_TABLE_NAME",
    "INSERT_ERROR",
    "INSERT_PLACE_GLOSSARY_ITEM",
    "INSERT_ROLE_GLOSSARY_ITEM",
    "INSERT_TRANSLATION",
    "METADATA_KEY",
    "METADATA_TABLE_NAME",
    "PLUGIN_TEXT_ANALYSIS_STATE_TABLE_NAME",
    "PLUGIN_TEXT_RULES_TABLE_NAME",
    "SELECT_ALL",
    "SELECT_GLOSSARY_STATE",
    "SELECT_METADATA",
    "SELECT_PLACE_GLOSSARY_ITEMS",
    "SELECT_PLUGIN_TEXT_ANALYSIS_STATE",
    "SELECT_PLUGIN_TEXT_RULES",
    "SELECT_ROLE_GLOSSARY_ITEMS",
    "SELECT_TABLE_NAMES_BY_PREFIX",
    "SELECT_TRANSLATED_ITEMS",
    "SELECT_TRANSLATION_PATHS",
    "TRANSLATION_TABLE_NAME",
    "UPDATE_METADATA_SOURCE_LANGUAGE",
    "UPSERT_GLOSSARY_STATE",
    "UPSERT_METADATA",
    "UPSERT_PLUGIN_TEXT_ANALYSIS_STATE",
    "UPSERT_PLUGIN_TEXT_RULE",
]
