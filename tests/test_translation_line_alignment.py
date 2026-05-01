"""长文本译文行数适配与行宽兜底测试。"""

import asyncio
import json

import pytest

from app.config.schemas import TextRulesSetting
from app.rmmz.schema import TranslationErrorItem, TranslationItem
from app.rmmz.text_rules import TextRules
from app.translation.line_wrap import count_line_width_chars, split_overwide_lines
from app.translation.verify import verify_translation_batch


def _build_text_rules(*, width_limit: int) -> TextRules:
    """构建指定长文本宽度的测试规则。"""
    return TextRules.from_setting(
        TextRulesSetting(
            long_text_line_width_limit=width_limit,
            line_split_punctuations=["，", "。"],
        )
    )


async def _verify_single_long_text(
    *,
    original_lines: list[str],
    translated_text: str,
    text_rules: TextRules,
) -> TranslationItem:
    """执行单条 long_text 校验并返回通过校验的条目。"""
    item = TranslationItem(
        location_path="Map001.json/1/0/0",
        item_type="long_text",
        original_lines=original_lines,
    )
    item.build_placeholders(text_rules)
    right_queue: asyncio.Queue[list[TranslationItem] | None] = asyncio.Queue()
    error_queue: asyncio.Queue[list[TranslationErrorItem] | None] = asyncio.Queue()

    await verify_translation_batch(
        ai_result=json.dumps({item.location_path: translated_text}, ensure_ascii=False),
        items=[item],
        right_queue=right_queue,
        error_queue=error_queue,
        text_rules=text_rules,
    )

    assert error_queue.empty()
    result = await right_queue.get()
    assert result is not None
    return result[0]


@pytest.mark.asyncio
async def test_long_text_keeps_fewer_model_lines_without_padding() -> None:
    """模型返回行数较少时不再补空行。"""
    item = await _verify_single_long_text(
        original_lines=["あ", "い", "う"],
        translated_text="甲\n乙",
        text_rules=_build_text_rules(width_limit=47),
    )

    assert item.translation_lines == ["甲", "乙"]


@pytest.mark.asyncio
async def test_invalid_model_response_is_recorded_on_error_items() -> None:
    """模型返回无法解析时，错误条目保留原始模型返回。"""
    item = TranslationItem(
        location_path="Map001.json/1/0/0",
        item_type="long_text",
        original_lines=["あ"],
    )
    raw_response = "无法解析的模型输出"
    right_queue: asyncio.Queue[list[TranslationItem] | None] = asyncio.Queue()
    error_queue: asyncio.Queue[list[TranslationErrorItem] | None] = asyncio.Queue()

    await verify_translation_batch(
        ai_result=raw_response,
        items=[item],
        right_queue=right_queue,
        error_queue=error_queue,
        text_rules=_build_text_rules(width_limit=47),
    )

    assert right_queue.empty()
    error_items = await error_queue.get()
    assert error_items is not None
    assert error_items[0].model_response == raw_response


@pytest.mark.asyncio
async def test_long_text_keeps_more_model_lines_without_merging() -> None:
    """模型返回行数较多时保留额外译文行。"""
    item = await _verify_single_long_text(
        original_lines=["あ", "い", "う"],
        translated_text="甲\n乙\n丙\n丁",
        text_rules=_build_text_rules(width_limit=47),
    )

    assert item.translation_lines == ["甲", "乙", "丙", "丁"]


@pytest.mark.asyncio
async def test_long_text_keeps_empty_lines_when_width_split_expands_lines() -> None:
    """行宽兜底切分非空行时保留模型输出的空行。"""
    item = await _verify_single_long_text(
        original_lines=["あ", "い"],
        translated_text="甲乙丙丁\n\n乙",
        text_rules=_build_text_rules(width_limit=2),
    )

    assert item.translation_lines == ["甲乙", "丙丁", "", "乙"]


@pytest.mark.asyncio
async def test_long_text_prefers_punctuation_and_then_hard_split() -> None:
    """超宽行优先按中文标点切分，剩余长段再按计数字符硬切。"""
    item = await _verify_single_long_text(
        original_lines=["あ"],
        translated_text="甲乙丙，丁戊己庚",
        text_rules=_build_text_rules(width_limit=3),
    )

    assert item.translation_lines == ["甲乙丙，", "丁戊己", "庚"]


@pytest.mark.asyncio
async def test_long_text_width_split_does_not_break_placeholders() -> None:
    """行宽兜底不会把占位符切成不可恢复的碎片。"""
    item = await _verify_single_long_text(
        original_lines=[r"\C[4]あいう\C[0]"],
        translated_text="[RMMZ_C_4]甲乙丙丁[RMMZ_C_0]",
        text_rules=_build_text_rules(width_limit=2),
    )

    assert item.translation_lines == [r"\C[4]甲乙", r"丙丁\C[0]"]


def test_line_width_count_ignores_control_sequences() -> None:
    """行宽统计忽略控制符，只统计实际可见字符。"""
    text_rules = _build_text_rules(width_limit=28)

    count = count_line_width_chars(r"\C[4]魔力\C[0]，", text_rules)

    assert count == 3


def test_line_width_split_uses_punctuation_near_limit() -> None:
    """标点切分优先接近宽度上限，避免过早断成碎句。"""
    text_rules = _build_text_rules(width_limit=8)

    lines = split_overwide_lines(
        lines=["甲乙丙丁戊己，庚辛壬癸"],
        location_path="Map001.json/1/0/0",
        text_rules=text_rules,
    )

    assert lines == ["甲乙丙丁戊己，", "庚辛壬癸"]
