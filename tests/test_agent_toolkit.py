"""Agent 工具包诊断、扫描和质量报告测试。"""

from pathlib import Path

import pytest

from app.agent_toolkit import AgentToolkitService
from app.llm import LLMHandler
from app.persistence import GameRegistry
from app.rmmz.schema import TranslationErrorItem, TranslationItem

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_SETTING_PATH = ROOT / "setting.example.toml"


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
async def test_quality_report_counts_errors_and_model_response(
    minimal_game_dir: Path,
    tmp_path: Path,
) -> None:
    """质量报告读取译文、错误表和规则状态，输出阻断级错误摘要。"""
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
        error_table = await session.start_error_table()
        await session.write_error_items(
            error_table,
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
    assert report.summary["latest_error_count"] == 1
    assert report.summary["model_response_error_count"] == 1
    assert report.details["error_type_counts"] == {"AI漏翻": 1}
