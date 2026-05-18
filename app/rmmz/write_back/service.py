"""正文写入游戏文件入口。

本模块只负责分发不同 RPG Maker 数据域的写入流程；事件指令、标准 data 字段、
Note 标签和写入前文本整理分别放在同包子模块中维护。
"""

from app.rmmz.placeholder_guard import ensure_no_internal_placeholder_tokens
from app.rmmz.schema import (
    COMMON_EVENTS_FILE_NAME,
    GameData,
    MAP_PATTERN,
    PLUGINS_FILE_NAME,
    SYSTEM_FILE_NAME,
    TROOPS_FILE_NAME,
    TranslationItem,
)
from app.rmmz.text_rules import TextRules

from .commands import command_item_sort_key, write_command_item
from .note_tags import is_note_tag_location_path, write_note_tag_item
from .standard import write_base_item, write_system_item


def write_data_text(
    game_data: GameData,
    items: list[TranslationItem],
    text_rules: TextRules | None = None,
    speaker_name_translations: dict[str, str] | None = None,
) -> None:
    """将最终翻译文本写入 `data/` 目录游戏数据的内存副本。"""
    command_items: list[TranslationItem] = []
    for item in items:
        ensure_no_internal_placeholder_tokens(
            lines=item.translation_lines,
            context=item.location_path,
            text_rules=text_rules,
        )
        file_name = item.location_path.split("/")[0]
        if file_name == PLUGINS_FILE_NAME:
            continue
        if is_note_tag_location_path(item.location_path):
            write_note_tag_item(game_data=game_data, item=item, text_rules=text_rules)
            continue
        if file_name == SYSTEM_FILE_NAME:
            write_system_item(game_data=game_data, item=item, text_rules=text_rules)
            continue
        if MAP_PATTERN.fullmatch(file_name) or file_name in {COMMON_EVENTS_FILE_NAME, TROOPS_FILE_NAME}:
            command_items.append(item)
            continue
        write_base_item(game_data=game_data, item=item, text_rules=text_rules)

    for item in sorted(command_items, key=command_item_sort_key, reverse=True):
        write_command_item(
            game_data=game_data,
            item=item,
            text_rules=text_rules,
            speaker_name_translations=speaker_name_translations,
        )


__all__: list[str] = ["write_data_text"]
