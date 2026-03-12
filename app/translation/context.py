"""
翻译上下文构造模块。

负责把正文翻译与错误重翻译的数据切成若干批次，
并为每个批次构造对应的 `list[ChatMessage]`。

边界说明：
1. 这里负责正文条目分批、命中术语筛选与用户提示词拼装。
2. 这里不负责调用 LLM，也不负责数据库读写。
3. 这里统一消费结构化 `Glossary`，不再依赖旧的扁平术语字典。
"""

from collections.abc import Iterator

from app.models.schemas import ErrorRetryItem, Glossary, Place, Role, TranslationData, TranslationItem
from app.services.llm.schemas import ChatMessage

USER_PROMPT_TEMPLATE: str = (
    "[[术语表-角色]]\n{role_glossary}\n"
    "[[术语表-地点]]\n{place_glossary}\n"
    "[[地图名]]\n{display_name}\n"
    "[[需要翻译的正文]]\n{unit_text}"
)
LONG_TEXT_CONTEXT_TEMPLATE: str = "[ID]{id}\n[类型]{item_type}\n[角色]{role}\n[建议换行数]{line_count}\n[台词]\n{lines}\n\n"
ARRAY_CONTEXT_TEMPLATE: str = (
    "[ID]{id}\n[类型]{item_type}\n[输出行数]{line_count}\n[选项列表]{lines}\n\n"
)
SHORT_TEXT_CONTEXT_TEMPLATE: str = "[ID]{id}\n[类型]{item_type}\n[游戏文本]{lines}\n\n"

ERROR_RETRY_USER_PROMPT_TEMPLATE: str = (
    "[[术语表-角色]]\n{role_glossary}\n"
    "[[术语表-地点]]\n{place_glossary}\n"
    "[[需要重翻译的错误正文]]\n{unit_text}"
)
ERROR_RETRY_CONTEXT_TEMPLATE: str = (
    "[ID]{id}\n"
    "[类型]{item_type}\n"
    "[角色]{role}\n"
    "[建议输出行数]{line_count}\n"
    "[原文]{original_lines}\n"
    "[上次失败译文]{previous_translation}\n"
    "[错误类型]{error_type}\n"
    "[错误详情]{error_detail}\n\n"
)


def iter_translation_context_batches(
    translation_data: TranslationData,
    token_size: int,
    factor: float,
    max_command_items: int,
    system_prompt: str,
    glossary: Glossary | None,
) -> Iterator[tuple[list[TranslationItem], list[ChatMessage]]]:
    """
    为单文件的翻译数据（`TranslationData`）生成上下文切批，并组装给大模型的消息。

    此方法主要解决如何将一个包含几千句台词的文件切分为适合大模型上下文窗口的片段。
    切分过程遵循以下设计规则，以确保翻译的连贯性和不会因硬截断破坏剧情语境：
    1. 正常积累：计算每句台词估算占用的 token 数，只要批次总 Token 小于 `token_size`，就持续追加。
    2. 动态延伸：如果当前已超 `token_size`，且最后一句话属于某个说话人（有 `role`），为了不把同一个人的连续长篇大论硬生生切断，程序会暂时忽略 token 限制，继续“吞并”同角色的后续台词。
    3. 截断止损：为防止“吞并”阶段无节制蔓延（例如某人连说了几百句话），额外吞并的条目数被限制在 `max_command_items` 以内。

    Args:
        translation_data: 从单个文件中提取到的包含所有 `TranslationItem` 的数据集。
        token_size: 期望的单批次最大 token 数量。
        factor: 经验值参数，用于将文本字符长度转换为估算的 Token 数量（通常汉字占比约 0.5-0.8）。
        max_command_items: 在动态延伸阶段，最多允许强行多吞并几条同角色的对话。
        system_prompt: 配置中定义的正文翻译专用系统提示词。
        glossary: 结构化的术语表。组装批次时，仅当正文命中了这些术语时，才会把对应的术语写入上下文，以节约 Token。

    Yields:
        一个元组，第一项是属于该批次的 `TranslationItem` 列表，第二项是组装完毕直接可发给 LLM 的 `list[ChatMessage]`。
        
    Raises:
        ValueError: 当参数配置无效（如非正数）时抛出。
    """
    if token_size <= 0:
        raise ValueError("token_size 必须大于 0")
    if factor <= 0:
        raise ValueError("factor 必须大于 0")
    if max_command_items <= 0:
        raise ValueError("max_command_items 必须大于 0")

    system_message: ChatMessage = ChatMessage(role="system", text=system_prompt)
    current_length: int = 0
    current_items: list[TranslationItem] = []
    current_glossary: Glossary = Glossary()
    main_bodies: list[str] = []
    display_name: str = translation_data.display_name or ""
    items: list[TranslationItem] = translation_data.translation_items

    index: int = 0
    while index < len(items):
        item: TranslationItem = items[index]
        current_length += _append_item_to_batch(
            item=item,
            current_items=current_items,
            current_glossary=current_glossary,
            glossary=glossary,
            main_bodies=main_bodies,
            display_name=display_name,
        )
        index += 1

        estimated_tokens: int = int(current_length / factor)
        if estimated_tokens < token_size:
            continue

        if item.role is None or item.role == "":
            yield _build_translation_batch(
                system_message=system_message,
                current_items=current_items,
                current_glossary=current_glossary,
                display_name=display_name,
                main_bodies=main_bodies,
            )
            current_length = 0
            current_items = []
            current_glossary = Glossary()
            main_bodies = []
            continue

        anchor_role: str = item.role
        appended_command_items: int = 0
        while index < len(items) and appended_command_items < max_command_items:
            next_item: TranslationItem = items[index]
            if next_item.role not in ("", anchor_role):
                break

            current_length += _append_item_to_batch(
                item=next_item,
                current_items=current_items,
                current_glossary=current_glossary,
                glossary=glossary,
                main_bodies=main_bodies,
                display_name=display_name,
            )
            index += 1
            appended_command_items += 1

        yield _build_translation_batch(
            system_message=system_message,
            current_items=current_items,
            current_glossary=current_glossary,
            display_name=display_name,
            main_bodies=main_bodies,
        )
        current_length = 0
        current_items = []
        current_glossary = Glossary()
        main_bodies = []

    if current_items:
        yield _build_translation_batch(
            system_message=system_message,
            current_items=current_items,
            current_glossary=current_glossary,
            display_name=display_name,
            main_bodies=main_bodies,
        )


