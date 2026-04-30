"""写回阶段字体替换服务。"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.rmmz.schema import GameData, PLUGINS_FILE_NAME
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


def apply_font_replacement(
    *,
    game_data: GameData,
    game_root: Path,
    replacement_font_path: str | None,
) -> FontReplacementSummary:
    """复制目标字体，并把即将写出的文件引用切换到目标字体。"""
    if replacement_font_path is None or not replacement_font_path.strip():
        return FontReplacementSummary(
            target_font_name=None,
            source_font_count=0,
            replaced_reference_count=0,
            copied=False,
        )

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
    replaced_reference_count = replace_font_references(
        game_data=game_data,
        old_font_names=old_font_names,
        replacement_font_name=target_font_name,
    )
    return FontReplacementSummary(
        target_font_name=target_font_name,
        source_font_count=len(old_font_names),
        replaced_reference_count=replaced_reference_count,
        copied=True,
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
) -> int:
    """在本轮可写数据中替换旧字体文件名。"""
    old_font_reference_tokens = build_font_reference_tokens(old_font_names)
    if not old_font_reference_tokens:
        return 0

    replaced_count = 0
    for file_name, writable_value in list(game_data.writable_data.items()):
        if file_name == PLUGINS_FILE_NAME:
            continue
        updated_value, count = replace_font_names_in_json_value(
            value=writable_value,
            old_font_names=old_font_reference_tokens,
            replacement_font_name=replacement_font_name,
        )
        if count:
            game_data.writable_data[file_name] = updated_value
            replaced_count += count

    updated_plugins, plugin_count = replace_font_names_in_plugins(
        plugins=game_data.writable_plugins_js,
        old_font_names=old_font_reference_tokens,
        replacement_font_name=replacement_font_name,
    )
    if plugin_count:
        game_data.writable_plugins_js = updated_plugins
        game_data.writable_data[PLUGINS_FILE_NAME] = serialize_plugins_js(updated_plugins)
        replaced_count += plugin_count

    return replaced_count


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


def serialize_plugins_js(plugins: list[dict[str, JsonValue]]) -> str:
    """序列化插件配置为 RPG Maker MZ 使用的 JavaScript 文本。"""
    plugins_text = json.dumps(plugins, ensure_ascii=False, indent=2)
    return f"var $plugins = {plugins_text};\n"


__all__ = [
    "FontReplacementSummary",
    "apply_font_replacement",
    "build_font_reference_tokens",
    "collect_existing_font_names",
    "replace_font_names_in_json_value",
    "resolve_replacement_font_path",
]
