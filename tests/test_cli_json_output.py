"""CLI 机器可读 JSON 输出测试。"""

from argparse import Namespace
from dataclasses import dataclass
import json
from pathlib import Path
from typing import cast

from main import main
import pytest
from pytest import CaptureFixture, MonkeyPatch

from app.agent_toolkit import AgentReport
from app.agent_toolkit.reports import issue
from app.cli import build_parser
from app.cli import build_progress_reporter
from app.cli import build_translate_summary_report
from app.cli import collect_write_back_gate_errors
from app.cli import ensure_text_translation_not_blocked
from app.cli import parser_command_names
from app.cli import registered_command_names
from app.cli import write_report_outputs
from app.cli.errors import CliArgumentError
from app.cli.commands.registry import run_list_command
from app.cli.runtime import build_setting_overrides
from app.application.summaries import TextTranslationSummary
from app.rmmz.json_types import coerce_json_value, ensure_json_object


@dataclass(frozen=True)
class FakeRegisteredGame:
    """供 CLI 注册列表测试使用的已注册游戏记录。"""

    game_title: str
    game_path: Path
    content_root: Path
    db_path: Path
    engine_kind: str
    engine_version: str
    source_language: str
    target_language: str


def namespace_optional_str(args: object, name: str) -> str | None:
    """从 argparse 结果中读取可选字符串参数，并在测试边界完成类型收窄。"""
    raw_value = cast(object, getattr(args, name))
    if raw_value is None:
        return None
    assert isinstance(raw_value, str)
    return raw_value


def test_add_game_requires_explicit_source_language() -> None:
    """注册游戏必须显式声明源语言，避免 CLI 默默按日文处理。"""
    parser = build_parser()

    with pytest.raises(CliArgumentError, match="--source-language"):
        _ = parser.parse_args(["add-game", "--path", "demo", "--json"])

    args = parser.parse_args(["add-game", "--path", "demo", "--source-language", "ja", "--json"])

    assert namespace_optional_str(args, "source_language") == "ja"


