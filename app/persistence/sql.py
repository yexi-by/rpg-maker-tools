"""多游戏数据库管理器使用的 SQL 语句模块。"""

TRANSLATION_TABLE_NAME = "translation_items"
METADATA_TABLE_NAME = "metadata"
PLUGIN_TEXT_RULES_TABLE_NAME = "plugin_text_rules"
NOTE_TAG_TEXT_RULES_TABLE_NAME = "note_tag_text_rules"
EVENT_COMMAND_TEXT_RULE_GROUPS_TABLE_NAME = "event_command_text_rule_groups"
EVENT_COMMAND_TEXT_RULE_FILTERS_TABLE_NAME = "event_command_text_rule_filters"
EVENT_COMMAND_TEXT_RULE_PATHS_TABLE_NAME = "event_command_text_rule_paths"
NAME_CONTEXT_TERMS_TABLE_NAME = "name_context_terms"
PLACEHOLDER_RULES_TABLE_NAME = "placeholder_rules"
JAPANESE_RESIDUAL_RULES_TABLE_NAME = "japanese_residual_rules"
TRANSLATION_RUNS_TABLE_NAME = "translation_runs"
LLM_FAILURES_TABLE_NAME = "llm_failures"
TRANSLATION_QUALITY_ERRORS_TABLE_NAME = "translation_quality_errors"
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

CREATE_PLACEHOLDER_RULES_TABLE = f"""
--sql
    CREATE TABLE IF NOT EXISTS [{PLACEHOLDER_RULES_TABLE_NAME}] (
        pattern_text         TEXT PRIMARY KEY,
        placeholder_template TEXT NOT NULL
    )
;
"""

CREATE_JAPANESE_RESIDUAL_RULES_TABLE = f"""
--sql
    CREATE TABLE IF NOT EXISTS [{JAPANESE_RESIDUAL_RULES_TABLE_NAME}] (
        location_path TEXT PRIMARY KEY,
        allowed_terms TEXT NOT NULL,
        reason        TEXT NOT NULL
    )
;
"""

CREATE_TRANSLATION_RUNS_TABLE = f"""
--sql
    CREATE TABLE IF NOT EXISTS [{TRANSLATION_RUNS_TABLE_NAME}] (
        run_id            TEXT PRIMARY KEY,
        status            TEXT NOT NULL,
        total_extracted   INTEGER NOT NULL,
        pending_count     INTEGER NOT NULL,
        deduplicated_count INTEGER NOT NULL,
        batch_count       INTEGER NOT NULL,
        success_count     INTEGER NOT NULL,
        quality_error_count INTEGER NOT NULL,
        llm_failure_count INTEGER NOT NULL,
        started_at        TEXT NOT NULL,
        updated_at        TEXT NOT NULL,
        finished_at       TEXT,
        stop_reason       TEXT NOT NULL,
        last_error        TEXT NOT NULL
    )
;
"""

CREATE_LLM_FAILURES_TABLE = f"""
--sql
    CREATE TABLE IF NOT EXISTS [{LLM_FAILURES_TABLE_NAME}] (
        failure_id      INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          TEXT NOT NULL,
        category        TEXT NOT NULL,
        error_type      TEXT NOT NULL,
        error_message   TEXT NOT NULL,
        retryable       INTEGER NOT NULL,
        attempt_count   INTEGER NOT NULL,
        created_at      TEXT NOT NULL,
        FOREIGN KEY (run_id) REFERENCES [{TRANSLATION_RUNS_TABLE_NAME}](run_id) ON DELETE CASCADE
    )
;
"""

