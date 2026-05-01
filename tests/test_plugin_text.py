"""插件文本外部规则导入、提取和回写测试。"""

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from app.application.file_writer import reset_writable_copies
from app.plugin_text import (
    PluginTextExtraction,
    build_plugin_rule_records_from_import,
    export_plugins_json_file,
    load_plugin_rule_import_file,
    resolve_plugin_leaves,
)
from app.plugin_text.write_back import write_plugin_text
from app.rmmz import load_game_data
from app.rmmz.schema import PluginTextRuleRecord
from app.rmmz.text_rules import JsonValue, ensure_json_array, ensure_json_object


@pytest.mark.asyncio
async def test_plugin_json_export_writes_raw_plugins_array(minimal_game_dir: Path, tmp_path: Path) -> None:
    """插件配置导出文件的顶层结构是原始 `$plugins` 数组。"""
    game_data = await load_game_data(minimal_game_dir)
    output_path = tmp_path / "plugins.json"

    await export_plugins_json_file(game_data=game_data, output_path=output_path)

    json_value_adapter: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
    exported_value = json_value_adapter.validate_json(output_path.read_text(encoding="utf-8"))
    exported_plugins = ensure_json_array(exported_value, "plugins.json")
    first_plugin = ensure_json_object(exported_plugins[0], "plugins.json[0]")
    assert first_plugin["name"] == "TestPlugin"
    assert "parameters" in first_plugin


@pytest.mark.asyncio
async def test_plugin_rule_import_validates_external_file(minimal_game_dir: Path, tmp_path: Path) -> None:
    """外部插件规则文件使用插件名到路径数组的简单映射。"""
    game_data = await load_game_data(minimal_game_dir)
    input_path = tmp_path / "plugin-rules.json"
    _ = input_path.write_text(
        json.dumps(
            {
                "TestPlugin": ["$['parameters']['Message']"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    import_file = await load_plugin_rule_import_file(input_path)
    records = build_plugin_rule_records_from_import(
        game_data=game_data,
        import_file=import_file,
    )

    assert len(records) == 1
    assert records[0].plugin_name == "TestPlugin"
    assert records[0].path_templates == ["$['parameters']['Message']"]


@pytest.mark.asyncio
async def test_plugin_text_extracts_rule_matched_leaves(minimal_game_dir: Path) -> None:
    """插件文本提取只使用已确认的规则路径，不扫描无关资源路径。"""
    game_data = await load_game_data(minimal_game_dir)
    leaves = resolve_plugin_leaves(game_data.plugins_js[0])
    leaf_paths = {leaf.path for leaf in leaves}
    assert "$['parameters']['Message']" in leaf_paths
    assert "$['parameters']['Nested']['text']" in leaf_paths
    assert "$['parameters']['List'][0]['text']" in leaf_paths
    assert "$['parameters']['List'][1]['text']" in leaf_paths

    rule_record = PluginTextRuleRecord(
        plugin_index=0,
        plugin_name="TestPlugin",
        plugin_hash="hash",
        path_templates=[
            "$['parameters']['Message']",
            "$['parameters']['Nested']['text']",
            "$['parameters']['List'][0]['text']",
            "$['parameters']['List'][1]['text']",
            "$['parameters']['Count']",
        ],
    )
    extracted = PluginTextExtraction(game_data, [rule_record]).extract_all_text()
    items = extracted["plugins.js"].translation_items

    assert {item.location_path for item in items} == {
        "plugins.js/0/Message",
        "plugins.js/0/Nested/text",
        "plugins.js/0/List/0/text",
        "plugins.js/0/List/1/text",
    }


@pytest.mark.asyncio
async def test_plugin_text_write_back_updates_nested_json_string(minimal_game_dir: Path) -> None:
    """插件文本回写能更新普通参数，也能更新 JSON 字符串里的嵌套文本。"""
    game_data = await load_game_data(minimal_game_dir)
    rule_record = PluginTextRuleRecord(
        plugin_index=0,
        plugin_name="TestPlugin",
        plugin_hash="hash",
        path_templates=[
            "$['parameters']['Message']",
            "$['parameters']['Nested']['text']",
        ],
    )
    items = PluginTextExtraction(game_data, [rule_record]).extract_all_text()[
        "plugins.js"
    ].translation_items
    for item in items:
        if item.location_path.endswith("/Message"):
            item.translation_lines = ["插件译文"]
        else:
            item.translation_lines = ["嵌套译文"]

    reset_writable_copies(game_data)
    write_plugin_text(game_data, items)

    parameters = game_data.writable_plugins_js[0]["parameters"]
    assert isinstance(parameters, dict)
    assert parameters["Message"] == "插件译文"
    nested_value = parameters["Nested"]
    assert isinstance(nested_value, str)
    assert json.loads(nested_value)["text"] == "嵌套译文"
    assert isinstance(game_data.writable_data["plugins.js"], str)
