"""
术语流式改造测试。

覆盖术语翻译层逐块产出，以及术语构建流程的
流式转发并最终写入数据库术语表的关键行为。
"""

import json
import tempfile
import unittest
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

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
from app.extraction import GlossaryExtraction
from app.models.schemas import GameData, Glossary, GlossaryBuildChunk, Place, Role
from app.services.llm import LLMHandler
from app.translation import GlossaryTranslation


class FakeLLMHandler:
    """按预设顺序返回响应的大模型替身对象。"""

    def __init__(self, responses: list[str]) -> None:
        self.responses: list[str] = list(responses)
        self.calls: int = 0

    async def get_ai_response(
        self,
        *,
        service_name: str,
        model: str,
        messages: list[Any],
        retry_count: int,
        retry_delay: int,
    ) -> str:
        """返回预设响应，并记录调用次数。"""
        del service_name, model, messages, retry_count, retry_delay
        self.calls += 1
        if not self.responses:
            raise AssertionError("FakeLLMHandler 没有更多可用响应")
        return self.responses.pop(0)


class FakeGlossaryExtraction:
    """返回固定术语提取结果的假提取器。"""

    def __init__(
        self,
        role_lines: dict[str, list[str]],
        display_names: dict[str, str],
    ) -> None:
        self.role_lines: dict[str, list[str]] = role_lines
        self.display_names: dict[str, str] = display_names

    def extract_role_dialogue_chunks(
        self,
        chunk_blocks: int,
        chunk_lines: int,
    ) -> dict[str, list[str]]:
        """返回预设角色名样本。"""
        del chunk_blocks, chunk_lines
        return self.role_lines

    def extract_display_names(self) -> dict[str, str]:
        """返回预设地点名。"""
        return self.display_names


class FakeRequestContainer:
    """模拟依赖注入框架的请求作用域容器。"""

    def __init__(
        self,
        glossary_extraction: FakeGlossaryExtraction,
        glossary_translation: GlossaryTranslation,
    ) -> None:
        self.glossary_extraction: FakeGlossaryExtraction = glossary_extraction
        self.glossary_translation: GlossaryTranslation = glossary_translation

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
        del exc_type, exc, tb
        return None

    async def get(self, cls: type[Any]) -> Any:
        """按请求类型返回预设对象。"""
        if cls is GlossaryExtraction:
            return self.glossary_extraction
        if cls is GlossaryTranslation:
            return self.glossary_translation
        raise AssertionError(f"未处理的依赖请求: {cls}")


class FakeTranslationProvider:
    """
    模拟统一依赖提供器的假对象。

    这个替身负责：
    1. 返回术语构建所需的进程级依赖。
    2. 返回术语构建时使用的请求作用域容器。
    """

    def __init__(
        self,
        *,
        request_container: FakeRequestContainer,
    ) -> None:
        self.request_container: FakeRequestContainer = request_container

    def open_request_scope(self) -> FakeRequestContainer:
        """返回预设的请求作用域容器。"""
        return self.request_container


class FakeGlossaryDB:
    """用于术语流程测试的假数据库对象。"""

    def __init__(self, glossary: Glossary | None = None) -> None:
        self.glossary: Glossary | None = glossary
        self.replace_calls: int = 0

    async def read_glossary(self) -> Glossary | None:
        """返回预设术语表。"""
        return self.glossary

    async def replace_glossary(self, glossary: Glossary) -> None:
        """记录整表替换后的术语表。"""
        self.glossary = glossary
        self.replace_calls += 1


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
    """构造本组测试足够使用的游戏数据替身对象。"""
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


async def collect_chunks(async_iterator: AsyncIterator[Any]) -> list[Any]:
    """收集异步生成器产出的全部分块结果。"""
    chunks: list[Any] = []
    async for chunk in async_iterator:
        chunks.append(chunk)
    return chunks


def build_handler(
    *,
    setting: Setting,
    game_data: GameData,
    llm_handler: Any,
    translation_db: Any,
    request_container: FakeRequestContainer,
) -> TranslationHandler:
    return TranslationHandler(
        provider=cast(
            TranslationProvider,
            cast(
                object,
                FakeTranslationProvider(request_container=request_container),
            ),
        ),
        setting=setting,
        game_data=game_data,
        llm_handler=llm_handler,
        translation_db=translation_db,
    )


