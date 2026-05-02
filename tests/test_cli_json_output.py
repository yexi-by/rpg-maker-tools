"""CLI 机器可读 JSON 输出测试。"""

import json
from typing import cast

from main import main
from pytest import CaptureFixture

from app.cli import build_parser
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
