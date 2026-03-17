"""
多游戏数据库管理模块。

本模块统一管理 `data/db` 下的多个 SQLite 数据库文件。
调用方通过 `game_title` 选择目标数据库，再执行翻译表、术语表和错误表相关操作。
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Self

import aiosqlite

from app.models.schemas import (
    ErrorRetryItem,
    Glossary,
    ItemType,
    Place,
    Role,
    SOURCE_LANGUAGE_VALUES,
    SourceLanguage,
    TranslationErrorItem,
    TranslationItem,
)
from app.utils.log_utils import logger
from app.utils.game_loader_utils import read_game_title, resolve_game_directory
from app.utils.source_language_utils import validate_source_language

from .sql import (
    CHECK_CONNECTION_READABLE,
    CREATE_ERROR_TABLE,
    CREATE_GLOSSARY_STATE_TABLE,
    CREATE_METADATA_TABLE,
    CREATE_PLACE_GLOSSARY_TABLE,
    CREATE_ROLE_GLOSSARY_TABLE,
    CREATE_TRANSLATION_TABLE,
    DELETE_ALL_ROWS,
    GLOSSARY_PLACE_TABLE_NAME,
    GLOSSARY_ROLE_TABLE_NAME,
    INSERT_ERROR,
    INSERT_PLACE_GLOSSARY_ITEM,
    INSERT_ROLE_GLOSSARY_ITEM,
    INSERT_TRANSLATION,
    SELECT_ALL,
    SELECT_GLOSSARY_STATE,
    SELECT_LATEST_TABLE_NAME_BY_PREFIX,
    SELECT_METADATA,
    SELECT_PLACE_GLOSSARY_ITEMS,
    SELECT_ROLE_GLOSSARY_ITEMS,
    SELECT_TRANSLATED_ITEMS,
    SELECT_TRANSLATION_PATHS,
    UPDATE_METADATA_SOURCE_LANGUAGE,
    UPSERT_GLOSSARY_STATE,
    UPSERT_METADATA,
    METADATA_KEY,
)

GLOSSARY_STATE_KEY: str = "current_glossary"
DEFAULT_ERROR_TABLE_PREFIX: str = "translation_errors"
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
DB_DIRECTORY: Path = PROJECT_ROOT / "data" / "db"
INVALID_FILE_NAME_CHARS: set[str] = set('<>:"/\\|?*')


def ensure_db_directory() -> None:
    """
    确保固定数据库目录存在。

    目录规则已经锁定为仓库根目录下的 `data/db`，
    因此这里不再走配置解析，直接按固定路径创建目录。
    """
    DB_DIRECTORY.mkdir(parents=True, exist_ok=True)


def build_db_path(game_title: str) -> Path:
    """
    根据游戏标题生成固定数据库文件路径。

    Args:
        game_title: 已完成基础校验的游戏标题。

    Returns:
        数据库文件绝对路径。

    Raises:
        ValueError: 标题包含 Windows 非法文件名字时抛出。
    """
    invalid_chars = sorted(
        {char for char in game_title if char in INVALID_FILE_NAME_CHARS}
    )
    if invalid_chars:
        joined_chars = "".join(invalid_chars)
        raise ValueError(f"游戏标题包含非法文件名字，无法创建数据库: {joined_chars}")

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
    source_language: SourceLanguage,
) -> None:
    """
    把游戏标题、游戏根目录和源语言写入元数据表。

    Args:
        connection: 目标数据库连接。
        game_title: 游戏标题。
        game_path: 游戏根目录绝对路径。
        source_language: 当前游戏的源语言。
    """
    _ = await connection.execute(
        UPSERT_METADATA,
        (METADATA_KEY, game_title, str(game_path), source_language),
    )
    await connection.commit()


async def read_metadata(
    connection: aiosqlite.Connection,
    db_path: Path,
) -> tuple[str, Path, SourceLanguage]:
    """
    从元数据表恢复游戏标题、游戏根目录与源语言。

    Args:
        connection: 已建立的数据库连接。
        db_path: 当前数据库文件路径，用于构造错误信息。

    Returns:
        校验通过后的游戏标题、游戏根目录和源语言。

    Raises:
        RuntimeError: 元数据表缺失记录、字段类型错误或字段为空时抛出。
    """
    try:
        async with connection.execute(SELECT_METADATA, (METADATA_KEY,)) as cursor:
            row = await cursor.fetchone()
    except aiosqlite.Error as error:
        raise RuntimeError(
            "数据库 metadata 表缺少 source_language 字段，请先执行迁移脚本: "
            f"{db_path}"
        ) from error

    if row is None:
        raise RuntimeError(f"数据库缺少 metadata 元数据记录: {db_path}")

    raw_game_title = row["game_title"]
    raw_game_path = row["game_path"]
    raw_source_language = row["source_language"]

    if not isinstance(raw_game_title, str) or not raw_game_title.strip():
        raise RuntimeError(f"metadata.game_title 非法: {db_path}")
    if not isinstance(raw_game_path, str) or not raw_game_path.strip():
        raise RuntimeError(f"metadata.game_path 非法: {db_path}")
    if not isinstance(raw_source_language, str) or not raw_source_language.strip():
        raise RuntimeError(f"metadata.source_language 非法: {db_path}")

    try:
        source_language = validate_source_language(raw_source_language)
    except ValueError as error:
        supported_values = ", ".join(SOURCE_LANGUAGE_VALUES)
        raise RuntimeError(
            f"metadata.source_language 非法，仅支持 {supported_values}: {db_path}"
        ) from error

    return raw_game_title.strip(), Path(raw_game_path).resolve(), source_language


async def update_metadata_source_language(
    connection: aiosqlite.Connection,
    source_language: SourceLanguage,
) -> None:
    """
    仅更新元数据表中的源语言字段。

    Args:
        connection: 目标数据库连接。
        source_language: 新的源语言值。
    """
    _ = await connection.execute(
        UPDATE_METADATA_SOURCE_LANGUAGE,
        (source_language, METADATA_KEY),
    )
    await connection.commit()


@dataclass(slots=True)
class GameDatabaseItem:
    """
    单个游戏数据库的运行时对象。

    Attributes:
        game_title: 游戏标题，来自 `package.json.window.title`。
        game_path: 游戏根目录绝对路径。
        source_language: 当前游戏登记的源语言。
        db_path: 当前数据库文件绝对路径。
        connection: 与当前数据库文件绑定的异步 SQLite 连接。
    """

    game_title: str
    game_path: Path
    source_language: SourceLanguage
    db_path: Path
    connection: aiosqlite.Connection


class GameDatabaseManager:
    """
    多游戏数据库管理器。

    管理器启动时会扫描仓库根目录下的 `data/db`，
    恢复所有可读取且带有完整元数据的数据库对象。
    后续所有数据库读写操作都通过 `game_title` 指定目标游戏。
    """

    def __init__(self, items: dict[str, GameDatabaseItem]) -> None:
        """
        初始化管理器实例。

        Args:
            items: 已准备好的数据库对象列表。
        """
        self.items: dict[str, GameDatabaseItem] = items

    @classmethod
    async def new(cls) -> Self:
        """
        扫描 `data/db` 并恢复全部数据库对象。

        Returns:
            已完成目录准备与数据库扫描的管理器实例。
        """
        ensure_db_directory()

        items: dict[str, GameDatabaseItem] = {}
        for db_path in sorted(DB_DIRECTORY.glob("*.db")):
            connection = await open_connection(db_path)
            try:
                await check_connection_readable(connection=connection, db_path=db_path)
                game_title, game_path, source_language = await read_metadata(
                    connection=connection,
                    db_path=db_path,
                )
            except Exception:
                await connection.close()
                raise

            items[game_title] = GameDatabaseItem(
                game_title=game_title,
                game_path=game_path,
                source_language=source_language,
                db_path=db_path,
                connection=connection,
            )

        return cls(items=items)

    async def create_database(
        self,
        game_path: str | Path,
        source_language: SourceLanguage,
    ) -> None:
        """
        为指定游戏目录创建数据库，复用已存在的同名数据库，或在同标题迁移时更新路径。

        Args:
            game_path: RPG Maker 游戏根目录路径。
            source_language: 当前游戏的源语言。
        """
        ensure_db_directory()

        resolved_game_path = resolve_game_directory(game_path)
        game_title = read_game_title(resolved_game_path)
        if game_title in self.items:
            item = self.items[game_title]
            if (
                item.game_path == resolved_game_path
                and item.source_language == source_language
            ):
                return

            await write_metadata(
                connection=item.connection,
                game_title=game_title,
                game_path=resolved_game_path,
                source_language=source_language,
            )
            item.game_path = resolved_game_path
            item.source_language = source_language
            logger.warning(
                f"[tag.warning]检测到同标题游戏路径变化，已更新数据库绑定路径[/tag.warning] "
                f"标题 [tag.count]{game_title}[/tag.count] "
                f"新路径 [tag.path]{resolved_game_path}[/tag.path]"
            )
            return

        db_path = build_db_path(game_title)
        db_already_exists = db_path.exists()
        connection = await open_connection(db_path)

        try:
            if db_already_exists:
                await check_connection_readable(connection=connection, db_path=db_path)
                await read_metadata(connection=connection, db_path=db_path)
            else:
                await create_static_tables(connection)

            await write_metadata(
                connection=connection,
                game_title=game_title,
                game_path=resolved_game_path,
                source_language=source_language,
            )
        except Exception:
            await connection.close()
            raise

        self.items[game_title] = GameDatabaseItem(
            game_title=game_title,
            game_path=resolved_game_path,
            source_language=source_language,
            db_path=db_path,
            connection=connection,
        )

    async def update_source_language(
        self,
        game_title: str,
        source_language: SourceLanguage,
    ) -> None:
        """
        更新指定游戏的源语言并立即写回数据库。

        Args:
            game_title: 目标游戏标题。
            source_language: 新的源语言。
        """
        item = self._get_item(game_title)
        if item.source_language == source_language:
            return

        await update_metadata_source_language(
            connection=item.connection,
            source_language=source_language,
        )
        item.source_language = source_language

    async def write_translation_items(
        self,
        game_title: str,
        items: list[tuple[str, ItemType, str | None, list[str], list[str]]],
    ) -> None:
        """
        批量写入已完成的译文到主翻译表。

        Args:
            game_title: 目标游戏标题。
            items: 待写入的翻译条目列表。
        """
        item = self._get_item(game_title)

        if items:
            serialized_items: list[tuple[str, str, str | None, str, str]] = [
                (
                    location_path,
                    item_type,
                    role,
                    json.dumps(original_lines, ensure_ascii=False),
                    json.dumps(translation_lines, ensure_ascii=False),
                )
                for location_path, item_type, role, original_lines, translation_lines in items
            ]
            _ = await item.connection.executemany(
                INSERT_TRANSLATION,
                serialized_items,
            )

        await item.connection.commit()

    async def read_glossary(self, game_title: str) -> Glossary | None:
        """
        读取当前有效术语表。

        Args:
            game_title: 目标游戏标题。

        Returns:
            已存在时返回结构化术语表；未就绪时返回 `None`。
        """
        item = self._get_item(game_title)

        async with item.connection.execute(
            SELECT_GLOSSARY_STATE,
            (GLOSSARY_STATE_KEY,),
        ) as cursor:
            state_row = await cursor.fetchone()

        if state_row is None or not state_row["is_ready"]:
            return None

        async with item.connection.execute(SELECT_ROLE_GLOSSARY_ITEMS) as cursor:
            role_rows = await cursor.fetchall()
        async with item.connection.execute(SELECT_PLACE_GLOSSARY_ITEMS) as cursor:
            place_rows = await cursor.fetchall()

        roles = [
            Role(
                name=row["name"],
                translated_name=row["translated_name"],
                gender=row["gender"],
            )
            for row in role_rows
        ]
        places = [
            Place(
                name=row["name"],
                translated_name=row["translated_name"],
            )
            for row in place_rows
        ]
        return Glossary(roles=roles, places=places)

    async def replace_glossary(self, game_title: str, glossary: Glossary) -> None:
        """
        用新的术语表内容整表替换当前数据库中的术语表。

        Args:
            game_title: 目标游戏标题。
            glossary: 新的完整术语表。
        """
        item = self._get_item(game_title)

        _ = await item.connection.execute(
            DELETE_ALL_ROWS.format(table_name=GLOSSARY_ROLE_TABLE_NAME)
        )
        _ = await item.connection.execute(
            DELETE_ALL_ROWS.format(table_name=GLOSSARY_PLACE_TABLE_NAME)
        )

        if glossary.roles:
            role_items: list[tuple[str, str, str]] = [
                (role.name, role.translated_name, role.gender)
                for role in glossary.roles
            ]
            _ = await item.connection.executemany(
                INSERT_ROLE_GLOSSARY_ITEM,
                role_items,
            )

        if glossary.places:
            place_items: list[tuple[str, str]] = [
                (place.name, place.translated_name) for place in glossary.places
            ]
            _ = await item.connection.executemany(
                INSERT_PLACE_GLOSSARY_ITEM,
                place_items,
            )

        _ = await item.connection.execute(
            UPSERT_GLOSSARY_STATE,
            (GLOSSARY_STATE_KEY, 1),
        )
        await item.connection.commit()

    async def read_translation_location_paths(self, game_title: str) -> set[str]:
        """
        读取主翻译表中全部已写入路径。

        Args:
            game_title: 目标游戏标题。

        Returns:
            主翻译表中的 `location_path` 集合。
        """
        item = self._get_item(game_title)

        async with item.connection.execute(SELECT_TRANSLATION_PATHS) as cursor:
            rows = await cursor.fetchall()

        return {row["location_path"] for row in rows}

    async def read_translated_items(self, game_title: str) -> list[TranslationItem]:
        """
        读取主翻译表中的全部正文译文。

        Args:
            game_title: 目标游戏标题。

        Returns:
            结构化正文译文列表。
        """
        item = self._get_item(game_title)

        async with item.connection.execute(SELECT_TRANSLATED_ITEMS) as cursor:
            rows = await cursor.fetchall()

        return [
            TranslationItem(
                location_path=row["location_path"],
                item_type=row["item_type"],
                role=row["role"],
                original_lines=json.loads(row["original_lines"]),
                translation_lines=json.loads(row["translation_lines"]),
            )
            for row in rows
        ]

    async def read_latest_error_table_name(
        self,
        game_title: str,
        prefix: str,
    ) -> str | None:
        """
        读取指定前缀下按名称排序最新的一张错误表。

        Args:
            game_title: 目标游戏标题。
            prefix: 错误表名前缀。

        Returns:
            最新错误表名；不存在时返回 `None`。
        """
        item = self._get_item(game_title)

        async with item.connection.execute(
            SELECT_LATEST_TABLE_NAME_BY_PREFIX,
            (f"{prefix}_%",),
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return None

        return row["name"]

    async def read_error_retry_items(
        self,
        game_title: str,
        table_name: str,
    ) -> list[ErrorRetryItem]:
        """
        读取指定错误表，并转换为错误重翻译条目列表。

        Args:
            game_title: 目标游戏标题。
            table_name: 错误表名。

        Returns:
            错误重翻译条目列表。
        """
        rows = await self.read_table(game_title, table_name)
        retry_items = [
            ErrorRetryItem(
                translation_item=TranslationItem(
                    location_path=row["location_path"],
                    item_type=row["item_type"],
                    role=row["role"],
                    original_lines=json.loads(row["original_lines"]),
                ),
                previous_translation_lines=json.loads(row["translation_lines"]),
                error_type=row["error_type"],
                error_detail=json.loads(row["error_detail"]),
            )
            for row in rows
        ]
        retry_items.sort(key=lambda item: item.translation_item.location_path)
        return retry_items

    async def start_error_table(
        self,
        game_title: str,
        prefix: str = DEFAULT_ERROR_TABLE_PREFIX,
    ) -> str:
        """
        创建当前翻译任务使用的错误表，并返回新表名。

        Args:
            game_title: 目标游戏标题。

            prefix: 错误表名前缀。

        Returns:
            新创建的错误表名。
        """
        item = self._get_item(game_title)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        table_name = f"{prefix}_{timestamp}"

        _ = await item.connection.execute(
            CREATE_ERROR_TABLE.format(table_name=table_name)
        )
        await item.connection.commit()
        return table_name

    async def write_error_items(
        self,
        game_title: str,
        table_name: str,
        items: list[TranslationErrorItem],
    ) -> None:
        """
        向指定错误表追加写入错误数据。

        Args:
            game_title: 目标游戏标题。
            table_name: 错误表名。
            items: 待写入的错误条目列表。
        """
        item = self._get_item(game_title)

        if items:
            serialized_items: list[tuple[str, str, str | None, str, str, str, str]] = [
                (
                    location_path,
                    item_type,
                    role,
                    json.dumps(original_lines, ensure_ascii=False),
                    json.dumps(translation_lines, ensure_ascii=False),
                    error_type,
                    json.dumps(error_detail, ensure_ascii=False),
                )
                for location_path, item_type, role, original_lines, translation_lines, error_type, error_detail in items
            ]
            _ = await item.connection.executemany(
                INSERT_ERROR.format(table_name=table_name),
                serialized_items,
            )

        await item.connection.commit()

    async def read_table(self, game_title: str, table_name: str) -> list[dict[str, Any]]:
        """
        读取指定表的所有数据，并转成原生字典列表。

        Args:
            game_title: 目标游戏标题。
            table_name: 目标表名。

        Returns:
            原生字典列表。
        """
        item = self._get_item(game_title)

        async with item.connection.execute(
            SELECT_ALL.format(table_name=table_name)
        ) as cursor:
            rows = await cursor.fetchall()

        return [dict(row) for row in rows]

    async def close(self) -> None:
        """
        关闭当前管理器持有的全部数据库连接。

        Raises:
            RuntimeError: 存在至少一个数据库连接关闭失败时抛出。
        """
        errors: list[str] = []

        for item in self.items.values():
            try:
                await item.connection.close()
            except Exception as exc:  # pragma: no cover
                errors.append(f"{item.db_path}: {exc}")

        self.items.clear()

        if errors:
            error_message = "\n".join(errors)
            raise RuntimeError(f"关闭数据库连接失败:\n{error_message}")

    def _get_item(self, game_title: str) -> GameDatabaseItem:
        """
        根据游戏标题查找已加载的数据库对象。

        Args:
            game_title: 目标游戏标题。

        Returns:
            命中的数据库对象。

        Raises:
            ValueError: 当前管理器中不存在对应标题时抛出。
        """
        item = self.items.get(game_title)
        if item is None:
            raise ValueError(f"未找到游戏数据库: {game_title}")
        return item


__all__: list[str] = ["DB_DIRECTORY", "GameDatabaseItem", "GameDatabaseManager"]
