"""
正文翻译校验模块。

负责承接正文翻译结果的解析与校验流程。

这一层会把模型返回的 JSON 结果映射回对应的 `TranslationItem`，
并使用 `TranslationItem` 自带的占位符校验、控制符恢复和源语言残留检查能力，
再把正确结果与错误结果分别推入对应队列。
"""

import asyncio
import json

from json_repair import repair_json
from pydantic import RootModel

from app.models.schemas import (
    ErrorType,
    SourceLanguage,
    TranslationErrorItem,
    TranslationItem,
)
from app.utils import check_source_language_residual, logger


ERR_MISSING_KEY: ErrorType = "AI漏翻"
ERR_PLACEHOLDER_MISMATCH: ErrorType = "控制符不匹配"
ERR_SOURCE_LANGUAGE_RESIDUAL: ErrorType = "源语言残留"
LONG_TEXT_LINE_LIMIT: int = 20
LINE_SPLIT_PUNCTUATIONS: tuple[str, ...] = ("，", "。", ",", ".")


class TranslationResponse(RootModel[dict[str, str]]):
    """正文翻译返回结果模型。"""


def _build_warning_preview(text: str, max_length: int = 40) -> str:
    """
    生成日志预览文本，避免把整段长文本直接打进告警日志。

    Args:
        text: 需要截断预览的原始文本。
        max_length: 预览最大字符数。

    Returns:
        适合写入日志的简短预览字符串。
    """
    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}..."


def _find_preferred_split_position(text: str) -> int | None:
    """
    在单行上限内寻找优先切分点。

    这里只允许在逗号或句号后切行，目的不是追求最漂亮的排版，
    而是避免把 `[N_1]`、`[RM_1]` 这类占位符从中间切断。

    Args:
        text: 待切分的单行文本。

    Returns:
        合适的切分位置；如果前 `LONG_TEXT_LINE_LIMIT` 个字符内没有可用标点则返回 `None`。
    """
    best_index: int = -1
    for punctuation in LINE_SPLIT_PUNCTUATIONS:
        candidate_index: int = text.rfind(
            punctuation, 0, LONG_TEXT_LINE_LIMIT
        )
        if candidate_index > best_index:
            best_index = candidate_index

    if best_index < 0:
        return None
    return best_index + 1


def _log_align_warning(
    *,
    location_path: str | None,
    line: str,
    reason: str,
) -> None:
    """
    记录长文本自动补切行失败的告警日志。

    Args:
        location_path: 当前翻译条目的路径，用于日志定位。
        line: 未能安全处理的文本行。
        reason: 具体告警原因。
    """
    logger.warning(
        "长文本自动补切行告警: 路径={}，行长={}，上限={}，原因={}，内容预览={}",
        location_path or "<unknown>",
        len(line),
        LONG_TEXT_LINE_LIMIT,
        reason,
        _build_warning_preview(line),
    )


def _expand_long_lines(
    *,
    lines: list[str],
    target_lines: int,
    location_path: str | None,
) -> list[str]:
    """
    在还有空余行数时，尝试把过长文本按逗号或句号安全切开。

    Args:
        lines: 当前已经按 LLM 原始换行拆开的行列表。
        target_lines: 目标行数上限。
        location_path: 当前条目的路径，用于告警定位。

    Returns:
        已尽力补切分后的行列表。
    """
    expanded_lines: list[str] = []
    remaining_extra_lines: int = max(target_lines - len(lines), 0)

    for line in lines:
        pending_line: str = line
        while (
            len(pending_line) > LONG_TEXT_LINE_LIMIT
            and remaining_extra_lines > 0
        ):
            split_position: int | None = _find_preferred_split_position(
                pending_line
            )
            if split_position is None:
                _log_align_warning(
                    location_path=location_path,
                    line=pending_line,
                    reason="前 20 个字符内没有逗号或句号，无法安全补切分",
                )
                break

            head: str = pending_line[:split_position].rstrip()
            tail: str = pending_line[split_position:].lstrip()
            if not tail:
                break

            expanded_lines.append(head)
            pending_line = tail
            remaining_extra_lines -= 1

        if (
            len(pending_line) > LONG_TEXT_LINE_LIMIT
            and remaining_extra_lines <= 0
        ):
            _log_align_warning(
                location_path=location_path,
                line=pending_line,
                reason="建议行数已用尽，无法继续补切分",
            )

        expanded_lines.append(pending_line)

    return expanded_lines


