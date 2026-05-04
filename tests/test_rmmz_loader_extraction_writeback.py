"""RMMZ 标准数据加载、提取与正文回写测试。"""

import json
from pathlib import Path
from typing import cast

import pytest

from app.application.file_writer import reset_writable_copies
from app.application.file_writer import write_game_files
from app.application.font_replacement import (
    apply_font_replacement,
    read_plugins_js_file,
    restore_font_references_from_origin_backups,
)
from app.config.schemas import TextRulesSetting
from app.note_tag_text import NoteTagTextExtraction, build_note_tag_rule_records_from_import
from app.note_tag_text.exporter import collect_note_tag_candidates
from app.rmmz import DataTextExtraction, load_game_data
from app.rmmz.control_codes import CustomPlaceholderRule
from app.rmmz.schema import NoteTagTextRuleRecord, PLUGINS_FILE_NAME
from app.rmmz.text_rules import JsonValue, TextRules, coerce_json_value, ensure_json_array, ensure_json_object, get_default_text_rules
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
    assert "Map002.json" in game_data.map_data
    assert game_data.plugins_js[0]["name"] == "TestPlugin"
    assert game_data.plugins_js[1]["name"] == "ComplexPlugin"


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
    assert "CommonEvents.json/2/0" in paths
    assert "CommonEvents.json/2/4" in paths
    assert "CommonEvents.json/2/5" in paths
    assert "CommonEvents.json/2/8" in paths
    assert "Map001.json/2/0/0" in paths
    assert "Map001.json/2/0/3" in paths
    assert "Map002.json/1/0/0" in paths
    assert "System.json/gameTitle" in paths
    assert "System.json/terms/basic/1" not in paths
    assert "Actors.json/1/name" in paths
    assert "Items.json/1/description" in paths
    assert "Skills.json/1/message1" in paths


@pytest.mark.asyncio
async def test_note_tag_rules_extract_and_write_back_only_target_values(minimal_game_dir: Path) -> None:
    """Note 标签只有导入规则后才进入正文提取，回写只替换目标标签值。"""
    items_path = minimal_game_dir / "data" / "Items.json"
    raw_items = _read_test_json(items_path)
    items = ensure_json_array(raw_items, "Items.json")
    item = ensure_json_object(items[1], "Items.json[1]")
    item["note"] = "<拡張説明:一行目\n二行目>\n<upgrade:1,2,3>\n<ExtendDesc:別説明>"
    _rewrite_json(items_path, raw_items)

    game_data = await load_game_data(minimal_game_dir)
    standard_extracted = DataTextExtraction(game_data, get_default_text_rules()).extract_all_text()
    standard_paths = {
        candidate.location_path
        for data in standard_extracted.values()
        for candidate in data.translation_items
    }
    note_extracted = NoteTagTextExtraction(
        game_data=game_data,
        rule_records=[
            NoteTagTextRuleRecord(
                file_name="Items.json",
                tag_names=["拡張説明", "ExtendDesc"],
            )
        ],
        text_rules=get_default_text_rules(),
    ).extract_all_text()
    note_items = note_extracted["Items.json"].translation_items

    assert "Items.json/1/note/拡張説明" not in standard_paths
    assert [candidate.location_path for candidate in note_items] == [
        "Items.json/1/note/拡張説明",
        "Items.json/1/note/ExtendDesc",
    ]
    assert note_items[0].original_lines == ["一行目\n二行目"]

    note_items[0].translation_lines = ["第一行\n第二行"]
    reset_writable_copies(game_data)
    write_data_text(game_data, [note_items[0]])
    writable_items = ensure_json_array(game_data.writable_data["Items.json"], "Items.json")
    writable_item = ensure_json_object(writable_items[1], "Items.json[1]")

    assert writable_item["note"] == "<拡張説明:第一行\n第二行>\n<upgrade:1,2,3>\n<ExtendDesc:別説明>"


