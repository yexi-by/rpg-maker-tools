"""
正文回写模块。

负责将 `TranslationItem` 的翻译结果写回 `GameData.writable_data`。
这一层只操作可写副本，不覆盖 `GameData.data` 原始内容。
"""

from typing import Any

from app.models.schemas import (
    COMMON_EVENTS_FILE_NAME,
    Code,
    GameData,
    MAP_PATTERN,
    PLUGINS_FILE_NAME,
    QUESTS_FILE_NAME,
    SYSTEM_FILE_NAME,
    TROOPS_FILE_NAME,
    TranslationItem,
)


def write_data_text(game_data: GameData, items: list[TranslationItem]) -> None:
    """
    将最终的翻译文本写回 `data/` 目录游戏数据的内存副本体内。

    为了避免破坏作为原始参照的 `GameData.data` 属性，所有的修改都将针对
    `GameData.writable_data` 进行。该函数通过拆解每个条目附带的 `location_path`
    来识别它的归属域（System, Events 还是基础数据库）。
    对于属于插件（plugins.js）的翻译项，该函数会直接略过。

    Args:
        game_data: 维护着内存数据的全局大对象。
        items: 由数据库中查询到的、已存在有效译文的翻译条目集合。
    """
    for item in items:
        file_name = item.location_path.split("/")[0]

        if file_name == PLUGINS_FILE_NAME:
            continue

        if file_name == SYSTEM_FILE_NAME:
            _write_system_item(game_data=game_data, item=item)
            continue

        if file_name == QUESTS_FILE_NAME:
            _write_quest_item(game_data=game_data, item=item)
            continue

        if MAP_PATTERN.fullmatch(file_name) or file_name in {
            COMMON_EVENTS_FILE_NAME,
            TROOPS_FILE_NAME,
        }:
            _write_command_item(game_data=game_data, item=item)
            continue

        _write_base_item(game_data=game_data, item=item)


def _write_command_item(game_data: GameData, item: TranslationItem) -> None:
    """
    将与事件指令（Code 101/401/102/405 等）相关的多行或选项译文写回数据副本。

    此方法会处理两种情况：
    1. 长文本（long_text）：由于长文本通常是由一个 NAME 配合若干个 TEXT 组成的，
       或者单独是一个 SCROLL_TEXT，这里会针对原长度寻找紧随其后的指令位并逐行替换 parameters。
    2. 选项数组（array）：直接替换 CHOICES(102) 指令对应的整个参数数组内容。

    Args:
        game_data: 游戏数据聚合对象。
        item: 当前归属于某个事件指令的翻译条目。
        
    Raises:
        RuntimeError: 当发现原本应该承接翻译的指令已被删改、导致 Code 匹配不上时抛出。
    """
    commands, command_index = _locate_commands(
        writable_data=game_data.writable_data,
        location_path=item.location_path,
    )
    command = commands[command_index]

    if item.item_type == "short_text":
        _write_plugin_command_text_item(command=command, item=item)
        return

    if item.item_type == "long_text":
        command_code = command.get("code")
        if command_code == Code.NAME:
            translated_lines: list[str] = item.translation_lines or [""]
            for offset, translated_text in enumerate(translated_lines, start=1):
                target_index = command_index + offset
                target_command = commands[target_index]
                if target_command.get("code") != Code.TEXT:
                    raise RuntimeError(
                        f"路径 {item.location_path} 后续指令不是 Code.TEXT: {target_index}"
                    )
                _write_first_parameter(target_command, translated_text)
            return

        if command_code == Code.SCROLL_TEXT:
            translated_text = item.translation_lines[0] if item.translation_lines else ""
            _write_first_parameter(command, translated_text)
            return

        raise RuntimeError(f"未知的 long_text 指令类型: {item.location_path}")

    if item.item_type == "array":
        if command.get("code") != Code.CHOICES:
            raise RuntimeError(f"路径 {item.location_path} 不是 CHOICES 指令")

        parameters = command.get("parameters")
        if not isinstance(parameters, list):
            command["parameters"] = [list(item.translation_lines)]
            return
        if parameters:
            parameters[0] = list(item.translation_lines)
        else:
            parameters.append(list(item.translation_lines))
        return

    raise ValueError(f"事件指令不支持的 item_type: {item.item_type}")