def _align_lines(
    text: str,
    target_lines: int,
    *,
    location_path: str | None = None,
) -> list[str]:
    """
    将从 LLM 解析出的大段文本严格按原内容的行数要求进行对齐。

    在 RPG Maker 的事件指令（长文本）中，对话的气泡显示是强依赖行数控制的。
    大模型可能会自作主张地增加或减少换行，此函数的作用就是兜底进行强制收敛。

    对齐规则：
    1. 如果 LLM 给了过多行，多出来的部分会被硬组合到最后一行（用空格分隔），以免丢字。
    2. 如果 LLM 给了过少行，且某一行超过 20 字，会优先尝试按逗号或句号补切分到空余行。
    3. 如果超过 20 字但没有可用标点，或目标行数已被用尽，会记录 warning，避免静默溢出。
    4. 如果仍然少于目标行数，会在尾部补充空字符串，保持整体数组长度恒定。
    5. 如果返回了完全空的内容，也会至少填补到目标长度。

    Args:
        text: 大模型返回并被取出对应的纯翻译文本。
        target_lines: `original_lines` 的数组长度，即预期要输出的数组长度。
        location_path: 当前翻译条目的路径，用于日志定位。

    Returns:
        长度刚好等于 target_lines 的列表，可直接赋给 `translation_lines_with_placeholders`。
    """
    normalized_target_lines: int = max(1, target_lines)
    if not text:
        return [""] * normalized_target_lines

    lines: list[str] = text.split("\n")
    current_count: int = len(lines)

    if current_count > normalized_target_lines:
        keep_lines: list[str] = lines[: max(normalized_target_lines - 1, 0)]
        merged_tail: str = " ".join(
            lines[max(normalized_target_lines - 1, 0) :]
        )
        keep_lines.append(merged_tail)
        lines = keep_lines

    if current_count < normalized_target_lines:
        lines = _expand_long_lines(
            lines=lines,
            target_lines=normalized_target_lines,
            location_path=location_path,
        )
    else:
        for line in lines:
            if len(line) > LONG_TEXT_LINE_LIMIT:
                _log_align_warning(
                    location_path=location_path,
                    line=line,
                    reason="当前结果已经没有多余空行可用于补切分",
                )

    if len(lines) < normalized_target_lines:
        lines.extend([""] * (normalized_target_lines - len(lines)))

    return lines


async def verify_translation_batch(
    ai_result: str,
    items: list[TranslationItem],
    right_queue: asyncio.Queue[list[TranslationItem] | None],
    error_queue: asyncio.Queue[list[TranslationErrorItem] | None],
    source_language: SourceLanguage,
) -> None:
    """
    承接大模型返回的原始字符串，对其进行反序列化、校验和错误分流。

    该函数会在 TextTranslation 的 Worker 中被调用。由于一次请求打包了多个 `TranslationItem`，
    所以即使解析 JSON 成功，也要分别去验证每一个小条目的译文质量（如漏翻、占位符是否缺失、是否有源语言残留等）。

    核心流程：
    1. 使用 `json_repair` 尽可能从模型可能夹杂 Markdown 标记或残缺的废话里把 JSON 字典捞出来。
    2. 如果连 JSON 都构不成，直接把这批所有的 `items` 标记为 `AI漏翻` 全部塞进错误队列。
    3. 针对提取成功的 JSON，按 `location_path` 与原本的 `items` 进行一一对应匹配。
    4. 分别进行行数对齐、占位符总量校验以及底层 RM 控制符的恢复。
    5. 最后执行日文未翻译残留扫描。如果发现有日文，抛入错误队列以便后续错误表重译。
    6. 将全部校验通过的条目放入 `right_queue`。

    Args:
        ai_result: 模型输出的不确定文本。
        items: 原本组织给这个批次的翻译项列表，作为校验的基准锚点。
        right_queue: 用于回写到主翻译表的成果队列。
        error_queue: 用于落库到专属错误记录表的错误队列。
        source_language: 当前游戏的源语言。
    """
    right_items: list[TranslationItem] = []
    error_items: list[TranslationErrorItem] = []

    try:
        repaired_result = repair_json(ai_result, return_objects=False)
        if isinstance(repaired_result, tuple):
            repaired_value = repaired_result[0]
        else:
            repaired_value = repaired_result

        if isinstance(repaired_value, str):
            clean_result: str = repaired_value
        else:
            clean_result = json.dumps(repaired_value, ensure_ascii=False)

        translation_map: dict[str, str] = TranslationResponse.model_validate_json(
            clean_result
        ).root
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
                    [
                        "模型返回无法解析为 JSON 对象",
                        f"详细错误: {error}",
                    ],
                )
            )
        if error_items:
            await error_queue.put(error_items)
        return None

    for item in items:
        translation_text: str | None = translation_map.get(item.location_path)
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
            translation_lines: list[str] = _align_lines(
                text=translation_text,
                target_lines=len(item.original_lines),
                location_path=item.location_path,
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
                        [
                            f"选项行数不匹配: 期望 {len(item.original_lines)} 行, 实际 {len(translation_lines)} 行"
                        ],
                    )
                )
                continue
        else:
            translation_lines = [translation_text]

        item.translation_lines_with_placeholders = list(translation_lines)
        item.translation_lines = []

        try:
            item.verify_placeholders()
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
            check_source_language_residual(
                translation_lines=item.translation_lines,
                source_language=source_language,
            )
        except ValueError as error:
            error_items.append(
                (
                    item.location_path,
                    item.item_type,
                    item.role,
                    list(item.original_lines),
                    list(item.translation_lines),
                    ERR_SOURCE_LANGUAGE_RESIDUAL,
                    [str(error)],
                )
            )
            continue

        right_items.append(item)

    if right_items:
        await right_queue.put(right_items)
    if error_items:
        await error_queue.put(error_items)
    return None


__all__: list[str] = ["verify_translation_batch"]
