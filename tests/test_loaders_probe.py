"""
游戏数据加载阶段的对话探针测试。

覆盖两个关键场景：
1. 合法对话结构可以通过 `load_game_data()`。
2. 孤立的 `Code 401` 会在加载阶段被探针拦截，直接阻止启动。
"""

import json
import tempfile
import unittest
from pathlib import Path

from app.models.loaders import load_game_data


def _build_system_data() -> dict[str, object]:
    """
    构造满足 `System` 模型最小要求的测试数据。
    """
    return {
        "gameTitle": "Test Game",
        "terms": {
            "basic": [],
            "commands": [],
            "params": [],
            "messages": {},
        },
        "elements": [],
        "skillTypes": [],
        "weaponTypes": [],
        "armorTypes": [],
        "equipTypes": [],
        "variables": [],
        "switches": [],
    }


def _write_json(path: Path, data: object) -> None:
    """
    以 UTF-8 格式写入测试 JSON 文件。
    """
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class LoadersProbeTestCase(unittest.IsolatedAsyncioTestCase):
    """
    `load_game_data()` 与对话探针的集成测试。
    """

    async def test_load_game_data_allows_valid_dialogue_sequence(self) -> None:
        """
        只要 `Code 401` 前面有合法的 `Code 101`，加载流程就应通过。
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            game_root: Path = Path(temp_dir)
            data_dir: Path = game_root / "data"
            data_dir.mkdir(parents=True, exist_ok=True)

            _write_json(data_dir / "System.json", _build_system_data())
            _write_json(data_dir / "CommonEvents.json", [])
            _write_json(data_dir / "Troops.json", [])
            _write_json(
                data_dir / "Map001.json",
                {
                    "displayName": "Map",
                    "note": "",
                    "events": [
                        None,
                        {
                            "id": 1,
                            "name": "Event1",
                            "note": "",
                            "pages": [
                                {
                                    "list": [
                                        {
                                            "code": 101,
                                            "parameters": [0, 0, 0, 0, "角色"],
                                        },
                                        {
                                            "code": 401,
                                            "parameters": ["你好"],
                                        },
                                    ]
                                }
                            ],
                        },
                    ],
                },
            )

            game_data = await load_game_data(game_root)

        self.assertIn("Map001.json", game_data.map_data)

    async def test_load_game_data_blocks_invalid_dialogue_sequence(self) -> None:
        """
        孤立的 `Code 401` 应在加载阶段直接报错，禁止项目继续启动。
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            game_root: Path = Path(temp_dir)
            data_dir: Path = game_root / "data"
            data_dir.mkdir(parents=True, exist_ok=True)

            _write_json(data_dir / "System.json", _build_system_data())
            _write_json(data_dir / "CommonEvents.json", [])
            _write_json(data_dir / "Troops.json", [])
            _write_json(
                data_dir / "Map001.json",
                {
                    "displayName": "Map",
                    "note": "",
                    "events": [
                        None,
                        {
                            "id": 1,
                            "name": "Event1",
                            "note": "",
                            "pages": [
                                {
                                    "list": [
                                        {
                                            "code": 401,
                                            "parameters": ["孤立文本"],
                                        }
                                    ]
                                }
                            ],
                        },
                    ],
                },
            )

            with self.assertRaisesRegex(ValueError, "对话探针检查失败"):
                await load_game_data(game_root)


if __name__ == "__main__":
    unittest.main()