@pytest.mark.asyncio
async def test_note_tag_multiline_value_keeps_line_break_structure_before_write_back(minimal_game_dir: Path) -> None:
    """Note 标签单字段写回不再为了切宽新增换行。"""
    items_path = minimal_game_dir / "data" / "Items.json"
    raw_items = _read_test_json(items_path)
    items = ensure_json_array(raw_items, "Items.json")
    item = ensure_json_object(items[1], "Items.json[1]")
    item["note"] = "<拡張説明:説明\n「原文」>"
    _rewrite_json(items_path, raw_items)
    text_rules = TextRules.from_setting(
        TextRulesSetting(
            long_text_line_width_limit=8,
            line_split_punctuations=["，", "。"],
        )
    )

    game_data = await load_game_data(minimal_game_dir)
    note_items = NoteTagTextExtraction(
        game_data=game_data,
        rule_records=[
            NoteTagTextRuleRecord(
                file_name="Items.json",
                tag_names=["拡張説明"],
            )
        ],
        text_rules=text_rules,
    ).extract_all_text()["Items.json"].translation_items
    note_items[0].translation_lines = ["说明\n「甲乙丙丁戊己，庚辛壬癸」"]

    reset_writable_copies(game_data)
    write_data_text(game_data, [note_items[0]], text_rules)

    writable_items = ensure_json_array(game_data.writable_data["Items.json"], "Items.json")
    writable_item = ensure_json_object(writable_items[1], "Items.json[1]")
    assert writable_item["note"] == "<拡張説明:说明\n「甲乙丙丁戊己，庚辛壬癸」>"


@pytest.mark.asyncio
async def test_note_tag_json_string_leaf_uses_visible_text_protocol(minimal_game_dir: Path) -> None:
    """Note 标签值如果带 JSON 字符串外壳，只翻玩家可见文本并按原结构写回。"""
    items_path = minimal_game_dir / "data" / "Items.json"
    raw_items = _read_test_json(items_path)
    items = ensure_json_array(raw_items, "Items.json")
    item = ensure_json_object(items[1], "Items.json[1]")
    source_note = "\n　" + r"\C[2]詳細説明\C[0]\n次の行" + "　\n"
    item["note"] = f"<拡張説明:{json.dumps(source_note, ensure_ascii=False)}>\n<upgrade:1,2,3>"
    _rewrite_json(items_path, raw_items)

    game_data = await load_game_data(minimal_game_dir)
    candidates = collect_note_tag_candidates(
        game_data=game_data,
        text_rules=get_default_text_rules(),
    )
    candidate = next(
        ensure_json_object(candidate_value, "note_tag_candidate")
        for candidate_value in candidates
        if isinstance(candidate_value, dict)
        and candidate_value.get("file_name") == "Items.json"
        and candidate_value.get("tag_name") == "拡張説明"
    )
    assert candidate["sample_values"] == [source_note]

    rule_records = build_note_tag_rule_records_from_import(
        game_data=game_data,
        import_file={"Items.json": ["拡張説明"]},
        text_rules=get_default_text_rules(),
    )
    note_items = NoteTagTextExtraction(
        game_data=game_data,
        rule_records=rule_records,
        text_rules=get_default_text_rules(),
    ).extract_all_text()["Items.json"].translation_items

    assert note_items[0].original_lines == [source_note]

    translated_note = "\n　" + r"\C[2]详细说明\C[0]\n下一行" + "　\n"
    note_items[0].translation_lines = [translated_note]
    reset_writable_copies(game_data)
    write_data_text(game_data, [note_items[0]])

    writable_items = ensure_json_array(game_data.writable_data["Items.json"], "Items.json")
    writable_item = ensure_json_object(writable_items[1], "Items.json[1]")
    writable_note = writable_item["note"]
    assert isinstance(writable_note, str)
    assert writable_note.endswith("\n<upgrade:1,2,3>")
    tag_value = writable_note.removeprefix("<拡張説明:").split(">", maxsplit=1)[0]
    assert json.loads(tag_value) == translated_note


