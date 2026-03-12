"""交互式命令行入口。"""

from __future__ import annotations

import asyncio
from typing import Literal

from rich.panel import Panel
from rich.table import Table

from app.config import resolve_setting_path
from app.core.di import TranslationProvider
from app.core.handler import TranslationHandler
from app.utils import console, logger

ActionId = Literal[
    "build_glossary",
    "translate_text",
    "retry_error_table",
    "write_back",
    "run_all",
    "exit",
]

MENU_ACTIONS: list[tuple[ActionId, str]] = [
    ("build_glossary", "构建术语表"),
    ("translate_text", "翻译正文"),
    ("retry_error_table", "重翻错误表"),
    ("write_back", "回写游戏文件"),
    ("run_all", "一键全流程"),
    ("exit", "退出"),
]


class CliApp:
    """管理交互式命令行会话。"""

    def __init__(self) -> None:
        self.setting_path = resolve_setting_path()

    async def run(self) -> None:
        provider = TranslationProvider()
        handler = await TranslationHandler.create(provider)
        self._render_banner()

        while True:
            action_id = self._prompt_action()
            if action_id == "exit":
                console.print("[tag.warning]CLI 会话已结束[/tag.warning]")
                return

            await self._run_action(action_id, handler)

    def _render_banner(self) -> None:
        console.print(
            Panel.fit(
                f"[tag.menu.title]RPG Maker Tools CLI[/tag.menu.title]\n"
                f"配置文件: [tag.path]{self.setting_path}[/tag.path]\n"
                "修改配置后请重启进程生效。",
                border_style="tag.menu.title",
            )
        )

    def _prompt_action(self) -> ActionId:
        while True:
            table = Table(title="[tag.menu.title]可执行动作[/tag.menu.title]")
            table.add_column("序号", style="tag.menu.index", justify="right")
            table.add_column("动作")

            for index, (_, label) in enumerate(MENU_ACTIONS, start=1):
                table.add_row(str(index), label)

            console.print(table)
            raw_choice = console.input(
                "[tag.menu.prompt]请输入序号[/tag.menu.prompt]: "
            ).strip()
            if not raw_choice.isdigit():
                console.print("[tag.warning]请输入有效数字[/tag.warning]")
                continue

            choice_index = int(raw_choice)
            if 1 <= choice_index <= len(MENU_ACTIONS):
                return MENU_ACTIONS[choice_index - 1][0]

            console.print("[tag.warning]序号超出范围，请重新输入[/tag.warning]")

    async def _run_action(self, action_id: ActionId, handler: TranslationHandler) -> None:
        action_label = dict(MENU_ACTIONS)[action_id]
        console.print(f"[tag.phase]开始执行[/tag.phase] {action_label}")

        try:
            await self._dispatch_action(action_id, handler)
        except Exception:
            logger.exception(f"[tag.exception]{action_label}执行失败[/tag.exception]")
            console.print(
                Panel.fit(
                    f"[tag.failure]{action_label}执行失败[/tag.failure]\n"
                    "详细错误请查看上方异常堆栈。",
                    border_style="tag.failure",
                )
            )
        else:
            console.print(
                Panel.fit(
                    f"[tag.success]{action_label}执行完成[/tag.success]",
                    border_style="tag.success",
                )
            )

    async def _dispatch_action(
        self,
        action_id: ActionId,
        handler: TranslationHandler,
    ) -> None:
        if action_id == "build_glossary":
            async for _ in handler.build_glossary():
                continue
            return

        if action_id == "translate_text":
            await handler.translate_text()
            return

        if action_id == "retry_error_table":
            await handler.retry_error_table()
            return

        if action_id == "write_back":
            await handler.write_back()
            return

        if action_id == "run_all":
            async for _ in handler.build_glossary():
                continue
            await handler.translate_text()
            await handler.write_back()
            return

        raise ValueError(f"未知动作: {action_id}")


def run_cli() -> None:
    """运行交互式命令行。"""
    try:
        asyncio.run(CliApp().run())
    except KeyboardInterrupt:
        console.print("\n[tag.warning]检测到中断，CLI 已退出[/tag.warning]")
