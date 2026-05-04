"""长文本译文行数适配与行宽兜底测试。"""

import asyncio
import json

import pytest

from app.config.schemas import TextRulesSetting
from app.rmmz.control_codes import CustomPlaceholderRule, LITERAL_LINE_BREAK_PLACEHOLDER
from app.rmmz.schema import ItemType, TranslationErrorItem, TranslationItem
from app.rmmz.text_rules import TextRules
from app.translation.line_wrap import (
    align_long_text_lines,
    count_line_width_chars,
    split_overwide_lines,
    split_overwide_single_text_value_if_needed,
)
from app.translation.verify import verify_translation_batch


def _build_text_rules(*, width_limit: int) -> TextRules:
    """构建指定长文本宽度的测试规则。"""
    return TextRules.from_setting(
        TextRulesSetting(
            long_text_line_width_limit=width_limit,
            line_split_punctuations=["，", "。"],
        )
    )


def _build_model_response(
    *,
    item: TranslationItem,
    translation_lines: list[str],
    source_lines: list[str] | None = None,
    extra_fields: dict[str, object] | None = None,
) -> str:
    """构建新数组协议下的模型返回。"""
    response_item: dict[str, object] = {
        "id": item.location_path,
        "role": item.role or "",
        "source_lines": source_lines if source_lines is not None else list(item.original_lines),
        "translation_lines": translation_lines,
    }
    if extra_fields is not None:
        response_item.update(extra_fields)
    return json.dumps([response_item], ensure_ascii=False)


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
        ai_result=_build_model_response(
            item=item,
            translation_lines=translated_text.splitlines(),
        ),
        items=[item],
        right_queue=right_queue,
        error_queue=error_queue,
        text_rules=text_rules,
    )

    assert error_queue.empty()
    result = await right_queue.get()
    assert result is not None
    return result[0]


async def _verify_single_item(
    *,
    item: TranslationItem,
    translation_lines: list[str],
    text_rules: TextRules,
) -> tuple[list[TranslationItem] | None, list[TranslationErrorItem] | None]:
    """执行单条模型译文校验并返回成功或失败条目。"""
    item.build_placeholders(text_rules)
    right_queue: asyncio.Queue[list[TranslationItem] | None] = asyncio.Queue()
    error_queue: asyncio.Queue[list[TranslationErrorItem] | None] = asyncio.Queue()

    await verify_translation_batch(
        ai_result=_build_model_response(item=item, translation_lines=translation_lines),
        items=[item],
        right_queue=right_queue,
        error_queue=error_queue,
        text_rules=text_rules,
    )

    right_items = None if right_queue.empty() else await right_queue.get()
    error_items = None if error_queue.empty() else await error_queue.get()
    return right_items, error_items


@pytest.mark.asyncio
async def test_multiline_short_text_keeps_existing_line_breaks_without_wrapping() -> None:
    """单值多行显示文本保留原有换行结构，不自动新增换行。"""
    text_rules = _build_text_rules(width_limit=8)
    item = TranslationItem(
        location_path="Items.json/1/note/拡張説明",
        item_type="short_text",
        original_lines=["説明\n「原文」"],
    )

    right_items, error_items = await _verify_single_item(
        item=item,
        translation_lines=["说明\n「甲乙丙丁戊己，庚辛壬癸」"],
        text_rules=text_rules,
    )

    assert error_items is None
    assert right_items is not None
    assert right_items[0].translation_lines == ["说明\n「甲乙丙丁戊己，庚辛壬癸」"]


@pytest.mark.asyncio
async def test_literal_line_break_short_text_keeps_literal_marker() -> None:
    """源文使用字面量反斜杠 n 时，模型必须保留同等数量的换行标记。"""
    text_rules = _build_text_rules(width_limit=8)
    item = TranslationItem(
        location_path="plugins.js/1/message",
        item_type="short_text",
        original_lines=["第一行\\n第二行"],
    )

    right_items, error_items = await _verify_single_item(
        item=item,
        translation_lines=[f"甲乙丙丁{LITERAL_LINE_BREAK_PLACEHOLDER}戊己庚辛"],
        text_rules=text_rules,
    )

    assert error_items is None
    assert right_items is not None
    assert right_items[0].translation_lines == ["甲乙丙丁\\n戊己庚辛"]


