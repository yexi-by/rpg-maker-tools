"""
文本规则服务模块。

本模块把控制符、占位符、插件配置值过滤、日文残留检查和提取阶段的硬编码标点
统一收敛到 `TextRules`。业务层只依赖这个规则门面，具体规则来源于 `setting.toml`。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.config.schemas import TextRulesSetting
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

type ControlSequenceKind = Literal["code", "symbol", "percent"]
NON_STRICT_JAPANESE_PATTERN: re.Pattern[str] = re.compile("[ぁ-ゖゝ-ゟァ-ヺヽ-ヿ一-鿿]")

type ControlSequenceSpan = tuple[
    int,
    int,
    str,
    ControlSequenceKind,
    str | None,
    str | None,
    bool,
]


@dataclass(frozen=True, slots=True)
class TextRules:
    """运行时文本规则集合。"""

    setting: TextRulesSetting
    simple_control_param_pattern: re.Pattern[str]
    translation_placeholder_pattern: re.Pattern[str]
    japanese_segment_pattern: re.Pattern[str]
    placeholder_pattern: re.Pattern[str]
    resource_like_pattern: re.Pattern[str]
    pure_number_pattern: re.Pattern[str]
    hex_color_pattern: re.Pattern[str]
    css_color_function_pattern: re.Pattern[str]
    resource_path_pattern: re.Pattern[str]
    snake_case_pattern: re.Pattern[str]
    camel_case_pattern: re.Pattern[str]
    bracket_identifier_pattern: re.Pattern[str]
    dot_identifier_pattern: re.Pattern[str]
    script_concat_pattern: re.Pattern[str]
    script_call_pattern: re.Pattern[str]
    non_content_after_control_pattern: re.Pattern[str]

    @classmethod
    def from_setting(cls, setting: TextRulesSetting) -> "TextRules":
        """根据配置构建并预编译全部正则规则。"""
        return cls(
            setting=setting,
            simple_control_param_pattern=re.compile(setting.simple_control_param_pattern),
            translation_placeholder_pattern=re.compile(
                setting.translation_placeholder_pattern,
                re.IGNORECASE,
            ),
            japanese_segment_pattern=re.compile(setting.japanese_segment_pattern),
            placeholder_pattern=re.compile(setting.placeholder_pattern),
            resource_like_pattern=re.compile(setting.resource_like_pattern, re.IGNORECASE),
            pure_number_pattern=re.compile(setting.pure_number_pattern),
            hex_color_pattern=re.compile(setting.hex_color_pattern),
            css_color_function_pattern=re.compile(
                setting.css_color_function_pattern,
                re.IGNORECASE,
            ),
            resource_path_pattern=re.compile(setting.resource_path_pattern, re.IGNORECASE),
            snake_case_pattern=re.compile(setting.snake_case_pattern),
            camel_case_pattern=re.compile(setting.camel_case_pattern),
            bracket_identifier_pattern=re.compile(setting.bracket_identifier_pattern),
            dot_identifier_pattern=re.compile(setting.dot_identifier_pattern),
            script_concat_pattern=re.compile(setting.script_concat_pattern),
            script_call_pattern=re.compile(setting.script_call_pattern),
            non_content_after_control_pattern=re.compile(
                setting.non_content_after_control_pattern,
            ),
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
            start_index, end_index = span[0], span[1]
            parts.append(text[last_end:start_index])
            parts.append(replacer(span))
            last_end = end_index
        parts.append(text[last_end:])
        return "".join(parts)

    def strip_rm_control_sequences(self, text: str) -> str:
        """从文本中剥离 RPG Maker 控制符。"""
        return self.replace_rm_control_sequences(text, lambda _span: "")

    def iter_control_sequence_spans(self, text: str) -> list[ControlSequenceSpan]:
        """顺序扫描一行文本，识别其中的 RPG Maker 控制符片段。"""
        spans: list[ControlSequenceSpan] = []
        index = 0
        no_param_codes = {code.upper() for code in self.setting.no_param_alpha_control_codes}

        while index < len(text):
            current_char = text[index]

            if current_char == "%":
                end_index = index + 1
                while end_index < len(text) and text[end_index].isdigit():
                    end_index += 1
                if end_index > index + 1:
                    original = text[index:end_index]
                    spans.append((index, end_index, original, "percent", None, original[1:], False))
                    index = end_index
                    continue

            if current_char != "\\" or index + 1 >= len(text):
                index += 1
                continue

            next_char = text[index + 1]
            if next_char.isalpha():
                code_end = index + 1
                while code_end < len(text) and text[code_end].isalpha():
                    code_end += 1

                code = text[index + 1 : code_end]
                if code_end < len(text) and text[code_end] in {"[", "<"}:
                    open_char = text[code_end]
                    close_char = "]" if open_char == "[" else ">"
                    match_end = _find_matching_delimiter_end(
                        text=text,
                        start_index=code_end,
                        open_char=open_char,
                        close_char=close_char,
                    )
                    if match_end is not None:
                        original = text[index : match_end + 1]
                        param = text[code_end + 1 : match_end]
                        is_complex = (
                            open_char == "<"
                            or "\\" in param
                            or "[" in param
                            or "]" in param
                            or "<" in param
                            or ">" in param
                            or self.simple_control_param_pattern.fullmatch(param) is None
                        )
                        spans.append(
                            (index, match_end + 1, original, "code", code, param, is_complex)
                        )
                        index = match_end + 1
                        continue

                if next_char.upper() in no_param_codes:
                    original = text[index : index + 2]
                    spans.append((index, index + 2, original, "code", next_char, None, False))
                    index += 2
                    continue

                index += 1
                continue

            original = text[index : index + 2]
            spans.append((index, index + 2, original, "symbol", None, None, False))
            index += 2

        return spans

    def collect_placeholder_tokens(self, lines: list[str]) -> set[str]:
        """收集文本行中的翻译占位符集合。"""
        placeholders: set[str] = set()
        for line in lines:
            placeholders.update(self.translation_placeholder_pattern.findall(line))
        return placeholders

    def should_extract_plugin_command_key(self, key: str) -> bool:
        """判断 357 插件命令参数键是否像可翻译文本字段。"""
        key_lower = key.strip().lower()
        if key_lower in {item.lower() for item in self.setting.plugin_command_excluded_keys}:
            return False
        return any(
            keyword.lower() in key_lower
            for keyword in self.setting.plugin_command_text_keywords
        )

    def passes_plugin_text_language_filter(self, text: str) -> bool:
        """日文核心版只放行包含日文特征的插件文本。"""
        normalized_text = text.strip()
        if not normalized_text:
            return False
        return NON_STRICT_JAPANESE_PATTERN.search(normalized_text) is not None

    def should_skip_plugin_like_text(
        self,
        *,
        text: str,
        path_parts: list[str | int],
        plugin_name: str | None = None,
        command_name: str | None = None,
    ) -> bool:
        """判断插件类文本是否应被排除。"""
        normalized_text = text.strip()
        if not normalized_text:
            return True
        if self._is_excluded_plugin_name(plugin_name):
            return True
        if self._matches_excluded_plugin_command_field(
            path_parts=path_parts,
            plugin_name=plugin_name,
            command_name=command_name,
        ):
            return True
        if self.has_non_translatable_path_key(path_parts):
            return True
        if normalized_text.lower() in {item.lower() for item in self.setting.boolean_texts}:
            return True
        if self.pure_number_pattern.fullmatch(normalized_text) is not None:
            return True
        if self._is_color_text(normalized_text):
            return True
        if self.resource_path_pattern.fullmatch(normalized_text) is not None:
            return True
        if Path(normalized_text).suffix.lower() in {
            suffix.lower() for suffix in self.setting.file_like_suffixes
        }:
            return True
        if normalized_text.lower() in {item.lower() for item in self.setting.generic_enum_texts}:
            return True
        if self._is_placeholder_only_text(normalized_text):
            return True
        if self._looks_like_script_expression(normalized_text):
            return True
        if self._looks_like_identifier_text(normalized_text):
            return True
        return False

    def has_non_translatable_path_key(self, path_parts: list[str | int]) -> bool:
        """判断路径中是否包含明确不可翻译的字段名。"""
        blocked_keys = {normalize_path_key(key) for key in self.setting.non_translatable_path_keywords}
        for part in path_parts:
            if isinstance(part, str) and normalize_path_key(part) in blocked_keys:
                return True
        return False

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
        cleaned_text = self.placeholder_pattern.sub("", cleaned_text)
        return re.sub(r"\\[nrt]", " ", cleaned_text)

    def _is_color_text(self, text: str) -> bool:
        """判断是否为颜色配置文本。"""
        return (
            self.hex_color_pattern.fullmatch(text) is not None
            or self.css_color_function_pattern.fullmatch(text) is not None
        )

    def _is_placeholder_only_text(self, text: str) -> bool:
        """判断文本去掉控制符后是否只剩符号噪音。"""
        stripped_text = self.strip_rm_control_sequences(text)
        return self.non_content_after_control_pattern.fullmatch(stripped_text) is not None

    def _looks_like_script_expression(self, text: str) -> bool:
        """判断是否像脚本表达式或数据访问表达式。"""
        if "$data" in text or "$game" in text:
            return True
        if self.script_concat_pattern.search(text):
            return True
        if " ? " in text and " : " in text:
            return True
        if self.bracket_identifier_pattern.fullmatch(text):
            return True
        if self.dot_identifier_pattern.fullmatch(text) and text.startswith("$"):
            return True
        if "$" in text and self.script_call_pattern.search(text):
            return True
        return False

    def _looks_like_identifier_text(self, text: str) -> bool:
        """判断是否像内部标识符而不是玩家可见文本。"""
        return (
            self.snake_case_pattern.fullmatch(text) is not None
            or self.camel_case_pattern.fullmatch(text) is not None
            or self.dot_identifier_pattern.fullmatch(text) is not None
            or self.bracket_identifier_pattern.fullmatch(text) is not None
        )

    def _is_excluded_plugin_name(self, plugin_name: str | None) -> bool:
        """判断插件名是否属于整插件排除名单。"""
        if plugin_name is None:
            return False
        return plugin_name.strip().lower() in {
            item.lower() for item in self.setting.excluded_plugin_names
        }

    def _matches_excluded_plugin_command_field(
        self,
        *,
        path_parts: list[str | int],
        plugin_name: str | None,
        command_name: str | None,
    ) -> bool:
        """判断插件命令字段是否命中定向排除规则。"""
        if plugin_name is None or command_name is None:
            return False

        field_key = _find_last_string_key(path_parts)
        if field_key is None:
            return False

        normalized_triplet = "|".join(
            (
                plugin_name.strip().lower(),
                command_name.strip().lower(),
                field_key,
            )
        )
        return normalized_triplet in {
            item.strip().lower() for item in self.setting.excluded_plugin_command_fields
        }


_DEFAULT_TEXT_RULES = TextRules.from_setting(TextRulesSetting())


def get_default_text_rules() -> TextRules:
    """返回配置缺省值构建的文本规则。"""
    return _DEFAULT_TEXT_RULES


def normalize_path_key(key: str) -> str:
    """归一化路径键名，便于黑名单匹配。"""
    return key.strip().lower().replace("_", "").replace("-", "")


def _find_last_string_key(path_parts: list[str | int]) -> str | None:
    """找出路径中最后一个字符串键名。"""
    for part in reversed(path_parts):
        if isinstance(part, str):
            return normalize_path_key(part)
    return None


def _find_matching_delimiter_end(
    *,
    text: str,
    start_index: int,
    open_char: str,
    close_char: str,
) -> int | None:
    """查找成对控制符参数的闭合位置。"""
    if open_char == "<" and close_char == ">":
        return _find_angle_delimiter_end(text=text, start_index=start_index)

    depth = 0
    active_quote: str | None = None

    for index in range(start_index, len(text)):
        char = text[index]
        previous_char = text[index - 1] if index > start_index else ""

        if active_quote is not None:
            if char == active_quote and previous_char != "\\":
                active_quote = None
            continue

        if char in {'"', "'"} and _can_start_quote(
            text=text,
            start_index=start_index,
            index=index,
            quote_char=char,
        ):
            active_quote = char
            continue

        if char == open_char:
            depth += 1
            continue

        if char == close_char:
            depth -= 1
            if depth == 0:
                return index

    return None


def _find_angle_delimiter_end(*, text: str, start_index: int) -> int | None:
    """查找 `\\js<...>` 角括号参数的闭合位置。"""
    active_quote: str | None = None

    for index in range(start_index + 1, len(text)):
        char = text[index]
        previous_char = text[index - 1]

        if active_quote is not None:
            if char == active_quote and previous_char != "\\":
                active_quote = None
            continue

        if char in {'"', "'"} and previous_char != "\\":
            active_quote = char
            continue

        if char == ">":
            return index

    return None


def _can_start_quote(
    *,
    text: str,
    start_index: int,
    index: int,
    quote_char: str,
) -> bool:
    """判断控制符参数中的引号是否是真正的字符串边界。"""
    previous_char = text[index - 1] if index > start_index else ""
    next_char = text[index + 1] if index + 1 < len(text) else ""

    if previous_char == "\\":
        return False
    if quote_char == "'" and previous_char.isalnum() and next_char.isalnum():
        return False
    return True


__all__: list[str] = [
    "ControlSequenceKind",
    "ControlSequenceSpan",
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
    "normalize_path_key",
]
