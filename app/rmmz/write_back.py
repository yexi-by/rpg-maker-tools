"""
正文回写模块。

负责将 `TranslationItem` 的翻译结果写回 `GameData.writable_data`。这一层只操作
标准 RMMZ 数据与外部规则命中的事件指令参数，不处理任何非标准 JSON 文件。
"""

import json
from typing import cast

from app.rmmz.schema import (
    Code,
    COMMON_EVENTS_FILE_NAME,
    GameData,
    MAP_PATTERN,
    PLUGINS_FILE_NAME,
    SYSTEM_FILE_NAME,
    TROOPS_FILE_NAME,
    TranslationItem,
)
from app.rmmz.text_rules import JsonArray, JsonObject, JsonValue, TextRules, coerce_json_value, ensure_json_array, ensure_json_object
from app.translation.line_wrap import split_overwide_lines


def write_data_text(game_data: GameData, items: list[TranslationItem], text_rules: TextRules | None = None) -> None:
    """将最终翻译文本写回 `data/` 目录游戏数据的内存副本。"""
    command_items: list[TranslationItem] = []
    for item in items:
        file_name = item.location_path.split("/")[0]
        if file_name == PLUGINS_FILE_NAME:
            continue
        if file_name == SYSTEM_FILE_NAME:
            _write_system_item(game_data=game_data, item=item)
            continue
        if MAP_PATTERN.fullmatch(file_name) or file_name in {COMMON_EVENTS_FILE_NAME, TROOPS_FILE_NAME}:
            command_items.append(item)
            continue
        _write_base_item(game_data=game_data, item=item)

    for item in sorted(command_items, key=_command_item_sort_key, reverse=True):
        _write_command_item(game_data=game_data, item=item, text_rules=text_rules)


def _command_item_sort_key(item: TranslationItem) -> tuple[str, tuple[int, ...], int]:
    """按事件指令定位生成倒序写回键，避免插入新行顶偏后续条目。"""
    anchor_path = item.location_path
    if item.item_type == "long_text" and item.source_line_paths:
        anchor_path = item.source_line_paths[-1]

    parts = anchor_path.split("/")
    file_name = parts[0]
    if MAP_PATTERN.fullmatch(file_name):
        return file_name, (int(parts[1]), int(parts[2])), int(parts[3])
    if file_name == COMMON_EVENTS_FILE_NAME:
        return file_name, (int(parts[1]),), int(parts[2])
    if file_name == TROOPS_FILE_NAME:
        return file_name, (int(parts[1]), int(parts[2])), int(parts[3])
    raise ValueError(f"无法识别的事件定位路径: {anchor_path}")


def _write_command_item(game_data: GameData, item: TranslationItem, text_rules: TextRules | None) -> None:
    """将事件指令相关译文写回数据副本。"""
    commands, command_index = _locate_commands(
        writable_data=game_data.writable_data,
        location_path=item.location_path,
    )
    command = commands[command_index]

    if item.item_type == "short_text":
        _write_event_command_text_item(command=command, item=item)
        return

    if item.item_type == "long_text":
        command_code = command.get("code")
        if command_code == Code.NAME:
            _write_line_commands_by_paths(
                game_data=game_data,
                item=item,
                expected_code=Code.TEXT,
                text_rules=text_rules,
            )
            return

        if command_code == Code.SCROLL_TEXT:
            _write_line_commands_by_paths(
                game_data=game_data,
                item=item,
                expected_code=Code.SCROLL_TEXT,
                text_rules=text_rules,
            )
            return

        raise RuntimeError(f"无法识别的 long_text 指令类型: {item.location_path}")

    if item.item_type == "array":
        if command.get("code") != Code.CHOICES:
            raise RuntimeError(f"路径 {item.location_path} 不是 CHOICES 指令")
        parameters = _ensure_command_parameters(command, item.location_path)
        if parameters:
            parameters[0] = list(item.translation_lines)
        else:
            parameters.append(list(item.translation_lines))
        return

    raise ValueError(f"事件指令 item_type 无法处理: {item.item_type}")


