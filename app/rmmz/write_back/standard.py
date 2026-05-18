"""标准 data JSON 文本写入。"""

from app.rmmz.schema import GameData, SYSTEM_FILE_NAME, TranslationItem
from app.rmmz.text_rules import JsonObject, TextRules, ensure_json_array, ensure_json_object

from .preparation import prepare_single_text_write_value


def write_system_item(game_data: GameData, item: TranslationItem, text_rules: TextRules | None) -> None:
    """将 `System.json` 文本写入数据副本。"""
    parts = item.location_path.split("/")
    system_data = ensure_json_object(game_data.writable_data[SYSTEM_FILE_NAME], SYSTEM_FILE_NAME)
    translated_text = prepare_single_text_write_value(item=item, text_rules=text_rules)

    if len(parts) == 2:
        system_data[parts[1]] = translated_text
        return

    if len(parts) == 3:
        key = parts[1]
        if key in {"variables", "switches"}:
            return
        target_list = ensure_json_array(system_data[key], item.location_path)
        target_list[int(parts[2])] = translated_text
        return

    if len(parts) == 4 and parts[1] == "terms" and parts[2] == "messages":
        terms = ensure_json_object(system_data["terms"], f"{SYSTEM_FILE_NAME}.terms")
        messages = ensure_json_object(terms["messages"], f"{SYSTEM_FILE_NAME}.terms.messages")
        messages[parts[3]] = translated_text
        return

    if len(parts) == 4 and parts[1] == "terms":
        terms = ensure_json_object(system_data["terms"], f"{SYSTEM_FILE_NAME}.terms")
        target_list = ensure_json_array(terms[parts[2]], item.location_path)
        target_list[int(parts[3])] = translated_text
        return

    raise ValueError(f"无法识别的 System 路径: {item.location_path}")


def write_base_item(game_data: GameData, item: TranslationItem, text_rules: TextRules | None) -> None:
    """将基础数据库条目文本写入数据副本。"""
    parts = item.location_path.split("/")
    file_name = parts[0]
    item_id = int(parts[1])
    key = parts[2]
    translated_text = prepare_single_text_write_value(item=item, text_rules=text_rules)
    data = ensure_json_array(game_data.writable_data[file_name], file_name)

    if item_id < len(data):
        target = data[item_id]
        if isinstance(target, dict) and target.get("id") == item_id:
            write_base_item_value(
                target=target,
                key=key,
                parts=parts,
                translated_text=translated_text,
                location_path=item.location_path,
            )
            return

    for target_value in data:
        if not isinstance(target_value, dict):
            continue
        if target_value.get("id") != item_id:
            continue
        write_base_item_value(
            target=target_value,
            key=key,
            parts=parts,
            translated_text=translated_text,
            location_path=item.location_path,
        )
        return

    raise ValueError(f"基础数据库条目不存在: {item.location_path}")


def write_base_item_value(
    *,
    target: JsonObject,
    key: str,
    parts: list[str],
    translated_text: str,
    location_path: str,
) -> None:
    """写入基础数据库条目的普通字段。"""
    if len(parts) == 3:
        target[key] = translated_text
        return

    raise ValueError(f"无法识别的基础数据库路径: {location_path}")