def _write_plugin_command_text_item(
    command: dict[str, Any],
    item: TranslationItem,
) -> None:
    """
    将 `Code.PLUGIN_TEXT(357)` 指令里的短文本译文写回参数容器。

    Args:
        command: 已经通过 `location_path` 定位到的目标事件指令。
        item: 当前待写回的短文本翻译项。

    Raises:
        RuntimeError: 当路径指向的实际指令并不是 357 时抛出。
        ValueError: 当参数结构、路径尾段或容器层级不匹配时抛出。
    """
    if command.get("code") != Code.PLUGIN_TEXT:
        raise RuntimeError(f"路径 {item.location_path} 不是 PLUGIN_TEXT 指令")

    path_parts: list[str] = _extract_command_value_path_parts(item.location_path)
    if len(path_parts) < 3 or path_parts[0] != "parameters":
        raise ValueError(f"357 指令路径缺少 parameters 段: {item.location_path}")

    parameters = command.get("parameters")
    if not isinstance(parameters, list):
        raise ValueError(f"357 指令 parameters 不是数组: {item.location_path}")

    param_index: int = int(path_parts[1])
    if param_index >= len(parameters):
        raise ValueError(f"357 指令参数索引越界: {item.location_path}")

    translated_text: str = item.translation_lines[0] if item.translation_lines else ""
    parameters[param_index] = _set_plugin_command_value(
        current_value=parameters[param_index],
        path_parts=path_parts[2:],
        translated_text=translated_text,
    )


def _locate_commands(
    writable_data: dict[str, Any],
    location_path: str,
) -> tuple[list[dict[str, Any]], int]:
    """
    根据 `location_path` 的层级结构，穿透解析字典，精准定位到具体的 RM Event List 数组。

    Args:
        writable_data: 游戏数据可写副本。
        location_path: 提取时记录下来的带有树状路由层级信息的路径字符串。

    Returns:
        `(目标命令列表引用, 在命令列表中的索引)`。修改该引用会直接影响 writable_data。
        
    Raises:
        ValueError: 如果提供了一个未知的针对事件定位的路由格式。
    """
    parts: list[str] = location_path.split("/")
    file_name: str = parts[0]
    data = writable_data[file_name]

    if MAP_PATTERN.fullmatch(file_name):
        event_id = int(parts[1])
        page_index = int(parts[2])
        command_index = int(parts[3])
        commands = data["events"][event_id]["pages"][page_index]["list"]
        return commands, command_index

    if file_name == COMMON_EVENTS_FILE_NAME:
        event_id = int(parts[1])
        command_index = int(parts[2])
        commands = data[event_id]["list"]
        return commands, command_index

    if file_name == TROOPS_FILE_NAME:
        troop_id = int(parts[1])
        page_index = int(parts[2])
        command_index = int(parts[3])
        commands = data[troop_id]["pages"][page_index]["list"]
        return commands, command_index

    raise ValueError(f"未知的事件定位路径: {location_path}")


def _extract_command_value_path_parts(location_path: str) -> list[str]:
    """
    从完整的 `location_path` 中拆出命令后的值路径尾段。

    例如地图事件中的
    `Map001.json/1/0/4/parameters/3/messageText`
    会被拆成
    `["parameters", "3", "messageText"]`。

    Args:
        location_path: 翻译项的完整路径字符串。

    Returns:
        事件命令索引之后的剩余路径片段列表。

    Raises:
        ValueError: 如果路径不属于任何已知事件来源。
    """
    parts: list[str] = location_path.split("/")
    file_name: str = parts[0]

    if MAP_PATTERN.fullmatch(file_name):
        return parts[4:]

    if file_name == COMMON_EVENTS_FILE_NAME:
        return parts[3:]

    if file_name == TROOPS_FILE_NAME:
        return parts[4:]

    raise ValueError(f"未知的事件值路径: {location_path}")


def _set_plugin_command_value(
    current_value: Any,
    path_parts: list[str],
    translated_text: str,
) -> Any:
    """
    按路径深入 357 指令的参数容器，并把最终字符串叶子替换为译文。

    这一层只处理原生 `dict` / `list` 容器，不尝试解析 JSON 字符串，
    因为当前需求只覆盖事件数据中已经展开成容器的插件参数。

    Args:
        current_value: 当前递归节点的值。
        path_parts: 尚未消费的定位路径。
        translated_text: 最终需要写入的译文。

    Returns:
        完成替换后的新节点。

    Raises:
        ValueError: 如果路径指向不存在的键、非法的数组索引，或者没有最终落到字符串叶子。
    """
    if not path_parts:
        if not isinstance(current_value, str):
            raise ValueError("357 指令路径没有指向字符串叶子")
        return translated_text

    key: str = path_parts[0]
    remain_parts: list[str] = path_parts[1:]

    if isinstance(current_value, dict):
        if key not in current_value:
            raise ValueError(f"357 指令参数键不存在: {key}")
        current_value[key] = _set_plugin_command_value(
            current_value=current_value[key],
            path_parts=remain_parts,
            translated_text=translated_text,
        )
        return current_value

    if isinstance(current_value, list):
        index: int = int(key)
        if index >= len(current_value):
            raise ValueError(f"357 指令参数索引越界: {index}")
        current_value[index] = _set_plugin_command_value(
            current_value=current_value[index],
            path_parts=remain_parts,
            translated_text=translated_text,
        )
        return current_value

    raise ValueError(f"357 指令路径无法继续下钻: {path_parts}")


