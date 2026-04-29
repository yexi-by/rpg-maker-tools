"""插件文本路径解析、提取和回写测试。"""

import json
from pathlib import Path

import pytest

from app.application.file_writer import reset_writable_copies
from app.plugin_text import PluginTextExtraction, resolve_plugin_leaves
from app.plugin_text.write_back import write_plugin_text
from app.rmmz import load_game_data
from app.rmmz.schema import PluginTextRuleRecord, PluginTextTranslateRule
from app.rmmz.text_rules import get_default_text_rules


@pytest.mark.asyncio
async def test_plugin_text_extracts_rule_matched_leaves(minimal_game_dir: Path) -> None:
    """插件文本提取只使用已确认的规则路径，不扫描无关资源路径。"""
    game_data = await load_game_data(minimal_game_dir)
    leaves = resolve_plugin_leaves(game_data.plugins_js[0])
    leaf_paths = {leaf.path for leaf in leaves}
    assert "$['parameters']['Message']" in leaf_paths
    assert "$['parameters']['Nested']['text']" in leaf_paths

    rule_record = PluginTextRuleRecord(
        plugin_index=0,
        plugin_name="TestPlugin",
        plugin_hash="hash",
        prompt_hash="prompt",
        status="success",
        translate_rules=[
            PluginTextTranslateRule(path_template="$['parameters']['Message']", reason="玩家可见文本"),
            PluginTextTranslateRule(path_template="$['parameters']['Nested']['text']", reason="嵌套玩家可见文本"),
        ],
        updated_at="2026-04-30T00:00:00+00:00",
    )
    extracted = PluginTextExtraction(game_data, [rule_record], get_default_text_rules()).extract_all_text()
    items = extracted["plugins.js"].translation_items

    assert {item.location_path for item in items} == {
        "plugins.js/0/Message",
        "plugins.js/0/Nested/text",
    }


@pytest.mark.asyncio
async def test_plugin_text_write_back_updates_nested_json_string(minimal_game_dir: Path) -> None:
    """插件文本回写能更新普通参数，也能更新 JSON 字符串里的嵌套文本。"""
    game_data = await load_game_data(minimal_game_dir)
    rule_record = PluginTextRuleRecord(
        plugin_index=0,
        plugin_name="TestPlugin",
        plugin_hash="hash",
        prompt_hash="prompt",
        status="success",
        translate_rules=[
            PluginTextTranslateRule(path_template="$['parameters']['Message']", reason="玩家可见文本"),
            PluginTextTranslateRule(path_template="$['parameters']['Nested']['text']", reason="嵌套玩家可见文本"),
        ],
        updated_at="2026-04-30T00:00:00+00:00",
    )
    items = PluginTextExtraction(game_data, [rule_record], get_default_text_rules()).extract_all_text()[
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
