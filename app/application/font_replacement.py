"""写回阶段字体替换服务。"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from app.rmmz.schema import FontReplacementRecord, GameData, PLUGINS_FILE_NAME
from app.rmmz.text_rules import JsonObject, JsonValue

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FONTS_DIRECTORY_NAME = "fonts"
FONT_FILE_SUFFIXES = frozenset({".ttf", ".otf", ".woff", ".woff2"})


@dataclass(frozen=True, slots=True)
class FontReplacementSummary:
    """字体替换执行摘要。"""

    target_font_name: str | None
    source_font_count: int
    replaced_reference_count: int
    copied: bool
    records: list[FontReplacementRecord]


def build_empty_font_replacement_summary() -> FontReplacementSummary:
    """生成未执行字体覆盖时使用的空摘要。"""
    return FontReplacementSummary(
        target_font_name=None,
        source_font_count=0,
        replaced_reference_count=0,
        copied=False,
        records=[],
    )


def apply_font_replacement(
    *,
    game_data: GameData,
    game_root: Path,
    replacement_font_path: str | None,
) -> FontReplacementSummary:
    """复制目标字体，并把即将写出的文件引用切换到目标字体。"""
    if replacement_font_path is None or not replacement_font_path.strip():
        return build_empty_font_replacement_summary()

    source_font_path = resolve_replacement_font_path(replacement_font_path)
    target_font_name = source_font_path.name
    font_dir = game_root / FONTS_DIRECTORY_NAME
    old_font_names = collect_existing_font_names(
        font_dir=font_dir,
        replacement_font_name=target_font_name,
    )
    copy_replacement_font(
        source_font_path=source_font_path,
        font_dir=font_dir,
    )
    replaced_reference_count, records = replace_font_references(
        game_data=game_data,
        old_font_names=old_font_names,
        replacement_font_name=target_font_name,
    )
    return FontReplacementSummary(
        target_font_name=target_font_name,
        source_font_count=len(old_font_names),
        replaced_reference_count=replaced_reference_count,
        copied=True,
        records=records,
    )


def resolve_replacement_font_path(font_path_text: str) -> Path:
    """解析配置中的字体路径。"""
    font_path = Path(font_path_text)
    if not font_path.is_absolute():
        font_path = PROJECT_ROOT / font_path
    resolved_path = font_path.resolve()
    if not resolved_path.exists():
        raise FileNotFoundError(f"替换字体文件不存在: {resolved_path}")
    if not resolved_path.is_file():
        raise FileNotFoundError(f"替换字体路径不是文件: {resolved_path}")
    if resolved_path.suffix.lower() not in FONT_FILE_SUFFIXES:
        raise ValueError(f"替换字体文件扩展名不受支持: {resolved_path}")
    return resolved_path


def collect_existing_font_names(*, font_dir: Path, replacement_font_name: str) -> list[str]:
    """收集游戏字体目录中需要被替换的字体文件名。"""
    if not font_dir.exists():
        return []
    if not font_dir.is_dir():
        raise NotADirectoryError(f"游戏字体路径不是目录: {font_dir}")

    replacement_font_name_lower = replacement_font_name.lower()
    font_names: list[str] = []
    for font_path in sorted(font_dir.iterdir(), key=lambda path: path.name.lower()):
        if not font_path.is_file():
            continue
        if font_path.suffix.lower() not in FONT_FILE_SUFFIXES:
            continue
        if font_path.name.lower() == replacement_font_name_lower:
            continue
        font_names.append(font_path.name)
    return font_names


def copy_replacement_font(*, source_font_path: Path, font_dir: Path) -> None:
    """把项目字体复制到游戏字体目录。"""
    font_dir.mkdir(parents=True, exist_ok=True)
    target_path = font_dir / source_font_path.name
    if source_font_path.resolve() == target_path.resolve():
        return
    _ = shutil.copy2(source_font_path, target_path)


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

    replaced_count = 0
    records: list[FontReplacementRecord] = []
    for file_name, writable_value in list(game_data.writable_data.items()):
        if file_name == PLUGINS_FILE_NAME:
            continue
        updated_value, count, item_records = replace_font_names_in_json_value_with_records(
            value=writable_value,
            old_font_names=old_font_reference_tokens,
            replacement_font_name=replacement_font_name,
            file_name=file_name,
            value_path="",
        )
        if count:
            game_data.writable_data[file_name] = updated_value
            replaced_count += count
            records.extend(item_records)

    updated_plugins, plugin_count, plugin_records = replace_font_names_in_plugins_with_records(
        plugins=game_data.writable_plugins_js,
        old_font_names=old_font_reference_tokens,
        replacement_font_name=replacement_font_name,
    )
    if plugin_count:
        game_data.writable_plugins_js = updated_plugins
        game_data.writable_data[PLUGINS_FILE_NAME] = serialize_plugins_js(updated_plugins)
        replaced_count += plugin_count
        records.extend(plugin_records)

    return replaced_count, records


def build_font_reference_tokens(old_font_names: list[str]) -> list[str]:
    """生成字体文件名和不带扩展名的字体引用候选。"""
    token_set: set[str] = set()
    for old_font_name in old_font_names:
        token_set.add(old_font_name)
        font_stem = Path(old_font_name).stem
        if font_stem:
            token_set.add(font_stem)
    return sorted(token_set, key=len, reverse=True)


def replace_font_names_in_plugins(
    *,
    plugins: list[dict[str, JsonValue]],
    old_font_names: list[str],
    replacement_font_name: str,
) -> tuple[list[dict[str, JsonValue]], int]:
    """替换插件配置对象中的旧字体文件名。"""
    replaced_plugins: list[dict[str, JsonValue]] = []
    replaced_count = 0
    for plugin in plugins:
        updated_plugin, count = replace_font_names_in_json_object(
            value=plugin,
            old_font_names=old_font_names,
            replacement_font_name=replacement_font_name,
        )
        replaced_plugins.append(updated_plugin)
        replaced_count += count
    return replaced_plugins, replaced_count


def replace_font_names_in_plugins_with_records(
    *,
    plugins: list[dict[str, JsonValue]],
    old_font_names: list[str],
    replacement_font_name: str,
) -> tuple[list[dict[str, JsonValue]], int, list[FontReplacementRecord]]:
    """替换插件配置对象中的字体引用，并记录可还原字段。"""
    replaced_plugins: list[dict[str, JsonValue]] = []
    replaced_count = 0
    records: list[FontReplacementRecord] = []
    for plugin_index, plugin in enumerate(plugins):
        updated_plugin, count, plugin_records = replace_font_names_in_json_object_with_records(
            value=plugin,
            old_font_names=old_font_names,
            replacement_font_name=replacement_font_name,
            file_name=PLUGINS_FILE_NAME,
            value_path=f"/{plugin_index}",
        )
        replaced_plugins.append(updated_plugin)
        replaced_count += count
        records.extend(plugin_records)
    return replaced_plugins, replaced_count, records


def replace_font_names_in_json_object(
    *,
    value: JsonObject,
    old_font_names: list[str],
    replacement_font_name: str,
) -> tuple[JsonObject, int]:
    """替换 JSON 对象值里的旧字体文件名。"""
    replaced_value, replaced_count = replace_font_names_in_json_value(
        value=value,
        old_font_names=old_font_names,
        replacement_font_name=replacement_font_name,
    )
    if not isinstance(replaced_value, dict):
        raise TypeError("字体替换后的插件配置不是 JSON 对象")
    return replaced_value, replaced_count


def replace_font_names_in_json_object_with_records(
    *,
    value: JsonObject,
    old_font_names: list[str],
    replacement_font_name: str,
    file_name: str,
    value_path: str,
) -> tuple[JsonObject, int, list[FontReplacementRecord]]:
    """替换 JSON 对象值里的旧字体文件名，并记录可还原字段。"""
    replaced_value, replaced_count, records = replace_font_names_in_json_value_with_records(
        value=value,
        old_font_names=old_font_names,
        replacement_font_name=replacement_font_name,
        file_name=file_name,
        value_path=value_path,
    )
    if not isinstance(replaced_value, dict):
        raise TypeError("字体替换后的插件配置不是 JSON 对象")
    return replaced_value, replaced_count, records


def replace_font_names_in_json_value(
    *,
    value: JsonValue,
    old_font_names: list[str],
    replacement_font_name: str,
) -> tuple[JsonValue, int]:
    """递归替换 JSON 值中的旧字体文件名。"""
    if isinstance(value, str):
        return replace_font_names_in_text(
            text=value,
            old_font_names=old_font_names,
            replacement_font_name=replacement_font_name,
        )

    if isinstance(value, list):
        replaced_items: list[JsonValue] = []
        replaced_count = 0
        for item in value:
            replaced_item, count = replace_font_names_in_json_value(
                value=item,
                old_font_names=old_font_names,
                replacement_font_name=replacement_font_name,
            )
            replaced_items.append(replaced_item)
            replaced_count += count
        return replaced_items, replaced_count

    if isinstance(value, dict):
        replaced_object: JsonObject = {}
        replaced_count = 0
        for key, item in value.items():
            replaced_item, count = replace_font_names_in_json_value(
                value=item,
                old_font_names=old_font_names,
                replacement_font_name=replacement_font_name,
            )
            replaced_object[key] = replaced_item
            replaced_count += count
        return replaced_object, replaced_count

    return value, 0


def replace_font_names_in_json_value_with_records(
    *,
    value: JsonValue,
    old_font_names: list[str],
    replacement_font_name: str,
    file_name: str,
    value_path: str,
) -> tuple[JsonValue, int, list[FontReplacementRecord]]:
    """递归替换 JSON 值中的字体引用，并记录每个被改写的字符串字段。"""
    if isinstance(value, str):
        replaced_text, replaced_count = replace_font_names_in_text(
            text=value,
            old_font_names=old_font_names,
            replacement_font_name=replacement_font_name,
        )
        if replaced_count == 0:
            return value, 0, []
        return replaced_text, replaced_count, [
            FontReplacementRecord(
                file_name=file_name,
                value_path=value_path,
                original_text=value,
                replaced_text=replaced_text,
                replacement_font_name=replacement_font_name,
            )
        ]

    if isinstance(value, list):
        replaced_items: list[JsonValue] = []
        replaced_count = 0
        list_records: list[FontReplacementRecord] = []
        for index, item in enumerate(value):
            child_path = append_json_pointer_part(value_path, str(index))
            replaced_item, count, item_records = replace_font_names_in_json_value_with_records(
                value=item,
                old_font_names=old_font_names,
                replacement_font_name=replacement_font_name,
                file_name=file_name,
                value_path=child_path,
            )
            replaced_items.append(replaced_item)
            replaced_count += count
            list_records.extend(item_records)
        return replaced_items, replaced_count, list_records

    if isinstance(value, dict):
        replaced_object: JsonObject = {}
        replaced_count = 0
        object_records: list[FontReplacementRecord] = []
        for key, item in value.items():
            child_path = append_json_pointer_part(value_path, key)
            replaced_item, count, item_records = replace_font_names_in_json_value_with_records(
                value=item,
                old_font_names=old_font_names,
                replacement_font_name=replacement_font_name,
                file_name=file_name,
                value_path=child_path,
            )
            replaced_object[key] = replaced_item
            replaced_count += count
            object_records.extend(item_records)
        return replaced_object, replaced_count, object_records

    return value, 0, []


def replace_font_names_in_text(
    *,
    text: str,
    old_font_names: list[str],
    replacement_font_name: str,
) -> tuple[str, int]:
    """替换字符串中的旧字体文件名。"""
    replaced_text = text
    replaced_count = 0
    for old_font_name in old_font_names:
        occurrence_count = replaced_text.count(old_font_name)
        if occurrence_count == 0:
            continue
        replaced_text = replaced_text.replace(old_font_name, replacement_font_name)
        replaced_count += occurrence_count
    return replaced_text, replaced_count


def restore_font_references(
    *,
    game_data: GameData,
    records: list[FontReplacementRecord],
) -> int:
    """按数据库记录把字体引用还原到覆盖前文本。"""
    restored_count = 0
    plugins_changed = False
    for record in records:
        root_value = resolve_record_root(game_data=game_data, file_name=record.file_name)
        current_value = get_json_pointer_value(root_value, record.value_path)
        if not isinstance(current_value, str):
            raise ValueError(
                f"字体还原失败：{record.file_name}{record.value_path} 当前不是字符串，不能安全还原"
            )
        if current_value != record.replaced_text:
            raise ValueError(
                f"字体还原失败：{record.file_name}{record.value_path} 当前字体引用和记录不一致，不能安全还原"
            )

        updated_root = set_json_pointer_value(
            root=root_value,
            value_path=record.value_path,
            new_value=record.original_text,
        )
        if record.file_name == PLUGINS_FILE_NAME:
            plugins_changed = True
        else:
            game_data.writable_data[record.file_name] = updated_root
        restored_count += 1

    if plugins_changed:
        game_data.writable_data[PLUGINS_FILE_NAME] = serialize_plugins_js(game_data.writable_plugins_js)
    return restored_count


def resolve_record_root(*, game_data: GameData, file_name: str) -> JsonValue:
    """根据字体记录定位当前可写 JSON 根对象。"""
    if file_name == PLUGINS_FILE_NAME:
        return cast(JsonValue, game_data.writable_plugins_js)
    root_value = game_data.writable_data.get(file_name)
    if root_value is None:
        raise ValueError(f"字体还原失败：游戏数据缺少文件 {file_name}")
    return root_value


def append_json_pointer_part(value_path: str, part: str) -> str:
    """向 JSON Pointer 路径追加一段字段名或数组下标。"""
    return f"{value_path}/{escape_json_pointer_part(part)}"


def escape_json_pointer_part(part: str) -> str:
    """转义 JSON Pointer 路径片段。"""
    return part.replace("~", "~0").replace("/", "~1")


def unescape_json_pointer_part(part: str) -> str:
    """还原 JSON Pointer 路径片段。"""
    return part.replace("~1", "/").replace("~0", "~")


def split_json_pointer(value_path: str) -> list[str]:
    """拆分 JSON Pointer 路径。"""
    if value_path == "":
        return []
    if not value_path.startswith("/"):
        raise ValueError(f"字体还原记录路径不是合法 JSON Pointer: {value_path}")
    return [unescape_json_pointer_part(part) for part in value_path[1:].split("/")]


def get_json_pointer_value(root: JsonValue, value_path: str) -> JsonValue:
    """读取 JSON Pointer 指向的值。"""
    current_value = root
    for part in split_json_pointer(value_path):
        if isinstance(current_value, list):
            index = parse_json_pointer_index(part, len(current_value), value_path)
            current_value = current_value[index]
        elif isinstance(current_value, dict):
            if part not in current_value:
                raise ValueError(f"字体还原记录路径不存在: {value_path}")
            current_value = current_value[part]
        else:
            raise ValueError(f"字体还原记录路径穿过了非容器值: {value_path}")
    return current_value


def set_json_pointer_value(root: JsonValue, value_path: str, new_value: str) -> JsonValue:
    """设置 JSON Pointer 指向的字符串值，并返回更新后的根对象。"""
    parts = split_json_pointer(value_path)
    if not parts:
        return new_value

    parent = get_json_pointer_value_by_parts(root, parts[:-1], value_path)
    final_part = parts[-1]
    if isinstance(parent, list):
        index = parse_json_pointer_index(final_part, len(parent), value_path)
        parent[index] = new_value
        return root
    if isinstance(parent, dict):
        if final_part not in parent:
            raise ValueError(f"字体还原记录路径不存在: {value_path}")
        parent[final_part] = new_value
        return root
    raise ValueError(f"字体还原记录路径父级不是容器: {value_path}")


def get_json_pointer_value_by_parts(
    root: JsonValue,
    parts: list[str],
    full_path: str,
) -> JsonValue:
    """按已拆分路径读取 JSON 值。"""
    current_value = root
    for part in parts:
        if isinstance(current_value, list):
            index = parse_json_pointer_index(part, len(current_value), full_path)
            current_value = current_value[index]
        elif isinstance(current_value, dict):
            if part not in current_value:
                raise ValueError(f"字体还原记录路径不存在: {full_path}")
            current_value = current_value[part]
        else:
            raise ValueError(f"字体还原记录路径穿过了非容器值: {full_path}")
    return current_value


def parse_json_pointer_index(part: str, item_count: int, full_path: str) -> int:
    """把 JSON Pointer 数组下标转成整数并校验范围。"""
    try:
        index = int(part)
    except ValueError as error:
        raise ValueError(f"字体还原记录数组下标非法: {full_path}") from error
    if index < 0 or index >= item_count:
        raise ValueError(f"字体还原记录数组下标越界: {full_path}")
    return index


def serialize_plugins_js(plugins: list[dict[str, JsonValue]]) -> str:
    """序列化插件配置为 RPG Maker MZ 使用的 JavaScript 文本。"""
    plugins_text = json.dumps(plugins, ensure_ascii=False, indent=2)
    return f"var $plugins = {plugins_text};\n"


__all__ = [
    "FontReplacementSummary",
    "apply_font_replacement",
    "build_font_reference_tokens",
    "build_empty_font_replacement_summary",
    "collect_existing_font_names",
    "replace_font_names_in_json_value",
    "resolve_replacement_font_path",
    "restore_font_references",
]
