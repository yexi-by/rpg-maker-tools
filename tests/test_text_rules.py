"""文本规则与占位符的业务测试。"""

import json
from pathlib import Path

import pytest

from app.config.custom_placeholder_rules import (
    load_custom_placeholder_rules,
    load_custom_placeholder_rules_file,
    load_custom_placeholder_rules_text,
)
from app.config.schemas import TextRulesSetting
from app.rmmz.control_codes import CustomPlaceholderRule
from app.rmmz.schema import TranslationItem
from app.rmmz.text_rules import TextRules, get_default_text_rules


def test_text_rules_replace_and_restore_standard_rmmz_control_sequences() -> None:
    """全部 RMMZ 标准控制符会被占位并可恢复。"""
    rules = get_default_text_rules()
    segments = [
        "\\V[1]",
        "\\N[2]",
        "\\P[3]",
        "\\G",
        "\\C[4]",
        "\\I[5]",
        "\\{",
        "\\}",
        "\\\\",
        "\\$",
        "\\.",
        "\\|",
        "\\!",
        "\\>",
        "\\<",
        "\\^",
        "\\PX[6]",
        "\\PY[7]",
        "\\FS[8]",
        "%9",
    ]
    placeholders = [
        "[RMMZ_V_1]",
        "[RMMZ_N_2]",
        "[RMMZ_P_3]",
        "[RMMZ_G]",
        "[RMMZ_C_4]",
        "[RMMZ_I_5]",
        "[RMMZ_FONT_LARGER]",
        "[RMMZ_FONT_SMALLER]",
        "[RMMZ_BACKSLASH]",
        "[RMMZ_GOLD_WINDOW]",
        "[RMMZ_WAIT_SHORT]",
        "[RMMZ_WAIT_LONG]",
        "[RMMZ_WAIT_INPUT]",
        "[RMMZ_INSTANT_ON]",
        "[RMMZ_INSTANT_OFF]",
        "[RMMZ_NO_WAIT]",
        "[RMMZ_PX_6]",
        "[RMMZ_PY_7]",
        "[RMMZ_FS_8]",
        "[RMMZ_PERCENT_9]",
    ]
    item = TranslationItem(
        location_path="Map001.json/1/0/0",
        item_type="long_text",
        original_lines=["こんにちは" + "".join(segments)],
    )

    item.build_placeholders(rules)
    assert item.original_lines_with_placeholders == ["こんにちは" + "".join(placeholders)]

    item.translation_lines_with_placeholders = ["你好" + "".join(placeholders)]
    item.verify_placeholders(rules)
    item.restore_placeholders()
    assert item.translation_lines == ["你好" + "".join(segments)]


def test_text_rules_filter_resource_and_japanese_residual() -> None:
    """译文残留明显日文时应显式失败。"""
    rules = get_default_text_rules()

    with pytest.raises(ValueError, match="日文残留"):
        rules.check_japanese_residual(["你好カ"])


def test_text_rules_requires_source_language_characters_for_translation() -> None:
    """原文必须包含平假名、片假名或汉字才进入正文翻译。"""
    rules = get_default_text_rules()

    assert rules.should_translate_source_text("こんにちは")
    assert rules.should_translate_source_text("テスト")
    assert rules.should_translate_source_text("勇者")
    assert not rules.should_translate_source_text("Untitled")
    assert not rules.should_translate_source_text("Back")
    assert not rules.should_translate_source_text("123")
    assert not rules.should_translate_source_text("img/pictures/Actor1.png")


def test_text_rules_can_apply_custom_placeholder_json_rules() -> None:
    """自定义正则规则会在标准 RMMZ 控制符之外保护特殊片段。"""
    rules = TextRules.from_setting(
        TextRulesSetting(line_width_count_pattern="@"),
        custom_placeholder_rules=(
            CustomPlaceholderRule.create(r"@V\[\d+\]", "[CUSTOM_AT_VAR_{index}]"),
            CustomPlaceholderRule.create(r"<tag:[^>]+>", "[CUSTOM_TAG_{index}]"),
        ),
    )
    item = TranslationItem(
        location_path="Map001.json/1/0/0",
        item_type="long_text",
        original_lines=["こんにちは@V[1]<tag:abc>\\V[2]"],
    )

    item.build_placeholders(rules)
    assert item.original_lines_with_placeholders == [
        "こんにちは[CUSTOM_AT_VAR_1][CUSTOM_TAG_2][RMMZ_V_2]"
    ]

    item.translation_lines_with_placeholders = [
        "你好[CUSTOM_AT_VAR_1][CUSTOM_TAG_2][RMMZ_V_2]"
    ]
    item.verify_placeholders(rules)
    item.restore_placeholders()
    assert item.translation_lines == ["你好@V[1]<tag:abc>\\V[2]"]
    assert rules.count_line_width_chars("@@中文") == 2
    assert rules.is_line_width_counted_char("@")


def test_custom_placeholder_rules_load_from_json_file(tmp_path: Path) -> None:
    """自定义占位符规则 JSON 使用正则字符串作为键、占位符模板作为值。"""
    rules_path = tmp_path / "custom_placeholder_rules.json"
    _ = rules_path.write_text(
        json.dumps({r"@name\[[^\]]+\]": "[CUSTOM_NAME_{index}]"}),
        encoding="utf-8",
    )

    custom_rules = load_custom_placeholder_rules(tmp_path)
    rules = TextRules.from_setting(
        TextRulesSetting(),
        custom_placeholder_rules=custom_rules,
    )
    item = TranslationItem(
        location_path="Map001.json/1/0/0",
        item_type="short_text",
        original_lines=["@name[アリス]"],
    )

    item.build_placeholders(rules)
    assert item.original_lines_with_placeholders == ["[CUSTOM_NAME_1]"]


def test_custom_placeholder_rules_load_from_cli_json_string() -> None:
    """CLI JSON 字符串会作为本次运行的规则来源。"""
    custom_rules = load_custom_placeholder_rules_text(
        json.dumps({r"\\F\[[^\]]+\]": "[CUSTOM_FACE_{index}]"})
    )
    rules = TextRules.from_setting(
        TextRulesSetting(),
        custom_placeholder_rules=custom_rules,
    )
    item = TranslationItem(
        location_path="Map001.json/1/0/0",
        item_type="short_text",
        original_lines=[r"\F[FinF]こんにちは"],
    )

    item.build_placeholders(rules)
    assert item.original_lines_with_placeholders == ["[CUSTOM_FACE_1]こんにちは"]


def test_custom_placeholder_rules_explicit_missing_file_fails(tmp_path: Path) -> None:
    """显式读取的规则文件不存在时应直接失败。"""
    with pytest.raises(FileNotFoundError):
        _ = load_custom_placeholder_rules_file(rules_path=tmp_path / "missing.json")


def test_custom_placeholder_rules_empty_cli_json_string_fails() -> None:
    """CLI 规则字符串为空时应直接失败。"""
    with pytest.raises(ValueError):
        _ = load_custom_placeholder_rules_text("")
