"""
多游戏数据库管理模块。

本模块管理 `data/db` 下的多个 SQLite 数据库文件。数据库保存 CLI 流程所需的
游戏元数据、译文、错误表、插件规则和术语表条目。
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Self

import aiosqlite

from app.name_context.schemas import NameContextRegistry, NameEntryKind, NameRegistryEntry
from app.rmmz.schema import (
    ItemType,
    PluginTextRuleRecord,
    TranslationErrorItem,
    TranslationItem,
)
from app.rmmz.loader import read_game_title, resolve_game_directory
from app.observability.logging import logger

from .rows import (
    decode_name_locations,
    decode_plugin_translate_rules,
    decode_string_list,
    row_int,
    row_item_type,
    row_optional_str,
    row_str,
    row_to_dict,
)
from .sql import (
    CHECK_CONNECTION_READABLE,
    CREATE_ERROR_TABLE,
    CREATE_METADATA_TABLE,
    CREATE_NAME_CONTEXT_ENTRIES_TABLE,
    CREATE_PLUGIN_TEXT_RULES_TABLE,
    CREATE_TRANSLATION_TABLE,
    DELETE_ALL_NAME_CONTEXT_ENTRIES,
    DELETE_ALL_PLUGIN_TEXT_RULES,
    DELETE_TRANSLATION_ITEMS_BY_PREFIX,
    DROP_TABLE,
    INSERT_ERROR,
    INSERT_NAME_CONTEXT_ENTRY,
    INSERT_TRANSLATION,
    METADATA_KEY,
    SELECT_ALL,
    SELECT_METADATA,
    SELECT_NAME_CONTEXT_ENTRIES,
    SELECT_PLUGIN_TEXT_RULES,
    SELECT_TABLE_NAMES_BY_PREFIX,
    SELECT_TRANSLATED_ITEMS,
    SELECT_TRANSLATION_PATHS,
    UPSERT_METADATA,
    UPSERT_PLUGIN_TEXT_RULE,
)

DEFAULT_ERROR_TABLE_PREFIX = "translation_errors"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_DIRECTORY = PROJECT_ROOT / "data" / "db"
INVALID_FILE_NAME_CHARS = set('<>:"/\\|?*')


def ensure_db_directory(db_directory: Path = DB_DIRECTORY) -> None:
    """确保固定数据库目录存在。"""
    db_directory.mkdir(parents=True, exist_ok=True)


def build_db_path(game_title: str, db_directory: Path = DB_DIRECTORY) -> Path:
    """根据游戏标题生成固定数据库路径。"""
    invalid_chars = sorted({char for char in game_title if char in INVALID_FILE_NAME_CHARS})
    if invalid_chars:
        joined_chars = "".join(invalid_chars)
        raise ValueError(f"游戏标题包含非法文件名字，无法创建数据库: {joined_chars}")
    return db_directory / f"{game_title}.db"


async def open_connection(db_path: Path) -> aiosqlite.Connection:
    """打开 SQLite 连接并设置统一行工厂。"""
    connection = await aiosqlite.connect(db_path)
    connection.row_factory = aiosqlite.Row
    return connection


async def check_connection_readable(connection: aiosqlite.Connection, db_path: Path) -> None:
    """对已打开连接执行最轻量可读性检查。"""
    async with connection.execute(CHECK_CONNECTION_READABLE) as cursor:
        row = await cursor.fetchone()

    if row is None:
        raise RuntimeError(f"数据库可读性校验失败，未返回任何结果: {db_path}")
    if row[0] != 1:
        raise RuntimeError(f"数据库可读性校验失败，返回值异常: {db_path}")


async def create_static_tables(connection: aiosqlite.Connection) -> None:
    """初始化当前数据库要求的全部静态表。"""
    _ = await connection.execute(CREATE_TRANSLATION_TABLE)
    _ = await connection.execute(CREATE_METADATA_TABLE)
    _ = await connection.execute(CREATE_PLUGIN_TEXT_RULES_TABLE)
    _ = await connection.execute(CREATE_NAME_CONTEXT_ENTRIES_TABLE)
    await connection.commit()


async def write_metadata(connection: aiosqlite.Connection, game_title: str, game_path: Path) -> None:
    """把游戏标题与游戏根目录写入元数据表。"""
    _ = await connection.execute(UPSERT_METADATA, (METADATA_KEY, game_title, str(game_path)))
    await connection.commit()


async def read_metadata(connection: aiosqlite.Connection, db_path: Path) -> tuple[str, Path]:
    """从元数据表恢复游戏标题和游戏根目录。"""
    try:
        async with connection.execute(SELECT_METADATA, (METADATA_KEY,)) as cursor:
            row = await cursor.fetchone()
    except aiosqlite.Error as error:
        raise RuntimeError(f"数据库 metadata 表不是当前核心版 schema，请重新注册游戏: {db_path}") from error

    if row is None:
        raise RuntimeError(f"数据库缺少 metadata 元数据记录: {db_path}")

    game_title = row_str(row, "game_title", db_path)
    game_path = row_str(row, "game_path", db_path)
    if not game_title.strip():
        raise RuntimeError(f"metadata.game_title 非法: {db_path}")
    if not game_path.strip():
        raise RuntimeError(f"metadata.game_path 非法: {db_path}")
    return game_title.strip(), Path(game_path).resolve()


def _read_name_entry_kind(value: str, db_path: Path) -> NameEntryKind:
    """读取并校验数据库中的术语表条目类型。"""
    if value not in ("speaker_name", "map_display_name"):
        raise TypeError(f"数据库字段 kind 不是有效术语表类型: {db_path}")
    return value


@dataclass(slots=True)
class GameDatabaseItem:
    """单个游戏数据库的运行时对象。"""

    game_title: str
    game_path: Path
    db_path: Path
    connection: aiosqlite.Connection


class GameDatabaseManager:
    """多游戏数据库管理器。"""

    def __init__(self, items: dict[str, GameDatabaseItem], db_directory: Path = DB_DIRECTORY) -> None:
        """初始化管理器。"""
        self.items: dict[str, GameDatabaseItem] = items
        self.db_directory: Path = db_directory

    @classmethod
    async def new(cls, db_directory: Path = DB_DIRECTORY) -> Self:
        """扫描 `data/db` 并恢复全部数据库对象。"""
        ensure_db_directory(db_directory)
        items: dict[str, GameDatabaseItem] = {}
        for db_path in sorted(db_directory.glob("*.db")):
            connection = await open_connection(db_path)
            try:
                await check_connection_readable(connection=connection, db_path=db_path)
                game_title, game_path = await read_metadata(connection=connection, db_path=db_path)
                await create_static_tables(connection)
            except Exception:
                await connection.close()
                raise

            items[game_title] = GameDatabaseItem(
                game_title=game_title,
                game_path=game_path,
                db_path=db_path,
                connection=connection,
            )
        return cls(items=items, db_directory=db_directory)

    async def create_database(self, game_path: str | Path) -> None:
        """为指定游戏目录创建或更新数据库绑定。"""
        ensure_db_directory(self.db_directory)
        resolved_game_path = resolve_game_directory(game_path)
        game_title = read_game_title(resolved_game_path)
        if game_title in self.items:
            item = self.items[game_title]
            if item.game_path == resolved_game_path:
                return
            await write_metadata(item.connection, game_title, resolved_game_path)
            item.game_path = resolved_game_path
            logger.warning(
                f"[tag.warning]检测到同标题游戏路径变化，已更新数据库绑定路径[/tag.warning] 标题 [tag.count]{game_title}[/tag.count] 新路径 [tag.path]{resolved_game_path}[/tag.path]"
            )
            return

        db_path = build_db_path(game_title, self.db_directory)
        db_already_exists = db_path.exists()
        connection = await open_connection(db_path)
        try:
            if db_already_exists:
                await check_connection_readable(connection=connection, db_path=db_path)
                _ = await read_metadata(connection=connection, db_path=db_path)
                await create_static_tables(connection)
            else:
                await create_static_tables(connection)
            await write_metadata(connection, game_title, resolved_game_path)
        except Exception:
            await connection.close()
            if not db_already_exists and db_path.exists():
                db_path.unlink(missing_ok=True)
            raise

        self.items[game_title] = GameDatabaseItem(
            game_title=game_title,
            game_path=resolved_game_path,
            db_path=db_path,
            connection=connection,
        )

    async def write_translation_items(
        self,
        game_title: str,
        items: Sequence[tuple[str, ItemType, str | None, list[str], list[str]]],
    ) -> None:
        """批量写入已完成译文到主翻译表。"""
        item = self._get_item(game_title)
        if items:
            serialized_items = [
                (
                    location_path,
                    item_type,
                    role,
                    json.dumps(original_lines, ensure_ascii=False),
                    json.dumps(translation_lines, ensure_ascii=False),
                )
                for location_path, item_type, role, original_lines, translation_lines in items
            ]
            _ = await item.connection.executemany(INSERT_TRANSLATION, serialized_items)
        await item.connection.commit()

    async def read_translation_location_paths(self, game_title: str) -> set[str]:
        """读取主翻译表中的全部已完成路径。"""
        item = self._get_item(game_title)
        async with item.connection.execute(SELECT_TRANSLATION_PATHS) as cursor:
            rows = await cursor.fetchall()
        return {row_str(row, "location_path", item.db_path) for row in rows}

    async def read_translated_items(self, game_title: str) -> list[TranslationItem]:
        """读取主翻译表中的全部正文译文。"""
        item = self._get_item(game_title)
        async with item.connection.execute(SELECT_TRANSLATED_ITEMS) as cursor:
            rows = await cursor.fetchall()

        translated_items: list[TranslationItem] = []
        for row in rows:
            original_lines = decode_string_list(row_str(row, "original_lines", item.db_path), "original_lines")
            translation_lines = decode_string_list(row_str(row, "translation_lines", item.db_path), "translation_lines")
            translated_items.append(
                TranslationItem(
                    location_path=row_str(row, "location_path", item.db_path),
                    item_type=row_item_type(row, "item_type", item.db_path),
                    role=row_optional_str(row, "role", item.db_path),
                    original_lines=original_lines,
                    translation_lines=translation_lines,
                )
            )
        return translated_items

    async def read_plugin_text_rules(self, game_title: str) -> list[PluginTextRuleRecord]:
        """读取当前游戏保存的全部插件文本规则快照。"""
        item = self._get_item(game_title)
        async with item.connection.execute(SELECT_PLUGIN_TEXT_RULES) as cursor:
            rows = await cursor.fetchall()

        records: list[PluginTextRuleRecord] = []
        for row in rows:
            translate_rules = decode_plugin_translate_rules(
                row_str(row, "translate_rules_json", item.db_path)
            )
            records.append(
                PluginTextRuleRecord(
                    plugin_index=row_int(row, "plugin_index", item.db_path),
                    plugin_name=row_str(row, "plugin_name", item.db_path),
                    plugin_hash=row_str(row, "plugin_hash", item.db_path),
                    plugin_reason=row_str(row, "plugin_reason", item.db_path),
                    translate_rules=translate_rules,
                    imported_at=row_str(row, "imported_at", item.db_path),
                )
            )
        return records

    async def upsert_plugin_text_rule(self, game_title: str, rule_record: PluginTextRuleRecord) -> None:
        """写入或更新单个插件的文本路径规则快照。"""
        item = self._get_item(game_title)
        _ = await item.connection.execute(
            UPSERT_PLUGIN_TEXT_RULE,
            (
                rule_record.plugin_index,
                rule_record.plugin_name,
                rule_record.plugin_hash,
                rule_record.plugin_reason,
                json.dumps(
                    [rule.model_dump(mode="json") for rule in rule_record.translate_rules],
                    ensure_ascii=False,
                ),
                rule_record.imported_at,
            ),
        )
        await item.connection.commit()

    async def replace_plugin_text_rules(
        self,
        game_title: str,
        rule_records: list[PluginTextRuleRecord],
    ) -> None:
        """用一次外部导入结果替换当前游戏的全部插件文本规则。"""
        item = self._get_item(game_title)
        _ = await item.connection.execute(DELETE_ALL_PLUGIN_TEXT_RULES)
        for rule_record in rule_records:
            _ = await item.connection.execute(
                UPSERT_PLUGIN_TEXT_RULE,
                (
                    rule_record.plugin_index,
                    rule_record.plugin_name,
                    rule_record.plugin_hash,
                    rule_record.plugin_reason,
                    json.dumps(
                        [rule.model_dump(mode="json") for rule in rule_record.translate_rules],
                        ensure_ascii=False,
                    ),
                    rule_record.imported_at,
                ),
            )
        await item.connection.commit()

    async def replace_name_context_registry(
        self,
        game_title: str,
        registry: NameContextRegistry,
    ) -> None:
        """用一次外部导入结果替换当前游戏的全部术语表条目。"""
        item = self._get_item(game_title)
        updated_at = registry.generated_at
        _ = await item.connection.execute(DELETE_ALL_NAME_CONTEXT_ENTRIES)
        for entry in registry.entries:
            _ = await item.connection.execute(
                INSERT_NAME_CONTEXT_ENTRY,
                (
                    entry.entry_id,
                    entry.kind,
                    entry.source_text,
                    entry.translated_text,
                    json.dumps(
                        [location.model_dump(mode="json") for location in entry.locations],
                        ensure_ascii=False,
                    ),
                    entry.note,
                    updated_at,
                ),
            )
        await item.connection.commit()

    async def read_name_context_registry(self, game_title: str) -> NameContextRegistry | None:
        """从数据库读取当前游戏已导入的术语表。"""
        item = self._get_item(game_title)
        async with item.connection.execute(SELECT_NAME_CONTEXT_ENTRIES) as cursor:
            rows = await cursor.fetchall()
        if not rows:
            return None

        entries: list[NameRegistryEntry] = []
        generated_at = ""
        for row in rows:
            if not generated_at:
                generated_at = row_str(row, "updated_at", item.db_path)
            entries.append(
                NameRegistryEntry(
                    entry_id=row_str(row, "entry_id", item.db_path),
                    kind=_read_name_entry_kind(row_str(row, "kind", item.db_path), item.db_path),
                    source_text=row_str(row, "source_text", item.db_path),
                    translated_text=row_str(row, "translated_text", item.db_path),
                    locations=decode_name_locations(row_str(row, "locations_json", item.db_path)),
                    note=row_str(row, "note", item.db_path),
                )
            )
        return NameContextRegistry(
            game_title=game_title,
            generated_at=generated_at,
            entries=entries,
        )

    async def delete_translation_items_by_prefixes(self, game_title: str, prefixes: list[str]) -> int:
        """按路径前缀批量删除主翻译表中的记录。"""
        item = self._get_item(game_title)
        deleted_rows = 0
        for prefix in prefixes:
            cursor = await item.connection.execute(
                DELETE_TRANSLATION_ITEMS_BY_PREFIX,
                (f"{prefix}%",),
            )
            if cursor.rowcount > 0:
                deleted_rows += cursor.rowcount
        await item.connection.commit()
        return deleted_rows

    async def read_error_table_names(self, game_title: str, prefix: str = DEFAULT_ERROR_TABLE_PREFIX) -> list[str]:
        """读取指定前缀下的全部错误表名。"""
        item = self._get_item(game_title)
        async with item.connection.execute(SELECT_TABLE_NAMES_BY_PREFIX, (f"{prefix}_%",)) as cursor:
            rows = await cursor.fetchall()
        return [row_str(row, "name", item.db_path) for row in rows]

    async def delete_error_tables(self, game_title: str, table_names: list[str]) -> int:
        """批量删除指定错误表。"""
        item = self._get_item(game_title)
        unique_table_names = [name for name in dict.fromkeys(table_names) if name]
        if not unique_table_names:
            return 0

        async with item.connection.execute(SELECT_TABLE_NAMES_BY_PREFIX, ("%",)) as cursor:
            rows = await cursor.fetchall()
        existing_table_names = {row_str(row, "name", item.db_path) for row in rows}
        deletable_table_names = [name for name in unique_table_names if name in existing_table_names]
        if not deletable_table_names:
            return 0

        for table_name in deletable_table_names:
            _ = await item.connection.execute(DROP_TABLE.format(table_name=table_name))
        await item.connection.commit()
        return len(deletable_table_names)

    async def start_error_table(self, game_title: str, prefix: str = DEFAULT_ERROR_TABLE_PREFIX) -> str:
        """创建当前翻译任务使用的错误表，并返回新表名。"""
        item = self._get_item(game_title)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        table_name = f"{prefix}_{timestamp}"
        _ = await item.connection.execute(CREATE_ERROR_TABLE.format(table_name=table_name))
        await item.connection.commit()
        return table_name

    async def write_error_items(
        self,
        game_title: str,
        table_name: str,
        items: list[TranslationErrorItem],
    ) -> None:
        """向指定错误表追加写入错误数据。"""
        item = self._get_item(game_title)
        if items:
            serialized_items = [
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
            _ = await item.connection.executemany(INSERT_ERROR.format(table_name=table_name), serialized_items)
        await item.connection.commit()

    async def read_table(self, game_title: str, table_name: str) -> list[dict[str, object]]:
        """读取指定表的所有数据，并转成字典列表。"""
        item = self._get_item(game_title)
        async with item.connection.execute(SELECT_ALL.format(table_name=table_name)) as cursor:
            rows = await cursor.fetchall()
        return [row_to_dict(row) for row in rows]

    async def close(self) -> None:
        """关闭当前管理器持有的全部数据库连接。"""
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
        """根据游戏标题查找已加载的数据库对象。"""
        item = self.items.get(game_title)
        if item is None:
            raise ValueError(f"未找到游戏数据库: {game_title}")
        return item

__all__: list[str] = [
    "DB_DIRECTORY",
    "DEFAULT_ERROR_TABLE_PREFIX",
    "GameDatabaseItem",
    "GameDatabaseManager",
    "build_db_path",
    "ensure_db_directory",
]
