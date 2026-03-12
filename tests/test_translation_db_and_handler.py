"""
翻译数据库与正文编排测试。

覆盖以下关键行为：
1. 翻译数据库初始化时自动创建主翻译表。
2. 术语表改为数据库静态表后，仍能完整读写与回读。
2. 主翻译表只按“已完成译文”暴露路径集合。
3. 正文翻译流程只按数据库中的完成路径过滤正文，
   并由总编排器统一推进正文翻译进度条。
"""

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from app.config import (
    ErrorTranslationSetting,
    GlossaryExtractionSetting,
    GlossaryTranslationSetting,
    GlossaryTranslationTaskSetting,
    LLMServicesSetting,
    LLMServiceSetting,
    ProjectSetting,
    Setting,
    TextTranslationSetting,
    TranslationContextSetting,
)
from app.core.di import TranslationProvider
from app.core.handler import TranslationHandler
from app.database.db import TranslationDB
from app.extraction import DataTextExtraction, GlossaryExtraction, PluginTextExtraction
from app.models.schemas import (
    ErrorRetryItem,
    GameData,
    Glossary,
    Place,
    Role,
    TranslationData,
    TranslationItem,
)
from app.services.llm import LLMHandler
from app.translation import TextTranslation, iter_error_retry_context_batches, iter_translation_context_batches


def build_setting(base_dir: Path) -> Setting:
    """构造测试所需的最小配置对象。"""
    glossary_task = GlossaryTranslationTaskSetting(
        chunk_size=1,
        retry_count=0,
        retry_delay=0,
        response_retry_count=2,
        system_prompt="test prompt",
    )
    llm_service = LLMServiceSetting(
        provider_type="openai",
        base_url="",
        api_key="test",
        model="test-model",
        timeout=1,
    )
    return Setting(
        project=ProjectSetting(
            file_path=base_dir / "game",
            work_path=base_dir / "work",
            db_name="test.db",
            translation_table_name="translations",
        ),
        llm_services=LLMServicesSetting(
            glossary=llm_service,
            text=llm_service,
        ),
        glossary_extraction=GlossaryExtractionSetting(
            role_chunk_blocks=1,
            role_chunk_lines=1,
        ),
        glossary_translation=GlossaryTranslationSetting(
            role_name=glossary_task,
            display_name=glossary_task,
        ),
        translation_context=TranslationContextSetting(
            token_size=1,
            factor=1,
            max_command_items=1,
        ),
        error_translation=ErrorTranslationSetting(
            chunk_size=1,
            system_prompt="error retry prompt",
        ),
        text_translation=TextTranslationSetting(
            worker_count=1,
            rpm=None,
            retry_count=0,
            retry_delay=0,
            system_prompt="text prompt",
        ),
    )


def build_game_data() -> GameData:
    """构造正文测试足够使用的游戏数据替身对象。"""
    return GameData.model_construct(
        data={},
        writable_data={},
        map_data={},
        system=None,
        common_events=[],
        troops=[],
        base_data={},
        plugins_js=[],
        writable_plugins_js=[],
    )


def build_handler(
    *,
    setting: Setting,
    game_data: GameData,
    llm_handler: Any,
    translation_db: Any,
    request_container: "FakeRequestContainer",
) -> TranslationHandler:
    provider = FakeTranslationProvider(request_container=request_container)
    return TranslationHandler(
        provider=cast(TranslationProvider, cast(object, provider)),
        setting=setting,
        game_data=game_data,
        llm_handler=llm_handler,
        translation_db=translation_db,
    )


class FakeGlossaryExtraction:
    """返回固定术语提取结果的假提取器。"""

    def extract_role_dialogue_chunks(
        self,
        chunk_blocks: int,
        chunk_lines: int,
    ) -> dict[str, list[str]]:
        """返回固定角色名结果，保证术语表校验通过。"""
        return {"勇者": ["你好"]}

    def extract_display_names(self) -> dict[str, str]:
        """返回固定地图显示名结果，保证术语表校验通过。"""
        return {"城镇": "Map001"}


class FakeDataTextExtraction:
    """返回固定数据目录正文结果的假提取器。"""

    def __init__(self, translation_data_map: dict[str, TranslationData]) -> None:
        self.translation_data_map: dict[str, TranslationData] = translation_data_map

    def extract_all_text(self) -> dict[str, TranslationData]:
        """返回预设的数据目录提取结果。"""
        return self.translation_data_map


