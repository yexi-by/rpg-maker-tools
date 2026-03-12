"""
插件正文回写模块。

负责将插件文本翻译结果写回 `GameData.writable_plugins_js`，
并在完成后重新序列化为 `plugins.js` 文本，覆盖到 `GameData.writable_data["plugins.js"]`。
"""

import json
from typing import Any

from app.models.schemas import GameData, PLUGINS_FILE_NAME, TranslationItem


def write_plugin_text(game_data: GameData, items: list[TranslationItem]) -> None:
    """
    将关于 `plugins.js` 的译文写回游戏内存的插件配置副本中，并最终序列化为目标字符串。

    该方法作为回写的最后环节之一，具有以下特点：
    1. 过滤器：由于它与 `write_data_text` 共享同一个 items 数组，因此会跳过非插件路径的条目。
    2. 双态同步：插件数据最初是从 JS 文本被提取出的 JSON，修改完后，必须再次手动序列化回合法的 JS 格式（`var $plugins = [...]`），并挂载到 `writable_data` 字典中，等待终极的磁盘写入。

    Args:
        game_data: 提供全局数据访问的聚合对象，包含需要被修改的 writable_plugins_js。
        items: 全部有效翻译条目的集合。
        
    Raises:
        ValueError: 当遇到格式非法或者无法穿透的插件配置字典时抛出。
    """
    wrote_plugin_item: bool = False

    for item in items:
        parts: list[str] = item.location_path.split("/")
        if not parts or parts[0] != PLUGINS_FILE_NAME:
            continue
        if len(parts) < 3:
            continue

        plugin_index: int = int(parts[1])
        translated_text: str = item.translation_lines[0] if item.translation_lines else ""

        plugin = game_data.writable_plugins_js[plugin_index]
        parameters = plugin.get("parameters")
        if not isinstance(parameters, dict):
            raise ValueError(f"插件参数不是字典: {item.location_path}")

        top_key: str = parts[2]
        if top_key not in parameters:
            raise ValueError(f"插件参数不存在: {item.location_path}")

        parameters[top_key] = _set_plugin_value(
            current_value=parameters[top_key],
            path_parts=parts[3:],
            translated_text=translated_text,
        )

        wrote_plugin_item = True

    if not wrote_plugin_item:
        return

    game_data.writable_data[PLUGINS_FILE_NAME] = _serialize_plugins_js(
        game_data.writable_plugins_js
    )


def _set_plugin_value(
    current_value: Any,
    path_parts: list[str],
    translated_text: str,
) -> Any:
    """
    核心递归方法：循着路径数组深入插件配置内部，并将最深处的叶子节点替换为译文。

    此方法不仅要处理常规嵌套（字典嵌套字典/列表），
    还需要应对 RM 插件中特有的“用字符串伪装容器”（被 JSON.stringify 过的数组或字典）的结构。
    一旦在路径下探过程中发现当前节点是字符串，它会尝试将其解析为 JSON 结构，
    深入替换完毕后，再以 `ensure_ascii=False` 的形式将其重新打包为字符串返回。

    Args:
        current_value: 当前递归深度的实际值。
        path_parts: 尚未消费的、指示后续寻找方向的路径键名数组。
        translated_text: 最终需要替换写入的译文字符串。

    Returns:
        完成深度替换及必要序列化后的新对象，原样返回给上层进行覆盖。
        
    Raises:
        ValueError: 路径指向了非法结构，或者嵌套的序列化字符串损坏时抛出。
    """
    if not path_parts:
        return translated_text

    key: str = path_parts[0]
    remain_parts: list[str] = path_parts[1:]

    if isinstance(current_value, dict):
        current_value[key] = _set_plugin_value(
            current_value=current_value[key],
            path_parts=remain_parts,
            translated_text=translated_text,
        )
        return current_value

    if isinstance(current_value, list):
        index: int = int(key)
        current_value[index] = _set_plugin_value(
            current_value=current_value[index],
            path_parts=remain_parts,
            translated_text=translated_text,
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
        )
        return json.dumps(updated_value, ensure_ascii=False)

    raise ValueError(f"不支持的插件参数类型: {type(current_value).__name__}")


def _try_parse_container_text(value: str) -> dict[str, Any] | list[Any] | None:
    """
    尝试将一个字符串反序列化为 JSON 容器。

    与提取层的同名函数作用一致，用于识别 RM 插件配置中被压缩为字符串的子级配置。

    Args:
        value: 疑似容器的字符串值。

    Returns:
        解析成功的字典或列表；如果是纯普通文本或者解析失败则返回 None。
    """
    try:
        parsed: Any = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None

    if isinstance(parsed, dict) or isinstance(parsed, list):
        return parsed
    return None


def _serialize_plugins_js(plugins_js: list[dict[str, Any]]) -> str:
    """
    根据 RM MZ 的标准格式，将修改完毕的插件对象数组重新打包为合法的 JavaScript 文本。

    Args:
        plugins_js: 完成全部字典内容修改后的最终插件列表结构。

    Returns:
        包含 `var $plugins = ` 前缀的 JavaScript 代码字符串，可以直接写盘。
    """
    plugins_text: str = json.dumps(plugins_js, ensure_ascii=False, indent=2)
    return f"var $plugins = {plugins_text};\n"


__all__: list[str] = ["write_plugin_text"]
