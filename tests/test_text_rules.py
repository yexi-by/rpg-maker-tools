"""文本规则与占位符的业务测试。"""

import pytest

from app.rmmz.schema import TranslationItem
from app.config.schemas import TextRulesSetting
from app.rmmz.text_rules import TextRules, get_default_text_rules


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
    assert rules.should_skip_plugin_command_text(text="img/pictures/Actor1.png", path_parts=["File"])
    assert rules.should_extract_plugin_command_key("messageText")
    assert not rules.should_extract_plugin_command_key("filename")

    with pytest.raises(ValueError, match="日文残留"):
        rules.check_japanese_residual(["你好カ"])


def test_text_rules_can_customize_control_placeholder_strategy() -> None:
    """不同游戏可以通过配置替换控制符识别和占位符格式。"""
    rules = TextRules.from_setting(
        TextRulesSetting(
            control_code_prefix="@",
            percent_control_prefix="$",
            percent_control_param_pattern=r"\d+",
            no_param_alpha_control_codes=["G"],
            no_param_control_placeholder_param="NONE",
            percent_placeholder_template="<P:{param}>",
            symbol_placeholder_template="<SYM:{index}>",
            simple_control_placeholder_template="<{code}:{param}>",
            complex_control_placeholder_template="<RM:{index}>",
            translation_placeholder_pattern=r"<(?:[A-Z]+|P|SYM|RM)(?::[^>]+)?>",
            placeholder_pattern=r"<(?:[A-Z]+|P|SYM|RM)(?::[^>]+)?>",
            line_width_count_pattern="@",
        )
    )
    item = TranslationItem(
        location_path="Map001.json/1/0/0",
        item_type="long_text",
        original_lines=["こんにちは@V[1]$12@G@!"],
    )

    item.build_placeholders(rules)
    assert item.original_lines_with_placeholders == [
        "こんにちは<V:1><P:12><G:NONE><SYM:1>"
    ]

    item.translation_lines_with_placeholders = ["你好<V:1><P:12><G:NONE><SYM:1>"]
    item.verify_placeholders(rules)
    item.restore_placeholders()
    assert item.translation_lines == ["你好@V[1]$12@G@!"]
    assert rules.count_line_width_chars("@@中文") == 2
    assert rules.is_line_width_counted_char("@")
