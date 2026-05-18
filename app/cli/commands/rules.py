"""规则导入导出与校验命令。

本模块负责插件、事件指令、Note 标签、自定义占位符和源文残留规则的 CLI 适配。
"""

from __future__ import annotations

import argparse

from app.agent_toolkit import AgentReport, AgentToolkitService
from app.agent_toolkit.reports import issue
from app.cli.arguments import (
    read_bool_arg,
    read_int_set_arg,
    read_optional_str_list_arg,
    read_optional_text_source_arg,
    read_required_path_arg,
    read_required_text_source_arg,
    read_text_file,
)
from app.cli.runtime import HandlerSession, resolve_optional_target_game_title, resolve_target_game_title
from app.cli.reports import write_report_outputs


async def run_export_plugins_json_command(args: argparse.Namespace) -> int:
    """执行 `export-plugins-json` 命令。"""
    game_title = await resolve_target_game_title(args)
    output_path = read_required_path_arg(args, "output")
    async with HandlerSession() as handler:
        _ = await handler.export_plugins_json(game_title=game_title, output_path=output_path)
    return 0


async def run_import_plugin_rules_command(args: argparse.Namespace) -> int:
    """执行 `import-plugin-rules` 命令。"""
    game_title = await resolve_target_game_title(args)
    input_path = read_required_path_arg(args, "input")
    try:
        async with HandlerSession() as handler:
            summary = await handler.import_plugin_rules(game_title=game_title, input_path=input_path)
    except Exception as error:
        if not read_bool_arg(args, "json_output"):
            raise
        report = AgentReport.from_parts(
            errors=[issue("plugin_rules_invalid", f"插件规则导入失败: {type(error).__name__}: {error}")],
            warnings=[],
            summary={"game": game_title, "input": str(input_path)},
            details={},
        )
        write_report_outputs(report=report, args=args, title="插件规则导入报告")
        return 1
    if read_bool_arg(args, "json_output"):
        report = AgentReport.from_parts(
            errors=[],
            warnings=[],
            summary={
                "game": game_title,
                "input": str(input_path),
                "imported_plugin_count": summary.imported_plugin_count,
                "imported_rule_count": summary.imported_rule_count,
                "deleted_translation_items": summary.deleted_translation_items,
            },
            details={},
        )
        write_report_outputs(report=report, args=args, title="插件规则导入报告")
    return 0


async def run_export_event_commands_json_command(args: argparse.Namespace) -> int:
    """执行 `export-event-commands-json` 命令。"""
    game_title = await resolve_target_game_title(args)
    output_path = read_required_path_arg(args, "output")
    command_codes = read_int_set_arg(args, "codes")
    async with HandlerSession() as handler:
        _ = await handler.export_event_commands_json(
            game_title=game_title,
            output_path=output_path,
            command_codes=command_codes,
        )
    return 0


async def run_import_event_command_rules_command(args: argparse.Namespace) -> int:
    """执行 `import-event-command-rules` 命令。"""
    game_title = await resolve_target_game_title(args)
    input_path = read_required_path_arg(args, "input")
    try:
        async with HandlerSession() as handler:
            summary = await handler.import_event_command_rules(game_title=game_title, input_path=input_path)
    except Exception as error:
        if not read_bool_arg(args, "json_output"):
            raise
        report = AgentReport.from_parts(
            errors=[issue("event_command_rules_invalid", f"事件指令规则导入失败: {type(error).__name__}: {error}")],
            warnings=[],
            summary={"game": game_title, "input": str(input_path)},
            details={},
        )
        write_report_outputs(report=report, args=args, title="事件指令规则导入报告")
        return 1
    if read_bool_arg(args, "json_output"):
        report = AgentReport.from_parts(
            errors=[],
            warnings=[],
            summary={
                "game": game_title,
                "input": str(input_path),
                "imported_rule_group_count": summary.imported_rule_group_count,
                "imported_path_rule_count": summary.imported_path_rule_count,
                "deleted_translation_items": summary.deleted_translation_items,
            },
            details={},
        )
        write_report_outputs(report=report, args=args, title="事件指令规则导入报告")
    return 0


async def run_export_note_tag_candidates_command(args: argparse.Namespace) -> int:
    """执行 `export-note-tag-candidates` 命令。"""
    game_title = await resolve_target_game_title(args)
    output_path = read_required_path_arg(args, "output")
    service = AgentToolkitService()
    report = await service.export_note_tag_candidates(
        game_title=game_title,
        output_path=output_path,
    )
    write_report_outputs(report=report, args=args, title="Note 标签候选导出报告", write_output_file=False)
    return 1 if report.status == "error" else 0


async def run_validate_note_tag_rules_command(args: argparse.Namespace) -> int:
    """执行 `validate-note-tag-rules` 命令。"""
    game_title = await resolve_target_game_title(args)
    rules_text = await read_text_file(read_required_path_arg(args, "input"))
    service = AgentToolkitService()
    report = await service.validate_note_tag_rules(game_title=game_title, rules_text=rules_text)
    write_report_outputs(report=report, args=args, title="Note 标签规则校验报告")
    return 1 if report.status == "error" else 0