@pytest.mark.asyncio
async def test_literal_line_break_placeholder_short_text_does_not_add_line_breaks() -> None:
    """模型保留字面量换行占位符时，short_text 不再为切宽新增换行。"""
    text_rules = _build_text_rules(width_limit=8)
    item = TranslationItem(
        location_path="plugins.js/1/message",
        item_type="short_text",
        original_lines=["説明\\n本文"],
    )

    right_items, error_items = await _verify_single_item(
        item=item,
        translation_lines=[f"说明{LITERAL_LINE_BREAK_PLACEHOLDER}甲乙丙丁戊己，庚辛壬癸"],
        text_rules=text_rules,
    )

    assert error_items is None
    assert right_items is not None
    assert right_items[0].translation_lines == ["说明\\n甲乙丙丁戊己，庚辛壬癸"]


@pytest.mark.parametrize(
    ("original_lines", "translation_lines", "expected_detail"),
    [
        (["説明"], ["第一行", "第二行"], "单字段文本必须只提供 1 条中文译文行"),
        (["説明"], ["第一行\n第二行"], "译文真实换行数量不一致"),
        (["説明"], [r"第一行\n第二行"], "译文字面量换行标记数量不一致"),
        (["説明\n本文"], ["说明\n第一行\n第二行"], "译文真实换行数量不一致"),
        (
            [r"説明\n本文"],
            [f"说明{LITERAL_LINE_BREAK_PLACEHOLDER}甲{LITERAL_LINE_BREAK_PLACEHOLDER}乙"],
            "译文字面量换行标记数量不一致",
        ),
    ],
)
@pytest.mark.asyncio
async def test_short_text_structure_mismatch_is_recorded(
    original_lines: list[str],
    translation_lines: list[str],
    expected_detail: str,
) -> None:
    """short_text 译文新增或删除换行结构时直接记为结构错误。"""
    text_rules = _build_text_rules(width_limit=40)
    item = TranslationItem(
        location_path="plugins.js/1/message",
        item_type="short_text",
        original_lines=original_lines,
    )

    right_items, error_items = await _verify_single_item(
        item=item,
        translation_lines=translation_lines,
        text_rules=text_rules,
    )

    assert right_items is None
    assert error_items is not None
    assert error_items[0].error_type == "文本结构不匹配"
    assert any(expected_detail in detail for detail in error_items[0].error_detail)


@pytest.mark.parametrize(
    "translation_text",
    [
        "译文：你好",
        "以下是翻译：你好",
        "translation_lines: 你好",
        "Map001.json/1/0/0",
    ],
)
@pytest.mark.asyncio
async def test_model_artifacts_are_recorded_as_text_structure_errors(translation_text: str) -> None:
    """模型解释性文本、协议字段和内部定位不能保存为译文。"""
    text_rules = _build_text_rules(width_limit=40)
    item = TranslationItem(
        location_path="Map001.json/1/0/0",
        item_type="short_text",
        original_lines=["こんにちは"],
    )

    right_items, error_items = await _verify_single_item(
        item=item,
        translation_lines=[translation_text],
        text_rules=text_rules,
    )

    assert right_items is None
    assert error_items is not None
    assert error_items[0].error_type == "文本结构不匹配"


@pytest.mark.asyncio
async def test_translation_response_ignores_source_lines_and_extra_fields() -> None:
    """模型返回的原文对照和额外字段不参与业务校验。"""
    text_rules = _build_text_rules(width_limit=40)
    item = TranslationItem(
        location_path="Map001.json/1/0/0",
        item_type="long_text",
        role="村人",
        original_lines=["こんにちは"],
    )
    item.build_placeholders(text_rules)
    right_queue: asyncio.Queue[list[TranslationItem] | None] = asyncio.Queue()
    error_queue: asyncio.Queue[list[TranslationErrorItem] | None] = asyncio.Queue()

    await verify_translation_batch(
        ai_result=_build_model_response(
            item=item,
            source_lines=["模型改写的原文"],
            translation_lines=["你好"],
            extra_fields={"type": "wrong", "unused": ["ignored"]},
        ),
        items=[item],
        right_queue=right_queue,
        error_queue=error_queue,
        text_rules=text_rules,
    )

    assert error_queue.empty()
    result = await right_queue.get()
    assert result is not None
    assert result[0].translation_lines == ["你好"]


