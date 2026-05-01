"""多游戏数据库管理器使用的 SQL 语句模块。"""

TRANSLATION_TABLE_NAME = "translation_items"
METADATA_TABLE_NAME = "metadata"
PLUGIN_TEXT_RULES_TABLE_NAME = "plugin_text_rules"
EVENT_COMMAND_TEXT_RULE_GROUPS_TABLE_NAME = "event_command_text_rule_groups"
EVENT_COMMAND_TEXT_RULE_FILTERS_TABLE_NAME = "event_command_text_rule_filters"
EVENT_COMMAND_TEXT_RULE_PATHS_TABLE_NAME = "event_command_text_rule_paths"
NAME_CONTEXT_TERMS_TABLE_NAME = "name_context_terms"
METADATA_KEY = "current_game"

CREATE_TRANSLATION_TABLE = f"""
--sql
    CREATE TABLE IF NOT EXISTS [{TRANSLATION_TABLE_NAME}] (
        location_path      TEXT PRIMARY KEY,
        item_type          TEXT NOT NULL,
        role               TEXT,
        original_lines     TEXT NOT NULL,
        source_line_paths  TEXT NOT NULL,
        translation_lines  TEXT NOT NULL
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
        error_detail      TEXT NOT NULL,
        model_response    TEXT NOT NULL
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

CREATE_PLUGIN_TEXT_RULES_TABLE = f"""
--sql
    CREATE TABLE IF NOT EXISTS [{PLUGIN_TEXT_RULES_TABLE_NAME}] (
        plugin_index  INTEGER NOT NULL,
        plugin_name   TEXT NOT NULL,
        plugin_hash   TEXT NOT NULL,
        path_template TEXT NOT NULL,
        PRIMARY KEY (plugin_index, path_template)
    )
;
"""

CREATE_EVENT_COMMAND_TEXT_RULE_GROUPS_TABLE = f"""
--sql
    CREATE TABLE IF NOT EXISTS [{EVENT_COMMAND_TEXT_RULE_GROUPS_TABLE_NAME}] (
        group_key    TEXT PRIMARY KEY,
        command_code INTEGER NOT NULL
    )
;
"""

CREATE_EVENT_COMMAND_TEXT_RULE_FILTERS_TABLE = f"""
--sql
    CREATE TABLE IF NOT EXISTS [{EVENT_COMMAND_TEXT_RULE_FILTERS_TABLE_NAME}] (
        group_key       TEXT NOT NULL,
        parameter_index INTEGER NOT NULL,
        parameter_value TEXT NOT NULL,
        PRIMARY KEY (group_key, parameter_index),
        FOREIGN KEY (group_key) REFERENCES [{EVENT_COMMAND_TEXT_RULE_GROUPS_TABLE_NAME}](group_key) ON DELETE CASCADE
    )
;
"""

CREATE_EVENT_COMMAND_TEXT_RULE_PATHS_TABLE = f"""
--sql
    CREATE TABLE IF NOT EXISTS [{EVENT_COMMAND_TEXT_RULE_PATHS_TABLE_NAME}] (
        group_key     TEXT NOT NULL,
        path_template TEXT NOT NULL,
        PRIMARY KEY (group_key, path_template),
        FOREIGN KEY (group_key) REFERENCES [{EVENT_COMMAND_TEXT_RULE_GROUPS_TABLE_NAME}](group_key) ON DELETE CASCADE
    )
;
"""

CREATE_NAME_CONTEXT_TERMS_TABLE = f"""
--sql
    CREATE TABLE IF NOT EXISTS [{NAME_CONTEXT_TERMS_TABLE_NAME}] (
        kind            TEXT NOT NULL,
        source_text     TEXT NOT NULL,
        translated_text TEXT NOT NULL,
        PRIMARY KEY (kind, source_text)
    )
