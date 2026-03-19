"""
插件文本规则驱动提取模块。

本模块不再根据字段关键词猜测插件文本，而是严格按照数据库中保存的
插件路径规则，从 `plugins.js` 中展开命中的精确叶子路径并生成 `TranslationItem`。
"""

from __future__ import annotations

from app.models.schemas import (
    GameData,
    PLUGINS_FILE_NAME,
    PluginTextRuleRecord,
    SourceLanguage,
    TranslationData,
    TranslationItem,
)
from app.plugin_text import (
    expand_rule_to_leaf_paths,
    jsonpath_to_location_path,
    resolve_plugin_leaves,
)
from app.utils.source_language_utils import passes_plugin_text_language_filter


class PluginTextExtraction:
    """
    插件文本规则驱动提取器。

    该提取器只消费已经分析成功并落库的路径规则：
    1. 不再做字段名关键词筛选。
    2. 不再根据源语言判断文本是否像自然语言。
    3. 只要规则命中非空字符串，就生成可回写的 `TranslationItem`。
    """

    def __init__(
        self,
        game_data: GameData,
        plugin_rule_records: list[PluginTextRuleRecord],
        source_language: SourceLanguage,
    ) -> None:
        """
        初始化插件文本提取器。

        Args:
            game_data: 已加载到内存的游戏数据。
            plugin_rule_records: 当前游戏有效的插件路径规则快照列表。
            source_language: 当前游戏源语言，用于执行额外的语言放行过滤。
        """

        self.game_data: GameData = game_data
        self.plugin_rule_records: list[PluginTextRuleRecord] = plugin_rule_records
        self.source_language: SourceLanguage = source_language

    def extract_all_text(self) -> dict[str, TranslationData]:
        """
        按规则全量提取 `plugins.js` 中的可翻译文本。

        Returns:
            存在可翻译条目时返回仅包含 `plugins.js` 的结果映射，否则返回空字典。
        """

        translation_items: list[TranslationItem] = []
        for rule_record in self.plugin_rule_records:
            if rule_record.status != "success" or not rule_record.translate_rules:
                continue
            if rule_record.plugin_index >= len(self.game_data.plugins_js):
                continue
            translation_items.extend(
                self._extract_plugin_items(rule_record=rule_record)
            )

        if not translation_items:
            return {}

        return {
            PLUGINS_FILE_NAME: TranslationData(
                display_name=None,
                translation_items=translation_items,
            )
        }

    def _extract_plugin_items(
        self,
        *,
        rule_record: PluginTextRuleRecord,
    ) -> list[TranslationItem]:
        """
        根据单个插件的规则快照提取正文条目。

        Args:
            rule_record: 当前插件的最新规则快照。

        Returns:
            当前插件命中的正文条目列表。
        """

        plugin = self.game_data.plugins_js[rule_record.plugin_index]
        resolved_leaves = resolve_plugin_leaves(plugin)
        string_leaf_map = {
            leaf.path: leaf.value
            for leaf in resolved_leaves
            if leaf.value_type == "string"
        }
        translation_items: list[TranslationItem] = []
        seen_leaf_paths: set[str] = set()

        for translate_rule in rule_record.translate_rules:
            matched_paths = expand_rule_to_leaf_paths(
                path_template=translate_rule.path_template,
                resolved_leaves=resolved_leaves,
            )
            for leaf_path in matched_paths:
                if leaf_path in seen_leaf_paths:
                    continue
                seen_leaf_paths.add(leaf_path)

                leaf_value = string_leaf_map.get(leaf_path)
                if not isinstance(leaf_value, str):
                    continue

                normalized_value = leaf_value.strip()
                if not normalized_value:
                    continue
                if not passes_plugin_text_language_filter(
                    text=normalized_value,
                    source_language=self.source_language,
                ):
                    continue

                translation_items.append(
                    TranslationItem(
                        location_path=jsonpath_to_location_path(
                            json_path=leaf_path,
                            plugin_index=rule_record.plugin_index,
                        ),
                        item_type="short_text",
                        original_lines=[normalized_value],
                    )
                )

        return translation_items


__all__: list[str] = ["PluginTextExtraction"]