@pytest.mark.asyncio
async def test_map_event_note_tag_rules_extract_and_write_back(minimal_game_dir: Path) -> None:
    """Note 标签规则覆盖地图事件 note 字段，并支持 Map*.json 文件模式。"""
    map_path = minimal_game_dir / "data" / "Map001.json"
    raw_map = _read_test_json(map_path)
    map_object = ensure_json_object(raw_map, "Map001.json")
    events = ensure_json_array(map_object["events"], "Map001.json.events")
    event = ensure_json_object(events[2], "Map001.json.events[2]")
    event["note"] = "<namePop:導き手>\n<machine:1>"
    _rewrite_json(map_path, raw_map)

    game_data = await load_game_data(minimal_game_dir)
    candidates = collect_note_tag_candidates(
        game_data=game_data,
        text_rules=get_default_text_rules(),
    )
    name_pop_candidate = next(
        ensure_json_object(candidate_value, "note_tag_candidate")
        for candidate_value in candidates
        if isinstance(candidate_value, dict)
        and candidate_value.get("file_name") == "Map*.json"
        and candidate_value.get("tag_name") == "namePop"
    )
    assert name_pop_candidate["translatable_hit_count"] == 1
    assert name_pop_candidate["sample_locations"] == ["Map001.json/events/2/note/namePop"]

    rule_records = build_note_tag_rule_records_from_import(
        game_data=game_data,
        import_file={"Map*.json": ["namePop"]},
        text_rules=get_default_text_rules(),
    )
    note_items = NoteTagTextExtraction(
        game_data=game_data,
        rule_records=rule_records,
        text_rules=get_default_text_rules(),
    ).extract_all_text()["Map001.json"].translation_items

    assert [item.location_path for item in note_items] == ["Map001.json/events/2/note/namePop"]
    assert note_items[0].original_lines == ["導き手"]

    note_items[0].translation_lines = ["引导者"]
    reset_writable_copies(game_data)
    write_data_text(game_data, [note_items[0]])
    writable_map = ensure_json_object(game_data.writable_data["Map001.json"], "Map001.json")
    writable_events = ensure_json_array(writable_map["events"], "Map001.json.events")
    writable_event = ensure_json_object(writable_events[2], "Map001.json.events[2]")

    assert writable_event["note"] == "<namePop:引导者>\n<machine:1>"


@pytest.mark.asyncio
async def test_fixture_custom_control_sequences_can_be_protected(minimal_game_dir: Path) -> None:
    """测试夹具里的自定义控制符可通过外部规则保护。"""
    text_rules = TextRules.from_setting(
        TextRulesSetting(),
        custom_placeholder_rules=(
            CustomPlaceholderRule.create(r"\\F\[[^\]]+\]", "[CUSTOM_FACE_PORTRAIT_{index}]"),
        ),
    )
    game_data = await load_game_data(minimal_game_dir)
    extracted = DataTextExtraction(game_data, text_rules).extract_all_text()
    item = next(
        candidate
        for candidate in extracted["CommonEvents.json"].translation_items
        if candidate.location_path == "CommonEvents.json/2/0"
    )

    item.build_placeholders(text_rules)

    assert item.original_lines_with_placeholders[0] == "[CUSTOM_FACE_PORTRAIT_1]テスト一行目です。[RMMZ_WAIT_INPUT]"
    assert item.original_lines_with_placeholders[1] == "[RMMZ_TEXT_COLOR_4]重要語[RMMZ_TEXT_COLOR_0]を含む二行目です。"


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
async def test_write_data_text_rejects_internal_placeholder_leak(minimal_game_dir: Path) -> None:
    """正文写回前必须拒绝项目内部占位符。"""
    game_data = await load_game_data(minimal_game_dir)
    extracted = DataTextExtraction(game_data, get_default_text_rules()).extract_all_text()
    item = next(
        candidate
        for candidate in extracted["CommonEvents.json"].translation_items
        if candidate.location_path == "CommonEvents.json/1/0"
    )
    item.translation_lines = ["你好[RMMZ_TEXT_COLOR_0]"]

    reset_writable_copies(game_data)
    with pytest.raises(ValueError, match="译文残留项目内部占位符"):
        write_data_text(game_data, [item])


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
async def test_write_back_inserts_401_without_shifting_later_name_block(minimal_game_dir: Path) -> None:
    """前一个名字框插入额外 401 时，后一个名字框仍按原始定位正确写回。"""
    common_events_path = minimal_game_dir / "data" / "CommonEvents.json"
    raw_events = _read_test_json(common_events_path)
    events = ensure_json_array(raw_events, "CommonEvents")
    event = ensure_json_object(events[1], "CommonEvents[1]")
    event["list"] = [
        {"code": 101, "parameters": [0, 0, 0, 2, "案内人A"]},
        {"code": 401, "parameters": ["前半一行目"]},
        {"code": 101, "parameters": [0, 0, 0, 2, "案内人B"]},
        {"code": 401, "parameters": ["後半一行目"]},
        {"code": 0, "parameters": []},
    ]
    _rewrite_json(common_events_path, raw_events)

    game_data = await load_game_data(minimal_game_dir)
    extracted = DataTextExtraction(game_data, get_default_text_rules()).extract_all_text()
    common_items = extracted["CommonEvents.json"].translation_items
    first_item = next(item for item in common_items if item.location_path == "CommonEvents.json/1/0")
    second_item = next(item for item in common_items if item.location_path == "CommonEvents.json/1/2")
    first_item.translation_lines = ["前半译文一", "前半译文二", "前半译文三"]
    second_item.translation_lines = ["后半译文"]

    reset_writable_copies(game_data)
    write_data_text(game_data, [first_item, second_item])

    common_events = ensure_json_array(game_data.writable_data["CommonEvents.json"], "CommonEvents")
    common_event = ensure_json_object(common_events[1], "CommonEvents[1]")
    commands = ensure_json_array(common_event["list"], "CommonEvents[1].list")

    assert ensure_json_object(commands[0], "command0")["code"] == 101
    assert ensure_json_array(ensure_json_object(commands[1], "command1")["parameters"], "command1.parameters")[0] == "前半译文一"
    assert ensure_json_array(ensure_json_object(commands[2], "command2")["parameters"], "command2.parameters")[0] == "前半译文二"
    assert ensure_json_array(ensure_json_object(commands[3], "command3")["parameters"], "command3.parameters")[0] == "前半译文三"
    assert ensure_json_object(commands[4], "command4")["code"] == 101
    assert ensure_json_array(ensure_json_object(commands[5], "command5")["parameters"], "command5.parameters")[0] == "后半译文"


