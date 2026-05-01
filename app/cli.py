"""
命令行编排模块。

本模块把 argparse 子命令适配到 `TranslationHandler`，统一处理进度条、业务失败、
退出码和资源释放。
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path
from types import TracebackType
from typing import Self, cast

from rich.progress import Progress, TaskID
from rich.table import Table

from app.agent_toolkit import AgentReport, AgentToolkitService
from app.application.handler import TextTranslationSummary, TranslationHandler
from app.config import SettingOverrides
from app.observability import console, get_progress, logger
from app.persistence import GameRegistry


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

    doctor_parser = subparsers.add_parser("doctor", help="检查项目配置、模型连接和目标游戏状态")
    _ = doctor_parser.add_argument("--game", help="目标游戏标题；不传时只检查项目级状态")
    _ = doctor_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")
    _ = doctor_parser.add_argument("--no-check-llm", action="store_true", help="跳过模型连通性检查")

    add_game_parser = subparsers.add_parser("add-game", help="注册新的 RPG Maker 游戏目录")
    _ = add_game_parser.add_argument("--path", required=True, help="RPG Maker 游戏根目录")

    export_plugins_parser = subparsers.add_parser(
        "export-plugins-json",
        help="把当前游戏的 js/plugins.js 转成纯 JSON 文件",
    )
    _ = export_plugins_parser.add_argument("--game", required=True, help="目标游戏标题")
    _ = export_plugins_parser.add_argument("--output", required=True, help="导出的 plugins JSON 文件")

    import_plugin_parser = subparsers.add_parser(
        "import-plugin-rules",
        help="把外部插件规则 JSON 导入游戏数据库",
    )
    _ = import_plugin_parser.add_argument("--game", required=True, help="目标游戏标题")
    _ = import_plugin_parser.add_argument("--input", required=True, help="外部插件规则 JSON 文件")

    export_event_commands_parser = subparsers.add_parser(
        "export-event-commands-json",
        help="把 data 事件指令参数导出为 JSON 文件",
    )
    _ = export_event_commands_parser.add_argument("--game", required=True, help="目标游戏标题")
    _ = export_event_commands_parser.add_argument("--output", required=True, help="导出的事件指令 JSON 文件")
    _ = export_event_commands_parser.add_argument(
        "--code",
        action="extend",
        nargs="+",
        type=int,
        dest="codes",
        metavar="CODE",
        help="需要导出的事件指令编码数组；传入后覆盖配置文件默认编码数组",
    )

    import_event_command_parser = subparsers.add_parser(
        "import-event-command-rules",
        help="把外部事件指令规则 JSON 导入游戏数据库",
    )
    _ = import_event_command_parser.add_argument("--game", required=True, help="目标游戏标题")
    _ = import_event_command_parser.add_argument("--input", required=True, help="外部事件指令规则 JSON 文件")

    scan_placeholder_parser = subparsers.add_parser(
        "scan-placeholder-candidates",
        help="扫描疑似自定义控制符候选",
    )
    _ = scan_placeholder_parser.add_argument("--game", required=True, help="目标游戏标题")
    _ = scan_placeholder_parser.add_argument("--output", help="写出 JSON 报告文件")
    _ = scan_placeholder_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")
    _ = scan_placeholder_parser.add_argument(
        "--placeholder-rules",
        help="本次扫描使用的自定义占位符规则 JSON 字符串；传入后不会读取项目根目录默认规则",
    )

    quality_report_parser = subparsers.add_parser(
        "quality-report",
        help="生成当前游戏翻译质量报告",
    )
    _ = quality_report_parser.add_argument("--game", required=True, help="目标游戏标题")
    _ = quality_report_parser.add_argument("--output", help="写出 JSON 报告文件")
    _ = quality_report_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")

    translate_parser = subparsers.add_parser("translate", help="翻译指定游戏的正文")
    _ = translate_parser.add_argument("--game", required=True, help="目标游戏标题")
    _ = translate_parser.add_argument(
        "--placeholder-rules",
        help="本次翻译使用的自定义占位符规则 JSON 字符串；传入后不会读取项目根目录默认规则",
    )
    add_setting_override_arguments(translate_parser)

    write_back_parser = subparsers.add_parser("write-back", help="把译文回写到游戏目录")
    _ = write_back_parser.add_argument("--game", required=True, help="目标游戏标题")
    add_setting_override_arguments(write_back_parser)

    export_name_parser = subparsers.add_parser(
        "export-name-context",
        help="导出 101 名字框和地图显示名上下文 JSON，供外部 Agent 填写译名",
    )
    _ = export_name_parser.add_argument("--game", required=True, help="目标游戏标题")
    _ = export_name_parser.add_argument(
        "--output-dir",
        required=True,
        help="临时导出目录；建议放在项目目录之外",
    )

    import_name_parser = subparsers.add_parser(
        "import-name-context",
        help="把外部 Agent 填写后的术语表大 JSON 导入游戏数据库",
    )
    _ = import_name_parser.add_argument("--game", required=True, help="目标游戏标题")
    _ = import_name_parser.add_argument("--input", required=True, help="已填写的大 JSON 路径")

    write_name_parser = subparsers.add_parser(
        "write-name-context",
        help="根据数据库中的术语表写回 101 名字框和 MapXXX.displayName",
    )
    _ = write_name_parser.add_argument("--game", required=True, help="目标游戏标题")
    add_setting_override_arguments(write_name_parser)

    run_all_parser = subparsers.add_parser("run-all", help="按固定顺序执行正文翻译和回写")
    _ = run_all_parser.add_argument("--game", required=True, help="目标游戏标题")
    _ = run_all_parser.add_argument(
        "--placeholder-rules",
        help="本次翻译使用的自定义占位符规则 JSON 字符串；传入后不会读取项目根目录默认规则",
    )
    _ = run_all_parser.add_argument("--skip-write-back", action="store_true", help="跳过最终回写阶段")
    add_setting_override_arguments(run_all_parser)
    return parser


def add_setting_override_arguments(parser: argparse.ArgumentParser) -> None:
    """为正文翻译命令增加 `setting.toml` 等价覆盖参数。"""
    group = parser.add_argument_group("配置覆盖")
    _ = group.add_argument("--llm-model", help="正文模型名称")
    _ = group.add_argument("--llm-timeout", type=int, help="正文模型请求超时秒数")
    _ = group.add_argument("--translation-token-size", type=int, help="每批目标 token 上限")
    _ = group.add_argument("--translation-factor", type=float, help="字符到 token 的换算系数")
    _ = group.add_argument("--translation-max-command-items", type=int, help="同角色连续补充条目上限")
    _ = group.add_argument("--translation-worker-count", type=int, help="正文翻译并发 worker 数")
    _ = group.add_argument("--translation-rpm", help="正文翻译 RPM；传 none 表示不限速")
    _ = group.add_argument("--translation-retry-count", type=int, help="可恢复错误重试次数")
    _ = group.add_argument("--translation-retry-delay", type=int, help="可恢复错误重试间隔秒数")
    _ = group.add_argument("--system-prompt", help="正文翻译系统提示词文本")
    _ = group.add_argument("--replacement-font-path", help="写回时复制并替换引用的字体路径")
    _ = group.add_argument(
        "--event-command-default-code",
        action="extend",
        nargs="+",
        type=int,
        dest="event_command_default_codes",
        metavar="CODE",
        help="事件指令参数默认编码数组",
    )
    _ = group.add_argument(
        "--strip-wrapping-punctuation-pair",
        action="append",
        nargs=2,
        metavar=("LEFT", "RIGHT"),
        help="提取时剥离的成对包裹标点，可重复传入",
    )
    _ = group.add_argument(
        "--allowed-japanese-char",
        action="extend",
        nargs="+",
        dest="allowed_japanese_chars",
        metavar="CHAR",
        help="日文残留检查允许保留的字符数组",
    )
    _ = group.add_argument(
        "--allowed-japanese-tail-char",
        action="extend",
        nargs="+",
        dest="allowed_japanese_tail_chars",
        metavar="CHAR",
        help="日文残留检查允许作为语气尾音的字符数组",
    )
    _ = group.add_argument(
        "--line-split-punctuation",
        action="extend",
        nargs="+",
        dest="line_split_punctuations",
        metavar="PUNCT",
        help="长文本优先切行标点数组",
    )
    _ = group.add_argument("--long-text-line-width-limit", type=int, help="长文本单行宽度上限")
    _ = group.add_argument("--line-width-count-pattern", help="长文本宽度计数字符正则")
    _ = group.add_argument("--source-text-required-pattern", help="进入正文翻译的源语言字符正则")
    _ = group.add_argument("--japanese-segment-pattern", help="日文残留片段识别正则")
    _ = group.add_argument("--residual-escape-sequence-pattern", help="残留检查前剥离的转义序列正则")


async def dispatch_command(args: argparse.Namespace) -> int:
    """分发并执行用户选择的子命令。"""
    command = read_str_arg(args, "command")

    if command == "list":
        return await run_list_command()
    if command == "doctor":
        return await run_doctor_command(args)
    if command == "add-game":
        return await run_add_game_command(args)
    if command == "export-plugins-json":
        return await run_export_plugins_json_command(args)
    if command == "import-plugin-rules":
        return await run_import_plugin_rules_command(args)
    if command == "export-event-commands-json":
        return await run_export_event_commands_json_command(args)
    if command == "import-event-command-rules":
        return await run_import_event_command_rules_command(args)
    if command == "scan-placeholder-candidates":
        return await run_scan_placeholder_candidates_command(args)
    if command == "quality-report":
        return await run_quality_report_command(args)
    if command == "translate":
        return await run_translate_command(args)
    if command == "write-back":
        return await run_write_back_command(args)
    if command == "export-name-context":
        return await run_export_name_context_command(args)
    if command == "import-name-context":
        return await run_import_name_context_command(args)
    if command == "write-name-context":
        return await run_write_name_context_command(args)
    if command == "run-all":
        return await run_all_command(args)

    raise CliBusinessError(f"未知命令：{command}")


async def run_list_command() -> int:
    """执行 `list` 命令。"""
    registry = GameRegistry()
    items = await registry.list_games()
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


async def run_import_plugin_rules_command(args: argparse.Namespace) -> int:
    """执行 `import-plugin-rules` 命令。"""
    game_title = read_str_arg(args, "game")
    input_path = read_required_path_arg(args, "input")
    async with HandlerSession() as handler:
        _ = await handler.import_plugin_rules(game_title=game_title, input_path=input_path)
    return 0


async def run_doctor_command(args: argparse.Namespace) -> int:
    """执行 `doctor` 命令。"""
    game_title = read_optional_str_arg(args, "game")
    check_llm = not read_bool_arg(args, "no_check_llm")
    service = AgentToolkitService()
    report = await service.doctor(game_title=game_title, check_llm=check_llm)
    write_report_outputs(report=report, args=args, title="环境诊断报告")
    return 1 if report.status == "error" else 0


async def run_export_plugins_json_command(args: argparse.Namespace) -> int:
    """执行 `export-plugins-json` 命令。"""
    game_title = read_str_arg(args, "game")
    output_path = read_required_path_arg(args, "output")
    async with HandlerSession() as handler:
        _ = await handler.export_plugins_json(game_title=game_title, output_path=output_path)
    return 0


async def run_export_event_commands_json_command(args: argparse.Namespace) -> int:
    """执行 `export-event-commands-json` 命令。"""
    game_title = read_str_arg(args, "game")
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
    game_title = read_str_arg(args, "game")
    input_path = read_required_path_arg(args, "input")
    async with HandlerSession() as handler:
        _ = await handler.import_event_command_rules(game_title=game_title, input_path=input_path)
    return 0


async def run_scan_placeholder_candidates_command(args: argparse.Namespace) -> int:
    """执行 `scan-placeholder-candidates` 命令。"""
    game_title = read_str_arg(args, "game")
    placeholder_rules_text = read_optional_str_arg(args, "placeholder_rules")
    service = AgentToolkitService()
    report = await service.scan_placeholder_candidates(
        game_title=game_title,
        custom_placeholder_rules_text=placeholder_rules_text,
    )
    write_report_outputs(report=report, args=args, title="自定义控制符候选报告")
    return 1 if report.status == "error" else 0


async def run_quality_report_command(args: argparse.Namespace) -> int:
    """执行 `quality-report` 命令。"""
    game_title = read_str_arg(args, "game")
    service = AgentToolkitService()
    report = await service.quality_report(game_title=game_title)
    write_report_outputs(report=report, args=args, title="翻译质量报告")
    return 1 if report.status == "error" else 0


async def run_translate_command(args: argparse.Namespace) -> int:
    """执行 `translate` 命令。"""
    game_title = read_str_arg(args, "game")
    placeholder_rules_text = read_optional_str_arg(args, "placeholder_rules")
    setting_overrides = build_setting_overrides(args)
    async with HandlerSession() as handler:
        summary = await translate_text_for_handler(
            handler=handler,
            game_title=game_title,
            setting_overrides=setting_overrides,
            placeholder_rules_text=placeholder_rules_text,
        )
    ensure_text_translation_success(summary)
    return 0


async def run_write_back_command(args: argparse.Namespace) -> int:
    """执行 `write-back` 命令。"""
    game_title = read_str_arg(args, "game")
    setting_overrides = build_setting_overrides(args)
    async with HandlerSession() as handler:
        await write_back_for_handler(
            handler=handler,
            game_title=game_title,
            setting_overrides=setting_overrides,
        )
    return 0


async def run_export_name_context_command(args: argparse.Namespace) -> int:
    """执行 `export-name-context` 命令。"""
    game_title = read_str_arg(args, "game")
    output_dir = read_required_path_arg(args, "output_dir")
    async with HandlerSession() as handler:
        summary = await handler.export_name_context(game_title=game_title, output_dir=output_dir)
    logger.success(f"[tag.success]标准名上下文可交给外部 Agent 处理[/tag.success] 大 JSON [tag.path]{summary.registry_path}[/tag.path] 小 JSON 目录 [tag.path]{summary.sample_dir}[/tag.path]")
    return 0


async def run_import_name_context_command(args: argparse.Namespace) -> int:
    """执行 `import-name-context` 命令。"""
    game_title = read_str_arg(args, "game")
    input_path = read_required_path_arg(args, "input")
    async with HandlerSession() as handler:
        _ = await handler.import_name_context(game_title=game_title, input_path=input_path)
    return 0


async def run_write_name_context_command(args: argparse.Namespace) -> int:
    """执行 `write-name-context` 命令。"""
    game_title = read_str_arg(args, "game")
    setting_overrides = build_setting_overrides(args)
    async with HandlerSession() as handler:
        with CliProgressReporter("标准名写回") as progress:
            _ = await handler.write_name_context(
                game_title=game_title,
                callbacks=progress.progress_callbacks(),
                setting_overrides=setting_overrides,
            )
    return 0


async def run_all_command(args: argparse.Namespace) -> int:
    """执行 `run-all` 命令。"""
    game_title = read_str_arg(args, "game")
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
        )
        ensure_text_translation_success(text_summary)

        if skip_write_back:
            logger.warning(f"[tag.warning]已按参数跳过回写[/tag.warning] 游戏 [tag.count]{game_title}[/tag.count]")
            return 0

        await write_back_for_handler(
            handler=handler,
            game_title=game_title,
            setting_overrides=setting_overrides,
        )
        logger.success(f"[tag.success]run-all 完成[/tag.success] 游戏 [tag.count]{game_title}[/tag.count]")
    return 0


async def translate_text_for_handler(
    *,
    handler: TranslationHandler,
    game_title: str,
    setting_overrides: SettingOverrides,
    placeholder_rules_text: str | None,
) -> TextTranslationSummary:
    """使用已创建的编排器翻译正文。"""
    with CliProgressReporter("正文翻译") as progress:
        return await handler.translate_text(
            game_title=game_title,
            setting_overrides=setting_overrides,
            custom_placeholder_rules_text=placeholder_rules_text,
            callbacks=progress.status_callbacks(),
        )


async def write_back_for_handler(
    *,
    handler: TranslationHandler,
    game_title: str,
    setting_overrides: SettingOverrides,
) -> None:
    """使用已创建的编排器回写译文。"""
    with CliProgressReporter("回写数据") as progress:
        await handler.write_back(
            game_title=game_title,
            callbacks=progress.progress_callbacks(),
            setting_overrides=setting_overrides,
        )


def ensure_text_translation_success(summary: TextTranslationSummary) -> None:
    """校验正文翻译摘要是否允许流水线继续。"""
    if summary.is_blocked:
        raise CliBusinessError(f"正文翻译被阻断：{summary.blocked_reason}")
    if summary.has_errors:
        raise CliBusinessError(f"正文翻译产生错误条目，已停止后续流程：成功 {summary.success_count} 条，失败 {summary.error_count} 条")


def write_report_outputs(*, report: AgentReport, args: argparse.Namespace, title: str) -> None:
    """按用户参数输出 Agent 工具包报告。"""
    output_path = read_optional_path_arg(args, "output")
    json_text = report.to_json_text()
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _ = output_path.write_text(f"{json_text}\n", encoding="utf-8")

    if read_bool_arg(args, "json_output"):
        print(json_text)
        return

    render_agent_report(report=report, title=title)
    if output_path is not None:
        logger.success(f"[tag.success]JSON 报告已写出[/tag.success] 文件 [tag.path]{output_path}[/tag.path]")


def render_agent_report(*, report: AgentReport, title: str) -> None:
    """用 Rich 表格展示报告摘要和问题列表。"""
    summary_table = Table(title=title)
    summary_table.add_column("字段", style="cyan")
    summary_table.add_column("值", style="magenta")
    summary_table.add_row("状态", report.status)
    for key, value in report.summary.items():
        summary_table.add_row(key, str(value))
    console.print(summary_table)

    if report.errors:
        error_table = Table(title="阻断错误")
        error_table.add_column("代码", style="red")
        error_table.add_column("说明", style="white")
        for item in report.errors:
            error_table.add_row(item.code, item.message)
        console.print(error_table)

    if report.warnings:
        warning_table = Table(title="告警")
        warning_table.add_column("代码", style="yellow")
        warning_table.add_column("说明", style="white")
        for item in report.warnings:
            warning_table.add_row(item.code, item.message)
        console.print(warning_table)


def build_setting_overrides(args: argparse.Namespace) -> SettingOverrides:
    """从 CLI 参数构建配置覆盖对象。"""
    rpm_value, rpm_is_set = read_optional_rpm_arg(args, "translation_rpm")
    return SettingOverrides(
        llm_model=read_optional_str_arg(args, "llm_model"),
        llm_timeout=read_optional_int_arg(args, "llm_timeout"),
        translation_token_size=read_optional_int_arg(args, "translation_token_size"),
        translation_factor=read_optional_float_arg(args, "translation_factor"),
        translation_max_command_items=read_optional_int_arg(
            args,
            "translation_max_command_items",
        ),
        text_translation_worker_count=read_optional_int_arg(
            args,
            "translation_worker_count",
        ),
        text_translation_rpm=rpm_value,
        text_translation_rpm_is_set=rpm_is_set,
        text_translation_retry_count=read_optional_int_arg(
            args,
            "translation_retry_count",
        ),
        text_translation_retry_delay=read_optional_int_arg(
            args,
            "translation_retry_delay",
        ),
        text_translation_system_prompt=read_optional_str_arg(args, "system_prompt"),
        write_back_replacement_font_path=read_optional_str_arg(
            args,
            "replacement_font_path",
        ),
        event_command_default_codes=read_optional_int_list_arg(
            args,
            "event_command_default_codes",
        ),
        strip_wrapping_punctuation_pairs=read_optional_pair_list_arg(
            args,
            "strip_wrapping_punctuation_pair",
        ),
        allowed_japanese_chars=read_optional_str_list_arg(args, "allowed_japanese_chars"),
        allowed_japanese_tail_chars=read_optional_str_list_arg(
            args,
            "allowed_japanese_tail_chars",
        ),
        line_split_punctuations=read_optional_str_list_arg(
            args,
            "line_split_punctuations",
        ),
        long_text_line_width_limit=read_optional_int_arg(
            args,
            "long_text_line_width_limit",
        ),
        line_width_count_pattern=read_optional_str_arg(args, "line_width_count_pattern"),
        source_text_required_pattern=read_optional_str_arg(args, "source_text_required_pattern"),
        japanese_segment_pattern=read_optional_str_arg(args, "japanese_segment_pattern"),
        residual_escape_sequence_pattern=read_optional_str_arg(
            args,
            "residual_escape_sequence_pattern",
        ),
    )


def read_str_arg(args: argparse.Namespace, name: str) -> str:
    """从命名空间读取非空字符串参数。"""
    raw_value = read_namespace_value(args, name)
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise CliBusinessError(f"命令参数缺失或为空：{name}")
    return raw_value.strip()


def read_optional_str_arg(args: argparse.Namespace, name: str) -> str | None:
    """从命名空间读取可选字符串参数。"""
    raw_value = read_namespace_value(args, name)
    if raw_value is None:
        return None
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise CliBusinessError(f"命令参数不是有效字符串：{name}")
    return raw_value.strip()


def read_bool_arg(args: argparse.Namespace, name: str) -> bool:
    """从命名空间读取布尔参数。"""
    raw_value = read_namespace_value(args, name)
    if not isinstance(raw_value, bool):
        raise CliBusinessError(f"命令参数不是布尔值：{name}")
    return raw_value


def read_optional_int_arg(args: argparse.Namespace, name: str) -> int | None:
    """从命名空间读取可选整数参数。"""
    raw_value = read_namespace_value(args, name)
    if raw_value is None:
        return None
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        raise CliBusinessError(f"命令参数不是整数：{name}")
    return raw_value


def read_optional_float_arg(args: argparse.Namespace, name: str) -> float | None:
    """从命名空间读取可选浮点数参数。"""
    raw_value = read_namespace_value(args, name)
    if raw_value is None:
        return None
    if isinstance(raw_value, bool) or not isinstance(raw_value, float):
        raise CliBusinessError(f"命令参数不是数字：{name}")
    return raw_value


def read_optional_rpm_arg(args: argparse.Namespace, name: str) -> tuple[int | None, bool]:
    """读取可选 RPM 参数，支持用 `none` 表示不限速。"""
    raw_value = read_namespace_value(args, name)
    if raw_value is None:
        return None, False
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise CliBusinessError(f"命令参数不是有效 RPM：{name}")

    normalized_value = raw_value.strip().lower()
    if normalized_value in {"none", "null", "off", "unlimited", "no", "不限"}:
        return None, True

    try:
        rpm = int(normalized_value)
    except ValueError as error:
        raise CliBusinessError(f"命令参数不是有效 RPM：{name}") from error
    if rpm <= 0:
        raise CliBusinessError(f"命令参数必须是正整数或 none：{name}")
    return rpm, True


def read_optional_int_list_arg(args: argparse.Namespace, name: str) -> list[int] | None:
    """从命名空间读取可选整数数组参数。"""
    raw_value = read_namespace_value(args, name)
    if raw_value is None:
        return None
    if not isinstance(raw_value, list):
        raise CliBusinessError(f"命令参数不是整数数组：{name}")

    result: list[int] = []
    raw_items = cast(list[object], raw_value)
    for item in raw_items:
        if isinstance(item, bool) or not isinstance(item, int):
            raise CliBusinessError(f"命令参数不是整数：{name}")
        result.append(item)
    return result


def read_optional_str_list_arg(args: argparse.Namespace, name: str) -> list[str] | None:
    """从命名空间读取可选字符串数组参数。"""
    raw_value = read_namespace_value(args, name)
    if raw_value is None:
        return None
    if not isinstance(raw_value, list):
        raise CliBusinessError(f"命令参数不是字符串数组：{name}")

    result: list[str] = []
    raw_items = cast(list[object], raw_value)
    for item in raw_items:
        if not isinstance(item, str) or not item:
            raise CliBusinessError(f"命令参数不是有效字符串：{name}")
        result.append(item)
    return result


def read_optional_pair_list_arg(
    args: argparse.Namespace,
    name: str,
) -> list[tuple[str, str]] | None:
    """从命名空间读取可选字符串二元组数组参数。"""
    raw_value = read_namespace_value(args, name)
    if raw_value is None:
        return None
    if not isinstance(raw_value, list):
        raise CliBusinessError(f"命令参数不是字符串二元组数组：{name}")

    pairs: list[tuple[str, str]] = []
    raw_pairs = cast(list[object], raw_value)
    for raw_pair in raw_pairs:
        if not isinstance(raw_pair, list):
            raise CliBusinessError(f"命令参数不是字符串二元组：{name}")
        pair_items = cast(list[object], raw_pair)
        if len(pair_items) != 2:
            raise CliBusinessError(f"命令参数不是字符串二元组：{name}")
        left = pair_items[0]
        right = pair_items[1]
        if not isinstance(left, str) or not isinstance(right, str):
            raise CliBusinessError(f"命令参数不是字符串二元组：{name}")
        pairs.append((left, right))
    return pairs


def read_int_set_arg(args: argparse.Namespace, name: str) -> set[int] | None:
    """从命名空间读取可选整数集合参数。"""
    raw_value = read_namespace_value(args, name)
    if raw_value is None:
        return None
    if not isinstance(raw_value, list):
        raise CliBusinessError(f"命令参数不是整数列表：{name}")

    result: set[int] = set()
    raw_items = cast(list[object], raw_value)
    for item in raw_items:
        if isinstance(item, bool) or not isinstance(item, int):
            raise CliBusinessError(f"命令参数不是整数：{name}")
        result.add(item)
    return result


def read_optional_path_arg(args: argparse.Namespace, name: str) -> Path | None:
    """从命名空间读取可选路径参数。"""
    raw_value = read_namespace_value(args, name)
    if raw_value is None:
        return None
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise CliBusinessError(f"命令参数不是有效路径：{name}")
    return Path(raw_value).resolve()


def read_required_path_arg(args: argparse.Namespace, name: str) -> Path:
    """从命名空间读取必填路径参数。"""
    path = read_optional_path_arg(args, name)
    if path is None:
        raise CliBusinessError(f"命令参数缺失或为空：{name}")
    return path


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
