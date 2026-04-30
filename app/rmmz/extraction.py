"""
标准 data 目录文本提取模块。

提取器处理 RPG Maker MZ 官方数据文件中的玩家可见文本。标准事件命令里的
357 插件命令参数也在本模块按文本规则提取。
"""

from app.rmmz.game_data import EventCommand
from app.rmmz.schema import (
    Code,
    GameData,
    MAP_PATTERN,
    SYSTEM_FILE_NAME,
    TranslationData,
    TranslationItem,
)
from app.rmmz.text_rules import JsonValue, TextRules
from app.rmmz.commands import iter_all_commands

NARRATION_ROLE = "旁白"


class DataTextExtraction:
    """标准 RMMZ data 文本提取器。"""

    def __init__(self, game_data: GameData, text_rules: TextRules) -> None:
        """初始化提取器。"""
        self.game_data: GameData = game_data
        self.text_rules: TextRules = text_rules

    def extract_all_text(self) -> dict[str, TranslationData]:
        """全量提取标准 `data/` 目录中的可翻译文本。"""
        all_translation_data: dict[str, TranslationData] = {}
        all_translation_data.update(self._extract_command_text())
        all_translation_data.update(self._extract_system_text())
        all_translation_data.update(self._extract_base_text())
        return all_translation_data

    def _extract_command_text(self) -> dict[str, TranslationData]:
        """从地图、公共事件和敌群事件指令中提取文本。"""
        translation_data_map: dict[str, TranslationData] = {}

        for path, display_name, command in iter_all_commands(self.game_data):
            file_name_value = path[0]
            if not isinstance(file_name_value, str):
                continue

            file_name = file_name_value
            if file_name not in translation_data_map:
                map_display_name = display_name if MAP_PATTERN.fullmatch(file_name) else None
                translation_data_map[file_name] = TranslationData(
                    display_name=map_display_name,
                    translation_items=[],
                )

            location_path = "/".join(map(str, path))
            items = translation_data_map[file_name].translation_items

            if command.code == Code.NAME:
                self._handle_name_command(command=command, items=items, location_path=location_path)
            elif command.code == Code.TEXT:
                self._handle_text_command(command=command, items=items)
            elif command.code == Code.CHOICES:
                self._handle_choices_command(command=command, items=items, location_path=location_path)
            elif command.code == Code.SCROLL_TEXT:
                self._handle_scroll_text_command(command=command, items=items, location_path=location_path)
            elif command.code == Code.PLUGIN_TEXT:
                self._handle_plugin_text_command(command=command, items=items, location_path=location_path)

        return {
            file_name: data
            for file_name, data in translation_data_map.items()
            if data.translation_items
        }

    def _extract_system_text(self) -> dict[str, TranslationData]:
        """提取 `System.json` 中的系统词汇和提示消息。"""
        translation_data = TranslationData(display_name=None, translation_items=[])
        system = self.game_data.system

        if system.gameTitle:
            translation_data.translation_items.append(
                TranslationItem(
                    location_path=f"{SYSTEM_FILE_NAME}/gameTitle",
                    item_type="short_text",
                    original_lines=[system.gameTitle],
                )
            )

        lists_to_extract: dict[str, list[str] | list[str | None]] = {
            "elements": system.elements,
            "skillTypes": system.skillTypes,
            "weaponTypes": system.weaponTypes,
            "armorTypes": system.armorTypes,
            "equipTypes": system.equipTypes,
        }
        for key, text_list in lists_to_extract.items():
            for index, text in enumerate(text_list):
                if text is None or text == "":
                    continue
                translation_data.translation_items.append(
                    TranslationItem(
                        location_path=f"{SYSTEM_FILE_NAME}/{key}/{index}",
                        item_type="short_text",
                        original_lines=[text],
                    )
                )

        terms_lists: dict[str, list[str] | list[str | None]] = {
            "basic": system.terms.basic,
            "commands": system.terms.commands,
            "params": system.terms.params,
        }
        for key, text_list in terms_lists.items():
            for index, text in enumerate(text_list):
                if text is None or text == "":
                    continue
                translation_data.translation_items.append(
                    TranslationItem(
                        location_path=f"{SYSTEM_FILE_NAME}/terms/{key}/{index}",
                        item_type="short_text",
                        original_lines=[text],
                    )
                )

        for key, value in system.terms.messages.items():
            if not value:
                continue
            translation_data.translation_items.append(
                TranslationItem(
                    location_path=f"{SYSTEM_FILE_NAME}/terms/messages/{key}",
                    item_type="short_text",
                    original_lines=[value],
                )
            )

        if not translation_data.translation_items:
            return {}
        return {SYSTEM_FILE_NAME: translation_data}

    def _extract_base_text(self) -> dict[str, TranslationData]:
        """提取基础数据库文件中的文本属性。"""
        translation_data_map: dict[str, TranslationData] = {}

        for file_name, data in self.game_data.base_data.items():
            translation_data = TranslationData(display_name=None, translation_items=[])
            for base_item in data:
                if base_item is None:
                    continue

                texts_to_extract: dict[str, str] = {
                    "name": base_item.name,
                    "nickname": base_item.nickname,
                    "profile": base_item.profile,
                    "description": base_item.description,
                    "message1": base_item.message1,
                    "message2": base_item.message2,
                    "message3": base_item.message3,
                    "message4": base_item.message4,
                }
                for key, text in texts_to_extract.items():
                    if not text:
                        continue
                    translation_data.translation_items.append(
                        TranslationItem(
                            location_path=f"{file_name}/{base_item.id}/{key}",
                            item_type="short_text",
                            original_lines=[text],
                        )
                    )

            if translation_data.translation_items:
                translation_data_map[file_name] = translation_data

        return translation_data_map

    def _handle_name_command(
        self,
        *,
        command: EventCommand,
        items: list[TranslationItem],
        location_path: str,
    ) -> None:
        """处理 NAME 指令，并创建后续长文本容器。"""
        role = NARRATION_ROLE
        if len(command.parameters) >= 5:
            role_value = command.parameters[4]
            if isinstance(role_value, str) and role_value.strip():
                role = role_value.strip()

        items.append(
            TranslationItem(
                role=role,
                location_path=location_path,
                item_type="long_text",
                original_lines=[],
            )
        )

    def _handle_text_command(self, *, command: EventCommand, items: list[TranslationItem]) -> None:
        """处理 TEXT 指令。"""
        if not items:
            return
        current_item = items[-1]
        if current_item.item_type != "long_text":
            return

        text = self._extract_text_value(command)
        if text is not None:
            current_item.original_lines.append(text)

    def _handle_choices_command(
        self,
        *,
        command: EventCommand,
        items: list[TranslationItem],
        location_path: str,
    ) -> None:
        """处理 CHOICES 指令。"""
        if not command.parameters:
            return

        choices_value = command.parameters[0]
        if not isinstance(choices_value, list):
            return

        original_lines = [item for item in choices_value if isinstance(item, str)]
        if not original_lines:
            return

        items.append(
            TranslationItem(
                role=NARRATION_ROLE,
                location_path=location_path,
                item_type="array",
                original_lines=original_lines,
            )
        )

    def _handle_scroll_text_command(
        self,
        *,
        command: EventCommand,
        items: list[TranslationItem],
        location_path: str,
    ) -> None:
        """处理 SCROLL_TEXT 指令。"""
        text = self._extract_text_value(command)
        if text is None:
            return

        items.append(
            TranslationItem(
                role=NARRATION_ROLE,
                location_path=location_path,
                item_type="long_text",
                original_lines=[text],
            )
        )

    def _handle_plugin_text_command(
        self,
        *,
        command: EventCommand,
        items: list[TranslationItem],
        location_path: str,
    ) -> None:
        """处理 357 插件命令参数文本。"""
        if not command.parameters:
            return

        plugin_name = command.parameters[0] if isinstance(command.parameters[0], str) else None
        command_name = (
            command.parameters[1]
            if len(command.parameters) > 1 and isinstance(command.parameters[1], str)
            else None
        )

        for param_index, parameter in enumerate(command.parameters):
            if not isinstance(parameter, dict | list):
                continue
            self._extract_plugin_command_container(
                value=parameter,
                path_parts=[location_path, "parameters", param_index],
                items=items,
                keyword_active=False,
                plugin_name=plugin_name,
                command_name=command_name,
            )

    def _extract_plugin_command_container(
        self,
        *,
        value: JsonValue,
        path_parts: list[str | int],
        items: list[TranslationItem],
        keyword_active: bool,
        plugin_name: str | None,
        command_name: str | None,
    ) -> None:
        """递归扫描 357 参数容器，抽取命中文本规则的字符串叶子。"""
        if isinstance(value, dict):
            for key, child in value.items():
                current_path_parts: list[str | int] = [*path_parts, key]
                key_matched = self.text_rules.should_extract_plugin_command_key(key)

                if isinstance(child, str):
                    if key_matched:
                        self._append_plugin_command_text_item(
                            text=child,
                            path_parts=current_path_parts,
                            items=items,
                            plugin_name=plugin_name,
                            command_name=command_name,
                        )
                    continue

                if isinstance(child, dict | list):
                    self._extract_plugin_command_container(
                        value=child,
                        path_parts=current_path_parts,
                        items=items,
                        keyword_active=keyword_active or key_matched,
                        plugin_name=plugin_name,
                        command_name=command_name,
                    )
            return

        if isinstance(value, list):
            for index, child in enumerate(value):
                current_path_parts = [*path_parts, index]

                if isinstance(child, str):
                    if keyword_active:
                        self._append_plugin_command_text_item(
                            text=child,
                            path_parts=current_path_parts,
                            items=items,
                            plugin_name=plugin_name,
                            command_name=command_name,
                        )
                    continue

                if isinstance(child, dict | list):
                    self._extract_plugin_command_container(
                        value=child,
                        path_parts=current_path_parts,
                        items=items,
                        keyword_active=keyword_active,
                        plugin_name=plugin_name,
                        command_name=command_name,
                    )

    def _append_plugin_command_text_item(
        self,
        *,
        text: str,
        path_parts: list[str | int],
        items: list[TranslationItem],
        plugin_name: str | None,
        command_name: str | None,
    ) -> None:
        """将命中的 357 插件参数文本封装为短文本翻译项。"""
        normalized_text = text.strip()
        if not normalized_text:
            return
        if self.text_rules.should_skip_plugin_command_text(
            text=normalized_text,
            path_parts=path_parts,
            plugin_name=plugin_name,
            command_name=command_name,
        ):
            return
        if not self.text_rules.passes_plugin_command_language_filter(normalized_text):
            return

        items.append(
            TranslationItem(
                location_path="/".join(map(str, path_parts)),
                item_type="short_text",
                original_lines=[normalized_text],
            )
        )

    def _extract_text_value(self, command: EventCommand) -> str | None:
        """从事件指令第一个参数中提取文本。"""
        if not command.parameters:
            return None

        text_value = command.parameters[0]
        if not isinstance(text_value, str):
            return None

        normalized_text = self.text_rules.normalize_extraction_text(text_value)
        if not normalized_text:
            return None
        return normalized_text


__all__: list[str] = ["DataTextExtraction"]