async def run_import_note_tag_rules_command(args: argparse.Namespace) -> int:
    """执行 `import-note-tag-rules` 命令。"""
    game_title = await resolve_target_game_title(args)
    rules_text = await read_text_file(read_required_path_arg(args, "input"))
    service = AgentToolkitService()
    report = await service.import_note_tag_rules(game_title=game_title, rules_text=rules_text)
    write_report_outputs(report=report, args=args, title="Note 标签规则导入报告")
    return 1 if report.status == "error" else 0


async def run_scan_placeholder_candidates_command(args: argparse.Namespace) -> int:
    """执行 `scan-placeholder-candidates` 命令。"""
    game_title = await resolve_target_game_title(args)
    placeholder_rules_text = await read_optional_text_source_arg(args, "placeholder_rules", "input")
    service = AgentToolkitService()
    report = await service.scan_placeholder_candidates(
        game_title=game_title,
        custom_placeholder_rules_text=placeholder_rules_text,
    )
    write_report_outputs(report=report, args=args, title="自定义控制符候选报告")
    return 1 if report.status == "error" else 0


async def run_validate_placeholder_rules_command(args: argparse.Namespace) -> int:
    """执行 `validate-placeholder-rules` 命令。"""
    game_title = await resolve_optional_target_game_title(args)
    placeholder_rules_text = await read_optional_text_source_arg(args, "placeholder_rules", "input")
    sample_texts = read_optional_str_list_arg(args, "sample") or []
    service = AgentToolkitService()
    report = await service.validate_placeholder_rules(
        game_title=game_title,
        custom_placeholder_rules_text=placeholder_rules_text,
        sample_texts=sample_texts,
    )
    write_report_outputs(report=report, args=args, title="自定义占位符规则校验报告")
    return 1 if report.status == "error" else 0


async def run_build_placeholder_rules_command(args: argparse.Namespace) -> int:
    """执行 `build-placeholder-rules` 命令。"""
    game_title = await resolve_target_game_title(args)
    output_path = read_required_path_arg(args, "output")
    service = AgentToolkitService()
    report = await service.build_placeholder_rules(game_title=game_title, output_path=output_path)
    write_report_outputs(report=report, args=args, title="占位符规则草稿报告", write_output_file=False)
    return 1 if report.status == "error" else 0


async def run_import_placeholder_rules_command(args: argparse.Namespace) -> int:
    """执行 `import-placeholder-rules` 命令。"""
    game_title = await resolve_target_game_title(args)
    rules_text = await read_required_text_source_arg(args, "rules", "input")
    try:
        async with HandlerSession() as handler:
            imported_rule_count = await handler.import_placeholder_rules(game_title=game_title, rules_text=rules_text)
    except Exception as error:
        if not read_bool_arg(args, "json_output"):
            raise
        report = AgentReport.from_parts(
            errors=[issue("placeholder_rules_invalid", f"自定义占位符规则导入失败: {type(error).__name__}: {error}")],
            warnings=[],
            summary={"game": game_title},
            details={},
        )
        write_report_outputs(report=report, args=args, title="自定义占位符规则导入报告")
        return 1
    if read_bool_arg(args, "json_output"):
        report = AgentReport.from_parts(
            errors=[],
            warnings=[] if imported_rule_count else [issue("placeholder_rules_empty", "已导入空自定义占位符规则")],
            summary={"game": game_title, "imported_rule_count": imported_rule_count},
            details={},
        )
        write_report_outputs(report=report, args=args, title="自定义占位符规则导入报告")
    return 0


async def run_validate_plugin_rules_command(args: argparse.Namespace) -> int:
    """执行 `validate-plugin-rules` 命令。"""
    game_title = await resolve_target_game_title(args)
    rules_text = await read_required_text_source_arg(args, "rules", "input")
    service = AgentToolkitService()
    report = await service.validate_plugin_rules(game_title=game_title, rules_text=rules_text)
    write_report_outputs(report=report, args=args, title="插件规则校验报告")
    return 1 if report.status == "error" else 0


async def run_validate_event_command_rules_command(args: argparse.Namespace) -> int:
    """执行 `validate-event-command-rules` 命令。"""
    game_title = await resolve_target_game_title(args)
    rules_text = await read_required_text_source_arg(args, "rules", "input")
    service = AgentToolkitService()
    report = await service.validate_event_command_rules(game_title=game_title, rules_text=rules_text)
    write_report_outputs(report=report, args=args, title="事件指令规则校验报告")
    return 1 if report.status == "error" else 0


async def run_validate_source_residual_rules_command(args: argparse.Namespace) -> int:
    """执行 `validate-source-residual-rules` 命令。"""
    game_title = await resolve_target_game_title(args)
    rules_text = await read_required_text_source_arg(args, "rules", "input")
    service = AgentToolkitService()
    report = await service.validate_source_residual_rules(game_title=game_title, rules_text=rules_text)
    write_report_outputs(report=report, args=args, title="源文残留例外规则校验报告")
    return 1 if report.status == "error" else 0


async def run_import_source_residual_rules_command(args: argparse.Namespace) -> int:
    """执行 `import-source-residual-rules` 命令。"""
    game_title = await resolve_target_game_title(args)
    rules_text = await read_required_text_source_arg(args, "rules", "input")
    service = AgentToolkitService()
    report = await service.import_source_residual_rules(game_title=game_title, rules_text=rules_text)
    write_report_outputs(report=report, args=args, title="源文残留例外规则导入报告")
    return 1 if report.status == "error" else 0
