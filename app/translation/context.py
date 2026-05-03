"""
正文翻译上下文构造模块。

负责把 `TranslationData` 切成适合模型请求的批次，并组装系统提示词与用户正文。
数据库术语表索引由调用方传入。
"""

from collections.abc import Iterator

from app.rmmz.schema import TranslationData, TranslationItem
from app.llm.schemas import ChatMessage
from app.rmmz.text_rules import TextRules
from app.name_context.prompt import NamePromptIndex, format_name_prompt_section
from app.translation.batch import TranslationBatch

SCENE_PROMPT_TEMPLATE = "# 场景\n\n地图：{display_name}"
BODY_PROMPT_TEMPLATE = "# 正文\n\n{unit_text}"
LONG_TEXT_CONTEXT_TEMPLATE = (
    "## {sequence}\n"
    "\n"
    "id: {id}\n"
    "type: {item_type}\n"
    "role: {role}\n"
    "\n"
    "{lines}\n\n"
)
ARRAY_CONTEXT_TEMPLATE = (
    "## {sequence}\n"
    "\n"
    "id: {id}\n"
    "type: {item_type}\n"
    "role: {role}\n"
    "line_count: {line_count}\n"
    "\n"
    "{lines}\n\n"
)
SHORT_TEXT_CONTEXT_TEMPLATE = (
    "## {sequence}\n"
    "\n"
    "id: {id}\n"
    "type: {item_type}\n"
    "role: {role}\n"
    "\n"
    "{lines}\n\n"
)
NARRATION_ROLE = "旁白"


def iter_translation_context_batches(
    translation_data: TranslationData,
    token_size: int,
    factor: float,
    max_command_items: int,
    system_prompt: str,
    text_rules: TextRules,
    name_prompt_index: NamePromptIndex | None = None,
) -> Iterator[TranslationBatch]:
    """为单文件翻译数据生成上下文切批。"""
    if token_size <= 0:
        raise ValueError("token_size 必须大于 0")
    if factor <= 0:
        raise ValueError("factor 必须大于 0")
    if max_command_items <= 0:
        raise ValueError("max_command_items 必须大于 0")

    system_message = ChatMessage(role="system", text=system_prompt)
    current_length = 0
    current_items: list[TranslationItem] = []
    main_bodies: list[str] = []
    display_name = translation_data.display_name or ""
    items = translation_data.translation_items

    index = 0
    while index < len(items):
        item = items[index]
        current_length += _append_item_to_batch(
            item=item,
            current_items=current_items,
            main_bodies=main_bodies,
            text_rules=text_rules,
        )
        index += 1

        estimated_tokens = int(current_length / factor)
        if estimated_tokens < token_size:
            continue

        if item.role is None or item.role == NARRATION_ROLE:
            yield _build_translation_batch(
                system_message=system_message,
                current_items=current_items,
                display_name=display_name,
                main_bodies=main_bodies,
                name_prompt_index=name_prompt_index,
            )
            current_length = 0
            current_items = []
            main_bodies = []
            continue

        anchor_role = item.role
        appended_command_items = 0
        while index < len(items) and appended_command_items < max_command_items:
            next_item = items[index]
            if not (
                next_item.role is None
                or next_item.role == NARRATION_ROLE
                or next_item.role == anchor_role
            ):
                break

            current_length += _append_item_to_batch(
                item=next_item,
                current_items=current_items,
                main_bodies=main_bodies,
                text_rules=text_rules,
            )
            index += 1
            appended_command_items += 1

        yield _build_translation_batch(
            system_message=system_message,
            current_items=current_items,
            display_name=display_name,
            main_bodies=main_bodies,
            name_prompt_index=name_prompt_index,
        )
        current_length = 0
        current_items = []
        main_bodies = []

    if current_items:
        yield _build_translation_batch(
            system_message=system_message,
            current_items=current_items,
            display_name=display_name,
            main_bodies=main_bodies,
            name_prompt_index=name_prompt_index,
        )


def _build_translation_batch(
    *,
    system_message: ChatMessage,
    current_items: list[TranslationItem],
    display_name: str,
    main_bodies: list[str],
    name_prompt_index: NamePromptIndex | None,
) -> TranslationBatch:
    """组装单个翻译批次。"""
    user_prompt_sections = [
        SCENE_PROMPT_TEMPLATE.format(display_name=display_name),
    ]
    if name_prompt_index is not None:
        name_entries = name_prompt_index.select_for_batch(
            display_name=display_name,
            items=current_items,
        )
        name_section = format_name_prompt_section(name_entries)
        if name_section:
            user_prompt_sections.append(name_section)
    user_prompt_sections.append(
        BODY_PROMPT_TEMPLATE.format(unit_text="".join(main_bodies))
    )
    return TranslationBatch(
        items=current_items,
        messages=[
            system_message,
            ChatMessage(
                role="user",
                text="\n\n".join(user_prompt_sections),
            ),
        ],
    )


def _format_translation_item(item: TranslationItem, masked_text: str, sequence: int) -> str:
    """将单个 `TranslationItem` 格式化成上下文正文块。"""
    if item.item_type == "long_text":
        return LONG_TEXT_CONTEXT_TEMPLATE.format(
            sequence=sequence,
            id=item.location_path,
            item_type=item.item_type,
            role=item.role or "",
            lines=masked_text,
        )
    if item.item_type == "array":
        return ARRAY_CONTEXT_TEMPLATE.format(
            sequence=sequence,
            id=item.location_path,
            item_type=item.item_type,
            role=item.role or "",
            line_count=len(item.original_lines),
            lines=masked_text,
        )
    if item.item_type == "short_text":
        return SHORT_TEXT_CONTEXT_TEMPLATE.format(
            sequence=sequence,
            id=item.location_path,
            item_type=item.item_type,
            role=item.role or "",
            lines=masked_text,
        )
    raise ValueError(f"未知的 item_type: {item.item_type}")


def _append_item_to_batch(
    *,
    item: TranslationItem,
    current_items: list[TranslationItem],
    main_bodies: list[str],
    text_rules: TextRules,
) -> int:
    """将单个正文条目追加到当前批次。"""
    item.build_placeholders(text_rules)
    masked_text = "\n".join(item.original_lines_with_placeholders)
    formatted_item = _format_translation_item(
        item=item,
        masked_text=masked_text,
        sequence=len(current_items) + 1,
    )
    main_bodies.append(formatted_item)
    current_items.append(item)
    return len(formatted_item)


__all__: list[str] = ["iter_translation_context_batches"]
