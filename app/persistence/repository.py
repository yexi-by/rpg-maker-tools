"""多游戏数据库管理模块。"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Self

import aiosqlite

from app.name_context.schemas import NameContextRegistry
from app.rmmz.schema import (
    EventCommandParameterFilter,
    EventCommandTextRuleRecord,
    ErrorType,
    FontReplacementRecord,
    GameData,
    JapaneseResidualRuleRecord,
    LlmFailureCategory,
    LlmFailureRecord,
    NoteTagTextRuleRecord,
    PlaceholderRuleRecord,
    PluginTextRuleRecord,
    TranslationErrorItem,
    TranslationItem,
    TranslationRunRecord,
    TranslationRunStatus,
)
from app.rmmz.loader import read_game_title, resolve_game_directory
from app.observability.logging import logger

from .rows import (
    decode_string_list,
    row_int,
    row_item_type,
    row_optional_str,
    row_str,
)
from .sql import (
    CHECK_CONNECTION_READABLE,
    CREATE_EVENT_COMMAND_TEXT_RULE_FILTERS_TABLE,
    CREATE_EVENT_COMMAND_TEXT_RULE_GROUPS_TABLE,
    CREATE_EVENT_COMMAND_TEXT_RULE_PATHS_TABLE,
    CREATE_FONT_REPLACEMENT_RECORDS_TABLE,
    CREATE_JAPANESE_RESIDUAL_RULES_TABLE,
    CREATE_LLM_FAILURES_TABLE,
    CREATE_METADATA_TABLE,
    CREATE_NAME_CONTEXT_TERMS_TABLE,
    CREATE_NOTE_TAG_TEXT_RULES_TABLE,
    CREATE_PLACEHOLDER_RULES_TABLE,
    CREATE_PLUGIN_TEXT_RULES_TABLE,
    CREATE_TRANSLATION_QUALITY_ERRORS_TABLE,
    CREATE_TRANSLATION_RUNS_TABLE,
    CREATE_TRANSLATION_TABLE,
    DELETE_ALL_EVENT_COMMAND_TEXT_RULE_FILTERS,
    DELETE_ALL_EVENT_COMMAND_TEXT_RULE_GROUPS,
    DELETE_ALL_EVENT_COMMAND_TEXT_RULE_PATHS,
    DELETE_ALL_FONT_REPLACEMENT_RECORDS,
    DELETE_ALL_JAPANESE_RESIDUAL_RULES,
    DELETE_ALL_NAME_CONTEXT_TERMS,
    DELETE_ALL_NOTE_TAG_TEXT_RULES,
    DELETE_ALL_PLACEHOLDER_RULES,
    DELETE_ALL_PLUGIN_TEXT_RULES,
    DELETE_ALL_TRANSLATION_QUALITY_ERRORS,
    DELETE_TRANSLATION_ITEM_BY_PATH,
    DELETE_TRANSLATION_ITEMS_BY_PREFIX,
    INSERT_EVENT_COMMAND_TEXT_RULE_FILTER,
    INSERT_EVENT_COMMAND_TEXT_RULE_GROUP,
    INSERT_EVENT_COMMAND_TEXT_RULE_PATH,
    INSERT_FONT_REPLACEMENT_RECORD,
    INSERT_JAPANESE_RESIDUAL_RULE,
    INSERT_LLM_FAILURE,
    INSERT_NAME_CONTEXT_TERM,
    INSERT_NOTE_TAG_TEXT_RULE,
    INSERT_PLACEHOLDER_RULE,
    INSERT_PLUGIN_TEXT_RULE,
    INSERT_TRANSLATION_QUALITY_ERROR,
    INSERT_TRANSLATION,
    METADATA_KEY,
    SELECT_LATEST_TRANSLATION_RUN,
    SELECT_FONT_REPLACEMENT_RECORDS,
    SELECT_JAPANESE_RESIDUAL_RULES,
    SELECT_LLM_FAILURES_BY_RUN,
    SELECT_EVENT_COMMAND_TEXT_RULE_FILTERS,
    SELECT_EVENT_COMMAND_TEXT_RULE_GROUPS,
    SELECT_EVENT_COMMAND_TEXT_RULE_PATHS,
    SELECT_METADATA,
    SELECT_NAME_CONTEXT_TERMS,
    SELECT_NOTE_TAG_TEXT_RULES,
    SELECT_PLACEHOLDER_RULES,
    SELECT_PLUGIN_TEXT_RULES,
    SELECT_TRANSLATION_QUALITY_ERRORS_BY_RUN,
    SELECT_TRANSLATION_RUN,
    SELECT_TRANSLATED_ITEMS,
    SELECT_TRANSLATION_PATHS,
    TRANSLATION_QUALITY_ERRORS_TABLE_NAME,
    UPSERT_METADATA,
    UPSERT_TRANSLATION_RUN,
)

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
    _ = await connection.execute("PRAGMA foreign_keys = ON")
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
    _ = await connection.execute(CREATE_NOTE_TAG_TEXT_RULES_TABLE)
    _ = await connection.execute(CREATE_EVENT_COMMAND_TEXT_RULE_GROUPS_TABLE)
    _ = await connection.execute(CREATE_EVENT_COMMAND_TEXT_RULE_FILTERS_TABLE)
    _ = await connection.execute(CREATE_EVENT_COMMAND_TEXT_RULE_PATHS_TABLE)
    _ = await connection.execute(CREATE_NAME_CONTEXT_TERMS_TABLE)
    _ = await connection.execute(CREATE_PLACEHOLDER_RULES_TABLE)
    _ = await connection.execute(CREATE_JAPANESE_RESIDUAL_RULES_TABLE)
    _ = await connection.execute(CREATE_FONT_REPLACEMENT_RECORDS_TABLE)
    _ = await connection.execute(CREATE_TRANSLATION_RUNS_TABLE)
    _ = await connection.execute(CREATE_LLM_FAILURES_TABLE)
    _ = await connection.execute(CREATE_TRANSLATION_QUALITY_ERRORS_TABLE)
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
        raise RuntimeError(f"数据库 metadata 表不可读，请重新注册游戏: {db_path}") from error

    if row is None:
        raise RuntimeError(f"数据库缺少 metadata 元数据记录: {db_path}")

    game_title = row_str(row, "game_title", db_path)
    game_path = row_str(row, "game_path", db_path)
    if not game_title.strip():
        raise RuntimeError(f"metadata.game_title 非法: {db_path}")
    if not game_path.strip():
        raise RuntimeError(f"metadata.game_path 非法: {db_path}")
    return game_title.strip(), Path(game_path).resolve()


@dataclass(slots=True)
class GameRecord:
    """单个已注册游戏的数据库元数据。"""

    game_title: str
    game_path: Path
    db_path: Path


class GameRegistry:
    """游戏注册表，负责发现、注册和打开目标游戏数据库。"""

    def __init__(self, db_directory: Path = DB_DIRECTORY) -> None:
        """初始化注册表。"""
        self.db_directory: Path = db_directory

    async def list_games(self) -> list[GameRecord]:
        """扫描数据库目录并读取每个数据库的元数据。"""
        ensure_db_directory(self.db_directory)
        records: list[GameRecord] = []
        for db_path in sorted(self.db_directory.glob("*.db")):
            connection = await open_connection(db_path)
            try:
                await check_connection_readable(connection=connection, db_path=db_path)
                game_title, game_path = await read_metadata(connection=connection, db_path=db_path)
                records.append(
                    GameRecord(
                        game_title=game_title,
                        game_path=game_path,
                        db_path=db_path,
                    )
                )
            finally:
                await connection.close()
        return sorted(records, key=lambda record: record.game_title)

    async def register_game(self, game_path: str | Path) -> GameRecord:
        """创建或更新单个游戏数据库绑定。"""
        ensure_db_directory(self.db_directory)
        resolved_game_path = resolve_game_directory(game_path)
        game_title = read_game_title(resolved_game_path)
        db_path = build_db_path(game_title, self.db_directory)
        db_already_exists = db_path.exists()
        connection = await open_connection(db_path)
        previous_game_path: Path | None = None
        try:
            if db_already_exists:
                await check_connection_readable(connection=connection, db_path=db_path)
                previous_game_title, previous_game_path = await read_metadata(
                    connection=connection,
                    db_path=db_path,
                )
                if previous_game_title != game_title:
                    raise RuntimeError(
                        f"数据库元数据标题与文件名目标不一致: {db_path}"
                    )
            await create_static_tables(connection)
            await write_metadata(connection, game_title, resolved_game_path)
        except Exception:
            await connection.close()
            if not db_already_exists and db_path.exists():
                db_path.unlink(missing_ok=True)
            raise

        await connection.close()
        if previous_game_path is not None and previous_game_path != resolved_game_path:
            logger.warning(
                f"[tag.warning]检测到同标题游戏路径变化，已更新数据库绑定路径[/tag.warning] 标题 [tag.count]{game_title}[/tag.count] 新路径 [tag.path]{resolved_game_path}[/tag.path]"
            )
        return GameRecord(
            game_title=game_title,
            game_path=resolved_game_path,
            db_path=db_path,
        )

    async def open_game(self, game_title: str) -> "TargetGameSession":
        """打开目标游戏数据库，返回命令级会话。"""
        ensure_db_directory(self.db_directory)
        db_path = build_db_path(game_title, self.db_directory)
        if not db_path.exists():
            raise ValueError(f"未找到游戏数据库: {game_title}")

        connection = await open_connection(db_path)
        try:
            await check_connection_readable(connection=connection, db_path=db_path)
            await create_static_tables(connection)
            metadata_title, game_path = await read_metadata(
                connection=connection,
                db_path=db_path,
            )
            if metadata_title != game_title:
                raise RuntimeError(
                    f"数据库元数据标题不匹配: 期望 {game_title}，实际 {metadata_title}"
                )
            return TargetGameSession(
                record=GameRecord(
                    game_title=metadata_title,
                    game_path=game_path,
                    db_path=db_path,
                ),
                connection=connection,
            )
        except Exception:
            await connection.close()
            raise

    async def resolve_registered_title_by_path(self, game_path: str | Path) -> str:
        """根据已注册游戏目录解析数据库中的游戏标题。"""
        resolved_game_path = resolve_game_directory(game_path)
        for record in await self.list_games():
            if record.game_path == resolved_game_path:
                return record.game_title
        title = read_game_title(resolved_game_path)
        raise ValueError(f"游戏目录尚未注册，请先执行 add-game: {title}")


class TargetGameSession:
    """单个目标游戏的数据库会话。"""

    def __init__(self, record: GameRecord, connection: aiosqlite.Connection) -> None:
        """初始化单游戏数据库会话。"""
        self.record: GameRecord = record
        self.connection: aiosqlite.Connection = connection
        self.game_data: GameData | None = None

    @property
    def game_title(self) -> str:
        """返回当前会话绑定的游戏标题。"""
        return self.record.game_title

    @property
    def game_path(self) -> Path:
        """返回当前会话绑定的游戏目录。"""
        return self.record.game_path

    @property
    def db_path(self) -> Path:
        """返回当前会话绑定的数据库路径。"""
        return self.record.db_path

    async def __aenter__(self) -> Self:
        """进入命令级数据库会话。"""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """退出命令级数据库会话并关闭连接。"""
        await self.close()

    def set_game_data(self, game_data: GameData) -> None:
        """把当前命令已加载的游戏数据绑定到会话。"""
        self.game_data = game_data

    def require_game_data(self) -> GameData:
        """读取当前会话已加载的游戏数据。"""
        if self.game_data is None:
            raise RuntimeError("当前命令尚未加载游戏数据")
        return self.game_data

    async def write_translation_items(
        self,
        items: Sequence[TranslationItem],
    ) -> None:
        """批量写入已完成译文到主翻译表。"""
        if items:
            serialized_items = [
                (
                    translation_item.location_path,
                    translation_item.item_type,
                    translation_item.role,
                    json.dumps(translation_item.original_lines, ensure_ascii=False),
                    json.dumps(translation_item.source_line_paths, ensure_ascii=False),
                    json.dumps(translation_item.translation_lines, ensure_ascii=False),
                )
                for translation_item in items
            ]
            _ = await self.connection.executemany(INSERT_TRANSLATION, serialized_items)
        await self.connection.commit()

    async def read_translation_location_paths(self) -> set[str]:
        """读取主翻译表中的全部已完成路径。"""
        async with self.connection.execute(SELECT_TRANSLATION_PATHS) as cursor:
            rows = await cursor.fetchall()
        return {row_str(row, "location_path", self.db_path) for row in rows}

    async def read_translated_items(self) -> list[TranslationItem]:
        """读取主翻译表中的全部正文译文。"""
        async with self.connection.execute(SELECT_TRANSLATED_ITEMS) as cursor:
            rows = await cursor.fetchall()

        translated_items: list[TranslationItem] = []
        for row in rows:
            original_lines = decode_string_list(row_str(row, "original_lines", self.db_path), "original_lines")
            source_line_paths = decode_string_list(
                row_str(row, "source_line_paths", self.db_path),
                "source_line_paths",
            )
            translation_lines = decode_string_list(
                row_str(row, "translation_lines", self.db_path),
                "translation_lines",
            )
            translated_items.append(
                TranslationItem(
                    location_path=row_str(row, "location_path", self.db_path),
                    item_type=row_item_type(row, "item_type", self.db_path),
                    role=row_optional_str(row, "role", self.db_path),
                    original_lines=original_lines,
                    source_line_paths=source_line_paths,
                    translation_lines=translation_lines,
                )
            )
        return translated_items

    async def read_plugin_text_rules(self) -> list[PluginTextRuleRecord]:
        """读取当前游戏保存的全部插件文本规则。"""
        async with self.connection.execute(SELECT_PLUGIN_TEXT_RULES) as cursor:
            rows = await cursor.fetchall()

        grouped_records: dict[int, PluginTextRuleRecord] = {}
        for row in rows:
            plugin_index = row_int(row, "plugin_index", self.db_path)
            record = grouped_records.get(plugin_index)
            if record is None:
                record = PluginTextRuleRecord(
                    plugin_index=plugin_index,
                    plugin_name=row_str(row, "plugin_name", self.db_path),
                    plugin_hash=row_str(row, "plugin_hash", self.db_path),
                    path_templates=[],
                )
                grouped_records[plugin_index] = record
            record.path_templates.append(row_str(row, "path_template", self.db_path))
        return list(grouped_records.values())

    async def replace_plugin_text_rules(
        self,
        rule_records: list[PluginTextRuleRecord],
    ) -> None:
        """用一次外部导入结果替换当前游戏的全部插件文本规则。"""
        _ = await self.connection.execute(DELETE_ALL_PLUGIN_TEXT_RULES)
        for rule_record in rule_records:
            for path_template in rule_record.path_templates:
                _ = await self.connection.execute(
                    INSERT_PLUGIN_TEXT_RULE,
                    (
                        rule_record.plugin_index,
                        rule_record.plugin_name,
                        rule_record.plugin_hash,
                        path_template,
                    ),
                )
        await self.connection.commit()

    async def read_note_tag_text_rules(self) -> list[NoteTagTextRuleRecord]:
        """读取当前游戏保存的 Note 标签文本规则。"""
        async with self.connection.execute(SELECT_NOTE_TAG_TEXT_RULES) as cursor:
            rows = await cursor.fetchall()

        grouped_records: dict[str, NoteTagTextRuleRecord] = {}
        for row in rows:
            file_name = row_str(row, "file_name", self.db_path)
            record = grouped_records.get(file_name)
            if record is None:
                record = NoteTagTextRuleRecord(file_name=file_name, tag_names=[])
                grouped_records[file_name] = record
            record.tag_names.append(row_str(row, "tag_name", self.db_path))
        return list(grouped_records.values())

    async def replace_note_tag_text_rules(
        self,
        rule_records: list[NoteTagTextRuleRecord],
    ) -> None:
        """用一次外部导入结果替换当前游戏的 Note 标签文本规则。"""
        _ = await self.connection.execute(DELETE_ALL_NOTE_TAG_TEXT_RULES)
        for rule_record in rule_records:
            for tag_name in rule_record.tag_names:
                _ = await self.connection.execute(
                    INSERT_NOTE_TAG_TEXT_RULE,
                    (
                        rule_record.file_name,
                        tag_name,
                    ),
                )
        await self.connection.commit()

    async def read_event_command_text_rules(self) -> list[EventCommandTextRuleRecord]:
        """读取当前游戏保存的事件指令文本规则。"""
        async with self.connection.execute(SELECT_EVENT_COMMAND_TEXT_RULE_GROUPS) as cursor:
            group_rows = await cursor.fetchall()
        async with self.connection.execute(SELECT_EVENT_COMMAND_TEXT_RULE_FILTERS) as cursor:
            filter_rows = await cursor.fetchall()
        async with self.connection.execute(SELECT_EVENT_COMMAND_TEXT_RULE_PATHS) as cursor:
            path_rows = await cursor.fetchall()

        filters_by_group: dict[str, list[EventCommandParameterFilter]] = {}
        for row in filter_rows:
            group_key = row_str(row, "group_key", self.db_path)
            filters_by_group.setdefault(group_key, []).append(
                EventCommandParameterFilter(
                    index=row_int(row, "parameter_index", self.db_path),
                    value=row_str(row, "parameter_value", self.db_path),
                )
            )

        paths_by_group: dict[str, list[str]] = {}
        for row in path_rows:
            group_key = row_str(row, "group_key", self.db_path)
            paths_by_group.setdefault(group_key, []).append(row_str(row, "path_template", self.db_path))

        records: list[EventCommandTextRuleRecord] = []
        for row in group_rows:
            group_key = row_str(row, "group_key", self.db_path)
            records.append(
                EventCommandTextRuleRecord(
                    command_code=row_int(row, "command_code", self.db_path),
                    parameter_filters=filters_by_group.get(group_key, []),
                    path_templates=paths_by_group.get(group_key, []),
                )
            )
        return records

    async def replace_event_command_text_rules(
        self,
        rule_records: list[EventCommandTextRuleRecord],
    ) -> None:
        """用一次外部导入结果替换当前游戏的事件指令文本规则。"""
        _ = await self.connection.execute(DELETE_ALL_EVENT_COMMAND_TEXT_RULE_PATHS)
        _ = await self.connection.execute(DELETE_ALL_EVENT_COMMAND_TEXT_RULE_FILTERS)
        _ = await self.connection.execute(DELETE_ALL_EVENT_COMMAND_TEXT_RULE_GROUPS)
        for rule_record in rule_records:
            group_key = build_event_command_group_key(rule_record)
            _ = await self.connection.execute(
                INSERT_EVENT_COMMAND_TEXT_RULE_GROUP,
                (group_key, rule_record.command_code),
            )
            for parameter_filter in rule_record.parameter_filters:
                _ = await self.connection.execute(
                    INSERT_EVENT_COMMAND_TEXT_RULE_FILTER,
                    (group_key, parameter_filter.index, parameter_filter.value),
                )
            for path_template in rule_record.path_templates:
                _ = await self.connection.execute(
                    INSERT_EVENT_COMMAND_TEXT_RULE_PATH,
                    (group_key, path_template),
                )
        await self.connection.commit()

    async def replace_name_context_registry(
        self,
        registry: NameContextRegistry,
    ) -> None:
        """用一次外部导入结果替换当前游戏的全部术语表条目。"""
        _ = await self.connection.execute(DELETE_ALL_NAME_CONTEXT_TERMS)
        for source_text, translated_text in registry.speaker_names.items():
            _ = await self.connection.execute(
                INSERT_NAME_CONTEXT_TERM,
                ("speaker_name", source_text, translated_text),
            )
        for source_text, translated_text in registry.map_display_names.items():
            _ = await self.connection.execute(
                INSERT_NAME_CONTEXT_TERM,
                ("map_display_name", source_text, translated_text),
            )
        await self.connection.commit()

    async def read_name_context_registry(self) -> NameContextRegistry | None:
        """从数据库读取当前游戏已导入的术语表。"""
        async with self.connection.execute(SELECT_NAME_CONTEXT_TERMS) as cursor:
            rows = await cursor.fetchall()
        if not rows:
            return None

        speaker_names: dict[str, str] = {}
        map_display_names: dict[str, str] = {}
        for row in rows:
            kind = row_str(row, "kind", self.db_path)
            source_text = row_str(row, "source_text", self.db_path)
            translated_text = row_str(row, "translated_text", self.db_path)
            if kind == "speaker_name":
                speaker_names[source_text] = translated_text
            elif kind == "map_display_name":
                map_display_names[source_text] = translated_text
            else:
                raise TypeError(f"数据库字段 kind 不是有效术语类型: {self.db_path}")
        return NameContextRegistry(
            speaker_names=speaker_names,
            map_display_names=map_display_names,
        )

    async def replace_placeholder_rules(
        self,
        rules: list[PlaceholderRuleRecord],
    ) -> None:
        """用当前游戏专用规则替换数据库中的自定义占位符规则。"""
        _ = await self.connection.execute(DELETE_ALL_PLACEHOLDER_RULES)
        for rule in rules:
            _ = await self.connection.execute(
                INSERT_PLACEHOLDER_RULE,
                (rule.pattern_text, rule.placeholder_template),
            )
        await self.connection.commit()

    async def read_placeholder_rules(self) -> list[PlaceholderRuleRecord]:
        """读取当前游戏专用自定义占位符规则。"""
        async with self.connection.execute(SELECT_PLACEHOLDER_RULES) as cursor:
            rows = await cursor.fetchall()
        return [
            PlaceholderRuleRecord(
                pattern_text=row_str(row, "pattern_text", self.db_path),
                placeholder_template=row_str(row, "placeholder_template", self.db_path),
            )
            for row in rows
        ]

    async def replace_japanese_residual_rules(
        self,
        rules: list[JapaneseResidualRuleRecord],
    ) -> None:
        """用当前游戏专用规则替换日文残留例外规则。"""
        _ = await self.connection.execute(DELETE_ALL_JAPANESE_RESIDUAL_RULES)
        for rule in rules:
            _ = await self.connection.execute(
                INSERT_JAPANESE_RESIDUAL_RULE,
                (
                    rule.location_path,
                    json.dumps(rule.allowed_terms, ensure_ascii=False),
                    rule.reason,
                ),
            )
        await self.connection.commit()

    async def read_japanese_residual_rules(self) -> list[JapaneseResidualRuleRecord]:
        """读取当前游戏专用日文残留例外规则。"""
        async with self.connection.execute(SELECT_JAPANESE_RESIDUAL_RULES) as cursor:
            rows = await cursor.fetchall()
        return [
            JapaneseResidualRuleRecord(
                location_path=row_str(row, "location_path", self.db_path),
                allowed_terms=decode_string_list(
                    row_str(row, "allowed_terms", self.db_path),
                    "allowed_terms",
                ),
                reason=row_str(row, "reason", self.db_path),
            )
            for row in rows
        ]

    async def replace_font_replacement_records(
        self,
        records: Sequence[FontReplacementRecord],
    ) -> None:
        """用本次字体覆盖记录替换当前游戏的候选覆盖字体记录。"""
        _ = await self.connection.execute(DELETE_ALL_FONT_REPLACEMENT_RECORDS)
        if records:
            serialized_records = [
                (
                    record.file_name,
                    record.value_path,
                    record.original_text,
                    record.replaced_text,
                    record.replacement_font_name,
                )
                for record in records
            ]
            _ = await self.connection.executemany(
                INSERT_FONT_REPLACEMENT_RECORD,
                serialized_records,
            )
        await self.connection.commit()

    async def read_font_replacement_records(self) -> list[FontReplacementRecord]:
        """读取当前游戏最近一次字体覆盖产生的候选覆盖字体记录。"""
        async with self.connection.execute(SELECT_FONT_REPLACEMENT_RECORDS) as cursor:
            rows = await cursor.fetchall()
        return [
            FontReplacementRecord(
                file_name=row_str(row, "file_name", self.db_path),
                value_path=row_str(row, "value_path", self.db_path),
                original_text=row_str(row, "original_text", self.db_path),
                replaced_text=row_str(row, "replaced_text", self.db_path),
                replacement_font_name=row_str(row, "replacement_font_name", self.db_path),
            )
            for row in rows
        ]

    async def clear_font_replacement_records(self) -> int:
        """清空当前游戏已经完成处理的字体覆盖记录。"""
        cursor = await self.connection.execute(DELETE_ALL_FONT_REPLACEMENT_RECORDS)
        await self.connection.commit()
        return max(cursor.rowcount, 0)

    async def start_translation_run(
        self,
        *,
        total_extracted: int,
        pending_count: int,
        deduplicated_count: int,
        batch_count: int,
    ) -> TranslationRunRecord:
        """创建新的正文翻译运行状态。"""
        _ = await self.connection.execute(DELETE_ALL_TRANSLATION_QUALITY_ERRORS)
        now = current_timestamp_text()
        run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
        record = TranslationRunRecord(
            run_id=run_id,
            status="running",
            total_extracted=total_extracted,
            pending_count=pending_count,
            deduplicated_count=deduplicated_count,
            batch_count=batch_count,
            success_count=0,
            quality_error_count=0,
            llm_failure_count=0,
            started_at=now,
            updated_at=now,
            finished_at=None,
            stop_reason="",
            last_error="",
        )
        await self.write_translation_run(record)
        return record

    async def write_translation_run(self, record: TranslationRunRecord) -> None:
        """写入正文翻译运行状态快照。"""
        _ = await self.connection.execute(
            UPSERT_TRANSLATION_RUN,
            (
                record.run_id,
                record.status,
                record.total_extracted,
                record.pending_count,
                record.deduplicated_count,
                record.batch_count,
                record.success_count,
                record.quality_error_count,
                record.llm_failure_count,
                record.started_at,
                current_timestamp_text(),
                record.finished_at,
                record.stop_reason,
                record.last_error,
            ),
        )
        await self.connection.commit()

    async def read_latest_translation_run(self) -> TranslationRunRecord | None:
        """读取最新正文翻译运行状态。"""
        async with self.connection.execute(SELECT_LATEST_TRANSLATION_RUN) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._decode_translation_run(row)

    async def read_translation_run(self, run_id: str) -> TranslationRunRecord | None:
        """按运行 ID 读取正文翻译状态。"""
        async with self.connection.execute(SELECT_TRANSLATION_RUN, (run_id,)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._decode_translation_run(row)

    async def write_llm_failure(self, failure: LlmFailureRecord) -> None:
        """写入运行级模型故障。"""
        _ = await self.connection.execute(
            INSERT_LLM_FAILURE,
            (
                failure.run_id,
                failure.category,
                failure.error_type,
                failure.error_message,
                1 if failure.retryable else 0,
                failure.attempt_count,
                failure.created_at,
            ),
        )
        await self.connection.commit()

    async def read_llm_failures(self, run_id: str) -> list[LlmFailureRecord]:
        """读取指定运行的模型故障记录。"""
        async with self.connection.execute(SELECT_LLM_FAILURES_BY_RUN, (run_id,)) as cursor:
            rows = await cursor.fetchall()
        return [
            LlmFailureRecord(
                run_id=row_str(row, "run_id", self.db_path),
                category=parse_llm_failure_category(row_str(row, "category", self.db_path), self.db_path),
                error_type=row_str(row, "error_type", self.db_path),
                error_message=row_str(row, "error_message", self.db_path),
                retryable=row_int(row, "retryable", self.db_path) == 1,
                attempt_count=row_int(row, "attempt_count", self.db_path),
                created_at=row_str(row, "created_at", self.db_path),
            )
            for row in rows
        ]

    async def write_translation_quality_errors(
        self,
        run_id: str,
        items: list[TranslationErrorItem],
    ) -> None:
        """写入没通过项目检查的最终译文。"""
        if items:
            serialized_items = [
                (
                    run_id,
                    error_item.location_path,
                    error_item.item_type,
                    error_item.role,
                    json.dumps(error_item.original_lines, ensure_ascii=False),
                    json.dumps(error_item.translation_lines, ensure_ascii=False),
                    error_item.error_type,
                    json.dumps(error_item.error_detail, ensure_ascii=False),
                    error_item.model_response,
                )
                for error_item in items
            ]
            _ = await self.connection.executemany(
                INSERT_TRANSLATION_QUALITY_ERROR,
                serialized_items,
            )
        await self.connection.commit()

    async def read_translation_quality_errors(self, run_id: str) -> list[TranslationErrorItem]:
        """读取指定运行中没通过项目检查的最终译文。"""
        async with self.connection.execute(SELECT_TRANSLATION_QUALITY_ERRORS_BY_RUN, (run_id,)) as cursor:
            rows = await cursor.fetchall()
        return [
            TranslationErrorItem(
                location_path=row_str(row, "location_path", self.db_path),
                item_type=row_item_type(row, "item_type", self.db_path),
                role=row_optional_str(row, "role", self.db_path),
                original_lines=decode_string_list(row_str(row, "original_lines", self.db_path), "original_lines"),
                translation_lines=decode_string_list(
                    row_str(row, "translation_lines", self.db_path),
                    "translation_lines",
                ),
                error_type=parse_error_type(row_str(row, "error_type", self.db_path), self.db_path),
                error_detail=decode_string_list(row_str(row, "error_detail", self.db_path), "error_detail"),
                model_response=row_str(row, "model_response", self.db_path),
            )
            for row in rows
        ]

    async def delete_translation_quality_errors_by_paths(self, location_paths: set[str]) -> int:
        """按文本内部位置清理已经修好的译文检查失败明细。"""
        if not location_paths:
            return 0
        sorted_paths = sorted(location_paths)
        placeholders = ", ".join("?" for _ in sorted_paths)
        cursor = await self.connection.execute(
            f"""
