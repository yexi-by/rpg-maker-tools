"""
多游戏数据库管理器使用的 SQL 常量模块。

本模块只服务新的多游戏数据库实现，不依赖项目内现有 `TranslationDB`。
这里集中定义静态表名、动态错误表 SQL、元数据读写 SQL 与连接可读性校验 SQL，
避免数据库管理逻辑把 SQL 字符串散落在各个方法里。
"""

TRANSLATION_TABLE_NAME: str = "translation_items"
GLOSSARY_ROLE_TABLE_NAME: str = "glossary_roles"
GLOSSARY_PLACE_TABLE_NAME: str = "glossary_places"
GLOSSARY_STATE_TABLE_NAME: str = "glossary_state"
METADATA_TABLE_NAME: str = "metadata"
METADATA_KEY: str = "current_game"

# 建立翻译结果表。
# 这张表沿用现有数据库语义，用于保存最终翻译产物。
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

# 建立错误表。
# 这张表使用动态表名，每次翻译任务可创建一张独立错误表。
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

# 建立角色术语表。
CREATE_ROLE_GLOSSARY_TABLE: str = f"""
--sql
    CREATE TABLE IF NOT EXISTS [{GLOSSARY_ROLE_TABLE_NAME}] (
        name            TEXT PRIMARY KEY,
        translated_name TEXT NOT NULL,
        gender          TEXT NOT NULL
    )
;
"""

# 建立地点术语表。
CREATE_PLACE_GLOSSARY_TABLE: str = f"""
--sql
    CREATE TABLE IF NOT EXISTS [{GLOSSARY_PLACE_TABLE_NAME}] (
        name            TEXT PRIMARY KEY,
        translated_name TEXT NOT NULL
    )
;
"""

# 建立术语状态表。
CREATE_GLOSSARY_STATE_TABLE: str = f"""
--sql
    CREATE TABLE IF NOT EXISTS [{GLOSSARY_STATE_TABLE_NAME}] (
        state_key TEXT PRIMARY KEY,
        is_ready  INTEGER NOT NULL
    )
;
"""

# 建立数据库元数据表。
# 这里专门保存游戏标题与游戏根目录，供进程重启后扫描 data/db 时恢复内存对象。
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

# 写入或替换翻译结果。
INSERT_TRANSLATION: str = f"""
--sql
    INSERT OR REPLACE INTO [{TRANSLATION_TABLE_NAME}]
    (location_path, item_type, role, original_lines, translation_lines)
    VALUES (?, ?, ?, ?, ?)
;
"""

# 写入或替换错误数据。
INSERT_ERROR: str = """
--sql
    INSERT OR REPLACE INTO [{table_name}]
    (location_path, item_type, role, original_lines, translation_lines, error_type, error_detail)
    VALUES (?, ?, ?, ?, ?, ?, ?)
;
"""

# 写入或替换角色术语。
INSERT_ROLE_GLOSSARY_ITEM: str = f"""
--sql
    INSERT OR REPLACE INTO [{GLOSSARY_ROLE_TABLE_NAME}]
    (name, translated_name, gender)
    VALUES (?, ?, ?)
;
"""

# 写入或替换地点术语。
INSERT_PLACE_GLOSSARY_ITEM: str = f"""
--sql
    INSERT OR REPLACE INTO [{GLOSSARY_PLACE_TABLE_NAME}]
    (name, translated_name)
    VALUES (?, ?)
;
"""

# 写入或替换术语状态。
UPSERT_GLOSSARY_STATE: str = f"""
--sql
    INSERT OR REPLACE INTO [{GLOSSARY_STATE_TABLE_NAME}]
    (state_key, is_ready)
    VALUES (?, ?)
;
"""

# 写入或更新当前数据库对应的游戏元数据。
UPSERT_METADATA: str = f"""
--sql
    INSERT OR REPLACE INTO [{METADATA_TABLE_NAME}]
    (metadata_key, game_title, game_path, source_language)
    VALUES (?, ?, ?, ?)
;
"""

UPDATE_METADATA_SOURCE_LANGUAGE: str = f"""
--sql
    UPDATE [{METADATA_TABLE_NAME}]
    SET source_language = ?
    WHERE metadata_key = ?
;
"""

# 查询整张表。
SELECT_ALL: str = """
--sql
    SELECT * FROM [{table_name}]
;
"""

# 查询角色术语表。
SELECT_ROLE_GLOSSARY_ITEMS: str = f"""
--sql
    SELECT name, translated_name, gender
    FROM [{GLOSSARY_ROLE_TABLE_NAME}]
    ORDER BY name
;
"""

# 查询地点术语表。
SELECT_PLACE_GLOSSARY_ITEMS: str = f"""
--sql
    SELECT name, translated_name
    FROM [{GLOSSARY_PLACE_TABLE_NAME}]
    ORDER BY name
;
"""

# 查询术语状态。
SELECT_GLOSSARY_STATE: str = f"""
--sql
    SELECT is_ready
    FROM [{GLOSSARY_STATE_TABLE_NAME}]
    WHERE state_key = ?
    LIMIT 1
;
"""

# 查询翻译表中全部已写入路径。
SELECT_TRANSLATION_PATHS: str = f"""
--sql
    SELECT location_path
    FROM [{TRANSLATION_TABLE_NAME}]
;
"""

# 查询翻译表中全部正文译文。
SELECT_TRANSLATED_ITEMS: str = f"""
--sql
    SELECT location_path, item_type, role, original_lines, translation_lines
    FROM [{TRANSLATION_TABLE_NAME}]
    ORDER BY location_path
;
"""

# 按名前缀查询最新错误表。
SELECT_LATEST_TABLE_NAME_BY_PREFIX: str = """
--sql
    SELECT name
    FROM sqlite_master
    WHERE type = 'table' AND name LIKE ?
    ORDER BY name DESC
    LIMIT 1
;
"""

# 读取当前数据库对应的游戏元数据。
SELECT_METADATA: str = f"""
--sql
    SELECT game_title, game_path, source_language
    FROM [{METADATA_TABLE_NAME}]
    WHERE metadata_key = ?
    LIMIT 1
;
"""

# 清空整张表。
DELETE_ALL_ROWS: str = """
--sql
    DELETE FROM [{table_name}]
;
"""

# 对已打开连接执行一次最轻量的读操作，用于尽早暴露坏库或非 SQLite 文件。
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
    "CREATE_ROLE_GLOSSARY_TABLE",
    "CREATE_TRANSLATION_TABLE",
    "DELETE_ALL_ROWS",
    "GLOSSARY_PLACE_TABLE_NAME",
    "GLOSSARY_ROLE_TABLE_NAME",
    "GLOSSARY_STATE_TABLE_NAME",
    "INSERT_ERROR",
    "INSERT_PLACE_GLOSSARY_ITEM",
    "INSERT_ROLE_GLOSSARY_ITEM",
    "INSERT_TRANSLATION",
    "METADATA_KEY",
    "METADATA_TABLE_NAME",
    "SELECT_ALL",
    "SELECT_GLOSSARY_STATE",
    "SELECT_LATEST_TABLE_NAME_BY_PREFIX",
    "SELECT_METADATA",
    "SELECT_PLACE_GLOSSARY_ITEMS",
    "SELECT_ROLE_GLOSSARY_ITEMS",
    "SELECT_TRANSLATED_ITEMS",
    "SELECT_TRANSLATION_PATHS",
    "TRANSLATION_TABLE_NAME",
    "UPDATE_METADATA_SOURCE_LANGUAGE",
    "UPSERT_GLOSSARY_STATE",
    "UPSERT_METADATA",
]
