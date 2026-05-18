"""命令行子命令分发器。

本模块维护子命令到处理函数的显式映射，保证解析器新增命令时能被测试发现。
"""

from __future__ import annotations

import argparse
from collections.abc import Awaitable, Callable

from app.cli.arguments import read_str_arg
from app.cli.commands.registry import run_add_game_command, run_doctor_command, run_list_command
from app.cli.commands.rules import (
    run_build_placeholder_rules_command,
    run_export_event_commands_json_command,
    run_export_note_tag_candidates_command,
    run_export_plugins_json_command,
    run_import_event_command_rules_command,
    run_import_note_tag_rules_command,
    run_import_placeholder_rules_command,
    run_import_plugin_rules_command,
    run_import_source_residual_rules_command,
    run_scan_placeholder_candidates_command,
    run_validate_event_command_rules_command,
    run_validate_note_tag_rules_command,
    run_validate_placeholder_rules_command,
    run_validate_plugin_rules_command,
    run_validate_source_residual_rules_command,
)
from app.cli.commands.terminology import run_export_terminology_command, run_import_terminology_command
from app.cli.commands.translation import (
    run_export_pending_translations_command,
    run_export_quality_fix_template_command,
    run_export_untranslated_translations_command,
    run_import_manual_translations_command,
    run_quality_report_command,
    run_reset_translations_command,
    run_translate_command,
    run_translation_status_command,
)
from app.cli.commands.workspace import (
    run_cleanup_agent_workspace_command,
    run_prepare_agent_workspace_command,
    run_validate_agent_workspace_command,
)
from app.cli.commands.write_back import (
    run_all_command,
    run_restore_font_command,
    run_write_back_command,
    run_write_terminology_command,
)
from app.cli.errors import CliBusinessError

CommandHandler = Callable[[argparse.Namespace], Awaitable[int]]

COMMAND_HANDLERS: dict[str, CommandHandler] = {
    "list": run_list_command,
    "doctor": run_doctor_command,
    "add-game": run_add_game_command,
    "export-plugins-json": run_export_plugins_json_command,
    "import-plugin-rules": run_import_plugin_rules_command,
    "export-event-commands-json": run_export_event_commands_json_command,
    "import-event-command-rules": run_import_event_command_rules_command,
    "export-note-tag-candidates": run_export_note_tag_candidates_command,
    "validate-note-tag-rules": run_validate_note_tag_rules_command,
    "import-note-tag-rules": run_import_note_tag_rules_command,
    "scan-placeholder-candidates": run_scan_placeholder_candidates_command,
    "validate-placeholder-rules": run_validate_placeholder_rules_command,
    "build-placeholder-rules": run_build_placeholder_rules_command,
    "import-placeholder-rules": run_import_placeholder_rules_command,
    "validate-plugin-rules": run_validate_plugin_rules_command,
    "validate-event-command-rules": run_validate_event_command_rules_command,
    "prepare-agent-workspace": run_prepare_agent_workspace_command,
    "validate-agent-workspace": run_validate_agent_workspace_command,
    "cleanup-agent-workspace": run_cleanup_agent_workspace_command,
    "quality-report": run_quality_report_command,
    "export-pending-translations": run_export_pending_translations_command,
    "export-untranslated-translations": run_export_untranslated_translations_command,
    "export-quality-fix-template": run_export_quality_fix_template_command,
    "import-manual-translations": run_import_manual_translations_command,
    "reset-translations": run_reset_translations_command,
    "validate-source-residual-rules": run_validate_source_residual_rules_command,
    "import-source-residual-rules": run_import_source_residual_rules_command,
    "translation-status": run_translation_status_command,
    "translate": run_translate_command,
    "write-back": run_write_back_command,
    "restore-font": run_restore_font_command,
    "export-terminology": run_export_terminology_command,
    "import-terminology": run_import_terminology_command,
    "write-terminology": run_write_terminology_command,
    "run-all": run_all_command,
}


def registered_command_names() -> frozenset[str]:
    """返回分发器当前支持的子命令集合。"""
    return frozenset(COMMAND_HANDLERS)


async def dispatch_command(args: argparse.Namespace) -> int:
    """分发并执行用户选择的子命令。"""
    command = read_str_arg(args, "command")
    handler = COMMAND_HANDLERS.get(command)
    if handler is None:
        raise CliBusinessError(f"未知命令：{command}")
    return await handler(args)


__all__ = ["dispatch_command", "registered_command_names"]