def _write_line_commands_by_paths(
    *,
    game_data: GameData,
    item: TranslationItem,
    expected_code: Code,
    text_rules: TextRules | None,
) -> None:
    """按提取路径写回长文本，并为额外译文行插入事件指令。"""
    if not item.source_line_paths:
        raise ValueError(f"长文本缺少逐行写回路径: {item.location_path}")

    existing_line_count = len(item.source_line_paths)
    padded_translation_lines = _prepare_long_text_write_lines(
        item=item,
        text_rules=text_rules,
    )
    if len(padded_translation_lines) < existing_line_count:
        padded_translation_lines.extend([""] * (existing_line_count - len(padded_translation_lines)))

    for source_line_path, translated_text in zip(
        item.source_line_paths,
        padded_translation_lines[:existing_line_count],
        strict=True,
    ):
        commands, command_index = _locate_commands(
            writable_data=game_data.writable_data,
            location_path=source_line_path,
        )
        target_command = commands[command_index]
        if target_command.get("code") != expected_code:
            raise RuntimeError(
                f"逐行路径指向的指令类型错误: {source_line_path}"
            )
        _write_first_parameter(target_command, translated_text)

    extra_lines = padded_translation_lines[existing_line_count:]
    if not extra_lines:
        return

    _insert_extra_line_commands(
        writable_data=game_data.writable_data,
        item=item,
        expected_code=expected_code,
        extra_lines=extra_lines,
    )


def _prepare_long_text_write_lines(
    *,
    item: TranslationItem,
    text_rules: TextRules | None,
) -> list[str]:
    """在写回前按当前配置再次执行长文本行宽兜底。"""
    if text_rules is None:
        return list(item.translation_lines)
    return split_overwide_lines(
        lines=list(item.translation_lines),
        location_path=item.location_path,
        text_rules=text_rules,
    )


def _insert_extra_line_commands(
    *,
    writable_data: dict[str, JsonValue],
    item: TranslationItem,
    expected_code: Code,
    extra_lines: list[str],
) -> None:
    """把模型额外输出的长文本行插入原事件列表。"""
    _ensure_source_paths_share_command_list(item.source_line_paths, item.location_path)
    last_source_path = item.source_line_paths[-1]
    command_array, command_index = _locate_command_array(
        writable_data=writable_data,
        location_path=last_source_path,
    )
    base_command = ensure_json_object(command_array[command_index], last_source_path)
    if base_command.get("code") != expected_code:
        raise RuntimeError(f"额外行插入锚点指令类型错误: {last_source_path}")

    for offset, translated_text in enumerate(extra_lines, start=1):
        command_array.insert(
            command_index + offset,
            _build_text_line_command(
                expected_code=expected_code,
                translated_text=translated_text,
                base_command=base_command,
            ),
        )


def _build_text_line_command(
    *,
    expected_code: Code,
    translated_text: str,
    base_command: JsonObject,
) -> JsonObject:
    """基于同块原始指令构造新增 401/405 指令。"""
    command: JsonObject = {
        "code": int(expected_code),
        "parameters": [translated_text],
    }
    indent = base_command.get("indent")
    if isinstance(indent, int) and not isinstance(indent, bool):
        command["indent"] = indent
    return command


def _ensure_source_paths_share_command_list(source_line_paths: list[str], location_path: str) -> None:
    """确保一个长文本条目的逐行路径来自同一事件列表。"""
    parent_paths = {_command_list_parent_path(path) for path in source_line_paths}
    if len(parent_paths) != 1:
        raise ValueError(f"长文本逐行路径跨事件列表，无法插入额外行: {location_path}")


def _write_event_command_text_item(command: JsonObject, item: TranslationItem) -> None:
    """将外部规则命中的事件指令短文本译文写回参数容器。"""
    path_parts = _extract_command_value_path_parts(item.location_path)
    if len(path_parts) < 3 or path_parts[0] != "parameters":
        raise ValueError(f"事件指令路径缺少 parameters 段: {item.location_path}")

    parameters = _ensure_command_parameters(command, item.location_path)
    param_index = int(path_parts[1])
    if param_index >= len(parameters):
        raise ValueError(f"事件指令参数索引越界: {item.location_path}")

    translated_text = item.translation_lines[0] if item.translation_lines else ""
    parameters[param_index] = _set_event_command_value(
        current_value=parameters[param_index],
        path_parts=path_parts[2:],
        translated_text=translated_text,
    )


