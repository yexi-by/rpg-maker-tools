"""
数据库模块共用工具函数。

这里集中放置新数据库实现依赖的路径、文件读取和 SQLite 辅助逻辑，
供 `app.database.db` 与其他主线代码使用。
"""

import json
from pathlib import Path

import aiosqlite

from app.database.sql import (
    CHECK_CONNECTION_READABLE,
    CREATE_GLOSSARY_STATE_TABLE,
    CREATE_METADATA_TABLE,
    CREATE_PLACE_GLOSSARY_TABLE,
    CREATE_ROLE_GLOSSARY_TABLE,
    CREATE_TRANSLATION_TABLE,
    METADATA_KEY,
    SELECT_METADATA,
    UPSERT_METADATA,
)

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
DB_DIRECTORY: Path = PROJECT_ROOT / "data" / "db"
PACKAGE_FILE_NAME: str = "package.json"
INVALID_FILE_NAME_CHARS: set[str] = set('<>:"/\\|?*')


def ensure_db_directory() -> None:
    """
    确保固定数据库目录存在。

    目录规则已经锁定为仓库根目录下的 `data/db`，
    所以这里不接配置系统，直接按固定路径创建目录。
    """
    DB_DIRECTORY.mkdir(parents=True, exist_ok=True)


def resolve_game_directory(game_path: str | Path) -> Path:
    """
    解析并校验游戏根目录路径。

    Args:
        game_path: 外部传入的游戏目录路径。

    Returns:
        解析后的游戏根目录绝对路径。

    Raises:
        FileNotFoundError: 路径不存在时抛出。
        NotADirectoryError: 路径不是目录时抛出。
    """
    resolved_path = Path(game_path).resolve()
    if not resolved_path.exists():
        raise FileNotFoundError(f"游戏目录不存在: {resolved_path}")
    if not resolved_path.is_dir():
        raise NotADirectoryError(f"游戏路径不是目录: {resolved_path}")
    return resolved_path


def read_game_title(game_path: Path) -> str:
    """
    从游戏目录下的 `package.json` 读取游戏标题。

    Args:
        game_path: 已校验存在的游戏根目录。

    Returns:
        `window.title` 中的非空标题字符串。

    Raises:
        FileNotFoundError: `package.json` 不存在时抛出。
        ValueError: `package.json` 结构不合法或缺少标题时抛出。
        json.JSONDecodeError: 文件内容不是合法 JSON 时抛出。
    """
    package_path = game_path / PACKAGE_FILE_NAME
    if not package_path.exists():
        raise FileNotFoundError(f"未找到 package.json: {package_path}")

    raw_text = package_path.read_text(encoding="utf-8")
    package_data = json.loads(raw_text)

    if not isinstance(package_data, dict):
        raise ValueError(f"package.json 顶层必须是对象: {package_path}")

    window_config = package_data.get("window")
    if not isinstance(window_config, dict):
        raise ValueError(f"package.json 缺少 window 对象: {package_path}")

    title = window_config.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"package.json 缺少有效的 window.title: {package_path}")

    return title.strip()


def build_db_path(game_title: str) -> Path:
    """
    根据游戏标题生成固定数据库文件路径。

    Args:
        game_title: 已完成基础校验的游戏标题。

    Returns:
        数据库文件绝对路径。

    Raises:
        ValueError: 标题包含 Windows 非法文件名字符时抛出。
    """
    invalid_chars = sorted(
        {char for char in game_title if char in INVALID_FILE_NAME_CHARS}
    )
    if invalid_chars:
        joined_chars = "".join(invalid_chars)
        raise ValueError(f"游戏标题包含非法文件名字符，无法创建数据库: {joined_chars}")

    return DB_DIRECTORY / f"{game_title}.db"


async def open_connection(db_path: Path) -> aiosqlite.Connection:
    """
    打开 SQLite 连接并设置统一行工厂。

    Args:
        db_path: 数据库文件绝对路径。

    Returns:
        已准备好的异步数据库连接。
    """
    connection = await aiosqlite.connect(db_path)
    connection.row_factory = aiosqlite.Row
    return connection


async def check_connection_readable(
    connection: aiosqlite.Connection,
    db_path: Path,
) -> None:
    """
    对已打开连接执行一次最轻量的可读性检查。

    Args:
        connection: 已建立的数据库连接。
        db_path: 当前数据库文件路径，用于拼装错误上下文。

    Raises:
        RuntimeError: 查询结果不是预期结构时抛出。
        aiosqlite.Error: 数据库文件不可读时由底层直接抛出。
    """
    async with connection.execute(CHECK_CONNECTION_READABLE) as cursor:
        row = await cursor.fetchone()

    if row is None:
        raise RuntimeError(f"数据库可读性校验失败，未返回任何结果: {db_path}")
    if row[0] != 1:
        raise RuntimeError(f"数据库可读性校验失败，返回值异常: {db_path}")


async def create_static_tables(connection: aiosqlite.Connection) -> None:
    """
    初始化多游戏数据库管理器要求的全部静态表。

    Args:
        connection: 目标数据库连接。
    """
    _ = await connection.execute(CREATE_TRANSLATION_TABLE)
    _ = await connection.execute(CREATE_ROLE_GLOSSARY_TABLE)
    _ = await connection.execute(CREATE_PLACE_GLOSSARY_TABLE)
    _ = await connection.execute(CREATE_GLOSSARY_STATE_TABLE)
    _ = await connection.execute(CREATE_METADATA_TABLE)
    await connection.commit()


async def write_metadata(
    connection: aiosqlite.Connection,
    game_title: str,
    game_path: Path,
) -> None:
    """
    把游戏标题与游戏根目录写入元数据表。

    Args:
        connection: 目标数据库连接。
        game_title: 游戏标题。
        game_path: 游戏根目录绝对路径。
    """
    _ = await connection.execute(
        UPSERT_METADATA,
        (METADATA_KEY, game_title, str(game_path)),
    )
    await connection.commit()


async def read_metadata(
    connection: aiosqlite.Connection,
    db_path: Path,
) -> tuple[str, Path]:
    """
    从元数据表恢复游戏标题与游戏根目录。

    Args:
        connection: 已建立的数据库连接。
        db_path: 当前数据库文件路径，用于构造清晰错误信息。

    Returns:
        校验通过后的游戏标题与游戏根目录。

    Raises:
        RuntimeError: 元数据表缺失记录、字段类型错误或字段为空时抛出。
    """
    async with connection.execute(SELECT_METADATA, (METADATA_KEY,)) as cursor:
        row = await cursor.fetchone()

    if row is None:
        raise RuntimeError(f"数据库缺少 metadata 元数据记录: {db_path}")

    raw_game_title = row["game_title"]
    raw_game_path = row["game_path"]

    if not isinstance(raw_game_title, str) or not raw_game_title.strip():
        raise RuntimeError(f"metadata.game_title 非法: {db_path}")
    if not isinstance(raw_game_path, str) or not raw_game_path.strip():
        raise RuntimeError(f"metadata.game_path 非法: {db_path}")

    return raw_game_title.strip(), Path(raw_game_path).resolve()


__all__: list[str] = [
    "DB_DIRECTORY",
    "build_db_path",
    "check_connection_readable",
    "create_static_tables",
    "ensure_db_directory",
    "open_connection",
    "read_game_title",
    "read_metadata",
    "resolve_game_directory",
    "write_metadata",
]
