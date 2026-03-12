"""
异步翻译数据库管理模块。

本模块负责统一管理项目运行期的 SQLite 状态。

边界说明：
1. 这里负责初始化主翻译表、错误表查询能力以及术语表静态表。
2. 这里负责把数据库行转换为业务层可消费的结构化对象。
3. 这里不负责术语提取、翻译编排和游戏文件回写。
4. 当前实现只接受现行数据库契约，不兼容旧版本数据库或脏数据。
"""

import json
from pathlib import Path
from typing import Any, Literal, Self

import aiosqlite

from app.config import Setting
from app.models.schemas import (
    ErrorRetryItem,
    ErrorType,
    Glossary,
    ItemType,
    Place,
    Role,
    TranslationErrorItem,
    TranslationItem,
)

from .sql import (
    CREATE_ERROR_TABLE,
    CREATE_GLOSSARY_STATE_TABLE,
    CREATE_PLACE_GLOSSARY_TABLE,
    CREATE_ROLE_GLOSSARY_TABLE,
    CREATE_TRANSLATION_TABLE,
    DELETE_ALL_ROWS,
    INSERT_ERROR,
    INSERT_PLACE_GLOSSARY_ITEM,
    INSERT_ROLE_GLOSSARY_ITEM,
    INSERT_TRANSLATION,
    SELECT_ALL,
    SELECT_GLOSSARY_STATE,
    SELECT_LATEST_TABLE_NAME_BY_PREFIX,
    SELECT_PLACE_GLOSSARY_ITEMS,
    SELECT_ROLE_GLOSSARY_ITEMS,
    SELECT_TRANSLATED_ITEMS,
    SELECT_TRANSLATION_PATHS,
    UPSERT_GLOSSARY_STATE,
)

type RoleGender = Literal["男", "女", "未知"]


def _parse_json_lines(raw_value: object, field_name: str) -> list[str]:
    """
    把数据库中的 JSON 文本字段严格反序列化为 `list[str]`。

    Args:
        raw_value: 原始数据库字段值。
        field_name: 当前字段名，用于构造清晰的错误信息。

    Returns:
        反序列化后的字符串列表。

    Raises:
        TypeError: 字段不是字符串，或解析结果不是 `list[str]`。
        json.JSONDecodeError: 字段内容不是合法 JSON。
    """
    if not isinstance(raw_value, str):
        raise TypeError(f"{field_name} 必须是 JSON 字符串")

    parsed: Any = json.loads(raw_value)
    if not isinstance(parsed, list):
        raise TypeError(f"{field_name} 反序列化后必须是 list[str]")
    if any(not isinstance(item, str) for item in parsed):
        raise TypeError(f"{field_name} 反序列化后必须是 list[str]")
    return list(parsed)


def _parse_item_type(raw_value: object) -> ItemType:
    """
    严格校验数据库中的正文条目类型。

    Args:
        raw_value: 原始字段值。

    Returns:
        合法的 `ItemType`。

    Raises:
        ValueError: 值不属于当前支持的正文类型。
    """
    if raw_value == "long_text":
        return "long_text"
    if raw_value == "array":
        return "array"
    if raw_value == "short_text":
        return "short_text"
    raise ValueError(f"非法 item_type: {raw_value}")


def _parse_error_type(raw_value: object) -> ErrorType:
    """
    严格校验数据库中的错误类型。

    Args:
        raw_value: 原始字段值。

    Returns:
        合法的 `ErrorType`。

    Raises:
        ValueError: 值不属于当前支持的错误类型。
    """
    if raw_value == "AI漏翻":
        return "AI漏翻"
    if raw_value == "控制符不匹配":
        return "控制符不匹配"
    if raw_value == "日文残留":
        return "日文残留"
    raise ValueError(f"非法 error_type: {raw_value}")


def _parse_role(raw_value: object) -> str | None:
    """
    严格校验数据库中的角色字段。

    Args:
        raw_value: 原始字段值。

    Returns:
        合法的角色名或 `None`。

    Raises:
        TypeError: 值既不是字符串，也不是 `None`。
    """
    if raw_value is None:
        return None
    if not isinstance(raw_value, str):
        raise TypeError("role 必须是字符串或 None")
    return raw_value


