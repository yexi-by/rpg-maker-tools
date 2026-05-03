"""外部标准名术语导出、注入与写回测试。"""

from pathlib import Path

import pytest

from app.application.file_writer import reset_writable_copies
from app.name_context import (
    NameContextRegistry,
    NamePromptIndex,
    SpeakerDialogueContext,
    apply_name_context_translations,
    export_name_context_artifacts,
)
from app.name_context.extraction import build_speaker_sample_file_name
from app.rmmz import load_game_data
from app.rmmz.schema import TranslationData, TranslationItem
from app.rmmz.text_rules import ensure_json_array, ensure_json_object, get_default_text_rules
from app.translation import iter_translation_context_batches


@pytest.mark.asyncio
async def test_export_name_context_writes_simple_registry_and_grouped_contexts(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """导出命令生成极简术语表和按名字聚合的对白样本。"""
    game_data = await load_game_data(minimal_game_dir)
    summary = await export_name_context_artifacts(
        game_data=game_data,
        output_dir=tmp_path / "name-context",
    )

    registry = NameContextRegistry.model_validate_json(
        summary.registry_path.read_text(encoding="utf-8")
    )

    expected_speaker_names = {
        "アリス": "",
        "敵": "",
        "村人": "",
        "案内人": "",
        "説明役": "",
    }
    assert registry.speaker_names.items() >= expected_speaker_names.items()
    assert registry.map_display_names == {"始まりの町": "", "第二テスト地点": ""}
    assert summary.sample_file_count == len(registry.speaker_names)

    context_payloads = [
        SpeakerDialogueContext.model_validate_json(path.read_text(encoding="utf-8"))
        for path in summary.sample_dir.glob("*.json")
    ]
    contexts_by_name = {context.name: context.dialogue_lines for context in context_payloads}
    assert contexts_by_name["アリス"] == ["こんにちは"]
    assert contexts_by_name["村人"] == ["マップこんにちは"]
    assert contexts_by_name["説明役"] == ["別マップの本文です。"]
    assert (summary.sample_dir / "アリス.json").exists()


def test_speaker_sample_file_name_uses_readable_source_name() -> None:
    """对白样本文件名直接使用清洗后的原文名字。"""
    assert build_speaker_sample_file_name("パティ") == "パティ.json"
    assert build_speaker_sample_file_name("A/B") == "A／B.json"
    assert build_speaker_sample_file_name("???") == "？？？.json"


@pytest.mark.asyncio
async def test_name_context_skips_actor_name_control_variables(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """名字框中的角色名变量不会进入术语表、提示词和写回。"""
    game_data = await load_game_data(minimal_game_dir)
    common_events = ensure_json_array(game_data.writable_data["CommonEvents.json"], "CommonEvents")
    event = ensure_json_object(common_events[1], "CommonEvents[1]")
    commands = ensure_json_array(event["list"], "CommonEvents[1].list")
    name_command = ensure_json_object(commands[0], "CommonEvents[1].list[0]")
    parameters = ensure_json_array(name_command["parameters"], "CommonEvents[1].list[0].parameters")
    parameters[4] = "\\n[1]："
    common_event = game_data.common_events[1]
    assert common_event is not None
    common_event.commands[0].parameters[4] = "\\n[1]："
    game_data.data["CommonEvents.json"] = game_data.writable_data["CommonEvents.json"]

    summary = await export_name_context_artifacts(
        game_data=game_data,
        output_dir=tmp_path / "name-context",
    )
    registry = NameContextRegistry.model_validate_json(
        summary.registry_path.read_text(encoding="utf-8")
    )

    assert "\\n[1]：" not in registry.speaker_names

    prompt_index = NamePromptIndex.from_registry(
        NameContextRegistry(speaker_names={"\\N[1]": "玩家"}, map_display_names={})
    )
    assert prompt_index.entries == []

    reset_writable_copies(game_data)
    written_count = apply_name_context_translations(
        game_data,
        NameContextRegistry(speaker_names={"\\n[1]：": "玩家："}, map_display_names={}),
    )

    assert written_count == 0
    current_events = ensure_json_array(game_data.writable_data["CommonEvents.json"], "CommonEvents")
    current_event = ensure_json_object(current_events[1], "CommonEvents[1]")
    current_commands = ensure_json_array(current_event["list"], "CommonEvents[1].list")
    current_name_command = ensure_json_object(current_commands[0], "CommonEvents[1].list[0]")
    current_parameters = ensure_json_array(current_name_command["parameters"], "CommonEvents[1].list[0].parameters")
    assert current_parameters[4] == "\\n[1]："


def test_translation_prompt_injects_filled_name_context() -> None:
    """正文提示词会注入已填写的术语表。"""
    registry = NameContextRegistry(
        speaker_names={"村人": "村民", "*": "*", ":": "冒号", "同名": "同名"},
        map_display_names={"始まりの町": "起始之镇"},
    )
    data = TranslationData(
        display_name="始まりの町",
        translation_items=[
            TranslationItem(
                location_path="Map001.json/1/0/0",
                item_type="long_text",
                role="村人",
                original_lines=["こんにちは", "同名", ":"],
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
            name_prompt_index=NamePromptIndex.from_registry(registry),
        )
    )
    user_prompt = batches[0].messages[1].text

    assert "# 术语表" in user_prompt
    assert "[[术语表]]" not in user_prompt
    assert "[[需要翻译的正文]]" not in user_prompt
    assert "name_registry.json" not in user_prompt
    assert "translated_text" not in user_prompt
    assert "位置:" not in user_prompt
    assert "村人 => 村民" in user_prompt
    assert "始まりの町 => 起始之镇" in user_prompt
    assert "* => *" not in user_prompt
    assert ": => 冒号" not in user_prompt
    assert "同名 => 同名" not in user_prompt
    assert "# 正文" in user_prompt


@pytest.mark.asyncio
async def test_apply_name_context_translations_updates_101_and_map_display_name(
    minimal_game_dir: Path,
) -> None:
    """已填写术语表可以写回名字框和地图显示名。"""
    game_data = await load_game_data(minimal_game_dir)
    registry = NameContextRegistry(
        speaker_names={"村人": "村民"},
        map_display_names={"始まりの町": "起始之镇"},
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
