"""RMMZ 标准数据加载、提取与正文回写测试。"""

import json
from pathlib import Path
from typing import cast

import pytest

from app.application.file_writer import reset_writable_copies
from app.application.file_writer import write_game_files
from app.application.font_replacement import apply_font_replacement
from app.rmmz import DataTextExtraction, load_game_data
from app.rmmz.schema import PLUGINS_FILE_NAME
from app.rmmz.text_rules import JsonValue, coerce_json_value, ensure_json_array, ensure_json_object, get_default_text_rules
from app.rmmz.write_back import write_data_text


def _rewrite_json(path: Path, value: JsonValue) -> None:
    """以 UTF-8 写回测试 JSON。"""
    _ = path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_test_json(path: Path) -> JsonValue:
    """读取测试 JSON 并收窄为项目 JSON 类型。"""
    return coerce_json_value(cast(object, json.loads(path.read_text(encoding="utf-8"))))


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
    assert "CommonEvents.json/1/4/parameters/3/message" not in paths
    assert "System.json/gameTitle" in paths
    assert "System.json/terms/basic/1" not in paths
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
async def test_name_text_write_back_uses_real_401_paths(minimal_game_dir: Path) -> None:
    """名字框正文按实际 401 路径写回，不按相邻下标猜测。"""
    common_events_path = minimal_game_dir / "data" / "CommonEvents.json"
    raw_events = _read_test_json(common_events_path)
    events = ensure_json_array(raw_events, "CommonEvents")
    event = ensure_json_object(events[1], "CommonEvents[1]")
    event_commands = ensure_json_array(event["list"], "CommonEvents[1].list")
    event_commands.insert(1, {"code": 401, "parameters": [""]})
    _rewrite_json(common_events_path, raw_events)

    game_data = await load_game_data(minimal_game_dir)
    extracted = DataTextExtraction(game_data, get_default_text_rules()).extract_all_text()
    item = next(
        candidate
        for candidate in extracted["CommonEvents.json"].translation_items
        if candidate.location_path == "CommonEvents.json/1/0"
    )
    assert item.original_lines == ["こんにちは"]
    assert item.source_line_paths == ["CommonEvents.json/1/2"]

    item.translation_lines = ["你好"]
    reset_writable_copies(game_data)
    write_data_text(game_data, [item])

    common_events = ensure_json_array(game_data.writable_data["CommonEvents.json"], "CommonEvents")
    common_event = ensure_json_object(common_events[1], "CommonEvents[1]")
    commands = ensure_json_array(common_event["list"], "CommonEvents[1].list")
    blank_text_command = ensure_json_object(commands[1], "CommonEvents[1].list[1]")
    translated_text_command = ensure_json_object(commands[2], "CommonEvents[1].list[2]")
    blank_parameters = ensure_json_array(blank_text_command["parameters"], "blank.parameters")
    translated_parameters = ensure_json_array(
        translated_text_command["parameters"],
        "translated.parameters",
    )
    assert blank_parameters[0] == ""
    assert translated_parameters[0] == "你好"


@pytest.mark.asyncio
async def test_name_text_write_back_inserts_extra_401_lines(minimal_game_dir: Path) -> None:
    """名字框正文译文行数增加时，在原文本块末尾插入新的 401。"""
    game_data = await load_game_data(minimal_game_dir)
    extracted = DataTextExtraction(game_data, get_default_text_rules()).extract_all_text()
    item = next(
        candidate
        for candidate in extracted["CommonEvents.json"].translation_items
        if candidate.location_path == "CommonEvents.json/1/0"
    )
    item.translation_lines = ["你好", "第二行", "第三行"]

    reset_writable_copies(game_data)
    write_data_text(game_data, [item])

    common_events = ensure_json_array(game_data.writable_data["CommonEvents.json"], "CommonEvents")
    common_event = ensure_json_object(common_events[1], "CommonEvents[1]")
    commands = ensure_json_array(common_event["list"], "CommonEvents[1].list")
    first_text = ensure_json_object(commands[1], "CommonEvents[1].list[1]")
    second_text = ensure_json_object(commands[2], "CommonEvents[1].list[2]")
    third_text = ensure_json_object(commands[3], "CommonEvents[1].list[3]")
    choice_command = ensure_json_object(commands[4], "CommonEvents[1].list[4]")

    assert first_text["code"] == 401
    assert second_text["code"] == 401
    assert third_text["code"] == 401
    assert choice_command["code"] == 102
    assert ensure_json_array(first_text["parameters"], "first.parameters")[0] == "你好"
    assert ensure_json_array(second_text["parameters"], "second.parameters")[0] == "第二行"
    assert ensure_json_array(third_text["parameters"], "third.parameters")[0] == "第三行"


