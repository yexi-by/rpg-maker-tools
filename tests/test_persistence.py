"""SQLite 持久化层测试。"""

from pathlib import Path

import pytest

from app.name_context.schemas import NameContextRegistry, NameLocation, NameRegistryEntry
from app.persistence import GameDatabaseManager
from app.rmmz.schema import PluginTextRuleRecord, PluginTextTranslateRule


@pytest.mark.asyncio
async def test_database_manager_uses_injected_directory_and_closes(minimal_game_dir: Path, tmp_path: Path) -> None:
    """数据库管理器支持测试注入目录，并能读写核心表后正常关闭连接。"""
    db_dir = tmp_path / "db"
    manager = await GameDatabaseManager.new(db_dir)
    try:
        await manager.create_database(minimal_game_dir)
        assert "テストゲーム" in manager.items

        await manager.write_translation_items(
            "テストゲーム",
            [
                (
                    "System.json/gameTitle",
                    "short_text",
                    None,
                    ["テストゲーム"],
                    ["测试游戏"],
                )
            ],
        )
        translated_items = await manager.read_translated_items("テストゲーム")
        assert translated_items[0].translation_lines == ["测试游戏"]

        rule = PluginTextRuleRecord(
            plugin_index=0,
            plugin_name="TestPlugin",
            plugin_hash="hash",
            translate_rules=[
                PluginTextTranslateRule(path_template="$['parameters']['Message']", reason="玩家可见文本")
            ],
            imported_at="2026-04-30T00:00:00+00:00",
        )
        await manager.replace_plugin_text_rules("テストゲーム", [rule])
        assert await manager.read_plugin_text_rules("テストゲーム") == [rule]

        registry = NameContextRegistry(
            game_title="テストゲーム",
            generated_at="2026-04-30T00:00:00+00:00",
            entries=[
                NameRegistryEntry(
                    entry_id="speaker_1",
                    kind="speaker_name",
                    source_text="アリス",
                    translated_text="爱丽丝",
                    locations=[
                        NameLocation(
                            location_path="Map001.json/1/0/0",
                            file_name="Map001.json",
                        )
                    ],
                )
            ],
        )
        await manager.replace_name_context_registry("テストゲーム", registry)
        assert await manager.read_name_context_registry("テストゲーム") == registry
    finally:
        await manager.close()

    assert manager.items == {}