class FakePluginTextExtraction:
    """返回固定插件正文结果的假提取器。"""

    def extract_all_text(self) -> dict[str, TranslationData]:
        """本组测试不需要插件正文。"""
        return {}


class FakeTextTranslation:
    """返回固定正确队列与错误队列的正文翻译假对象。"""

    def __init__(
        self,
        right_batches: list[list[TranslationItem]],
        error_batches: list[list[tuple[str, str, str | None, list[str], list[str], str, list[str]]]],
    ) -> None:
        self.right_batches = right_batches
        self.error_batches = error_batches
        self.started_batches: list[tuple[list[TranslationItem], list[Any]]] | None = None

    def start_translation(
        self,
        *,
        llm_handler: Any,
        batches: list[tuple[list[TranslationItem], list[Any]]],
    ) -> None:
        """记录启动批次，但不自行创建后台任务。"""
        self.started_batches = batches

    async def iter_right_items(self):
        """按预设顺序产出成功批次。"""
        for items in self.right_batches:
            yield items

    async def iter_error_items(self):
        """按预设顺序产出错误批次。"""
        for items in self.error_batches:
            yield items


class FakeRequestContainer:
    """模拟依赖注入框架的请求作用域容器。"""

    def __init__(
        self,
        glossary_extraction: FakeGlossaryExtraction,
        data_text_extraction: FakeDataTextExtraction,
        plugin_text_extraction: FakePluginTextExtraction,
        text_translation: FakeTextTranslation,
    ) -> None:
        self.glossary_extraction = glossary_extraction
        self.data_text_extraction = data_text_extraction
        self.plugin_text_extraction = plugin_text_extraction
        self.text_translation = text_translation

    async def __aenter__(self) -> "FakeRequestContainer":
        """进入异步上下文。"""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        """退出异步上下文。"""
        return None

    async def get(self, cls: type[Any]) -> Any:
        """按请求类型返回预设对象。"""
        if cls is GlossaryExtraction:
            return self.glossary_extraction
        if cls is DataTextExtraction:
            return self.data_text_extraction
        if cls is PluginTextExtraction:
            return self.plugin_text_extraction
        if cls is TextTranslation:
            return self.text_translation
        raise AssertionError(f"未处理的依赖请求: {cls}")


class FakeAppContainer:
    """模拟依赖注入框架的进程级容器。"""

    def __init__(
        self,
        *,
        setting: Setting,
        game_data: GameData,
        llm_handler: Any,
        translation_db: Any,
        request_container: FakeRequestContainer,
    ) -> None:
        self.request_container = request_container
        self.dependency_map: dict[type[Any], Any] = {
            Setting: setting,
            GameData: game_data,
            LLMHandler: llm_handler,
            TranslationDB: translation_db,
        }

    async def get(self, cls: type[Any]) -> Any:
        """按类型返回进程级依赖。"""
        return self.dependency_map[cls]

    def __call__(self) -> FakeRequestContainer:
        """派生请求作用域容器。"""
        return self.request_container


class FakeTranslationProvider:
    """
    模拟统一依赖提供器的假对象。

    这个替身只保留 `open_request_scope()`，供总编排器获取请求级依赖。
    """

    def __init__(
        self,
        *,
        request_container: FakeRequestContainer,
    ) -> None:
        self.request_container: FakeRequestContainer = request_container
        self.request_scope_calls: int = 0

    def open_request_scope(self) -> FakeRequestContainer:
        """返回预设的请求作用域容器。"""
        self.request_scope_calls += 1
        return self.request_container


