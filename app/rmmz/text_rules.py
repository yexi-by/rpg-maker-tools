"""
文本规则服务模块。

本模块把 RPG Maker 标准控制符保护、自定义正则占位符、源文残留检查和提取阶段
文本正规化统一收敛到 `TextRules`。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.config.schemas import TextRulesSetting
from app.rmmz.control_codes import (
    ALL_PLACEHOLDER_PATTERN,
    ControlSequenceSpan,
    CustomPlaceholderRule,
    RawControlSequenceCandidate,
    format_placeholder_template,
    iter_raw_control_sequence_candidates,
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
    source_residual_segment_pattern: re.Pattern[str]
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
            source_residual_segment_pattern=re.compile(setting.source_residual_segment_pattern),
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

    def normalize_translation_lines(self, lines: list[str]) -> list[str]:
        """清理模型或人工译文行的意外首尾空白，保留行内空白。"""
        return [line.strip() for line in lines]

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
        if (
            self.setting.source_text_exclusion_profile == "english_protocol_noise"
            and self._is_english_protocol_noise_text(normalized_text)
        ):
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

    def collect_unprotected_control_sequences(self, lines: list[str]) -> dict[str, int]:
        """统计未被标准或自定义规则覆盖的疑似控制符片段。"""
        counts: dict[str, int] = {}
        for line in lines:
            for candidate in self.iter_unprotected_control_sequence_candidates(line):
                counts[candidate.original] = counts.get(candidate.original, 0) + 1
        return counts

    def iter_unprotected_control_sequence_candidates(
        self,
        text: str,
    ) -> list[RawControlSequenceCandidate]:
        """找出一行文本中仍裸露的反斜杠控制符候选。"""
        protected_spans = self.iter_control_sequence_spans(text)
        candidates: list[RawControlSequenceCandidate] = []
        for candidate in iter_raw_control_sequence_candidates(text):
            if _overlaps_any_control_span(candidate, protected_spans):
                continue
            candidates.append(candidate)
        return candidates

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

    def check_source_residual(
        self,
        translation_lines: list[str],
        *,
        allowed_terms: Sequence[str] = (),
    ) -> None:
        """检查译文中是否残留当前源语言文本。"""
        allowed_chars = set(self.setting.source_residual_allowed_chars)
        allowed_tail_chars = set(self.setting.source_residual_allowed_tail_chars)
        masked_lines = self.mask_source_residual_terms(
            translation_lines,
            [*allowed_terms, *self.setting.allowed_source_residual_terms],
        )
        for index, line in enumerate(translation_lines, start=1):
            line = masked_lines[index - 1]
            cleaned_line = self._strip_non_content_for_residual(line)
            segments = [match.group(0) for match in self.source_residual_segment_pattern.finditer(cleaned_line)]
            if not segments:
                continue

            has_non_source_content = self._has_non_source_content(cleaned_line)
            real_residual_segments: list[str] = []
            for segment in segments:
                filtered_segment = [char for char in segment if char not in allowed_chars]
                if not filtered_segment:
                    if not has_non_source_content:
                        real_residual_segments.append(segment)
                    continue
                if has_non_source_content and all(char in allowed_tail_chars for char in filtered_segment):
                    continue
                real_residual_segments.append(segment)

            if real_residual_segments:
                raise ValueError(
                    f"发现{self.setting.source_residual_label}残留(第 {index} 行): {real_residual_segments}"
                )

    def _strip_non_content_for_residual(self, text: str) -> str:
        """在残留校验前剥离控制符和占位符噪音。"""
        cleaned_text = self.strip_rm_control_sequences(text)
        cleaned_text = self.placeholder_token_pattern.sub("", cleaned_text)
        return self.residual_escape_sequence_pattern.sub(" ", cleaned_text)

    def _has_non_source_content(self, text: str) -> bool:
        """判断残留检查文本中是否存在源语言片段之外的正文内容。"""
        text_without_source = self.source_residual_segment_pattern.sub("", text)
        return any(char.isalnum() for char in text_without_source)

    def mask_source_residual_terms(
        self,
        lines: list[str],
        allowed_terms: Sequence[str],
    ) -> list[str]:
        """遮蔽允许保留的源语言片段，供源文残留检测复用。"""
        allowed_terms = [term for term in allowed_terms if term]
        if not allowed_terms:
            return list(lines)
        sorted_terms = sorted(allowed_terms, key=len, reverse=True)
        masked_lines: list[str] = []
        for line in lines:
            masked_line = line
            for term in sorted_terms:
                if self.setting.source_residual_terms_ignore_case:
                    masked_line = re.sub(
                        rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])",
                        " ",
                        masked_line,
                        flags=re.IGNORECASE,
                    )
                else:
                    masked_line = masked_line.replace(term, " ")
            masked_lines.append(masked_line)
        return masked_lines

    def _is_english_protocol_noise_text(self, text: str) -> bool:
        """排除英文游戏中常见的资源路径、脚本片段和机器协议值。"""
        stripped_text = self.strip_rm_control_sequences(text).strip()
        if not stripped_text:
            return True
        lowered_text = stripped_text.lower()
        if lowered_text in {
            "true",
            "false",
            "null",
            "none",
            "auto",
            "left",
            "right",
            "center",
            "default",
            "gamefont",
        }:
            return True
        if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", stripped_text):
            return True
        if re.search(r"(?:^|[\\/])(?:img|audio|fonts|icon|js|data)[\\/]", lowered_text):
            return True
        if re.search(r"\.(?:png|jpe?g|webp|gif|ogg|m4a|mp3|wav|webm|json|js|css|html|ttf|otf|woff2?|rpgmvp|rpgmvo|rpgmvm)$", lowered_text):
            return True
        if re.search(r"[$;{}]|=>|\b(?:var|let|const|function|return|this|console|math)\b", lowered_text):
            return True
        if re.search(r"[+\-*/<>=]=?|&&|\|\|", stripped_text) and not re.search(r"\s[A-Za-z]{2,}\s", stripped_text):
            return True
        if re.fullmatch(r"[A-Za-z0-9_./\\:-]+", stripped_text):
            if re.search(r"\d", stripped_text) and not re.search(r"\s", stripped_text):
                return True
            if "_" in stripped_text or "/" in stripped_text or "\\" in stripped_text:
                return True
            if re.search(r"[a-z][A-Z]", stripped_text):
                return True
        return False


_DEFAULT_TEXT_RULES = TextRules.from_setting(TextRulesSetting())


def get_default_text_rules() -> TextRules:
    """返回配置缺省值构建的文本规则。"""
    return _DEFAULT_TEXT_RULES


def _overlaps_any_control_span(
    candidate: RawControlSequenceCandidate,
    spans: list[ControlSequenceSpan],
) -> bool:
    """判断原始候选是否已经由占位符规则覆盖。"""
    for span in spans:
        if candidate.start_index < span.end_index and candidate.end_index > span.start_index:
            return True
    return False


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
