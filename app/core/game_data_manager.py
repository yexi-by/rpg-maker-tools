"""
全局游戏数据管理模块。

本模块负责在进程内统一管理多个 `GameData`，
调用方通过游戏路径触发加载，再按 `game_title` 持有结果。
"""

from pathlib import Path

from app.models.schemas import GameData
from app.utils.database_utils import read_game_title, resolve_game_directory
from app.utils.game_loader_utils import (
    load_game_data as load_game_data_from_path,
    resolve_game_source_paths,
)
from app.utils.log_utils import logger


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
        source_data_dir, source_plugins_path, has_origin_backup = (
            resolve_game_source_paths(resolved_game_path)
        )
        game_data = await load_game_data_from_path(resolved_game_path)

        if has_origin_backup:
            logger.warning(
                f"[tag.warning]检测到该游戏已经执行过激活版回写，后续将始终读取原件[/tag.warning] "
                f"游戏 [tag.count]{game_title}[/tag.count] "
                f"原件数据 [tag.path]{source_data_dir}[/tag.path] "
                f"原件插件 [tag.path]{source_plugins_path}[/tag.path]"
            )

        self.items[game_title] = game_data


__all__: list[str] = ["GameDataManager"]
