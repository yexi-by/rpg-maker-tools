"""
正文翻译校验模块。

负责解析模型返回的 JSON，按 `location_path` 映射回翻译条目，并执行漏翻、
占位符和日文残留校验。英文残留兼容已删除。
"""

import asyncio
import re

from json_repair import repair_json
from pydantic import RootModel

from app.rmmz.schema import ErrorType, TranslationErrorItem, TranslationItem
from app.rmmz.text_rules import TextRules
from app.observability import logger

ERR_MISSING_KEY: ErrorType = "AI漏翻"
ERR_PLACEHOLDER_MISMATCH: ErrorType = "控制符不匹配"
ERR_JAPANESE_RESIDUAL: ErrorType = "日文残留"
HANZI_PATTERN: re.Pattern[str] = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")


class TranslationResponse(RootModel[dict[str, str]]):
    """正文翻译返回结果模型。"""


def _build_warning_preview(text: str, max_length: int = 40) -> str:
    """生成日志预览文本，避免告警刷屏。"""
    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}..."


def _count_hanzi(text: str) -> int:
    """统计文本中的汉字数量。"""
    return len(HANZI_PATTERN.findall(text))


def _find_preferred_split_position(text: str, text_rules: TextRules) -> int | None:
    """在单行上限内寻找优先切分点。"""
    best_index = -1
    hanzi_count = 0
    punctuations = set(text_rules.setting.line_split_punctuations)

    for index, char in enumerate(text):
        if HANZI_PATTERN.fullmatch(char):
            hanzi_count += 1
        if hanzi_count > text_rules.setting.long_text_hanzi_limit:
            break
        if char in punctuations:
            best_index = index

    if best_index < 0:
        return None
    return best_index + 1


def _log_align_warning(*, location_path: str | None, line: str, reason: str, text_rules: TextRules) -> None:
    """记录长文本自动补切行失败的告警日志。"""
    logger.warning(
        "长文本自动补切行告警: 路径={}，汉字数={}，上限={}，原因={}，内容预览={}",
        location_path or "<unknown>",
        _count_hanzi(line),
        text_rules.setting.long_text_hanzi_limit,
        reason,
        _build_warning_preview(line),
    )


def _expand_long_lines(
    *,
    lines: list[str],
    target_lines: int,
    location_path: str | None,
    text_rules: TextRules,
) -> list[str]:
    """在还有空余行数时尝试把过长文本安全切开。"""
    expanded_lines: list[str] = []
    remaining_extra_lines = max(target_lines - len(lines), 0)
    hanzi_limit = text_rules.setting.long_text_hanzi_limit

    for line in lines:
        pending_line = line
        while _count_hanzi(pending_line) > hanzi_limit and remaining_extra_lines > 0:
            split_position = _find_preferred_split_position(pending_line, text_rules)
            if split_position is None:
                _log_align_warning(
                    location_path=location_path,
                    line=pending_line,
                    reason=f"前 {hanzi_limit} 个汉字内没有可配置切分标点，无法安全补切分",
                    text_rules=text_rules,
                )
                break

            head = pending_line[:split_position].rstrip()
            tail = pending_line[split_position:].lstrip()
            if not tail:
                break

            expanded_lines.append(head)
            pending_line = tail
            remaining_extra_lines -= 1

        if _count_hanzi(pending_line) > hanzi_limit and remaining_extra_lines <= 0:
            _log_align_warning(
                location_path=location_path,
                line=pending_line,
                reason="建议行数已用尽，无法继续补切分",
                text_rules=text_rules,
            )

        expanded_lines.append(pending_line)

    return expanded_lines


def _align_lines(
    text: str,
    target_lines: int,
    *,
    location_path: str | None,
    text_rules: TextRules,
) -> list[str]:
    """将大模型返回文本按原内容行数要求进行对齐。"""
    normalized_target_lines = max(1, target_lines)
    if not text:
        return [""] * normalized_target_lines

    lines = text.split("\n")
    current_count = len(lines)

    if current_count > normalized_target_lines:
        keep_lines = lines[: max(normalized_target_lines - 1, 0)]
        merged_tail = " ".join(lines[max(normalized_target_lines - 1, 0) :])
        keep_lines.append(merged_tail)
        lines = keep_lines

    if current_count < normalized_target_lines:
        lines = _expand_long_lines(
            lines=lines,
            target_lines=normalized_target_lines,
            location_path=location_path,
            text_rules=text_rules,
        )
    else:
        for line in lines:
            if _count_hanzi(line) > text_rules.setting.long_text_hanzi_limit:
                _log_align_warning(
                    location_path=location_path,
                    line=line,
                    reason="当前结果已经没有多余空行可用于补切分",
                    text_rules=text_rules,
                )

    if len(lines) < normalized_target_lines:
        lines.extend([""] * (normalized_target_lines - len(lines)))

    return lines


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
                (
                    item.location_path,
                    item.item_type,
                    item.role,
                    list(item.original_lines),
                    [],
                    ERR_MISSING_KEY,
                    ["模型返回无法解析为 JSON 对象", f"详细错误: {error}"],
                )
            )
        if error_items:
            await error_queue.put(error_items)
        return

    for item in items:
        translation_text = translation_map.get(item.location_path)
        if translation_text is None:
            error_items.append(
                (
                    item.location_path,
                    item.item_type,
                    item.role,
                    list(item.original_lines),
                    [],
                    ERR_MISSING_KEY,
                    [f"AI漏翻: 未找到键 {item.location_path}"],
                )
            )
            continue

        if item.item_type == "long_text":
            translation_lines = _align_lines(
                text=translation_text,
                target_lines=len(item.original_lines),
                location_path=item.location_path,
                text_rules=text_rules,
            )
        elif item.item_type == "array":
            translation_lines = translation_text.splitlines()
            if len(translation_lines) != len(item.original_lines):
                error_items.append(
                    (
                        item.location_path,
                        item.item_type,
                        item.role,
                        list(item.original_lines),
                        list(translation_lines),
                        ERR_PLACEHOLDER_MISMATCH,
                        [f"选项行数不匹配: 期望 {len(item.original_lines)} 行, 实际 {len(translation_lines)} 行"],
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
                (
                    item.location_path,
                    item.item_type,
                    item.role,
                    list(item.original_lines),
                    list(item.translation_lines_with_placeholders),
                    ERR_PLACEHOLDER_MISMATCH,
                    str(error).split(";\n"),
                )
            )
            continue

        try:
            text_rules.check_japanese_residual(item.translation_lines)
        except ValueError as error:
            error_items.append(
                (
                    item.location_path,
                    item.item_type,
                    item.role,
                    list(item.original_lines),
                    list(item.translation_lines),
                    ERR_JAPANESE_RESIDUAL,
                    [str(error)],
                )
            )
            continue

        right_items.append(item)

    if right_items:
        await right_queue.put(right_items)
    if error_items:
        await error_queue.put(error_items)


__all__: list[str] = ["verify_translation_batch"]
