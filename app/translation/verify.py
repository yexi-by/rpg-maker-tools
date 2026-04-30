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
from app.observability import logger

ERR_MISSING_KEY: ErrorType = "AI漏翻"
ERR_PLACEHOLDER_MISMATCH: ErrorType = "控制符不匹配"
ERR_JAPANESE_RESIDUAL: ErrorType = "日文残留"


class TranslationResponse(RootModel[dict[str, str]]):
    """正文翻译返回结果模型。"""


def _build_warning_preview(text: str, max_length: int = 40) -> str:
    """生成日志预览文本，避免告警刷屏。"""
    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}..."


def _count_line_width_chars(text: str, text_rules: TextRules) -> int:
    """统计长文本切行时计入长度的字符数量。"""
    return text_rules.count_line_width_chars(text)


def _find_preferred_split_position(text: str, text_rules: TextRules) -> int | None:
    """在单行上限内寻找优先切分点。"""
    best_index = -1
    line_width_count = 0
    punctuations = set(text_rules.setting.line_split_punctuations)

    for index, char in enumerate(text):
        if text_rules.is_line_width_counted_char(char):
            line_width_count += 1
        if line_width_count > text_rules.setting.long_text_line_width_limit:
            break
        if char in punctuations and not _is_inside_placeholder_token(text, index + 1, text_rules):
            best_index = index

    if best_index < 0:
        return None
    return best_index + 1


def _find_hard_split_position(text: str, text_rules: TextRules) -> int | None:
    """在没有可用标点时按计数字符上限切分。"""
    line_width_count = 0
    limit = text_rules.setting.long_text_line_width_limit
    for index, char in enumerate(text):
        if not text_rules.is_line_width_counted_char(char):
            continue
        line_width_count += 1
        if line_width_count < limit:
            continue
        return _move_split_position_outside_placeholder(text, index + 1, text_rules)
    return None


def _is_inside_placeholder_token(text: str, position: int, text_rules: TextRules) -> bool:
    """判断切分点是否落在翻译占位符内部。"""
    for match in text_rules.placeholder_token_pattern.finditer(text):
        if match.start() < position < match.end():
            return True
    return False


def _move_split_position_outside_placeholder(text: str, position: int, text_rules: TextRules) -> int:
    """把切分点移动到占位符之后，避免破坏占位符。"""
    for match in text_rules.placeholder_token_pattern.finditer(text):
        if match.start() < position < match.end():
            return match.end()
    return position


def _log_align_warning(*, location_path: str | None, line: str, reason: str, text_rules: TextRules) -> None:
    """记录长文本自动补切行失败的告警日志。"""
    logger.warning(
        "长文本自动补切行告警: 路径={}，计数字符数={}，上限={}，原因={}，内容预览={}",
        location_path or "<unknown>",
        _count_line_width_chars(line, text_rules),
        text_rules.setting.long_text_line_width_limit,
        reason,
        _build_warning_preview(line),
    )


def _split_overwide_lines(
    *,
    lines: list[str],
    location_path: str | None,
    text_rules: TextRules,
) -> list[str]:
    """按配置宽度切开过长非空行，空行保持原位。"""
    split_lines: list[str] = []
    for line in lines:
        if not line:
            split_lines.append(line)
            continue
        split_lines.extend(
            _split_single_overwide_line(
                line=line,
                location_path=location_path,
                text_rules=text_rules,
            )
        )
    return split_lines


def _split_single_overwide_line(
    *,
    line: str,
    location_path: str | None,
    text_rules: TextRules,
) -> list[str]:
    """切开单个超宽文本行。"""
    line_width_limit = text_rules.setting.long_text_line_width_limit
    result: list[str] = []
    pending_line = line
    while _count_line_width_chars(pending_line, text_rules) > line_width_limit:
        split_position = _find_preferred_split_position(pending_line, text_rules)
        if split_position is None:
            split_position = _find_hard_split_position(pending_line, text_rules)

        if split_position is None or split_position <= 0 or split_position >= len(pending_line):
            _log_align_warning(
                location_path=location_path,
                line=pending_line,
                reason="无法找到安全切分点，保留模型原行",
                text_rules=text_rules,
            )
            break

        head = pending_line[:split_position].rstrip()
        tail = pending_line[split_position:].lstrip()
        if not head or not tail:
            _log_align_warning(
                location_path=location_path,
                line=pending_line,
                reason="切分后出现空片段，保留模型原行",
                text_rules=text_rules,
            )
            break

        result.append(head)
        pending_line = tail

    result.append(pending_line)
    return result


def _align_lines(
    text: str,
    target_lines: int,
    *,
    location_path: str | None,
    text_rules: TextRules,
) -> list[str]:
    """按模型断句做行数适配，再执行行宽兜底。"""
    normalized_target_lines = max(1, target_lines)
    if not text:
        return [""] * normalized_target_lines

    lines = text.split("\n")

    if len(lines) > normalized_target_lines:
        keep_lines = lines[: max(normalized_target_lines - 1, 0)]
        merged_tail = " ".join(lines[max(normalized_target_lines - 1, 0) :])
        keep_lines.append(merged_tail)
        lines = keep_lines

    if len(lines) < normalized_target_lines:
        lines.extend([""] * (normalized_target_lines - len(lines)))

    return _split_overwide_lines(
        lines=lines,
        location_path=location_path,
        text_rules=text_rules,
    )


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
                    TranslationErrorItem(
                        location_path=item.location_path,
                        item_type=item.item_type,
                        role=item.role,
                        original_lines=list(item.original_lines),
                        translation_lines=list(translation_lines),
                        error_type=ERR_PLACEHOLDER_MISMATCH,
                        error_detail=[f"选项行数不匹配: 期望 {len(item.original_lines)} 行, 实际 {len(translation_lines)} 行"],
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
                )
            )
            continue

        right_items.append(item)

    if right_items:
        await right_queue.put(right_items)
    if error_items:
        await error_queue.put(error_items)


__all__: list[str] = ["verify_translation_batch"]
