"""译文内部占位符防漏写保护。"""

from app.rmmz.text_rules import TextRules, get_default_text_rules


def collect_internal_placeholder_tokens(
    *,
    lines: list[str],
    text_rules: TextRules | None = None,
) -> set[str]:
    """收集不应进入游戏文件的项目内部占位符。"""
    rules = text_rules or get_default_text_rules()
    return rules.collect_placeholder_tokens(lines)


def ensure_no_internal_placeholder_tokens(
    *,
    lines: list[str],
    context: str,
    text_rules: TextRules | None = None,
) -> None:
    """写回前确认译文不包含项目内部占位符。"""
    tokens = collect_internal_placeholder_tokens(lines=lines, text_rules=text_rules)
    if not tokens:
        return
    joined_tokens = "、".join(sorted(tokens))
    raise ValueError(f"{context} 译文残留项目内部占位符，不能写进游戏文件: {joined_tokens}")


__all__: list[str] = [
    "collect_internal_placeholder_tokens",
    "ensure_no_internal_placeholder_tokens",
]
