"""Agent 工具包诊断、扫描和质量报告测试。"""

import json
from pathlib import Path
from typing import cast

import pytest

from app.agent_toolkit import AgentToolkitService
from app.llm import LLMHandler
from app.name_context.schemas import NameContextRegistry
from app.persistence import GameRegistry
from app.plugin_text import build_plugin_hash
from app.rmmz.json_types import JsonObject, coerce_json_value, ensure_json_array, ensure_json_object
from app.rmmz.loader import load_game_data
from app.rmmz.schema import (
    EventCommandParameterFilter,
    EventCommandTextRuleRecord,
    NoteTagTextRuleRecord,
    PlaceholderRuleRecord,
    PluginTextRuleRecord,
    TranslationErrorItem,
    TranslationItem,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_SETTING_PATH = ROOT / "setting.example.toml"


def load_json_object(path: Path) -> dict[str, object]:
    """读取测试产物 JSON 对象，并在边界处收窄动态解析结果。"""
    raw_value = cast(object, json.loads(path.read_text(encoding="utf-8")))
    json_object = ensure_json_object(coerce_json_value(raw_value), str(path))
    return {key: value for key, value in json_object.items()}


def _contains_japanese_test_char(text: str) -> bool:
    """判断测试样本文本是否含有日文假名。"""
    return any("\u3040" <= char <= "\u30ff" for char in text)


@pytest.mark.asyncio
async def test_doctor_uses_fake_llm_check_without_real_request(tmp_path: Path) -> None:
    """doctor 可以注入模型检查函数，测试环境不触发真实 API。"""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    called_models: list[str] = []

    async def fake_llm_check(_llm_handler: LLMHandler, model: str) -> None:
        """记录模型名称，不发起网络请求。"""
        called_models.append(model)

    service = AgentToolkitService(
        game_registry=GameRegistry(db_dir),
        llm_check=fake_llm_check,
        setting_path=EXAMPLE_SETTING_PATH,
    )

    report = await service.doctor(game_title=None, check_llm=True)

    assert report.status in {"ok", "warning"}
    assert called_models
    assert report.summary["llm_model"]


@pytest.mark.asyncio
async def test_doctor_creates_missing_db_directory(tmp_path: Path) -> None:
    """doctor 会自愈创建缺失的固定数据库目录。"""
    db_dir = tmp_path / "missing-db"
    service = AgentToolkitService(
        game_registry=GameRegistry(db_dir),
        setting_path=EXAMPLE_SETTING_PATH,
    )

    report = await service.doctor(game_title=None, check_llm=False)

    error_codes = {error.code for error in report.errors}
    assert "db_dir" not in error_codes
    assert db_dir.exists()


@pytest.mark.asyncio
async def test_scan_placeholder_candidates_marks_custom_rule_coverage(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """扫描命令能区分内置控制符、未覆盖自定义控制符和 CLI 覆盖规则。"""
    registry = GameRegistry(tmp_path / "db")
    _ = await registry.register_game(minimal_game_dir)
    service = AgentToolkitService(game_registry=registry, setting_path=EXAMPLE_SETTING_PATH)

    uncovered_report = await service.scan_placeholder_candidates(
        game_title="テストゲーム",
        custom_placeholder_rules_text="{}",
    )
    covered_report = await service.scan_placeholder_candidates(
        game_title="テストゲーム",
        custom_placeholder_rules_text='{"\\\\\\\\F\\\\[[^\\\\]]+\\\\]":"[CUSTOM_FACE_PORTRAIT_{index}]"}',
    )

    assert uncovered_report.summary["uncovered_count"] != 0
    assert covered_report.summary["uncovered_count"] == 0
    raw_json = covered_report.to_json_text()
    assert r"\F[GuideA]" in raw_json
    assert "テスト一行目です" not in raw_json


@pytest.mark.asyncio
async def test_build_placeholder_rules_groups_similar_candidates(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """规则草稿会把同类自定义控制符合并成少量通用正则。"""
    registry = GameRegistry(tmp_path / "db")
    _ = await registry.register_game(minimal_game_dir)
    service = AgentToolkitService(game_registry=registry, setting_path=EXAMPLE_SETTING_PATH)
    output_path = tmp_path / "placeholder-rules.json"

    report = await service.build_placeholder_rules(game_title="テストゲーム", output_path=output_path)

    assert report.status == "ok"
    rules = load_json_object(output_path)
    assert rules == {r"(?i)\\F\d*\[[^\]\r\n]+\]": "[CUSTOM_FACE_PORTRAIT_{index}]"}
    assert report.summary["draft_rule_count"] == 1


@pytest.mark.asyncio
async def test_prepare_agent_workspace_includes_placeholder_rule_draft(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """Agent 工作区会携带占位符和 Note 标签规则草稿，避免重复手写解析脚本。"""
    registry = GameRegistry(tmp_path / "db")
    _ = await registry.register_game(minimal_game_dir)
    service = AgentToolkitService(game_registry=registry, setting_path=EXAMPLE_SETTING_PATH)
    workspace = tmp_path / "workspace"

    report = await service.prepare_agent_workspace(
        game_title="テストゲーム",
        output_dir=workspace,
        command_codes=None,
    )

    assert report.status == "ok"
    rules_path = workspace / "placeholder-rules.json"
    note_candidates_path = workspace / "note-tag-candidates.json"
    note_rules_path = workspace / "note-tag-rules.json"
    assert rules_path.exists()
    assert note_candidates_path.exists()
    assert note_rules_path.exists()
    rules = load_json_object(rules_path)
    note_rules = load_json_object(note_rules_path)
    assert rules == {r"(?i)\\F\d*\[[^\]\r\n]+\]": "[CUSTOM_FACE_PORTRAIT_{index}]"}
    assert note_rules == {}
    assert report.summary["placeholder_rule_draft_count"] == 1
    assert "note-tag-rules.json" in json.dumps(report.details, ensure_ascii=False)


@pytest.mark.asyncio
async def test_prepare_agent_workspace_prefills_imported_database_rules(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """二次翻译工作区会回填当前数据库中已导入的规则和术语表。"""
    items_path = minimal_game_dir / "data" / "Items.json"
    raw_items = cast(object, json.loads(items_path.read_text(encoding="utf-8")))
    items = ensure_json_array(coerce_json_value(raw_items), "Items.json")
    first_item = ensure_json_object(items[1], "Items.json[1]")
    first_item["note"] = "<拡張説明:薬草の詳細説明>"
    _ = items_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    registry = GameRegistry(tmp_path / "db")
    _ = await registry.register_game(minimal_game_dir)
    service = AgentToolkitService(game_registry=registry, setting_path=EXAMPLE_SETTING_PATH)
    seed_workspace = tmp_path / "seed-workspace"
    workspace = tmp_path / "workspace"

    _ = await service.prepare_agent_workspace(
        game_title="テストゲーム",
        output_dir=seed_workspace,
        command_codes=None,
    )
    exported_registry = load_json_object(seed_workspace / "name-context" / "name_registry.json")
    speaker_names = ensure_json_object(coerce_json_value(exported_registry["speaker_names"]), "speaker_names")
    map_display_names = ensure_json_object(coerce_json_value(exported_registry["map_display_names"]), "map_display_names")
    filled_registry = NameContextRegistry(
        speaker_names={source_text: f"{source_text}译" for source_text in speaker_names},
        map_display_names={source_text: f"{source_text}译" for source_text in map_display_names},
    )
    game_data = await load_game_data(minimal_game_dir)
    async with await registry.open_game("テストゲーム") as session:
        await session.replace_name_context_registry(filled_registry)
        await session.replace_plugin_text_rules(
            [
                PluginTextRuleRecord(
                    plugin_index=0,
                    plugin_name="TestPlugin",
                    plugin_hash=build_plugin_hash(game_data.plugins_js[0]),
                    path_templates=["$['parameters']['Message']"],
                )
            ]
        )
        await session.replace_event_command_text_rules(
            [
                EventCommandTextRuleRecord(
                    command_code=357,
                    parameter_filters=[EventCommandParameterFilter(index=0, value="TestPlugin")],
                    path_templates=["$['parameters'][3]['message']"],
                )
            ]
        )
        await session.replace_note_tag_text_rules(
            [NoteTagTextRuleRecord(file_name="Items.json", tag_names=["拡張説明"])]
        )
        await session.replace_placeholder_rules(
            [
                PlaceholderRuleRecord(
                    pattern_text=r"(?i)\\F\d*\[[^\]\r\n]+\]",
                    placeholder_template="[CUSTOM_FACE_PORTRAIT_{index}]",
                )
            ]
        )

    report = await service.prepare_agent_workspace(
        game_title="テストゲーム",
        output_dir=workspace,
        command_codes=None,
    )
    validation_report = await service.validate_agent_workspace(game_title="テストゲーム", workspace=workspace)

    prepared_registry = load_json_object(workspace / "name-context" / "name_registry.json")
    plugin_rules = load_json_object(workspace / "plugin-rules.json")
    event_rules = load_json_object(workspace / "event-command-rules.json")
    note_rules = load_json_object(workspace / "note-tag-rules.json")
    placeholder_rules = load_json_object(workspace / "placeholder-rules.json")
    warning_codes = {warning.code for warning in validation_report.warnings}
    assert report.status == "ok"
    assert report.summary["plugin_rule_count"] == 1
    assert report.summary["event_command_rule_count"] == 1
    assert report.summary["note_tag_rule_count"] == 1
    assert report.summary["placeholder_rule_count"] == 1
    assert prepared_registry["speaker_names"] == filled_registry.speaker_names
    assert prepared_registry["map_display_names"] == filled_registry.map_display_names
    assert plugin_rules == {"TestPlugin": ["$['parameters']['Message']"]}
    assert event_rules == {
        "357": [
            {
                "match": {"0": "TestPlugin"},
                "paths": ["$['parameters'][3]['message']"],
            }
        ]
    }
    assert note_rules == {"Items.json": ["拡張説明"]}
    assert placeholder_rules == {r"(?i)\\F\d*\[[^\]\r\n]+\]": "[CUSTOM_FACE_PORTRAIT_{index}]"}
    assert validation_report.status == "ok"
    assert "plugin_rules_missing" not in warning_codes
    assert "event_command_rules_missing" not in warning_codes
    assert "name_context_empty_translation" not in warning_codes


@pytest.mark.asyncio
async def test_validate_agent_workspace_blocks_missing_note_tag_rules(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """Note 标签规则是第五类强制子代理产物，缺失时工作区校验阻断。"""
    registry = GameRegistry(tmp_path / "db")
    _ = await registry.register_game(minimal_game_dir)
    service = AgentToolkitService(game_registry=registry, setting_path=EXAMPLE_SETTING_PATH)
    workspace = tmp_path / "workspace"

    _ = await service.prepare_agent_workspace(
        game_title="テストゲーム",
        output_dir=workspace,
        command_codes=None,
    )
    (workspace / "note-tag-rules.json").unlink()
    report = await service.validate_agent_workspace(game_title="テストゲーム", workspace=workspace)

    assert report.status == "error"
    assert "note_tag_rules_missing" in {error.code for error in report.errors}


@pytest.mark.asyncio
async def test_note_tag_rule_validation_import_and_pending_export(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """Note 标签规则校验后会让目标标签值进入 pending，机器协议标签会被拒绝。"""
    items_path = minimal_game_dir / "data" / "Items.json"
    raw_items = cast(object, json.loads(items_path.read_text(encoding="utf-8")))
    items = ensure_json_array(coerce_json_value(raw_items), "Items.json")
    item = ensure_json_object(items[1], "Items.json[1]")
    item["note"] = "<拡張説明:一行目\n二行目>\n<upgrade:1,2,3>\n<ExtendDesc:別説明>"
    items.append({"id": 2, "name": "空タグ項目", "note": "<拡張説明:>", "description": ""})
    _ = items_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    registry = GameRegistry(tmp_path / "db")
    _ = await registry.register_game(minimal_game_dir)
    service = AgentToolkitService(game_registry=registry, setting_path=EXAMPLE_SETTING_PATH)
    candidates_path = tmp_path / "note-tag-candidates.json"
    pending_path = tmp_path / "pending-translations.json"
    rules_text = json.dumps(
        {"Items.json": ["拡張説明", "ExtendDesc"]},
        ensure_ascii=False,
    )
    machine_rules_text = json.dumps({"Items.json": ["upgrade"]}, ensure_ascii=False)

    candidate_report = await service.export_note_tag_candidates(
        game_title="テストゲーム",
        output_path=candidates_path,
    )
    validate_report = await service.validate_note_tag_rules(
        game_title="テストゲーム",
        rules_text=rules_text,
    )
    rejected_report = await service.validate_note_tag_rules(
        game_title="テストゲーム",
        rules_text=machine_rules_text,
    )
    import_report = await service.import_note_tag_rules(
        game_title="テストゲーム",
        rules_text=rules_text,
    )
    export_report = await service.export_pending_translations(
        game_title="テストゲーム",
        output_path=pending_path,
        limit=None,
    )

    payload = load_json_object(pending_path)
    assert candidate_report.status == "ok"
    assert candidates_path.exists()
    assert validate_report.status == "ok"
    assert validate_report.summary["hit_count"] == 2
    assert rejected_report.status == "error"
    assert "机器协议" in rejected_report.errors[0].message
    assert import_report.status == "ok"
    assert export_report.status == "ok"
    assert "Items.json/1/note/拡張説明" in payload
    assert "Items.json/1/note/ExtendDesc" in payload
    assert "Items.json/2/note/拡張説明" not in payload


@pytest.mark.asyncio
async def test_manual_pending_translation_export_and_import(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """Agent 可以导出少量待翻译条目，人工补齐后再由工具校验入库。"""
    registry = GameRegistry(tmp_path / "db")
    _ = await registry.register_game(minimal_game_dir)
    service = AgentToolkitService(game_registry=registry, setting_path=EXAMPLE_SETTING_PATH)
    pending_path = tmp_path / "pending-translations.json"

    export_report = await service.export_pending_translations(
        game_title="テストゲーム",
        output_path=pending_path,
        limit=10,
    )

    assert export_report.status == "ok"
    payload = load_json_object(pending_path)
    target_path = ""
    for location_path, raw_entry in payload.items():
        entry = ensure_json_object(coerce_json_value(raw_entry), location_path)
        original_lines = ensure_json_array(entry["original_lines"], f"{location_path}.original_lines")
        if original_lines == ["こんにちは"]:
            target_path = location_path
            entry["translation_lines"] = ["你好"]
            payload[location_path] = entry
            break
    assert target_path
    _ = pending_path.write_text(json.dumps({target_path: payload[target_path]}, ensure_ascii=False, indent=2), encoding="utf-8")
    async with await registry.open_game("テストゲーム") as session:
        run_record = await session.start_translation_run(
            total_extracted=10,
            pending_count=10,
            deduplicated_count=10,
            batch_count=1,
        )
        await session.write_translation_quality_errors(
            run_record.run_id,
            [
                TranslationErrorItem(
                    location_path=target_path,
                    item_type="short_text",
                    role=None,
                    original_lines=["こんにちは"],
                    translation_lines=[],
                    error_type="AI漏翻",
                    error_detail=["人工补译前的历史错误"],
                    model_response='{"bad": true}',
                )
            ],
        )

    import_report = await service.import_manual_translations(
        game_title="テストゲーム",
        input_path=pending_path,
    )
    status_report = await service.translation_status(game_title="テストゲーム")
    quality_report = await service.quality_report(game_title="テストゲーム")

    assert import_report.status == "ok"
    assert status_report.summary["pending_count"] == quality_report.summary["pending_count"]
    assert status_report.summary["run_pending_count"] == 10
    assert status_report.summary["quality_error_count"] == 0
    assert status_report.summary["run_quality_error_count"] == 0
    assert quality_report.summary["quality_error_count"] == 0
    assert quality_report.summary["run_quality_error_count"] == 0
    async with await registry.open_game("テストゲーム") as session:
        translated_items = await session.read_translated_items()
        quality_errors = await session.read_translation_quality_errors(run_record.run_id)
    translated_by_path = {item.location_path: item for item in translated_items}
    assert translated_by_path[target_path].translation_lines == ["你好"]
    assert quality_errors == []


@pytest.mark.asyncio
async def test_manual_translation_rejects_changed_unprotected_control_sequence(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """人工补译不得改写未被占位符规则覆盖的疑似控制符。"""
    common_events_path = minimal_game_dir / "data" / "CommonEvents.json"
    raw_common_events = cast(object, json.loads(common_events_path.read_text(encoding="utf-8")))
    common_events = ensure_json_array(coerce_json_value(raw_common_events), "CommonEvents.json")
    common_event = ensure_json_object(common_events[1], "CommonEvents[1]")
    commands = ensure_json_array(common_event["list"], "CommonEvents[1].list")
    commands.insert(-1, {"code": 101, "parameters": [0, 0, 0, 2, "アリス"]})
    commands.insert(-1, {"code": 401, "parameters": [r"\F3[66」「ふーん……？」"]})
    _ = common_events_path.write_text(
        json.dumps(common_events, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    registry = GameRegistry(tmp_path / "db")
    _ = await registry.register_game(minimal_game_dir)
    service = AgentToolkitService(game_registry=registry, setting_path=EXAMPLE_SETTING_PATH)
    pending_path = tmp_path / "pending-translations.json"

    export_report = await service.export_pending_translations(
        game_title="テストゲーム",
        output_path=pending_path,
        limit=None,
    )
    payload = load_json_object(pending_path)
    target_path = ""
    target_entry: JsonObject = {}
    for location_path, raw_entry in payload.items():
        entry = ensure_json_object(coerce_json_value(raw_entry), location_path)
        original_lines = ensure_json_array(entry["original_lines"], f"{location_path}.original_lines")
        if original_lines == [r"\F3[66」「ふーん……？」"]:
            target_path = location_path
            entry["translation_lines"] = [r"\F3[60」「唔——嗯……？」"]
            target_entry = {key: value for key, value in entry.items()}
            break
    assert export_report.status == "ok"
    assert target_path
    _ = pending_path.write_text(
        json.dumps({target_path: target_entry}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    rejected_report = await service.import_manual_translations(
        game_title="テストゲーム",
        input_path=pending_path,
    )

    assert rejected_report.status == "error"
    assert rejected_report.errors
    assert "疑似控制符不一致" in rejected_report.errors[0].message
    assert r"\F3[66」" in rejected_report.errors[0].message
    assert r"\F3[60」" in rejected_report.errors[0].message


@pytest.mark.asyncio
async def test_manual_translation_uses_japanese_residual_exception_rules(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """确需保留的日文片段必须先导入显式例外规则才能通过人工补译。"""
    registry = GameRegistry(tmp_path / "db")
    _ = await registry.register_game(minimal_game_dir)
    service = AgentToolkitService(game_registry=registry, setting_path=EXAMPLE_SETTING_PATH)
    pending_path = tmp_path / "pending-translations.json"

    export_report = await service.export_pending_translations(
        game_title="テストゲーム",
        output_path=pending_path,
        limit=10,
    )
    payload = load_json_object(pending_path)
    target_path = ""
    target_entry: JsonObject = {}
    for location_path, raw_entry in payload.items():
        entry = ensure_json_object(coerce_json_value(raw_entry), location_path)
        original_lines = ensure_json_array(entry["original_lines"], f"{location_path}.original_lines")
        if original_lines == ["こんにちは"]:
            target_path = location_path
            entry["translation_lines"] = ["こんにちは"]
            target_entry = {key: value for key, value in entry.items()}
            break
    assert export_report.status == "ok"
    assert target_path
    _ = pending_path.write_text(
        json.dumps({target_path: target_entry}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    rejected_report = await service.import_manual_translations(
        game_title="テストゲーム",
        input_path=pending_path,
    )
    rules_text = json.dumps(
        {
            target_path: {
                "allowed_terms": ["こんにちは"],
                "reason": "proper_noun",
            }
        },
        ensure_ascii=False,
    )
    validate_report = await service.validate_japanese_residual_rules(
        game_title="テストゲーム",
        rules_text=rules_text,
    )
    import_rules_report = await service.import_japanese_residual_rules(
        game_title="テストゲーム",
        rules_text=rules_text,
    )
    accepted_report = await service.import_manual_translations(
        game_title="テストゲーム",
        input_path=pending_path,
    )
    quality_report = await service.quality_report(game_title="テストゲーム")

    assert rejected_report.status == "error"
    assert "日文残留" in rejected_report.errors[0].message
    assert validate_report.status == "ok"
    assert import_rules_report.status == "ok"
    assert accepted_report.status == "ok"
    assert quality_report.summary["japanese_residual_rule_count"] == 1
    assert quality_report.summary["japanese_residual_count"] == 0
    assert quality_report.details["japanese_residual_items"] == []
    async with await registry.open_game("テストゲーム") as session:
        translated_items = await session.read_translated_items()
        residual_rules = await session.read_japanese_residual_rules()
    translated_by_path = {item.location_path: item for item in translated_items}
    assert translated_by_path[target_path].translation_lines == ["こんにちは"]
    assert residual_rules[0].allowed_terms == ["こんにちは"]
    assert residual_rules[0].reason == "proper_noun"


@pytest.mark.asyncio
async def test_agent_reports_ignore_stale_plugin_rules(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """Agent 工具包与主翻译流程一样跳过过期插件规则，避免生成假 pending。"""
    registry = GameRegistry(tmp_path / "db")
    _ = await registry.register_game(minimal_game_dir)
    async with await registry.open_game("テストゲーム") as session:
        await session.replace_plugin_text_rules(
            [
                PluginTextRuleRecord(
                    plugin_index=0,
                    plugin_name="TestPlugin",
                    plugin_hash="stale-hash",
                    path_templates=["$['parameters']['Message']"],
                )
            ]
        )
    service = AgentToolkitService(game_registry=registry, setting_path=EXAMPLE_SETTING_PATH)
    pending_path = tmp_path / "pending-translations.json"

    quality_report = await service.quality_report(game_title="テストゲーム")
    export_report = await service.export_pending_translations(
        game_title="テストゲーム",
        output_path=pending_path,
        limit=None,
    )

    payload = load_json_object(pending_path)
    warning_codes = {warning.code for warning in quality_report.warnings}
    assert quality_report.summary["plugin_rule_count"] == 0
    assert quality_report.summary["stale_plugin_rule_count"] == 1
    assert "stale_plugin_rules" in warning_codes
    assert export_report.status in {"ok", "warning"}
    assert all(not location_path.startswith("plugins.js/") for location_path in payload)


@pytest.mark.asyncio
async def test_manual_long_text_import_splits_overwide_lines(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """人工补译 long_text 入库前会按当前行宽配置自动拆短。"""
    registry = GameRegistry(tmp_path / "db")
    _ = await registry.register_game(minimal_game_dir)
    setting_path = tmp_path / "setting.toml"
    setting_text = EXAMPLE_SETTING_PATH.read_text(encoding="utf-8")
    setting_text = setting_text.replace("long_text_line_width_limit = 26", "long_text_line_width_limit = 3")
    setting_text = setting_text.replace(
        'system_prompt_file = "prompts/text_translation_system.md"',
        f'system_prompt_file = "{(ROOT / "prompts" / "text_translation_system.md").as_posix()}"',
    )
    _ = setting_path.write_text(setting_text, encoding="utf-8")
    service = AgentToolkitService(game_registry=registry, setting_path=setting_path)
    pending_path = tmp_path / "pending-translations.json"

    export_report = await service.export_pending_translations(
        game_title="テストゲーム",
        output_path=pending_path,
        limit=None,
    )
    payload = load_json_object(pending_path)
    target_path = ""
    for location_path, raw_entry in payload.items():
        entry = ensure_json_object(coerce_json_value(raw_entry), location_path)
        if entry["item_type"] == "long_text":
            target_path = location_path
            entry["translation_lines"] = ["甲乙丙丁戊己庚辛"]
            _ = pending_path.write_text(
                json.dumps({target_path: entry}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            break

    import_report = await service.import_manual_translations(
        game_title="テストゲーム",
        input_path=pending_path,
    )

    assert export_report.status == "ok"
    assert target_path
    assert import_report.status == "ok"
    async with await registry.open_game("テストゲーム") as session:
        translated_items = await session.read_translated_items()
    translated_by_path = {item.location_path: item for item in translated_items}
    assert translated_by_path[target_path].translation_lines == ["甲乙丙", "丁戊己", "庚辛"]


@pytest.mark.asyncio
async def test_export_quality_fix_template_collects_repairable_items(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """质量修复模板会从报告问题导出标准修复表并预填当前译文。"""
    registry = GameRegistry(tmp_path / "db")
    _ = await registry.register_game(minimal_game_dir)
    service = AgentToolkitService(game_registry=registry, setting_path=EXAMPLE_SETTING_PATH)
    pending_path = tmp_path / "pending-translations.json"
    template_path = tmp_path / "quality-fix-template.json"

    _ = await service.export_pending_translations(
        game_title="テストゲーム",
        output_path=pending_path,
        limit=None,
    )
    payload = load_json_object(pending_path)
    sorted_paths = sorted(payload)
    quality_error_path = sorted_paths[0]
    residual_path = ""
    for candidate_path in sorted_paths:
        if candidate_path == quality_error_path:
            continue
        candidate_entry = ensure_json_object(coerce_json_value(payload[candidate_path]), candidate_path)
        candidate_lines = ensure_json_array(candidate_entry["original_lines"], f"{candidate_path}.original_lines")
        if any(isinstance(line, str) and _contains_japanese_test_char(line) for line in candidate_lines):
            residual_path = candidate_path
            break
    assert residual_path
    placeholder_path = next(path for path in sorted_paths if path not in {quality_error_path, residual_path})
    quality_error_entry = ensure_json_object(coerce_json_value(payload[quality_error_path]), quality_error_path)
    residual_entry = ensure_json_object(coerce_json_value(payload[residual_path]), residual_path)
    residual_original_lines = [
        line
        for line in ensure_json_array(residual_entry["original_lines"], f"{residual_path}.original_lines")
        if isinstance(line, str)
    ]
    async with await registry.open_game("テストゲーム") as session:
        await session.write_translation_items(
            [
                TranslationItem(
                    location_path=residual_path,
                    item_type="short_text",
                    role=None,
                    original_lines=residual_original_lines,
                    source_line_paths=[],
                    translation_lines=residual_original_lines,
                ),
                TranslationItem(
                    location_path=placeholder_path,
                    item_type="long_text",
                    role=None,
                    original_lines=["こんにちは"],
                    source_line_paths=[],
                    translation_lines=[r"\C[4]甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲"],
                ),
            ]
        )
        run_record = await session.start_translation_run(
            total_extracted=len(sorted_paths),
            pending_count=len(sorted_paths),
            deduplicated_count=len(sorted_paths),
            batch_count=1,
        )
        await session.write_translation_quality_errors(
            run_record.run_id,
            [
                TranslationErrorItem(
                    location_path=quality_error_path,
                    item_type="short_text",
                    role=None,
                    original_lines=[
                        line
                        for line in ensure_json_array(
                            quality_error_entry["original_lines"],
                            f"{quality_error_path}.original_lines",
                        )
                        if isinstance(line, str)
                    ],
                    translation_lines=["候选译文"],
                    error_type="AI漏翻",
                    error_detail=["测试质量错误"],
                    model_response='{"translation_lines":["候选译文"]}',
                )
            ],
        )

    report = await service.export_quality_fix_template(
        game_title="テストゲーム",
        output_path=template_path,
    )

    template = load_json_object(template_path)
    assert report.status == "ok"
    assert report.summary["quality_error_count"] == 1
    assert report.summary["japanese_residual_count"] == 1
    assert report.summary["placeholder_risk_count"] == 1
    assert report.summary["overwide_line_count"] == 1
    assert set(template) == {quality_error_path, residual_path, placeholder_path}
    quality_template = ensure_json_object(coerce_json_value(template[quality_error_path]), quality_error_path)
    placeholder_template = ensure_json_object(coerce_json_value(template[placeholder_path]), placeholder_path)
    assert quality_template["translation_lines"] == ["候选译文"]
    assert placeholder_template["translation_lines"] == [r"\C[4]甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲"]
    categories = ensure_json_object(report.details["problem_categories_by_path"], "problem_categories_by_path")
    assert categories[placeholder_path] == ["placeholder_risk", "overwide_line"]


@pytest.mark.asyncio
async def test_reset_translations_validates_paths_before_deleting(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """重置译文命令遇到非法定位路径时不做部分删除。"""
    registry = GameRegistry(tmp_path / "db")
    _ = await registry.register_game(minimal_game_dir)
    service = AgentToolkitService(game_registry=registry, setting_path=EXAMPLE_SETTING_PATH)
    pending_path = tmp_path / "pending-translations.json"
    reset_path = tmp_path / "reset-translations.json"

    _ = await service.export_pending_translations(
        game_title="テストゲーム",
        output_path=pending_path,
        limit=1,
    )
    payload = load_json_object(pending_path)
    target_path = next(iter(payload))
    async with await registry.open_game("テストゲーム") as session:
        await session.write_translation_items(
            [
                TranslationItem(
                    location_path=target_path,
                    item_type="short_text",
                    role=None,
                    original_lines=["こんにちは"],
                    source_line_paths=[],
                    translation_lines=["你好"],
                )
            ]
        )

    _ = reset_path.write_text(
        json.dumps({"location_paths": [target_path, "Missing.json/1"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    rejected_report = await service.reset_translations(
        game_title="テストゲーム",
        input_path=reset_path,
    )
    async with await registry.open_game("テストゲーム") as session:
        paths_after_reject = await session.read_translation_location_paths()

    _ = reset_path.write_text(
        json.dumps({"location_paths": [target_path]}, ensure_ascii=False),
        encoding="utf-8",
    )
    accepted_report = await service.reset_translations(
        game_title="テストゲーム",
        input_path=reset_path,
    )
    quality_report = await service.quality_report(game_title="テストゲーム")
    async with await registry.open_game("テストゲーム") as session:
        paths_after_accept = await session.read_translation_location_paths()

    assert rejected_report.status == "error"
    assert rejected_report.summary["reset_count"] == 0
    assert target_path in paths_after_reject
    assert accepted_report.status == "ok"
    assert accepted_report.summary["requested_count"] == 1
    assert accepted_report.summary["reset_count"] == 1
    assert target_path not in paths_after_accept
    pending_count = quality_report.summary["pending_count"]
    assert isinstance(pending_count, int)
    assert pending_count >= 1


@pytest.mark.asyncio
async def test_reset_translations_all_deletes_current_active_translation_cache(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """完整重译入口可以清除当前提取范围内全部已入库译文。"""
    registry = GameRegistry(tmp_path / "db")
    _ = await registry.register_game(minimal_game_dir)
    service = AgentToolkitService(game_registry=registry, setting_path=EXAMPLE_SETTING_PATH)
    pending_path = tmp_path / "pending-translations.json"

    _ = await service.export_pending_translations(
        game_title="テストゲーム",
        output_path=pending_path,
        limit=2,
    )
    payload = load_json_object(pending_path)
    target_paths = list(payload)[:2]
    async with await registry.open_game("テストゲーム") as session:
        await session.write_translation_items(
            [
                TranslationItem(
                    location_path=target_path,
                    item_type="short_text",
                    role=None,
                    original_lines=["こんにちは"],
                    source_line_paths=[],
                    translation_lines=["你好"],
                )
                for target_path in target_paths
            ]
        )

    report = await service.reset_translations(game_title="テストゲーム", reset_all=True)
    quality_report = await service.quality_report(game_title="テストゲーム")
    async with await registry.open_game("テストゲーム") as session:
        remaining_paths = await session.read_translation_location_paths()

    assert report.status == "warning"
    assert report.summary["mode"] == "all"
    assert report.summary["reset_count"] == len(target_paths)
    requested_count = report.summary["requested_count"]
    assert isinstance(requested_count, int)
    assert requested_count >= len(target_paths)
    assert all(target_path not in remaining_paths for target_path in target_paths)
    pending_count = quality_report.summary["pending_count"]
    assert isinstance(pending_count, int)
    assert pending_count >= len(target_paths)


@pytest.mark.asyncio
async def test_validate_placeholder_rules_previews_roundtrip() -> None:
    """占位符规则校验报告展示模型可见文本与还原结果。"""
    service = AgentToolkitService(setting_path=EXAMPLE_SETTING_PATH)

    report = await service.validate_placeholder_rules(
        game_title=None,
        custom_placeholder_rules_text='{"\\\\\\\\F\\\\[[^\\\\]]+\\\\]":"[CUSTOM_FACE_PORTRAIT_{index}]"}',
        sample_texts=[r"\F[GuideA]こんにちは\V[1]"],
    )

    assert report.status == "ok"
    assert report.summary["rule_count"] == 1
    samples = report.details["samples"]
    assert isinstance(samples, list)
    first_sample = samples[0]
    assert isinstance(first_sample, dict)
    assert first_sample["text_for_model"] == "[CUSTOM_FACE_PORTRAIT_1]こんにちは[RMMZ_VARIABLE_1]"
    assert first_sample["restored_text"] == r"\F[GuideA]こんにちは\V[1]"
    assert first_sample["roundtrip_ok"] is True


@pytest.mark.asyncio
async def test_validate_placeholder_rules_blocks_bare_escape_match() -> None:
    """占位符规则不得误匹配裸 \\n、\\r、\\t 这类常见文本转义。"""
    service = AgentToolkitService(setting_path=EXAMPLE_SETTING_PATH)

    unsafe_report = await service.validate_placeholder_rules(
        game_title=None,
        custom_placeholder_rules_text=json.dumps(
            {r"(?i)\\N\d*": "[CUSTOM_PLUGIN_N_{index}]"},
            ensure_ascii=False,
        ),
        sample_texts=[r"\n"],
    )
    safe_report = await service.validate_placeholder_rules(
        game_title=None,
        custom_placeholder_rules_text=json.dumps(
            {r"(?i)\\N\d+": "[CUSTOM_PLUGIN_N_{index}]"},
            ensure_ascii=False,
        ),
        sample_texts=[r"\N12"],
    )

    assert unsafe_report.status == "error"
    assert {error.code for error in unsafe_report.errors} == {"placeholder_rule_matches_common_escape"}
    assert safe_report.status == "ok"


@pytest.mark.asyncio
async def test_validate_placeholder_rules_warns_unicode_control_boundary() -> None:
    """占位符校验会提示非 ASCII 控制符边界，避免 Agent 按终端乱码猜测。"""
    service = AgentToolkitService(setting_path=EXAMPLE_SETTING_PATH)

    report = await service.validate_placeholder_rules(
        game_title=None,
        custom_placeholder_rules_text="{}",
        sample_texts=[r"\F3[66」「ふーん……？」"],
    )

    warning_codes = {warning.code for warning in report.warnings}
    assert "unprotected_control_unicode_boundary" in warning_codes
    assert "U+300D" in report.warnings[0].message


@pytest.mark.asyncio
async def test_validate_event_command_rules_previews_direct_parameter_write_back(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """事件指令规则校验会预演 direct parameters[N] 命中项的回写。"""
    common_events_path = minimal_game_dir / "data" / "CommonEvents.json"
    raw_common_events = cast(object, json.loads(common_events_path.read_text(encoding="utf-8")))
    common_events = ensure_json_array(coerce_json_value(raw_common_events), "CommonEvents.json")
    common_event = ensure_json_object(common_events[1], "CommonEvents[1]")
    commands = ensure_json_array(common_event["list"], "CommonEvents[1].list")
    command = ensure_json_object(commands[4], "CommonEvents[1].list[4]")
    parameters = ensure_json_array(command["parameters"], "CommonEvents[1].list[4].parameters")
    parameters[2] = "トップパラメータ"
    _ = common_events_path.write_text(json.dumps(common_events, ensure_ascii=False, indent=2), encoding="utf-8")
    registry = GameRegistry(tmp_path / "db")
    _ = await registry.register_game(minimal_game_dir)
    service = AgentToolkitService(game_registry=registry, setting_path=EXAMPLE_SETTING_PATH)

    report = await service.validate_event_command_rules(
        game_title="テストゲーム",
        rules_text=json.dumps(
            {
                "357": [
                    {
                        "match": {
                            "0": "TestPlugin",
                            "1": "Show",
                        },
                        "paths": [
                            "$['parameters'][2]",
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
    )

    assert report.status == "ok"
    preview = ensure_json_object(report.details["write_back_preview"], "write_back_preview")
    assert preview["status"] == "ok"
    assert preview["checked_item_count"] == 1


@pytest.mark.asyncio
async def test_validate_event_command_rules_reports_hits_per_rule(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """事件指令规则报告按规则组统计命中数量，避免把总命中数写到每条规则。"""
    registry = GameRegistry(tmp_path / "db")
    _ = await registry.register_game(minimal_game_dir)
    service = AgentToolkitService(game_registry=registry, setting_path=EXAMPLE_SETTING_PATH)
    rules_text = json.dumps(
        {
            "357": [
                {
                    "match": {"0": "TestPlugin", "1": "Show"},
                    "paths": ["$['parameters'][3]['message']"],
                },
                {
                    "match": {"0": "ComplexPlugin", "1": "ShowWindow"},
                    "paths": [
                        "$['parameters'][3]['window']['title']",
                        "$['parameters'][3]['choices'][*]",
                    ],
                },
            ]
        },
        ensure_ascii=False,
    )

    report = await service.validate_event_command_rules(
        game_title="テストゲーム",
        rules_text=rules_text,
    )

    assert report.status == "ok"
    rule_details = ensure_json_array(report.details["rules"], "rules")
    hit_counts = [
        ensure_json_object(coerce_json_value(raw_detail), f"rules[{index}]")["hit_count"]
        for index, raw_detail in enumerate(rule_details)
    ]
    assert hit_counts == [1, 3]


@pytest.mark.asyncio
async def test_quality_report_counts_errors_and_model_response(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """质量报告读取译文、质量错误和规则状态，输出阻断级错误摘要。"""
    registry = GameRegistry(tmp_path / "db")
    _ = await registry.register_game(minimal_game_dir)
    async with await registry.open_game("テストゲーム") as session:
        await session.write_translation_items(
            [
                TranslationItem(
                    location_path="CommonEvents.json/1/0",
                    item_type="long_text",
                    role="アリス",
                    original_lines=["こんにちは"],
                    source_line_paths=["CommonEvents.json/1/1"],
                    translation_lines=[r"\C[4]甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲"],
                )
            ]
        )
        run_record = await session.start_translation_run(
            total_extracted=3,
            pending_count=2,
            deduplicated_count=2,
            batch_count=1,
        )
        await session.write_translation_quality_errors(
            run_record.run_id,
            [
                TranslationErrorItem(
                    location_path="CommonEvents.json/1/2",
                    item_type="array",
                    role=None,
                    original_lines=["はい", "いいえ"],
                    translation_lines=[],
                    error_type="AI漏翻",
                    error_detail=["缺少键"],
                    model_response='{"bad": true}',
                )
            ],
        )

    service = AgentToolkitService(game_registry=registry, setting_path=EXAMPLE_SETTING_PATH)
    report = await service.quality_report(game_title="テストゲーム")

    assert report.status == "error"
    assert report.summary["quality_error_count"] == 1
    assert report.summary["model_response_error_count"] == 1
    assert report.summary["placeholder_risk_count"] == 1
    assert report.summary["overwide_line_count"] == 1
    assert report.details["error_type_counts"] == {"AI漏翻": 1}
    quality_error_items = ensure_json_array(report.details["quality_error_items"], "quality_error_items")
    placeholder_items = ensure_json_array(report.details["placeholder_risk_items"], "placeholder_risk_items")
    overwide_items = ensure_json_array(report.details["overwide_line_items"], "overwide_line_items")
    quality_error_detail = ensure_json_object(quality_error_items[0], "quality_error_items[0]")
    placeholder_detail = ensure_json_object(placeholder_items[0], "placeholder_risk_items[0]")
    overwide_detail = ensure_json_object(overwide_items[0], "overwide_line_items[0]")
    assert quality_error_detail["location_path"] == "CommonEvents.json/1/2"
    assert quality_error_detail["error_type"] == "AI漏翻"
    assert placeholder_detail["location_path"] == "CommonEvents.json/1/0"
    assert overwide_detail["location_path"] == "CommonEvents.json/1/0"
    assert overwide_detail["line_width"] == 30


@pytest.mark.asyncio
async def test_quality_report_flags_internal_placeholder_leak(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """质量报告必须拦截译文里的项目内部占位符。"""
    registry = GameRegistry(tmp_path / "db")
    _ = await registry.register_game(minimal_game_dir)
    async with await registry.open_game("テストゲーム") as session:
        await session.write_translation_items(
            [
                TranslationItem(
                    location_path="CommonEvents.json/1/0",
                    item_type="long_text",
                    role="アリス",
                    original_lines=["こんにちは"],
                    source_line_paths=["CommonEvents.json/1/1"],
                    translation_lines=["你好[RMMZ_TEXT_COLOR_0]"],
                )
            ]
        )

    service = AgentToolkitService(game_registry=registry, setting_path=EXAMPLE_SETTING_PATH)
    report = await service.quality_report(game_title="テストゲーム")

    assert report.status == "error"
    assert report.summary["placeholder_risk_count"] == 1
    placeholder_items = ensure_json_array(report.details["placeholder_risk_items"], "placeholder_risk_items")
    placeholder_detail = ensure_json_object(placeholder_items[0], "placeholder_risk_items[0]")
    assert placeholder_detail["location_path"] == "CommonEvents.json/1/0"
    assert "译文残留项目内部占位符" in str(placeholder_detail["reason"])


@pytest.mark.asyncio
async def test_quality_report_flags_multiline_short_text_overwide_line(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """质量报告按单值文本的实际显示行检查 Note 标签超宽风险。"""
    items_path = minimal_game_dir / "data" / "Items.json"
    raw_value = coerce_json_value(cast(object, json.loads(items_path.read_text(encoding="utf-8"))))
    items = ensure_json_array(raw_value, "Items.json")
    item = ensure_json_object(items[1], "Items.json[1]")
    item["note"] = "<拡張説明:説明\n原文>"
    _ = items_path.write_text(json.dumps(raw_value, ensure_ascii=False, indent=2), encoding="utf-8")
    registry = GameRegistry(tmp_path / "db")
    _ = await registry.register_game(minimal_game_dir)
    async with await registry.open_game("テストゲーム") as session:
        await session.replace_note_tag_text_rules(
            [
                NoteTagTextRuleRecord(
                    file_name="Items.json",
                    tag_names=["拡張説明"],
                )
            ]
        )
        await session.write_translation_items(
            [
                TranslationItem(
                    location_path="Items.json/1/note/拡張説明",
                    item_type="short_text",
                    original_lines=["説明\n原文"],
                    translation_lines=["说明\n甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲"],
                )
            ]
        )

    service = AgentToolkitService(game_registry=registry, setting_path=EXAMPLE_SETTING_PATH)
    report = await service.quality_report(game_title="テストゲーム")

    overwide_items = ensure_json_array(report.details["overwide_line_items"], "overwide_line_items")
    overwide_detail = ensure_json_object(overwide_items[0], "overwide_line_items[0]")
    assert report.summary["overwide_line_count"] == 1
    assert overwide_detail["location_path"] == "Items.json/1/note/拡張説明"
    assert overwide_detail["item_type"] == "short_text"
    assert overwide_detail["line_index"] == 1
    assert overwide_detail["line_width"] == 30
