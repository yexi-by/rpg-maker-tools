"""写进游戏文件前的译文整理。"""

from app.rmmz.schema import TranslationItem
from app.rmmz.text_layout import normalize_translated_wrapping_punctuation, split_overwide_lines
from app.rmmz.text_rules import TextRules


def prepare_long_text_write_lines(
    *,
    item: TranslationItem,
    text_rules: TextRules | None,
) -> list[str]:
    """在写入前按当前配置再次执行长文本行宽兜底。"""
    if text_rules is None:
        return strip_trailing_empty_lines(
            normalize_translation_lines_for_write(
                lines=item.translation_lines,
                text_rules=None,
            )
        )
    translation_lines = prepare_text_write_lines(item=item, text_rules=text_rules)
    return strip_trailing_empty_lines(
        split_overwide_lines(
            lines=translation_lines,
            location_path=item.location_path,
            text_rules=text_rules,
        )
    )


def prepare_text_write_lines(
    *,
    item: TranslationItem,
    text_rules: TextRules | None,
) -> list[str]:
    """写入前修复源文外层包裹标点被模型改写的译文。"""
    translation_lines = normalize_translation_lines_for_write(
        lines=item.translation_lines,
        text_rules=text_rules,
    )
    if text_rules is None:
        return translation_lines
    return normalize_translated_wrapping_punctuation(
        original_lines=item.original_lines,
        translation_lines=translation_lines,
        text_rules=text_rules,
    )


def prepare_single_text_write_value(
    *,
    item: TranslationItem,
    text_rules: TextRules | None,
) -> str:
    """读取单值文本写入内容，并套用外层包裹标点修复。"""
    translation_lines = prepare_text_write_lines(item=item, text_rules=text_rules)
    return translation_lines[0] if translation_lines else ""


def strip_trailing_empty_lines(lines: list[str]) -> list[str]:
    """删除长文本尾部空行，保留中间空行。"""
    stripped_lines = list(lines)
    while stripped_lines and not stripped_lines[-1]:
        _ = stripped_lines.pop()
    return stripped_lines


def normalize_translation_lines_for_write(
    *,
    lines: list[str],
    text_rules: TextRules | None,
) -> list[str]:
    """写入前清理译文首尾空白，避免旧记录或外部写入污染游戏显示。"""
    if text_rules is None:
        return [line.strip() for line in lines]
    return text_rules.normalize_translation_lines(lines)