def _write_first_parameter(command: dict[str, Any], text: str) -> None:
    """
    将文本写入指令的第一个参数位。

    Args:
        command: 目标指令字典。
        text: 目标文本。
    """
    parameters = command.get("parameters")
    if not isinstance(parameters, list):
        command["parameters"] = [text]
        return

    if parameters:
        parameters[0] = text
    else:
        parameters.append(text)


def _write_system_item(game_data: GameData, item: TranslationItem) -> None:
    """
    将 System.json 文本写回数据副本。

    Args:
        game_data: 游戏数据聚合对象。
        item: 当前翻译条目。
    """
    parts: list[str] = item.location_path.split("/")
    system_data = game_data.writable_data[SYSTEM_FILE_NAME]
    translated_text: str = item.translation_lines[0] if item.translation_lines else ""

    if len(parts) == 2:
        system_data[parts[1]] = translated_text
        return

    if len(parts) == 3:
        key = parts[1]
        index = int(parts[2])
        system_data[key][index] = translated_text
        return

    if len(parts) == 4 and parts[1] == "terms" and parts[2] == "messages":
        system_data["terms"]["messages"][parts[3]] = translated_text
        return

    if len(parts) == 4 and parts[1] == "terms":
        key = parts[2]
        index = int(parts[3])
        system_data["terms"][key][index] = translated_text
        return

    raise ValueError(f"未知的 System 路径: {item.location_path}")


def _write_base_item(game_data: GameData, item: TranslationItem) -> None:
    """
    将诸如技能名称、物品描述、角色配置等基础数据库条目的文本写回数据副本。

    因为基础数据库（例如 Actors.json 或 Skills.json）是一个由字典组成的数组，且由于 RM 的设计，
    其索引（index）往往就是该条目的内部 ID。这套逻辑支持：
    1. 优先尝试通过数组索引直接访问以提高效率。
    2. 若索引位偏离或存在稀疏项，则通过遍历寻找 `id` 字段相匹配的字典对象进行替换。

    Args:
        game_data: 游戏数据聚合对象。
        item: 归属于某个基础数据库条目的翻译项。
        
    Raises:
        ValueError: 如果由于数据残缺导致怎么也找不到对应的基础条目对象时抛出。
    """
    parts: list[str] = item.location_path.split("/")
    file_name: str = parts[0]
    item_id: int = int(parts[1])
    key: str = parts[2]
    translated_text: str = item.translation_lines[0] if item.translation_lines else ""

    data = game_data.writable_data[file_name]
    if item_id < len(data):
        target = data[item_id]
        if isinstance(target, dict) and target.get("id") == item_id:
            target[key] = translated_text
            return

    for target in data:
        if not isinstance(target, dict):
            continue
        if target.get("id") != item_id:
            continue
        target[key] = translated_text
        return

    raise ValueError(f"未找到基础数据库条目: {item.location_path}")


def _write_quest_item(game_data: GameData, item: TranslationItem) -> None:
    """
    将 `Quests.json` 中的任务文本写回数据副本。

    当前只支持四类可翻译字段：
    1. `title_cte`
    2. `summaries_cte`
    3. `rewards_cte`
    4. `objectives_cte`

    其余字段即便存在于原始 JSON，也不应该走正文翻译写回通路。

    Args:
        game_data: 游戏数据聚合对象。
        item: 当前归属于 `Quests.json` 的翻译条目。

    Raises:
        ValueError: 当任务 ID、字段路径、目标容器或目标键不存在时抛出。
    """
    parts: list[str] = item.location_path.split("/")
    if len(parts) < 3:
        raise ValueError(f"未知的 Quests 路径: {item.location_path}")

    quests_data = game_data.writable_data.get(QUESTS_FILE_NAME)
    if not isinstance(quests_data, dict):
        raise ValueError(f"缺少 Quests.json 可写数据: {item.location_path}")

    quest_id: str = parts[1]
    quest = quests_data.get(quest_id)
    if not isinstance(quest, dict):
        raise ValueError(f"未找到任务条目: {item.location_path}")

    translated_text: str = item.translation_lines[0] if item.translation_lines else ""
    field_name: str = parts[2]

    if field_name == "title_cte":
        if len(parts) != 3:
            raise ValueError(f"title_cte 路径层级错误: {item.location_path}")
        if field_name not in quest:
            raise ValueError(f"任务字段不存在: {item.location_path}")
        quest[field_name] = translated_text
        return

    if field_name not in {"summaries_cte", "rewards_cte", "objectives_cte"}:
        raise ValueError(f"未知的任务文本字段: {item.location_path}")
    if len(parts) != 4:
        raise ValueError(f"任务子文本路径层级错误: {item.location_path}")

    text_map = quest.get(field_name)
    if not isinstance(text_map, dict):
        raise ValueError(f"任务子文本容器不存在: {item.location_path}")

    entry_id: str = parts[3]
    if entry_id not in text_map:
        raise ValueError(f"任务子文本键不存在: {item.location_path}")

    text_map[entry_id] = translated_text


__all__: list[str] = ["write_data_text"]
