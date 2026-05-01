"""
正文翻译校验模块。

负责解析模型返回的 JSON，按 `location_path` 映射回翻译条目，并执行漏翻、
占位符和日文残留校验。
"""

import asyncio

from json_repair import repair_json
from pydantic import RootModel

from app.rmmz.schema import ErrorType, TranslationErrorItem, TranslationItem
from app.rmmz.text_rules import TextRules
from app.translation.line_wrap import align_long_text_lines

ERR_MISSING_KEY: ErrorType = "AI漏翻"
ERR_PLACEHOLDER_MISMATCH: ErrorType = "控制符不匹配"
ERR_JAPANESE_RESIDUAL: ErrorType = "日文残留"


class TranslationResponse(RootModel[dict[str, str]]):
    """正文翻译返回结果模型。"""


async def verify_translation_batch(
    *,
    ai_result: str,
    items: list[TranslationItem],
    right_queue: asyncio.Queue[list[TranslationItem] | None],
    error_queue: asyncio.Queue[list[TranslationErrorItem] | None],
    text_rules: TextRules,
) -> None:
    """解析模型返回并把通过校验/失败条目分别推入队列。"""
    right_items: list[TranslationItem] = []
    error_items: list[TranslationErrorItem] = []

    try:
        clean_result = repair_json(ai_result, return_objects=False)

        translation_map = TranslationResponse.model_validate_json(clean_result).root
    except Exception as error:
        for item in items:
            error_items.append(
                TranslationErrorItem(
                    location_path=item.location_path,
                    item_type=item.item_type,
                    role=item.role,
                    original_lines=list(item.original_lines),
                    translation_lines=[],
                    error_type=ERR_MISSING_KEY,
                    error_detail=["模型返回无法解析为 JSON 对象", f"详细错误: {error}"],
                    model_response=ai_result,
                )
            )
        if error_items:
            await error_queue.put(error_items)
        return

    for item in items:
        translation_text = translation_map.get(item.location_path)
        if translation_text is None:
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
                text=translation_text,
                target_lines=len(item.original_lines),
                location_path=item.location_path,
                text_rules=text_rules,
            )
        elif item.item_type == "array":
            translation_lines = translation_text.splitlines()
            if len(translation_lines) != len(item.original_lines):
                error_items.append(
                    TranslationErrorItem(
                        location_path=item.location_path,
                        item_type=item.item_type,
                        role=item.role,
                        original_lines=list(item.original_lines),
                        translation_lines=list(translation_lines),
                        error_type=ERR_PLACEHOLDER_MISMATCH,
                        error_detail=[f"选项行数不匹配: 期望 {len(item.original_lines)} 行, 实际 {len(translation_lines)} 行"],
                        model_response=ai_result,
                    )
                )
                continue
        else:
            translation_lines = [translation_text]

        item.translation_lines_with_placeholders = list(translation_lines)
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
            text_rules.check_japanese_residual(item.translation_lines)
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


__all__: list[str] = ["verify_translation_batch"]
