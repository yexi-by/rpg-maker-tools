"""
标准 data 目录文本提取模块。

提取器处理 RPG Maker MZ 官方数据文件中的玩家可见文本。标准事件命令里的
对白、选项和滚动文本由本模块直接提取。
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
from app.rmmz.text_rules import TextRules
from app.rmmz.commands import iter_all_commands

NARRATION_ROLE = "旁白"
type CommandListKey = tuple[str | int, ...]


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
        pending_scroll_item: TranslationItem | None = None
        pending_scroll_file_name: str | None = None
        pending_scroll_list_key: CommandListKey | None = None
        pending_scroll_last_index: int | None = None

        def flush_scroll_item() -> None:
            """把连续滚动文本作为一个翻译单元写入结果集。"""
            nonlocal pending_scroll_file_name
            nonlocal pending_scroll_item
            nonlocal pending_scroll_last_index
            nonlocal pending_scroll_list_key
            if pending_scroll_item is not None and pending_scroll_file_name is not None:
                translation_data_map[pending_scroll_file_name].translation_items.append(
                    pending_scroll_item
                )
            pending_scroll_item = None
            pending_scroll_file_name = None
            pending_scroll_list_key = None
            pending_scroll_last_index = None

        for path, display_name, command in iter_all_commands(self.game_data):
            file_name_value = path[0]
            if not isinstance(file_name_value, str):
                flush_scroll_item()
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
                flush_scroll_item()
                self._handle_name_command(command=command, items=items, location_path=location_path)
            elif command.code == Code.TEXT:
                flush_scroll_item()
                self._handle_text_command(command=command, items=items, location_path=location_path)
            elif command.code == Code.CHOICES:
                flush_scroll_item()
                self._handle_choices_command(command=command, items=items, location_path=location_path)
            elif command.code == Code.SCROLL_TEXT:
                list_key = _command_list_key(path)
                command_index = _command_index(path)
                if (
                    pending_scroll_item is not None
                    and (
                        pending_scroll_file_name != file_name
                        or pending_scroll_list_key != list_key
                        or pending_scroll_last_index is None
                        or command_index != pending_scroll_last_index + 1
                    )
                ):
                    flush_scroll_item()

                text = self._extract_text_value(command)
                if text is None:
                    flush_scroll_item()
                    continue

                if pending_scroll_item is None:
                    pending_scroll_item = self._build_scroll_text_item(
                        text=text,
                        location_path=location_path,
                    )
                    pending_scroll_file_name = file_name
                    pending_scroll_list_key = list_key
                else:
                    pending_scroll_item.original_lines.append(text)
                    pending_scroll_item.source_line_paths.append(location_path)
                pending_scroll_last_index = command_index
            else:
                flush_scroll_item()
        flush_scroll_item()
        return self._filter_translation_data_map(translation_data_map)

    def _extract_system_text(self) -> dict[str, TranslationData]:
        """提取 `System.json` 中的系统词汇和提示消息。"""
        translation_data = TranslationData(display_name=None, translation_items=[])
        system = self.game_data.system

        if self._should_extract_text(system.gameTitle):
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
                if text is None or not self._should_extract_text(text):
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
                if text is None or not self._should_extract_text(text):
                    continue
                translation_data.translation_items.append(
                    TranslationItem(
                        location_path=f"{SYSTEM_FILE_NAME}/terms/{key}/{index}",
                        item_type="short_text",
                        original_lines=[text],
                    )
                )

        for key, value in system.terms.messages.items():
            if not self._should_extract_text(value):
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
                    if not self._should_extract_text(text):
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

    def _handle_text_command(
        self,
        *,
        command: EventCommand,
        items: list[TranslationItem],
        location_path: str,
    ) -> None:
        """处理 TEXT 指令。"""
        if not items:
            return
        current_item = items[-1]
        if current_item.item_type != "long_text":
            return

        text = self._extract_text_value(command)
        if text is not None:
            current_item.original_lines.append(text)
            current_item.source_line_paths.append(location_path)

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
        if not self._should_extract_lines(original_lines):
            return

        items.append(
            TranslationItem(
                role=NARRATION_ROLE,
                location_path=location_path,
                item_type="array",
                original_lines=original_lines,
            )
        )

    def _build_scroll_text_item(self, *, text: str, location_path: str) -> TranslationItem:
        """创建滚动文本翻译单元。"""
        return TranslationItem(
            role=NARRATION_ROLE,
            location_path=location_path,
            item_type="long_text",
            original_lines=[text],
            source_line_paths=[location_path],
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

    def _should_extract_text(self, text: str | None) -> bool:
        """判断单条原文是否需要进入正文翻译流程。"""
        if text is None:
            return False
        return self.text_rules.should_translate_source_text(text)

    def _should_extract_lines(self, lines: list[str]) -> bool:
        """判断多行原文是否至少包含一处需要翻译的源语言字符。"""
        return self.text_rules.should_translate_source_lines(lines)

    def _filter_translation_data_map(
        self,
        translation_data_map: dict[str, TranslationData],
    ) -> dict[str, TranslationData]:
        """移除整条原文都不含源语言字符的条目。"""
        filtered_map: dict[str, TranslationData] = {}
        for file_name, translation_data in translation_data_map.items():
            filtered_items = [
                item
                for item in translation_data.translation_items
                if self._should_extract_lines(item.original_lines)
            ]
            if not filtered_items:
                continue
            filtered_map[file_name] = TranslationData(
                display_name=translation_data.display_name,
                translation_items=filtered_items,
            )
        return filtered_map


__all__: list[str] = ["DataTextExtraction"]


def _command_list_key(path: list[str | int]) -> CommandListKey:
    """返回事件指令所在列表的稳定键。"""
    return tuple(path[:-1])


def _command_index(path: list[str | int]) -> int:
    """读取事件指令在当前列表中的下标。"""
    index_value = path[-1]
    if not isinstance(index_value, int):
        raise TypeError(f"事件指令路径末段必须是整数: {path}")
    return index_value
