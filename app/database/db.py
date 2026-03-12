"""
异步翻译数据库管理模块。

本模块负责统一管理项目运行期的 SQLite 状态。

边界说明：
1. 这里负责初始化主翻译表、错误表查询能力以及术语表静态表。
2. 这里负责把数据库行转换为业务层可消费的结构化对象。
3. 这里不负责术语提取、翻译编排和游戏文件回写。
"""

import json
from pathlib import Path
from typing import Any, Self

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
    CREATE_PLACE_GLOSSARY_TABLE,
    CREATE_ROLE_GLOSSARY_TABLE,
    CREATE_GLOSSARY_STATE_TABLE,
    CREATE_TRANSLATION_TABLE,
    DELETE_ALL_ROWS,
    INSERT_ERROR,
    INSERT_PLACE_GLOSSARY_ITEM,
    INSERT_ROLE_GLOSSARY_ITEM,
    INSERT_TRANSLATION,
    SELECT_ALL,
    SELECT_PLACE_GLOSSARY_ITEMS,
    SELECT_ROLE_GLOSSARY_ITEMS,
    SELECT_GLOSSARY_STATE,
    SELECT_LATEST_TABLE_NAME_BY_PREFIX,
    SELECT_TRANSLATION_PATHS,
    UPSERT_GLOSSARY_STATE,
)


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
            db: 已建立的异步数据库连接
            setting: 全局配置对象
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
        # 步骤 1: 拼接数据库文件路径
        db_path: Path = setting.project.work_path / setting.project.db_name

        # 步骤 2: 确保工作目录存在
        # 为什么这样做: 如果直接连接不存在的目录会报系统找不到路径的错误，这里做一次兜底创建。
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # 步骤 3: 建立异步数据库连接，设置 row_factory 以支持字典式访问
        db: aiosqlite.Connection = await aiosqlite.connect(db_path)
        db.row_factory = aiosqlite.Row

        # 步骤 4: 初始化主翻译表与术语表静态表
        # 这里统一执行建表操作，保证项目一启动数据库就绪
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

        主翻译表在 `new(...)` 阶段已经完成初始化，
        此方法不再承担建表职责，而是将内存中的译文通过 UPSERT 策略同步至数据库，
        避免重复路径抛出主键冲突。

        Args:
            items: 待写入的已完成翻译数据列表。每个元组包含:
                - location_path: 定位路径 (str)
                - item_type: 条目类型 (ItemType)
                - role: 角色名 (str | None)
                - original_lines: 原文行列表 (list[str])
                - translation_lines: 译文行列表 (list[str])
        """
        table_name: str = self.translation_table_name

        if items:
            # 步骤 1: 将列表形式的原文和译文序列化为 JSON 字符串
            # 为什么这样做: SQLite 原生不支持存储数组，JSON 序列化是存储文本列表的最轻量做法。
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
            
            # 步骤 2: 批量插入或替换
            _ = await self.db.executemany(
                INSERT_TRANSLATION.format(table_name=table_name),
                serialized_items,
            )

        # 步骤 3: 统一提交事务
        await self.db.commit()

    async def read_glossary(self) -> Glossary | None:
        """
        读取当前有效术语表。

        为什么这里返回 `None`：
        1. 项目刚初始化但尚未执行过术语构建时，数据库中不应伪造一张空术语表。
        2. “已构建空术语表”和“从未构建术语表”需要在语义上区分开。

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

        该操作是破坏性的替换操作，会清空旧的术语表数据并写入新数据，
        并在完成后更新“当前术语表已建立”的状态标记。

        Args:
            glossary: 待写入数据库的完整结构化术语表。
        """
        # 步骤 1: 清空已有的角色名与地图显示名术语表。
        _ = await self.db.execute(
            DELETE_ALL_ROWS.format(table_name=self.GLOSSARY_ROLE_TABLE)
        )
        _ = await self.db.execute(
            DELETE_ALL_ROWS.format(table_name=self.GLOSSARY_PLACE_TABLE)
        )

        # 步骤 2: 将角色模型元组化并批量插入
        if glossary.roles:
            role_items: list[tuple[str, str, str]] = [
                (role.name, role.translated_name, role.gender)
                for role in glossary.roles
            ]
            _ = await self.db.executemany(
                INSERT_ROLE_GLOSSARY_ITEM.format(table_name=self.GLOSSARY_ROLE_TABLE),
                role_items,
            )

        # 步骤 3: 将地点模型元组化并批量插入
        if glossary.places:
            place_items: list[tuple[str, str]] = [
                (place.name, place.translated_name) for place in glossary.places
            ]
            _ = await self.db.executemany(
                INSERT_PLACE_GLOSSARY_ITEM.format(table_name=self.GLOSSARY_PLACE_TABLE),
                place_items,
            )

        # 步骤 4: 写入/更新术语表就绪状态，使得下次启动能够识别到有效的术语表
        _ = await self.db.execute(
            UPSERT_GLOSSARY_STATE.format(table_name=self.GLOSSARY_STATE_TABLE),
            (self.GLOSSARY_STATE_KEY, 1),
        )
        
        # 步骤 5: 提交所有替换事务
        await self.db.commit()

    async def read_translation_location_paths(self) -> set[str]:
        """
        读取主翻译表中“已完成译文”的路径集合。

        为什么这里仍要解析 `translation_lines`：
        1. 旧版本数据库可能残留空译文行。
        2. 当前新语义虽然只保存完成译文，但读取时仍要兼容历史数据。

        Returns:
            所有已完成译文对应的 `location_path` 集合。
        """
        translated_paths: set[str] = set()
        async with self.db.execute(
            SELECT_TRANSLATION_PATHS.format(table_name=self.translation_table_name)
        ) as cursor:
            rows = await cursor.fetchall()

        for row in rows:
            location_path = row["location_path"]
            translation_lines_raw = row["translation_lines"]
            if not isinstance(location_path, str):
                continue
            if not isinstance(translation_lines_raw, str):
                continue

            try:
                translation_lines = json.loads(translation_lines_raw)
            except json.JSONDecodeError:
                continue

            if not isinstance(translation_lines, list):
                continue

            if any(isinstance(line, str) and line.strip() for line in translation_lines):
                translated_paths.add(location_path)

        return translated_paths

    async def read_latest_error_table_name(self, prefix: str) -> str | None:
        """
        读取指定前缀下按名称排序最新的一张错误表。
        Args:
            prefix: 错误表名前缀。
        Returns:
            最新错误表名；不存在时返回 `None`。
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
            return None
        return table_name

    async def read_error_retry_items(self, table_name: str) -> list[ErrorRetryItem]:
        """
        读取指定错误表，并反序列化为错误重翻译条目列表。
        Args:
            table_name: 目标错误表名。
        Returns:
            结构化后的错误重翻译条目列表。
        """
        rows: list[dict[str, Any]] = await self.read_table(table_name)
        retry_items: list[ErrorRetryItem] = []

        for row in rows:
            location_path = row.get("location_path")
            item_type = self._normalize_item_type(row.get("item_type"))
            error_type = self._normalize_error_type(row.get("error_type"))

            if not isinstance(location_path, str):
                continue
            if item_type is None or error_type is None:
                continue

            retry_items.append(
                ErrorRetryItem(
                    translation_item=TranslationItem(
                        location_path=location_path,
                        item_type=item_type,
                        role=row.get("role") if isinstance(row.get("role"), str) else None,
                        original_lines=self._deserialize_lines(row.get("original_lines")),
                    ),
                    previous_translation_lines=self._deserialize_lines(
                        row.get("translation_lines")
                    ),
                    error_type=error_type,
                    error_detail=self._deserialize_lines(row.get("error_detail")),
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

        每次正文翻译或重翻任务都会根据时间戳生成唯一的错误表名，
        这个方法负责初始化这类的临时错误表，并把翻译失败的条目入库保存，
        方便后续用户追溯或进行自动重翻。

        Args:
            table_name: 动态生成的错误表名。
            items: 包含错误上下文和错误详情的条目列表。
        """
        # 步骤 1: 执行动态建表 SQL，如果表已存在则忽略
        await self.db.execute(
            CREATE_ERROR_TABLE.format(table_name=table_name)
        )

        if items:
            # 步骤 2: 将包含多行文本及列表的字段序列化为 JSON
            # 为什么这样做: 错误详情及原文同样是列表，必须序列化才能写入 SQLite 文本字段。
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
            
            # 步骤 3: 批量插入错误记录
            await self.db.executemany(
                INSERT_ERROR.format(table_name=table_name),
                serialized_items,
            )

        # 步骤 4: 统一提交事务
        await self.db.commit()

    async def read_table(self, table_name: str) -> list[dict[str, Any]]:
        """
        读取指定表的所有数据，并将其转化为原生字典列表返回。

        此方法提供通用的表读取能力，通常用于拉取需要做后续业务映射的基础数据。

        Args:
            table_name: 目标读取的表名。

        Returns:
            包含所有行数据的字典列表。字典的键对应数据表列名。
        """
        # 步骤 1: 执行全表查询
        async with self.db.execute(SELECT_ALL.format(table_name=table_name)) as cursor:
            # 步骤 2: 获取所有的 Row 对象
            rows = await cursor.fetchall()

            # 步骤 3: 利用 aiosqlite.Row 的特性，直接转换为标准字典
            result: list[dict[str, Any]] = [dict(row) for row in rows]

        return result

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

        is_ready = row["is_ready"]
        return is_ready == 1

    async def _read_roles(self) -> list[Role]:
        """
        读取角色术语表并转换为结构化角色对象列表。

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
            gender = row["gender"]
            if not isinstance(name, str):
                continue
            if not isinstance(translated_name, str):
                continue
            if gender not in ("男", "女", "未知"):
                continue
            roles.append(
                Role(
                    name=name,
                    translated_name=translated_name,
                    gender=gender,
                )
            )

        return roles

    async def _read_places(self) -> list[Place]:
        """
        读取地点术语表并转换为结构化地点对象列表。

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
                continue
            if not isinstance(translated_name, str):
                continue
            places.append(Place(name=name, translated_name=translated_name))

        return places

    def _deserialize_lines(self, raw_value: Any) -> list[str]:
        """
        把数据库中的 JSON 文本字段反序列化为字符串列表。
        Args:
            raw_value: 原始字段值。
        Returns:
            归一化后的字符串列表。
        """
        if isinstance(raw_value, list):
            return [item for item in raw_value if isinstance(item, str)]

        if not isinstance(raw_value, str):
            return []

        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            return []

        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, str)]

    def _normalize_item_type(self, raw_value: Any) -> ItemType | None:
        """
        把外部输入归一化为合法的 `ItemType`。
        Args:
            raw_value: 原始输入值。
        Returns:
            合法时返回 `ItemType`，否则返回 `None`。
        """
        if raw_value == "long_text":
            return "long_text"
        if raw_value == "array":
            return "array"
        if raw_value == "short_text":
            return "short_text"
        return None

    def _normalize_error_type(self, raw_value: Any) -> ErrorType | None:
        """
        把外部输入归一化为合法的错误类型。
        Args:
            raw_value: 原始输入值。
        Returns:
            合法时返回错误类型，否则返回 `None`。
        """
        if raw_value == "AI漏翻":
            return "AI漏翻"
        if raw_value == "控制符不匹配":
            return "控制符不匹配"
        if raw_value == "日文残留":
            return "日文残留"
        return None

    async def close(self) -> None:
        """
        关闭数据库连接。

        释放底层 SQLite 连接资源。
        """
        await self.db.close()