def _locate_commands(
    *,
    writable_data: dict[str, JsonValue],
    location_path: str,
) -> tuple[list[JsonObject], int]:
    """根据 `location_path` 定位到具体 RM Event List 数组。"""
    parts = location_path.split("/")
    file_name = parts[0]
    data = writable_data[file_name]

    if MAP_PATTERN.fullmatch(file_name):
        map_object = ensure_json_object(data, file_name)
        events = ensure_json_array(map_object["events"], f"{file_name}.events")
        event_id = int(parts[1])
        event = ensure_json_object(events[event_id], item_context(location_path, "event"))
        pages = ensure_json_array(event["pages"], item_context(location_path, "pages"))
        page = ensure_json_object(pages[int(parts[2])], item_context(location_path, "page"))
        commands = _ensure_command_list(page["list"], item_context(location_path, "list"))
        return commands, int(parts[3])

    if file_name == COMMON_EVENTS_FILE_NAME:
        events = ensure_json_array(data, file_name)
        event = ensure_json_object(events[int(parts[1])], item_context(location_path, "event"))
        commands = _ensure_command_list(event["list"], item_context(location_path, "list"))
        return commands, int(parts[2])

    if file_name == TROOPS_FILE_NAME:
        troops = ensure_json_array(data, file_name)
        troop = ensure_json_object(troops[int(parts[1])], item_context(location_path, "troop"))
        pages = ensure_json_array(troop["pages"], item_context(location_path, "pages"))
        page = ensure_json_object(pages[int(parts[2])], item_context(location_path, "page"))
        commands = _ensure_command_list(page["list"], item_context(location_path, "list"))
        return commands, int(parts[3])

    raise ValueError(f"无法识别的事件定位路径: {location_path}")


def _locate_command_array(
    *,
    writable_data: dict[str, JsonValue],
    location_path: str,
) -> tuple[JsonArray, int]:
    """根据 `location_path` 定位到原始 RM Event List 数组。"""
    parts = location_path.split("/")
    file_name = parts[0]
    data = writable_data[file_name]

    if MAP_PATTERN.fullmatch(file_name):
        map_object = ensure_json_object(data, file_name)
        events = ensure_json_array(map_object["events"], f"{file_name}.events")
        event = ensure_json_object(events[int(parts[1])], item_context(location_path, "event"))
        pages = ensure_json_array(event["pages"], item_context(location_path, "pages"))
        page = ensure_json_object(pages[int(parts[2])], item_context(location_path, "page"))
        return ensure_json_array(page["list"], item_context(location_path, "list")), int(parts[3])

    if file_name == COMMON_EVENTS_FILE_NAME:
        events = ensure_json_array(data, file_name)
        event = ensure_json_object(events[int(parts[1])], item_context(location_path, "event"))
        return ensure_json_array(event["list"], item_context(location_path, "list")), int(parts[2])

    if file_name == TROOPS_FILE_NAME:
        troops = ensure_json_array(data, file_name)
        troop = ensure_json_object(troops[int(parts[1])], item_context(location_path, "troop"))
        pages = ensure_json_array(troop["pages"], item_context(location_path, "pages"))
        page = ensure_json_object(pages[int(parts[2])], item_context(location_path, "page"))
        return ensure_json_array(page["list"], item_context(location_path, "list")), int(parts[3])

    raise ValueError(f"无法识别的事件定位路径: {location_path}")


def _command_list_parent_path(location_path: str) -> tuple[str, ...]:
    """返回事件指令所在列表的路径前缀。"""
    parts = location_path.split("/")
    file_name = parts[0]
    if MAP_PATTERN.fullmatch(file_name):
        return tuple(parts[:3])
    if file_name == COMMON_EVENTS_FILE_NAME:
        return tuple(parts[:2])
    if file_name == TROOPS_FILE_NAME:
        return tuple(parts[:3])
    raise ValueError(f"无法识别的事件定位路径: {location_path}")


def _extract_command_value_path_parts(location_path: str) -> list[str]:
    """从完整 `location_path` 中拆出命令后的值路径尾段。"""
    parts = location_path.split("/")
    file_name = parts[0]
    if MAP_PATTERN.fullmatch(file_name):
        return parts[4:]
    if file_name == COMMON_EVENTS_FILE_NAME:
        return parts[3:]
    if file_name == TROOPS_FILE_NAME:
        return parts[4:]
    raise ValueError(f"无法识别的事件值路径: {location_path}")