@pytest.mark.asyncio
async def test_list_json_includes_engine_layout_metadata(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """`list --json` 必须公开数据库保存的引擎和布局元数据。"""

    class FakeRegistry:
        """替代真实注册表，避免测试依赖全局数据库目录。"""

        async def list_games(self) -> list[FakeRegisteredGame]:
            """返回一条带完整引擎布局字段的注册记录。"""
            return [
                FakeRegisteredGame(
                    game_title="示例游戏",
                    game_path=tmp_path / "game",
                    content_root=tmp_path / "game" / "www",
                    db_path=tmp_path / "db" / "示例游戏.db",
                    engine_kind="mv",
                    engine_version="1.6.1",
                    source_language="en",
                    target_language="zh-Hans",
                )
            ]

    monkeypatch.setattr("app.cli.commands.registry.GameRegistry", FakeRegistry)

    exit_code = await run_list_command(Namespace(json_output=True))

    captured = capsys.readouterr()
    raw_payload = cast(object, json.loads(captured.out))
    payload = ensure_json_object(coerce_json_value(raw_payload), "CLI JSON 输出")
    details = ensure_json_object(payload["details"], "CLI JSON 输出详情")
    games = details["games"]
    assert isinstance(games, list)
    first_game = ensure_json_object(games[0], "CLI JSON 已注册游戏")

    assert exit_code == 0
    assert first_game["engine_kind"] == "mv"
    assert first_game["engine_version"] == "1.6.1"
    assert first_game["source_language"] == "en"
    assert first_game["target_language"] == "zh-Hans"
    assert first_game["content_root"] == str(tmp_path / "game" / "www")
    assert first_game["game_path"] == str(tmp_path / "game")


def test_parser_commands_have_dispatch_handlers() -> None:
    """解析器暴露的每个子命令都必须在分发器中有处理函数。"""
    parser = build_parser()

    assert parser_command_names(parser) == registered_command_names()


def test_json_command_reports_unexpected_error_as_parseable_json(
    capsys: CaptureFixture[str],
) -> None:
    """`--json` 命令遇到异常时仍只向 stdout 输出 JSON。"""
    exit_code = main(
        [
            "scan-placeholder-candidates",
            "--game",
            "missing-game",
            "--placeholder-rules",
            r'{"\\N":"[CUSTOM_NAME_OVERRIDE_1]"}',
            "--json",
        ]
    )

    captured = capsys.readouterr()
    raw_payload = cast(object, json.loads(captured.out))
    payload = ensure_json_object(coerce_json_value(raw_payload), "CLI JSON 输出")
    errors = payload["errors"]
    assert isinstance(errors, list)
    first_error = errors[0]
    assert isinstance(first_error, dict)

    assert exit_code == 1
    assert payload["status"] == "error"
    assert first_error["code"] == "unexpected_error"
    assert "CLI 运行开始" not in captured.out


def test_json_import_command_reports_business_error_as_parseable_json(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    """规则导入命令的 `--json` 失败输出保持机器可读。"""
    rules_path = tmp_path / "placeholder-rules.json"
    _ = rules_path.write_text("{}\n", encoding="utf-8")

    exit_code = main(
        [
            "import-placeholder-rules",
            "--game",
            "missing-game",
            "--input",
            str(rules_path),
            "--json",
        ]
    )

    captured = capsys.readouterr()
    raw_payload = cast(object, json.loads(captured.out))
    payload = ensure_json_object(coerce_json_value(raw_payload), "CLI JSON 输出")
    errors = payload["errors"]
    assert isinstance(errors, list)
    first_error = errors[0]
    assert isinstance(first_error, dict)

    assert exit_code == 1
    assert payload["status"] == "error"
    assert first_error["code"] == "placeholder_rules_invalid"
    assert "CLI 运行开始" not in captured.out


def test_placeholder_rule_commands_accept_input_files() -> None:
    """占位符导入与校验命令支持文件输入，避免 Agent 手写长 JSON 参数。"""
    parser = build_parser()

    import_args = parser.parse_args(
        [
            "import-placeholder-rules",
            "--game",
            "demo",
            "--input",
            "placeholder-rules.json",
            "--json",
        ]
    )
    validate_args = parser.parse_args(
        [
            "validate-placeholder-rules",
            "--game",
            "demo",
            "--input",
            "placeholder-rules.json",
            "--json",
        ]
    )

    assert namespace_optional_str(import_args, "input") == "placeholder-rules.json"
    assert namespace_optional_str(import_args, "rules") is None
    assert getattr(import_args, "json_output") is True
    assert namespace_optional_str(validate_args, "input") == "placeholder-rules.json"
    assert namespace_optional_str(validate_args, "placeholder_rules") is None


def test_rule_commands_accept_input_files_and_json_output() -> None:
    """规则扫描、验收与导入命令支持文件输入和机器可读输出。"""
    parser = build_parser()

    scan_args = parser.parse_args(
        [
            "scan-placeholder-candidates",
            "--game",
            "demo",
            "--input",
            "placeholder-rules.json",
            "--json",
        ]
    )
    plugin_args = parser.parse_args(
        [
            "validate-plugin-rules",
            "--game",
            "demo",
            "--input",
            "plugin-rules.json",
            "--json",
        ]
    )
    plugin_import_args = parser.parse_args(
        [
            "import-plugin-rules",
            "--game",
            "demo",
            "--input",
            "plugin-rules.json",
            "--json",
        ]
    )
    event_args = parser.parse_args(
        [
            "validate-event-command-rules",
            "--game",
            "demo",
            "--input",
            "event-command-rules.json",
            "--json",
        ]
    )
    event_import_args = parser.parse_args(
        [
            "import-event-command-rules",
            "--game",
            "demo",
            "--input",
            "event-command-rules.json",
            "--json",
        ]
    )
    note_export_args = parser.parse_args(
        [
            "export-note-tag-candidates",
            "--game",
            "demo",
            "--output",
            "note-tag-candidates.json",
            "--json",
        ]
    )
    note_validate_args = parser.parse_args(
        [
            "validate-note-tag-rules",
            "--game",
            "demo",
            "--input",
            "note-tag-rules.json",
            "--json",
        ]
    )
    note_import_args = parser.parse_args(
        [
            "import-note-tag-rules",
            "--game",
            "demo",
            "--input",
            "note-tag-rules.json",
            "--json",
        ]
    )
    residual_args = parser.parse_args(
        [
            "validate-source-residual-rules",
            "--game",
            "demo",
            "--input",
            "source-residual-rules.json",
            "--json",
        ]
    )
    residual_import_args = parser.parse_args(
        [
            "import-source-residual-rules",
            "--game",
            "demo",
            "--input",
            "source-residual-rules.json",
            "--json",
        ]
    )
    terminology_import_args = parser.parse_args(
        [
            "import-terminology",
            "--game",
            "demo",
            "--input",
            "terminology/field-terms.json",
            "--glossary-input",
            "terminology/glossary.json",
            "--json",
        ]
    )

    assert namespace_optional_str(scan_args, "input") == "placeholder-rules.json"
    assert namespace_optional_str(scan_args, "placeholder_rules") is None
    assert namespace_optional_str(plugin_args, "input") == "plugin-rules.json"
    assert namespace_optional_str(plugin_args, "rules") is None
    assert namespace_optional_str(plugin_import_args, "input") == "plugin-rules.json"
    assert getattr(plugin_import_args, "json_output") is True
    assert namespace_optional_str(event_args, "input") == "event-command-rules.json"
    assert namespace_optional_str(event_args, "rules") is None
    assert namespace_optional_str(event_import_args, "input") == "event-command-rules.json"
    assert getattr(event_import_args, "json_output") is True
    assert namespace_optional_str(note_export_args, "output") == "note-tag-candidates.json"
    assert namespace_optional_str(note_validate_args, "input") == "note-tag-rules.json"
    assert getattr(note_validate_args, "json_output") is True
    assert namespace_optional_str(note_import_args, "input") == "note-tag-rules.json"
    assert getattr(note_import_args, "json_output") is True
    assert namespace_optional_str(residual_args, "input") == "source-residual-rules.json"
    assert namespace_optional_str(residual_args, "rules") is None
    assert namespace_optional_str(residual_import_args, "input") == "source-residual-rules.json"
    assert getattr(residual_import_args, "json_output") is True
    assert namespace_optional_str(terminology_import_args, "input") == "terminology/field-terms.json"
    assert namespace_optional_str(terminology_import_args, "glossary_input") == "terminology/glossary.json"
    assert getattr(terminology_import_args, "json_output") is True


def test_translate_quality_errors_do_not_fail_process() -> None:
    """单独 translate 命令的质量错误属于可续跑状态，不应变成进程失败。"""
    summary = TextTranslationSummary(
        total_extracted_items=10,
        pending_count=10,
        deduplicated_count=10,
        batch_count=1,
        success_count=8,
        error_count=2,
    )
    ensure_text_translation_not_blocked(summary)

    report = build_translate_summary_report(summary)

    assert report.status == "warning"
    assert report.summary["quality_error_count"] == 2


def test_partial_write_back_gate_only_blocks_saved_translation_risks() -> None:
    """标准名写回只拦截会写入游戏文件的危险译文。"""
    report = AgentReport.from_parts(
        errors=[
            issue("pending_translations", "存在还没成功保存译文的文本"),
            issue("source_residual", "发现译文存在源文残留风险"),
            issue("text_structure", "发现译文改动了游戏文本结构"),
            issue("llm_failures", "模型运行存在故障"),
        ],
        warnings=[],
        summary={},
        details={},
    )

    full_gate_codes = {
        error.code
        for error in collect_write_back_gate_errors(
            report=report,
            require_complete_translation=True,
        )
    }
    partial_gate_codes = {
        error.code
        for error in collect_write_back_gate_errors(
            report=report,
            require_complete_translation=False,
        )
    }

    assert full_gate_codes == {"pending_translations", "source_residual", "text_structure", "llm_failures"}
    assert partial_gate_codes == {"source_residual", "text_structure"}


def test_translate_command_accepts_json_summary_flag() -> None:
    """translate 支持 JSON 摘要，方便 Agent 区分命令状态和条目状态。"""
    parser = build_parser()

    args = parser.parse_args(["translate", "--game", "demo", "--json"])

    assert namespace_optional_str(args, "game") == "demo"
    assert getattr(args, "json_output") is True


def test_translate_command_accepts_source_residual_override_names() -> None:
    """源文残留 CLI 覆盖参数会进入配置覆盖对象。"""
    parser = build_parser()

    args = parser.parse_args(
        [
            "translate",
            "--game",
            "demo",
            "--source-residual-allowed-char",
            "ー",
            "--source-residual-allowed-tail-char",
            "よ",
            "--source-residual-segment-pattern",
            "[ぁ-ん]+",
        ]
    )
    overrides = build_setting_overrides(args)

    assert overrides.source_residual_allowed_chars == ["ー"]
    assert overrides.source_residual_allowed_tail_chars == ["よ"]
    assert overrides.source_residual_segment_pattern == "[ぁ-ん]+"


def test_json_progress_reports_to_stderr_without_polluting_stdout(
    capsys: CaptureFixture[str],
) -> None:
    """JSON 模式下长任务进度走 stderr，stdout 保持给最终 JSON。"""
    args = Namespace(agent_mode=True, json_output=True)

    with build_progress_reporter("正文翻译", args) as progress:
        set_progress, advance_progress, set_status = progress.status_callbacks()
        set_progress(0, 10)
        set_status("还没成功保存译文 10 条，相同原文合并后 8 条，批次 2 个")
        advance_progress(3)

    captured = capsys.readouterr()

    assert captured.out == ""
    assert "进度 正文翻译" in captured.err
    assert "[######--------------]" in captured.err
    assert "3/10" in captured.err
    assert "预计剩余" in captured.err
    assert "还没成功保存译文 10 条" in captured.err


def test_manual_translation_export_commands_are_black_box_friendly() -> None:
    """人工补译导出命令同时支持全量入口和分批限制。"""
    parser = build_parser()

    all_args = parser.parse_args(
        [
            "export-untranslated-translations",
            "--game",
            "demo",
            "--output",
            "pending-translations.json",
            "--json",
        ]
    )
    limited_args = parser.parse_args(
        [
            "export-pending-translations",
            "--game",
            "demo",
            "--limit",
            "20",
            "--output",
            "pending-translations.json",
            "--json",
        ]
    )

    assert namespace_optional_str(all_args, "game") == "demo"
    assert namespace_optional_str(all_args, "output") == "pending-translations.json"
    assert getattr(all_args, "json_output") is True
    assert namespace_optional_str(limited_args, "game") == "demo"
    assert getattr(limited_args, "limit") == 20


def test_quality_fix_and_reset_commands_are_black_box_friendly() -> None:
    """质量修复模板和显式重置命令提供稳定文件型接口。"""
    parser = build_parser()

    quality_fix_args = parser.parse_args(
        [
            "export-quality-fix-template",
            "--game",
            "demo",
            "--output",
            "quality-fix-template.json",
            "--json",
        ]
    )
    reset_args = parser.parse_args(
        [
            "reset-translations",
            "--game",
            "demo",
            "--input",
            "reset-translations.json",
            "--json",
        ]
    )
    reset_all_args = parser.parse_args(
        [
            "reset-translations",
            "--game",
            "demo",
            "--all",
            "--json",
        ]
    )

    assert namespace_optional_str(quality_fix_args, "output") == "quality-fix-template.json"
    assert getattr(quality_fix_args, "json_output") is True
    assert namespace_optional_str(reset_args, "input") == "reset-translations.json"
    assert getattr(reset_args, "json_output") is True
    assert namespace_optional_str(reset_all_args, "input") is None
    assert getattr(reset_all_args, "reset_all") is True
    assert getattr(reset_all_args, "json_output") is True


def test_reset_translations_invalid_input_returns_json_error(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    """reset-translations 的输入 schema 错误会返回机器可读错误。"""
    input_path = tmp_path / "reset-translations.json"
    _ = input_path.write_text("{}", encoding="utf-8")

    exit_code = main(
        [
            "reset-translations",
            "--game",
            "demo",
            "--input",
            str(input_path),
            "--json",
        ]
    )

    captured = capsys.readouterr()
    raw_payload = cast(object, json.loads(captured.out))
    payload = ensure_json_object(coerce_json_value(raw_payload), "CLI JSON 输出")
    errors = payload["errors"]
    assert isinstance(errors, list)
    first_error = errors[0]
    assert isinstance(first_error, dict)
    assert exit_code == 1
    assert first_error["code"] == "reset_translation_file"


def test_report_output_can_leave_data_output_file_untouched(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    """业务数据导出命令打印报告时不得覆盖自己的输出文件。"""
    output_path = tmp_path / "pending-translations.json"
    data_json = '{"entry": {"translation_lines": []}}\n'
    _ = output_path.write_text(data_json, encoding="utf-8")
    report = AgentReport(status="ok", summary={"exported_item_count": 1})

    write_report_outputs(
        report=report,
        args=Namespace(output=str(output_path), json_output=True),
        title="手动填写译文表导出报告",
        write_output_file=False,
    )

    captured = capsys.readouterr()
    raw_payload = cast(object, json.loads(captured.out))
    payload = ensure_json_object(coerce_json_value(raw_payload), "CLI JSON 输出")
    assert payload["status"] == "ok"
    assert output_path.read_text(encoding="utf-8") == data_json


def test_placeholder_rule_build_report_can_leave_rule_file_untouched(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    """占位符规则草稿命令打印报告时不得覆盖规则文件。"""
    output_path = tmp_path / "placeholder-rules.json"
    rules_json = '{"(?i)\\\\A<tag>\\\\Z": "[CUSTOM_TAG_1]"}\n'
    _ = output_path.write_text(rules_json, encoding="utf-8")
    report = AgentReport(status="ok", summary={"draft_rule_count": 1})

    write_report_outputs(
        report=report,
        args=Namespace(output=str(output_path), json_output=True),
        title="占位符规则草稿报告",
        write_output_file=False,
    )

    captured = capsys.readouterr()
    raw_payload = cast(object, json.loads(captured.out))
    payload = ensure_json_object(coerce_json_value(raw_payload), "CLI JSON 输出")
    assert payload["status"] == "ok"
    assert output_path.read_text(encoding="utf-8") == rules_json
