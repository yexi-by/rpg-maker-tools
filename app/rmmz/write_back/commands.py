"""RPG Maker 事件指令文本写入。"""

from app.rmmz.schema import Code, GameData, MAP_PATTERN, COMMON_EVENTS_FILE_NAME, TROOPS_FILE_NAME, TranslationItem
from app.rmmz.speaker import MvVirtualSpeaker, parse_mv_virtual_speaker_line
from app.rmmz.text_rules import JsonArray, JsonObject, JsonValue, TextRules, ensure_json_object

from .locators import (
    command_list_parent_path,
    ensure_command_parameters,
    extract_command_value_path_parts,
    locate_command_array,
    locate_commands,
    set_event_command_value,
    write_first_parameter,
)
from .preparation import prepare_long_text_write_lines, prepare_single_text_write_value, prepare_text_write_lines


def command_item_sort_key(item: TranslationItem) -> tuple[str, tuple[int, ...], int]:
    """按事件指令定位生成倒序写入键，避免插入新行顶偏后续条目。"""
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


def write_command_item(
    game_data: GameData,
    item: TranslationItem,
    text_rules: TextRules | None,
    speaker_name_translations: dict[str, str] | None,
) -> None:
    """将事件指令相关译文写入数据副本。"""
    commands, command_index = locate_commands(
        writable_data=game_data.writable_data,
        location_path=item.location_path,
    )
    command = ensure_json_object(commands[command_index], item.location_path)

    if item.item_type == "short_text":
        write_event_command_text_item(command=command, item=item, text_rules=text_rules)
        return

    if item.item_type == "long_text":
        command_code = command.get("code")
        if command_code == Code.NAME:
            write_name_text_item(
                game_data=game_data,
                item=item,
                text_rules=text_rules,
                speaker_name_translations=speaker_name_translations,
            )
            return

        if command_code == Code.SCROLL_TEXT:
            write_line_commands_by_paths(
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
        parameters = ensure_command_parameters(command, item.location_path)
        translation_lines = prepare_text_write_lines(item=item, text_rules=text_rules)
        translation_values: JsonArray = [line for line in translation_lines]
        if parameters:
            parameters[0] = translation_values
        else:
            parameters.append(translation_values)
        return

    raise ValueError(f"事件指令 item_type 无法处理: {item.item_type}")


def write_name_text_item(
    *,
    game_data: GameData,
    item: TranslationItem,
    text_rules: TextRules | None,
    speaker_name_translations: dict[str, str] | None,
) -> None:
    """写入普通名字框后的长文本，MV 会先重建虚拟名字框。"""
    if game_data.layout.engine_kind == "mv":
        virtual_speaker = find_mv_virtual_speaker_for_name_command(game_data=game_data, item=item)
        if virtual_speaker is not None:
            write_mv_virtual_name_text_item(
                game_data=game_data,
                item=item,
                text_rules=text_rules,
                speaker_name_translations=speaker_name_translations,
                virtual_speaker=virtual_speaker,
            )
            return

    write_line_commands_by_paths(
        game_data=game_data,
        item=item,
        expected_code=Code.TEXT,
        text_rules=text_rules,
    )


def write_mv_virtual_name_text_item(
    *,
    game_data: GameData,
    item: TranslationItem,
    text_rules: TextRules | None,
    speaker_name_translations: dict[str, str] | None,
    virtual_speaker: tuple[str, MvVirtualSpeaker],
) -> None:
    """按 MV 虚拟名字框协议重建说话人行并写入剥离后的正文。"""
    speaker_line_path, speaker = virtual_speaker
    if not speaker.body_text and speaker_line_path in item.source_line_paths:
        raise ValueError(
            "当前 MV 译文仍包含说话人行，请先执行 reset-translations --all 后重新提取和翻译。"
        )

    translated_speaker = read_mv_translated_speaker(
        speaker_name_translations=speaker_name_translations,
        source_speaker=speaker.speaker,
        location_path=item.location_path,
    )
    translation_lines = prepare_long_text_write_lines(item=item, text_rules=text_rules)
    ensure_mv_translation_body_is_clean(
        source_speaker=speaker.speaker,
        translated_speaker=translated_speaker,
        translation_lines=translation_lines,
        location_path=item.location_path,
    )

    if speaker.body_text:
        if not translation_lines:
            raise ValueError(f"MV 内联说话人正文缺少译文: {item.location_path}")
        write_text_command_first_parameter(
            game_data=game_data,
            source_line_path=speaker_line_path,
            translated_text=speaker.render(
                translated_speaker=translated_speaker,
                translated_body=translation_lines[0],
            ),
        )
        write_prepared_line_commands_by_paths(
            game_data=game_data,
            item=item,
            expected_code=Code.TEXT,
            source_line_paths=item.source_line_paths[1:],
            insertion_anchor_path=speaker_line_path,
            translation_lines=translation_lines[1:],
        )
        return

    write_text_command_first_parameter(
        game_data=game_data,
        source_line_path=speaker_line_path,
        translated_text=speaker.render(translated_speaker=translated_speaker),
    )
    write_prepared_line_commands_by_paths(
        game_data=game_data,
        item=item,
        expected_code=Code.TEXT,
        source_line_paths=item.source_line_paths,
        insertion_anchor_path=speaker_line_path,
        translation_lines=translation_lines,
    )


def write_line_commands_by_paths(
    *,
    game_data: GameData,
    item: TranslationItem,
    expected_code: Code,
    text_rules: TextRules | None,
) -> None:
    """按提取路径写入长文本，并为额外译文行插入事件指令。"""
    if not item.source_line_paths:
        raise ValueError(f"长文本缺少逐行写入路径: {item.location_path}")

    translation_lines = prepare_long_text_write_lines(
        item=item,
        text_rules=text_rules,
    )
    write_prepared_line_commands_by_paths(
        game_data=game_data,
        item=item,
        expected_code=expected_code,
        source_line_paths=item.source_line_paths,
        insertion_anchor_path=item.source_line_paths[-1],
        translation_lines=translation_lines,
    )


def write_prepared_line_commands_by_paths(
    *,
    game_data: GameData,
    item: TranslationItem,
    expected_code: Code,
    source_line_paths: list[str],
    insertion_anchor_path: str,
    translation_lines: list[str],
) -> None:
    """把已经预处理好的译文逐行写入指定事件指令路径。"""
    existing_line_count = len(source_line_paths)
    write_line_count = min(existing_line_count, len(translation_lines))

    for source_line_path, translated_text in zip(
        source_line_paths[:write_line_count],
        translation_lines[:write_line_count],
        strict=True,
    ):
        commands, command_index = locate_commands(
            writable_data=game_data.writable_data,
            location_path=source_line_path,
        )
        target_command = ensure_json_object(commands[command_index], source_line_path)
        if target_command.get("code") != expected_code:
            raise RuntimeError(f"逐行路径指向的指令类型错误: {source_line_path}")
        write_first_parameter(target_command, translated_text)

    if len(translation_lines) < existing_line_count:
        delete_surplus_line_commands(
            writable_data=game_data.writable_data,
            item=item,
            expected_code=expected_code,
            surplus_source_line_paths=source_line_paths[len(translation_lines) :],
        )
        return

    extra_lines = translation_lines[existing_line_count:]
    if not extra_lines:
        return

    insert_extra_line_commands(
        writable_data=game_data.writable_data,
        item=item,
        expected_code=expected_code,
        insertion_anchor_path=source_line_paths[-1] if source_line_paths else insertion_anchor_path,
        extra_lines=extra_lines,
    )


def delete_surplus_line_commands(
    *,
    writable_data: dict[str, JsonValue],
    item: TranslationItem,
    expected_code: Code,
    surplus_source_line_paths: list[str],
) -> None:
    """删除译文不再需要的原始 401/405 行指令。"""
    if not surplus_source_line_paths:
        return
    ensure_source_paths_share_command_list(item.source_line_paths, item.location_path)
    indexes: list[int] = []
    command_array: JsonArray | None = None
    for source_line_path in surplus_source_line_paths:
        current_command_array, command_index = locate_command_array(
            writable_data=writable_data,
            location_path=source_line_path,
        )
        if command_array is None:
            command_array = current_command_array
        elif command_array is not current_command_array:
            raise ValueError(f"长文本逐行路径跨事件列表，无法删除多余行: {item.location_path}")

        target_command = ensure_json_object(current_command_array[command_index], source_line_path)
        if target_command.get("code") != expected_code:
            raise RuntimeError(f"多余行删除锚点指令类型错误: {source_line_path}")
        indexes.append(command_index)

    if command_array is None:
        return
    for command_index in sorted(indexes, reverse=True):
        del command_array[command_index]


def find_mv_virtual_speaker_for_name_command(
    *,
    game_data: GameData,
    item: TranslationItem,
) -> tuple[str, MvVirtualSpeaker] | None:
    """定位 MV `101` 后首条非空 `401` 中的虚拟名字框。"""
    commands, command_index = locate_commands(
        writable_data=game_data.writable_data,
        location_path=item.location_path,
    )
    next_index = command_index + 1
    while next_index < len(commands):
        command = ensure_json_object(commands[next_index], command_path_from_index(item.location_path, next_index))
        if command.get("code") != Code.TEXT:
            break
        text = read_first_parameter_text(command)
        if text is None or not text.strip():
            next_index += 1
            continue
        virtual_speaker = parse_mv_virtual_speaker_line(text=text, game_data=game_data)
        if virtual_speaker is None:
            return None
        return command_path_from_index(item.location_path, next_index), virtual_speaker
    return None


def command_path_from_index(location_path: str, command_index: int) -> str:
    """用同一事件列表路径和新的指令下标生成定位路径。"""
    parts = location_path.split("/")
    parts[-1] = str(command_index)
    return "/".join(parts)


def read_first_parameter_text(command: JsonObject) -> str | None:
    """读取事件指令第一个字符串参数。"""
    parameters = command.get("parameters")
    if not isinstance(parameters, list) or not parameters:
        return None
    first_parameter = parameters[0]
    if not isinstance(first_parameter, str):
        return None
    return first_parameter


def read_mv_translated_speaker(
    *,
    speaker_name_translations: dict[str, str] | None,
    source_speaker: str,
    location_path: str,
) -> str:
    """读取 MV 虚拟名字框说话人的术语译名，缺失时立即阻止写入。"""
    if speaker_name_translations is None:
        raise ValueError(f"MV 说话人 {source_speaker} 缺少术语译名，请先导入 speaker_names: {location_path}")
    translated_speaker = speaker_name_translations.get(source_speaker, "").strip()
    if not translated_speaker:
        raise ValueError(f"MV 说话人 {source_speaker} 缺少术语译名，请先导入 speaker_names: {location_path}")
    return translated_speaker


def ensure_mv_translation_body_is_clean(
    *,
    source_speaker: str,
    translated_speaker: str,
    translation_lines: list[str],
    location_path: str,
) -> None:
    """阻止正文译文把虚拟名字框说话人再次塞进正文。"""
    if not translation_lines:
        return
    first_line = translation_lines[0].strip()
    forbidden_prefixes = (
        f"{source_speaker}:",
        f"{source_speaker}：",
        f"{source_speaker}「",
        f"{source_speaker}（",
        f"{translated_speaker}:",
        f"{translated_speaker}：",
        f"{translated_speaker}「",
        f"{translated_speaker}（",
    )
    if first_line.startswith(forbidden_prefixes):
        raise ValueError(
            f"MV 译文正文仍包含说话人前缀，请先执行 reset-translations --all 后重新翻译: {location_path}"
        )


def write_text_command_first_parameter(
    *,
    game_data: GameData,
    source_line_path: str,
    translated_text: str,
) -> None:
    """定位指定 `401` 并写入第一个参数。"""
    commands, command_index = locate_commands(
        writable_data=game_data.writable_data,
        location_path=source_line_path,
    )
    target_command = ensure_json_object(commands[command_index], source_line_path)
    if target_command.get("code") != Code.TEXT:
        raise RuntimeError(f"虚拟名字框路径指向的指令类型错误: {source_line_path}")
    write_first_parameter(target_command, translated_text)


def insert_extra_line_commands(
    *,
    writable_data: dict[str, JsonValue],
    item: TranslationItem,
    expected_code: Code,
    insertion_anchor_path: str,
    extra_lines: list[str],
) -> None:
    """把模型额外输出的长文本行插入原事件列表。"""
    ensure_source_paths_share_command_list(item.source_line_paths, item.location_path)
    command_array, command_index = locate_command_array(
        writable_data=writable_data,
        location_path=insertion_anchor_path,
    )
    base_command = ensure_json_object(command_array[command_index], insertion_anchor_path)
    if base_command.get("code") != expected_code:
        raise RuntimeError(f"额外行插入锚点指令类型错误: {insertion_anchor_path}")

    for offset, translated_text in enumerate(extra_lines, start=1):
        command_array.insert(
            command_index + offset,
            build_text_line_command(
                expected_code=expected_code,
                translated_text=translated_text,
                base_command=base_command,
            ),
        )


def build_text_line_command(
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


def ensure_source_paths_share_command_list(source_line_paths: list[str], location_path: str) -> None:
    """确保一个长文本条目的逐行路径来自同一事件列表。"""
    parent_paths = {command_list_parent_path(path) for path in source_line_paths}
    if len(parent_paths) != 1:
        raise ValueError(f"长文本逐行路径跨事件列表，无法插入额外行: {location_path}")


def write_event_command_text_item(command: JsonObject, item: TranslationItem, text_rules: TextRules | None) -> None:
    """将外部规则命中的事件指令短文本译文写入参数容器。"""
    path_parts = extract_command_value_path_parts(item.location_path)
    if len(path_parts) < 2 or path_parts[0] != "parameters":
        raise ValueError(f"事件指令路径缺少 parameters 段: {item.location_path}")

    parameters = ensure_command_parameters(command, item.location_path)
    param_index = int(path_parts[1])
    if param_index >= len(parameters):
        raise ValueError(f"事件指令参数索引越界: {item.location_path}")

    translated_text = prepare_single_text_write_value(item=item, text_rules=text_rules)
    parameters[param_index] = set_event_command_value(
        current_value=parameters[param_index],
        path_parts=path_parts[2:],
        translated_text=translated_text,
        context=item.location_path,
    )
