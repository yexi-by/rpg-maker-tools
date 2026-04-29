"""SQLite 持久化层测试。"""

from pathlib import Path

import pytest

from app.persistence import GameDatabaseManager
from app.rmmz.schema import PluginTextAnalysisState, PluginTextRuleRecord, PluginTextTranslateRule


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

        state = PluginTextAnalysisState(
            plugins_file_hash="plugins",
            prompt_hash="prompt",
            total_plugins=1,
            success_plugins=1,
            failed_plugins=0,
            updated_at="2026-04-30T00:00:00+00:00",
        )
        await manager.write_plugin_text_analysis_state("テストゲーム", state)
        assert await manager.read_plugin_text_analysis_state("テストゲーム") == state

        rule = PluginTextRuleRecord(
            plugin_index=0,
            plugin_name="TestPlugin",
            plugin_hash="hash",
            prompt_hash="prompt",
            status="success",
            translate_rules=[
                PluginTextTranslateRule(path_template="$['parameters']['Message']", reason="玩家可见文本")
            ],
            updated_at="2026-04-30T00:00:00+00:00",
        )
        await manager.upsert_plugin_text_rule("テストゲーム", rule)
        assert await manager.read_plugin_text_rules("テストゲーム") == [rule]
    finally:
        await manager.close()

    assert manager.items == {}
