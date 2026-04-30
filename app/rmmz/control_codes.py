"""RMMZ 控制符占位符协议。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Self


type PlaceholderSource = Literal["standard", "custom"]


@dataclass(frozen=True, slots=True)
class ControlSequenceSpan:
    """文本中需要保护的控制符或自定义片段。"""

    start_index: int
    end_index: int
    original: str
    source: PlaceholderSource
    placeholder: str | None
    custom_template: str | None
    priority: int


@dataclass(frozen=True, slots=True)
class CustomPlaceholderRule:
    """外部 JSON 提供的自定义正则占位符规则。"""

    pattern_text: str
    placeholder_template: str
    pattern: re.Pattern[str]

    @classmethod
    def create(cls, pattern_text: str, placeholder_template: str) -> Self:
        """编译并校验单条自定义占位符规则。"""
        if not pattern_text.strip():
            raise ValueError("自定义占位符规则的正则表达式不能为空")
        if not placeholder_template.strip():
            raise ValueError("自定义占位符规则的占位符模板不能为空")

        try:
            pattern = re.compile(pattern_text)
        except re.error as error:
            raise ValueError(f"自定义占位符正则无效: {pattern_text}") from error

        if pattern.search("") is not None:
            raise ValueError(f"自定义占位符正则不能匹配空字符串: {pattern_text}")

        preview = format_placeholder_template(
            template=placeholder_template,
            code="",
            param="",
            index=1,
        )
        if STANDARD_PLACEHOLDER_PATTERN.fullmatch(preview) is not None:
            raise ValueError(
                f"自定义占位符模板不能生成 RMMZ 标准占位符: {placeholder_template}"
            )
        if CUSTOM_PLACEHOLDER_PATTERN.fullmatch(preview) is None:
            raise ValueError(
                "自定义占位符模板必须生成形如 [CUSTOM_NAME_1] 的方括号占位符"
            )

        return cls(
            pattern_text=pattern_text,
            placeholder_template=placeholder_template,
            pattern=pattern,
        )


INDEXED_STANDARD_CODES: frozenset[str] = frozenset(
    {"V", "N", "P", "C", "I", "PX", "PY", "FS"}
)
NO_PARAM_STANDARD_PLACEHOLDERS: dict[str, str] = {
    "G": "[RMMZ_G]",
}
SYMBOL_STANDARD_PLACEHOLDERS: dict[str, str] = {
    "{": "[RMMZ_FONT_LARGER]",
    "}": "[RMMZ_FONT_SMALLER]",
    "\\": "[RMMZ_BACKSLASH]",
    "$": "[RMMZ_GOLD_WINDOW]",
    ".": "[RMMZ_WAIT_SHORT]",
    "|": "[RMMZ_WAIT_LONG]",
    "!": "[RMMZ_WAIT_INPUT]",
    ">": "[RMMZ_INSTANT_ON]",
    "<": "[RMMZ_INSTANT_OFF]",
    "^": "[RMMZ_NO_WAIT]",
}

INDEXED_STANDARD_CONTROL_PATTERN: re.Pattern[str] = re.compile(
    r"\\(?P<code>V|N|P|C|I|PX|PY|FS)\[(?P<param>\d+)\]",
    re.IGNORECASE,
)
NO_PARAM_STANDARD_CONTROL_PATTERN: re.Pattern[str] = re.compile(
    r"\\(?P<code>G)(?![A-Za-z\[])",
    re.IGNORECASE,
)
SYMBOL_STANDARD_CONTROL_PATTERN: re.Pattern[str] = re.compile(
    r"\\(?P<symbol>[{}\\$.\|!><^])"
)
TERMS_PERCENT_PLACEHOLDER_PATTERN: re.Pattern[str] = re.compile(
    r"%(?P<param>\d+)"
)
STANDARD_PLACEHOLDER_PATTERN: re.Pattern[str] = re.compile(
    "|".join(
        (
            r"\[RMMZ_(?:V|N|P|C|I|PX|PY|FS)_\d+\]",
            r"\[RMMZ_PERCENT_\d+\]",
            *(
                re.escape(placeholder)
                for placeholder in [
                    *NO_PARAM_STANDARD_PLACEHOLDERS.values(),
                    *SYMBOL_STANDARD_PLACEHOLDERS.values(),
                ]
            ),
        )
    ),
    re.IGNORECASE,
)
CUSTOM_PLACEHOLDER_PATTERN: re.Pattern[str] = re.compile(
    r"\[CUSTOM_[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_\d+\]",
    re.IGNORECASE,
)
ALL_PLACEHOLDER_PATTERN: re.Pattern[str] = re.compile(
    f"(?:{STANDARD_PLACEHOLDER_PATTERN.pattern})|(?:{CUSTOM_PLACEHOLDER_PATTERN.pattern})",
    re.IGNORECASE,
)


def iter_standard_control_spans(text: str) -> list[ControlSequenceSpan]:
    """扫描 RMMZ 标准控制符和数据库消息占位符。"""
    spans: list[ControlSequenceSpan] = []
    spans.extend(_iter_indexed_standard_control_spans(text))
    spans.extend(_iter_no_param_standard_control_spans(text))
    spans.extend(_iter_symbol_standard_control_spans(text))
    spans.extend(_iter_terms_percent_spans(text))
    return spans


def select_non_overlapping_spans(
    spans: list[ControlSequenceSpan],
) -> list[ControlSequenceSpan]:
    """按位置、优先级和长度选择不重叠的保护片段。"""
    sorted_spans = sorted(
        spans,
        key=lambda span: (
            span.start_index,
            -span.priority,
            -(span.end_index - span.start_index),
        ),
    )
    selected_spans: list[ControlSequenceSpan] = []
    protected_until = -1
    for span in sorted_spans:
        if span.start_index < protected_until:
            continue
        selected_spans.append(span)
        protected_until = span.end_index
    return selected_spans


def format_placeholder_template(
    *,
    template: str,
    code: str,
    param: str,
    index: int,
) -> str:
    """使用统一变量格式化占位符模板。"""
    try:
        return template.format(code=code, param=param, index=index)
    except (IndexError, KeyError, ValueError) as error:
        raise ValueError(
            f"占位符模板格式无效，仅支持 code、param、index 变量: {template}"
        ) from error


def _iter_indexed_standard_control_spans(text: str) -> list[ControlSequenceSpan]:
    """扫描带数字参数的 RMMZ 标准控制符。"""
    spans: list[ControlSequenceSpan] = []
    for match in INDEXED_STANDARD_CONTROL_PATTERN.finditer(text):
        code = match.group("code").upper()
        param = match.group("param")
        placeholder = f"[RMMZ_{code}_{param}]"
        spans.append(
            ControlSequenceSpan(
                start_index=match.start(),
                end_index=match.end(),
                original=match.group(0),
                source="standard",
                placeholder=placeholder,
                custom_template=None,
                priority=0,
            )
        )
    return spans


def _iter_no_param_standard_control_spans(text: str) -> list[ControlSequenceSpan]:
    """扫描不带参数的 RMMZ 标准字母控制符。"""
    spans: list[ControlSequenceSpan] = []
    for match in NO_PARAM_STANDARD_CONTROL_PATTERN.finditer(text):
        code = match.group("code").upper()
        placeholder = NO_PARAM_STANDARD_PLACEHOLDERS[code]
        spans.append(
            ControlSequenceSpan(
                start_index=match.start(),
                end_index=match.end(),
                original=match.group(0),
                source="standard",
                placeholder=placeholder,
                custom_template=None,
                priority=0,
            )
        )
    return spans


def _iter_symbol_standard_control_spans(text: str) -> list[ControlSequenceSpan]:
    """扫描 RMMZ 标准符号控制符。"""
    spans: list[ControlSequenceSpan] = []
    for match in SYMBOL_STANDARD_CONTROL_PATTERN.finditer(text):
        symbol = match.group("symbol")
        placeholder = SYMBOL_STANDARD_PLACEHOLDERS[symbol]
        spans.append(
            ControlSequenceSpan(
                start_index=match.start(),
                end_index=match.end(),
                original=match.group(0),
                source="standard",
                placeholder=placeholder,
                custom_template=None,
                priority=0,
            )
        )
    return spans


def _iter_terms_percent_spans(text: str) -> list[ControlSequenceSpan]:
    """扫描 RMMZ 数据库消息中的百分号占位符。"""
    spans: list[ControlSequenceSpan] = []
    for match in TERMS_PERCENT_PLACEHOLDER_PATTERN.finditer(text):
        param = match.group("param")
        spans.append(
            ControlSequenceSpan(
                start_index=match.start(),
                end_index=match.end(),
                original=match.group(0),
                source="standard",
                placeholder=f"[RMMZ_PERCENT_{param}]",
                custom_template=None,
                priority=0,
            )
        )
    return spans


__all__: list[str] = [
    "ALL_PLACEHOLDER_PATTERN",
    "CUSTOM_PLACEHOLDER_PATTERN",
    "ControlSequenceSpan",
    "CustomPlaceholderRule",
    "INDEXED_STANDARD_CODES",
    "STANDARD_PLACEHOLDER_PATTERN",
    "SYMBOL_STANDARD_PLACEHOLDERS",
    "format_placeholder_template",
    "iter_standard_control_spans",
    "select_non_overlapping_spans",
]
