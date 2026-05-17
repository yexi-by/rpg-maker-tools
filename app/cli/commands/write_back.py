"""写入游戏文件相关命令。

本模块负责正文写入、术语写入、字体还原和 run-all 流水线的 CLI 适配。
"""

from __future__ import annotations

import argparse

from app.cli.arguments import read_bool_arg, read_optional_str_arg
from app.cli.progress import build_progress_reporter
from app.cli.reports import build_font_restore_summary_report, build_write_back_summary_report
from app.cli.runtime import (
    HandlerSession,
    build_setting_overrides,
    build_translation_run_limits,
    ensure_text_translation_success,
    ensure_write_back_gate,
    resolve_target_game_title,
    translate_text_for_handler,
    write_back_for_handler,
)
from app.observability import logger


async def run_write_back_command(args: argparse.Namespace) -> int:
    """执行 `write-back` 命令。"""
    game_title = await resolve_target_game_title(args)
    setting_overrides = build_setting_overrides(args)
    async with HandlerSession() as handler:
        summary = await write_back_for_handler(
            handler=handler,
            game_title=game_title,
            setting_overrides=setting_overrides,
            args=args,
        )
    if read_bool_arg(args, "json_output"):
        report = build_write_back_summary_report(summary)
        print(report.to_json_text())
    return 0


async def run_restore_font_command(args: argparse.Namespace) -> int:
    """执行 `restore-font` 命令。"""
    game_title = await resolve_target_game_title(args)
    setting_overrides = build_setting_overrides(args)
    async with HandlerSession() as handler:
        summary = await handler.restore_font_replacement(
            game_title=game_title,
            setting_overrides=setting_overrides,
        )
    if read_bool_arg(args, "json_output"):
        report = build_font_restore_summary_report(summary)
        print(report.to_json_text())
    return 0


async def run_write_terminology_command(args: argparse.Namespace) -> int:
    """执行 `write-terminology` 命令。"""
    game_title = await resolve_target_game_title(args)
    setting_overrides = build_setting_overrides(args)
    async with HandlerSession() as handler:
        await ensure_write_back_gate(
            game_title=game_title,
            setting_overrides=setting_overrides,
            game_registry=handler.game_registry,
            require_complete_translation=False,
            args=args,
        )
        with build_progress_reporter("术语写回", args) as progress:
            _ = await handler.write_terminology(
                game_title=game_title,
                callbacks=progress.progress_callbacks(),
                setting_overrides=setting_overrides,
                confirm_font_overwrite=read_bool_arg(args, "confirm_font_overwrite"),
            )
    return 0


async def run_all_command(args: argparse.Namespace) -> int:
    """执行 `run-all` 命令。"""
    game_title = await resolve_target_game_title(args)
    placeholder_rules_text = read_optional_str_arg(args, "placeholder_rules")
    setting_overrides = build_setting_overrides(args)
    skip_write_back = read_bool_arg(args, "skip_write_back")

    async with HandlerSession() as handler:
        logger.info(f"[tag.phase]run-all 开始[/tag.phase] 游戏 [tag.count]{game_title}[/tag.count]")
        text_summary = await translate_text_for_handler(
            handler=handler,
            game_title=game_title,
            setting_overrides=setting_overrides,
            placeholder_rules_text=placeholder_rules_text,
            run_limits=build_translation_run_limits(args),
            args=args,
        )
        ensure_text_translation_success(text_summary)

        if skip_write_back:
            logger.warning(f"[tag.warning]已按参数跳过回写[/tag.warning] 游戏 [tag.count]{game_title}[/tag.count]")
            return 0

        _ = await write_back_for_handler(
            handler=handler,
            game_title=game_title,
            setting_overrides=setting_overrides,
            args=args,
        )
        logger.success(f"[tag.success]run-all 完成[/tag.success] 游戏 [tag.count]{game_title}[/tag.count]")
    return 0