@pytest.mark.asyncio
async def test_write_back_deletes_401_without_shifting_later_name_block(minimal_game_dir: Path) -> None:
    """前一个名字框删除多余 401 时，后一个名字框仍按原始定位正确写回。"""
    common_events_path = minimal_game_dir / "data" / "CommonEvents.json"
    raw_events = _read_test_json(common_events_path)
    events = ensure_json_array(raw_events, "CommonEvents")
    event = ensure_json_object(events[1], "CommonEvents[1]")
    event["list"] = [
        {"code": 101, "parameters": [0, 0, 0, 2, "案内人A"]},
        {"code": 401, "parameters": ["前半一行目"]},
        {"code": 401, "parameters": ["前半二行目"]},
        {"code": 101, "parameters": [0, 0, 0, 2, "案内人B"]},
        {"code": 401, "parameters": ["後半一行目"]},
        {"code": 0, "parameters": []},
    ]
    _rewrite_json(common_events_path, raw_events)

    game_data = await load_game_data(minimal_game_dir)
    extracted = DataTextExtraction(game_data, get_default_text_rules()).extract_all_text()
    common_items = extracted["CommonEvents.json"].translation_items
    first_item = next(item for item in common_items if item.location_path == "CommonEvents.json/1/0")
    second_item = next(item for item in common_items if item.location_path == "CommonEvents.json/1/3")
    first_item.translation_lines = ["前半译文"]
    second_item.translation_lines = ["后半译文"]

    reset_writable_copies(game_data)
    write_data_text(game_data, [first_item, second_item])

    common_events = ensure_json_array(game_data.writable_data["CommonEvents.json"], "CommonEvents")
    common_event = ensure_json_object(common_events[1], "CommonEvents[1]")
    commands = ensure_json_array(common_event["list"], "CommonEvents[1].list")

    assert ensure_json_object(commands[0], "command0")["code"] == 101
    assert ensure_json_array(ensure_json_object(commands[1], "command1")["parameters"], "command1.parameters")[0] == "前半译文"
    assert ensure_json_object(commands[2], "command2")["code"] == 101
    assert ensure_json_array(ensure_json_object(commands[3], "command3")["parameters"], "command3.parameters")[0] == "后半译文"
    assert ensure_json_object(commands[4], "command4")["code"] == 0


