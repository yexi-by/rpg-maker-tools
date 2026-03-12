"""
`plugins.js` 文本提取测试。

覆盖重点：
1. 只保留真正含日文字符的插件文本。
2. 布尔值、纯数字、颜色值、明显 ASCII 配置值会被源头过滤。
3. 即使包含日文字符，只要整体像资源文件名，也不会提取。
4. 被二次序列化的 JSON 容器仍可递归提取其中真正的日文文本。
"""

from __future__ import annotations

import json
import unittest

from app.extraction import PluginTextExtraction
from app.models.schemas import GameData


def build_game_data_with_plugins(plugins_js: list[dict[str, object]]) -> GameData:
    """
    构造仅包含 `plugins.js` 数据的测试用 `GameData`。

    Args:
        plugins_js: 待测试的插件配置列表。

    Returns:
        最小可用的 `GameData` 替身对象。
    """
    return GameData.model_construct(
        data={},
        writable_data={},
        map_data={},
        system=None,
        common_events=[],
        troops=[],
        base_data={},
        plugins_js=plugins_js,
        writable_plugins_js=[],
    )


class PluginTextExtractionTestCase(unittest.TestCase):
    def test_extract_all_text_filters_config_noise_at_source(self) -> None:
        plugins_js: list[dict[str, object]] = [
            {
                "name": "TestPlugin",
                "parameters": {
                    "MessageObtained": "を手に入れた！",
                    "Decoration_Text": "GET!",
                    "Decoration_TextColor": "0",
                    "Color_NameWindow": "#ffff55",
                    "Enable_MessageSpeedOption": "true",
                    "SwitchAutoMessageProceedKey": "tab",
                    "BetweenNameAndValue": ":",
                    "resourceText": "回想.jpg",
                    "kanjiName": "装備",
                    "textContainer": json.dumps(
                        {
                            "title": "スタッフロール",
                            "fontSize": "30",
                            "textColor": "#ffffff",
                            "icon": "mumasaria/2",
                            "imageText": "立ち絵01.png",
                        },
                        ensure_ascii=False,
                    ),
                    "contentList": json.dumps(
                        [
                            {"helpText": "設定を開きます。"},
                            {"helpText": "center"},
                            {"helpText": "5"},
                        ],
                        ensure_ascii=False,
                    ),
                },
            }
        ]
        game_data: GameData = build_game_data_with_plugins(plugins_js)

        result = PluginTextExtraction(game_data).extract_all_text()

        self.assertIn("plugins.js", result)
        items = result["plugins.js"].translation_items
        extracted_map = {
            item.location_path: item.original_lines[0]
            for item in items
        }

        self.assertEqual(
            extracted_map,
            {
                "plugins.js/0/MessageObtained": "を手に入れた！",
                "plugins.js/0/kanjiName": "装備",
                "plugins.js/0/textContainer/title": "スタッフロール",
                "plugins.js/0/contentList/0/helpText": "設定を開きます。",
            },
        )

    def test_extract_all_text_returns_empty_when_only_noise_exists(self) -> None:
        plugins_js: list[dict[str, object]] = [
            {
                "name": "NoiseOnlyPlugin",
                "parameters": {
                    "messageSwitch": "false",
                    "messageWidth": "640",
                    "textColor": "#ffffff",
                    "textAlign": "center",
                    "commandScript": "SceneManager.callCustomMenu('Scene_State');",
                    "helpImageText": "回想.png",
                },
            }
        ]
        game_data: GameData = build_game_data_with_plugins(plugins_js)

        result = PluginTextExtraction(game_data).extract_all_text()

        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
