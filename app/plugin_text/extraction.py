"""
插件文本规则驱动提取模块。

本模块严格按照数据库中的插件路径规则，从 `plugins.js` 展开命中的字符串叶子。
日文核心版只保留日文文本放行规则，不再兼容英文游戏。
"""

from __future__ import annotations

from app.rmmz.schema import (
    GameData,
    PLUGINS_FILE_NAME,
    PluginTextRuleRecord,
    TranslationData,
    TranslationItem,
)
from app.plugin_text.common import (
    expand_rule_to_leaf_paths,
    jsonpath_to_location_path,
    resolve_plugin_leaves,
)
from app.rmmz.text_rules import TextRules


class PluginTextExtraction:
    """插件文本规则驱动提取器。"""

    def __init__(
        self,
        game_data: GameData,
        plugin_rule_records: list[PluginTextRuleRecord],
        text_rules: TextRules,
    ) -> None:
        """初始化插件文本提取器。"""
        self.game_data: GameData = game_data
        self.plugin_rule_records: list[PluginTextRuleRecord] = plugin_rule_records
        self.text_rules: TextRules = text_rules

    def extract_all_text(self) -> dict[str, TranslationData]:
        """按规则全量提取 `plugins.js` 中的可翻译文本。"""
        translation_items: list[TranslationItem] = []
        for rule_record in self.plugin_rule_records:
            if rule_record.status != "success" or not rule_record.translate_rules:
                continue
            if rule_record.plugin_index >= len(self.game_data.plugins_js):
                continue
            translation_items.extend(self._extract_plugin_items(rule_record=rule_record))

        if not translation_items:
            return {}

        return {
            PLUGINS_FILE_NAME: TranslationData(
                display_name=None,
                translation_items=translation_items,
            )
        }

    def _extract_plugin_items(self, *, rule_record: PluginTextRuleRecord) -> list[TranslationItem]:
        """根据单个插件规则快照提取正文条目。"""
        plugin = self.game_data.plugins_js[rule_record.plugin_index]
        resolved_leaves = resolve_plugin_leaves(plugin)
        string_leaf_map = {
            leaf.path: leaf.value for leaf in resolved_leaves if leaf.value_type == "string"
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
                if not self.text_rules.passes_plugin_text_language_filter(normalized_value):
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
