"""
命令行编排模块。

本模块把 argparse 子命令适配到 `TranslationHandler`，统一处理进度条、业务失败、
退出码和资源释放。CLI 只保留当前核心流程需要的子命令。
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path
from types import TracebackType
from typing import Self, cast

from rich.progress import Progress, TaskID
from rich.table import Table

from app.application.handler import PluginTextAnalysisSummary, TextTranslationSummary, TranslationHandler
from app.observability import console, get_progress, logger


class CliBusinessError(Exception):
    """表示命令行任务遇到了已知业务失败。"""


class HandlerSession:
    """管理 `TranslationHandler` 的命令级生命周期。"""

    def __init__(self) -> None:
        """初始化尚未打开的编排器会话。"""
        self._handler: TranslationHandler | None = None

    async def __aenter__(self) -> TranslationHandler:
        """创建并返回本轮使用的业务编排器。"""
        self._handler = await TranslationHandler.create()
        return self._handler

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """关闭本轮持有的业务编排器。"""
        if self._handler is not None:
            await self._handler.close()
            self._handler = None


class CliProgressReporter:
    """将编排器进度回调适配为 Rich 进度条。"""

    def __init__(self, description: str) -> None:
        """初始化进度条适配器。"""
        self.description: str = description
        self._progress: Progress | None = None
        self._task_id: TaskID | None = None

    def __enter__(self) -> Self:
        """启动 Rich 进度条。"""
        self._progress = get_progress()
        self._progress.start()
        self._task_id = self._progress.add_task(self.description, total=1)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """停止 Rich 进度条。"""
        if self._progress is not None:
            self._progress.stop()
        self._progress = None
        self._task_id = None

    def progress_callbacks(self) -> tuple[Callable[[int, int], None], Callable[[int], None]]:
        """返回基础进度回调。"""
        return (self.set_progress, self.advance_progress)

    def status_callbacks(
        self,
    ) -> tuple[Callable[[int, int], None], Callable[[int], None], Callable[[str], None]]:
        """返回带状态文本的进度回调。"""
        return (self.set_progress, self.advance_progress, self.set_status)

    def set_progress(self, current: int, total: int) -> None:
        """设置当前任务的绝对进度。"""
        if self._progress is None or self._task_id is None:
            return
        visible_total = max(total, 1)
        visible_current = min(max(current, 0), visible_total)
        self._progress.update(self._task_id, completed=visible_current, total=visible_total)

    def advance_progress(self, count: int) -> None:
        """推进当前任务进度。"""
        if self._progress is None or self._task_id is None:
            return
        self._progress.advance(self._task_id, max(count, 0))

    def set_status(self, status: str) -> None:
        """更新当前任务状态文本。"""
        if self._progress is not None and self._task_id is not None:
            self._progress.update(self._task_id, description=f"{self.description}：{status}")
        logger.debug(f"[tag.phase]任务状态[/tag.phase] {status}")


def build_parser() -> argparse.ArgumentParser:
    """构建项目主命令行解析器。"""
    parser = argparse.ArgumentParser(description="RPG Maker 翻译工具命令行入口")
    _ = parser.add_argument(
        "--debug",
        action="store_true",
        help="在终端显示 DEBUG 级别日志，默认仅写入文件日志",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<命令>", required=True)

    _ = subparsers.add_parser("list", help="列出当前已注册游戏")

    add_game_parser = subparsers.add_parser("add-game", help="注册新的 RPG Maker 游戏目录")
    _ = add_game_parser.add_argument("--path", required=True, help="RPG Maker 游戏根目录")

    analyze_parser = subparsers.add_parser("analyze-plugin", help="分析 plugins.js 插件文本路径")
    _ = analyze_parser.add_argument("--game", required=True, help="目标游戏标题")

    translate_parser = subparsers.add_parser("translate", help="翻译指定游戏的正文")
    _ = translate_parser.add_argument("--game", required=True, help="目标游戏标题")

    write_back_parser = subparsers.add_parser("write-back", help="把译文回写到游戏目录")
    _ = write_back_parser.add_argument("--game", required=True, help="目标游戏标题")

    run_all_parser = subparsers.add_parser("run-all", help="按固定顺序执行插件解析、正文翻译和回写")
    _ = run_all_parser.add_argument("--game", required=True, help="目标游戏标题")
    _ = run_all_parser.add_argument(
        "--skip-plugin-analysis",
        action="store_true",
        help="跳过插件解析阶段，直接复用现有插件规则",
    )
    _ = run_all_parser.add_argument("--skip-write-back", action="store_true", help="跳过最终回写阶段")
    return parser


async def dispatch_command(args: argparse.Namespace) -> int:
    """分发并执行用户选择的子命令。"""
    command = read_str_arg(args, "command")

    if command == "list":
        return await run_list_command()
    if command == "add-game":
        return await run_add_game_command(args)
    if command == "analyze-plugin":
        return await run_analyze_plugin_command(args)
    if command == "translate":
        return await run_translate_command(args)
    if command == "write-back":
        return await run_write_back_command(args)
    if command == "run-all":
        return await run_all_command(args)

    raise CliBusinessError(f"未知命令：{command}")


async def run_list_command() -> int:
    """执行 `list` 命令。"""
    async with HandlerSession() as handler:
        items = sorted(handler.game_database_manager.items.values(), key=lambda item: item.game_title)
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
        logger.success(f"[tag.success]游戏注册完成[/tag.success] 标题 [tag.count]{game_title}[/tag.count]")
    return 0


async def run_analyze_plugin_command(args: argparse.Namespace) -> int:
    """执行 `analyze-plugin` 命令。"""
    game_title = read_str_arg(args, "game")
    async with HandlerSession() as handler:
        summary = await analyze_plugin_for_handler(handler=handler, game_title=game_title)
    ensure_plugin_analysis_success(summary)
    return 0


async def run_translate_command(args: argparse.Namespace) -> int:
    """执行 `translate` 命令。"""
    game_title = read_str_arg(args, "game")
    async with HandlerSession() as handler:
        summary = await translate_text_for_handler(handler=handler, game_title=game_title)
    ensure_text_translation_success(summary)
    return 0


async def run_write_back_command(args: argparse.Namespace) -> int:
    """执行 `write-back` 命令。"""
    game_title = read_str_arg(args, "game")
    async with HandlerSession() as handler:
        await write_back_for_handler(handler=handler, game_title=game_title)
    return 0


async def run_all_command(args: argparse.Namespace) -> int:
    """执行 `run-all` 命令。"""
    game_title = read_str_arg(args, "game")
    skip_plugin_analysis = read_bool_arg(args, "skip_plugin_analysis")
    skip_write_back = read_bool_arg(args, "skip_write_back")

    async with HandlerSession() as handler:
        logger.info(f"[tag.phase]run-all 开始[/tag.phase] 游戏 [tag.count]{game_title}[/tag.count]")
        if skip_plugin_analysis:
            logger.warning(
                f"[tag.warning]已按参数跳过插件解析[/tag.warning] 游戏 [tag.count]{game_title}[/tag.count]"
            )
        else:
            plugin_summary = await analyze_plugin_for_handler(handler=handler, game_title=game_title)
            ensure_plugin_analysis_success(plugin_summary)

        text_summary = await translate_text_for_handler(handler=handler, game_title=game_title)
        ensure_text_translation_success(text_summary)

        if skip_write_back:
            logger.warning(f"[tag.warning]已按参数跳过回写[/tag.warning] 游戏 [tag.count]{game_title}[/tag.count]")
            return 0

        await write_back_for_handler(handler=handler, game_title=game_title)
        logger.success(f"[tag.success]run-all 完成[/tag.success] 游戏 [tag.count]{game_title}[/tag.count]")
    return 0


async def analyze_plugin_for_handler(*, handler: TranslationHandler, game_title: str) -> PluginTextAnalysisSummary:
    """使用已创建的编排器分析插件文本规则。"""
    with CliProgressReporter("插件解析") as progress:
        return await handler.analyze_plugin_text(game_title=game_title, callbacks=progress.status_callbacks())


async def translate_text_for_handler(*, handler: TranslationHandler, game_title: str) -> TextTranslationSummary:
    """使用已创建的编排器翻译正文。"""
    with CliProgressReporter("正文翻译") as progress:
        return await handler.translate_text(game_title=game_title, callbacks=progress.status_callbacks())


async def write_back_for_handler(*, handler: TranslationHandler, game_title: str) -> None:
    """使用已创建的编排器回写译文。"""
    with CliProgressReporter("回写数据") as progress:
        await handler.write_back(game_title=game_title, callbacks=progress.progress_callbacks())


def ensure_plugin_analysis_success(summary: PluginTextAnalysisSummary) -> None:
    """校验插件解析摘要是否允许流水线继续。"""
    if not summary.has_failures:
        return
    raise CliBusinessError(f"插件解析存在失败插件，已停止后续流程：失败 {summary.failed_plugins} 个 / 总计 {summary.total_plugins} 个")


def ensure_text_translation_success(summary: TextTranslationSummary) -> None:
    """校验正文翻译摘要是否允许流水线继续。"""
    if summary.is_blocked:
        raise CliBusinessError(f"正文翻译被阻断：{summary.blocked_reason}")
    if summary.has_errors:
        raise CliBusinessError(f"正文翻译产生错误条目，已停止后续流程：成功 {summary.success_count} 条，失败 {summary.error_count} 条")


def read_str_arg(args: argparse.Namespace, name: str) -> str:
    """从命名空间读取非空字符串参数。"""
    raw_value = read_namespace_value(args, name)
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise CliBusinessError(f"命令参数缺失或为空：{name}")
    return raw_value.strip()


def read_bool_arg(args: argparse.Namespace, name: str) -> bool:
    """从命名空间读取布尔参数。"""
    raw_value = read_namespace_value(args, name)
    if not isinstance(raw_value, bool):
        raise CliBusinessError(f"命令参数不是布尔值：{name}")
    return raw_value


def format_namespace(args: argparse.Namespace) -> str:
    """把命令参数格式化为适合日志记录的摘要。"""
    namespace = cast(dict[str, object], vars(args))
    return ", ".join(f"{key}={value}" for key, value in sorted(namespace.items()))


def read_namespace_value(args: argparse.Namespace, name: str) -> object:
    """从 argparse 命名空间读取原始对象，并在入口处收窄动态类型。"""
    namespace = cast(dict[str, object], vars(args))
    return namespace.get(name)


def format_argv(argv: Sequence[str]) -> str:
    """格式化原始命令行参数。"""
    if not argv:
        return "<空>"
    return " ".join(argv)


__all__: list[str] = [
    "CliBusinessError",
    "build_parser",
    "dispatch_command",
    "format_argv",
    "format_namespace",
]