;
"""

INSERT_TRANSLATION = f"""
--sql
    INSERT OR REPLACE INTO [{TRANSLATION_TABLE_NAME}]
    (location_path, item_type, role, original_lines, source_line_paths, translation_lines)
    VALUES (?, ?, ?, ?, ?, ?)
;
"""

INSERT_ERROR = """
--sql
    INSERT OR REPLACE INTO [{table_name}]
    (location_path, item_type, role, original_lines, translation_lines, error_type, error_detail, model_response)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
;
"""

UPSERT_METADATA = f"""
--sql
    INSERT OR REPLACE INTO [{METADATA_TABLE_NAME}]
    (metadata_key, game_title, game_path)
    VALUES (?, ?, ?)
;
"""

INSERT_PLUGIN_TEXT_RULE = f"""
--sql
    INSERT OR REPLACE INTO [{PLUGIN_TEXT_RULES_TABLE_NAME}]
    (plugin_index, plugin_name, plugin_hash, path_template)
    VALUES (?, ?, ?, ?)
;
"""

INSERT_EVENT_COMMAND_TEXT_RULE_GROUP = f"""
--sql
    INSERT OR REPLACE INTO [{EVENT_COMMAND_TEXT_RULE_GROUPS_TABLE_NAME}]
    (group_key, command_code)
    VALUES (?, ?)
;
"""

INSERT_EVENT_COMMAND_TEXT_RULE_FILTER = f"""
--sql
    INSERT OR REPLACE INTO [{EVENT_COMMAND_TEXT_RULE_FILTERS_TABLE_NAME}]
    (group_key, parameter_index, parameter_value)
    VALUES (?, ?, ?)
;
"""

INSERT_EVENT_COMMAND_TEXT_RULE_PATH = f"""
--sql
    INSERT OR REPLACE INTO [{EVENT_COMMAND_TEXT_RULE_PATHS_TABLE_NAME}]
    (group_key, path_template)
    VALUES (?, ?)
;
"""

INSERT_NAME_CONTEXT_TERM = f"""
--sql
    INSERT OR REPLACE INTO [{NAME_CONTEXT_TERMS_TABLE_NAME}]
    (kind, source_text, translated_text)
    VALUES (?, ?, ?)
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
    SELECT location_path, item_type, role, original_lines, source_line_paths, translation_lines
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

SELECT_PLUGIN_TEXT_RULES = f"""
--sql
    SELECT plugin_index, plugin_name, plugin_hash, path_template
    FROM [{PLUGIN_TEXT_RULES_TABLE_NAME}]
    ORDER BY plugin_index, path_template
;
"""

SELECT_EVENT_COMMAND_TEXT_RULE_GROUPS = f"""
--sql
    SELECT group_key, command_code
    FROM [{EVENT_COMMAND_TEXT_RULE_GROUPS_TABLE_NAME}]
    ORDER BY group_key
;
"""

SELECT_EVENT_COMMAND_TEXT_RULE_FILTERS = f"""
--sql
    SELECT group_key, parameter_index, parameter_value
    FROM [{EVENT_COMMAND_TEXT_RULE_FILTERS_TABLE_NAME}]
    ORDER BY group_key, parameter_index
;
"""

SELECT_EVENT_COMMAND_TEXT_RULE_PATHS = f"""
--sql
    SELECT group_key, path_template
    FROM [{EVENT_COMMAND_TEXT_RULE_PATHS_TABLE_NAME}]
    ORDER BY group_key, path_template
;
"""

SELECT_NAME_CONTEXT_TERMS = f"""
--sql
    SELECT kind, source_text, translated_text
    FROM [{NAME_CONTEXT_TERMS_TABLE_NAME}]
    ORDER BY kind, source_text
;
"""

DELETE_ALL_PLUGIN_TEXT_RULES = f"""
--sql
    DELETE FROM [{PLUGIN_TEXT_RULES_TABLE_NAME}]
;
"""

DELETE_ALL_EVENT_COMMAND_TEXT_RULE_PATHS = f"""
--sql
    DELETE FROM [{EVENT_COMMAND_TEXT_RULE_PATHS_TABLE_NAME}]
;
"""

