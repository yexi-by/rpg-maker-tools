"""SQLite 持久化层测试。"""

import sqlite3
from pathlib import Path
from typing import cast

import pytest

from app.terminology import TerminologyGlossary, TerminologyRegistry
from app.persistence import GameRegistry
from app.rmmz.schema import (
    EventCommandParameterFilter,
    EventCommandTextRuleRecord,
    LlmFailureRecord,
    PlaceholderRuleRecord,
    PluginTextRuleRecord,
    TranslationErrorItem,
    TranslationItem,
)


@pytest.mark.asyncio
async def test_registry_and_target_session_use_injected_directory(minimal_game_dir: Path, tmp_path: Path) -> None:
    """注册表支持测试注入目录，单游戏会话能读写核心表并关闭连接。"""
    db_dir = tmp_path / "db"
    registry = GameRegistry(db_dir)
    record = await registry.register_game(minimal_game_dir, source_language="ja")
    assert record.game_title == "テストゲーム"
    assert record.engine_kind == "mz"
    assert record.content_root == minimal_game_dir
    assert record.source_language == "ja"
    assert record.target_language == "zh-Hans"
    assert [item.game_title for item in await registry.list_games()] == ["テストゲーム"]

    async with await registry.open_game("テストゲーム") as session:
        assert session.source_language == "ja"
        assert session.target_language == "zh-Hans"
        await session.write_translation_items(
            [
                TranslationItem(
                    location_path="System.json/gameTitle",
                    item_type="short_text",
                    role=None,
                    original_lines=["テストゲーム"],
                    source_line_paths=[],
                    translation_lines=["测试游戏"],
                )
            ],
        )
        translated_items = await session.read_translated_items()
        assert translated_items[0].translation_lines == ["测试游戏"]
        assert translated_items[0].source_line_paths == []
        await session.write_translation_items(
            [
                TranslationItem(
                    location_path="CommonEvents.json/1/0",
                    item_type="long_text",
                    role="アリス",
                    original_lines=["こんにちは"],
                    source_line_paths=["CommonEvents.json/1/1"],
                    translation_lines=["你好"],
                )
            ],
        )
        translated_long_item = next(
            item
            for item in await session.read_translated_items()
            if item.location_path == "CommonEvents.json/1/0"
        )
        assert translated_long_item.source_line_paths == ["CommonEvents.json/1/1"]
        await session.write_translation_items(
            [
                TranslationItem(
                    location_path="plugins.js/0/Title",
                    item_type="short_text",
                    role=None,
                    original_lines=["Untitled"],
                    source_line_paths=[],
                    translation_lines=["无标题"],
                )
            ],
        )
        deleted_count = await session.delete_translation_items_except_paths(
            {"System.json/gameTitle"},
        )
        assert deleted_count == 2
        assert await session.read_translation_location_paths() == {
            "System.json/gameTitle"
        }
        assert session.engine_kind == "mz"
        assert session.content_root == minimal_game_dir

        rule = PluginTextRuleRecord(
            plugin_index=0,
            plugin_name="TestPlugin",
            plugin_hash="hash",
            path_templates=["$['parameters']['Message']"],
        )
        await session.replace_plugin_text_rules([rule])
        assert await session.read_plugin_text_rules() == [rule]

        event_rule = EventCommandTextRuleRecord(
            command_code=357,
            parameter_filters=[
                EventCommandParameterFilter(index=0, value="TestPlugin"),
            ],
            path_templates=["$['parameters'][3]['message']"],
        )
        await session.replace_event_command_text_rules([event_rule])
        assert await session.read_event_command_text_rules() == [event_rule]

        terminology_registry = TerminologyRegistry(
            speaker_names={"アリス": "爱丽丝"},
            map_display_names={"始まりの町": "起始之镇"},
            skill_names={"火の術": "火术"},
        )
        await session.replace_terminology_registry(terminology_registry)
        assert await session.read_terminology_registry() == terminology_registry
        terminology_glossary = TerminologyGlossary(
            terms={"アリス": "爱丽丝"},
        )
        await session.replace_terminology_glossary(terminology_glossary)
        assert await session.read_terminology_glossary() == terminology_glossary
        empty_terminology_registry = TerminologyRegistry()
        await session.replace_terminology_registry(empty_terminology_registry)
        assert await session.read_terminology_registry() == empty_terminology_registry
        empty_terminology_glossary = TerminologyGlossary()
        await session.replace_terminology_glossary(empty_terminology_glossary)
        assert await session.read_terminology_glossary() == empty_terminology_glossary

        placeholder_rule = PlaceholderRuleRecord(
            pattern_text=r"\\F\[[^\]]+\]",
            placeholder_template="[CUSTOM_FACE_PORTRAIT_{index}]",
        )
        await session.replace_placeholder_rules([placeholder_rule])
        assert await session.read_placeholder_rules() == [placeholder_rule]

        run_record = await session.start_translation_run(
            total_extracted=10,
            pending_count=4,
            deduplicated_count=3,
            batch_count=2,
        )
        await session.write_translation_quality_errors(
            run_record.run_id,
            [
                TranslationErrorItem(
                    location_path="Map001.json/1/0/0",
                    item_type="long_text",
                    role=None,
                    original_lines=["原文"],
                    translation_lines=[],
                    error_type="AI漏翻",
                    error_detail=["无法解析"],
                    model_response="模型原始返回",
                )
            ],
        )
        quality_errors = await session.read_translation_quality_errors(run_record.run_id)
        assert quality_errors[0].model_response == "模型原始返回"
        await session.write_translation_run(
            run_record.model_copy(
                update={
                    "success_count": 2,
                    "quality_error_count": 1,
                }
            )
        )
        quality_errors_after_progress_update = await session.read_translation_quality_errors(
            run_record.run_id
        )
        assert quality_errors_after_progress_update[0].model_response == "模型原始返回"

        await session.write_llm_failure(
            LlmFailureRecord(
                run_id=run_record.run_id,
                category="rate_limit",
                error_type="RateLimitError",
                error_message="请求过于频繁",
                retryable=True,
                attempt_count=3,
                created_at="2026-01-01T00:00:00",
            )
        )
        llm_failures = await session.read_llm_failures(run_record.run_id)
        assert llm_failures[0].category == "rate_limit"


