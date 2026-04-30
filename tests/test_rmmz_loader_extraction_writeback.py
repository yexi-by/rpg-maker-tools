"""RMMZ 标准数据加载、提取与正文回写测试。"""

from pathlib import Path

import pytest

from app.application.file_writer import reset_writable_copies
from app.application.file_writer import write_game_files
from app.rmmz import DataTextExtraction, load_game_data
from app.rmmz.text_rules import ensure_json_array, ensure_json_object, get_default_text_rules
from app.rmmz.write_back import write_data_text


@pytest.mark.asyncio
async def test_loader_only_keeps_standard_rmmz_data_files(minimal_game_dir: Path) -> None:
    """加载器接收官方 data 文件，并跳过未知插件衍生 JSON。"""
    game_data = await load_game_data(minimal_game_dir)

    assert "UnknownPluginData.json" not in game_data.data
    assert "System.json" in game_data.data
    assert "Map001.json" in game_data.map_data
    assert game_data.plugins_js[0]["name"] == "TestPlugin"


@pytest.mark.asyncio
async def test_data_extraction_covers_core_text_sources(minimal_game_dir: Path) -> None:
    """正文提取覆盖事件对白、选项、滚动文本、系统词汇和基础数据库。"""
    game_data = await load_game_data(minimal_game_dir)
    extracted = DataTextExtraction(game_data, get_default_text_rules()).extract_all_text()
    paths = {
        item.location_path
        for data in extracted.values()
        for item in data.translation_items
    }

    assert "Map001.json/1/0/0" in paths
    assert "CommonEvents.json/1/0" in paths
    assert "CommonEvents.json/1/2" in paths
    assert "CommonEvents.json/1/3" in paths
    assert "CommonEvents.json/1/4/parameters/3/message" in paths
    assert "System.json/gameTitle" in paths
    assert "Actors.json/1/name" in paths


@pytest.mark.asyncio
async def test_write_data_text_updates_writable_copy(minimal_game_dir: Path) -> None:
    """正文回写修改可写副本，原始加载数据保持不变。"""
    game_data = await load_game_data(minimal_game_dir)
    extracted = DataTextExtraction(game_data, get_default_text_rules()).extract_all_text()
    item = next(
        candidate
        for candidate in extracted["CommonEvents.json"].translation_items
        if candidate.location_path == "CommonEvents.json/1/0"
    )
    item.translation_lines = ["你好"]

    reset_writable_copies(game_data)
    write_data_text(game_data, [item])

    common_events = ensure_json_array(game_data.writable_data["CommonEvents.json"], "CommonEvents")
    common_event = ensure_json_object(common_events[1], "CommonEvents[1]")
    commands = ensure_json_array(common_event["list"], "CommonEvents[1].list")
    text_command = ensure_json_object(commands[1], "CommonEvents[1].list[1]")
    parameters = ensure_json_array(text_command["parameters"], "CommonEvents[1].list[1].parameters")
    assert parameters[0] == "你好"


@pytest.mark.asyncio
async def test_first_write_back_only_archives_affected_data_files(minimal_game_dir: Path) -> None:
    """首次磁盘回写把受影响原文件复制到 `data_origin/`。"""
    game_data = await load_game_data(minimal_game_dir)
    extracted = DataTextExtraction(game_data, get_default_text_rules()).extract_all_text()
    item = next(
        candidate
        for candidate in extracted["CommonEvents.json"].translation_items
        if candidate.location_path == "CommonEvents.json/1/0"
    )
    item.translation_lines = ["你好"]

    reset_writable_copies(game_data)
    write_data_text(game_data, [item])
    write_game_files(game_data, minimal_game_dir)

    assert (minimal_game_dir / "data_origin" / "CommonEvents.json").exists()
    assert not (minimal_game_dir / "data_origin" / "System.json").exists()
    assert not (minimal_game_dir / "js" / "plugins_origin.js").exists()

    active_common_events = ensure_json_array(
        game_data.writable_data["CommonEvents.json"],
        "CommonEvents",
    )
    active_event = ensure_json_object(active_common_events[1], "CommonEvents[1]")
    active_commands = ensure_json_array(active_event["list"], "CommonEvents[1].list")
    active_text_command = ensure_json_object(active_commands[1], "CommonEvents[1].list[1]")
    active_parameters = ensure_json_array(
        active_text_command["parameters"],
        "CommonEvents[1].list[1].parameters",
    )
    assert active_parameters[0] == "你好"


@pytest.mark.asyncio
async def test_old_game_reads_archived_files_without_touching_new_backups(minimal_game_dir: Path) -> None:
    """留档布局优先读取原件留档，二次写回保持留档不变。"""
    first_game_data = await load_game_data(minimal_game_dir)
    extracted = DataTextExtraction(first_game_data, get_default_text_rules()).extract_all_text()
    common_item = next(
        candidate
        for candidate in extracted["CommonEvents.json"].translation_items
        if candidate.location_path == "CommonEvents.json/1/0"
    )
    common_item.translation_lines = ["你好"]
    reset_writable_copies(first_game_data)
    write_data_text(first_game_data, [common_item])
    write_game_files(first_game_data, minimal_game_dir)

    reloaded_game_data = await load_game_data(minimal_game_dir)
    reloaded_extracted = DataTextExtraction(reloaded_game_data, get_default_text_rules()).extract_all_text()
    reloaded_common_item = next(
        candidate
        for candidate in reloaded_extracted["CommonEvents.json"].translation_items
        if candidate.location_path == "CommonEvents.json/1/0"
    )
    assert reloaded_common_item.original_lines == ["こんにちは"]

    actor_item = next(
        candidate
        for candidate in reloaded_extracted["Actors.json"].translation_items
        if candidate.location_path == "Actors.json/1/name"
    )
    actor_item.translation_lines = ["勇者译名"]
    reset_writable_copies(reloaded_game_data)
    write_data_text(reloaded_game_data, [actor_item])
    write_game_files(reloaded_game_data, minimal_game_dir)

    assert not (minimal_game_dir / "data_origin" / "Actors.json").exists()
