"""游戏注册与环境诊断命令。

本模块负责列出、注册游戏，并把环境诊断服务适配为 CLI 子命令。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rich.table import Table

from app.agent_toolkit import AgentReport, AgentToolkitService
from app.cli.arguments import read_bool_arg, read_str_arg
from app.cli.runtime import HandlerSession, resolve_optional_target_game_title
from app.cli.reports import write_report_outputs
from app.observability import console, logger
from app.persistence import GameRegistry


async def run_list_command(args: argparse.Namespace) -> int:
    """执行 `list` 命令。"""
    registry = GameRegistry()
    items = await registry.list_games()
    if read_bool_arg(args, "json_output"):
        report = AgentReport.from_parts(
            errors=[],
            warnings=[],
            summary={"game_count": len(items)},
            details={
                "games": [
                    {
                        "game_title": item.game_title,
                        "game_path": str(item.game_path),
                        "db_path": str(item.db_path),
                    }
                    for item in items
                ]
            },
        )
        print(report.to_json_text())
        return 0
    if not items:
        logger.info("[tag.skip]当前还没有注册任何游戏[/tag.skip]")
        return 0

    table = Table(title="已注册游戏")
    table.add_column("游戏标题", style="cyan")
    table.add_column("游戏目录", style="blue")
    table.add_column("数据库", style="magenta")
    for item in items:
        table.add_row(item.game_title, str(item.game_path), str(item.db_path))
    console.print(table)
    return 0


async def run_add_game_command(args: argparse.Namespace) -> int:
    """执行 `add-game` 命令。"""
    game_path = Path(read_str_arg(args, "path"))
    async with HandlerSession() as handler:
        game_title = await handler.add_game(game_path)
        if read_bool_arg(args, "json_output"):
            report = AgentReport.from_parts(
                errors=[],
                warnings=[],
                summary={"game_title": game_title},
                details={"next_game_argument": game_title},
            )
            print(report.to_json_text())
            return 0
        logger.success(f"[tag.success]游戏注册完成[/tag.success] 标题 [tag.count]{game_title}[/tag.count]")
    return 0


async def run_doctor_command(args: argparse.Namespace) -> int:
    """执行 `doctor` 命令。"""
    game_title = await resolve_optional_target_game_title(args)
    check_llm = not read_bool_arg(args, "no_check_llm")
    service = AgentToolkitService()
    report = await service.doctor(game_title=game_title, check_llm=check_llm)
    write_report_outputs(report=report, args=args, title="环境诊断报告")
    return 1 if report.status == "error" else 0
