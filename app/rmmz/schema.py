"""
核心业务数据模型定义模块。

本模块定义 CLI 翻译流程使用的业务模型：翻译条目、插件文本规则和
标准 RPG Maker MZ 文件名常量。
"""

import re
from enum import IntEnum
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.rmmz.game_data import BaseItem, CommonEvent, MapData, System, Troop
from app.rmmz.text_rules import ControlSequenceSpan, JsonValue, TextRules, get_default_text_rules


type ItemType = Literal["long_text", "array", "short_text"]
type ErrorType = Literal["AI漏翻", "控制符不匹配", "日文残留"]
type TranslationErrorItem = tuple[
    str,
    ItemType,
    str | None,
    list[str],
    list[str],
    ErrorType,
    list[str],
]


class Code(IntEnum):
    """RPG Maker 常用事件指令代码枚举。"""

    NAME = 101
    TEXT = 401
    CHOICES = 102
    SCROLL_TEXT = 405
    PLUGIN_TEXT = 357


class TranslationItem(BaseModel):
    """单个翻译条目，负责维护控制符占位符与译文恢复。"""

    role: str | None = None
    location_path: str
    item_type: ItemType
    original_lines: list[str] = Field(default_factory=list)
    original_lines_with_placeholders: list[str] = Field(default_factory=list)
    translation_lines_with_placeholders: list[str] = Field(default_factory=list)
    translation_lines: list[str] = Field(default_factory=list)
    placeholder_map: dict[str, str] = Field(default_factory=dict)
    placeholder_counts: dict[str, int] = Field(default_factory=dict)

    def build_placeholders(self, text_rules: TextRules | None = None) -> None:
        """为原文中的 RM 控制符构建简短占位符。"""
        rules = text_rules or get_default_text_rules()
        self.original_lines_with_placeholders.clear()
        self.placeholder_map.clear()
        self.placeholder_counts.clear()
        symbol_counter = 0
        complex_control_counter = 0
        complex_placeholder_map: dict[str, str] = {}

        def replace_func(span: ControlSequenceSpan) -> str:
            """把单个控制符替换为结构化占位符。"""
            nonlocal symbol_counter, complex_control_counter
            _start_index, _end_index, original, kind, code, param, is_complex = span
            placeholder = ""

            if kind == "percent":
                if param is None:
                    raise ValueError(f"百分号控制符缺少参数: {original}")
                placeholder = rules.format_percent_placeholder(param)
            elif kind == "symbol":
                symbol_counter += 1
                placeholder = rules.format_symbol_placeholder(symbol_counter)
                while placeholder in self.placeholder_map:
                    symbol_counter += 1
                    placeholder = rules.format_symbol_placeholder(symbol_counter)
            elif code is not None:
                if is_complex:
                    existing_placeholder = (
                        complex_placeholder_map.get(original)
                        if rules.setting.reuse_identical_complex_controls
                        else None
                    )
                    if existing_placeholder is not None:
                        placeholder = existing_placeholder
                    else:
                        complex_control_counter += 1
                        placeholder = rules.format_complex_control_placeholder(complex_control_counter)
                        complex_placeholder_map[original] = placeholder
                else:
                    suffix = param if param is not None else rules.setting.no_param_control_placeholder_param
                    placeholder = rules.format_simple_control_placeholder(code=code, param=suffix)

            if not placeholder:
                raise ValueError(f"无法为控制符生成占位符: {original}")
            if placeholder not in self.placeholder_map:
                self.placeholder_map[placeholder] = original
            self.placeholder_counts[placeholder] = self.placeholder_counts.get(placeholder, 0) + 1
            return placeholder

        self.original_lines_with_placeholders = [
            rules.replace_rm_control_sequences(line, replace_func)
            for line in self.original_lines
        ]

    def verify_placeholders(self, text_rules: TextRules | None = None) -> None:
        """校验模型返回的占位符数量是否与原文一致。"""
        rules = text_rules or get_default_text_rules()
        errors: list[str] = []
        original_placeholders = rules.collect_placeholder_tokens(
            self.original_lines_with_placeholders
        )
        translated_placeholders = rules.collect_placeholder_tokens(
            self.translation_lines_with_placeholders
        )

        if not original_placeholders and translated_placeholders:
            joined_placeholders = "、".join(sorted(translated_placeholders))
            errors.append(f"原文不包含任何占位符，但译文新增了以下占位符: {joined_placeholders}")

        if self.placeholder_map:
            combined_text = "".join(self.translation_lines_with_placeholders).lower()
            for placeholder, expected_count in self.placeholder_counts.items():
                actual_count = combined_text.count(placeholder.lower())
                if actual_count != expected_count:
                    errors.append(
                        f"占位符 {placeholder} 数量错误 (期望: {expected_count}, 实际: {actual_count})"
                    )

        if errors:
            raise ValueError(";\n".join(errors))

    def restore_placeholders(self) -> None:
        """将占位符恢复成 RPG Maker 原始控制符。"""
        if not self.translation_lines_with_placeholders:
            self.translation_lines = []
            return

        if not self.placeholder_map:
            self.translation_lines = list(self.translation_lines_with_placeholders)
            return

        sorted_placeholders = sorted(self.placeholder_map.keys(), key=len, reverse=True)
        new_translation_lines: list[str] = []
        for line in self.translation_lines_with_placeholders:
            restored_line = line
            for placeholder in sorted_placeholders:
                original_code = self.placeholder_map[placeholder]
                pattern = re.compile(re.escape(placeholder), re.IGNORECASE)
                restored_line = pattern.sub(lambda _match: original_code, restored_line)
            new_translation_lines.append(restored_line)
        self.translation_lines = new_translation_lines


