"""术语表工程导出、注入与写回测试。"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.application.handler import validate_terminology_registry_shape
from app.application.file_writer import reset_writable_copies
from app.rmmz import load_game_data
from app.rmmz.schema import TranslationData, TranslationItem
from app.rmmz.text_rules import ensure_json_array, ensure_json_object, get_default_text_rules
from app.terminology import (
    SpeakerDialogueContext,
    TerminologyPromptIndex,
    TerminologyRegistry,
    apply_terminology_translations,
    export_terminology_artifacts,
    load_terminology_registry,
)
from app.terminology.extraction import build_speaker_sample_file_name
from app.translation import iter_translation_context_batches


def json_dump_text(registry: TerminologyRegistry) -> str:
    """把术语表转成可搜索的测试文本。"""
    return json.dumps(registry.model_dump(mode="json"), ensure_ascii=False)


@pytest.mark.asyncio
async def test_export_terminology_writes_terms_and_contexts(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """导出命令生成完整术语表和只读上下文。"""
    game_data = await load_game_data(minimal_game_dir)
    summary = await export_terminology_artifacts(
        game_data=game_data,
        output_dir=tmp_path / "terminology",
    )

    registry = TerminologyRegistry.model_validate_json(
        summary.terms_path.read_text(encoding="utf-8")
    )
    assert set(registry.model_dump(mode="json")) == {
        "speaker_names",
        "map_display_names",
        "actor_names",
        "actor_nicknames",
        "class_names",
        "skill_names",
        "item_names",
        "weapon_names",
        "armor_names",
        "enemy_names",
        "state_names",
        "system_elements",
        "system_skill_types",
        "system_weapon_types",
        "system_armor_types",
        "system_equip_types",
    }

    expected_speaker_names = {
        "アリス": "",
        "敵": "",
        "村人": "",
        "案内人": "",
        "説明役": "",
    }
    assert registry.speaker_names.items() >= expected_speaker_names.items()
    assert registry.map_display_names == {"始まりの町": "", "第二テスト地点": ""}
    assert registry.actor_names == {"勇者": ""}
    assert registry.actor_nicknames == {"ニック": ""}
    assert registry.skill_names == {"火の術": ""}
    assert registry.item_names == {"回復薬": ""}
    assert registry.system_elements["炎"] == ""
    assert registry.system_skill_types["魔法"] == ""
    assert registry.system_weapon_types["剣"] == ""
    assert registry.system_armor_types["盾"] == ""
    assert registry.system_equip_types["武器"] == ""
    assert "案内イベント" not in json_dump_text(registry)
    assert "これは無視される" not in json_dump_text(registry)
    assert summary.sample_file_count == len(registry.speaker_names)

    context_payloads = [
        SpeakerDialogueContext.model_validate_json(path.read_text(encoding="utf-8"))
        for path in summary.speaker_context_dir.glob("*.json")
    ]
    contexts_by_name = {context.name: context.dialogue_lines for context in context_payloads}
    assert contexts_by_name["アリス"] == ["こんにちは"]
    assert contexts_by_name["村人"] == ["マップこんにちは"]
    assert contexts_by_name["説明役"] == ["別マップの本文です。"]
    assert (summary.speaker_context_dir / "アリス.json").exists()
    assert summary.database_context_path.exists()


def test_speaker_sample_file_name_uses_readable_source_name() -> None:
    """对白样本文件名直接使用清洗后的原文名字。"""
    assert build_speaker_sample_file_name("パティ") == "パティ.json"
    assert build_speaker_sample_file_name("A/B") == "A／B.json"
    assert build_speaker_sample_file_name("???") == "？？？.json"


def test_terminology_import_shape_validation_rejects_changed_keys() -> None:
    """术语表导入前会拒绝缺失 key 和新增 key。"""
    expected_registry = TerminologyRegistry(
        speaker_names={"案内人": ""},
        skill_names={"火の術": ""},
    )
    missing_registry = TerminologyRegistry(speaker_names={"案内人": ""})
    extra_registry = TerminologyRegistry(
        speaker_names={"案内人": ""},
        skill_names={"火の術": "", "氷の術": ""},
    )

    with pytest.raises(ValueError, match="skill_names 缺少 1 个术语"):
        validate_terminology_registry_shape(
            imported_registry=missing_registry,
            expected_registry=expected_registry,
        )
    with pytest.raises(ValueError, match="skill_names 多出 1 个术语"):
        validate_terminology_registry_shape(
            imported_registry=extra_registry,
            expected_registry=expected_registry,
        )


def test_terminology_registry_rejects_unknown_category_and_empty_source() -> None:
    """术语表文件结构错误会在模型边界被拒绝。"""
    with pytest.raises(ValidationError):
        _ = TerminologyRegistry.model_validate({"unknown_terms": {}})

    with pytest.raises(ValidationError, match="不能包含空原文"):
        _ = TerminologyRegistry(speaker_names={"": "空"})


@pytest.mark.asyncio
async def test_load_terminology_registry_requires_all_file_categories(tmp_path: Path) -> None:
    """外部术语表文件必须显式保留全部固定顶层类别。"""
    terms_path = tmp_path / "terms.json"
    _ = terms_path.write_text(
        json.dumps({"speaker_names": {"案内人": ""}}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="缺少类别"):
        _ = await load_terminology_registry(terms_path=terms_path)


@pytest.mark.asyncio
async def test_terminology_skips_actor_name_control_variables(
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

    summary = await export_terminology_artifacts(
        game_data=game_data,
        output_dir=tmp_path / "terminology",
    )
    registry = TerminologyRegistry.model_validate_json(
        summary.terms_path.read_text(encoding="utf-8")
    )

    assert "\\n[1]：" not in registry.speaker_names

    prompt_index = TerminologyPromptIndex.from_registry(
        TerminologyRegistry(speaker_names={"\\N[1]": "玩家"})
    )
    assert prompt_index.entries == []

    reset_writable_copies(game_data)
    written_count = apply_terminology_translations(
        game_data,
        TerminologyRegistry(speaker_names={"\\n[1]：": "玩家："}),
    )

    assert written_count == 0
    current_events = ensure_json_array(game_data.writable_data["CommonEvents.json"], "CommonEvents")
    current_event = ensure_json_object(current_events[1], "CommonEvents[1]")
    current_commands = ensure_json_array(current_event["list"], "CommonEvents[1].list")
    current_name_command = ensure_json_object(current_commands[0], "CommonEvents[1].list[0]")
    current_parameters = ensure_json_array(current_name_command["parameters"], "CommonEvents[1].list[0].parameters")
    assert current_parameters[4] == "\\n[1]："


def test_translation_prompt_injects_filled_terminology() -> None:
    """正文提示词会注入已填写的术语表。"""
    registry = TerminologyRegistry(
        speaker_names={"村人": "村民", "*": "*", ":": "冒号", "同名": "同名"},
        map_display_names={"始まりの町": "起始之镇"},
        skill_names={"火の術": "火术"},
    )
    data = TranslationData(
        display_name="始まりの町",
        translation_items=[
            TranslationItem(
                location_path="Map001.json/1/0/0",
                item_type="long_text",
                role="村人",
                original_lines=["こんにちは", "火の術", "同名", ":"],
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
            terminology_prompt_index=TerminologyPromptIndex.from_registry(registry),
        )
    )
    user_prompt = batches[0].messages[1].text

    assert "[[术语表]]" in user_prompt
    assert "# 术语表" not in user_prompt
    assert "[[需要翻译的正文]]" not in user_prompt
    assert "terms.json" not in user_prompt
    assert "translated_text" not in user_prompt
    assert "位置:" not in user_prompt
    assert "村人 => 村民" in user_prompt
    assert "始まりの町 => 起始之镇" in user_prompt
    assert "火の術 => 火术" in user_prompt
    assert "* => *" not in user_prompt
    assert ": => 冒号" not in user_prompt
    assert "同名 => 同名" not in user_prompt
    assert "# 正文" in user_prompt


@pytest.mark.asyncio
async def test_translation_prompt_injects_same_database_entry_name(
    minimal_game_dir: Path,
) -> None:
    """翻译数据库条目正文时会注入同一条目的名称术语。"""
    game_data = await load_game_data(minimal_game_dir)
    registry = TerminologyRegistry(skill_names={"火の術": "火术"})
    data = TranslationData(
        display_name="",
        translation_items=[
            TranslationItem(
                location_path="Skills.json/1/description",
                item_type="short_text",
                role=None,
                original_lines=["炎で攻撃する。"],
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
            terminology_prompt_index=TerminologyPromptIndex.from_registry(
                registry,
                game_data=game_data,
            ),
        )
    )
    user_prompt = batches[0].messages[1].text

    assert "火の術 => 火术" in user_prompt


@pytest.mark.asyncio
async def test_apply_terminology_translations_updates_all_supported_fields(
    minimal_game_dir: Path,
) -> None:
    """已填写术语表可以直接写回名字框、地图名、数据库名称和系统类型。"""
    game_data = await load_game_data(minimal_game_dir)
    registry = TerminologyRegistry(
        speaker_names={"村人": "村民"},
        map_display_names={"始まりの町": "起始之镇"},
        actor_names={"勇者": "勇者甲"},
        actor_nicknames={"ニック": "绰号"},
        skill_names={"火の術": "火术"},
        item_names={"回復薬": "回复药"},
        system_elements={"炎": "火焰"},
    )

    reset_writable_copies(game_data)
    written_count = apply_terminology_translations(game_data, registry)

    assert written_count == 7
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

    actors = ensure_json_array(game_data.writable_data["Actors.json"], "Actors")
    actor = ensure_json_object(actors[1], "Actors[1]")
    assert actor["name"] == "勇者甲"
    assert actor["nickname"] == "绰号"
    skills = ensure_json_array(game_data.writable_data["Skills.json"], "Skills")
    skill = ensure_json_object(skills[1], "Skills[1]")
    assert skill["name"] == "火术"
    items = ensure_json_array(game_data.writable_data["Items.json"], "Items")
    item = ensure_json_object(items[1], "Items[1]")
    assert item["name"] == "回复药"
    system = ensure_json_object(game_data.writable_data["System.json"], "System")
    elements = ensure_json_array(system["elements"], "System.elements")
    assert elements[1] == "火焰"