@pytest.mark.asyncio
async def test_write_data_text_splits_overwide_long_text_before_write_back(minimal_game_dir: Path) -> None:
    """写回阶段按当前行宽配置再次切分已有长译文。"""
    game_data = await load_game_data(minimal_game_dir)
    extracted = DataTextExtraction(game_data, get_default_text_rules()).extract_all_text()
    item = next(
        candidate
        for candidate in extracted["CommonEvents.json"].translation_items
        if candidate.location_path == "CommonEvents.json/1/0"
    )
    item.translation_lines = ["甲乙丙丁戊己庚辛"]
    text_rules = TextRules.from_setting(
        TextRulesSetting(
            long_text_line_width_limit=3,
            line_width_count_pattern=r"\S",
            line_split_punctuations=["，", "。"],
        )
    )

    reset_writable_copies(game_data)
    write_data_text(game_data, [item], text_rules=text_rules)

    common_events = ensure_json_array(game_data.writable_data["CommonEvents.json"], "CommonEvents")
    common_event = ensure_json_object(common_events[1], "CommonEvents[1]")
    commands = ensure_json_array(common_event["list"], "CommonEvents[1].list")

    assert ensure_json_array(ensure_json_object(commands[1], "command1")["parameters"], "command1.parameters")[0] == "甲乙丙"
    assert ensure_json_array(ensure_json_object(commands[2], "command2")["parameters"], "command2.parameters")[0] == "丁戊己"
    assert ensure_json_array(ensure_json_object(commands[3], "command3")["parameters"], "command3.parameters")[0] == "庚辛"


@pytest.mark.asyncio
async def test_write_data_text_indents_wrapping_punctuation_continuation_lines(minimal_game_dir: Path) -> None:
    """写回阶段为跨行引号续行补视觉缩进。"""
    game_data = await load_game_data(minimal_game_dir)
    extracted = DataTextExtraction(game_data, get_default_text_rules()).extract_all_text()
    item = next(
        candidate
        for candidate in extracted["CommonEvents.json"].translation_items
        if candidate.location_path == "CommonEvents.json/1/0"
    )
    item.translation_lines = ["「甲乙丙。", "丁戊己」"]

    reset_writable_copies(game_data)
    write_data_text(game_data, [item], text_rules=get_default_text_rules())

    common_events = ensure_json_array(game_data.writable_data["CommonEvents.json"], "CommonEvents")
    common_event = ensure_json_object(common_events[1], "CommonEvents[1]")
    commands = ensure_json_array(common_event["list"], "CommonEvents[1].list")

    assert ensure_json_array(ensure_json_object(commands[1], "command1")["parameters"], "command1.parameters")[0] == "「甲乙丙。"
    assert ensure_json_array(ensure_json_object(commands[2], "command2")["parameters"], "command2.parameters")[0] == "　丁戊己」"


@pytest.mark.asyncio
async def test_write_data_text_restores_converted_outer_quote_before_indent(minimal_game_dir: Path) -> None:
    """写回阶段先修复被模型改写的外层引号，再补跨行视觉缩进。"""
    game_data = await load_game_data(minimal_game_dir)
    extracted = DataTextExtraction(game_data, get_default_text_rules()).extract_all_text()
    item = next(
        candidate
        for candidate in extracted["CommonEvents.json"].translation_items
        if candidate.location_path == "CommonEvents.json/1/0"
    )
    item.original_lines = ["「甲。", "乙」"]
    item.translation_lines = ["“甲乙丙。", "丁戊己。”"]

    reset_writable_copies(game_data)
    write_data_text(game_data, [item], text_rules=get_default_text_rules())

    common_events = ensure_json_array(game_data.writable_data["CommonEvents.json"], "CommonEvents")
    common_event = ensure_json_object(common_events[1], "CommonEvents[1]")
    commands = ensure_json_array(common_event["list"], "CommonEvents[1].list")

    assert ensure_json_array(ensure_json_object(commands[1], "command1")["parameters"], "command1.parameters")[0] == "「甲乙丙。"
    assert ensure_json_array(ensure_json_object(commands[2], "command2")["parameters"], "command2.parameters")[0] == "　丁戊己。」"


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
async def test_long_text_write_back_deletes_extra_original_lines(minimal_game_dir: Path) -> None:
    """译文行数少于原始 405 行数时，删除多余原始行指令。"""
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
    assert ensure_json_object(commands[1], "command1")["code"] == 0