@pytest.mark.asyncio
async def test_translation_response_missing_id_is_recorded_as_missing_key() -> None:
    """未知 ID 被忽略后，本地未返回条目仍按漏翻处理。"""
    text_rules = _build_text_rules(width_limit=40)
    item = TranslationItem(
        location_path="Map001.json/1/0/0",
        item_type="long_text",
        original_lines=["こんにちは"],
    )
    item.build_placeholders(text_rules)
    right_queue: asyncio.Queue[list[TranslationItem] | None] = asyncio.Queue()
    error_queue: asyncio.Queue[list[TranslationErrorItem] | None] = asyncio.Queue()
    ai_result = json.dumps(
        [
            {
                "id": "Map999.json/1/0/0",
                "role": "",
                "source_lines": ["こんにちは"],
                "translation_lines": ["你好"],
            }
        ],
        ensure_ascii=False,
    )

    await verify_translation_batch(
        ai_result=ai_result,
        items=[item],
        right_queue=right_queue,
        error_queue=error_queue,
        text_rules=text_rules,
    )

    assert right_queue.empty()
    error_items = await error_queue.get()
    assert error_items is not None
    assert error_items[0].error_type == "AI漏翻"


@pytest.mark.parametrize(
    ("item_type", "original_lines", "translation_lines"),
    [
        ("long_text", ["こんにちは"], []),
        ("short_text", ["説明"], [""]),
        ("array", ["はい", "いいえ"], [" ", ""]),
    ],
)
@pytest.mark.asyncio
async def test_empty_translation_lines_are_recorded_as_missing_translation(
    item_type: ItemType,
    original_lines: list[str],
    translation_lines: list[str],
) -> None:
    """模型返回空数组或全空白译文时按漏翻记录，禁止保存空译文。"""
    text_rules = _build_text_rules(width_limit=40)
    item = TranslationItem(
        location_path="Map001.json/1/0/0",
        item_type=item_type,
        original_lines=original_lines,
    )
    item.build_placeholders(text_rules)
    right_queue: asyncio.Queue[list[TranslationItem] | None] = asyncio.Queue()
    error_queue: asyncio.Queue[list[TranslationErrorItem] | None] = asyncio.Queue()

    await verify_translation_batch(
        ai_result=_build_model_response(item=item, translation_lines=translation_lines),
        items=[item],
        right_queue=right_queue,
        error_queue=error_queue,
        text_rules=text_rules,
    )

    assert right_queue.empty()
    error_items = await error_queue.get()
    assert error_items is not None
    assert error_items[0].error_type == "AI漏翻"
    assert error_items[0].error_detail == ["AI漏翻: 模型返回空译文"]


@pytest.mark.asyncio
async def test_translation_response_duplicate_valid_id_blocks_batch() -> None:
    """同一批次重复返回有效 ID 时整批作为格式错误处理。"""
    text_rules = _build_text_rules(width_limit=40)
    item = TranslationItem(
        location_path="Map001.json/1/0/0",
        item_type="long_text",
        original_lines=["こんにちは"],
    )
    item.build_placeholders(text_rules)
    response_item = {
        "id": item.location_path,
        "role": "",
        "source_lines": ["こんにちは"],
        "translation_lines": ["你好"],
    }
    right_queue: asyncio.Queue[list[TranslationItem] | None] = asyncio.Queue()
    error_queue: asyncio.Queue[list[TranslationErrorItem] | None] = asyncio.Queue()

    await verify_translation_batch(
        ai_result=json.dumps([response_item, response_item], ensure_ascii=False),
        items=[item],
        right_queue=right_queue,
        error_queue=error_queue,
        text_rules=text_rules,
    )

    assert right_queue.empty()
    error_items = await error_queue.get()
    assert error_items is not None
    assert error_items[0].error_type == "模型返回不可解析"


