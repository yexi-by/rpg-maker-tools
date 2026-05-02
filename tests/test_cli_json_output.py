"""CLI 机器可读 JSON 输出测试。"""

from argparse import Namespace
import json
from pathlib import Path
from typing import cast

from main import main
from pytest import CaptureFixture

from app.agent_toolkit import AgentReport
from app.cli import build_parser
from app.cli import build_translate_summary_report
from app.cli import ensure_text_translation_not_blocked
from app.cli import write_report_outputs
from app.application.summaries import TextTranslationSummary
from app.rmmz.json_types import coerce_json_value, ensure_json_object


def namespace_optional_str(args: object, name: str) -> str | None:
    """从 argparse 结果中读取可选字符串参数，并在测试边界完成类型收窄。"""
    raw_value = cast(object, getattr(args, name))
    if raw_value is None:
        return None
    assert isinstance(raw_value, str)
    return raw_value


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
    assert namespace_optional_str(validate_args, "input") == "placeholder-rules.json"
    assert namespace_optional_str(validate_args, "placeholder_rules") is None


def test_rule_validation_commands_accept_input_files() -> None:
    """规则扫描与验收命令支持文件输入，避免 Agent 拼接大段 JSON 字符串。"""
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

    assert namespace_optional_str(scan_args, "input") == "placeholder-rules.json"
    assert namespace_optional_str(scan_args, "placeholder_rules") is None
    assert namespace_optional_str(plugin_args, "input") == "plugin-rules.json"
    assert namespace_optional_str(plugin_args, "rules") is None
    assert namespace_optional_str(event_args, "input") == "event-command-rules.json"
    assert namespace_optional_str(event_args, "rules") is None


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


def test_translate_command_accepts_json_summary_flag() -> None:
    """translate 支持 JSON 摘要，方便 Agent 区分命令状态和条目状态。"""
    parser = build_parser()

    args = parser.parse_args(["translate", "--game", "demo", "--json"])

    assert namespace_optional_str(args, "game") == "demo"
    assert getattr(args, "json_output") is True


def test_manual_translation_export_commands_are_black_box_friendly() -> None:
    """人工补译导出命令同时支持全量别名和分批限制。"""
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
        title="人工补译导出报告",
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
