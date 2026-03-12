"""
数据目录文本提取模块。

构造参数接受 `GameData` 作为依赖，负责提取 `data/` 目录中的可翻译游戏文本，
并构造 `TranslationData` 对象返回。

设计约束：
1. 这一层只负责 `data/` 目录文本提取，不进行数据库过滤。
2. 这一层不依赖数据库。
3. 返回值类型为 `dict[str, TranslationData]`。
4. 这一层不处理 `plugins.js`，插件文本由同目录独立模块负责。
5. 只有地图文件（MapXXX.json）的 `display_name` 使用地图显示名称，其余文件一律为 `None`。
"""

from typing import Any

from app.models.game_data import EventCommand
from app.models.schemas import (
    Code,
    GameData,
    MAP_PATTERN,
    SYSTEM_FILE_NAME,
    TranslationData,
    TranslationItem,
)
from app.utils import iter_all_commands

PLUGIN_COMMAND_TEXT_KEYWORDS: set[str] = {
    "text",
    "message",
    "name",
    "desc",
}
PLUGIN_COMMAND_EXCLUDED_KEYS: set[str] = {
    "filename",
    "fontname",
}
NARRATION_ROLE: str = "旁白"


class DataTextExtraction:
    """
    数据目录文本提取器。

    专门负责从已解析的 GameData 中抽取 `data/` 目录下的可翻译文本
    （包括事件指令里的对话、系统术语以及基础数据库说明等），
    并将它们聚合并格式化为统一的 `TranslationData` 结构。
    
    注意：此提取器不涉及插件文本（`plugins.js`）的提取。
    """

    def __init__(self, game_data: GameData) -> None:
        """
        初始化数据目录文本提取器。

        Args:
            game_data: 已完成解析的全局游戏数据聚合模型。
        """
        self.game_data: GameData = game_data

    def extract_all_text(self) -> dict[str, TranslationData]:
        """
        全量提取 `data/` 目录中的可翻译文本。

        该方法将分别调用事件指令、系统配置、基础数据库的专属提取方法，
        最终将散落的零碎文本统一合并为以文件名为维度的翻译数据包。

        Returns:
            一个字典，键为对应的 JSON 文件名（如 "Map001.json", "System.json"），
            值为该文件内包含所有待翻译条目的 `TranslationData` 对象。
        """
        all_translation_data: dict[str, TranslationData] = {}
        all_translation_data.update(self._extract_command_text())
        all_translation_data.update(self._extract_system_text())
        all_translation_data.update(self._extract_base_text())
        return all_translation_data

    def _extract_command_text(self) -> dict[str, TranslationData]:
        """
        从全游戏的事件指令中提取可翻译内容（如对话文本、滚动文本、选项分支）。

        内部使用 `iter_all_commands` 统一遍历地图、公共事件与敌群，
        并针对不同的 Code (NAME, TEXT, CHOICES, SCROLL_TEXT) 进行结构化组装。

        Returns:
            一个字典，包含了从事件指令中抽取的 `TranslationData`，按文件名映射。
        """
        translation_data_map: dict[str, TranslationData] = {}

        for path, display_name, command in iter_all_commands(self.game_data):
            file_name_value = path[0]
            if not isinstance(file_name_value, str):
                continue

            file_name: str = file_name_value
            if file_name not in translation_data_map:
                map_display_name: str | None = (
                    display_name if MAP_PATTERN.fullmatch(file_name) else None
                )
                translation_data_map[file_name] = TranslationData(
                    display_name=map_display_name,
                    translation_items=[],
                )

            location_path: str = "/".join(map(str, path))
            items: list[TranslationItem] = translation_data_map[file_name].translation_items

            match command.code:
                case Code.NAME:
                    self._handle_name_command(
                        command=command,
                        items=items,
                        location_path=location_path,
                    )
                case Code.TEXT:
                    self._handle_text_command(command=command, items=items)
                case Code.CHOICES:
                    self._handle_choices_command(
                        command=command,
                        items=items,
                        location_path=location_path,
                    )
                case Code.SCROLL_TEXT:
                    self._handle_scroll_text_command(
                        command=command,
                        items=items,
                        location_path=location_path,
                    )
                case Code.PLUGIN_TEXT:
                    self._handle_plugin_text_command(
                        command=command,
                        items=items,
                        location_path=location_path,
                    )

        return {
            file_name: data
            for file_name, data in translation_data_map.items()
            if data.translation_items
        }

    def _extract_system_text(self) -> dict[str, TranslationData]:
        """
        提取 `System.json` 文件中的固定系统术语和提示消息。

        该方法会逐个读取游戏标题、属性名、技能类型、武器/防具类型、装备槽，
        以及各种 UI terms 和 messages 文本，并将非空的项封装为短文本翻译条目。

        Returns:
            包含 System.json 提取结果的字典，如果没有任何提取项则返回空字典。
        """
        translation_data: TranslationData = TranslationData(
            display_name=None,
            translation_items=[],
        )
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
            "variables": system.variables,
            "switches": system.switches,
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
        """
        提取基础数据库（如 Actors, Items, Skills 等）中的文本属性。

        该方法会遍历每个对象的常规显示字段（名称、昵称、简介、说明以及四条战斗消息），
        过滤掉空值后生成短文本条目，并附加上精准的定位路径。

        Returns:
            按基础数据库文件名分类的提取结果。
        """
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
        command: EventCommand,
        items: list[TranslationItem],
        location_path: str,
    ) -> None:
        """
        处理 RM 指令中的 NAME (Code 101) 指令。

        在 RM 的对话结构中，101 指令通常意味着接下来的一段 401(TEXT) 属于该角色。
        因此，这里提取出角色名后，会立刻向项目列表追加一个携带该 `role` 的全新长文本条目，
        以供后续的 TEXT 指令继续“挂靠”。

        Args:
            command: 当前的 NAME 事件指令。
            items: 当前正在构建的所在文件的翻译项列表（原地追加）。
            location_path: 当前指令的精确定位路径。
        """
        role: str = NARRATION_ROLE
        if len(command.parameters) >= 5:
            role_value = command.parameters[4]
            if isinstance(role_value, str):
                stripped_role: str = role_value.strip()
                if stripped_role:
                    role = stripped_role

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
        command: EventCommand,
        items: list[TranslationItem],
    ) -> None:
        """
        处理 RM 指令中的 TEXT (Code 401) 指令。

        401 指令包含实际的对话行。如果前序有 101 指令，则该文本会属于前序创立的那个 `long_text` 条目；
        如果连续多个 401，它们将被不断追加进同一个条目的 `original_lines` 中，形成一个完整的段落。

        Args:
            command: 当前的 TEXT 事件指令。
            items: 当前正在构建的翻译项列表（原地追加文本行）。
        """
        if not items:
            return
        current_item: TranslationItem = items[-1]
        if current_item.item_type != "long_text":
            return

        text = self._extract_text_value(command)
        if text is None:
            return
        current_item.original_lines.append(text)

    def _handle_choices_command(
        self,
        command: EventCommand,
        items: list[TranslationItem],
        location_path: str,
    ) -> None:
        """
        处理 CHOICES 指令，构造数组型翻译项。

        Args:
            command: 当前事件指令。
            items: 当前文件的翻译项列表。
            location_path: 当前指令的定位路径。
        """
        if not command.parameters:
            return

        choices_value = command.parameters[0]
        if not isinstance(choices_value, list):
            return

        original_lines: list[str] = [
            item for item in choices_value if isinstance(item, str)
        ]
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
        command: EventCommand,
        items: list[TranslationItem],
        location_path: str,
    ) -> None:
        """
        处理 SCROLL_TEXT 指令，构造单独的长文本翻译项。

        Args:
            command: 当前事件指令。
            items: 当前文件的翻译项列表。
            location_path: 当前指令的定位路径。
        """
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
        command: EventCommand,
        items: list[TranslationItem],
        location_path: str,
    ) -> None:
        """
        处理 `Code.PLUGIN_TEXT(357)` 指令中的插件命令参数文本。

        RPG Maker MZ 的 357 指令会把插件命令的参数对象放进 `parameters` 数组里。
        这里不提取顶层插件名、命令名等普通字符串，只深入 `dict` / `list` 容器，
        并根据键名关键词过滤出真正需要翻译的短文本叶子。

        Args:
            command: 当前的 357 事件指令。
            items: 当前文件正在收集的翻译项列表。
            location_path: 当前指令本身的定位路径。
        """
        if not command.parameters:
            return

        for param_index, parameter in enumerate(command.parameters):
            if not isinstance(parameter, dict | list):
                continue

            self._extract_plugin_command_container(
                value=parameter,
                path_parts=[location_path, "parameters", param_index],
                items=items,
                keyword_active=False,
            )

    def _extract_plugin_command_container(
        self,
        value: Any,
        path_parts: list[str | int],
        items: list[TranslationItem],
        keyword_active: bool,
    ) -> None:
        """
        递归扫描 357 指令内部的容器参数，并抽取命中关键词的短文本。

        设计意图：
        1. `dict` 节点通过键名判断是否属于文本字段。
        2. `list` 节点自身没有键名，只有在上层键已经命中文本关键词时，才把其中的字符串叶子视为可翻译文本。
        3. 顶层普通字符串参数不会进入这个函数，从而避免误提取插件名、命令名、编辑器显示名。

        Args:
            value: 当前递归节点的值。
            path_parts: 从命令路径累积到当前节点的完整路径片段。
            items: 当前文件正在收集的翻译项列表。
            keyword_active: 上层是否已经命中过文本关键词。
        """
        if isinstance(value, dict):
            for key, child in value.items():
                current_path_parts: list[str | int] = [*path_parts, key]
                key_matched: bool = self._should_extract_plugin_command_key(key)

                if isinstance(child, str):
                    if key_matched:
                        self._append_plugin_command_text_item(
                            text=child,
                            path_parts=current_path_parts,
                            items=items,
                        )
                    continue

                if isinstance(child, dict | list):
                    self._extract_plugin_command_container(
                        value=child,
                        path_parts=current_path_parts,
                        items=items,
                        keyword_active=keyword_active or key_matched,
                    )
            return

        if isinstance(value, list):
            for index, child in enumerate(value):
                current_path_parts: list[str | int] = [*path_parts, index]

                if isinstance(child, str):
                    if keyword_active:
                        self._append_plugin_command_text_item(
                            text=child,
                            path_parts=current_path_parts,
                            items=items,
                        )
                    continue

                if isinstance(child, dict | list):
                    self._extract_plugin_command_container(
                        value=child,
                        path_parts=current_path_parts,
                        items=items,
                        keyword_active=keyword_active,
                    )

    def _should_extract_plugin_command_key(self, key: str) -> bool:
        """
        判断 357 参数字典中的键名是否命中文本关键词。

        Args:
            key: 当前字典键名。

        Returns:
            只要键名包含 `text`、`message`、`name`、`desc` 之一就返回 True。
        """
        key_lower: str = key.lower()
        if key_lower in PLUGIN_COMMAND_EXCLUDED_KEYS:
            return False
        return any(keyword in key_lower for keyword in PLUGIN_COMMAND_TEXT_KEYWORDS)

    def _append_plugin_command_text_item(
        self,
        text: str,
        path_parts: list[str | int],
        items: list[TranslationItem],
    ) -> None:
        """
        将命中的 357 插件参数文本封装为短文本翻译项。

        Args:
            text: 命中的原始字符串。
            path_parts: 文本叶子的完整定位路径。
            items: 当前文件正在收集的翻译项列表。
        """
        if not text:
            return

        items.append(
            TranslationItem(
                location_path="/".join(map(str, path_parts)),
                item_type="short_text",
                original_lines=[text],
            )
        )


    def _extract_text_value(self, command: EventCommand) -> str | None:
        """
        从事件指令中提取文本值。

        Args:
            command: 当前事件指令。

        Returns:
            清理后的文本；如果无法提取则返回 None。
        """
        if not command.parameters:
            return None

        text_value = command.parameters[0]
        if not isinstance(text_value, str):
            return None

        return text_value.replace("「", "").replace("」", "").strip()


__all__: list[str] = ["DataTextExtraction"]