class TranslationData(BaseModel):
    """单个文件维度的翻译数据集合。"""

    display_name: str | None
    translation_items: list[TranslationItem] = Field(default_factory=list)


class PluginTextTranslateRule(BaseModel):
    """插件文本路径规则。"""

    path_template: str
    reason: str


class PluginTextRuleRecord(BaseModel):
    """单个插件的最新文本路径规则快照。"""

    plugin_index: int
    plugin_name: str
    plugin_hash: str
    plugin_reason: str = ""
    translate_rules: list[PluginTextTranslateRule] = Field(default_factory=list)
    imported_at: str


DATA_DIRECTORY_NAME = "data"
DATA_ORIGIN_DIRECTORY_NAME = "data_origin"
JS_DIRECTORY_NAME = "js"
SYSTEM_FILE_NAME = "System.json"
PLUGINS_FILE_NAME = "plugins.js"
PLUGINS_ORIGIN_FILE_NAME = "plugins_origin.js"
COMMON_EVENTS_FILE_NAME = "CommonEvents.json"
TROOPS_FILE_NAME = "Troops.json"
MAP_INFOS_FILE_NAME = "MapInfos.json"

PLUGINS_JS_PATTERN: re.Pattern[str] = re.compile(
    r"var\s+\$plugins\s*=\s*(\[.*?\])\s*;\s*$",
    re.DOTALL | re.MULTILINE,
)
MAP_PATTERN: re.Pattern[str] = re.compile(r"Map\d+\.json")
FIXED_FILE_NAMES: set[str] = {
    "Actors.json",
    "Animations.json",
    "Armors.json",
    "Classes.json",
    "Enemies.json",
    "Items.json",
    "Skills.json",
    "States.json",
    "Weapons.json",
    "Tilesets.json",
    MAP_INFOS_FILE_NAME,
    COMMON_EVENTS_FILE_NAME,
    TROOPS_FILE_NAME,
    SYSTEM_FILE_NAME,
}


class GameData(BaseModel):
    """游戏数据聚合模型。"""

    data: dict[str, JsonValue]
    writable_data: dict[str, JsonValue]
    map_data: dict[str, MapData]
    system: System
    common_events: list[CommonEvent | None]
    troops: list[Troop | None]
    base_data: dict[str, list[BaseItem | None]]
    plugins_js: list[dict[str, JsonValue]]
    writable_plugins_js: list[dict[str, JsonValue]]

    @model_validator(mode="after")
    def validate_required_files(self) -> Self:
        """确保标准核心文件已经加载。"""
        required_files = {SYSTEM_FILE_NAME, COMMON_EVENTS_FILE_NAME, TROOPS_FILE_NAME}
        missing_files = sorted(required_files.difference(self.data))
        if missing_files:
            raise ValueError(f"游戏缺少必要标准文件: {', '.join(missing_files)}")
        return self


__all__: list[str] = [
    "Code",
    "COMMON_EVENTS_FILE_NAME",
    "DATA_DIRECTORY_NAME",
    "DATA_ORIGIN_DIRECTORY_NAME",
    "ErrorType",
    "FIXED_FILE_NAMES",
    "GameData",
    "ItemType",
    "JS_DIRECTORY_NAME",
    "MAP_INFOS_FILE_NAME",
    "MAP_PATTERN",
    "PluginTextRuleRecord",
    "PluginTextTranslateRule",
    "PLUGINS_FILE_NAME",
    "PLUGINS_JS_PATTERN",
    "PLUGINS_ORIGIN_FILE_NAME",
    "SYSTEM_FILE_NAME",
    "TROOPS_FILE_NAME",
    "TranslationData",
    "TranslationErrorItem",
    "TranslationItem",
]
