"""
术语表回写模块。

负责把角色名术语与地图显示名术语写回 `GameData.writable_data`。
这一层只操作可写副本，不覆盖 `GameData.data` 原始内容。
"""

from typing import Any

from app.models.schemas import (
    COMMON_EVENTS_FILE_NAME,
    Code,
    GameData,
    Glossary,
    MAP_PATTERN,
    TROOPS_FILE_NAME,
)


def write_glossary(game_data: GameData, glossary: Glossary) -> None:
    """
    将术语表（包含角色名和地图显示名）翻译结果写回到游戏数据的内存副本中。

    该操作会全局遍历所有地图、公共事件以及敌群事件，
    寻找到带有 displayName 的地图属性以及所有 101(NAME) 的事件指令，
    如果它们的值存在于已翻译的术语表中，则执行覆盖。

    Args:
        game_data: 提供全局数据访问的聚合对象，包含需要被修改的 writable_data。
        glossary: 包含原名与译名映射关系的结构化术语表。
    """
    place_map: dict[str, str] = glossary.place_map()
    role_map: dict[str, str] = glossary.role_map()

    for file_name, data in game_data.writable_data.items():
        if not MAP_PATTERN.fullmatch(file_name):
            continue
        if not isinstance(data, dict):
            continue

        display_name = data.get("displayName")
        if isinstance(display_name, str) and display_name in place_map:
            data["displayName"] = place_map[display_name]

        events = data.get("events", [])
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            pages = event.get("pages", [])
            if not isinstance(pages, list):
                continue
            for page in pages:
                if not isinstance(page, dict):
                    continue
                commands = page.get("list", [])
                if not isinstance(commands, list):
                    continue
                _write_role_names_to_commands(commands, role_map)

    common_events = game_data.writable_data.get(COMMON_EVENTS_FILE_NAME)
    if isinstance(common_events, list):
        for common_event in common_events:
            if not isinstance(common_event, dict):
                continue
            commands = common_event.get("list", [])
            if not isinstance(commands, list):
                continue
            _write_role_names_to_commands(commands, role_map)

    troops = game_data.writable_data.get(TROOPS_FILE_NAME)
    if isinstance(troops, list):
        for troop in troops:
            if not isinstance(troop, dict):
                continue
            pages = troop.get("pages", [])
            if not isinstance(pages, list):
                continue
            for page in pages:
                if not isinstance(page, dict):
                    continue
                commands = page.get("list", [])
                if not isinstance(commands, list):
                    continue
                _write_role_names_to_commands(commands, role_map)


def _write_role_names_to_commands(
    commands: list[dict[str, Any]],
    role_name_map: dict[str, str],
) -> None:
    """
    对指定的事件指令列表进行原地遍历修改，专门用于替换 101(NAME) 指令中的角色名称。

    Args:
        commands: 事件指令对象列表引用（直接修改此列表会改变外层数据）。
        role_name_map: 角色术语表的字典映射。
    """
    for command in commands:
        if command.get("code") != Code.NAME:
            continue

        parameters = command.get("parameters")
        if not isinstance(parameters, list) or len(parameters) < 5:
            continue

        role = parameters[4]
        if not isinstance(role, str):
            continue
        if role not in role_name_map:
            continue
        parameters[4] = role_name_map[role]


__all__: list[str] = ["write_glossary"]
