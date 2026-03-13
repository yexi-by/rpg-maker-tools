"""
全局游戏数据管理模块。

本模块负责在进程内统一管理多个 `GameData`，
调用方通过游戏路径触发加载，再按 `game_title` 持有结果。
"""

from pathlib import Path

from app.models.schemas import GameData
from app.utils.database_utils import read_game_title, resolve_game_directory
from app.utils.game_loader_utils import load_game_data as load_game_data_from_path


class GameDataManager:
    """
    全局游戏数据管理器。

    Attributes:
        items: 以 `game_title` 为键的游戏数据字典。
    """

    def __init__(self) -> None:
        """初始化空的游戏数据字典。"""
        self.items: dict[str, GameData] = {}

    async def load_game_data(self, game_path: str | Path) -> None:
        """
        读取指定游戏目录，并以 `game_title` 为键写入全局字典。

        如果同名键已存在，本轮实现直接覆盖旧值。

        Args:
            game_path: RPG Maker 游戏根目录路径。
        """
        resolved_game_path = resolve_game_directory(game_path)
        game_title = read_game_title(resolved_game_path)
        game_data = await load_game_data_from_path(resolved_game_path)
        self.items[game_title] = game_data


__all__: list[str] = ["GameDataManager"]
