"""SQLite 行对象类型收窄与 JSON 字段适配。"""

import json
from pathlib import Path
from typing import cast

import aiosqlite
from pydantic import TypeAdapter

from app.name_context.schemas import NameLocation
from app.rmmz.schema import ItemType, PluginTextTranslateRule
from app.rmmz.text_rules import coerce_json_value, ensure_json_string_list

_PLUGIN_RULE_LIST_ADAPTER: TypeAdapter[list[PluginTextTranslateRule]] = TypeAdapter(
    list[PluginTextTranslateRule]
)
_NAME_LOCATION_LIST_ADAPTER: TypeAdapter[list[NameLocation]] = TypeAdapter(list[NameLocation])


def decode_string_list(raw_value: object, field_name: str) -> list[str]:
    """从数据库 JSON 文本中读取字符串数组。"""
    if not isinstance(raw_value, str):
        raise TypeError(f"{field_name} 必须是 JSON 字符串")
    decoded_raw = cast(object, json.loads(raw_value))
    decoded = coerce_json_value(decoded_raw)
    return ensure_json_string_list(decoded, field_name)


def decode_plugin_translate_rules(raw_value: str) -> list[PluginTextTranslateRule]:
    """从数据库 JSON 文本中读取插件文本路径规则列表。"""
    decoded_raw = cast(object, json.loads(raw_value))
    decoded = coerce_json_value(decoded_raw)
    return _PLUGIN_RULE_LIST_ADAPTER.validate_python(decoded)


def decode_name_locations(raw_value: str) -> list[NameLocation]:
    """从数据库 JSON 文本中读取标准名位置列表。"""
    decoded_raw = cast(object, json.loads(raw_value))
    decoded = coerce_json_value(decoded_raw)
    return _NAME_LOCATION_LIST_ADAPTER.validate_python(decoded)


def row_to_dict(row: aiosqlite.Row) -> dict[str, object]:
    """把 SQLite 行对象转换为显式 `object` 字典边界。"""
    return cast(dict[str, object], dict(row))


def row_value(row: aiosqlite.Row, key: str) -> object:
    """从 `aiosqlite.Row` 中读取动态字段，并立刻收窄为 `object`。"""
    return cast(object, row[key])


def row_str(row: aiosqlite.Row, key: str, db_path: Path) -> str:
    """读取字符串字段。"""
    value = row_value(row, key)
    if not isinstance(value, str):
        raise TypeError(f"数据库字段 {key} 必须是字符串: {db_path}")
    return value


def row_optional_str(row: aiosqlite.Row, key: str, db_path: Path) -> str | None:
    """读取可空字符串字段。"""
    value = row_value(row, key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"数据库字段 {key} 必须是字符串或空值: {db_path}")
    return value


def row_int(row: aiosqlite.Row, key: str, db_path: Path) -> int:
    """读取整数字段。"""
    value = row_value(row, key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"数据库字段 {key} 必须是整数: {db_path}")
    return value


def row_item_type(row: aiosqlite.Row, key: str, db_path: Path) -> ItemType:
    """读取并校验翻译条目类型。"""
    value = row_str(row, key, db_path)
    if value not in ("long_text", "array", "short_text"):
        raise TypeError(f"数据库字段 {key} 不是有效条目类型: {db_path}")
    return value


__all__: list[str] = [
    "decode_name_locations",
    "decode_plugin_translate_rules",
    "decode_string_list",
    "row_int",
    "row_item_type",
    "row_optional_str",
    "row_str",
    "row_to_dict",
]
