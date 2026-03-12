"""
`Code.PLUGIN_TEXT(357)` 事件指令的提取与回写测试。

覆盖以下关键场景：
1. 提取层能从 357 参数容器中抽取命中关键词的短文本。
2. 回写层能把短文本译文准确写回 357 指令的参数树。
3. 路径、命令码或参数结构不匹配时，会抛出明确异常。
4. 占位符构建与恢复能正确处理 357 场景中的 RM 控制符。
"""

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.extraction import DataTextExtraction
from app.models.loaders import load_game_data
from app.models.schemas import TranslationItem
from app.write_back.data_text_write_back import write_data_text


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


def _build_map_data(commands: list[dict[str, Any]]) -> dict[str, Any]:
    """
    构造仅包含一个事件页的最小地图数据。
    """
    return {
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
                        "list": commands,
                    }
                ],
            },
        ],
    }


async def _load_test_game(commands: list[dict[str, Any]]) -> Any:
    """
    构造临时游戏目录并加载成 `GameData`。
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        game_root: Path = Path(temp_dir)
        data_dir: Path = game_root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        _write_json(data_dir / "System.json", _build_system_data())
        _write_json(data_dir / "CommonEvents.json", [])
        _write_json(data_dir / "Troops.json", [])
        _write_json(data_dir / "Map001.json", _build_map_data(commands))

        return await load_game_data(game_root)


class PluginCommand357TestCase(unittest.IsolatedAsyncioTestCase):
    """
    357 指令提取与回写的专项测试。
    """

    async def test_extract_plugin_text_command_collects_matching_fields(self) -> None:
        """
        357 指令应只提取命中关键词的字符串叶子，并保留精确路径。
        """
        game_data = await _load_test_game(
            [
                {
                    "code": 357,
                    "parameters": [
                        "LL_InfoPopupWIndow",
                        "showMessage",
                        "メッセージを表示",
                        {
                            "messageText": "\\fs[30]\\c[27]❤双子の囁き❤",
                            "showCount": "-1",
                            "windowX": "auto",
                            "speakerName": "ティナ",
                            "nested": {
                                "descText": "説明文",
                                "ignored": "不应提取",
                            },
                            "messageList": [
                                "第一行",
                                {"descLine": "第二行"},
                                {"ignored": "第三行"},
                            ],
                            "meta": {
                                "innerName": "内部名称",
                            },
                            "nameTextList": [
                                "名单A",
                                "名单B",
                            ],
                        },
                    ],
                }
            ]
        )

        translation_data_map = DataTextExtraction(game_data).extract_all_text()
        self.assertIn("Map001.json", translation_data_map)

        items = translation_data_map["Map001.json"].translation_items
        extracted_paths: set[str] = {item.location_path for item in items}

        self.assertEqual(
            extracted_paths,
            {
                "Map001.json/1/0/0/parameters/3/messageText",
                "Map001.json/1/0/0/parameters/3/speakerName",
                "Map001.json/1/0/0/parameters/3/nested/descText",
                "Map001.json/1/0/0/parameters/3/messageList/0",
                "Map001.json/1/0/0/parameters/3/messageList/1/descLine",
                "Map001.json/1/0/0/parameters/3/meta/innerName",
                "Map001.json/1/0/0/parameters/3/nameTextList/0",
                "Map001.json/1/0/0/parameters/3/nameTextList/1",
            },
        )

        sample_item = next(
            item
            for item in items
            if item.location_path == "Map001.json/1/0/0/parameters/3/messageText"
        )
        self.assertEqual(
            sample_item.original_lines,
            ["\\fs[30]\\c[27]❤双子の囁き❤"],
        )

    async def test_extract_plugin_text_command_excludes_font_name_and_file_name(self) -> None:
        """
        `fontName` 与 `fileName` 虽然包含 `name`，但它们是资源标识，不应被提取。
        """
        game_data = await _load_test_game(
            [
                {
                    "code": 357,
                    "parameters": [
                        "PluginA",
                        "showMessage",
                        "显示消息",
                        {
                            "fontName": "uzu",
                            "fileName": "face_01",
                            "messageText": "真正正文",
                        },
                    ],
                }
            ]
        )

        translation_data_map = DataTextExtraction(game_data).extract_all_text()
        items = translation_data_map["Map001.json"].translation_items
        extracted_paths: set[str] = {item.location_path for item in items}

        self.assertEqual(
            extracted_paths,
            {
                "Map001.json/1/0/0/parameters/3/messageText",
            },
        )

    async def test_write_data_text_updates_plugin_text_command_fields(self) -> None:
        """
        357 指令中的短文本译文应能按路径精准写回参数容器。
        """
        game_data = await _load_test_game(
            [
                {
                    "code": 357,
                    "parameters": [
                        "LL_InfoPopupWIndow",
                        "showMessage",
                        "メッセージを表示",
                        {
                            "messageText": "原始消息",
                            "messageList": [
                                "原始第一行",
                                {"descLine": "原始第二行"},
                            ],
                        },
                    ],
                }
            ]
        )

        write_data_text(
            game_data=game_data,
            items=[
                TranslationItem(
                    location_path="Map001.json/1/0/0/parameters/3/messageText",
                    item_type="short_text",
                    translation_lines=["译文消息"],
                ),
                TranslationItem(
                    location_path="Map001.json/1/0/0/parameters/3/messageList/0",
                    item_type="short_text",
                    translation_lines=["译文第一行"],
                ),
                TranslationItem(
                    location_path="Map001.json/1/0/0/parameters/3/messageList/1/descLine",
                    item_type="short_text",
                    translation_lines=["译文第二行"],
                ),
            ],
        )

        command = game_data.writable_data["Map001.json"]["events"][1]["pages"][0]["list"][0]
        parameters = command["parameters"][3]

        self.assertEqual(parameters["messageText"], "译文消息")
        self.assertEqual(parameters["messageList"][0], "译文第一行")
        self.assertEqual(parameters["messageList"][1]["descLine"], "译文第二行")

    async def test_write_data_text_rejects_non_plugin_text_command(self) -> None:
        """
        如果路径命中到的实际指令不是 357，应拒绝写回短文本。
        """
        game_data = await _load_test_game(
            [
                {
                    "code": 102,
                    "parameters": [["选项1", "选项2"]],
                }
            ]
        )

        with self.assertRaisesRegex(RuntimeError, "不是 PLUGIN_TEXT 指令"):
            write_data_text(
                game_data=game_data,
                items=[
                    TranslationItem(
                        location_path="Map001.json/1/0/0/parameters/0/messageText",
                        item_type="short_text",
                        translation_lines=["错误译文"],
                    )
                ],
            )

    async def test_write_data_text_rejects_invalid_plugin_text_path(self) -> None:
        """
        如果 357 路径试图把普通字符串参数当作容器继续下钻，应抛出错误。
        """
        game_data = await _load_test_game(
            [
                {
                    "code": 357,
                    "parameters": [
                        "LL_InfoPopupWIndow",
                        "showMessage",
                        "メッセージを表示",
                        {
                            "messageText": "原始消息",
                        },
                    ],
                }
            ]
        )

        with self.assertRaisesRegex(ValueError, "无法继续下钻"):
            write_data_text(
                game_data=game_data,
                items=[
                    TranslationItem(
                        location_path="Map001.json/1/0/0/parameters/1/messageText",
                        item_type="short_text",
                        translation_lines=["错误译文"],
                    )
                ],
            )


class PluginCommand357PlaceholderTestCase(unittest.TestCase):
    """
    357 文本占位符处理的回归测试。
    """

    def test_placeholder_round_trip_preserves_rm_control_codes(self) -> None:
        """
        `\\fs[30]\\c[27]` 这类控制符应能被正确替换并无损恢复。
        """
        item = TranslationItem(
            location_path="Map001.json/1/0/0/parameters/3/messageText",
            item_type="short_text",
            original_lines=["\\fs[30]\\c[27]❤双子の囁き❤"],
        )

        item.build_placeholders()
        self.assertEqual(
            item.original_lines_with_placeholders,
            ["[FS_30][C_27]❤双子の囁き❤"],
        )
        self.assertEqual(
            item.placeholder_map,
            {
                "[FS_30]": "\\fs[30]",
                "[C_27]": "\\c[27]",
            },
        )
        self.assertEqual(
            item.placeholder_counts,
            {
                "[FS_30]": 1,
                "[C_27]": 1,
            },
        )

        item.translation_lines_with_placeholders = list(
            item.original_lines_with_placeholders
        )
        item.verify_placeholders()
        item.restore_placeholders()

        self.assertEqual(
            item.translation_lines,
            ["\\fs[30]\\c[27]❤双子の囁き❤"],
        )


if __name__ == "__main__":
    unittest.main()