@pytest.mark.asyncio
async def test_long_text_write_back_ignores_trailing_empty_translation_lines(minimal_game_dir: Path) -> None:
    """长文本写回忽略译文尾部空行，避免生成空白文本指令。"""
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
    scroll_item.translation_lines = ["滚动第一行", ""]

    reset_writable_copies(game_data)
    write_data_text(game_data, [scroll_item])

    common_events = ensure_json_array(game_data.writable_data["CommonEvents.json"], "CommonEvents")
    common_event = ensure_json_object(common_events[1], "CommonEvents[1]")
    commands = ensure_json_array(common_event["list"], "CommonEvents[1].list")
    assert ensure_json_array(ensure_json_object(commands[0], "command0")["parameters"], "command0.parameters")[0] == "滚动第一行"
    assert ensure_json_object(commands[1], "command1")["code"] == 0


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
async def test_old_game_reads_archived_files_and_adds_missing_backups(minimal_game_dir: Path) -> None:
    """留档布局优先读取原件留档，后续写回补齐新增受影响文件留档。"""
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

    origin_actors_path = minimal_game_dir / "data_origin" / "Actors.json"
    assert origin_actors_path.exists()
    origin_actors = ensure_json_array(_read_test_json(origin_actors_path), "data_origin/Actors.json")
    active_actors = ensure_json_array(_read_test_json(minimal_game_dir / "data" / "Actors.json"), "Actors.json")
    origin_actor = ensure_json_object(origin_actors[1], "data_origin/Actors.json[1]")
    active_actor = ensure_json_object(active_actors[1], "Actors.json[1]")
    assert origin_actor["name"] == "勇者"
    assert active_actor["name"] == "勇者译名"

    plugin_game_data = await load_game_data(minimal_game_dir)
    reset_writable_copies(plugin_game_data)
    plugin_text = plugin_game_data.writable_data[PLUGINS_FILE_NAME]
    assert isinstance(plugin_text, str)
    plugin_game_data.writable_data[PLUGINS_FILE_NAME] = plugin_text.replace("プラグイン本文", "插件正文")
    write_game_files(plugin_game_data, minimal_game_dir)

    origin_plugins_path = minimal_game_dir / "js" / "plugins_origin.js"
    assert origin_plugins_path.exists()
    assert "プラグイン本文" in origin_plugins_path.read_text(encoding="utf-8")


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
    parameters["HelpText"] = f"请在设置中选择 {Path(old_font).stem} 字体。"

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
    assert len(summary.records) == 5
    writable_system = ensure_json_object(game_data.writable_data["System.json"], "System")
    advanced = ensure_json_object(writable_system["advanced"], "System.advanced")
    writable_plugin = ensure_json_object(game_data.writable_plugins_js[0], "plugins[0]")
    writable_parameters = ensure_json_object(writable_plugin["parameters"], "plugins[0].parameters")
    assert advanced["mainFontFilename"] == replacement_name
    assert advanced["numberFontFilename"] == replacement_name
    assert writable_parameters["FontFace"] == replacement_name
    assert writable_parameters["FontStem"] == replacement_name
    nested_text = writable_parameters["Nested"]
    assert isinstance(nested_text, str)
    nested_value = ensure_json_object(coerce_json_value(cast(object, json.loads(nested_text))), "Nested")
    assert nested_value["font"] == replacement_name
    assert nested_value["text"] == "プラグイン本文"
    assert writable_parameters["HelpText"] == f"请在设置中选择 {Path(old_font).stem} 字体。"
    original_system = json.dumps(game_data.data["System.json"], ensure_ascii=False)
    assert old_font not in original_system
    assert another_font not in original_system

    assert replacement_name not in original_system


