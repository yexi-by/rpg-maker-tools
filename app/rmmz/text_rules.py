"""
文本规则服务模块。

本模块把 RMMZ 标准控制符保护、自定义正则占位符、日文残留检查和提取阶段
文本正规化统一收敛到 `TextRules`。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from app.config.schemas import TextRulesSetting
from app.rmmz.control_codes import (
    ALL_PLACEHOLDER_PATTERN,
    ControlSequenceSpan,
    CustomPlaceholderRule,
    format_placeholder_template,
    iter_standard_control_spans,
    select_non_overlapping_spans,
)
from app.rmmz.json_types import (
    JsonArray,
    JsonObject,
    JsonPrimitive,
    JsonValue,
    coerce_json_value,
    ensure_json_array,
    ensure_json_object,
    ensure_json_string_list,
)


@dataclass(frozen=True, slots=True)
class TextRules:
    """运行时文本规则集合。"""

    setting: TextRulesSetting
    custom_placeholder_rules: tuple[CustomPlaceholderRule, ...]
    placeholder_token_pattern: re.Pattern[str]
    source_text_required_pattern: re.Pattern[str]
    japanese_segment_pattern: re.Pattern[str]
    line_width_count_pattern: re.Pattern[str]
    residual_escape_sequence_pattern: re.Pattern[str]

    @classmethod
    def from_setting(
        cls,
        setting: TextRulesSetting,
        custom_placeholder_rules: tuple[CustomPlaceholderRule, ...] = (),
    ) -> "TextRules":
        """根据配置构建并预编译全部正则规则。"""
        return cls(
            setting=setting,
            custom_placeholder_rules=custom_placeholder_rules,
            placeholder_token_pattern=ALL_PLACEHOLDER_PATTERN,
            source_text_required_pattern=re.compile(setting.source_text_required_pattern),
            japanese_segment_pattern=re.compile(setting.japanese_segment_pattern),
            line_width_count_pattern=re.compile(setting.line_width_count_pattern),
            residual_escape_sequence_pattern=re.compile(setting.residual_escape_sequence_pattern),
        )

    def normalize_extraction_text(self, text: str) -> str:
        """按配置清理提取阶段的包裹标点并去除首尾空白。"""
        normalized_text = text.strip()
        for left, right in self.setting.strip_wrapping_punctuation_pairs:
            if normalized_text.startswith(left) and normalized_text.endswith(right):
                normalized_text = normalized_text[len(left) : len(normalized_text) - len(right)]
        return normalized_text.strip()

    def replace_rm_control_sequences(
        self,
        text: str,
        replacer: Callable[[ControlSequenceSpan], str],
    ) -> str:
        """按顺序替换文本中的 RPG Maker 控制符。"""
        spans = self.iter_control_sequence_spans(text)
        if not spans:
            return text

        parts: list[str] = []
        last_end = 0
        for span in spans:
            parts.append(text[last_end:span.start_index])
            parts.append(replacer(span))
            last_end = span.end_index
        parts.append(text[last_end:])
        return "".join(parts)

    def strip_rm_control_sequences(self, text: str) -> str:
        """从文本中剥离 RPG Maker 控制符。"""
        return self.replace_rm_control_sequences(text, lambda _span: "")

    def iter_control_sequence_spans(self, text: str) -> list[ControlSequenceSpan]:
        """顺序扫描一行文本，识别标准控制符和自定义保护片段。"""
        spans = iter_standard_control_spans(text)
        spans.extend(self._iter_custom_placeholder_spans(text))
        return select_non_overlapping_spans(spans)

    def format_custom_placeholder(self, *, template: str, index: int) -> str:
        """按外部 JSON 模板格式化自定义占位符。"""
        return format_placeholder_template(
            template=template,
            code="",
            param="",
            index=index,
        )

    def count_line_width_chars(self, text: str) -> int:
        """按配置统计长文本切行时计入长度的字符数量。"""
        return len(self.line_width_count_pattern.findall(text))

    def should_translate_source_text(self, text: str) -> bool:
        """判断原文是否包含需要交给模型处理的源语言字符。"""
        normalized_text = self.normalize_extraction_text(text)
        if not normalized_text:
            return False
        return self.source_text_required_pattern.search(normalized_text) is not None

    def should_translate_source_lines(self, lines: list[str]) -> bool:
        """判断多行原文是否至少包含一处需要翻译的源语言字符。"""
        return any(self.should_translate_source_text(line) for line in lines)

    def is_line_width_counted_char(self, char: str) -> bool:
        """判断单个字符是否计入长文本切行长度。"""
        return self.line_width_count_pattern.fullmatch(char) is not None

    def collect_placeholder_tokens(self, lines: list[str]) -> set[str]:
        """收集文本行中的翻译占位符集合。"""
        placeholders: set[str] = set()
        for line in lines:
            placeholders.update(self.placeholder_token_pattern.findall(line))
        return placeholders

    def _iter_custom_placeholder_spans(self, text: str) -> list[ControlSequenceSpan]:
        """扫描外部 JSON 中定义的自定义占位符规则。"""
        spans: list[ControlSequenceSpan] = []
        for rule in self.custom_placeholder_rules:
            for match in rule.pattern.finditer(text):
                spans.append(
                    ControlSequenceSpan(
                        start_index=match.start(),
                        end_index=match.end(),
                        original=match.group(0),
                        source="custom",
                        placeholder=None,
                        custom_template=rule.placeholder_template,
                        priority=1,
                    )
                )
        return spans

    def check_japanese_residual(self, translation_lines: list[str]) -> None:
        """检查译文中是否残留明显日文。"""
        allowed_chars = set(self.setting.allowed_japanese_chars)
        allowed_tail_chars = set(self.setting.allowed_japanese_tail_chars)
        for index, line in enumerate(translation_lines, start=1):
            cleaned_line = self._strip_non_content_for_residual(line)
            segments = [match.group(0) for match in self.japanese_segment_pattern.finditer(cleaned_line)]
            if not segments:
                continue

            real_residual: list[str] = []
            for segment in segments:
                filtered_segment = [char for char in segment if char not in allowed_chars]
                if not filtered_segment:
                    continue
                if all(char in allowed_tail_chars for char in filtered_segment):
                    continue
                real_residual.extend(filtered_segment)

            if real_residual:
                raise ValueError(f"发现日文残留(第 {index} 行): {real_residual}")

    def _strip_non_content_for_residual(self, text: str) -> str:
        """在残留校验前剥离控制符和占位符噪音。"""
        cleaned_text = self.strip_rm_control_sequences(text)
        cleaned_text = self.placeholder_token_pattern.sub("", cleaned_text)
        return self.residual_escape_sequence_pattern.sub(" ", cleaned_text)


_DEFAULT_TEXT_RULES = TextRules.from_setting(TextRulesSetting())


def get_default_text_rules() -> TextRules:
    """返回配置缺省值构建的文本规则。"""
    return _DEFAULT_TEXT_RULES


__all__: list[str] = [
    "ControlSequenceSpan",
    "CustomPlaceholderRule",
    "JsonArray",
    "JsonObject",
    "JsonPrimitive",
    "JsonValue",
    "TextRules",
    "coerce_json_value",
    "ensure_json_array",
    "ensure_json_object",
    "ensure_json_string_list",
    "get_default_text_rules",
]
