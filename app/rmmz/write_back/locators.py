"""RPG Maker 事件指令和 JSON 路径定位工具。"""

from app.rmmz.schema import (
    COMMON_EVENTS_FILE_NAME,
    MAP_PATTERN,
    TROOPS_FILE_NAME,
)
from app.rmmz.text_protocol import (
    decode_json_container_text,
    encode_json_container_like,
    encode_visible_text_like,
    ensure_encoded_text_valid,
)
from app.rmmz.text_rules import JsonArray, JsonObject, JsonValue, ensure_json_array, ensure_json_object


def locate_commands(
    *,
    writable_data: dict[str, JsonValue],
    location_path: str,
) -> tuple[JsonArray, int]:
    """根据定位路径找到具体 RPG Maker 事件指令数组。"""
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
        commands = ensure_json_array(page["list"], item_context(location_path, "list"))
        return commands, int(parts[3])

    if file_name == COMMON_EVENTS_FILE_NAME:
        events = ensure_json_array(data, file_name)
        event = ensure_json_object(events[int(parts[1])], item_context(location_path, "event"))
        commands = ensure_json_array(event["list"], item_context(location_path, "list"))
        return commands, int(parts[2])

    if file_name == TROOPS_FILE_NAME:
        troops = ensure_json_array(data, file_name)
        troop = ensure_json_object(troops[int(parts[1])], item_context(location_path, "troop"))
        pages = ensure_json_array(troop["pages"], item_context(location_path, "pages"))
        page = ensure_json_object(pages[int(parts[2])], item_context(location_path, "page"))
        commands = ensure_json_array(page["list"], item_context(location_path, "list"))
        return commands, int(parts[3])

    raise ValueError(f"无法识别的事件定位路径: {location_path}")


def locate_command_array(
    *,
    writable_data: dict[str, JsonValue],
    location_path: str,
) -> tuple[JsonArray, int]:
    """根据定位路径找到原始 RPG Maker 事件指令数组。"""
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


def command_list_parent_path(location_path: str) -> tuple[str, ...]:
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


def extract_command_value_path_parts(location_path: str) -> list[str]:
    """从完整定位路径中拆出命令后的值路径尾段。"""
    parts = location_path.split("/")
    file_name = parts[0]
    if MAP_PATTERN.fullmatch(file_name):
        return parts[4:]
    if file_name == COMMON_EVENTS_FILE_NAME:
        return parts[3:]
    if file_name == TROOPS_FILE_NAME:
        return parts[4:]
    raise ValueError(f"无法识别的事件值路径: {location_path}")


def set_event_command_value(
    *,
    current_value: JsonValue,
    path_parts: list[str],
    translated_text: str,
    context: str,
) -> JsonValue:
    """按路径深入事件指令参数容器，并替换最终字符串叶子。"""
    if not path_parts:
        if not isinstance(current_value, str):
            raise ValueError("事件指令路径没有指向字符串叶子")
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
            raise ValueError(f"事件指令参数键不存在: {key}")
        current_value[key] = set_event_command_value(
            current_value=current_value[key],
            path_parts=remain_parts,
            translated_text=translated_text,
            context=context,
        )
        return current_value

    if isinstance(current_value, list):
        index = int(key)
        if index >= len(current_value):
            raise ValueError(f"事件指令参数索引越界: {index}")
        current_value[index] = set_event_command_value(
            current_value=current_value[index],
            path_parts=remain_parts,
            translated_text=translated_text,
            context=context,
        )
        return current_value

    if isinstance(current_value, str):
        parsed_container = try_parse_container_text(current_value)
        if parsed_container is None:
            raise ValueError(f"事件指令路径无法继续下钻: {path_parts}")
        updated_value = set_event_command_value(
            current_value=parsed_container,
            path_parts=path_parts,
            translated_text=translated_text,
            context=context,
        )
        if not isinstance(updated_value, dict | list):
            raise ValueError("事件指令 JSON 容器写入结果不是数组或对象")
        return encode_json_container_like(
            original_raw_text=current_value,
            updated_value=updated_value,
        )

    raise ValueError(f"事件指令路径无法继续下钻: {path_parts}")


def try_parse_container_text(value: str) -> dict[str, JsonValue] | list[JsonValue] | None:
    """尝试将字符串反序列化为 JSON 容器。"""
    decoded = decode_json_container_text(value)
    if decoded is None:
        return None
    return decoded.value


def write_first_parameter(command: JsonObject, text: str) -> None:
    """将文本写入指令的第一个参数位。"""
    parameters = command.get("parameters")
    if not isinstance(parameters, list):
        command["parameters"] = [text]
        return
    if parameters:
        parameters[0] = text
    else:
        parameters.append(text)


def find_json_object_by_id(values: JsonArray, item_id: int) -> JsonObject | None:
    """在数组中按 id 字段查找对象，支持索引与 id 不一致的数据。"""
    for value in values:
        if not isinstance(value, dict):
            continue
        if value.get("id") == item_id:
            return value
    return None


def ensure_command_parameters(command: JsonObject, location_path: str) -> JsonArray:
    """读取事件命令 parameters 数组。"""
    parameters = command.get("parameters")
    if not isinstance(parameters, list):
        raise ValueError(f"事件指令 parameters 不是数组: {location_path}")
    return parameters


def item_context(location_path: str, label: str) -> str:
    """生成写入定位错误上下文。"""
    return f"{location_path}.{label}"