@pytest.mark.asyncio
async def test_scroll_text_commands_are_grouped_by_adjacent_405(minimal_game_dir: Path) -> None:
    """连续 405 滚动文本作为一个翻译单元提取，并支持额外译文行写回。"""
    common_events_path = minimal_game_dir / "data" / "CommonEvents.json"
    raw_events = _read_test_json(common_events_path)
    events = ensure_json_array(raw_events, "CommonEvents")
    event = ensure_json_object(events[1], "CommonEvents[1]")
    event["list"] = [
        {"code": 101, "parameters": [0, 0, 0, 2, "アリス"]},
        {"code": 401, "parameters": ["こんにちは"]},
        {"code": 405, "parameters": ["スクロール一行目"]},
        {"code": 405, "parameters": ["スクロール二行目"]},
        {"code": 405, "parameters": [""]},
        {"code": 405, "parameters": ["別段落"]},
        {"code": 0, "parameters": []},
    ]
    _rewrite_json(common_events_path, raw_events)

    game_data = await load_game_data(minimal_game_dir)
    extracted = DataTextExtraction(game_data, get_default_text_rules()).extract_all_text()
    first_scroll_item = next(
        candidate
        for candidate in extracted["CommonEvents.json"].translation_items
        if candidate.location_path == "CommonEvents.json/1/2"
    )
    second_scroll_item = next(
        candidate
        for candidate in extracted["CommonEvents.json"].translation_items
        if candidate.location_path == "CommonEvents.json/1/5"
    )
    assert first_scroll_item.original_lines == ["スクロール一行目", "スクロール二行目"]
    assert first_scroll_item.source_line_paths == [
        "CommonEvents.json/1/2",
        "CommonEvents.json/1/3",
    ]
    assert second_scroll_item.original_lines == ["別段落"]

    first_scroll_item.translation_lines = ["滚动第一行", "滚动第二行", "滚动第三行"]
    second_scroll_item.translation_lines = ["另一段"]
    reset_writable_copies(game_data)
    write_data_text(game_data, [first_scroll_item, second_scroll_item])

    common_events = ensure_json_array(game_data.writable_data["CommonEvents.json"], "CommonEvents")
    common_event = ensure_json_object(common_events[1], "CommonEvents[1]")
    commands = ensure_json_array(common_event["list"], "CommonEvents[1].list")
    assert ensure_json_array(ensure_json_object(commands[2], "command2")["parameters"], "command2.parameters")[0] == "滚动第一行"
    assert ensure_json_array(ensure_json_object(commands[3], "command3")["parameters"], "command3.parameters")[0] == "滚动第二行"
    assert ensure_json_array(ensure_json_object(commands[4], "command4")["parameters"], "command4.parameters")[0] == "滚动第三行"
    assert ensure_json_array(ensure_json_object(commands[5], "command5")["parameters"], "command5.parameters")[0] == ""
    assert ensure_json_array(ensure_json_object(commands[6], "command6")["parameters"], "command6.parameters")[0] == "另一段"