@pytest.mark.asyncio
async def test_restore_font_references_uses_origin_backups_without_rolling_back_text(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """字体还原按原件留档替回旧字体引用，不回滚已经写入的译文。"""
    fonts_dir = minimal_game_dir / "fonts"
    fonts_dir.mkdir()
    old_font = "OldFont.woff"
    another_font = "AnotherFont.woff"
    _ = (fonts_dir / old_font).write_bytes(b"old font")
    _ = (fonts_dir / another_font).write_bytes(b"another font")
    replacement_font = tmp_path / "NotoSansSC-Regular.ttf"
    _ = replacement_font.write_bytes(b"new font")

    system_path = minimal_game_dir / "data" / "System.json"
    raw_system = _read_test_json(system_path)
    system = ensure_json_object(raw_system, "System.json")
    system["advanced"] = {
        "mainFontFilename": old_font,
        "numberFontFilename": another_font,
    }
    _rewrite_json(system_path, raw_system)

    base_game_data = await load_game_data(minimal_game_dir)
    plugin = ensure_json_object(base_game_data.plugins_js[0], "plugins[0]")
    parameters = ensure_json_object(plugin["parameters"], "plugins[0].parameters")
    parameters["FontFace"] = old_font
    parameters["FontStem"] = Path(old_font).stem
    parameters["Nested"] = json.dumps(
        {"font": another_font, "text": "プラグイン本文"},
        ensure_ascii=False,
    )
    parameters["HelpText"] = f"请在设置中选择 {old_font} 字体。"
    plugins_path = minimal_game_dir / "js" / "plugins.js"
    _ = plugins_path.write_text(
        f"var $plugins = {json.dumps(base_game_data.plugins_js, ensure_ascii=False, indent=2)};\n",
        encoding="utf-8",
    )

    game_data = await load_game_data(minimal_game_dir)
    reset_writable_copies(game_data)
    writable_system = ensure_json_object(game_data.writable_data["System.json"], "System")
    writable_system["gameTitle"] = "翻译标题"
    writable_plugin = ensure_json_object(game_data.writable_plugins_js[0], "plugins[0]")
    writable_parameters = ensure_json_object(writable_plugin["parameters"], "plugins[0].parameters")
    replacement_name = replacement_font.name
    writable_parameters["Nested"] = json.dumps(
        {"font": another_font, "text": "插件正文"},
        ensure_ascii=False,
    )
    writable_parameters["HelpText"] = f"请在设置中选择 {replacement_name} 字体。"

    _ = apply_font_replacement(
        game_data=game_data,
        game_root=minimal_game_dir,
        replacement_font_path=str(replacement_font),
    )
    write_game_files(game_data, minimal_game_dir)

    restore_summary = restore_font_references_from_origin_backups(
        game_root=minimal_game_dir,
        replacement_font_names=[replacement_name],
    )

    assert restore_summary.restored_reference_count == 5
    active_system = ensure_json_object(_read_test_json(system_path), "System.json")
    active_advanced = ensure_json_object(active_system["advanced"], "System.advanced")
    assert active_system["gameTitle"] == "翻译标题"
    assert active_advanced["mainFontFilename"] == old_font
    assert active_advanced["numberFontFilename"] == another_font

    restored_plugins = read_plugins_js_file(plugins_path)
    restored_plugin = ensure_json_object(restored_plugins[0], "plugins[0]")
    restored_parameters = ensure_json_object(restored_plugin["parameters"], "plugins[0].parameters")
    assert restored_parameters["FontFace"] == old_font
    assert restored_parameters["FontStem"] == Path(old_font).stem
    nested_text = restored_parameters["Nested"]
    assert isinstance(nested_text, str)
    nested_value = ensure_json_object(coerce_json_value(cast(object, json.loads(nested_text))), "Nested")
    assert nested_value["font"] == another_font
    assert nested_value["text"] == "插件正文"
    assert restored_parameters["HelpText"] == f"请在设置中选择 {replacement_name} 字体。"
