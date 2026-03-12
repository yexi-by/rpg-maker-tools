"""
插件文本提取模块。

构造参数接受 `GameData` 作为依赖，专门负责提取 `plugins.js` 中的可翻译文本，
并构造 `TranslationData` 对象返回。

设计约束：
1. 这一层只负责插件文本提取，不与 `data/` 目录文本混用。
2. 这一层只做结构提取，不进行数据库过滤。
3. 这一层会在字符串叶子入库前过滤明显的配置值与非日文内容。
4. 返回值类型为 `dict[str, TranslationData]`。
"""

import json
import re
from pathlib import Path
from typing import Any

from app.models.schemas import (
    GameData,
    PLUGINS_FILE_NAME,
    TranslationData,
    TranslationItem,
)
from app.utils import has_japanese


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
BOOLEAN_TEXTS: set[str] = {"true", "false"}
FILE_LIKE_SUFFIXES: set[str] = {
    ".aac",
    ".avi",
    ".bmp",
    ".css",
    ".csv",
    ".flac",
    ".gif",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".m4a",
    ".mid",
    ".midi",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".otf",
    ".png",
    ".svg",
    ".tif",
    ".ttf",
    ".txt",
    ".wav",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
}
PURE_NUMBER_PATTERN: re.Pattern[str] = re.compile(r"[-+]?\d+(?:\.\d+)?")
HEX_COLOR_PATTERN: re.Pattern[str] = re.compile(r"#[0-9A-Fa-f]{6,8}")
CSS_COLOR_FUNCTION_PATTERN: re.Pattern[str] = re.compile(
    r"(?:rgb|rgba|hsl|hsla)\([^)]*\)",
    flags=re.IGNORECASE,
)
ASCII_CONFIG_PATTERN: re.Pattern[str] = re.compile(r"[A-Za-z0-9_./:\\-]+")


