"""翻译缓存与提示词组装测试。"""

from app.rmmz.schema import TranslationData, TranslationItem
from app.rmmz.text_rules import get_default_text_rules
from app.translation import TranslationCache, iter_translation_context_batches


def test_translation_cache_deduplicates_and_expands_items() -> None:
    """同轮重复正文只送模一次，成功后可展开重复项用于断点续传写库。"""
    cache = TranslationCache()
    first = TranslationItem(location_path="A/1", item_type="short_text", original_lines=["こんにちは"])
    duplicate = TranslationItem(location_path="B/1", item_type="short_text", original_lines=["こんにちは"])

    assert cache.remember_or_defer(first)
    assert not cache.remember_or_defer(duplicate)
    assert cache.pop_duplicate_items(first) == [duplicate]


def test_translation_context_prompt_contains_map_and_body_without_terms() -> None:
    """未传入术语表索引时，提示词包含地图名与正文上下文。"""
    data = TranslationData(
        display_name="始まりの町",
        translation_items=[
            TranslationItem(
                location_path="Map001.json/1/0/0",
                item_type="long_text",
                role="村人",
                original_lines=["こんにちは"],
            )
        ],
    )

    batches = list(
        iter_translation_context_batches(
            translation_data=data,
            token_size=100,
            factor=1.0,
            max_command_items=3,
            system_prompt="系统提示",
            text_rules=get_default_text_rules(),
        )
    )
    joined_prompt = "\n".join(message.text for message in batches[0].messages)

    assert "术语" not in joined_prompt
    assert "源语言" not in joined_prompt
    assert "[建议换行数]" not in joined_prompt
    assert "こんにちは" in joined_prompt


def test_translation_context_keeps_array_output_line_count_hint() -> None:
    """选项数组仍然向模型提供严格输出行数。"""
    data = TranslationData(
        display_name=None,
        translation_items=[
            TranslationItem(
                location_path="Map001.json/1/0/2",
                item_type="array",
                original_lines=["はい", "いいえ"],
            )
        ],
    )

    batches = list(
        iter_translation_context_batches(
            translation_data=data,
            token_size=100,
            factor=1.0,
            max_command_items=3,
            system_prompt="系统提示",
            text_rules=get_default_text_rules(),
        )
    )
    joined_prompt = "\n".join(message.text for message in batches[0].messages)

    assert "[输出行数]2" in joined_prompt