--sql
                DELETE FROM [{TRANSLATION_QUALITY_ERRORS_TABLE_NAME}]
                WHERE location_path IN ({placeholders})
            """,
            tuple(sorted_paths),
        )
        await self.connection.commit()
        return max(cursor.rowcount, 0)

    def _decode_translation_run(self, row: aiosqlite.Row) -> TranslationRunRecord:
        """把 SQLite 行转换成正文翻译运行状态。"""
        return TranslationRunRecord(
            run_id=row_str(row, "run_id", self.db_path),
            status=parse_translation_run_status(row_str(row, "status", self.db_path), self.db_path),
            total_extracted=row_int(row, "total_extracted", self.db_path),
            pending_count=row_int(row, "pending_count", self.db_path),
            deduplicated_count=row_int(row, "deduplicated_count", self.db_path),
            batch_count=row_int(row, "batch_count", self.db_path),
            success_count=row_int(row, "success_count", self.db_path),
            quality_error_count=row_int(row, "quality_error_count", self.db_path),
            llm_failure_count=row_int(row, "llm_failure_count", self.db_path),
            started_at=row_str(row, "started_at", self.db_path),
            updated_at=row_str(row, "updated_at", self.db_path),
            finished_at=row_optional_str(row, "finished_at", self.db_path),
            stop_reason=row_str(row, "stop_reason", self.db_path),
            last_error=row_str(row, "last_error", self.db_path),
        )

    async def delete_translation_items_by_prefixes(self, prefixes: list[str]) -> int:
        """按路径前缀批量删除主翻译表中的记录。"""
        deleted_rows = 0
        for prefix in prefixes:
            cursor = await self.connection.execute(
                DELETE_TRANSLATION_ITEMS_BY_PREFIX,
                (f"{prefix}%",),
            )
            if cursor.rowcount > 0:
                deleted_rows += cursor.rowcount
        await self.connection.commit()
        return deleted_rows

    async def delete_translation_items_except_paths(
        self,
        allowed_paths: set[str],
    ) -> int:
        """删除当前提取规则之外的主翻译表记录。"""
        async with self.connection.execute(SELECT_TRANSLATION_PATHS) as cursor:
            rows = await cursor.fetchall()

        stored_paths = {row_str(row, "location_path", self.db_path) for row in rows}
        stale_paths = sorted(stored_paths - allowed_paths)
        if not stale_paths:
            return 0

        _ = await self.connection.executemany(
            DELETE_TRANSLATION_ITEM_BY_PATH,
            [(path,) for path in stale_paths],
        )
        await self.connection.commit()
        return len(stale_paths)

    async def delete_translation_items_by_paths(
        self,
        location_paths: Sequence[str],
    ) -> int:
        """按精确定位路径批量删除主翻译表记录。"""
        deleted_rows = 0
        for location_path in location_paths:
            cursor = await self.connection.execute(
                DELETE_TRANSLATION_ITEM_BY_PATH,
                (location_path,),
            )
            if cursor.rowcount > 0:
                deleted_rows += cursor.rowcount
        await self.connection.commit()
        return deleted_rows

    async def close(self) -> None:
        """关闭当前游戏数据库连接。"""
        await self.connection.close()


def build_event_command_group_key(rule_record: EventCommandTextRuleRecord) -> str:
    """生成事件指令规则组主键。"""
    filter_text = "|".join(
        f"{parameter_filter.index}={parameter_filter.value}"
        for parameter_filter in rule_record.parameter_filters
    )
    payload = f"{rule_record.command_code}:{filter_text}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"event_{rule_record.command_code}_{digest}"


def current_timestamp_text() -> str:
    """生成数据库状态记录使用的本地时间文本。"""
    return datetime.now().isoformat(timespec="seconds")


def parse_translation_run_status(value: str, db_path: Path) -> TranslationRunStatus:
    """校验并收窄数据库中的翻译运行状态。"""
    allowed: set[TranslationRunStatus] = {"running", "completed", "blocked", "cancelled", "failed", "stopped"}
    if value in allowed:
        return value
    raise RuntimeError(f"数据库字段 status 不是有效翻译运行状态: {db_path}")


def parse_llm_failure_category(value: str, db_path: Path) -> LlmFailureCategory:
    """校验并收窄数据库中的模型故障分类。"""
    allowed: set[LlmFailureCategory] = {
        "rate_limit",
        "timeout",
        "connection",
        "server",
        "conflict",
        "fatal",
        "unknown",
    }
    if value in allowed:
        return value
    raise RuntimeError(f"数据库字段 category 不是有效模型故障分类: {db_path}")


def parse_error_type(value: str, db_path: Path) -> ErrorType:
    """校验并收窄数据库中的译文检查错误类型。"""
    allowed: set[ErrorType] = {
        "模型返回不可解析",
        "AI漏翻",
        "文本结构不匹配",
        "控制符不匹配",
        "日文残留",
        "选项行数不匹配",
    }
    if value in allowed:
        return value
    raise RuntimeError(f"数据库字段 error_type 不是有效译文检查错误类型: {db_path}")


__all__: list[str] = [
    "DB_DIRECTORY",
    "GameRecord",
    "GameRegistry",
    "TargetGameSession",
    "build_db_path",
    "ensure_db_directory",
]
