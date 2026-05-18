"""应用 Rust 字体扫描结果。"""

from __future__ import annotations

from typing import cast

from app.native_quality import collect_native_font_replacements
from app.rmmz.schema import FontReplacementRecord, GameData, PLUGINS_FILE_NAME
from app.rmmz.text_rules import JsonArray, JsonObject, JsonValue, ensure_json_array, ensure_json_object

from .files import serialize_plugins_js
from .references import build_font_reference_tokens

def replace_font_references(
    *,
    game_data: GameData,
    old_font_names: list[str],
    replacement_font_name: str,
) -> tuple[int, list[FontReplacementRecord]]:
    """在本轮可写数据中替换旧字体文件名。"""
    old_font_reference_tokens = build_font_reference_tokens(old_font_names)
    if not old_font_reference_tokens:
        return 0, []

    plugins_payload: JsonArray = [
        cast(JsonValue, plugin) for plugin in game_data.writable_plugins_js
    ]
    native_result = collect_native_font_replacements(
        game_data=game_data.writable_data,
        plugins_js=plugins_payload,
        old_font_names=old_font_reference_tokens,
        replacement_font_name=replacement_font_name,
    )
    data_changes = ensure_json_array(
        native_result["data_changes"],
        "font_replacements.data_changes",
    )
    plugin_changes = ensure_json_array(
        native_result["plugin_changes"],
        "font_replacements.plugin_changes",
    )
    replaced_count_value = native_result["replaced_count"]
    if not isinstance(replaced_count_value, int) or isinstance(replaced_count_value, bool):
        raise TypeError("font_replacements.replaced_count 必须是整数")
    records = apply_native_font_replacement_changes(
        game_data=game_data,
        data_changes=data_changes,
        plugin_changes=plugin_changes,
        replacement_font_name=replacement_font_name,
    )
    if plugin_changes:
        game_data.writable_data[PLUGINS_FILE_NAME] = serialize_plugins_js(
            game_data.writable_plugins_js
        )
    return replaced_count_value, records

def apply_native_font_replacement_changes(
    *,
    game_data: GameData,
    data_changes: JsonArray,
    plugin_changes: JsonArray,
    replacement_font_name: str,
) -> list[FontReplacementRecord]:
    """把 Rust 计算出的字体替换清单应用到本轮可写数据。"""
    records: list[FontReplacementRecord] = []
    for change_value in data_changes:
        change = ensure_json_object(change_value, "font_replacements.data_changes[]")
        file_name = read_font_change_text_field(change=change, field_name="file_name")
        if file_name not in game_data.writable_data:
            raise KeyError(f"字体替换目标文件不存在: {file_name}")
        records.append(
            apply_native_font_change(
                root=game_data.writable_data[file_name],
                change=change,
                replacement_font_name=replacement_font_name,
            )
        )

    plugins_root = cast(JsonValue, game_data.writable_plugins_js)
    for change_value in plugin_changes:
        change = ensure_json_object(change_value, "font_replacements.plugin_changes[]")
        records.append(
            apply_native_font_change(
                root=plugins_root,
                change=change,
                replacement_font_name=replacement_font_name,
            )
        )
    return records

def apply_native_font_change(
    *,
    root: JsonValue,
    change: JsonObject,
    replacement_font_name: str,
) -> FontReplacementRecord:
    """应用单条字体替换并生成记录。"""
    file_name = read_font_change_text_field(change=change, field_name="file_name")
    value_path = read_font_change_text_field(change=change, field_name="value_path")
    original_text = read_font_change_text_field(change=change, field_name="original_text")
    replaced_text = read_font_change_text_field(change=change, field_name="replaced_text")
    set_json_pointer_text(
        root=root,
        value_path=value_path,
        original_text=original_text,
        replaced_text=replaced_text,
    )
    return FontReplacementRecord(
        file_name=file_name,
        value_path=value_path,
        original_text=original_text,
        replaced_text=replaced_text,
        replacement_font_name=replacement_font_name,
    )

def read_font_change_text_field(*, change: JsonObject, field_name: str) -> str:
    """读取 Rust 字体替换清单中的字符串字段。"""
    value = change[field_name]
    if not isinstance(value, str):
        raise TypeError(f"font_replacements.{field_name} 必须是字符串")
    return value

def set_json_pointer_text(
    *,
    root: JsonValue,
    value_path: str,
    original_text: str,
    replaced_text: str,
) -> None:
    """按 JSON Pointer 路径替换字符串字段，并校验扫描来源没有漂移。"""
    parts = split_json_pointer_path(value_path)
    if not parts:
        raise ValueError("字体替换路径不能为空")
    current_value = root
    for part in parts[:-1]:
        current_value = resolve_json_pointer_child(
            value=current_value,
            part=part,
            context=value_path,
        )
    last_part = parts[-1]
    if isinstance(current_value, list):
        index = parse_json_pointer_index(part=last_part, context=value_path)
        current_text = current_value[index]
        if current_text != original_text:
            raise ValueError(f"字体替换目标内容已变化: {value_path}")
        current_value[index] = replaced_text
        return
    if isinstance(current_value, dict):
        current_text = current_value[last_part]
        if current_text != original_text:
            raise ValueError(f"字体替换目标内容已变化: {value_path}")
        current_value[last_part] = replaced_text
        return
    raise TypeError(f"字体替换路径无法写入: {value_path}")

def resolve_json_pointer_child(*, value: JsonValue, part: str, context: str) -> JsonValue:
    """沿 JSON Pointer 路径向下定位一层。"""
    if isinstance(value, list):
        return value[parse_json_pointer_index(part=part, context=context)]
    if isinstance(value, dict):
        return value[part]
    raise TypeError(f"字体替换路径无法继续定位: {context}")

def parse_json_pointer_index(*, part: str, context: str) -> int:
    """把 JSON Pointer 数组片段解析成下标。"""
    try:
        return int(part)
    except ValueError as error:
        raise ValueError(f"字体替换数组下标无效: {context}") from error

def split_json_pointer_path(value_path: str) -> list[str]:
    """拆分 Rust 返回的 JSON Pointer 路径。"""
    if not value_path.startswith("/"):
        raise ValueError(f"字体替换路径必须以 / 开头: {value_path}")
    return [unescape_json_pointer_part(part) for part in value_path.split("/")[1:]]

def unescape_json_pointer_part(part: str) -> str:
    """还原 JSON Pointer 路径片段。"""
    return part.replace("~1", "/").replace("~0", "~")