DELETE_ALL_EVENT_COMMAND_TEXT_RULE_FILTERS = f"""
--sql
    DELETE FROM [{EVENT_COMMAND_TEXT_RULE_FILTERS_TABLE_NAME}]
;
"""

DELETE_ALL_EVENT_COMMAND_TEXT_RULE_GROUPS = f"""
--sql
    DELETE FROM [{EVENT_COMMAND_TEXT_RULE_GROUPS_TABLE_NAME}]
;
"""

DELETE_ALL_NAME_CONTEXT_TERMS = f"""
--sql
    DELETE FROM [{NAME_CONTEXT_TERMS_TABLE_NAME}]
;
"""

DELETE_TRANSLATION_ITEMS_BY_PREFIX = f"""
--sql
    DELETE FROM [{TRANSLATION_TABLE_NAME}]
    WHERE location_path LIKE ?
;
"""

DELETE_TRANSLATION_ITEM_BY_PATH = f"""
--sql
    DELETE FROM [{TRANSLATION_TABLE_NAME}]
    WHERE location_path = ?
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
    "CREATE_EVENT_COMMAND_TEXT_RULE_FILTERS_TABLE",
    "CREATE_EVENT_COMMAND_TEXT_RULE_GROUPS_TABLE",
    "CREATE_EVENT_COMMAND_TEXT_RULE_PATHS_TABLE",
    "CREATE_METADATA_TABLE",
    "CREATE_NAME_CONTEXT_TERMS_TABLE",
    "CREATE_PLUGIN_TEXT_RULES_TABLE",
    "CREATE_TRANSLATION_TABLE",
    "DELETE_ALL_EVENT_COMMAND_TEXT_RULE_FILTERS",
    "DELETE_ALL_EVENT_COMMAND_TEXT_RULE_GROUPS",
    "DELETE_ALL_EVENT_COMMAND_TEXT_RULE_PATHS",
    "DELETE_ALL_NAME_CONTEXT_TERMS",
    "DELETE_ALL_PLUGIN_TEXT_RULES",
    "DELETE_TRANSLATION_ITEM_BY_PATH",
    "DELETE_TRANSLATION_ITEMS_BY_PREFIX",
    "DROP_TABLE",
    "EVENT_COMMAND_TEXT_RULE_FILTERS_TABLE_NAME",
    "EVENT_COMMAND_TEXT_RULE_GROUPS_TABLE_NAME",
    "EVENT_COMMAND_TEXT_RULE_PATHS_TABLE_NAME",
    "INSERT_ERROR",
    "INSERT_EVENT_COMMAND_TEXT_RULE_FILTER",
    "INSERT_EVENT_COMMAND_TEXT_RULE_GROUP",
    "INSERT_EVENT_COMMAND_TEXT_RULE_PATH",
    "INSERT_NAME_CONTEXT_TERM",
    "INSERT_PLUGIN_TEXT_RULE",
    "INSERT_TRANSLATION",
    "METADATA_KEY",
    "METADATA_TABLE_NAME",
    "NAME_CONTEXT_TERMS_TABLE_NAME",
    "PLUGIN_TEXT_RULES_TABLE_NAME",
    "SELECT_ALL",
    "SELECT_EVENT_COMMAND_TEXT_RULE_FILTERS",
    "SELECT_EVENT_COMMAND_TEXT_RULE_GROUPS",
    "SELECT_EVENT_COMMAND_TEXT_RULE_PATHS",
    "SELECT_METADATA",
    "SELECT_NAME_CONTEXT_TERMS",
    "SELECT_PLUGIN_TEXT_RULES",
    "SELECT_TABLE_NAMES_BY_PREFIX",
    "SELECT_TRANSLATED_ITEMS",
    "SELECT_TRANSLATION_PATHS",
    "TRANSLATION_TABLE_NAME",
    "UPSERT_METADATA",
]
