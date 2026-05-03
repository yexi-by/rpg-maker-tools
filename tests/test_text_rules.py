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
from app.rmmz.control_codes import CustomPlaceholderRule, LITERAL_LINE_BREAK_PLACEHOLDER
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
        "\\n",
    ]
    placeholders = [
        "[RMMZ_VARIABLE_1]",
        "[RMMZ_ACTOR_NAME_2]",
        "[RMMZ_PARTY_MEMBER_NAME_3]",
        "[RMMZ_CURRENCY_UNIT]",
        "[RMMZ_TEXT_COLOR_4]",
        "[RMMZ_ICON_5]",
        "[RMMZ_FONT_LARGER]",
        "[RMMZ_FONT_SMALLER]",
        "[RMMZ_BACKSLASH]",
        "[RMMZ_SHOW_GOLD_WINDOW]",
        "[RMMZ_WAIT_SHORT]",
        "[RMMZ_WAIT_LONG]",
        "[RMMZ_WAIT_INPUT]",
        "[RMMZ_INSTANT_TEXT_ON]",
        "[RMMZ_INSTANT_TEXT_OFF]",
        "[RMMZ_NO_WAIT]",
        "[RMMZ_TEXT_X_POSITION_6]",
        "[RMMZ_TEXT_Y_POSITION_7]",
        "[RMMZ_FONT_SIZE_8]",
        "[RMMZ_MESSAGE_ARGUMENT_9]",
        LITERAL_LINE_BREAK_PLACEHOLDER,
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


def test_japanese_tail_allowlist_does_not_hide_untranslated_short_lines() -> None:
    """整行只剩日文尾音时仍按未翻译残留处理。"""
    rules = get_default_text_rules()

    with pytest.raises(ValueError, match="日文残留"):
        rules.check_japanese_residual(["「なっ……」"])

    with pytest.raises(ValueError, match="日文残留"):
        rules.check_japanese_residual(['"え？"'])

    rules.check_japanese_residual(["已经好了よ"])


def test_text_rules_requires_configured_source_characters_for_translation() -> None:
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
            CustomPlaceholderRule.create(r"@V\[\d+\]", "[CUSTOM_AT_VARIABLE_{index}]"),
            CustomPlaceholderRule.create(r"<tag:[^>]+>", "[CUSTOM_INLINE_TAG_{index}]"),
        ),
    )
    item = TranslationItem(
        location_path="Map001.json/1/0/0",
        item_type="long_text",
        original_lines=["こんにちは@V[1]<tag:abc>\\V[2]"],
    )

    item.build_placeholders(rules)
    assert item.original_lines_with_placeholders == [
        "こんにちは[CUSTOM_AT_VARIABLE_1][CUSTOM_INLINE_TAG_2][RMMZ_VARIABLE_2]"
    ]

    item.translation_lines_with_placeholders = [
        "你好[CUSTOM_AT_VARIABLE_1][CUSTOM_INLINE_TAG_2][RMMZ_VARIABLE_2]"
    ]
    item.verify_placeholders(rules)
    item.restore_placeholders()
    assert item.translation_lines == ["你好@V[1]<tag:abc>\\V[2]"]
    assert rules.count_line_width_chars("@@中文") == 2
    assert rules.is_line_width_counted_char("@")


def test_unprotected_control_sequences_must_stay_exact() -> None:
    """未被规则覆盖的畸形控制符也必须在译文中原样保留。"""
    rules = get_default_text_rules()
    item = TranslationItem(
        location_path="CommonEvents.json/99/293",
        item_type="long_text",
        original_lines=[r"\F3[66」「ふーん……？」"],
    )

    item.build_placeholders(rules)
    assert item.original_lines_with_placeholders == [r"\F3[66」「ふーん……？」"]

    item.translation_lines_with_placeholders = [r"\F3[66」「唔——嗯……？」"]
    item.verify_placeholders(rules)

    item.translation_lines_with_placeholders = [r"\F3[60」「唔——嗯……？」"]
    with pytest.raises(ValueError, match="疑似控制符不一致"):
        item.verify_placeholders(rules)

    item.translation_lines_with_placeholders = [r"\F3[66]「唔——嗯……？」"]
    with pytest.raises(ValueError, match="疑似控制符不一致"):
        item.verify_placeholders(rules)


def test_unprotected_control_sequences_report_added_unknown_escape() -> None:
    """译文新增未覆盖反斜杠片段时必须显式失败。"""
    rules = get_default_text_rules()
    item = TranslationItem(
        location_path="CommonEvents.json/1/0",
        item_type="long_text",
        original_lines=["こんにちは"],
    )

    item.build_placeholders(rules)
    item.translation_lines_with_placeholders = [r"你好\X下一行"]

    with pytest.raises(ValueError, match=r"\\X"):
        item.verify_placeholders(rules)


def test_literal_line_break_placeholder_allows_width_wrap_additions() -> None:
    """字面量反斜杠 n 是标准换行占位符，允许行宽兜底追加换行。"""
    rules = get_default_text_rules()
    item = TranslationItem(
        location_path="plugins.js/1/message",
        item_type="short_text",
        original_lines=["説明\\n本文"],
    )

    item.build_placeholders(rules)
    assert item.original_lines_with_placeholders == [f"説明{LITERAL_LINE_BREAK_PLACEHOLDER}本文"]

    item.translation_lines_with_placeholders = [
        f"说明{LITERAL_LINE_BREAK_PLACEHOLDER}正文{LITERAL_LINE_BREAK_PLACEHOLDER}补充"
    ]
    item.verify_placeholders(rules)
    item.restore_placeholders()
    assert item.translation_lines == ["说明\\n正文\\n补充"]


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
        json.dumps({r"\\F\[[^\]]+\]": "[CUSTOM_FACE_PORTRAIT_{index}]"})
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
    assert item.original_lines_with_placeholders == ["[CUSTOM_FACE_PORTRAIT_1]こんにちは"]


def test_custom_placeholder_rules_explicit_missing_file_fails(tmp_path: Path) -> None:
    """显式读取的规则文件不存在时应直接失败。"""
    with pytest.raises(FileNotFoundError):
        _ = load_custom_placeholder_rules_file(rules_path=tmp_path / "missing.json")


def test_custom_placeholder_rules_empty_cli_json_string_fails() -> None:
    """CLI 规则字符串为空时应直接失败。"""
    with pytest.raises(ValueError):
        _ = load_custom_placeholder_rules_text("")