class FakeTranslationDB:
    """用于正文流程测试的假数据库对象。"""

    def __init__(
        self,
        translated_paths: set[str],
        glossary: Glossary | None = None,
        latest_error_table_name: str | None = None,
        error_retry_items: list[ErrorRetryItem] | None = None,
        table_rows: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.translated_paths: set[str] = translated_paths
        self.glossary: Glossary | None = glossary
        self.latest_error_table_name: str | None = latest_error_table_name
        self.error_retry_items: list[ErrorRetryItem] = error_retry_items or []
        self.table_rows: dict[str, list[dict[str, Any]]] = table_rows or {}
        self.written_translation_items: list[
            list[tuple[str, str, str | None, list[str], list[str]]]
        ] = []
        self.created_error_tables: list[tuple[str, list[Any]]] = []
        self.replaced_glossaries: list[Glossary] = []

    async def read_glossary(self) -> Glossary | None:
        """返回预设术语表。"""
        return self.glossary

    async def replace_glossary(self, glossary: Glossary) -> None:
        """记录整表替换后的术语表。"""
        self.glossary = glossary
        self.replaced_glossaries.append(glossary)

    async def read_translation_location_paths(self) -> set[str]:
        """返回预设的完成译文路径集合。"""
        return set(self.translated_paths)

    async def write_translation_items(
        self,
        items: list[tuple[str, str, str | None, list[str], list[str]]],
    ) -> None:
        """记录写入的完成译文。"""
        self.written_translation_items.append(items)

    async def create_error_table(self, table_name: str, items: list[Any]) -> None:
        """记录错误表创建与写入。"""
        self.created_error_tables.append((table_name, items))

    async def read_latest_error_table_name(self, prefix: str) -> str | None:
        """返回预设的最新错误表名。"""
        return self.latest_error_table_name

    async def read_error_retry_items(self, table_name: str) -> list[ErrorRetryItem]:
        """返回预设的错误重翻译条目列表。"""
        return list(self.error_retry_items)

    async def read_table(self, table_name: str) -> list[dict[str, Any]]:
        """返回预设表数据。"""
        return list(self.table_rows.get(table_name, []))


@dataclass
class FakeProgressTask:
    """记录单个任务进度状态。"""

    description: str
    total: int
    completed: int = 0


class FakeProgress:
    """记录进度条推进情况的假对象。"""

    def __init__(self) -> None:
        self.tasks: list[FakeProgressTask] = []
        self.advances: list[int] = []
        self.descriptions: list[str] = []

    def __enter__(self) -> "FakeProgress":
        """进入上下文。"""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        """退出上下文。"""
        return None

    def add_task(self, description: str, total: int) -> int:
        """创建单个假任务。"""
        self.tasks.append(FakeProgressTask(description=description, total=total))
        self.descriptions.append(description)
        return len(self.tasks) - 1

    def advance(self, task_id: int, advance: int = 1) -> None:
        """推进进度。"""
        self.tasks[task_id].completed += advance
        self.advances.append(advance)

    def update(
        self,
        task_id: int,
        description: str | None = None,
        completed: int | None = None,
    ) -> None:
        """更新任务描述或完成量。"""
        if description is not None:
            self.tasks[task_id].description = description
            self.descriptions.append(description)
        if completed is not None:
            self.tasks[task_id].completed = completed


class TranslationDBAndHandlerTestCase(unittest.IsolatedAsyncioTestCase):
    """数据库与正文编排测试。"""

    async def test_create_builds_handler_from_provider_and_caches_app_container(self) -> None:
        """异步工厂应从提供器创建总编排器，并缓存进程级容器。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            setting: Setting = build_setting(Path(temp_dir))
            game_data = build_game_data()
            llm_handler = object()
            translation_db = FakeTranslationDB(translated_paths=set())
            request_container = FakeRequestContainer(
                glossary_extraction=FakeGlossaryExtraction(),
                data_text_extraction=FakeDataTextExtraction({}),
                plugin_text_extraction=FakePluginTextExtraction(),
                text_translation=FakeTextTranslation(right_batches=[], error_batches=[]),
            )
            app_container = FakeAppContainer(
                setting=setting,
                game_data=game_data,
                llm_handler=llm_handler,
                translation_db=translation_db,
                request_container=request_container,
            )
            provider = cast(
                TranslationProvider,
                cast(object, FakeTranslationProvider(request_container=request_container)),
            )

            with patch("app.core.handler.make_async_container", return_value=app_container):
                handler = await TranslationHandler.create(provider)

        self.assertIs(handler.provider, provider)
        self.assertIs(handler.setting, setting)
        self.assertIs(handler.game_data, game_data)
        self.assertIs(handler.llm_handler, llm_handler)
        self.assertIs(handler.translation_db, translation_db)
        self.assertIs(handler._app_container, app_container)
        self.assertIs(handler._open_request_scope(), request_container)

    async def test_translation_db_creates_table_on_init_and_reads_completed_paths(self) -> None:
        """数据库初始化应自动建表，并只返回有效完成译文路径。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            setting: Setting = build_setting(Path(temp_dir))
            translation_db = await TranslationDB.new(setting)
            try:
                empty_paths = await translation_db.read_translation_location_paths()
                await translation_db.write_translation_items(
                    [
                        (
                            "Map001/events/1",
                            "short_text",
                            None,
                            ["原文1"],
                            ["译文1"],
                        ),
                        (
                            "Map001/events/2",
                            "short_text",
                            None,
                            ["原文2"],
                            [""],
                        ),
                    ]
                )
                translated_paths = await translation_db.read_translation_location_paths()
            finally:
                await translation_db.close()

        self.assertEqual(empty_paths, set())
        self.assertEqual(translated_paths, {"Map001/events/1"})

    async def test_translation_db_round_trips_glossary_and_preserves_empty_state(self) -> None:
        """术语表应能在数据库中完整回读，并区分未构建与已构建空术语表。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            setting: Setting = build_setting(Path(temp_dir))
            translation_db = await TranslationDB.new(setting)
            try:
                missing_glossary = await translation_db.read_glossary()

                glossary = Glossary(
                    roles=[Role(name="勇者", translated_name="Hero", gender="男")],
                    places=[Place(name="城镇", translated_name="Town")],
                )
                await translation_db.replace_glossary(glossary)
                restored_glossary = await translation_db.read_glossary()

                empty_glossary = Glossary()
                await translation_db.replace_glossary(empty_glossary)
                restored_empty_glossary = await translation_db.read_glossary()
            finally:
                await translation_db.close()

        self.assertIsNone(missing_glossary)
        self.assertIsNotNone(restored_glossary)
        assert restored_glossary is not None
        self.assertEqual(restored_glossary.model_dump(), glossary.model_dump())
        self.assertIsNotNone(restored_empty_glossary)
        assert restored_empty_glossary is not None
        self.assertEqual(
            restored_empty_glossary.model_dump(),
            empty_glossary.model_dump(),
        )

    async def test_translate_text_filters_only_by_completed_location_path_and_advances_progress(self) -> None:
        """正文流程应只按已完成路径过滤，并把成功与错误都计入进度。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir: Path = Path(temp_dir)
            setting: Setting = build_setting(base_dir)

            already_done_item = TranslationItem(
                location_path="Map001/events/1",
                item_type="short_text",
                original_lines=["旧文本"],
            )
            pending_success_item = TranslationItem(
                location_path="Map001/events/2",
                item_type="short_text",
                original_lines=["待翻译文本1"],
            )
            pending_error_item = TranslationItem(
                location_path="Map001/events/3",
                item_type="short_text",
                original_lines=["待翻译文本2"],
            )
            translated_success_item = TranslationItem(
                location_path="Map001/events/2",
                item_type="short_text",
                original_lines=["待翻译文本1"],
                translation_lines=["译文1"],
            )
            error_item = (
                "Map001/events/3",
                "short_text",
                None,
                ["待翻译文本2"],
                [],
                "AI漏翻",
                ["未返回该条目"],
            )

            translation_data_map = {
                "Map001.json": TranslationData(
                    display_name="城镇",
                    translation_items=[
                        already_done_item,
                        pending_success_item,
                        pending_error_item,
                    ],
                )
            }
            fake_text_translation = FakeTextTranslation(
                right_batches=[[translated_success_item]],
                error_batches=[[error_item]],
            )
            request_container = FakeRequestContainer(
                glossary_extraction=FakeGlossaryExtraction(),
                data_text_extraction=FakeDataTextExtraction(translation_data_map),
                plugin_text_extraction=FakePluginTextExtraction(),
                text_translation=fake_text_translation,
            )
            fake_db = FakeTranslationDB(
                translated_paths={"Map001/events/1"},
                glossary=Glossary(
                    roles=[Role(name="勇者", translated_name="Hero", gender="男")],
                    places=[Place(name="城镇", translated_name="Town")],
                ),
            )
            fake_progress = FakeProgress()
            captured_pending_paths: list[str] = []

            def fake_build_translation_batches(
                translation_data_map: dict[str, TranslationData],
                glossary: Glossary,
            ) -> list[tuple[list[TranslationItem], list[Any]]]:
                for translation_data in translation_data_map.values():
                    for item in translation_data.translation_items:
                        captured_pending_paths.append(item.location_path)
                return [([], [])]

            handler = build_handler(
                setting=setting,
                game_data=build_game_data(),
                llm_handler=object(),
                translation_db=fake_db,
                request_container=request_container,
            )

            with (
                patch("app.core.handler.get_progress", return_value=fake_progress),
                patch.object(
                    TranslationHandler,
                    "_build_translation_batches",
                    side_effect=fake_build_translation_batches,
                ),
            ):
                await handler.translate_text()
        self.assertEqual(
            captured_pending_paths,
            ["Map001/events/2", "Map001/events/3"],
        )
        self.assertEqual(
            fake_db.written_translation_items,
            [
                [
                    (
                        "Map001/events/2",
                        "short_text",
                        None,
                        ["待翻译文本1"],
                        ["译文1"],
                    )
                ]
            ],
        )
        self.assertEqual(fake_progress.tasks[0].total, 2)
        self.assertEqual(sum(fake_progress.advances), 2)
        self.assertEqual(fake_progress.tasks[0].completed, 2)
        self.assertIn("正文翻译完成，共处理 2 条", fake_progress.descriptions)

    async def test_translation_db_reads_latest_error_table_and_retry_items(self) -> None:
        """最新错误表读取应返回最新表名并反序列化为重翻译条目。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            setting: Setting = build_setting(Path(temp_dir))
            translation_db = await TranslationDB.new(setting)
            try:
                await translation_db.create_error_table(
                    "translation_errors_20260101_000000",
                    [
                        (
                            "Map001/events/1",
                            "short_text",
                            None,
                            ["原文A"],
                            ["旧译文A"],
                            "AI漏翻",
                            ["第一张错误表"],
                        )
                    ],
                )
                await translation_db.create_error_table(
                    "translation_errors_20260102_000000",
                    [
                        (
                            "Map001/events/2",
                            "short_text",
                            "勇者",
                            ["原文B"],
                            ["旧译文B"],
                            "控制符不匹配",
                            ["最新错误表"],
                        )
                    ],
                )

                latest_table_name = await translation_db.read_latest_error_table_name(
                    "translation_errors"
                )
                retry_items = await translation_db.read_error_retry_items(
                    "translation_errors_20260102_000000"
                )
            finally:
                await translation_db.close()

        self.assertEqual(latest_table_name, "translation_errors_20260102_000000")
        self.assertEqual(len(retry_items), 1)
        self.assertEqual(
            retry_items[0].translation_item.location_path,
            "Map001/events/2",
        )
        self.assertEqual(retry_items[0].translation_item.role, "勇者")
        self.assertEqual(retry_items[0].previous_translation_lines, ["旧译文B"])
        self.assertEqual(retry_items[0].error_type, "控制符不匹配")
        self.assertEqual(retry_items[0].error_detail, ["最新错误表"])

    def test_iter_translation_context_batches_do_not_use_max_command_items_before_token_limit(self) -> None:
        """未超过令牌阈值时，不应因为 `max_command_items` 提前切批。"""
        translation_data = TranslationData(
            display_name="Map001",
            translation_items=[
                TranslationItem(
                    location_path="Map001/events/1",
                    item_type="short_text",
                    role="勇者",
                    original_lines=["你好"],
                ),
                TranslationItem(
                    location_path="Map001/events/2",
                    item_type="short_text",
                    role="勇者",
                    original_lines=["再见"],
                ),
            ],
        )

        batches = list(
            iter_translation_context_batches(
                translation_data=translation_data,
                token_size=999999,
                factor=1,
                max_command_items=1,
                system_prompt="text prompt",
                glossary=None,
            )
        )

        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0][0]), 2)
    async def test_iter_error_retry_context_batches_include_error_info(self) -> None:
        """错误重翻译上下文应包含旧译文、错误类型和错误详情。"""
        retry_item = ErrorRetryItem(
            translation_item=TranslationItem(
                location_path="Map001/events/2",
                item_type="short_text",
                role="勇者",
                original_lines=["你好 \\N[1]"],
            ),
            previous_translation_lines=["Hello \\N[1]"],
            error_type="控制符不匹配",
            error_detail=["placeholder mismatch"],
        )

        batches = list(
            iter_error_retry_context_batches(
                error_retry_items=[retry_item],
                chunk_size=10,
                system_prompt="error retry prompt",
                glossary=Glossary(
                    roles=[Role(name="勇者", translated_name="Hero", gender="男")]
                ),
            )
        )

        self.assertEqual(len(batches), 1)
        batch_items, messages = batches[0]
        self.assertEqual(len(batch_items), 1)
        self.assertEqual(batch_items[0].location_path, "Map001/events/2")
        self.assertEqual(messages[0].text, "error retry prompt")
        self.assertIn("placeholder mismatch", messages[1].text)
        self.assertIn("控制符不匹配", messages[1].text)
        self.assertIn("[N_1]", messages[1].text)
        self.assertIn("原名: 勇者 | 译名: Hero | 性别: 男", messages[1].text)

    async def test_retry_error_table_reuses_text_translation_and_writes_new_error_table(self) -> None:
        """错误表重翻译应写回主表，并把仍失败的结果写入新错误表。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir: Path = Path(temp_dir)
            setting: Setting = build_setting(base_dir)

            success_item = TranslationItem(
                location_path="Map001/events/2",
                item_type="short_text",
                original_lines=["原文A"],
                translation_lines=["译文A"],
            )
            retry_source_item = ErrorRetryItem(
                translation_item=TranslationItem(
                    location_path="Map001/events/2",
                    item_type="short_text",
                    original_lines=["原文A"],
                ),
                previous_translation_lines=["旧译文A"],
                error_type="AI漏翻",
                error_detail=["缺少条目"],
            )
            retry_failed_item = (
                "Map001/events/3",
                "short_text",
                None,
                ["原文B"],
                ["旧译文B"],
                "日文残留",
                ["仍存在日文"],
            )
            fake_text_translation = FakeTextTranslation(
                right_batches=[[success_item]],
                error_batches=[[retry_failed_item]],
            )
            request_container = FakeRequestContainer(
                glossary_extraction=FakeGlossaryExtraction(),
                data_text_extraction=FakeDataTextExtraction({}),
                plugin_text_extraction=FakePluginTextExtraction(),
                text_translation=fake_text_translation,
            )
            fake_db = FakeTranslationDB(
                translated_paths=set(),
                glossary=Glossary(
                    roles=[Role(name="勇者", translated_name="Hero", gender="男")],
                    places=[Place(name="城镇", translated_name="Town")],
                ),
                latest_error_table_name="translation_errors_20260102_000000",
                error_retry_items=[retry_source_item],
            )
            fake_progress = FakeProgress()
            handler = build_handler(
                setting=setting,
                game_data=build_game_data(),
                llm_handler=object(),
                translation_db=fake_db,
                request_container=request_container,
            )

            with (
                patch("app.core.handler.get_progress", return_value=fake_progress),
                patch.object(
                    TranslationHandler,
                    "_build_error_table_name",
                    return_value="translation_errors_20990101_000000",
                ),
            ):
                await handler.retry_error_table()
        self.assertIsNotNone(fake_text_translation.started_batches)
        self.assertEqual(
            fake_db.written_translation_items,
            [
                [
                    (
                        "Map001/events/2",
                        "short_text",
                        None,
                        ["原文A"],
                        ["译文A"],
                    )
                ]
            ],
        )
        self.assertEqual(
            fake_db.created_error_tables,
            [
                ("translation_errors_20990101_000000", []),
                ("translation_errors_20990101_000000", [retry_failed_item]),
            ],
        )
        self.assertEqual(fake_progress.tasks[0].total, 1)
        self.assertEqual(sum(fake_progress.advances), 2)
        self.assertEqual(fake_progress.tasks[0].completed, 1)
        self.assertIn("错误表重翻译完成", fake_progress.descriptions[-1])

    async def test_write_back_reads_glossary_from_database(self) -> None:
        """回写流程应从数据库读取术语表，而不是依赖外部结构化文件。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir: Path = Path(temp_dir)
            setting: Setting = build_setting(base_dir)
            game_data = GameData.model_construct(
                data={
                    "Map001.json": {
                        "displayName": "城镇",
                        "events": [],
                    }
                },
                writable_data={},
                map_data={},
                system=None,
                common_events=[],
                troops=[],
                base_data={},
                plugins_js=[],
                writable_plugins_js=[],
            )
            fake_db = FakeTranslationDB(
                translated_paths=set(),
                glossary=Glossary(
                    places=[Place(name="城镇", translated_name="Town")],
                ),
            )
            request_container = FakeRequestContainer(
                glossary_extraction=FakeGlossaryExtraction(),
                data_text_extraction=FakeDataTextExtraction({}),
                plugin_text_extraction=FakePluginTextExtraction(),
                text_translation=FakeTextTranslation(right_batches=[], error_batches=[]),
            )
            fake_progress = FakeProgress()
            handler = build_handler(
                setting=setting,
                game_data=game_data,
                llm_handler=object(),
                translation_db=fake_db,
                request_container=request_container,
            )
            with patch("app.core.handler.get_progress", return_value=fake_progress):
                await handler.write_back()

            written_map_path: Path = setting.project.file_path / "data" / "Map001.json"
            written_map_data = json.loads(written_map_path.read_text(encoding="utf-8"))

        self.assertEqual(written_map_data["displayName"], "Town")

if __name__ == "__main__":
    unittest.main()