@pytest.mark.asyncio
async def test_array_response_line_count_mismatch_is_recorded() -> None:
    """array 译文行数仍按本地原文数量校验。"""
    text_rules = _build_text_rules(width_limit=40)
    item = TranslationItem(
        location_path="Map001.json/1/0/2",
        item_type="array",
        original_lines=["はい", "いいえ"],
    )
    item.build_placeholders(text_rules)
    right_queue: asyncio.Queue[list[TranslationItem] | None] = asyncio.Queue()
    error_queue: asyncio.Queue[list[TranslationErrorItem] | None] = asyncio.Queue()

    await verify_translation_batch(
        ai_result=_build_model_response(item=item, translation_lines=["是"]),
        items=[item],
        right_queue=right_queue,
        error_queue=error_queue,
        text_rules=text_rules,
    )

    assert right_queue.empty()
    error_items = await error_queue.get()
    assert error_items is not None
    assert error_items[0].error_type == "选项行数不匹配"


def test_single_text_value_wraps_embedded_display_lines() -> None:
    """单值文本内部换行按显示行切宽后仍作为一个字段返回。"""
    text_rules = _build_text_rules(width_limit=8)

    text = split_overwide_single_text_value_if_needed(
        original_lines=["説明\n「原文」"],
        translation_text="说明\n「甲乙丙丁戊己，庚辛壬癸」",
        location_path="Items.json/1/note/拡張説明",
        text_rules=text_rules,
    )

    assert text == "说明\n「甲乙丙丁戊己，\n　庚辛壬癸」"


def test_single_text_value_preserves_literal_line_break_markers() -> None:
    """源文使用字面量反斜杠 n 时，切宽后仍返回单个字段。"""
    text_rules = _build_text_rules(width_limit=8)

    text = split_overwide_single_text_value_if_needed(
        original_lines=["説明\\n「原文」"],
        translation_text="说明\n「甲乙丙丁戊己，庚辛壬癸」",
        location_path="plugins.js/1/message",
        text_rules=text_rules,
    )

    assert text == "说明\\n「甲乙丙丁戊己，\\n　庚辛壬癸」"


def test_single_text_value_splits_by_literal_line_break_placeholder() -> None:
    """标准换行占位符参与 short_text 的显示行切宽。"""
    text_rules = _build_text_rules(width_limit=8)

    text = split_overwide_single_text_value_if_needed(
        original_lines=["説明\\n原文"],
        translation_text=f"说明{LITERAL_LINE_BREAK_PLACEHOLDER}甲乙丙丁戊己，庚辛壬癸",
        location_path="plugins.js/1/message",
        text_rules=text_rules,
    )

    assert text == f"说明{LITERAL_LINE_BREAK_PLACEHOLDER}甲乙丙丁戊己，{LITERAL_LINE_BREAK_PLACEHOLDER}庚辛壬癸"


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
        translated_text="[RMMZ_TEXT_COLOR_4]甲乙丙丁[RMMZ_TEXT_COLOR_0]",
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


def test_hard_split_backs_off_when_only_trailing_punctuation_exceeds_limit() -> None:
    """只有句末标点越界时，硬切回退到可读尾段而不是保留超宽行。"""
    text_rules = _build_text_rules(width_limit=26)

    lines = split_overwide_lines(
        lines=["地下之国阿格尼卡从遗迹出土品中发展出了超常的机械技术。"],
        location_path="Weapons.json/1/note/拡張説明",
        text_rules=text_rules,
    )

    assert len(lines) == 2
    assert all(count_line_width_chars(line, text_rules) <= 26 for line in lines)
    assert not lines[1].startswith("。")
    assert lines[1].endswith("。")


def test_wrapping_punctuation_continuation_line_gets_visual_indent() -> None:
    """跨行引号续行自动补全角空格，保持游戏窗口里的视觉对齐。"""
    text_rules = _build_text_rules(width_limit=20)

    lines = split_overwide_lines(
        lines=["「甲乙丙。", "丁戊己」"],
        location_path="Map001.json/1/0/0",
        text_rules=text_rules,
    )

    assert lines == ["「甲乙丙。", "　丁戊己」"]


def test_source_corner_quote_converted_to_curly_quote_is_restored_before_indent() -> None:
    """源文外层日文引号被模型改成中文弯引号时先修回再补缩进。"""
    text_rules = _build_text_rules(width_limit=40)

    lines = align_long_text_lines(
        text="“啊啊，是啊。我才被狼袭击了。还以为要死了，\n累得要命。”",
        target_lines=3,
        location_path="CommonEvents.json/1/0",
        text_rules=text_rules,
        original_lines=[
            "「ああ、そうだよ。",
            "こっちは狼に襲われたばっかりだ。",
            "死ぬかと思ったし、めちゃくちゃ疲れてる」",
        ],
    )

    assert lines == ["「啊啊，是啊。我才被狼袭击了。还以为要死了，", "　累得要命。」"]