def _parse_role_gender(raw_value: object) -> RoleGender:
    """
    严格校验数据库中的角色性别字段。

    Args:
        raw_value: 原始字段值。

    Returns:
        合法的角色性别字面量。

    Raises:
        ValueError: 值不属于当前支持的角色性别集合。
    """
    if raw_value == "男":
        return "男"
    if raw_value == "女":
        return "女"
    if raw_value == "未知":
        return "未知"
    raise ValueError(f"非法 gender: {raw_value}")


def _parse_glossary_ready(raw_value: object) -> bool:
    """
    严格校验术语表就绪标记。

    Args:
        raw_value: 数据库中的 `is_ready` 字段值。

    Returns:
        术语表是否就绪。

    Raises:
        TypeError: 值不是整数。
        ValueError: 值不是 0 或 1。
    """
    if type(raw_value) is not int:
        raise TypeError("glossary_state.is_ready 必须是整数 0 或 1")
    if raw_value not in (0, 1):
        raise ValueError("glossary_state.is_ready 只能是 0 或 1")
    return raw_value == 1


class TranslationDB:
    """
    异步翻译数据库管理类。

    负责管理 SQLite 数据库连接，提供翻译结果、错误表和术语表的读写操作。
    初始化时自动准备主翻译表和术语表静态表。

    Attributes:
        db: `aiosqlite` 异步数据库连接。
        setting: 全局配置对象。
        translation_table_name: 主翻译表名。
    """

    GLOSSARY_ROLE_TABLE: str = "glossary_roles"
    GLOSSARY_PLACE_TABLE: str = "glossary_places"
    GLOSSARY_STATE_TABLE: str = "glossary_state"
    GLOSSARY_STATE_KEY: str = "current_glossary"

    def __init__(self, db: aiosqlite.Connection, setting: Setting) -> None:
        """
        初始化 TranslationDB 实例。

        Args:
            db: 已建立的异步数据库连接。
            setting: 全局配置对象。
        """
        self.db: aiosqlite.Connection = db
        self.setting: Setting = setting
        self.translation_table_name: str = setting.project.translation_table_name

    @classmethod
    async def new(cls, setting: Setting) -> Self:
        """
        异步工厂方法，根据 setting 配置建立并初始化数据库连接。

        该工厂方法会完成数据库文件的创建、表的初始化（主翻译表和各项术语表），
        并返回一个可用的 TranslationDB 实例供全局使用。

        Args:
            setting: 全局配置对象。

        Returns:
            已建立连接并完成初始化的 TranslationDB 实例。
        """
        db_path: Path = setting.project.work_path / setting.project.db_name
        db_path.parent.mkdir(parents=True, exist_ok=True)

        db: aiosqlite.Connection = await aiosqlite.connect(db_path)
        db.row_factory = aiosqlite.Row

        _ = await db.execute(
            CREATE_TRANSLATION_TABLE.format(
                table_name=setting.project.translation_table_name
            )
        )
        _ = await db.execute(
            CREATE_ROLE_GLOSSARY_TABLE.format(table_name=cls.GLOSSARY_ROLE_TABLE)
        )
        _ = await db.execute(
            CREATE_PLACE_GLOSSARY_TABLE.format(table_name=cls.GLOSSARY_PLACE_TABLE)
        )
        _ = await db.execute(
            CREATE_GLOSSARY_STATE_TABLE.format(table_name=cls.GLOSSARY_STATE_TABLE)
        )
        await db.commit()

        return cls(db=db, setting=setting)

    async def write_translation_items(
        self,
        items: list[tuple[str, ItemType, str | None, list[str], list[str]]],
    ) -> None:
        """
        批量写入已完成的译文到主翻译表。

        Args:
            items: 待写入的已完成翻译数据列表。每个元组包含:
                - location_path: 定位路径。
                - item_type: 条目类型。
                - role: 角色名。
                - original_lines: 原文行列表。
                - translation_lines: 译文行列表。
        """
        table_name: str = self.translation_table_name

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
            _ = await self.db.executemany(
                INSERT_TRANSLATION.format(table_name=table_name),
                serialized_items,
            )

        await self.db.commit()

    async def read_glossary(self) -> Glossary | None:
        """
        读取当前有效术语表。

        Returns:
            已存在时返回结构化术语表；从未构建时返回 `None`。
        """
        is_ready: bool = await self._is_glossary_ready()
        if not is_ready:
            return None

        roles: list[Role] = await self._read_roles()
        places: list[Place] = await self._read_places()
        return Glossary(roles=roles, places=places)

    async def replace_glossary(self, glossary: Glossary) -> None:
        """
        用新的术语表内容整表替换当前数据库中的术语表快照。

        Args:
            glossary: 待写入数据库的完整结构化术语表。
        """
        _ = await self.db.execute(
            DELETE_ALL_ROWS.format(table_name=self.GLOSSARY_ROLE_TABLE)
        )
        _ = await self.db.execute(
            DELETE_ALL_ROWS.format(table_name=self.GLOSSARY_PLACE_TABLE)
        )

        if glossary.roles:
            role_items: list[tuple[str, str, str]] = [
                (role.name, role.translated_name, role.gender)
                for role in glossary.roles
            ]
            _ = await self.db.executemany(
                INSERT_ROLE_GLOSSARY_ITEM.format(table_name=self.GLOSSARY_ROLE_TABLE),
                role_items,
            )

        if glossary.places:
            place_items: list[tuple[str, str]] = [
                (place.name, place.translated_name) for place in glossary.places
            ]
            _ = await self.db.executemany(
                INSERT_PLACE_GLOSSARY_ITEM.format(table_name=self.GLOSSARY_PLACE_TABLE),
                place_items,
            )

        _ = await self.db.execute(
            UPSERT_GLOSSARY_STATE.format(table_name=self.GLOSSARY_STATE_TABLE),
            (self.GLOSSARY_STATE_KEY, 1),
        )
        await self.db.commit()

    async def read_translation_location_paths(self) -> set[str]:
        """
        读取主翻译表中全部已写入路径。

        Returns:
            主翻译表中的 `location_path` 集合。

        Raises:
            TypeError: 某条记录的 `location_path` 不是字符串。
        """
        translated_paths: set[str] = set()
        async with self.db.execute(
            SELECT_TRANSLATION_PATHS.format(table_name=self.translation_table_name)
        ) as cursor:
            rows = await cursor.fetchall()

        for row in rows:
            location_path = row["location_path"]
            if not isinstance(location_path, str):
                raise TypeError("translation.location_path 必须是字符串")
            translated_paths.add(location_path)

        return translated_paths

    async def read_translated_items(self) -> list[TranslationItem]:
        """
        严格读取主翻译表中的全部正文译文。

        Returns:
            供回写层直接消费的 `TranslationItem` 列表。

        Raises:
            TypeError: 数据库字段类型与当前契约不一致。
            ValueError: 枚举字段值与当前契约不一致。
            json.JSONDecodeError: JSON 字段内容非法。
        """
        translated_items: list[TranslationItem] = []
        async with self.db.execute(
            SELECT_TRANSLATED_ITEMS.format(table_name=self.translation_table_name)
        ) as cursor:
            rows = await cursor.fetchall()

        for row in rows:
            location_path = row["location_path"]
            if not isinstance(location_path, str):
                raise TypeError("translation.location_path 必须是字符串")

            translated_items.append(
                TranslationItem(
                    location_path=location_path,
                    item_type=_parse_item_type(row["item_type"]),
                    role=_parse_role(row["role"]),
                    original_lines=_parse_json_lines(
                        row["original_lines"],
                        "translation.original_lines",
                    ),
                    translation_lines=_parse_json_lines(
                        row["translation_lines"],
                        "translation.translation_lines",
                    ),
                )
            )

        return translated_items

    async def read_latest_error_table_name(self, prefix: str) -> str | None:
        """
        读取指定前缀下按名称排序最新的一张错误表。

        Args:
            prefix: 错误表名前缀。

        Returns:
            最新错误表名；不存在时返回 `None`。

        Raises:
            TypeError: SQLite 返回的表名字段不是字符串。
        """
        async with self.db.execute(
            SELECT_LATEST_TABLE_NAME_BY_PREFIX,
            (f"{prefix}_%",),
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return None

        table_name = row["name"]
        if not isinstance(table_name, str):
            raise TypeError("sqlite_master.name 必须是字符串")
        return table_name

    async def read_error_retry_items(self, table_name: str) -> list[ErrorRetryItem]:
        """
        严格读取指定错误表，并反序列化为错误重翻译条目列表。

        Args:
            table_name: 目标错误表名。

        Returns:
            结构化后的错误重翻译条目列表。
        """
        rows: list[dict[str, Any]] = await self.read_table(table_name)
        retry_items: list[ErrorRetryItem] = []

        for row in rows:
            location_path = row["location_path"]
            if not isinstance(location_path, str):
                raise TypeError(f"{table_name}.location_path 必须是字符串")

            retry_items.append(
                ErrorRetryItem(
                    translation_item=TranslationItem(
                        location_path=location_path,
                        item_type=_parse_item_type(row["item_type"]),
                        role=_parse_role(row["role"]),
                        original_lines=_parse_json_lines(
                            row["original_lines"],
                            f"{table_name}.original_lines",
                        ),
                    ),
                    previous_translation_lines=_parse_json_lines(
                        row["translation_lines"],
                        f"{table_name}.translation_lines",
                    ),
                    error_type=_parse_error_type(row["error_type"]),
                    error_detail=_parse_json_lines(
                        row["error_detail"],
                        f"{table_name}.error_detail",
                    ),
                )
            )

        retry_items.sort(key=lambda item: item.translation_item.location_path)
        return retry_items

    async def create_error_table(
        self,
        table_name: str,
        items: list[TranslationErrorItem],
    ) -> None:
        """
        根据动态表名创建错误数据表，并批量插入错误数据。

        Args:
            table_name: 动态生成的错误表名。
            items: 包含错误上下文和错误详情的条目列表。
        """
        await self.db.execute(CREATE_ERROR_TABLE.format(table_name=table_name))

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
            await self.db.executemany(
                INSERT_ERROR.format(table_name=table_name),
                serialized_items,
            )

        await self.db.commit()

    async def read_table(self, table_name: str) -> list[dict[str, Any]]:
        """
        读取指定表的所有数据，并将其转化为原生字典列表返回。

        Args:
            table_name: 目标读取的表名。

        Returns:
            包含所有行数据的字典列表。字典的键对应数据表列名。
        """
        async with self.db.execute(SELECT_ALL.format(table_name=table_name)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def _is_glossary_ready(self) -> bool:
        """
        判断当前数据库中是否已经存在一张有效术语表快照。

        Returns:
            已执行过术语表替换时返回 `True`，否则返回 `False`。
        """
        async with self.db.execute(
            SELECT_GLOSSARY_STATE.format(table_name=self.GLOSSARY_STATE_TABLE),
            (self.GLOSSARY_STATE_KEY,),
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return False

        return _parse_glossary_ready(row["is_ready"])

    async def _read_roles(self) -> list[Role]:
        """
        严格读取角色术语表并转换为结构化角色对象列表。

        Returns:
            角色术语对象列表。
        """
        roles: list[Role] = []
        async with self.db.execute(
            SELECT_ROLE_GLOSSARY_ITEMS.format(table_name=self.GLOSSARY_ROLE_TABLE)
        ) as cursor:
            rows = await cursor.fetchall()

        for row in rows:
            name = row["name"]
            translated_name = row["translated_name"]
            if not isinstance(name, str):
                raise TypeError("glossary_roles.name 必须是字符串")
            if not isinstance(translated_name, str):
                raise TypeError("glossary_roles.translated_name 必须是字符串")

            roles.append(
                Role(
                    name=name,
                    translated_name=translated_name,
                    gender=_parse_role_gender(row["gender"]),
                )
            )

        return roles

    async def _read_places(self) -> list[Place]:
        """
        严格读取地点术语表并转换为结构化地点对象列表。

        Returns:
            地点术语对象列表。
        """
        places: list[Place] = []
        async with self.db.execute(
            SELECT_PLACE_GLOSSARY_ITEMS.format(table_name=self.GLOSSARY_PLACE_TABLE)
        ) as cursor:
            rows = await cursor.fetchall()

        for row in rows:
            name = row["name"]
            translated_name = row["translated_name"]
            if not isinstance(name, str):
                raise TypeError("glossary_places.name 必须是字符串")
            if not isinstance(translated_name, str):
                raise TypeError("glossary_places.translated_name 必须是字符串")
            places.append(Place(name=name, translated_name=translated_name))

        return places


__all__: list[str] = ["TranslationDB"]