def _set_event_command_value(
    *,
    current_value: JsonValue,
    path_parts: list[str],
    translated_text: str,
) -> JsonValue:
    """按路径深入事件指令参数容器，并替换最终字符串叶子。"""
    if not path_parts:
        if not isinstance(current_value, str):
            raise ValueError("事件指令路径没有指向字符串叶子")
        return translated_text

    key = path_parts[0]
    remain_parts = path_parts[1:]
    if isinstance(current_value, dict):
        if key not in current_value:
            raise ValueError(f"事件指令参数键不存在: {key}")
        current_value[key] = _set_event_command_value(
            current_value=current_value[key],
            path_parts=remain_parts,
            translated_text=translated_text,
        )
        return current_value

    if isinstance(current_value, list):
        index = int(key)
        if index >= len(current_value):
            raise ValueError(f"事件指令参数索引越界: {index}")
        current_value[index] = _set_event_command_value(
            current_value=current_value[index],
            path_parts=remain_parts,
            translated_text=translated_text,
        )
        return current_value

    if isinstance(current_value, str):
        parsed_container = _try_parse_container_text(current_value)
        if parsed_container is None:
            raise ValueError(f"事件指令路径无法继续下钻: {path_parts}")
        updated_value = _set_event_command_value(
            current_value=parsed_container,
            path_parts=path_parts,
            translated_text=translated_text,
        )
        return json.dumps(updated_value, ensure_ascii=False)

    raise ValueError(f"事件指令路径无法继续下钻: {path_parts}")


def _try_parse_container_text(value: str) -> dict[str, JsonValue] | list[JsonValue] | None:
    """尝试将字符串反序列化为 JSON 容器。"""
    try:
        decoded = cast(object, json.loads(value))
        parsed = coerce_json_value(decoded)
    except (json.JSONDecodeError, TypeError):
        return None

    if isinstance(parsed, dict | list):
        return parsed
    return None


def _write_first_parameter(command: JsonObject, text: str) -> None:
    """将文本写入指令的第一个参数位。"""
    parameters = command.get("parameters")
    if not isinstance(parameters, list):
        command["parameters"] = [text]
        return
    if parameters:
        parameters[0] = text
    else:
        parameters.append(text)


def _write_system_item(game_data: GameData, item: TranslationItem) -> None:
    """将 `System.json` 文本写回数据副本。"""
    parts = item.location_path.split("/")
    system_data = ensure_json_object(game_data.writable_data[SYSTEM_FILE_NAME], SYSTEM_FILE_NAME)
    translated_text = item.translation_lines[0] if item.translation_lines else ""

    if len(parts) == 2:
        system_data[parts[1]] = translated_text
        return

    if len(parts) == 3:
        key = parts[1]
        if key in {"variables", "switches"}:
            return
        target_list = ensure_json_array(system_data[key], item.location_path)
        target_list[int(parts[2])] = translated_text
        return

    if len(parts) == 4 and parts[1] == "terms" and parts[2] == "messages":
        terms = ensure_json_object(system_data["terms"], f"{SYSTEM_FILE_NAME}.terms")
        messages = ensure_json_object(terms["messages"], f"{SYSTEM_FILE_NAME}.terms.messages")
        messages[parts[3]] = translated_text
        return

    if len(parts) == 4 and parts[1] == "terms":
        terms = ensure_json_object(system_data["terms"], f"{SYSTEM_FILE_NAME}.terms")
        target_list = ensure_json_array(terms[parts[2]], item.location_path)
        target_list[int(parts[3])] = translated_text
        return

    raise ValueError(f"无法识别的 System 路径: {item.location_path}")


def _write_base_item(game_data: GameData, item: TranslationItem) -> None:
    """将基础数据库条目文本写回数据副本。"""
    parts = item.location_path.split("/")
    file_name = parts[0]
    item_id = int(parts[1])
    key = parts[2]
    translated_text = item.translation_lines[0] if item.translation_lines else ""
    data = ensure_json_array(game_data.writable_data[file_name], file_name)

    if item_id < len(data):
        target = data[item_id]
        if isinstance(target, dict) and target.get("id") == item_id:
            target[key] = translated_text
            return

    for target_value in data:
        if not isinstance(target_value, dict):
            continue
        if target_value.get("id") != item_id:
            continue
        target_value[key] = translated_text
        return

    raise ValueError(f"基础数据库条目不存在: {item.location_path}")


def _ensure_command_list(value: JsonValue, context: str) -> list[JsonObject]:
    """把 JSON 值收窄为事件命令对象列表。"""
    raw_commands = ensure_json_array(value, context)
    commands: list[JsonObject] = []
    for index, command in enumerate(raw_commands):
        commands.append(ensure_json_object(command, f"{context}[{index}]"))
    return commands


def _ensure_command_parameters(command: JsonObject, location_path: str) -> JsonArray:
    """读取事件命令 parameters 数组。"""
    parameters = command.get("parameters")
    if not isinstance(parameters, list):
        raise ValueError(f"事件指令 parameters 不是数组: {location_path}")
    return parameters


def item_context(location_path: str, label: str) -> str:
    """生成写回定位错误上下文。"""
    return f"{location_path}.{label}"


__all__: list[str] = ["write_data_text"]
