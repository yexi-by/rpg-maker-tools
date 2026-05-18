"""Note 标签文本写入。"""

from app.note_tag_text import replace_note_tag_value
from app.rmmz.schema import GameData, TranslationItem
from app.rmmz.text_rules import JsonObject, JsonValue, TextRules, ensure_json_object

from .locators import find_json_object_by_id, item_context
from .preparation import prepare_single_text_write_value


def is_note_tag_location_path(location_path: str) -> bool:
    """判断路径是否指向 Note 标签值。"""
    parts = location_path.split("/")
    return len(parts) >= 3 and parts[-2] == "note"


def write_note_tag_item(game_data: GameData, item: TranslationItem, text_rules: TextRules | None) -> None:
    """按通用 data JSON 路径写入 Note 标签译文。"""
    parts = item.location_path.split("/")
    file_name = parts[0]
    tag_name = parts[-1]
    owner_parts = parts[1:-2]
    translated_text = prepare_single_text_write_value(item=item, text_rules=text_rules)
    target = locate_note_owner(
        value=game_data.writable_data[file_name],
        owner_parts=owner_parts,
        location_path=item.location_path,
    )
    note_value = target.get("note")
    if not isinstance(note_value, str):
        raise ValueError(f"Note 字段不是字符串: {item.location_path}")
    target["note"] = replace_note_tag_value(
        note_text=note_value,
        tag_name=tag_name,
        translated_text=translated_text,
    )


def locate_note_owner(
    *,
    value: JsonValue,
    owner_parts: list[str],
    location_path: str,
) -> JsonObject:
    """根据 Note 标签路径定位持有 note 字段的 JSON 对象。"""
    current_value = value
    for part in owner_parts:
        if isinstance(current_value, dict):
            if part not in current_value:
                raise ValueError(f"Note 路径对象键不存在: {location_path}")
            current_value = current_value[part]
            continue
        if isinstance(current_value, list):
            index = int(part)
            if index < len(current_value):
                indexed_value = current_value[index]
                if indexed_value is not None:
                    current_value = indexed_value
                    continue
            matched_value = find_json_object_by_id(current_value, index)
            if matched_value is None:
                raise ValueError(f"Note 路径数组索引不存在: {location_path}")
            current_value = matched_value
            continue
        raise ValueError(f"Note 路径无法继续定位: {location_path}")

    return ensure_json_object(current_value, item_context(location_path, "note_owner"))
