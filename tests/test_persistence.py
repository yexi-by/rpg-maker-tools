"""SQLite 持久化层测试。"""

from pathlib import Path

import pytest

from app.name_context.schemas import NameContextRegistry
from app.persistence import GameRegistry
from app.rmmz.schema import (
    EventCommandParameterFilter,
    EventCommandTextRuleRecord,
    PluginTextRuleRecord,
    TranslationErrorItem,
    TranslationItem,
)


@pytest.mark.asyncio
async def test_registry_and_target_session_use_injected_directory(minimal_game_dir: Path, tmp_path: Path) -> None:
    """注册表支持测试注入目录，单游戏会话能读写核心表并关闭连接。"""
    db_dir = tmp_path / "db"
    registry = GameRegistry(db_dir)
    record = await registry.register_game(minimal_game_dir)
    assert record.game_title == "テストゲーム"
    assert [item.game_title for item in await registry.list_games()] == ["テストゲーム"]

    async with await registry.open_game("テストゲーム") as session:
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

        name_registry = NameContextRegistry(
            speaker_names={"アリス": "爱丽丝"},
            map_display_names={"始まりの町": "起始之镇"},
        )
        await session.replace_name_context_registry(name_registry)
        assert await session.read_name_context_registry() == name_registry

        error_table_name = await session.start_error_table()
        await session.write_error_items(
            error_table_name,
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
        error_rows = await session.read_table(error_table_name)
        assert error_rows[0]["model_response"] == "模型原始返回"
