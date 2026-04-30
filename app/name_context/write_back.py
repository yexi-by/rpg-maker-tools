"""标准名写回模块。"""

from app.rmmz.schema import (
    COMMON_EVENTS_FILE_NAME,
    Code,
    GameData,
    MAP_PATTERN,
    TROOPS_FILE_NAME,
)
from app.rmmz.text_rules import JsonArray, JsonObject, JsonValue, ensure_json_array, ensure_json_object

from .schemas import NameContextRegistry, NameRegistryEntry


def apply_name_context_translations(game_data: GameData, registry: NameContextRegistry) -> int:
    """把外部标准名译文写入 `101` 名字框和 `MapXXX.displayName`。"""
    written_count = 0
    for entry in registry.entries:
        translated_text = entry.translated_text.strip()
        if not translated_text:
            continue
        if entry.kind == "map_display_name":
            written_count += _write_map_display_name(game_data=game_data, entry=entry, translated_text=translated_text)
            continue
        if entry.kind == "speaker_name":
            written_count += _write_speaker_name(game_data=game_data, entry=entry, translated_text=translated_text)
    return written_count


def _write_map_display_name(
    *,
    game_data: GameData,
    entry: NameRegistryEntry,
    translated_text: str,
) -> int:
    """写回单条地图显示名。"""
    written_count = 0
    for location in entry.locations:
        parts = location.location_path.split("/")
        if len(parts) != 2 or parts[1] != "displayName":
            raise ValueError(f"地图名位置路径非法: {location.location_path}")
        file_name = parts[0]
        if MAP_PATTERN.fullmatch(file_name) is None:
            raise ValueError(f"地图名位置不是 MapXXX.json: {location.location_path}")
        map_object = ensure_json_object(game_data.writable_data[file_name], file_name)
        map_object["displayName"] = translated_text
        written_count += 1
    return written_count


def _write_speaker_name(
    *,
    game_data: GameData,
    entry: NameRegistryEntry,
    translated_text: str,
) -> int:
    """写回单条或多条 `101.parameters[4]` 名字框。"""
    written_count = 0
    for location in entry.locations:
        command = _locate_event_command(
            writable_data=game_data.writable_data,
            location_path=location.location_path,
        )
        if command.get("code") != Code.NAME:
            raise ValueError(f"标准名位置不是 Code 101 名字框: {location.location_path}")
        parameters = _ensure_command_parameters(command, location.location_path)
        while len(parameters) <= 4:
            parameters.append("")
        parameters[4] = translated_text
        written_count += 1
    return written_count


def _locate_event_command(*, writable_data: dict[str, JsonValue], location_path: str) -> JsonObject:
    """根据标准名位置路径定位事件命令。"""
    parts = location_path.split("/")
    if not parts:
        raise ValueError("标准名位置路径为空")
    file_name = parts[0]

    if MAP_PATTERN.fullmatch(file_name):
        if len(parts) != 4:
            raise ValueError(f"地图 101 位置路径非法: {location_path}")
        map_object = ensure_json_object(writable_data[file_name], file_name)
        events = ensure_json_array(map_object["events"], f"{file_name}.events")
        event = ensure_json_object(events[int(parts[1])], f"{location_path}.event")
        pages = ensure_json_array(event["pages"], f"{location_path}.pages")
        page = ensure_json_object(pages[int(parts[2])], f"{location_path}.page")
        commands = _ensure_command_list(page["list"], f"{location_path}.list")
        return commands[int(parts[3])]

    if file_name == COMMON_EVENTS_FILE_NAME:
        if len(parts) != 3:
            raise ValueError(f"公共事件 101 位置路径非法: {location_path}")
        events = ensure_json_array(writable_data[file_name], file_name)
        event = ensure_json_object(events[int(parts[1])], f"{location_path}.event")
        commands = _ensure_command_list(event["list"], f"{location_path}.list")
        return commands[int(parts[2])]

    if file_name == TROOPS_FILE_NAME:
        if len(parts) != 4:
            raise ValueError(f"敌群 101 位置路径非法: {location_path}")
        troops = ensure_json_array(writable_data[file_name], file_name)
        troop = ensure_json_object(troops[int(parts[1])], f"{location_path}.troop")
        pages = ensure_json_array(troop["pages"], f"{location_path}.pages")
        page = ensure_json_object(pages[int(parts[2])], f"{location_path}.page")
        commands = _ensure_command_list(page["list"], f"{location_path}.list")
        return commands[int(parts[3])]

    raise ValueError(f"未知的 101 位置路径: {location_path}")


def _ensure_command_list(value: JsonValue, context: str) -> list[JsonObject]:
    """把 JSON 数组收窄为事件命令对象列表。"""
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


__all__: list[str] = ["apply_name_context_translations"]
