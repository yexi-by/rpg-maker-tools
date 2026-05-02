"""Agent 工具包诊断、扫描和质量报告测试。"""

import json
from pathlib import Path
from typing import cast

import pytest

from app.agent_toolkit import AgentToolkitService
from app.llm import LLMHandler
from app.persistence import GameRegistry
from app.rmmz.json_types import coerce_json_value, ensure_json_array, ensure_json_object
from app.rmmz.schema import TranslationErrorItem, TranslationItem

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_SETTING_PATH = ROOT / "setting.example.toml"


def load_json_object(path: Path) -> dict[str, object]:
    """读取测试产物 JSON 对象，并在边界处收窄动态解析结果。"""
    raw_value = cast(object, json.loads(path.read_text(encoding="utf-8")))
    json_object = ensure_json_object(coerce_json_value(raw_value), str(path))
    return {key: value for key, value in json_object.items()}


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
    """Agent 工作区会携带占位符规则草稿，避免重复手写解析脚本。"""
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
    guide_path = workspace / "WORKSPACE.md"
    assert rules_path.exists()
    assert guide_path.exists()
    rules = load_json_object(rules_path)
    assert rules == {r"(?i)\\F\d*\[[^\]\r\n]+\]": "[CUSTOM_FACE_PORTRAIT_{index}]"}
    assert report.summary["placeholder_rule_draft_count"] == 1
    guide_text = guide_path.read_text(encoding="utf-8")
    assert "validate-plugin-rules --game <游戏标题> --input" in guide_text
    assert "validate-event-command-rules --game <游戏标题> --input" in guide_text


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

    import_report = await service.import_manual_translations(
        game_title="テストゲーム",
        input_path=pending_path,
    )

    assert import_report.status == "ok"
    async with await registry.open_game("テストゲーム") as session:
        translated_items = await session.read_translated_items()
    translated_by_path = {item.location_path: item for item in translated_items}
    assert translated_by_path[target_path].translation_lines == ["你好"]


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
                    translation_lines=["你好"],
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
    assert report.details["error_type_counts"] == {"AI漏翻": 1}
