"""
插件文本提取模块。

本模块专门负责从 `plugins.js` 中提取可翻译文本，并与正文提取解耦。
提取流程分为两层：
1. 先用统一的“非正文过滤”剔除变量名、配置值、资源名、脚本表达式等明显不该翻译的值。
2. 再根据当前游戏的 `source_language` 判断该文本是否像自然语言。

这样既能保留英文游戏中的真实 UI 文本，也能尽量避免把配置值送入翻译流程。
"""

from __future__ import annotations

import json
from typing import Any

from app.models.schemas import (
    GameData,
    PLUGINS_FILE_NAME,
    SourceLanguage,
    TranslationData,
    TranslationItem,
)
from app.utils import is_plugin_text_candidate, should_skip_plugin_like_text


PLUGINS_TEXT_KEYWORDS: set[str] = {
    "text",
    "message",
    "help",
    "desc",
    "description",
    "name",
    "label",
    "command",
    "title",
    "content",
}


class PluginTextExtraction:
    """
    插件文本提取器。

    该类遍历 `GameData.plugins_js` 中的所有插件配置，递归抽取具备翻译价值的叶子字符串，
    并统一组织成 `plugins.js` 对应的 `TranslationData`。
    """

    def __init__(
        self,
        game_data: GameData,
        source_language: SourceLanguage,
    ) -> None:
        """
        初始化插件文本提取器。

        Args:
            game_data: 已加载到内存的全局游戏数据。
            source_language: 当前游戏的源语言。
        """

        self.game_data: GameData = game_data
        self.source_language: SourceLanguage = source_language

    def extract_all_text(self) -> dict[str, TranslationData]:
        """
        全量提取 `plugins.js` 中的可翻译文本。

        Returns:
            如果存在可翻译条目，则返回仅包含 `plugins.js` 一个键的结果；
            否则返回空字典。
        """

        translation_data = TranslationData(
            display_name=None,
            translation_items=[],
        )

        for plugin_index, plugin in enumerate(self.game_data.plugins_js):
            self._extract_plugin_parameters(
                plugin=plugin,
                plugin_index=plugin_index,
                items=translation_data.translation_items,
            )

        if not translation_data.translation_items:
            return {}
        return {PLUGINS_FILE_NAME: translation_data}

    def _extract_plugin_parameters(
        self,
        plugin: dict[str, Any],
        plugin_index: int,
        items: list[TranslationItem],
    ) -> None:
        """
        处理单个插件对象，并从命中文本型键的参数树里递归提取叶子文本。

        Args:
            plugin: 当前插件配置对象。
            plugin_index: 当前插件在 `plugins.js` 数组中的索引。
            items: 待追加的翻译条目列表。
        """

        parameters = plugin.get("parameters")
        if not isinstance(parameters, dict):
            return

        plugin_name = plugin.get("name")
        if not isinstance(plugin_name, str):
            plugin_name = None

        for param_key, param_value in parameters.items():
            if not self._should_extract_plugin_key(param_key):
                continue

            self._extract_plugins_recursive(
                value=param_value,
                path_parts=[PLUGINS_FILE_NAME, plugin_index, param_key],
                items=items,
                plugin_name=plugin_name,
            )

    def _should_extract_plugin_key(self, key: str) -> bool:
        """
        判断顶层插件参数键名是否像文本类配置。

        Args:
            key: 顶层参数键名。

        Returns:
            只要包含常见文本关键词，就继续递归。
        """

        key_lower = key.lower()
        return any(keyword in key_lower for keyword in PLUGINS_TEXT_KEYWORDS)

    def _extract_plugins_recursive(
        self,
        value: Any,
        path_parts: list[str | int],
        items: list[TranslationItem],
        plugin_name: str | None,
    ) -> None:
        """
        深度递归遍历插件参数结构，并收集叶子字符串。

        Args:
            value: 当前节点值。
            path_parts: 当前节点的完整路径。
            items: 结果列表。
            plugin_name: 当前所属插件名。
        """

        if isinstance(value, str):
            parsed_container = self._try_parse_container_text(value)
            if parsed_container is not None:
                self._extract_plugins_recursive(
                    value=parsed_container,
                    path_parts=path_parts,
                    items=items,
                    plugin_name=plugin_name,
                )
                return

            self._append_text_item(
                text=value,
                path_parts=path_parts,
                items=items,
                plugin_name=plugin_name,
            )
            return

        if isinstance(value, dict):
            for key, child in value.items():
                self._extract_plugins_recursive(
                    value=child,
                    path_parts=[*path_parts, key],
                    items=items,
                    plugin_name=plugin_name,
                )
            return

        if isinstance(value, list):
            for index, child in enumerate(value):
                self._extract_plugins_recursive(
                    value=child,
                    path_parts=[*path_parts, index],
                    items=items,
                    plugin_name=plugin_name,
                )

    def _try_parse_container_text(
        self,
        value: str,
    ) -> dict[str, Any] | list[Any] | None:
        """
        尝试把二次序列化的 JSON 字符串还原成容器对象。

        Args:
            value: 待探测字符串。

        Returns:
            解析成功时返回 `dict` 或 `list`，否则返回 `None`。
        """

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError, TypeError:
            return None

        if isinstance(parsed, dict | list):
            return parsed
        return None

    def _append_text_item(
        self,
        text: str,
        path_parts: list[str | int],
        items: list[TranslationItem],
        plugin_name: str | None,
    ) -> None:
        """
        将叶子文本封装为短文本翻译条目并加入结果集。

        Args:
            text: 当前叶子文本。
            path_parts: 当前叶子节点路径。
            items: 结果列表。
            plugin_name: 当前所属插件名。
        """

        normalized_text = text.strip()
        if not self._should_extract_text_value(
            text=normalized_text,
            path_parts=path_parts,
            plugin_name=plugin_name,
        ):
            return

        items.append(
            TranslationItem(
                location_path="/".join(map(str, path_parts)),
                item_type="short_text",
                original_lines=[normalized_text],
            )
        )

    def _should_extract_text_value(
        self,
        text: str,
        path_parts: list[str | int],
        plugin_name: str | None,
    ) -> bool:
        """
        判断插件叶子字符串是否值得进入翻译流程。

        Args:
            text: 已去掉首尾空白的叶子字符串。
            path_parts: 当前值对应的完整路径。
            plugin_name: 当前所属插件名。

        Returns:
            具备翻译价值时返回 `True`。
        """

        if not text:
            return False
        if should_skip_plugin_like_text(
            text=text,
            path_parts=path_parts,
            plugin_name=plugin_name,
        ):
            return False
        if not is_plugin_text_candidate(
            text=text,
            source_language=self.source_language,
        ):
            return False
        return True


__all__: list[str] = ["PluginTextExtraction"]