class PluginTextExtraction:
    """
    插件文本提取器。

    专注于从 `GameData.plugins_js` 的树状结构中提取所有配置的文本参数。
    由于插件配置的值往往包含大量无关的数值、开关或者纯代码脚本，
    提取器会根据 `PLUGINS_TEXT_KEYWORDS` 中的关键词对顶层参数键进行过滤，
    然后再对命中的参数进行深度递归解析，并在叶子层剔除明显不该翻译的配置值。
    
    提取的结果将统一归类为一个名为 `plugins.js` 的 `TranslationData`。
    """

    def __init__(self, game_data: GameData) -> None:
        """
        初始化插件文本提取器。

        Args:
            game_data: 已经载入内存的全局游戏数据对象。
        """
        self.game_data: GameData = game_data

    def extract_all_text(self) -> dict[str, TranslationData]:
        """
        全量提取 `plugins.js` 中的可翻译文本。

        该方法会遍历插件列表中的每一个插件，对满足文本过滤规则的 parameters
        执行深度提取，最后将所有命中的短文本条目整合在一起返回。

        Returns:
            一个字典，键始终为 "plugins.js"（如果存在需要翻译的条目的话），
            值为包含所有提取条目的 `TranslationData` 对象。
        """
        translation_data: TranslationData = TranslationData(
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
        处理单个插件配置字典，并在匹配关键词后启动递归提取。

        Args:
            plugin: 当前遍历到的单个插件对象字典。
            plugin_index: 当前插件在 `plugins.js` 数组中的索引位置，用于构建定位路径。
            items: 正在构建的短文本翻译项列表（原地追加）。
        """
        parameters = plugin.get("parameters")
        if not isinstance(parameters, dict):
            return

        for param_key, param_value in parameters.items():
            if not self._should_extract_plugin_key(param_key):
                continue

            self._extract_plugins_recursive(
                value=param_value,
                path_parts=[PLUGINS_FILE_NAME, plugin_index, param_key],
                items=items,
            )

    def _should_extract_plugin_key(self, key: str) -> bool:
        """
        判断插件顶层参数的键名是否包含可能作为文本展示的关键词。

        为什么这样做: 
        插件参数中包含大量不需要翻译的系统变量、颜色代码和布尔值，
        通过关键词匹配能极大地降低不必要的文本提取和翻译成本。

        Args:
            key: 插件 parameter 中的键名。

        Returns:
            如果键名包含指定的关键词集合之一，则返回 True，否则 False。
        """
        key_lower: str = key.lower()
        return any(keyword in key_lower for keyword in PLUGINS_TEXT_KEYWORDS)

    def _extract_plugins_recursive(
        self,
        value: Any,
        path_parts: list[str | int],
        items: list[TranslationItem],
    ) -> None:
        """
        深度递归遍历插件参数的数据结构，并抽取所有叶子节点的字符串。

        在 RM 的插件系统中，有时复杂配置（如数组对象）会被开发者二次序列化为 JSON 字符串
        存储在顶层参数中。该方法除了处理原生的 dict/list 外，还会对字符串进行嗅探，
        如果字符串实质上是一个 JSON 对象，它会自动反序列化并继续递归。

        Args:
            value: 当前递归深度的值。
            path_parts: 用于追踪当前所在层级的路径数组，以便最后构造出精确的 location_path。
            items: 收集翻译文本的列表引用（原地追加）。
        """
        if isinstance(value, str):
            parsed_container = self._try_parse_container_text(value)
            if parsed_container is not None:
                self._extract_plugins_recursive(
                    value=parsed_container,
                    path_parts=path_parts,
                    items=items,
                )
                return

            self._append_text_item(
                text=value,
                path_parts=path_parts,
                items=items,
            )
            return

        if isinstance(value, dict):
            for key, child in value.items():
                self._extract_plugins_recursive(
                    value=child,
                    path_parts=[*path_parts, key],
                    items=items,
                )
            return

        if isinstance(value, list):
            for index, child in enumerate(value):
                self._extract_plugins_recursive(
                    value=child,
                    path_parts=[*path_parts, index],
                    items=items,
                )

    def _try_parse_container_text(
        self,
        value: str,
    ) -> dict[str, Any] | list[Any] | None:
        """
        尝试将疑似被二次序列化的 JSON 字符串还原为原生容器（字典或列表）。

        为什么这样做：
        为了规避 RM 编辑器对于复杂结构编辑UI的限制，很多插件作者会将子级数据对象
        转为 JSON 字符串保存。直接把这些字符串送去翻译容易破坏其内部的引号和语法。
        通过预先嗅探解析，既能保证文本抽取的干净，又能方便后续的回写组装。

        Args:
            value: 需要嗅探的字符串。

        Returns:
            解析成功时返回原生字典或列表。如果仅仅是普通文本则返回 None。
        """
        try:
            parsed: Any = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None

        if isinstance(parsed, dict) or isinstance(parsed, list):
            return parsed
        return None

    def _append_text_item(
        self,
        text: str,
        path_parts: list[str | int],
        items: list[TranslationItem],
    ) -> None:
        """
        将提取到的纯叶子文本节点封装成短文本翻译条目并收集。

        Args:
            text: 提取出的有效字符串。
            path_parts: 从根节点累积到该文本属性的路径序列。
            items: 收集结果的目标列表（原地追加）。
        """
        normalized_text: str = text.strip()
        if not self._should_extract_text_value(normalized_text):
            return

        items.append(
            TranslationItem(
                location_path="/".join(map(str, path_parts)),
                item_type="short_text",
                original_lines=[normalized_text],
            )
        )

    def _should_extract_text_value(self, text: str) -> bool:
        """
        判断叶子字符串是否真的值得进入翻译流程。

        过滤思路：
        1. 先排除空字符串、布尔值、纯数字、颜色值、明显配置型 ASCII。
        2. 再强制要求文本里至少出现日文字符，避免英文配置和脚本误入。
        3. 即使包含日文，只要整体形态像资源文件名（如 `立绘01.png`），仍然排除。

        Args:
            text: 已去掉首尾空白的叶子字符串。

        Returns:
            值得提取时返回 `True`，否则返回 `False`。
        """
        if not text:
            return False

        if self._is_boolean_text(text):
            return False
        if self._is_pure_number_text(text):
            return False
        if self._is_color_text(text):
            return False
        if self._is_obvious_ascii_config_text(text):
            return False
        if not has_japanese(text, mode="non_strict"):
            return False
        if self._looks_like_file_name(text):
            return False
        return True

    def _is_boolean_text(self, text: str) -> bool:
        """
        判断字符串是否为布尔字面量。

        Args:
            text: 待判断字符串。

        Returns:
            布尔字面量时返回 `True`。
        """
        return text.lower() in BOOLEAN_TEXTS

    def _is_pure_number_text(self, text: str) -> bool:
        """
        判断字符串是否为纯数字配置值。

        Args:
            text: 待判断字符串。

        Returns:
            仅由数字构成时返回 `True`。
        """
        return PURE_NUMBER_PATTERN.fullmatch(text) is not None

    def _is_color_text(self, text: str) -> bool:
        """
        判断字符串是否为颜色配置。

        Args:
            text: 待判断字符串。

        Returns:
            十六进制颜色或 CSS 颜色函数时返回 `True`。
        """
        return (
            HEX_COLOR_PATTERN.fullmatch(text) is not None
            or CSS_COLOR_FUNCTION_PATTERN.fullmatch(text) is not None
        )

    def _is_obvious_ascii_config_text(self, text: str) -> bool:
        """
        判断字符串是否为明显的 ASCII 配置值。

        这里专门拦截诸如 `tab`、`center`、`/backup`、`mumasaria/2`
        这种不含日文、看起来像按键名、路径、资源 ID 或对齐枚举的值。

        Args:
            text: 待判断字符串。

        Returns:
            明显 ASCII 配置值时返回 `True`。
        """
        return ASCII_CONFIG_PATTERN.fullmatch(text) is not None

    def _looks_like_file_name(self, text: str) -> bool:
        """
        判断字符串是否整体像资源文件名。

        即使文件名里包含日文字符，也不应作为翻译文本送给模型，
        例如 `立绘01.png`、`回想.jpg` 这类资源引用。

        Args:
            text: 待判断字符串。

        Returns:
            看起来像文件名时返回 `True`。
        """
        suffix: str = Path(text).suffix.lower()
        return suffix in FILE_LIKE_SUFFIXES


__all__: list[str] = ["PluginTextExtraction"]