def test_source_corner_quote_fix_ignores_edge_control_sequences() -> None:
    """外层引号修复忽略行首行尾控制符，避免漏掉带头像控制符的对白。"""
    text_rules = TextRules.from_setting(
        TextRulesSetting(
            long_text_line_width_limit=40,
            line_split_punctuations=["，", "。"],
        ),
        custom_placeholder_rules=(
            CustomPlaceholderRule.create(r"\\F\d*\[[^\]\r\n]+\]", "[CUSTOM_FACE_PORTRAIT_{index}]"),
        ),
    )

    lines = align_long_text_lines(
        text=r"\F1[2]“甲乙丙。\n丁戊己。”\C[0]".replace(r"\n", "\n"),
        target_lines=2,
        location_path="Map001.json/1/0/0",
        text_rules=text_rules,
        original_lines=[r"\F1[2]「あ。", r"い」\C[0]"],
    )

    assert lines == [r"\F1[2]「甲乙丙。", r"　丁戊己。」\C[0]"]


def test_source_corner_quote_fix_keeps_unquoted_translation_unchanged() -> None:
    """译文没有成对引号时不强行补引号，避免误改普通正文。"""
    text_rules = _build_text_rules(width_limit=40)

    lines = align_long_text_lines(
        text="甲乙丙。\n丁戊己。",
        target_lines=2,
        location_path="Map001.json/1/0/0",
        text_rules=text_rules,
        original_lines=["「あ。", "い」"],
    )

    assert lines == ["甲乙丙。", "丁戊己。"]


def test_inner_corner_quote_converted_to_curly_quote_is_restored() -> None:
    """源文内部日文引号被模型改成中文弯引号时修回日文引号。"""
    text_rules = _build_text_rules(width_limit=40)

    lines = align_long_text_lines(
        text="上面绣着“莉亚”。",
        target_lines=1,
        location_path="Items.json/1/note/拡張説明",
        text_rules=text_rules,
        original_lines=["「リア」と刺繍がある"],
    )

    assert lines == ["上面绣着「莉亚」。"]


def test_inner_corner_quote_converted_to_straight_quotes_is_restored() -> None:
    """多个内部日文引号被模型改成直引号时按顺序修回。"""
    text_rules = _build_text_rules(width_limit=40)

    lines = align_long_text_lines(
        text='部分患者报告"能看到声音""主就在身边"。',
        target_lines=1,
        location_path="CommonEvents.json/1/0",
        text_rules=text_rules,
        original_lines=["患者の一部は「声が見える」「主が近い」と報告。"],
    )

    assert lines == ["部分患者报告「能看到声音」「主就在身边」。"]


def test_inner_corner_quote_converted_to_single_quotes_is_restored() -> None:
    """内部日文引号被模型改成英文单引号时也按顺序修回。"""
    text_rules = _build_text_rules(width_limit=40)

    lines = align_long_text_lines(
        text="上面绣着'莉亚'。",
        target_lines=1,
        location_path="Items.json/1/note/拡張説明",
        text_rules=text_rules,
        original_lines=["「リア」と刺繍がある"],
    )

    assert lines == ["上面绣着「莉亚」。"]


def test_inner_book_title_quote_converted_to_straight_quotes_is_restored() -> None:
    """内部日文书名号被模型改成英文直引号时也按顺序修回。"""
    text_rules = _build_text_rules(width_limit=40)

    lines = align_long_text_lines(
        text='莉可开了个叫"莉可银行"的地方。',
        target_lines=1,
        location_path="plugins.js/1/message",
        text_rules=text_rules,
        original_lines=["リコは『リコの銀行』なるものを始めたようだ。"],
    )

    assert lines == ["莉可开了个叫『莉可银行』的地方。"]


