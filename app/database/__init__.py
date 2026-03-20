"""
数据库模块公共导出入口。

本模块只暴露业务编排层真正需要依赖的数据库能力，
避免外部直接引用 `db.py` 里的底层连接辅助函数和 SQL 实现细节。
"""

from .db import (
    DB_DIRECTORY,
    DEFAULT_ERROR_TABLE_PREFIX,
    GameDatabaseItem,
    GameDatabaseManager,
    build_db_path,
    ensure_db_directory,
)

__all__: list[str] = [
    "DB_DIRECTORY",
    "DEFAULT_ERROR_TABLE_PREFIX",
    "GameDatabaseItem",
    "GameDatabaseManager",
    "build_db_path",
    "ensure_db_directory",
]