class GlossaryStreamingTestCase(unittest.IsolatedAsyncioTestCase):
    """术语流式改造的异步测试。"""

    async def test_translate_role_names_yields_each_chunk(self) -> None:
        """角色术语翻译应按块依次产出结构化角色对象。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            setting: Setting = build_setting(Path(temp_dir))
            glossary_translation = GlossaryTranslation(setting)
            llm_handler = FakeLLMHandler(
                responses=[
                    json.dumps(
                        {"勇者": {"译名": "Hero", "性别": "男"}},
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {"魔王": {"译名": "Demon Lord", "性别": "未知"}},
                        ensure_ascii=False,
                    ),
                ]
            )

            result = await collect_chunks(
                glossary_translation.translate_role_names(
                    llm_handler=cast(LLMHandler, cast(object, llm_handler)),
                    role_lines={
                        "勇者": ["你好"],
                        "魔王": ["欢迎来到终点"],
                    },
                )
            )

        self.assertEqual(
            result,
            [
                [Role(name="勇者", translated_name="Hero", gender="男")],
                [Role(name="魔王", translated_name="Demon Lord", gender="未知")],
            ],
        )
        self.assertEqual(llm_handler.calls, 2)

    async def test_translate_display_names_retry_success_only_yields_once(self) -> None:
        """地点术语分块重试成功时，只应在成功后产出一次结果。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            setting: Setting = build_setting(Path(temp_dir))
            glossary_translation = GlossaryTranslation(setting)
            llm_handler = FakeLLMHandler(
                responses=[
                    json.dumps({"其他地图": {"译名": "Wrong"}}, ensure_ascii=False),
                    json.dumps({"城镇": {"译名": "Town"}}, ensure_ascii=False),
                ]
            )

            result = await collect_chunks(
                glossary_translation.translate_display_names(
                    llm_handler=cast(LLMHandler, cast(object, llm_handler)),
                    display_names={"城镇": ""},
                    roles=[Role(name="勇者", translated_name="Hero", gender="男")],
                )
            )

        self.assertEqual(
            result,
            [[Place(name="城镇", translated_name="Town")]],
        )
        self.assertEqual(llm_handler.calls, 2)

    async def test_build_glossary_streams_chunks_and_saves_to_database(self) -> None:
        """编排器应转发每个结构化分块，并在结束后把完整术语表写入数据库。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir: Path = Path(temp_dir)
            setting: Setting = build_setting(base_dir)
            glossary_translation = GlossaryTranslation(setting)
            glossary_extraction = FakeGlossaryExtraction(
                role_lines={
                    "勇者": ["你好"],
                    "魔王": ["欢迎来到终点"],
                },
                display_names={
                    "城镇": "",
                    "洞窟": "",
                },
            )
            llm_handler = FakeLLMHandler(
                responses=[
                    json.dumps(
                        {"勇者": {"译名": "Hero", "性别": "男"}},
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {"魔王": {"译名": "Demon Lord", "性别": "未知"}},
                        ensure_ascii=False,
                    ),
                    json.dumps({"城镇": {"译名": "Town"}}, ensure_ascii=False),
                    json.dumps({"洞窟": {"译名": "Cave"}}, ensure_ascii=False),
                ]
            )
            fake_db = FakeGlossaryDB()
            handler = build_handler(
                setting=setting,
                game_data=build_game_data(),
                llm_handler=llm_handler,
                translation_db=fake_db,
                request_container=FakeRequestContainer(
                    glossary_extraction=glossary_extraction,
                    glossary_translation=glossary_translation,
                ),
            )
            result = await collect_chunks(handler.build_glossary())

        self.assertEqual(
            result,
            [
                GlossaryBuildChunk(
                    kind="roles",
                    items=[Role(name="勇者", translated_name="Hero", gender="男")],
                ),
                GlossaryBuildChunk(
                    kind="roles",
                    items=[
                        Role(
                            name="魔王",
                            translated_name="Demon Lord",
                            gender="未知",
                        )
                    ],
                ),
                GlossaryBuildChunk(
                    kind="places",
                    items=[Place(name="城镇", translated_name="Town")],
                ),
                GlossaryBuildChunk(
                    kind="places",
                    items=[Place(name="洞窟", translated_name="Cave")],
                ),
            ],
        )
        self.assertIsNotNone(fake_db.glossary)
        assert fake_db.glossary is not None
        self.assertEqual(
            fake_db.glossary.model_dump(),
            Glossary(
                roles=[
                    Role(name="勇者", translated_name="Hero", gender="男"),
                    Role(name="魔王", translated_name="Demon Lord", gender="未知"),
                ],
                places=[
                    Place(name="城镇", translated_name="Town"),
                    Place(name="洞窟", translated_name="Cave"),
                ],
            ).model_dump(),
        )
        self.assertEqual(fake_db.replace_calls, 1)

    async def test_build_glossary_empty_input_writes_empty_glossary_to_database(self) -> None:
        """没有术语时不产出分块，但应写出空术语表到数据库。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir: Path = Path(temp_dir)
            setting: Setting = build_setting(base_dir)
            glossary_translation = GlossaryTranslation(setting)
            glossary_extraction = FakeGlossaryExtraction(
                role_lines={},
                display_names={},
            )
            fake_db = FakeGlossaryDB()
            handler = build_handler(
                setting=setting,
                game_data=build_game_data(),
                llm_handler=FakeLLMHandler(responses=[]),
                translation_db=fake_db,
                request_container=FakeRequestContainer(
                    glossary_extraction=glossary_extraction,
                    glossary_translation=glossary_translation,
                ),
            )
            result = await collect_chunks(handler.build_glossary())

        self.assertEqual(result, [])
        self.assertIsNotNone(fake_db.glossary)
        assert fake_db.glossary is not None
        self.assertEqual(fake_db.glossary.model_dump(), Glossary().model_dump())
        self.assertEqual(fake_db.replace_calls, 1)

    async def test_build_glossary_skips_when_database_glossary_is_complete(self) -> None:
        """数据库中已有完整术语表时，应直接跳过重建。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir: Path = Path(temp_dir)
            setting: Setting = build_setting(base_dir)
            glossary_translation = GlossaryTranslation(setting)
            glossary_extraction = FakeGlossaryExtraction(
                role_lines={"勇者": ["你好"]},
                display_names={"城镇": ""},
            )
            llm_handler = FakeLLMHandler(responses=[])
            fake_db = FakeGlossaryDB(
                glossary=Glossary(
                    roles=[Role(name="勇者", translated_name="Hero", gender="男")],
                    places=[Place(name="城镇", translated_name="Town")],
                )
            )
            handler = build_handler(
                setting=setting,
                game_data=build_game_data(),
                llm_handler=llm_handler,
                translation_db=fake_db,
                request_container=FakeRequestContainer(
                    glossary_extraction=glossary_extraction,
                    glossary_translation=glossary_translation,
                ),
            )
            result = await collect_chunks(handler.build_glossary())

        self.assertEqual(result, [])
        self.assertEqual(llm_handler.calls, 0)
        self.assertEqual(fake_db.replace_calls, 0)


if __name__ == "__main__":
    unittest.main()
