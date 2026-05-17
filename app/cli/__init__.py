"""命令行包公共接口。

本模块只汇总对外稳定导入，避免调用方依赖 CLI 内部拆分路径。
"""

from __future__ import annotations

from app.cli.arguments import (
    format_argv,
    format_namespace,
    read_bool_arg,
    read_int_set_arg,
    read_optional_str_arg,
)
from app.cli.dispatch import dispatch_command, registered_command_names
from app.cli.errors import CliArgumentError, CliBusinessError
from app.cli.parser import build_parser, parser_command_names
from app.cli.progress import build_progress_reporter
from app.cli.reports import build_translate_summary_report, write_report_outputs
from app.cli.runtime import collect_write_back_gate_errors, ensure_text_translation_not_blocked

__all__: list[str] = [
    "CliArgumentError",
    "CliBusinessError",
    "build_parser",
    "build_progress_reporter",
    "build_translate_summary_report",
    "collect_write_back_gate_errors",
    "dispatch_command",
    "ensure_text_translation_not_blocked",
    "format_argv",
    "format_namespace",
    "parser_command_names",
    "read_bool_arg",
    "read_int_set_arg",
    "read_optional_str_arg",
    "registered_command_names",
    "write_report_outputs",
]
