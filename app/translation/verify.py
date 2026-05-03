"""
正文翻译校验模块。

负责解析模型返回的 JSON，按 `location_path` 映射回翻译条目，并执行漏翻、
占位符和日文残留校验。
"""

import asyncio

from json_repair import repair_json
from pydantic import BaseModel, RootModel

from app.japanese_residual import JapaneseResidualRuleSet, check_japanese_residual_for_item
from app.rmmz.schema import ErrorType, TranslationErrorItem, TranslationItem
from app.rmmz.text_rules import ControlSequenceSpan, TextRules
from app.translation.line_wrap import (
    align_long_text_lines,
    normalize_translated_wrapping_punctuation,
    split_overwide_single_text_value_if_needed,
)

ERR_PARSE_FAILED: ErrorType = "模型返回不可解析"
ERR_MISSING_KEY: ErrorType = "AI漏翻"
ERR_PLACEHOLDER_MISMATCH: ErrorType = "控制符不匹配"
ERR_JAPANESE_RESIDUAL: ErrorType = "日文残留"
ERR_ARRAY_LINE_COUNT: ErrorType = "选项行数不匹配"


class TranslationResponseItem(BaseModel):
    """模型返回的单条对照译文。"""

    id: str
    translation_lines: list[str]


class TranslationResponse(RootModel[list[TranslationResponseItem]]):
    """正文翻译返回结果模型。"""


async def verify_translation_batch(
    *,
    ai_result: str,
    items: list[TranslationItem],
    right_queue: asyncio.Queue[list[TranslationItem] | None],
    error_queue: asyncio.Queue[list[TranslationErrorItem] | None],
    text_rules: TextRules,
    japanese_residual_rule_set: JapaneseResidualRuleSet | None = None,
) -> None:
    """解析模型返回并把通过校验/失败条目分别推入队列。"""
    right_items: list[TranslationItem] = []
    error_items: list[TranslationErrorItem] = []

    try:
        clean_result = repair_json(ai_result, return_objects=False)

        response_items = TranslationResponse.model_validate_json(clean_result).root
        translation_map = _build_translation_line_map(response_items=response_items, items=items)
    except Exception as error:
        for item in items:
            error_items.append(
                TranslationErrorItem(
                    location_path=item.location_path,
                    item_type=item.item_type,
                    role=item.role,
                    original_lines=list(item.original_lines),
                    translation_lines=[],
                    error_type=ERR_PARSE_FAILED,
                    error_detail=["模型返回无法解析为 JSON 数组", f"详细错误: {error}"],
                    model_response=ai_result,
                )
            )
        if error_items:
            await error_queue.put(error_items)
        return

    for item in items:
        model_translation_lines = translation_map.get(item.location_path)
        if model_translation_lines is None:
            error_items.append(
                TranslationErrorItem(
                    location_path=item.location_path,
                    item_type=item.item_type,
                    role=item.role,
                    original_lines=list(item.original_lines),
                    translation_lines=[],
                    error_type=ERR_MISSING_KEY,
                    error_detail=[f"AI漏翻: 未找到键 {item.location_path}"],
                    model_response=ai_result,
                )
            )
            continue

        if item.item_type == "long_text":
            translation_lines = align_long_text_lines(
                text="\n".join(model_translation_lines),
                target_lines=len(item.original_lines),
                location_path=item.location_path,
                text_rules=text_rules,
                original_lines=item.original_lines,
            )
        elif item.item_type == "array":
            translation_lines = list(model_translation_lines)
            translation_lines = normalize_translated_wrapping_punctuation(
                original_lines=item.original_lines,
                translation_lines=translation_lines,
                text_rules=text_rules,
            )
            if len(translation_lines) != len(item.original_lines):
                error_items.append(
                    TranslationErrorItem(
                        location_path=item.location_path,
                        item_type=item.item_type,
                        role=item.role,
                        original_lines=list(item.original_lines),
                        translation_lines=list(translation_lines),
                        error_type=ERR_ARRAY_LINE_COUNT,
                        error_detail=[f"选项行数不匹配: 期望 {len(item.original_lines)} 行, 实际 {len(translation_lines)} 行"],
                        model_response=ai_result,
                    )
                )
                continue
        else:
            translation_lines = ["\n".join(model_translation_lines)]
            translation_lines = normalize_translated_wrapping_punctuation(
                original_lines=item.original_lines,
                translation_lines=translation_lines,
                text_rules=text_rules,
            )
            if translation_lines:
                translation_lines[0] = split_overwide_single_text_value_if_needed(
                    original_lines=item.original_lines,
                    translation_text=translation_lines[0],
                    location_path=item.location_path,
                    text_rules=text_rules,
                )

        item.translation_lines_with_placeholders = _mask_known_translation_controls(
            item=item,
            translation_lines=translation_lines,
            text_rules=text_rules,
        )
        item.translation_lines = []

        try:
            item.verify_placeholders(text_rules)
            item.restore_placeholders()
        except ValueError as error:
            error_items.append(
                TranslationErrorItem(
                    location_path=item.location_path,
                    item_type=item.item_type,
                    role=item.role,
                    original_lines=list(item.original_lines),
                    translation_lines=list(item.translation_lines_with_placeholders),
                    error_type=ERR_PLACEHOLDER_MISMATCH,
                    error_detail=str(error).split(";\n"),
                    model_response=ai_result,
                )
            )
            continue

        try:
            check_japanese_residual_for_item(
                item=item,
                text_rules=text_rules,
                rule_set=japanese_residual_rule_set,
            )
        except ValueError as error:
            error_items.append(
                TranslationErrorItem(
                    location_path=item.location_path,
                    item_type=item.item_type,
                    role=item.role,
                    original_lines=list(item.original_lines),
                    translation_lines=list(item.translation_lines),
                    error_type=ERR_JAPANESE_RESIDUAL,
                    error_detail=[str(error)],
                    model_response=ai_result,
                )
            )
            continue

        right_items.append(item)

    if right_items:
        await right_queue.put(right_items)
    if error_items:
        await error_queue.put(error_items)


def _build_translation_line_map(
    *,
    response_items: list[TranslationResponseItem],
    items: list[TranslationItem],
) -> dict[str, list[str]]:
    """按本地批次条目收窄模型译文，忽略无关字段和未知 ID。"""
    valid_ids = {item.location_path for item in items}
    translation_map: dict[str, list[str]] = {}
    for response_item in response_items:
        if response_item.id not in valid_ids:
            continue
        if response_item.id in translation_map:
            raise ValueError(f"模型返回重复 ID: {response_item.id}")
        translation_map[response_item.id] = list(response_item.translation_lines)
    return translation_map


def _mask_known_translation_controls(
    *,
    item: TranslationItem,
    translation_lines: list[str],
    text_rules: TextRules,
) -> list[str]:
    """把模型返回的原始控制符修回本条原文对应的占位符。"""
    reverse_map = {original: placeholder for placeholder, original in item.placeholder_map.items()}

    def replacer(span: ControlSequenceSpan) -> str:
        """只修回原文已有的控制符，未知控制符继续交给后续校验。"""
        placeholder = reverse_map.get(span.original)
        if placeholder is not None:
            return placeholder
        return span.original

    return [
        text_rules.replace_rm_control_sequences(line, replacer)
        for line in translation_lines
    ]


__all__: list[str] = ["verify_translation_batch"]
