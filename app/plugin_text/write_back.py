"""
插件正文回写模块。

负责将 `plugins.js` 路径规则提取出的译文写回 `GameData.writable_plugins_js`，并重新
序列化为 RPG Maker MZ 标准 `var $plugins = ...;` 文本。
"""

import json

from app.rmmz.schema import GameData, PLUGINS_FILE_NAME, TranslationItem
from app.rmmz.placeholder_guard import ensure_no_internal_placeholder_tokens
from app.rmmz.text_rules import JsonValue
from app.rmmz.text_protocol import (
    decode_json_container_text,
    encode_json_container_like,
    encode_visible_text_like,
    ensure_encoded_text_valid,
)


def write_plugin_text(game_data: GameData, items: list[TranslationItem]) -> None:
    """将关于 `plugins.js` 的译文写回插件配置副本。"""
    wrote_plugin_item = False
    for item in items:
        parts = item.location_path.split("/")
        if not parts or parts[0] != PLUGINS_FILE_NAME:
            continue
        if len(parts) < 3:
            continue
        ensure_no_internal_placeholder_tokens(
            lines=item.translation_lines,
            context=item.location_path,
        )

        plugin_index = int(parts[1])
        translated_text = item.translation_lines[0] if item.translation_lines else ""
        plugin = game_data.writable_plugins_js[plugin_index]
        parameters = plugin.get("parameters")
        if not isinstance(parameters, dict):
            raise ValueError(f"插件参数不是字典: {item.location_path}")

        top_key = parts[2]
        if top_key not in parameters:
            raise ValueError(f"插件参数不存在: {item.location_path}")

        try:
            parameters[top_key] = _set_plugin_value(
                current_value=parameters[top_key],
                path_parts=parts[3:],
                translated_text=translated_text,
                context=item.location_path,
            )
        except ValueError as error:
            raise ValueError(f"{item.location_path}: {error}") from error
        wrote_plugin_item = True

    if not wrote_plugin_item:
        return

    game_data.writable_data[PLUGINS_FILE_NAME] = _serialize_plugins_js(game_data.writable_plugins_js)


def _set_plugin_value(
    *,
    current_value: JsonValue,
    path_parts: list[str],
    translated_text: str,
    context: str,
) -> JsonValue:
    """循着路径深入插件配置内部，并将最深处叶子替换为译文。"""
    if not path_parts:
        if not isinstance(current_value, str):
            raise ValueError("插件路径没有指向字符串叶子")
        written_text = encode_visible_text_like(
            original_raw_text=current_value,
            translated_visible_text=translated_text,
        )
        ensure_encoded_text_valid(
            original_raw_text=current_value,
            written_raw_text=written_text,
            context=context,
        )
        return written_text

    key = path_parts[0]
    remain_parts = path_parts[1:]

    if isinstance(current_value, dict):
        if key not in current_value:
            raise ValueError(f"插件参数键不存在: {key}")
        current_value[key] = _set_plugin_value(
            current_value=current_value[key],
            path_parts=remain_parts,
            translated_text=translated_text,
            context=context,
        )
        return current_value

    if isinstance(current_value, list):
        index = int(key)
        if index >= len(current_value):
            raise ValueError(f"插件参数索引越界: {index}")
        current_value[index] = _set_plugin_value(
            current_value=current_value[index],
            path_parts=remain_parts,
            translated_text=translated_text,
            context=context,
        )
        return current_value

    if isinstance(current_value, str):
        parsed_container = _try_parse_container_text(current_value)
        if parsed_container is None:
            raise ValueError(f"插件路径无法继续下钻: {path_parts}")
        updated_value = _set_plugin_value(
            current_value=parsed_container,
            path_parts=path_parts,
            translated_text=translated_text,
            context=context,
        )
        if not isinstance(updated_value, dict | list):
            raise ValueError("插件 JSON 容器写回结果不是数组或对象")
        return encode_json_container_like(
            original_raw_text=current_value,
            updated_value=updated_value,
        )

    raise ValueError(f"插件参数类型无法处理: {type(current_value).__name__}")


def _try_parse_container_text(value: str) -> dict[str, JsonValue] | list[JsonValue] | None:
    """尝试将字符串反序列化为 JSON 容器。"""
    decoded = decode_json_container_text(value)
    if decoded is None:
        return None
    return decoded.value


def _serialize_plugins_js(plugins_js: list[dict[str, JsonValue]]) -> str:
    """将插件对象数组重新打包为合法 JavaScript 文本。"""
    plugins_text = json.dumps(plugins_js, ensure_ascii=False, indent=2)
    return f"var $plugins = {plugins_text};\n"


__all__: list[str] = ["write_plugin_text"]
