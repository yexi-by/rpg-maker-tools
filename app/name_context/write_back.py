"""标准名术语写回模块。"""

from app.rmmz.schema import (
    COMMON_EVENTS_FILE_NAME,
    Code,
    GameData,
    MAP_PATTERN,
    TROOPS_FILE_NAME,
)
from app.rmmz.text_rules import JsonArray, JsonObject, JsonValue, ensure_json_array, ensure_json_object

from .extraction import is_translatable_name_context_source
from .schemas import NameContextRegistry


def apply_name_context_translations(game_data: GameData, registry: NameContextRegistry) -> int:
    """把术语表译名写入 `101` 名字框和 `MapXXX.displayName`。"""
    written_count = 0
    written_count += _write_map_display_names(game_data=game_data, translations=registry.map_display_names)
    written_count += _write_speaker_names(game_data=game_data, translations=registry.speaker_names)
    return written_count


def _write_map_display_names(*, game_data: GameData, translations: dict[str, str]) -> int:
    """按原地图显示名写回译名。"""
    written_count = 0
    clean_translations = _filled_translations(translations)
    if not clean_translations:
        return 0

    for file_name, data in game_data.writable_data.items():
        if MAP_PATTERN.fullmatch(file_name) is None:
            continue
        map_object = ensure_json_object(data, file_name)
        display_name = map_object.get("displayName")
        if not isinstance(display_name, str):
            continue
        if not is_translatable_name_context_source(display_name):
            continue
        translated_text = clean_translations.get(display_name)
        if translated_text is None:
            continue
        map_object["displayName"] = translated_text
        written_count += 1
    return written_count


def _write_speaker_names(*, game_data: GameData, translations: dict[str, str]) -> int:
    """按原名字框写回译名。"""
    clean_translations = _filled_translations(translations)
    if not clean_translations:
        return 0

    written_count = 0
    for file_name, data in game_data.writable_data.items():
        if MAP_PATTERN.fullmatch(file_name) is not None:
            written_count += _write_map_speaker_names(
                file_name=file_name,
                data=data,
                translations=clean_translations,
            )
            continue
        if file_name == COMMON_EVENTS_FILE_NAME:
            written_count += _write_common_event_speaker_names(data=data, translations=clean_translations)
            continue
        if file_name == TROOPS_FILE_NAME:
            written_count += _write_troop_speaker_names(data=data, translations=clean_translations)
    return written_count


def _write_map_speaker_names(
    *,
    file_name: str,
    data: JsonValue,
    translations: dict[str, str],
) -> int:
    """写回地图事件中的名字框。"""
    written_count = 0
    map_object = ensure_json_object(data, file_name)
    events = ensure_json_array(map_object["events"], f"{file_name}.events")
    for event_index, event in enumerate(events):
        if event is None:
            continue
        event_object = ensure_json_object(event, f"{file_name}.events[{event_index}]")
        pages = ensure_json_array(event_object["pages"], f"{file_name}.events[{event_index}].pages")
        for page_index, page in enumerate(pages):
            page_object = ensure_json_object(page, f"{file_name}.events[{event_index}].pages[{page_index}]")
            commands = _ensure_command_list(page_object["list"], f"{file_name}.events[{event_index}].pages[{page_index}].list")
            written_count += _write_speaker_names_to_commands(commands=commands, translations=translations)
    return written_count


def _write_common_event_speaker_names(*, data: JsonValue, translations: dict[str, str]) -> int:
    """写回公共事件中的名字框。"""
    written_count = 0
    events = ensure_json_array(data, COMMON_EVENTS_FILE_NAME)
    for event_index, event in enumerate(events):
        if event is None:
            continue
        event_object = ensure_json_object(event, f"{COMMON_EVENTS_FILE_NAME}[{event_index}]")
        commands = _ensure_command_list(event_object["list"], f"{COMMON_EVENTS_FILE_NAME}[{event_index}].list")
        written_count += _write_speaker_names_to_commands(commands=commands, translations=translations)
    return written_count


def _write_troop_speaker_names(*, data: JsonValue, translations: dict[str, str]) -> int:
    """写回敌群事件中的名字框。"""
    written_count = 0
    troops = ensure_json_array(data, TROOPS_FILE_NAME)
    for troop_index, troop in enumerate(troops):
        if troop is None:
            continue
        troop_object = ensure_json_object(troop, f"{TROOPS_FILE_NAME}[{troop_index}]")
        pages = ensure_json_array(troop_object["pages"], f"{TROOPS_FILE_NAME}[{troop_index}].pages")
        for page_index, page in enumerate(pages):
            page_object = ensure_json_object(page, f"{TROOPS_FILE_NAME}[{troop_index}].pages[{page_index}]")
            commands = _ensure_command_list(page_object["list"], f"{TROOPS_FILE_NAME}[{troop_index}].pages[{page_index}].list")
            written_count += _write_speaker_names_to_commands(commands=commands, translations=translations)
    return written_count


def _write_speaker_names_to_commands(
    *,
    commands: list[JsonObject],
    translations: dict[str, str],
) -> int:
    """写回事件指令列表中的名字框。"""
    written_count = 0
    for command in commands:
        if command.get("code") != Code.NAME:
            continue
        parameters = _ensure_command_parameters(command)
        while len(parameters) <= 4:
            parameters.append("")
        source_text = parameters[4]
        if not isinstance(source_text, str):
            continue
        if not is_translatable_name_context_source(source_text):
            continue
        translated_text = translations.get(source_text)
        if translated_text is None:
            continue
        parameters[4] = translated_text
        written_count += 1
    return written_count


def _filled_translations(translations: dict[str, str]) -> dict[str, str]:
    """过滤空原文和空译名。"""
    return {
        source_text.strip(): translated_text.strip()
        for source_text, translated_text in translations.items()
        if is_translatable_name_context_source(source_text) and translated_text.strip()
    }


def _ensure_command_list(value: JsonValue, context: str) -> list[JsonObject]:
    """把 JSON 数组收窄为事件命令对象列表。"""
    raw_commands = ensure_json_array(value, context)
    commands: list[JsonObject] = []
    for index, command in enumerate(raw_commands):
        commands.append(ensure_json_object(command, f"{context}[{index}]"))
    return commands


def _ensure_command_parameters(command: JsonObject) -> JsonArray:
    """读取事件命令 parameters 数组。"""
    parameters = command.get("parameters")
    if not isinstance(parameters, list):
        new_parameters: JsonArray = []
        command["parameters"] = new_parameters
        return new_parameters
    return parameters


__all__: list[str] = ["apply_name_context_translations"]
