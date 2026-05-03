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
class RawControlSequenceCandidate:
    """尚未被标准或自定义规则覆盖的反斜杠控制符候选。"""

    start_index: int
    end_index: int
    original: str


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
                f"自定义占位符模板必须生成形如 [CUSTOM_NAME_1] 的方括号占位符，当前生成: {preview}"
            )

        return cls(
            pattern_text=pattern_text,
            placeholder_template=placeholder_template,
            pattern=pattern,
        )


INDEXED_STANDARD_CODE_NAMES: dict[str, str] = {
    "V": "VARIABLE",
    "N": "ACTOR_NAME",
    "P": "PARTY_MEMBER_NAME",
    "C": "TEXT_COLOR",
    "I": "ICON",
    "PX": "TEXT_X_POSITION",
    "PY": "TEXT_Y_POSITION",
    "FS": "FONT_SIZE",
}
INDEXED_STANDARD_CODES: frozenset[str] = frozenset(INDEXED_STANDARD_CODE_NAMES)
NO_PARAM_STANDARD_PLACEHOLDERS: dict[str, str] = {
    "G": "[RMMZ_CURRENCY_UNIT]",
}
SYMBOL_STANDARD_PLACEHOLDERS: dict[str, str] = {
    "{": "[RMMZ_FONT_LARGER]",
    "}": "[RMMZ_FONT_SMALLER]",
    "\\": "[RMMZ_BACKSLASH]",
    "$": "[RMMZ_SHOW_GOLD_WINDOW]",
    ".": "[RMMZ_WAIT_SHORT]",
    "|": "[RMMZ_WAIT_LONG]",
    "!": "[RMMZ_WAIT_INPUT]",
    ">": "[RMMZ_INSTANT_TEXT_ON]",
    "<": "[RMMZ_INSTANT_TEXT_OFF]",
    "^": "[RMMZ_NO_WAIT]",
}
LITERAL_LINE_BREAK_MARKER = r"\n"
LITERAL_LINE_BREAK_PLACEHOLDER = "[RMMZ_LITERAL_LINE_BREAK]"
LITERAL_ESCAPE_PLACEHOLDERS: dict[str, str] = {
    "\\\"": "[RMMZ_LITERAL_DOUBLE_QUOTE]",
    "\\'": "[RMMZ_LITERAL_SINGLE_QUOTE]",
    r"\/": "[RMMZ_LITERAL_SLASH]",
    r"\?": "[RMMZ_LITERAL_QUESTION_MARK]",
    r"\a": "[RMMZ_LITERAL_BELL]",
    r"\b": "[RMMZ_LITERAL_BACKSPACE]",
    r"\f": "[RMMZ_LITERAL_FORM_FEED]",
    r"\n": LITERAL_LINE_BREAK_PLACEHOLDER,
    r"\r": "[RMMZ_LITERAL_CARRIAGE_RETURN]",
    r"\t": "[RMMZ_LITERAL_TAB]",
    r"\v": "[RMMZ_LITERAL_VERTICAL_TAB]",
}
LITERAL_DYNAMIC_ESCAPE_PATTERNS: dict[str, re.Pattern[str]] = {
    "UNICODE": re.compile(r"\\(?:u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8})"),
    "HEX": re.compile(r"\\x[0-9A-Fa-f]{2}"),
    "OCTAL": re.compile(r"\\[0-7]{1,3}(?!\[)"),
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
LITERAL_ESCAPE_PATTERN: re.Pattern[str] = re.compile(
    "|".join(re.escape(marker) for marker in LITERAL_ESCAPE_PLACEHOLDERS)
)
STANDARD_PLACEHOLDER_PATTERN: re.Pattern[str] = re.compile(
    "|".join(
        (
            (
                r"\[RMMZ_(?:"
                + "|".join(re.escape(name) for name in INDEXED_STANDARD_CODE_NAMES.values())
                + r")_\d+\]"
            ),
            r"\[RMMZ_MESSAGE_ARGUMENT_\d+\]",
            r"\[RMMZ_LITERAL_(?:UNICODE|HEX|OCTAL)_ESCAPE_[0-9A-F]+\]",
            *(re.escape(placeholder) for placeholder in LITERAL_ESCAPE_PLACEHOLDERS.values()),
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
RAW_CONTROL_SEQUENCE_CANDIDATE_PATTERN: re.Pattern[str] = re.compile(
    r"\\[A-Za-z]+\d*\[[A-Za-z0-9_./:-]{1,32}[^\]\w\s\[\]\\]"
    + r"|\\[A-Za-z]+\d*(?:\[[^\]\r\n]{0,64}\])?"
    + r"|\\[{}\\$.\|!><^]"
)


def iter_standard_control_spans(text: str) -> list[ControlSequenceSpan]:
    """扫描 RMMZ 标准控制符和数据库消息占位符。"""
    spans: list[ControlSequenceSpan] = []
    spans.extend(_iter_indexed_standard_control_spans(text))
    spans.extend(_iter_no_param_standard_control_spans(text))
    spans.extend(_iter_symbol_standard_control_spans(text))
    spans.extend(_iter_terms_percent_spans(text))
    spans.extend(_iter_literal_escape_spans(text))
    return spans


def iter_raw_control_sequence_candidates(text: str) -> list[RawControlSequenceCandidate]:
    """扫描所有形似 RPG Maker 控制符的原始反斜杠片段。"""
    candidates: list[RawControlSequenceCandidate] = []
    for match in RAW_CONTROL_SEQUENCE_CANDIDATE_PATTERN.finditer(text):
        candidates.append(
            RawControlSequenceCandidate(
                start_index=match.start(),
                end_index=match.end(),
                original=match.group(0),
            )
        )
    return candidates


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
        placeholder = f"[RMMZ_{INDEXED_STANDARD_CODE_NAMES[code]}_{param}]"
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
                placeholder=f"[RMMZ_MESSAGE_ARGUMENT_{param}]",
                custom_template=None,
                priority=0,
            )
        )
    return spans


def _format_literal_dynamic_escape_placeholder(*, escape_name: str, original: str) -> str:
    """用原始转义文本生成可精确还原的稳定占位符。"""
    encoded_original = original.encode("utf-8").hex().upper()
    return f"[RMMZ_LITERAL_{escape_name}_ESCAPE_{encoded_original}]"


def _iter_literal_escape_spans(text: str) -> list[ControlSequenceSpan]:
    """扫描插件和 Note 文本中的字面量反斜杠转义片段。"""
    spans: list[ControlSequenceSpan] = []
    for match in LITERAL_ESCAPE_PATTERN.finditer(text):
        original = match.group(0)
        spans.append(
            ControlSequenceSpan(
                start_index=match.start(),
                end_index=match.end(),
                original=original,
                source="standard",
                placeholder=LITERAL_ESCAPE_PLACEHOLDERS[original],
                custom_template=None,
                priority=0,
            )
        )
    for escape_name, pattern in LITERAL_DYNAMIC_ESCAPE_PATTERNS.items():
        for match in pattern.finditer(text):
            spans.append(
                ControlSequenceSpan(
                    start_index=match.start(),
                    end_index=match.end(),
                    original=match.group(0),
                    source="standard",
                    placeholder=_format_literal_dynamic_escape_placeholder(
                        escape_name=escape_name,
                        original=match.group(0),
                    ),
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
    "LITERAL_DYNAMIC_ESCAPE_PATTERNS",
    "LITERAL_ESCAPE_PLACEHOLDERS",
    "LITERAL_LINE_BREAK_MARKER",
    "LITERAL_LINE_BREAK_PLACEHOLDER",
    "RawControlSequenceCandidate",
    "STANDARD_PLACEHOLDER_PATTERN",
    "SYMBOL_STANDARD_PLACEHOLDERS",
    "format_placeholder_template",
    "iter_raw_control_sequence_candidates",
    "iter_standard_control_spans",
    "select_non_overlapping_spans",
]
