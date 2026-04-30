"""外部标准名上下文导出、注入与写回测试。"""

from pathlib import Path

import pytest

from app.application.file_writer import reset_writable_copies
from app.llm.schemas import ChatMessage
from app.name_context import (
    NameContextRegistry,
    NameLocation,
    NamePromptIndex,
    NameRegistryEntry,
    SpeakerDialogueContext,
    apply_name_context_translations,
    export_name_context_files,
)
from app.rmmz import load_game_data
from app.rmmz.schema import TranslationData, TranslationItem
from app.rmmz.text_rules import ensure_json_array, ensure_json_object, get_default_text_rules
from app.translation import iter_translation_context_batches


@pytest.mark.asyncio
async def test_export_name_context_writes_registry_and_speaker_contexts(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """导出命令需要生成大 JSON 和每个 `101` 的小 JSON。"""
    game_data = await load_game_data(minimal_game_dir)
    summary = await export_name_context_files(
        game_title="テストゲーム",
        game_data=game_data,
        output_dir=tmp_path / "name-context",
    )

    registry = NameContextRegistry.model_validate_json(
        summary.registry_path.read_text(encoding="utf-8")
    )
    source_texts = {entry.source_text for entry in registry.entries}

    assert "始まりの町" in source_texts
    assert "アリス" in source_texts
    assert summary.context_file_count == 3

    alice_context_path = next(summary.context_dir.glob("*CommonEvents_json_1_0*.json"))
    alice_context = SpeakerDialogueContext.model_validate_json(
        alice_context_path.read_text(encoding="utf-8")
    )
    assert alice_context.dialogue_lines == ["こんにちは"]


def test_translation_prompt_injects_filled_name_context() -> None:
    """正文提示词会注入外部 Agent 已填写的大 JSON 标准名。"""
    registry = NameContextRegistry(
        game_title="テストゲーム",
        generated_at="2026-04-30T00:00:00+00:00",
        entries=[
            NameRegistryEntry(
                entry_id="speaker_1",
                kind="speaker_name",
                source_text="村人",
                translated_text="村民",
                locations=[
                    NameLocation(
                        location_path="Map001.json/1/0/0",
                        file_name="Map001.json",
                    )
                ],
            ),
            NameRegistryEntry(
                entry_id="map_1",
                kind="map_display_name",
                source_text="始まりの町",
                translated_text="起始之镇",
                locations=[
                    NameLocation(
                        location_path="Map001.json/displayName",
                        file_name="Map001.json",
                    )
                ],
            ),
        ],
    )
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
            file_name="Map001.json",
            name_prompt_index=NamePromptIndex.from_registry(registry),
        )
    )
    messages: list[ChatMessage] = batches[0][1]
    user_prompt = messages[1].text

    assert "[[术语表]]" in user_prompt
    assert "name_registry.json" not in user_prompt
    assert "translated_text" not in user_prompt
    assert "位置:" not in user_prompt
    assert "村人 => 村民" in user_prompt
    assert "始まりの町 => 起始之镇" in user_prompt
    assert "[[需要翻译的正文]]" in user_prompt


@pytest.mark.asyncio
async def test_apply_name_context_translations_updates_101_and_map_display_name(
    minimal_game_dir: Path,
) -> None:
    """已填写的大 JSON 可以直接写回名字框和地图显示名。"""
    game_data = await load_game_data(minimal_game_dir)
    registry = NameContextRegistry(
        game_title="テストゲーム",
        generated_at="2026-04-30T00:00:00+00:00",
        entries=[
            NameRegistryEntry(
                entry_id="speaker_1",
                kind="speaker_name",
                source_text="村人",
                translated_text="村民",
                locations=[
                    NameLocation(
                        location_path="Map001.json/1/0/0",
                        file_name="Map001.json",
                    )
                ],
            ),
            NameRegistryEntry(
                entry_id="map_1",
                kind="map_display_name",
                source_text="始まりの町",
                translated_text="起始之镇",
                locations=[
                    NameLocation(
                        location_path="Map001.json/displayName",
                        file_name="Map001.json",
                    )
                ],
            ),
        ],
    )

    reset_writable_copies(game_data)
    written_count = apply_name_context_translations(game_data, registry)

    assert written_count == 2
    map_object = ensure_json_object(game_data.writable_data["Map001.json"], "Map001")
    assert map_object["displayName"] == "起始之镇"
    events = ensure_json_array(map_object["events"], "Map001.events")
    event = ensure_json_object(events[1], "Map001.events[1]")
    pages = ensure_json_array(event["pages"], "Map001.events[1].pages")
    page = ensure_json_object(pages[0], "Map001.events[1].pages[0]")
    commands = ensure_json_array(page["list"], "Map001.events[1].pages[0].list")
    name_command = ensure_json_object(commands[0], "Map001.events[1].pages[0].list[0]")
    parameters = ensure_json_array(name_command["parameters"], "name.parameters")
    assert parameters[4] == "村民"
