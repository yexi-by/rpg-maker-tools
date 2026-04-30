"""插件配置 JSON 导出模块。

负责把 RPG Maker MZ `js/plugins.js` 中的 `$plugins` 数组导出为纯 JSON 文件，
供外部 Agent 直接阅读原始插件配置结构。
"""

import json
from pathlib import Path

import aiofiles

from app.rmmz.schema import GameData


async def export_plugins_json_file(*, game_data: GameData, output_path: Path) -> None:
    """把已加载游戏的 `$plugins` 数组写成 JSON 文件。"""
    resolved_output_path = output_path.resolve()
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(game_data.plugins_js, ensure_ascii=False, indent=2)
    async with aiofiles.open(resolved_output_path, "w", encoding="utf-8") as file:
        _ = await file.write(f"{payload}\n")


__all__: list[str] = ["export_plugins_json_file"]
