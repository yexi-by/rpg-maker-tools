"""写回阶段字体替换服务。"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import demjson3

from app.application.file_writer import replace_json_file, replace_plugins_file
from app.native_quality import collect_native_font_replacements
from app.rmmz.schema import (
    DATA_DIRECTORY_NAME,
    DATA_ORIGIN_DIRECTORY_NAME,
    FontReplacementRecord,
    GameData,
    JS_DIRECTORY_NAME,
    PLUGINS_FILE_NAME,
    PLUGINS_JS_PATTERN,
    PLUGINS_ORIGIN_FILE_NAME,
)
from app.rmmz.text_rules import (
    JsonArray,
    JsonObject,
    JsonValue,
    coerce_json_value,
    ensure_json_array,
    ensure_json_object,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FONTS_DIRECTORY_NAME = "fonts"
FONT_FILE_SUFFIXES = frozenset({".ttf", ".otf", ".woff", ".woff2"})
FONT_FILE_REFERENCE_PATTERN: re.Pattern[str] = re.compile(
    r"[\w .+\-\u0080-\uffff]+?\.(?:ttf|otf|woff2?)",
    re.IGNORECASE,
)
BARE_FONT_REFERENCE_PATTERN: re.Pattern[str] = re.compile(r"[A-Za-z0-9_ .+\-]{1,128}")


@dataclass(frozen=True, slots=True)
class FontReplacementSummary:
    """字体替换执行摘要。"""

    target_font_name: str | None
    source_font_count: int
    replaced_reference_count: int
    copied: bool
    records: list[FontReplacementRecord]


@dataclass(frozen=True, slots=True)
class OriginFontRestoreSummary:
    """按原件留档对比还原字体引用的执行摘要。"""

    target_font_names: list[str]
    restored_field_count: int
    restored_reference_count: int


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


def build_font_reference_tokens(old_font_names: list[str]) -> list[str]:
    """生成字体文件名和不带扩展名的字体引用候选。"""
    token_set: set[str] = set()
    for old_font_name in old_font_names:
        token_set.add(old_font_name)
        font_stem = Path(old_font_name).stem
        if font_stem:
            token_set.add(font_stem)
    return sorted(token_set, key=len, reverse=True)


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


def replace_font_names_in_text(
    *,
    text: str,
    old_font_names: list[str],
    replacement_font_name: str,
) -> tuple[str, int]:
    """只替换完整字体引用，避免误改玩家可见正文。"""
    if not any(old_font_name in text for old_font_name in old_font_names):
        return text, 0
    replaced_text, replaced_count = replace_complete_font_reference_text(
        text=text,
        old_font_names=old_font_names,
        replacement_font_name=replacement_font_name,
    )
    if replaced_count:
        return replaced_text, replaced_count
    return replace_font_references_in_encoded_json_text(
        text=text,
        old_font_names=old_font_names,
        replacement_font_name=replacement_font_name,
    )


def replace_complete_font_reference_text(
    *,
    text: str,
    old_font_names: list[str],
    replacement_font_name: str,
) -> tuple[str, int]:
    """替换整个字符串就是旧字体引用的值。"""
    stripped_text = text.strip()
    if not stripped_text:
        return text, 0

    leading_text = text[:len(text) - len(text.lstrip())]
    trailing_text = text[len(text.rstrip()):]
    for old_font_name in old_font_names:
        replaced_reference = replace_complete_font_reference_core(
            text=stripped_text,
            old_font_name=old_font_name,
            replacement_font_name=replacement_font_name,
        )
        if replaced_reference is not None:
            return f"{leading_text}{replaced_reference}{trailing_text}", 1
    return text, 0


def replace_complete_font_reference_core(
    *,
    text: str,
    old_font_name: str,
    replacement_font_name: str,
) -> str | None:
    """替换完整文件名、完整 stem 或带目录前缀的完整字体引用。"""
    if text == old_font_name:
        return replacement_font_name

    separator_index = max(text.rfind("/"), text.rfind("\\"))
    if separator_index < 0:
        return None
    reference_name = text[separator_index + 1:]
    if reference_name != old_font_name:
        return None
    return f"{text[:separator_index + 1]}{replacement_font_name}"


def replace_font_references_in_encoded_json_text(
    *,
    text: str,
    old_font_names: list[str],
    replacement_font_name: str,
) -> tuple[str, int]:
    """解析插件参数里的 JSON 字符串，并只替换其中的完整字体引用。"""
    try:
        raw_value = cast(object, json.loads(text))
        json_value = coerce_json_value(raw_value)
    except (json.JSONDecodeError, TypeError):
        return text, 0

    if not isinstance(json_value, list | dict):
        return text, 0

    replaced_value, replaced_count = replace_font_names_in_json_value(
        value=json_value,
        old_font_names=old_font_names,
        replacement_font_name=replacement_font_name,
    )
    if replaced_count == 0:
        return text, 0
    return json.dumps(replaced_value, ensure_ascii=False), replaced_count


def collect_replacement_font_names(
    *,
    replacement_font_path: str | None,
    records: list[FontReplacementRecord],
) -> list[str]:
    """收集本次字体还原应识别的新字体文件名。"""
    font_names: list[str] = []
    if replacement_font_path is not None and replacement_font_path.strip():
        font_names.append(Path(replacement_font_path).name)
    font_names.extend(record.replacement_font_name for record in records)
    return normalize_font_name_list(font_names)


def restore_font_references_from_origin_backups(
    *,
    game_root: Path,
    replacement_font_names: list[str],
) -> OriginFontRestoreSummary:
    """对比激活版和原件留档，把覆盖字体引用替回原来的字体引用。"""
    target_font_names = normalize_font_name_list(
        build_font_reference_tokens(replacement_font_names)
    )
    if not target_font_names:
        raise ValueError("字体还原缺少候选覆盖字体名称")

    active_data_dir = game_root / DATA_DIRECTORY_NAME
    origin_data_dir = game_root / DATA_ORIGIN_DIRECTORY_NAME
    active_plugins_path = game_root / JS_DIRECTORY_NAME / PLUGINS_FILE_NAME
    origin_plugins_path = game_root / JS_DIRECTORY_NAME / PLUGINS_ORIGIN_FILE_NAME
    if not origin_data_dir.exists() and not origin_plugins_path.exists():
        raise FileNotFoundError("字体还原需要 data_origin 或 plugins_origin.js 原件留档")

    restored_field_count = 0
    restored_reference_count = 0
    if origin_data_dir.exists():
        if not origin_data_dir.is_dir():
            raise NotADirectoryError(f"原件数据留档不是目录: {origin_data_dir}")
        for origin_file_path in sorted(origin_data_dir.glob("*.json"), key=lambda path: path.name):
            active_file_path = active_data_dir / origin_file_path.name
            if not active_file_path.exists():
                raise FileNotFoundError(f"激活数据文件不存在，无法对比还原字体: {active_file_path}")
            active_value = read_json_value_file(active_file_path)
            origin_value = read_json_value_file(origin_file_path)
            updated_value, field_count, reference_count = restore_font_references_in_json_value_by_origin(
                active_value=active_value,
                origin_value=origin_value,
                target_font_names=target_font_names,
            )
            if field_count == 0:
                continue
            replace_json_file(
                target_path=active_file_path,
                data=updated_value,
                temp_dir=game_root,
            )
            restored_field_count += field_count
            restored_reference_count += reference_count

    if origin_plugins_path.exists():
        if not active_plugins_path.exists():
            raise FileNotFoundError(f"激活插件配置不存在，无法对比还原字体: {active_plugins_path}")
        active_plugins = read_plugins_js_file(active_plugins_path)
        origin_plugins = read_plugins_js_file(origin_plugins_path)
        updated_plugins_value, field_count, reference_count = restore_font_references_in_json_value_by_origin(
            active_value=cast(JsonValue, active_plugins),
            origin_value=cast(JsonValue, origin_plugins),
            target_font_names=target_font_names,
        )
        if field_count > 0:
            if not isinstance(updated_plugins_value, list):
                raise TypeError("字体还原后的插件配置不是数组")
            updated_plugins: list[dict[str, JsonValue]] = []
            for index, plugin_value in enumerate(updated_plugins_value):
                if not isinstance(plugin_value, dict):
                    raise TypeError(f"字体还原后的第 {index} 个插件不是对象")
                updated_plugins.append(plugin_value)
            replace_plugins_file(
                plugins_path=active_plugins_path,
                data=serialize_plugins_js(updated_plugins),
                temp_dir=active_plugins_path.parent,
            )
            restored_field_count += field_count
            restored_reference_count += reference_count

    return OriginFontRestoreSummary(
        target_font_names=target_font_names,
        restored_field_count=restored_field_count,
        restored_reference_count=restored_reference_count,
    )


def normalize_font_name_list(font_names: list[str]) -> list[str]:
    """清理字体名列表并保持稳定去重顺序。"""
    normalized_names: list[str] = []
    seen_names: set[str] = set()
    for font_name in font_names:
        normalized_name = font_name.strip()
        if not normalized_name or normalized_name in seen_names:
            continue
        normalized_names.append(normalized_name)
        seen_names.add(normalized_name)
    return normalized_names


def read_json_value_file(file_path: Path) -> JsonValue:
    """读取 JSON 文件并收窄为项目 JSON 值。"""
    raw_text = file_path.read_text(encoding="utf-8")
    decoded = cast(object, json.loads(raw_text))
    return coerce_json_value(decoded)


def read_plugins_js_file(file_path: Path) -> list[dict[str, JsonValue]]:
    """读取并解析 RPG Maker MZ 的 `plugins.js`。"""
    plugins_text = file_path.read_text(encoding="utf-8")
    match = PLUGINS_JS_PATTERN.search(plugins_text)
    if match is None:
        raise ValueError(f"plugins.js 中未找到标准 $plugins 数组: {file_path}")
    decoded = coerce_json_value(demjson3.decode(match.group(1)))
    if not isinstance(decoded, list):
        raise ValueError(f"plugins.js 顶层不是数组: {file_path}")
    plugins: list[dict[str, JsonValue]] = []
    for index, plugin_value in enumerate(decoded):
        if not isinstance(plugin_value, dict):
            raise TypeError(f"plugins.js 第 {index} 个插件不是对象: {file_path}")
        plugins.append(plugin_value)
    return plugins


def restore_font_references_in_json_value_by_origin(
    *,
    active_value: JsonValue,
    origin_value: JsonValue,
    target_font_names: list[str],
) -> tuple[JsonValue, int, int]:
    """递归对比同路径 JSON 值，并只还原字符串里的字体引用。"""
    if isinstance(active_value, str) and isinstance(origin_value, str):
        restored_text, reference_count = restore_font_references_in_text_by_origin(
            active_text=active_value,
            origin_text=origin_value,
            target_font_names=target_font_names,
        )
        return restored_text, 1 if reference_count > 0 else 0, reference_count

    if isinstance(active_value, list) and isinstance(origin_value, list):
        restored_items: list[JsonValue] = []
        restored_field_count = 0
        restored_reference_count = 0
        for index, active_item in enumerate(active_value):
            if index >= len(origin_value):
                restored_items.append(active_item)
                continue
            restored_item, field_count, reference_count = restore_font_references_in_json_value_by_origin(
                active_value=active_item,
                origin_value=origin_value[index],
                target_font_names=target_font_names,
            )
            restored_items.append(restored_item)
            restored_field_count += field_count
            restored_reference_count += reference_count
        return restored_items, restored_field_count, restored_reference_count

    if isinstance(active_value, dict) and isinstance(origin_value, dict):
        restored_object: JsonObject = {}
        restored_field_count = 0
        restored_reference_count = 0
        for key, active_item in active_value.items():
            if key not in origin_value:
                restored_object[key] = active_item
                continue
            restored_item, field_count, reference_count = restore_font_references_in_json_value_by_origin(
                active_value=active_item,
                origin_value=origin_value[key],
                target_font_names=target_font_names,
            )
            restored_object[key] = restored_item
            restored_field_count += field_count
            restored_reference_count += reference_count
        return restored_object, restored_field_count, restored_reference_count

    return active_value, 0, 0


def restore_font_references_in_text_by_origin(
    *,
    active_text: str,
    origin_text: str,
    target_font_names: list[str],
) -> tuple[str, int]:
    """只在完整字体引用位置按原件留档还原字体名。"""
    restored_text, reference_count = restore_complete_font_reference_text(
        active_text=active_text,
        origin_text=origin_text,
        target_font_names=target_font_names,
    )
    if reference_count:
        return restored_text, reference_count
    return restore_font_references_in_encoded_json_text(
        active_text=active_text,
        origin_text=origin_text,
        target_font_names=target_font_names,
    )


def restore_complete_font_reference_text(
    *,
    active_text: str,
    origin_text: str,
    target_font_names: list[str],
) -> tuple[str, int]:
    """还原整个字符串就是覆盖字体引用的字段。"""
    stripped_active_text = active_text.strip()
    if not stripped_active_text:
        return active_text, 0
    origin_font_reference = collect_origin_font_reference(origin_text)
    if origin_font_reference is None:
        return active_text, 0

    leading_text = active_text[:len(active_text) - len(active_text.lstrip())]
    trailing_text = active_text[len(active_text.rstrip()):]
    for target_font_name in target_font_names:
        if not is_complete_reference_to_font(
            text=stripped_active_text,
            font_name=target_font_name,
        ):
            continue
        return f"{leading_text}{origin_font_reference}{trailing_text}", 1
    return active_text, 0


def restore_font_references_in_encoded_json_text(
    *,
    active_text: str,
    origin_text: str,
    target_font_names: list[str],
) -> tuple[str, int]:
    """还原插件参数 JSON 字符串内部的完整字体引用。"""
    try:
        active_raw_value = cast(object, json.loads(active_text))
        origin_raw_value = cast(object, json.loads(origin_text))
        active_value = coerce_json_value(active_raw_value)
        origin_value = coerce_json_value(origin_raw_value)
    except (json.JSONDecodeError, TypeError):
        return active_text, 0

    if not isinstance(active_value, list | dict) or not isinstance(origin_value, list | dict):
        return active_text, 0

    restored_value, _field_count, reference_count = restore_font_references_in_json_value_by_origin(
        active_value=active_value,
        origin_value=origin_value,
        target_font_names=target_font_names,
    )
    if reference_count == 0:
        return active_text, 0
    return json.dumps(restored_value, ensure_ascii=False), reference_count


def collect_origin_font_reference(text: str) -> str | None:
    """从原文本字段中读取完整旧字体引用。"""
    stripped_text = text.strip()
    if not stripped_text:
        return None
    if is_complete_font_file_reference(stripped_text):
        return stripped_text
    if is_complete_bare_font_reference(stripped_text):
        return stripped_text
    return None


def is_complete_reference_to_font(*, text: str, font_name: str) -> bool:
    """判断当前字符串是否完整指向指定字体名。"""
    if text == font_name:
        return True
    reference_name = extract_font_reference_name(text)
    return reference_name == font_name


def is_complete_font_file_reference(text: str) -> bool:
    """判断字符串是否是完整字体文件引用。"""
    reference_name = extract_font_reference_name(text)
    return FONT_FILE_REFERENCE_PATTERN.fullmatch(reference_name) is not None


def is_complete_bare_font_reference(text: str) -> bool:
    """判断字符串是否是完整无扩展名字体引用。"""
    reference_name = extract_font_reference_name(text)
    return BARE_FONT_REFERENCE_PATTERN.fullmatch(reference_name) is not None


def extract_font_reference_name(text: str) -> str:
    """取出带目录字体引用末尾的字体名。"""
    separator_index = max(text.rfind("/"), text.rfind("\\"))
    if separator_index < 0:
        return text
    return text[separator_index + 1:]


def serialize_plugins_js(plugins: list[dict[str, JsonValue]]) -> str:
    """序列化插件配置为 RPG Maker MZ 使用的 JavaScript 文本。"""
    plugins_text = json.dumps(plugins, ensure_ascii=False, indent=2)
    return f"var $plugins = {plugins_text};\n"


__all__ = [
    "FontReplacementSummary",
    "OriginFontRestoreSummary",
    "apply_font_replacement",
    "build_font_reference_tokens",
    "build_empty_font_replacement_summary",
    "collect_replacement_font_names",
    "collect_existing_font_names",
    "replace_font_names_in_json_value",
    "resolve_replacement_font_path",
    "restore_font_references_from_origin_backups",
]
