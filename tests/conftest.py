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
            {
                "id": 2,
                "list": [
                    {"code": 101, "parameters": [0, 0, 0, 2, "案内人"]},
                    {"code": 401, "parameters": [r"\F[GuideA]テスト一行目です。\!"]},
                    {"code": 401, "parameters": [r"\C[4]重要語\C[0]を含む二行目です。"]},
                    {"code": 401, "parameters": ["Plain English helper line"]},
                    {"code": 102, "parameters": [["第一選択", "English Choice"], 0, 0, 2, 0]},
                    {"code": 405, "parameters": ["スクロール一行目"]},
                    {"code": 405, "parameters": ["スクロール二行目"]},
                    {"code": 405, "parameters": [""]},
                    {"code": 405, "parameters": [r"\F[ScrollFace]別スクロール"]},
                    {
                        "code": 357,
                        "parameters": [
                            "ComplexPlugin",
                            "ShowWindow",
                            0,
                            {
                                "window": {
                                    "title": "複雑タイトル",
                                    "body": "複雑本文",
                                },
                                "choices": ["第一項目", "第二項目"],
                                "file": "img/pictures/Window.png",
                            },
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
                {
                    "id": 2,
                    "name": "案内イベント",
                    "note": "",
                    "pages": [
                        {
                            "list": [
                                {"code": 101, "parameters": [0, 0, 0, 2, "案内人"]},
                                {"code": 401, "parameters": [r"\F[MapFace]マップ案内です。"]},
                                {"code": 401, "parameters": ["重要地点へ進みます。"]},
                                {"code": 102, "parameters": [["進む", "戻る"], 0, 0, 2, 0]},
                                {"code": 0, "parameters": []},
                            ]
                        }
                    ],
                },
            ],
        },
    )
    write_json(
        data_dir / "Map002.json",
        {
            "displayName": "第二テスト地点",
            "note": "",
            "events": [
                None,
                {
                    "id": 1,
                    "name": "説明役",
                    "note": "",
                    "pages": [
                        {
                            "list": [
                                {"code": 101, "parameters": [0, 0, 0, 2, "説明役"]},
                                {"code": 401, "parameters": ["別マップの本文です。"]},
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
    write_json(
        data_dir / "Items.json",
        [
            None,
            {
                "id": 1,
                "name": "回復薬",
                "note": "",
                "description": "体力を回復する。",
            },
        ],
    )
    write_json(
        data_dir / "Skills.json",
        [
            None,
            {
                "id": 1,
                "name": "火の術",
                "note": "",
                "description": "炎で攻撃する。",
                "message1": "は火の術を唱えた！",
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
                "List": json.dumps(
                    [
                        {"text": "配列本文", "file": "Window.png"},
                        {"text": "二つ目の本文", "enabled": "true"},
                    ],
                    ensure_ascii=False,
                ),
                "File": "img/pictures/Actor1.png",
                "Count": "123",
            },
        },
        {
            "name": "ComplexPlugin",
            "status": True,
            "description": "複雑テスト",
            "parameters": {
                "Window": json.dumps(
                    {
                        "title": "ウィンドウ見出し",
                        "body": "ウィンドウ本文",
                        "font": "GameFont",
                    },
                    ensure_ascii=False,
                ),
                "Rows": json.dumps(
                    [
                        {"label": "一行目", "path": "img/system/Icon.png"},
                        {"label": "二行目", "value": "auto"},
                    ],
                    ensure_ascii=False,
                ),
            },
        },
    ]
    plugins_text = f"var $plugins = {json.dumps(plugins, ensure_ascii=False, indent=2)};\n"
    _ = (js_dir / "plugins.js").write_text(plugins_text, encoding="utf-8")
    return game_root