@pytest.mark.asyncio
async def test_long_text_write_back_clears_extra_original_lines(minimal_game_dir: Path) -> None:
    """译文行数少于原始 405 行数时，剩余原始行写为空字符串。"""
    common_events_path = minimal_game_dir / "data" / "CommonEvents.json"
    raw_events = _read_test_json(common_events_path)
    events = ensure_json_array(raw_events, "CommonEvents")
    event = ensure_json_object(events[1], "CommonEvents[1]")
    event["list"] = [
        {"code": 405, "parameters": ["スクロール一行目"]},
        {"code": 405, "parameters": ["スクロール二行目"]},
        {"code": 0, "parameters": []},
    ]
    _rewrite_json(common_events_path, raw_events)

    game_data = await load_game_data(minimal_game_dir)
    extracted = DataTextExtraction(game_data, get_default_text_rules()).extract_all_text()
    scroll_item = next(
        candidate
        for candidate in extracted["CommonEvents.json"].translation_items
        if candidate.location_path == "CommonEvents.json/1/0"
    )
    assert scroll_item.original_lines == ["スクロール一行目", "スクロール二行目"]

    scroll_item.translation_lines = ["滚动第一行"]
    reset_writable_copies(game_data)
    write_data_text(game_data, [scroll_item])

    common_events = ensure_json_array(game_data.writable_data["CommonEvents.json"], "CommonEvents")
    common_event = ensure_json_object(common_events[1], "CommonEvents[1]")
    commands = ensure_json_array(common_event["list"], "CommonEvents[1].list")
    assert ensure_json_array(ensure_json_object(commands[0], "command0")["parameters"], "command0.parameters")[0] == "滚动第一行"
    assert ensure_json_array(ensure_json_object(commands[1], "command1")["parameters"], "command1.parameters")[0] == ""


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


@pytest.mark.asyncio
async def test_font_replacement_updates_only_writable_outputs(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """字体替换只作用于本轮可写副本，并复制目标字体到游戏目录。"""
    fonts_dir = minimal_game_dir / "fonts"
    fonts_dir.mkdir()
    old_font = "OldFont.woff"
    another_font = "AnotherFont.woff"
    _ = (fonts_dir / old_font).write_bytes(b"old font")
    _ = (fonts_dir / another_font).write_bytes(b"another font")
    replacement_font = tmp_path / "NotoSansSC-Regular.ttf"
    _ = replacement_font.write_bytes(b"new font")

    game_data = await load_game_data(minimal_game_dir)
    reset_writable_copies(game_data)
    system = ensure_json_object(game_data.writable_data["System.json"], "System")
    system["advanced"] = {
        "mainFontFilename": old_font,
        "numberFontFilename": another_font,
    }
    plugin = ensure_json_object(game_data.writable_plugins_js[0], "plugins[0]")
    parameters = ensure_json_object(plugin["parameters"], "plugins[0].parameters")
    parameters["FontFace"] = old_font
    parameters["FontStem"] = Path(old_font).stem
    parameters["Nested"] = json.dumps(
        {"font": another_font, "text": "プラグイン本文"},
        ensure_ascii=False,
    )

    summary = apply_font_replacement(
        game_data=game_data,
        game_root=minimal_game_dir,
        replacement_font_path=str(replacement_font),
    )

    replacement_name = replacement_font.name
    assert (fonts_dir / replacement_name).exists()
    assert summary.target_font_name == replacement_name
    assert summary.source_font_count == 2
    assert summary.replaced_reference_count == 5
    serialized_system = json.dumps(game_data.writable_data["System.json"], ensure_ascii=False)
    serialized_plugins = str(game_data.writable_data[PLUGINS_FILE_NAME])
    assert old_font not in serialized_system
    assert another_font not in serialized_system
    assert old_font not in serialized_plugins
    assert another_font not in serialized_plugins
    assert Path(old_font).stem not in serialized_plugins
    assert replacement_name in serialized_system
    assert replacement_name in serialized_plugins
    original_system = json.dumps(game_data.data["System.json"], ensure_ascii=False)
    assert old_font not in original_system
    assert another_font not in original_system