@pytest.mark.asyncio
async def test_short_text_inner_book_title_quote_converted_to_curly_quote_is_restored() -> None:
    """单值多行文本中的内部日文书名号被模型改写时修回源文符号。"""
    text_rules = _build_text_rules(width_limit=80)
    original_text = (
        r"\C[2]イベント完了"
        "\n\n"
        r"\C[24]【詳細】\C[0]"
        "\n研究室を手に入れた。"
        "\nリコはちゃっかりと『リコの銀行』なるものを始めたようだ。"
    )
    translated_text = (
        r"\C[2]事件完成"
        "\n\n"
        r"\C[24]【详情】\C[0]"
        "\n得到了研究室。"
        "\n莉可倒是很精明地搞了个叫“莉可银行”的玩意儿。"
    )
    item = TranslationItem(
        location_path="plugins.js/1/message",
        item_type="short_text",
        original_lines=[original_text],
    )
    item.build_placeholders(text_rules)
    right_queue: asyncio.Queue[list[TranslationItem] | None] = asyncio.Queue()
    error_queue: asyncio.Queue[list[TranslationErrorItem] | None] = asyncio.Queue()

    await verify_translation_batch(
        ai_result=_build_model_response(
            item=item,
            translation_lines=[translated_text],
        ),
        items=[item],
        right_queue=right_queue,
        error_queue=error_queue,
        text_rules=text_rules,
    )

    assert error_queue.empty()
    result = await right_queue.get()
    assert result is not None
    assert result[0].translation_lines == [
        (
            r"\C[2]事件完成"
            "\n\n"
            r"\C[24]【详情】\C[0]"
            "\n得到了研究室。"
            "\n莉可倒是很精明地搞了个叫『莉可银行』的玩意儿。"
        )
    ]


def test_inner_corner_quote_fix_skips_ambiguous_extra_translation_quotes() -> None:
    """译文引号数量无法和源文一一对应时保持原样，避免误改新增引号。"""
    text_rules = _build_text_rules(width_limit=40)

    lines = align_long_text_lines(
        text="所谓“记录”，上面绣着“莉亚”。",
        target_lines=1,
        location_path="Items.json/1/note/拡張説明",
        text_rules=text_rules,
        original_lines=["「リア」と刺繍がある"],
    )

    assert lines == ["所谓“记录”，上面绣着“莉亚”。"]


def test_inner_corner_quote_fix_skips_unpaired_translation_quote() -> None:
    """译文存在未配对引号时不自动修复，避免制造重复引号。"""
    text_rules = _build_text_rules(width_limit=40)

    lines = align_long_text_lines(
        text="“所以……求你了。做夫妻之事吧？”」",
        target_lines=2,
        location_path="CommonEvents.json/1/0",
        text_rules=text_rules,
        original_lines=["「だから……お願い。", "ふうふのいとなみ」、しよ？"],
    )

    assert lines == ["“所以……求你了。做夫妻之事吧？”」"]


def test_wrapping_punctuation_split_tail_gets_visual_indent() -> None:
    """同一行被自动拆短后，拆出的引号续行也补全角空格。"""
    text_rules = _build_text_rules(width_limit=8)

    lines = split_overwide_lines(
        lines=["「甲乙丙丁戊己，庚辛壬癸」"],
        location_path="Map001.json/1/0/0",
        text_rules=text_rules,
    )

    assert lines == ["「甲乙丙丁戊己，", "　庚辛壬癸」"]


def test_wrapping_punctuation_existing_indent_is_preserved() -> None:
    """Agent 已经补过续行缩进时不重复插入空白。"""
    text_rules = _build_text_rules(width_limit=20)

    lines = split_overwide_lines(
        lines=["「甲乙丙。", "　丁戊己」"],
        location_path="Map001.json/1/0/0",
        text_rules=text_rules,
    )

    assert lines == ["「甲乙丙。", "　丁戊己」"]


def test_wrapping_punctuation_state_ignores_edge_control_sequences() -> None:
    """包裹标点状态判定忽略行首和行尾控制符。"""
    text_rules = _build_text_rules(width_limit=20)

    lines = split_overwide_lines(
        lines=[r"\C[2]「甲乙丙。", r"丁戊己」\C[0]", "庚辛"],
        location_path="Map001.json/1/0/0",
        text_rules=text_rules,
    )

    assert lines == [r"\C[2]「甲乙丙。", r"　丁戊己」\C[0]", "庚辛"]
