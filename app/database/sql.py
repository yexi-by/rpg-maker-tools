"""
SQL 语句常量模块。

集中管理所有数据库操作的 SQL 语句模板。
使用 `{table_name}` 占位符表示表名，在 `db.py` 中通过 `.format()` 拼接实际表名。

边界说明：
1. 这里定义翻译主表、错误表和术语表的建表与读写 SQL。
2. 这里不负责任何业务语义判断，不负责“术语表是否完整”这类流程规则。
"""

# 建立翻译数据表
# location_path: 作为主键，记录该文本在 JSON 文件中的精确树状路径。
# item_type: 记录属于 long_text / short_text / array。
# original_lines / translation_lines: 序列化为 JSON 的字符串列表。
CREATE_TRANSLATION_TABLE: str = """
--sql
    CREATE TABLE IF NOT EXISTS [{table_name}] (
        location_path     TEXT PRIMARY KEY,
        item_type         TEXT NOT NULL,
        role              TEXT,
        original_lines    TEXT NOT NULL,
        translation_lines TEXT NOT NULL
    )
;
"""

# 建立错误数据表（比翻译表多 error_type 和 error_detail 两列）
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

# 建立角色术语表
CREATE_ROLE_GLOSSARY_TABLE: str = """
--sql
    CREATE TABLE IF NOT EXISTS [{table_name}] (
        name            TEXT PRIMARY KEY,
        translated_name TEXT NOT NULL,
        gender          TEXT NOT NULL
    )
;
"""

# 建立地点术语表
CREATE_PLACE_GLOSSARY_TABLE: str = """
--sql
    CREATE TABLE IF NOT EXISTS [{table_name}] (
        name            TEXT PRIMARY KEY,
        translated_name TEXT NOT NULL
    )
;
"""

# 建立术语状态表，用于区分“未构建术语表”和“已构建空术语表”
CREATE_GLOSSARY_STATE_TABLE: str = """
--sql
    CREATE TABLE IF NOT EXISTS [{table_name}] (
        state_key TEXT PRIMARY KEY,
        is_ready  INTEGER NOT NULL
    )
;
"""

# 插入或替换翻译数据
INSERT_TRANSLATION: str = """
--sql
    INSERT OR REPLACE INTO [{table_name}]
    (location_path, item_type, role, original_lines, translation_lines)
    VALUES (?, ?, ?, ?, ?)
;
"""

# 插入或替换错误数据
INSERT_ERROR: str = """
--sql
    INSERT OR REPLACE INTO [{table_name}]
    (location_path, item_type, role, original_lines, translation_lines, error_type, error_detail)
    VALUES (?, ?, ?, ?, ?, ?, ?)
;
"""

# 插入或替换角色术语数据
INSERT_ROLE_GLOSSARY_ITEM: str = """
--sql
    INSERT OR REPLACE INTO [{table_name}]
    (name, translated_name, gender)
    VALUES (?, ?, ?)
;
"""

# 插入或替换地点术语数据
INSERT_PLACE_GLOSSARY_ITEM: str = """
--sql
    INSERT OR REPLACE INTO [{table_name}]
    (name, translated_name)
    VALUES (?, ?)
;
"""

# 插入或替换术语状态
UPSERT_GLOSSARY_STATE: str = """
--sql
    INSERT OR REPLACE INTO [{table_name}]
    (state_key, is_ready)
    VALUES (?, ?)
;
"""

# 查询指定表的全部数据
SELECT_ALL: str = """
--sql
    SELECT * FROM [{table_name}]
;
"""

# 查询角色术语表中的全部数据
SELECT_ROLE_GLOSSARY_ITEMS: str = """
--sql
    SELECT name, translated_name, gender
    FROM [{table_name}]
    ORDER BY name
;
"""

# 查询地点术语表中的全部数据
SELECT_PLACE_GLOSSARY_ITEMS: str = """
--sql
    SELECT name, translated_name
    FROM [{table_name}]
    ORDER BY name
;
"""

# 查询术语状态
SELECT_GLOSSARY_STATE: str = """
--sql
    SELECT is_ready
    FROM [{table_name}]
    WHERE state_key = ?
    LIMIT 1
;
"""

# 查询翻译表中的路径与译文字段
SELECT_TRANSLATION_PATHS: str = """
--sql
    SELECT location_path, translation_lines FROM [{table_name}]
;
"""

# 按名前缀查询最新错误表名
# 从 sqlite_master 系统表中，按名称倒序（时间戳由新到旧）查找匹配前缀的第一张表。
SELECT_LATEST_TABLE_NAME_BY_PREFIX: str = """
--sql
    SELECT name
    FROM sqlite_master
    WHERE type = 'table' AND name LIKE ?
    ORDER BY name DESC
    LIMIT 1
;
"""

# 清空整张表
DELETE_ALL_ROWS: str = """
--sql
    DELETE FROM [{table_name}]
;
"""