def iter_error_retry_context_batches(
    error_retry_items: list[ErrorRetryItem],
    chunk_size: int,
    system_prompt: str,
    glossary: Glossary | None,
) -> Iterator[tuple[list[TranslationItem], list[ChatMessage]]]:
    """
    将之前翻译失败并在数据库里留下记录的错误条目切分成重试批次。

    由于错误条目本身包含了大量上下文（上次的失败译文、失败的详细错误信息等），
    而且通常这些错误条目是离散的（分布在整个游戏中），因此不再适用按 Token 切批及同角色顺延的策略。
    这里直接采用简单的固定数量（`chunk_size`）进行切分，以确保模型有足够的精力对每一条失败原因进行聚焦纠错。

    Args:
        error_retry_items: 从数据库中加载出来的错误重翻条目列表。
        chunk_size: 每个批次固定处理的错误条目数量。
        system_prompt: 配置中定义的专门用于指导模型“修Bug”的系统提示词。
        glossary: 结构化的术语表，同样按需进行动态筛选。

    Yields:
        与正文翻译同构的返回元组，但其消息结构中额外包含了错误详情的模板。
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")

    system_message: ChatMessage = ChatMessage(role="system", text=system_prompt)
    current_items: list[TranslationItem] = []
    current_glossary: Glossary = Glossary()
    main_bodies: list[str] = []

    for error_retry_item in error_retry_items:
        _append_error_retry_item_to_batch(
            error_retry_item=error_retry_item,
            current_items=current_items,
            current_glossary=current_glossary,
            glossary=glossary,
            main_bodies=main_bodies,
        )

        if len(current_items) < chunk_size:
            continue

        yield (
            current_items,
            [
                system_message,
                ChatMessage(
                    role="user",
                    text=_create_error_retry_user_prompt(
                        glossary=current_glossary,
                        main_bodies=main_bodies,
                    ),
                ),
            ],
        )
        current_items = []
        current_glossary = Glossary()
        main_bodies = []

    if current_items:
        yield (
            current_items,
            [
                system_message,
                ChatMessage(
                    role="user",
                    text=_create_error_retry_user_prompt(
                        glossary=current_glossary,
                        main_bodies=main_bodies,
                    ),
                ),
            ],
        )


def _build_translation_batch(
    system_message: ChatMessage,
    current_items: list[TranslationItem],
    current_glossary: Glossary,
    display_name: str,
    main_bodies: list[str],
) -> tuple[list[TranslationItem], list[ChatMessage]]:
    """
    根据给定的片段组装出单个批次的完整 ChatMessage 历史与控制对象。

    Args:
        system_message: 通用的系统提示词消息。
        current_items: 当前批次归属的所有翻译条目对象引用。
        current_glossary: 经过命中筛选后，当前批次专用的术语小集合。
        display_name: 当前文件所处的地图名（如有），为翻译提供宏观空间背景。
        main_bodies: 将原始对象格式化后的待翻译纯文本列表。

    Returns:
        (当前条目列表, [系统消息, 包含了术语和正文的用户消息])
    """
    return (
        current_items,
        [
            system_message,
            ChatMessage(
                role="user",
                text=_create_user_prompt(
                    display_name=display_name,
                    glossary=current_glossary,
                    main_bodies=main_bodies,
                ),
            ),
        ],
    )


def _create_user_prompt(
    display_name: str,
    glossary: Glossary,
    main_bodies: list[str],
) -> str:
    """
    利用模板将筛选后的上下文组合成发送给大模型的用户提示词文本。

    Args:
        display_name: 游戏内地图/文件的显示名称。
        glossary: 已经精准过滤过的，仅与当前片段相关的术语集。
        main_bodies: 包含了经过占位符替换和特定结构化编排的正文片段数组。

    Returns:
        最终发送给模型的长文本。
    """
    return USER_PROMPT_TEMPLATE.format(
        role_glossary=_format_roles(glossary.roles),
        place_glossary=_format_places(glossary.places),
        display_name=display_name,
        unit_text="".join(main_bodies),
    )


def _create_error_retry_user_prompt(
    glossary: Glossary,
    main_bodies: list[str],
) -> str:
    """
    构建错误重翻译专属用户提示词。

    Args:
        glossary: 当前批次命中的结构化术语子集。
        main_bodies: 当前批次正文块列表。

    Returns:
        错误重翻译专属用户提示词字符串。
    """
    return ERROR_RETRY_USER_PROMPT_TEMPLATE.format(
        role_glossary=_format_roles(glossary.roles),
        place_glossary=_format_places(glossary.places),
        unit_text="".join(main_bodies),
    )


def _format_roles(roles: list[Role]) -> str:
    """
    把角色术语列表格式化为提示词文本。

    Args:
        roles: 当前批次命中的角色术语对象列表。

    Returns:
        适合放入提示词的角色术语文本。
    """
    if not roles:
        return ""
    return "\n".join(
        f"原名: {role.name} | 译名: {role.translated_name} | 性别: {role.gender}"
        for role in roles
    )


def _format_places(places: list[Place]) -> str:
    """
    把地点术语列表格式化为提示词文本。

    Args:
        places: 当前批次命中的地点术语对象列表。

    Returns:
        适合放入提示词的地点术语文本。
    """
    if not places:
        return ""
    return "\n".join(
        f"原名: {place.name} | 译名: {place.translated_name}" for place in places
    )


def _format_translation_item(
    item: TranslationItem,
    masked_text: str,
) -> str:
    """
    将单个 `TranslationItem` 格式化成上下文正文块。

    Args:
        item: 当前翻译条目。
        masked_text: 已完成占位符替换后的文本。

    Returns:
        当前条目对应的正文块字符串。

    Raises:
        ValueError: 遇到未知 `item_type` 时抛出。
    """
    match item.item_type:
        case "long_text":
            role_text: str = item.role or ""
            return LONG_TEXT_CONTEXT_TEMPLATE.format(
                id=item.location_path,
                item_type=item.item_type,
                role=role_text,
                line_count=len(item.original_lines),
                lines=masked_text,
            )
        case "array":
            return ARRAY_CONTEXT_TEMPLATE.format(
                id=item.location_path,
                item_type=item.item_type,
                line_count=len(item.original_lines),
                lines=masked_text,
            )
        case "short_text":
            return SHORT_TEXT_CONTEXT_TEMPLATE.format(
                id=item.location_path,
                item_type=item.item_type,
                lines=masked_text,
            )
        case _:
            raise ValueError(f"未知的 item_type: {item.item_type}")


def _collect_hit_glossary(
    item: TranslationItem,
    masked_text: str,
    display_name: str,
    glossary: Glossary | None,
    current_glossary: Glossary,
) -> None:
    """
    收集当前条目命中的术语表子集。

    Args:
        item: 当前翻译条目。
        masked_text: 已完成占位符替换后的正文。
        display_name: 当前文件显示名。
        glossary: 全量结构化术语表；若为 `None` 则直接跳过。
        current_glossary: 当前批次命中的术语子集，会被原地更新。
    """
    if glossary is None:
        return

    texts_for_roles: list[str] = [masked_text]
    if item.role:
        texts_for_roles.append(item.role)

    texts_for_places: list[str] = [masked_text]
    if display_name:
        texts_for_places.append(display_name)

    _merge_roles(
        target=current_glossary.roles,
        roles=glossary.find_hit_roles(texts_for_roles),
    )
    _merge_places(
        target=current_glossary.places,
        places=glossary.find_hit_places(texts_for_places),
    )


def _merge_roles(target: list[Role], roles: list[Role]) -> None:
    """
    将命中的角色术语合并到当前批次子集中。

    Args:
        target: 当前批次角色术语列表，会被原地更新。
        roles: 新命中的角色术语列表。
    """
    existing_names: set[str] = {role.name for role in target}
    for role in roles:
        if role.name in existing_names:
            continue
        target.append(role)
        existing_names.add(role.name)


def _merge_places(target: list[Place], places: list[Place]) -> None:
    """
    将命中的地点术语合并到当前批次子集中。

    Args:
        target: 当前批次地点术语列表，会被原地更新。
        places: 新命中的地点术语列表。
    """
    existing_names: set[str] = {place.name for place in target}
    for place in places:
        if place.name in existing_names:
            continue
        target.append(place)
        existing_names.add(place.name)


def _append_item_to_batch(
    item: TranslationItem,
    current_items: list[TranslationItem],
    current_glossary: Glossary,
    glossary: Glossary | None,
    main_bodies: list[str],
    display_name: str,
) -> int:
    """
    将单个正文条目追加到当前批次中。

    Args:
        item: 当前翻译条目。
        current_items: 当前批次条目列表，会被原地追加。
        current_glossary: 当前批次命中的术语子集，会被原地更新。
        glossary: 全量结构化术语表。
        main_bodies: 当前批次正文块列表，会被原地追加。
        display_name: 当前文件显示名。

    Returns:
        当前条目占位符替换后文本的字符长度。
    """
    item.build_placeholders()
    masked_text: str = "\n".join(item.original_lines_with_placeholders)

    _collect_hit_glossary(
        item=item,
        masked_text=masked_text,
        display_name=display_name,
        glossary=glossary,
        current_glossary=current_glossary,
    )

    body_text: str = _format_translation_item(item=item, masked_text=masked_text)
    main_bodies.append(body_text)
    current_items.append(item)
    return len(masked_text)


def _append_error_retry_item_to_batch(
    error_retry_item: ErrorRetryItem,
    current_items: list[TranslationItem],
    current_glossary: Glossary,
    glossary: Glossary | None,
    main_bodies: list[str],
) -> int:
    """
    将单个错误重翻译条目追加到当前批次中。

    Args:
        error_retry_item: 当前错误重翻译条目。
        current_items: 当前批次条目列表。
        current_glossary: 当前批次命中的术语子集。
        glossary: 全量结构化术语表。
        main_bodies: 当前批次正文块列表。

    Returns:
        当前条目格式化后的字符长度。
    """
    item: TranslationItem = error_retry_item.translation_item
    item.build_placeholders()
    masked_original_text: str = "\n".join(item.original_lines_with_placeholders)
    previous_translation_text: str = "\n".join(
        _mask_previous_translation_lines(
            item=item,
            previous_translation_lines=error_retry_item.previous_translation_lines,
        )
    )
    error_detail_text: str = "\n".join(error_retry_item.error_detail)

    _collect_hit_glossary(
        item=item,
        masked_text=masked_original_text,
        display_name="",
        glossary=glossary,
        current_glossary=current_glossary,
    )

    body_text: str = ERROR_RETRY_CONTEXT_TEMPLATE.format(
        id=item.location_path,
        item_type=item.item_type,
        role=item.role or "",
        line_count=len(item.original_lines),
        original_lines=masked_original_text,
        previous_translation=previous_translation_text,
        error_type=error_retry_item.error_type,
        error_detail=error_detail_text,
    )
    main_bodies.append(body_text)
    current_items.append(item)
    return len(body_text)


def _mask_previous_translation_lines(
    item: TranslationItem,
    previous_translation_lines: list[str],
) -> list[str]:
    """
    把数据库中存储的上次翻译失败的历史译文，再次进行控制符遮蔽（替换为占位符）。

    为什么需要这一步：
    在提供给 LLM 错误重试上下文时，如果历史译文中包含原生的 RPG Maker 控制符（如 `\\C[2]`），
    不仅会增加理解成本，还会与它看到的要求处理的 `[C_2]` 占位符原文形式相冲突。
    所以我们使用当前对象的反向映射表，把这些乱七八糟的控制符强行转回占位符形式。

    Args:
        item: 当前拥有占位符映射表的翻译条目。
        previous_translation_lines: 包含控制符的旧译文列表。

    Returns:
        控制符已经被替换为简洁占位符的旧译文列表。
    """
    if not previous_translation_lines or not item.placeholder_map:
        return list(previous_translation_lines)

    reverse_map: dict[str, str] = {
        original_code: placeholder
        for placeholder, original_code in item.placeholder_map.items()
    }
    sorted_codes: list[str] = sorted(reverse_map.keys(), key=len, reverse=True)
    masked_lines: list[str] = []

    for line in previous_translation_lines:
        masked_line: str = line
        for original_code in sorted_codes:
            masked_line = masked_line.replace(
                original_code,
                reverse_map[original_code],
            )
        masked_lines.append(masked_line)

    return masked_lines


__all__: list[str] = [
    "iter_error_retry_context_batches",
    "iter_translation_context_batches",
]
