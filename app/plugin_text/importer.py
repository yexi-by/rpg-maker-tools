"""外部插件文本规则导入模块。

本模块读取外部 Agent 产出的 JSON 文件，并把其中声明的 JSONPath 规则校验成数据库
可保存的插件规则记录。
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

import aiofiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.rmmz.schema import GameData, PluginTextRuleRecord, PluginTextTranslateRule

from .common import build_plugin_hash, expand_rule_to_leaf_paths, extract_plugin_name, resolve_plugin_leaves

PLUGIN_RULE_IMPORT_SCHEMA_VERSION = 1


class StrictPluginRuleImportModel(BaseModel):
    """外部插件规则导入文件的严格模型基类。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


class PluginRuleImportEntry(StrictPluginRuleImportModel):
    """外部文件中单个插件的规则声明。"""

    plugin_index: int = Field(ge=0)
    plugin_name: str
    plugin_reason: str = ""
    translate_rules: list[PluginTextTranslateRule] = Field(default_factory=list)

    @field_validator("plugin_name")
    @classmethod
    def _validate_plugin_name(cls, value: str) -> str:
        """插件名必须非空，避免导入文件和当前 plugins.js 难以核对。"""
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("plugin_name 不能为空")
        return normalized_value

    @field_validator("translate_rules")
    @classmethod
    def _validate_translate_rules(
        cls,
        value: list[PluginTextTranslateRule],
    ) -> list[PluginTextTranslateRule]:
        """外部规则文件只接收确认为需要翻译的路径。"""
        if not value:
            raise ValueError("translate_rules 不能为空；没有可翻译路径的插件不要写入文件")
        return value


class PluginRuleImportFile(StrictPluginRuleImportModel):
    """外部插件规则导入文件根对象。"""

    schema_version: int = PLUGIN_RULE_IMPORT_SCHEMA_VERSION
    game_title: str
    plugins: list[PluginRuleImportEntry] = Field(default_factory=list)


async def load_plugin_rule_import_file(input_path: Path) -> PluginRuleImportFile:
    """读取并校验外部插件规则 JSON 文件。"""
    resolved_path = input_path.resolve()
    if not resolved_path.exists():
        raise FileNotFoundError(f"插件规则导入文件不存在: {resolved_path}")
    async with aiofiles.open(resolved_path, "r", encoding="utf-8") as file:
        raw_text = await file.read()
    import_file = PluginRuleImportFile.model_validate_json(raw_text)
    if import_file.schema_version != PLUGIN_RULE_IMPORT_SCHEMA_VERSION:
        raise ValueError(
            f"插件规则导入文件 schema_version 无法识别: {import_file.schema_version}"
        )
    return import_file


def build_plugin_rule_records_from_import(
    *,
    game_title: str,
    game_data: GameData,
    import_file: PluginRuleImportFile,
) -> list[PluginTextRuleRecord]:
    """把外部规则声明转换成数据库插件规则记录。"""
    if import_file.game_title != game_title:
        raise ValueError(
            f"插件规则导入文件的 game_title 不匹配，期望 {game_title}，实际 {import_file.game_title}"
        )

    imported_at = datetime.now(timezone.utc).isoformat()
    records: list[PluginTextRuleRecord] = []
    seen_plugin_indexes: set[int] = set()
    for entry in import_file.plugins:
        if entry.plugin_index in seen_plugin_indexes:
            raise ValueError(f"插件规则导入文件存在重复 plugin_index: {entry.plugin_index}")
        seen_plugin_indexes.add(entry.plugin_index)
        records.append(
            build_plugin_rule_record(
                game_data=game_data,
                entry=entry,
                imported_at=imported_at,
            )
        )
    return records


def build_plugin_rule_record(
    *,
    game_data: GameData,
    entry: PluginRuleImportEntry,
    imported_at: str,
) -> PluginTextRuleRecord:
    """校验单个插件导入条目并构造数据库记录。"""
    if entry.plugin_index >= len(game_data.plugins_js):
        raise ValueError(f"插件索引越界: {entry.plugin_index}")

    plugin = game_data.plugins_js[entry.plugin_index]
    actual_plugin_name = extract_plugin_name(plugin, entry.plugin_index)
    if entry.plugin_name != actual_plugin_name:
        raise ValueError(
            f"插件名称不匹配，索引 {entry.plugin_index} 期望 {actual_plugin_name}，实际 {entry.plugin_name}"
        )

    resolved_leaves = resolve_plugin_leaves(plugin)
    normalized_rules: list[PluginTextTranslateRule] = []
    seen_templates: set[str] = set()
    for rule in entry.translate_rules:
        if rule.path_template in seen_templates:
            continue
        matched_paths = expand_rule_to_leaf_paths(
            path_template=rule.path_template,
            resolved_leaves=resolved_leaves,
        )
        if not matched_paths:
            raise ValueError(
                f"插件 {entry.plugin_name} 的路径没有命中当前插件字符串叶子: {rule.path_template}"
            )
        seen_templates.add(rule.path_template)
        normalized_rules.append(rule)

    return PluginTextRuleRecord(
        plugin_index=entry.plugin_index,
        plugin_name=entry.plugin_name,
        plugin_hash=build_plugin_hash(plugin),
        plugin_reason=entry.plugin_reason,
        translate_rules=normalized_rules,
        imported_at=imported_at,
    )


__all__: list[str] = [
    "PLUGIN_RULE_IMPORT_SCHEMA_VERSION",
    "PluginRuleImportEntry",
    "PluginRuleImportFile",
    "build_plugin_rule_records_from_import",
    "load_plugin_rule_import_file",
]