CREATE_TRANSLATION_QUALITY_ERRORS_TABLE = f"""
--sql
    CREATE TABLE IF NOT EXISTS [{TRANSLATION_QUALITY_ERRORS_TABLE_NAME}] (
        run_id           TEXT NOT NULL,
        location_path    TEXT NOT NULL,
        item_type        TEXT NOT NULL,
        role             TEXT,
        original_lines   TEXT NOT NULL,
        translation_lines TEXT NOT NULL,
        error_type       TEXT NOT NULL,
        error_detail     TEXT NOT NULL,
        model_response   TEXT NOT NULL,
        PRIMARY KEY (run_id, location_path),
        FOREIGN KEY (run_id) REFERENCES [{TRANSLATION_RUNS_TABLE_NAME}](run_id) ON DELETE CASCADE
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

CREATE_NOTE_TAG_TEXT_RULES_TABLE = f"""
--sql
    CREATE TABLE IF NOT EXISTS [{NOTE_TAG_TEXT_RULES_TABLE_NAME}] (
        file_name TEXT NOT NULL,
        tag_name  TEXT NOT NULL,
        PRIMARY KEY (file_name, tag_name)
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

INSERT_NOTE_TAG_TEXT_RULE = f"""
--sql
    INSERT OR REPLACE INTO [{NOTE_TAG_TEXT_RULES_TABLE_NAME}]
    (file_name, tag_name)
    VALUES (?, ?)
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

INSERT_PLACEHOLDER_RULE = f"""
--sql
    INSERT OR REPLACE INTO [{PLACEHOLDER_RULES_TABLE_NAME}]
    (pattern_text, placeholder_template)
    VALUES (?, ?)
;
"""

INSERT_JAPANESE_RESIDUAL_RULE = f"""
--sql
    INSERT OR REPLACE INTO [{JAPANESE_RESIDUAL_RULES_TABLE_NAME}]
    (location_path, allowed_terms, reason)
    VALUES (?, ?, ?)
;
"""

UPSERT_TRANSLATION_RUN = f"""
--sql
    INSERT INTO [{TRANSLATION_RUNS_TABLE_NAME}]
    (
        run_id,
        status,
        total_extracted,
        pending_count,
        deduplicated_count,
        batch_count,
        success_count,
        quality_error_count,
        llm_failure_count,
        started_at,
        updated_at,
        finished_at,
        stop_reason,
        last_error
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(run_id) DO UPDATE SET
        status = excluded.status,
        total_extracted = excluded.total_extracted,
        pending_count = excluded.pending_count,
        deduplicated_count = excluded.deduplicated_count,
        batch_count = excluded.batch_count,
        success_count = excluded.success_count,
        quality_error_count = excluded.quality_error_count,
        llm_failure_count = excluded.llm_failure_count,
        started_at = excluded.started_at,
        updated_at = excluded.updated_at,
        finished_at = excluded.finished_at,
        stop_reason = excluded.stop_reason,
        last_error = excluded.last_error
;
"""

INSERT_LLM_FAILURE = f"""
--sql
    INSERT INTO [{LLM_FAILURES_TABLE_NAME}]
    (run_id, category, error_type, error_message, retryable, attempt_count, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
;
"""

INSERT_TRANSLATION_QUALITY_ERROR = f"""
--sql
    INSERT OR REPLACE INTO [{TRANSLATION_QUALITY_ERRORS_TABLE_NAME}]
    (run_id, location_path, item_type, role, original_lines, translation_lines, error_type, error_detail, model_response)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
;
"""

DELETE_ALL_TRANSLATION_QUALITY_ERRORS = f"""
--sql
    DELETE FROM [{TRANSLATION_QUALITY_ERRORS_TABLE_NAME}]
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

SELECT_NOTE_TAG_TEXT_RULES = f"""
--sql
    SELECT file_name, tag_name
    FROM [{NOTE_TAG_TEXT_RULES_TABLE_NAME}]
    ORDER BY file_name, tag_name
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

SELECT_PLACEHOLDER_RULES = f"""
--sql
    SELECT pattern_text, placeholder_template
    FROM [{PLACEHOLDER_RULES_TABLE_NAME}]
    ORDER BY pattern_text
;
"""

SELECT_JAPANESE_RESIDUAL_RULES = f"""
--sql
    SELECT location_path, allowed_terms, reason
    FROM [{JAPANESE_RESIDUAL_RULES_TABLE_NAME}]
    ORDER BY location_path
;
"""

SELECT_LATEST_TRANSLATION_RUN = f"""
--sql
    SELECT *
    FROM [{TRANSLATION_RUNS_TABLE_NAME}]
    ORDER BY started_at DESC, run_id DESC
    LIMIT 1
;
"""

SELECT_TRANSLATION_RUN = f"""
--sql
    SELECT *
    FROM [{TRANSLATION_RUNS_TABLE_NAME}]
    WHERE run_id = ?
    LIMIT 1
;
"""

SELECT_LLM_FAILURES_BY_RUN = f"""
--sql
    SELECT *
    FROM [{LLM_FAILURES_TABLE_NAME}]
    WHERE run_id = ?
    ORDER BY failure_id
;
"""

SELECT_TRANSLATION_QUALITY_ERRORS_BY_RUN = f"""
--sql
    SELECT *
    FROM [{TRANSLATION_QUALITY_ERRORS_TABLE_NAME}]
    WHERE run_id = ?
    ORDER BY location_path
;
"""

DELETE_ALL_PLUGIN_TEXT_RULES = f"""
--sql
    DELETE FROM [{PLUGIN_TEXT_RULES_TABLE_NAME}]
;
"""

DELETE_ALL_NOTE_TAG_TEXT_RULES = f"""
--sql
    DELETE FROM [{NOTE_TAG_TEXT_RULES_TABLE_NAME}]
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

DELETE_ALL_PLACEHOLDER_RULES = f"""
--sql
    DELETE FROM [{PLACEHOLDER_RULES_TABLE_NAME}]
;
"""

DELETE_ALL_JAPANESE_RESIDUAL_RULES = f"""
--sql
    DELETE FROM [{JAPANESE_RESIDUAL_RULES_TABLE_NAME}]
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

CHECK_CONNECTION_READABLE = """
--sql
    SELECT 1
;
"""

__all__: list[str] = [
    "CHECK_CONNECTION_READABLE",
    "CREATE_EVENT_COMMAND_TEXT_RULE_FILTERS_TABLE",
    "CREATE_EVENT_COMMAND_TEXT_RULE_GROUPS_TABLE",
    "CREATE_EVENT_COMMAND_TEXT_RULE_PATHS_TABLE",
    "CREATE_LLM_FAILURES_TABLE",
    "CREATE_JAPANESE_RESIDUAL_RULES_TABLE",
    "CREATE_METADATA_TABLE",
    "CREATE_NAME_CONTEXT_TERMS_TABLE",
    "CREATE_NOTE_TAG_TEXT_RULES_TABLE",
    "CREATE_PLACEHOLDER_RULES_TABLE",
    "CREATE_PLUGIN_TEXT_RULES_TABLE",
    "CREATE_TRANSLATION_QUALITY_ERRORS_TABLE",
    "CREATE_TRANSLATION_RUNS_TABLE",
    "CREATE_TRANSLATION_TABLE",
    "DELETE_ALL_PLACEHOLDER_RULES",
    "DELETE_ALL_JAPANESE_RESIDUAL_RULES",
    "DELETE_ALL_EVENT_COMMAND_TEXT_RULE_FILTERS",
    "DELETE_ALL_EVENT_COMMAND_TEXT_RULE_GROUPS",
    "DELETE_ALL_EVENT_COMMAND_TEXT_RULE_PATHS",
    "DELETE_ALL_NAME_CONTEXT_TERMS",
    "DELETE_ALL_NOTE_TAG_TEXT_RULES",
    "DELETE_ALL_PLUGIN_TEXT_RULES",
    "DELETE_ALL_TRANSLATION_QUALITY_ERRORS",
    "DELETE_TRANSLATION_ITEM_BY_PATH",
    "DELETE_TRANSLATION_ITEMS_BY_PREFIX",
    "EVENT_COMMAND_TEXT_RULE_FILTERS_TABLE_NAME",
    "EVENT_COMMAND_TEXT_RULE_GROUPS_TABLE_NAME",
    "EVENT_COMMAND_TEXT_RULE_PATHS_TABLE_NAME",
    "INSERT_EVENT_COMMAND_TEXT_RULE_FILTER",
    "INSERT_EVENT_COMMAND_TEXT_RULE_GROUP",
    "INSERT_EVENT_COMMAND_TEXT_RULE_PATH",
    "INSERT_LLM_FAILURE",
    "INSERT_NAME_CONTEXT_TERM",
    "INSERT_NOTE_TAG_TEXT_RULE",
    "INSERT_PLACEHOLDER_RULE",
    "INSERT_JAPANESE_RESIDUAL_RULE",
    "INSERT_PLUGIN_TEXT_RULE",
    "INSERT_TRANSLATION_QUALITY_ERROR",
    "LLM_FAILURES_TABLE_NAME",
    "JAPANESE_RESIDUAL_RULES_TABLE_NAME",
    "INSERT_TRANSLATION",
    "METADATA_KEY",
    "METADATA_TABLE_NAME",
    "NAME_CONTEXT_TERMS_TABLE_NAME",
    "NOTE_TAG_TEXT_RULES_TABLE_NAME",
    "PLACEHOLDER_RULES_TABLE_NAME",
    "PLUGIN_TEXT_RULES_TABLE_NAME",
    "SELECT_EVENT_COMMAND_TEXT_RULE_FILTERS",
    "SELECT_EVENT_COMMAND_TEXT_RULE_GROUPS",
    "SELECT_EVENT_COMMAND_TEXT_RULE_PATHS",
    "SELECT_METADATA",
    "SELECT_NAME_CONTEXT_TERMS",
    "SELECT_NOTE_TAG_TEXT_RULES",
    "SELECT_LATEST_TRANSLATION_RUN",
    "SELECT_JAPANESE_RESIDUAL_RULES",
    "SELECT_LLM_FAILURES_BY_RUN",
    "SELECT_PLACEHOLDER_RULES",
    "SELECT_PLUGIN_TEXT_RULES",
    "SELECT_TRANSLATION_QUALITY_ERRORS_BY_RUN",
    "SELECT_TRANSLATION_RUN",
    "SELECT_TRANSLATED_ITEMS",
    "SELECT_TRANSLATION_PATHS",
    "TRANSLATION_QUALITY_ERRORS_TABLE_NAME",
    "TRANSLATION_RUNS_TABLE_NAME",
    "TRANSLATION_TABLE_NAME",
    "UPSERT_METADATA",
    "UPSERT_TRANSLATION_RUN",
]
