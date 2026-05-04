"""正文译文结构一致性校验。"""

from __future__ import annotations

from app.rmmz.control_codes import LITERAL_LINE_BREAK_MARKER, LITERAL_LINE_BREAK_PLACEHOLDER
from app.rmmz.schema import TranslationItem

EXPLANATION_PREFIXES: tuple[str, ...] = (
    "译文：",
    "译文:",
    "翻译：",
    "翻译:",
)
EXPLANATION_MARKERS: tuple[str, ...] = (
    "以下是翻译",
)
PROTOCOL_FIELD_PREFIXES: tuple[str, ...] = (
    "id:",
    "id：",
    '"id":',
    "source_lines:",
    "source_lines：",
    '"source_lines":',
    "translation_lines:",
    "translation_lines：",
    '"translation_lines":',
)


def validate_translation_text_structure(
    *,
    item: TranslationItem,
    translation_lines: list[str],
    translation_lines_with_placeholders: list[str] | None = None,
) -> None:
    """校验译文没有改动单字段结构，也没有混入模型输出协议文本。"""
    errors = collect_translation_text_structure_errors(
        item=item,
        translation_lines=translation_lines,
        translation_lines_with_placeholders=translation_lines_with_placeholders,
    )
    if errors:
        raise ValueError(";\n".join(errors))


def collect_translation_text_structure_errors(
    *,
    item: TranslationItem,
    translation_lines: list[str],
    translation_lines_with_placeholders: list[str] | None = None,
) -> list[str]:
    """收集译文结构错误，调用方决定是否作为业务失败处理。"""
    errors = _collect_artifact_errors(item=item, translation_lines=translation_lines)
    if item.item_type != "short_text":
        return errors

    if len(translation_lines) != 1:
        errors.append(f"单字段文本必须只提供 1 条中文译文行，当前提供 {len(translation_lines)} 条")
        return errors

    original_real_break_count = _count_real_line_breaks(item.original_lines)
    translation_real_break_count = _count_real_line_breaks(translation_lines)
    if original_real_break_count != translation_real_break_count:
        errors.append(
            f"译文真实换行数量不一致（原文 {original_real_break_count} 个，译文 {translation_real_break_count} 个）"
        )

    placeholder_lines = translation_lines_with_placeholders or translation_lines
    original_literal_break_count = _count_literal_line_breaks(item.original_lines_with_placeholders or item.original_lines)
    translation_literal_break_count = _count_literal_line_breaks(placeholder_lines)
    if original_literal_break_count != translation_literal_break_count:
        errors.append(
            f"译文字面量换行标记数量不一致（原文 {original_literal_break_count} 个，译文 {translation_literal_break_count} 个）"
        )
    return errors


def _collect_artifact_errors(*, item: TranslationItem, translation_lines: list[str]) -> list[str]:
    """收集模型解释文本、协议字段和内部定位泄漏。"""
    errors: list[str] = []
    joined_text = "\n".join(translation_lines)
    if item.location_path and item.location_path in joined_text:
        errors.append("译文包含文本在游戏里的内部位置，不能写进游戏文件")

    for line in translation_lines:
        stripped_line = line.strip()
        lowered_line = stripped_line.lower()
        if any(stripped_line.startswith(prefix) for prefix in EXPLANATION_PREFIXES):
            errors.append("译文包含明显解释性前缀，不是可写入游戏的正文")
            break
        if any(marker in stripped_line for marker in EXPLANATION_MARKERS):
            errors.append("译文包含明显解释性说明，不是可写入游戏的正文")
            break
        if any(lowered_line.startswith(prefix) for prefix in PROTOCOL_FIELD_PREFIXES):
            errors.append("译文包含模型输出协议字段，不是可写入游戏的正文")
            break
    return errors


def _count_real_line_breaks(lines: list[str]) -> int:
    """统计字段内容中的真实换行数量。"""
    if not lines:
        return 0
    return "\n".join(lines).count("\n")


def _count_literal_line_breaks(lines: list[str]) -> int:
    """统计字段内容中的字面量换行标记数量。"""
    return sum(
        line.count(LITERAL_LINE_BREAK_MARKER) + line.count(LITERAL_LINE_BREAK_PLACEHOLDER)
        for line in lines
    )


__all__: list[str] = [
    "collect_translation_text_structure_errors",
    "validate_translation_text_structure",
]
