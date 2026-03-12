"""
正文翻译校验模块。

负责承接正文翻译结果的解析与校验流程。

这一层会把模型返回的 JSON 结果映射回对应的 `TranslationItem`，
并使用 `TranslationItem` 自带的占位符校验、控制符恢复和日文残留检查能力，
再把正确结果与错误结果分别推入对应队列。
"""

import asyncio
import json

from json_repair import repair_json
from pydantic import RootModel

from app.models.schemas import ErrorType, TranslationErrorItem, TranslationItem


ERR_MISSING_KEY: ErrorType = "AI漏翻"
ERR_PLACEHOLDER_MISMATCH: ErrorType = "控制符不匹配"
ERR_JAPANESE_RESIDUAL: ErrorType = "日文残留"


class TranslationResponse(RootModel[dict[str, str]]):
    """正文翻译返回结果模型。"""


def _align_lines(text: str, target_lines: int) -> list[str]:
    """
    将从 LLM 解析出的大段文本严格按原内容的行数要求进行对齐。

    在 RPG Maker 的事件指令（长文本）中，对话的气泡显示是强依赖行数控制的。
    大模型可能会自作主张地增加或减少换行，此函数的作用就是兜底进行强制收敛。

    对齐规则：
    1. 如果 LLM 给了过多行，多出来的部分会被硬组合到最后一行（用空格分隔），以免丢字。
    2. 如果 LLM 给了过少行，直接在尾部补充空字符串，保持整体数组长度恒定。
    3. 如果返回了完全空的内容，也会至少填补到目标长度。

    Args:
        text: 大模型返回并被取出对应的纯翻译文本。
        target_lines: `original_lines` 的数组长度，即预期要输出的数组长度。

    Returns:
        长度刚好等于 target_lines 的列表，可直接赋给 `translation_lines_with_placeholders`。
    """
    if not text:
        return [""] * max(1, target_lines)

    lines: list[str] = text.split("\n")
    current_count: int = len(lines)

    if current_count > target_lines:
        keep_lines: list[str] = lines[: max(target_lines - 1, 0)]
        merged_tail: str = " ".join(lines[max(target_lines - 1, 0) :])
        keep_lines.append(merged_tail)
        return keep_lines

    if current_count < target_lines:
        lines.extend([""] * (target_lines - current_count))

    return lines


async def verify_translation_batch(
    ai_result: str,
    items: list[TranslationItem],
    right_queue: asyncio.Queue[list[TranslationItem] | None],
    error_queue: asyncio.Queue[list[TranslationErrorItem] | None],
) -> None:
    """
    承接大模型返回的原始字符串，对其进行反序列化、校验和错误分流。

    该函数会在 TextTranslation 的 Worker 中被调用。由于一次请求打包了多个 `TranslationItem`，
    所以即使解析 JSON 成功，也要分别去验证每一个小条目的译文质量（如漏翻、占位符是否缺失、是否有日文残留等）。

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
            item.check_residual()
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
    return None


__all__: list[str] = ["verify_translation_batch"]