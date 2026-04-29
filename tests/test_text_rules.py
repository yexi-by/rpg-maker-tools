"""文本规则与占位符的业务测试。"""

import pytest

from app.rmmz.schema import TranslationItem
from app.rmmz.text_rules import get_default_text_rules


def test_text_rules_replace_and_restore_control_sequences() -> None:
    """控制符会被占位并可恢复，避免模型破坏 RMMZ 特殊语法。"""
    rules = get_default_text_rules()
    item = TranslationItem(
        location_path="Map001.json/1/0/0",
        item_type="long_text",
        original_lines=["こんにちは\\V[1]%12\\G"],
    )

    item.build_placeholders(rules)
    assert item.original_lines_with_placeholders == ["こんにちは[V_1][P_12][G_0]"]

    item.translation_lines_with_placeholders = ["你好[V_1][P_12][G_0]"]
    item.verify_placeholders(rules)
    item.restore_placeholders()
    assert item.translation_lines == ["你好\\V[1]%12\\G"]


def test_text_rules_filter_resource_and_japanese_residual() -> None:
    """资源路径应跳过，译文残留明显日文时应显式失败。"""
    rules = get_default_text_rules()
    assert rules.should_skip_plugin_like_text(text="img/pictures/Actor1.png", path_parts=["File"])
    assert rules.should_extract_plugin_command_key("messageText")
    assert not rules.should_extract_plugin_command_key("filename")

    with pytest.raises(ValueError, match="日文残留"):
        rules.check_japanese_residual(["你好カ"])
