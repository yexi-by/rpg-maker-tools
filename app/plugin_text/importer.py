"""插件文本规则导入模块。"""

import json
from pathlib import Path
from typing import cast

import aiofiles
from pydantic import TypeAdapter

from app.rmmz.schema import GameData, PluginTextRuleRecord
from app.rmmz.text_rules import JsonValue, coerce_json_value

from .common import build_plugin_hash, expand_rule_to_leaf_paths, extract_plugin_name, resolve_plugin_leaves

type PluginRuleImportFile = dict[str, list[str]]
_PLUGIN_RULE_IMPORT_ADAPTER: TypeAdapter[PluginRuleImportFile] = TypeAdapter(PluginRuleImportFile)


async def load_plugin_rule_import_file(input_path: Path) -> PluginRuleImportFile:
    """读取外部插件规则 JSON 文件。"""
    resolved_path = input_path.resolve()
    if not resolved_path.exists():
        raise FileNotFoundError(f"插件规则导入文件不存在: {resolved_path}")
    async with aiofiles.open(resolved_path, "r", encoding="utf-8") as file:
        raw_text = await file.read()
    decoded_raw = cast(object, json.loads(raw_text))
    decoded = coerce_json_value(decoded_raw)
    return _PLUGIN_RULE_IMPORT_ADAPTER.validate_python(decoded)


def build_plugin_rule_records_from_import(
    *,
    game_data: GameData,
    import_file: PluginRuleImportFile,
) -> list[PluginTextRuleRecord]:
    """把外部插件路径映射转换成数据库规则记录。"""
    plugin_index = build_plugin_name_index(game_data)
    records: list[PluginTextRuleRecord] = []
    for plugin_name, path_templates in import_file.items():
        normalized_plugin_name = plugin_name.strip()
        if not normalized_plugin_name:
            raise ValueError("插件规则不能包含空插件名")
        if normalized_plugin_name not in plugin_index:
            raise ValueError(f"插件规则没有命中当前 plugins.js: {normalized_plugin_name}")
        index, plugin = plugin_index[normalized_plugin_name]
        normalized_paths = normalize_path_templates(path_templates)
        if not normalized_paths:
            raise ValueError(f"插件规则路径不能为空: {normalized_plugin_name}")
        records.append(
            build_plugin_rule_record(
                plugin_index=index,
                plugin_name=normalized_plugin_name,
                plugin=plugin,
                path_templates=normalized_paths,
            )
        )
    return records


def build_plugin_name_index(game_data: GameData) -> dict[str, tuple[int, dict[str, JsonValue]]]:
    """按插件名索引当前 `$plugins` 数组。"""
    plugin_index: dict[str, tuple[int, dict[str, JsonValue]]] = {}
    for index, plugin in enumerate(game_data.plugins_js):
        plugin_name = extract_plugin_name(plugin, index)
        if plugin_name in plugin_index:
            raise ValueError(f"plugins.js 中存在重复插件名，无法按名称导入规则: {plugin_name}")
        plugin_index[plugin_name] = (index, plugin)
    return plugin_index


def build_plugin_rule_record(
    *,
    plugin_index: int,
    plugin_name: str,
    plugin: dict[str, JsonValue],
    path_templates: list[str],
) -> PluginTextRuleRecord:
    """校验单个插件路径列表并构造数据库记录。"""
    resolved_leaves = resolve_plugin_leaves(plugin)
    accepted_paths: list[str] = []
    for path_template in path_templates:
        matched_paths = expand_rule_to_leaf_paths(
            path_template=path_template,
            resolved_leaves=resolved_leaves,
        )
        if not matched_paths:
            raise ValueError(
                f"插件 {plugin_name} 的路径没有命中当前插件字符串叶子: {path_template}"
            )
        accepted_paths.append(path_template)

    return PluginTextRuleRecord(
        plugin_index=plugin_index,
        plugin_name=plugin_name,
        plugin_hash=build_plugin_hash(plugin),
        path_templates=accepted_paths,
    )


def normalize_path_templates(path_templates: list[str]) -> list[str]:
    """清理并去重路径模板。"""
    normalized_paths: list[str] = []
    seen_paths: set[str] = set()
    for path_template in path_templates:
        normalized_path = path_template.strip()
        if not normalized_path or normalized_path in seen_paths:
            continue
        normalized_paths.append(normalized_path)
        seen_paths.add(normalized_path)
    return normalized_paths


__all__: list[str] = [
    "PluginRuleImportFile",
    "build_plugin_rule_records_from_import",
    "load_plugin_rule_import_file",
]