@pytest.mark.asyncio
async def test_register_game_updates_source_language_setting(
    minimal_english_game_dir: Path,
    tmp_path: Path,
) -> None:
    """重复注册同一游戏时会按本次参数更新源语言设置。"""
    registry = GameRegistry(tmp_path / "db")

    english_record = await registry.register_game(minimal_english_game_dir, source_language="en")
    japanese_record = await registry.register_game(minimal_english_game_dir, source_language="ja")

    assert english_record.source_language == "en"
    assert japanese_record.source_language == "ja"
    async with await registry.open_game("English Fixture Game") as session:
        assert session.source_language == "ja"
        assert session.target_language == "zh-Hans"


@pytest.mark.asyncio
async def test_open_game_requires_language_settings_without_creating_empty_table(tmp_path: Path) -> None:
    """缺少语言设置的旧库会直接报错，运行时不会写入空语言表。"""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    db_path = db_dir / "Legacy.db"
    with sqlite3.connect(db_path) as connection:
        _ = connection.execute(
            """
            CREATE TABLE metadata (
                metadata_key TEXT PRIMARY KEY,
                game_title TEXT NOT NULL,
                game_path TEXT NOT NULL,
                engine_kind TEXT NOT NULL,
                content_root TEXT NOT NULL,
                engine_version TEXT NOT NULL
            )
            """
        )
        _ = connection.execute(
            """
            INSERT INTO metadata (
                metadata_key,
                game_title,
                game_path,
                engine_kind,
                content_root,
                engine_version
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "current_game",
                "Legacy",
                str(tmp_path),
                "mz",
                str(tmp_path),
                "1.0.0",
            ),
        )
    registry = GameRegistry(db_dir)

    with pytest.raises(RuntimeError, match="语言设置表"):
        _ = await registry.open_game("Legacy")

    with sqlite3.connect(db_path) as connection:
        # sqlite3 类型存根无法表达当前 SELECT 的行形状，这里立即收窄为字符串元组列表。
        table_rows = cast(
            list[tuple[str]],
            connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall(),
        )
        table_names = {row[0] for row in table_rows}
    assert "language_settings" not in table_names


@pytest.mark.asyncio
async def test_registry_stores_mv_engine_and_content_root(
    minimal_mv_game_dir: Path,
    tmp_path: Path,
) -> None:
    """MV 外层目录注册时会保存引擎类型和真实内容目录。"""
    registry = GameRegistry(tmp_path / "db")

    record = await registry.register_game(minimal_mv_game_dir, source_language="ja")

    assert record.game_title == "MVテストゲーム"
    assert record.engine_kind == "mv"
    assert record.engine_version == "1.6.1"
    assert record.content_root == minimal_mv_game_dir / "www"
    async with await registry.open_game("MVテストゲーム") as session:
        assert session.engine_kind == "mv"
        assert session.content_root == minimal_mv_game_dir / "www"


@pytest.mark.asyncio
async def test_start_translation_run_clears_previous_quality_errors(minimal_game_dir: Path, tmp_path: Path) -> None:
    """新一轮正文翻译开始时清空上一轮检查失败明细。"""
    db_dir = tmp_path / "db"
    registry = GameRegistry(db_dir)
    record = await registry.register_game(minimal_game_dir, source_language="ja")

    async with await registry.open_game(record.game_title) as session:
        first_run = await session.start_translation_run(
            total_extracted=10,
            pending_count=4,
            deduplicated_count=3,
            batch_count=2,
        )
        await session.write_translation_quality_errors(
            first_run.run_id,
            [
                TranslationErrorItem(
                    location_path="Map001.json/1/0/0",
                    item_type="long_text",
                    role=None,
                    original_lines=["原文"],
                    translation_lines=[],
                    error_type="AI漏翻",
                    error_detail=["无法解析"],
                    model_response="上一轮模型原始返回",
                )
            ],
        )
        assert len(await session.read_translation_quality_errors(first_run.run_id)) == 1

        second_run = await session.start_translation_run(
            total_extracted=10,
            pending_count=3,
            deduplicated_count=2,
            batch_count=1,
        )

        assert await session.read_translation_quality_errors(first_run.run_id) == []
        assert await session.read_translation_quality_errors(second_run.run_id) == []
