"""测试夹具：构造最小可用的 RPG Maker MZ 游戏目录。"""

import json
from pathlib import Path

import pytest

from app.rmmz.text_rules import JsonValue


def write_json(path: Path, value: JsonValue) -> None:
    """以 UTF-8 写入 JSON 文件，保持 fixture 内容易读。"""
    _ = path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture
def minimal_game_dir(tmp_path: Path) -> Path:
    """创建只包含核心流程所需文件的最小 RMMZ 游戏目录。"""
    game_root = tmp_path / "mini-game"
    data_dir = game_root / "data"
    js_dir = game_root / "js"
    data_dir.mkdir(parents=True)
    js_dir.mkdir(parents=True)

    write_json(game_root / "package.json", {"window": {"title": "テストゲーム"}})
    write_json(
        data_dir / "System.json",
        {
            "gameTitle": "テストゲーム",
            "terms": {
                "basic": ["", "HP"],
                "commands": ["", "戦う"],
                "params": ["攻撃"],
                "messages": {"alwaysDash": "常時ダッシュ"},
            },
            "elements": ["", "炎"],
            "skillTypes": ["", "魔法"],
            "weaponTypes": ["", "剣"],
            "armorTypes": ["", "盾"],
            "equipTypes": ["", "武器"],
        },
    )
    write_json(
        data_dir / "CommonEvents.json",
        [
            None,
            {
                "id": 1,
                "list": [
                    {"code": 101, "parameters": [0, 0, 0, 2, "アリス"]},
                    {"code": 401, "parameters": ["こんにちは"]},
                    {"code": 102, "parameters": [["はい", "いいえ"], 0, 0, 2, 0]},
                    {"code": 405, "parameters": ["スクロール本文"]},
                    {
                        "code": 357,
                        "parameters": [
                            "TestPlugin",
                            "Show",
                            0,
                            {"message": "プラグイン台詞", "file": "Actor1.png"},
                        ],
                    },
                    {"code": 0, "parameters": []},
                ],
            },
        ],
    )
    write_json(
        data_dir / "Troops.json",
        [
            None,
            {
                "id": 1,
                "pages": [
                    {
                        "list": [
                            {"code": 101, "parameters": [0, 0, 0, 2, "敵"]},
                            {"code": 401, "parameters": ["敵の台詞"]},
                            {"code": 0, "parameters": []},
                        ]
                    }
                ],
            },
        ],
    )
    write_json(
        data_dir / "Map001.json",
        {
            "displayName": "始まりの町",
            "note": "",
            "events": [
                None,
                {
                    "id": 1,
                    "name": "村人",
                    "note": "",
                    "pages": [
                        {
                            "list": [
                                {"code": 101, "parameters": [0, 0, 0, 2, "村人"]},
                                {"code": 401, "parameters": ["マップこんにちは"]},
                                {"code": 0, "parameters": []},
                            ]
                        }
                    ],
                },
            ],
        },
    )
    write_json(
        data_dir / "Actors.json",
        [
            None,
            {
                "id": 1,
                "name": "勇者",
                "note": "",
                "nickname": "ニック",
                "profile": "プロフィール",
            },
        ],
    )
    write_json(data_dir / "UnknownPluginData.json", [{"id": 1, "name": "これは無視される"}])

    plugins: list[JsonValue] = [
        {
            "name": "TestPlugin",
            "status": True,
            "description": "テスト説明",
            "parameters": {
                "Message": "プラグイン本文",
                "Nested": json.dumps({"text": "入れ子本文", "file": "Actor1.png"}, ensure_ascii=False),
                "File": "img/pictures/Actor1.png",
                "Count": "123",
            },
        }
    ]
    plugins_text = f"var $plugins = {json.dumps(plugins, ensure_ascii=False, indent=2)};\n"
    _ = (js_dir / "plugins.js").write_text(plugins_text, encoding="utf-8")
    return game_root
