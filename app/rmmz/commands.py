"""
事件指令遍历工具模块。

提供遍历 RPG Maker 游戏内全部事件指令的工具函数，
统一处理地图事件、公共事件与敌群事件三类来源。
"""

from collections.abc import Generator

from app.rmmz.game_data import EventCommand
from app.rmmz.schema import COMMON_EVENTS_FILE_NAME, TROOPS_FILE_NAME, GameData


def iter_all_commands(
    game_data: GameData,
) -> Generator[tuple[list[str | int], str, EventCommand], None, None]:
    """
    遍历游戏中的全部事件指令（Event Command）。

    该生成器将依次产生全游戏三个主要来源的事件指令：地图事件、公共事件以及敌群战斗事件。
    通过使用 yield 返回每一个指令对象及其精确路径，解耦了遍历逻辑与具体的业务处理逻辑。

    为什么要单独抽成工具函数：
    1. 这段多层嵌套（事件 -> 页面 -> 指令）的遍历逻辑如果写在业务代码里会非常臃肿。
    2. 提取正文、回写正文以及启动前的对话探针检查，都需要复用这套遍历逻辑。

    Args:
        game_data: 已完成解析的全局游戏数据聚合模型。

    Yields:
        (path, display_name, command) 形式的三元组：
        - path: list[str | int]，用于精确定位指令的路径数组（如 ["地图文件.json", 1, 0, 5]）。
        - display_name: str，指令所属地图的显示名称（公共事件和敌群返回固定文件名）。
        - command: EventCommand，当前事件指令对象。
    """
    # 步骤 1: 遍历所有地图文件
    path: list[str | int]
    for file_name, map_data in game_data.map_data.items():
        for event in map_data.events:
            if event is None:
                continue
            for p_index, page in enumerate(event.pages):
                for c_index, command in enumerate(page.commands):
                    path = [file_name, event.id, p_index, c_index]
                    yield (path, map_data.displayName, command)

    # 步骤 2: 遍历公共事件
    for common_event in game_data.common_events:
        if common_event is None:
            continue
        for c_index, command in enumerate(common_event.commands):
            path = [COMMON_EVENTS_FILE_NAME, common_event.id, c_index]
            yield (path, COMMON_EVENTS_FILE_NAME, command)

    # 步骤 3: 遍历敌群事件
    for troop in game_data.troops:
        if troop is None:
            continue
        for p_index, page in enumerate(troop.pages):
            for c_index, command in enumerate(page.commands):
                path = [TROOPS_FILE_NAME, troop.id, p_index, c_index]
                yield (path, TROOPS_FILE_NAME, command)
