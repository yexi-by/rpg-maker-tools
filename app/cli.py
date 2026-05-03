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
from typing import NoReturn, Self, cast, override

import aiofiles
from rich.progress import Progress, TaskID
from rich.table import Table

from app.agent_toolkit import AgentIssue, AgentReport, AgentToolkitService
from app.agent_toolkit.reports import issue
from app.application.handler import (
    TextTranslationSummary,
    TranslationHandler,
    TranslationRunLimits,
    WriteBackSummary,
)
from app.config import SettingOverrides
from app.observability import console, get_progress, logger
from app.persistence import GameRegistry


class CliBusinessError(Exception):
    """表示命令行任务遇到了已知业务失败。"""


class CliArgumentError(Exception):
    """表示命令行参数解析失败。"""


class CliArgumentParser(argparse.ArgumentParser):
    """把 argparse 默认退出改成可被 JSON 输出层接管的异常。"""

    @override
    def error(self, message: str) -> NoReturn:
        """抛出参数错误，避免 argparse 直接写 stderr 并退出。"""
        raise CliArgumentError(message)


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


class NoopProgressReporter:
    """Agent 模式使用的无输出进度回调。"""

    def __enter__(self) -> Self:
        """进入无输出进度上下文。"""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """退出无输出进度上下文。"""

    def progress_callbacks(self) -> tuple[Callable[[int, int], None], Callable[[int], None]]:
        """返回无输出基础进度回调。"""
        return (self.set_progress, self.advance_progress)

    def status_callbacks(
        self,
    ) -> tuple[Callable[[int, int], None], Callable[[int], None], Callable[[str], None]]:
        """返回无输出状态进度回调。"""
        return (self.set_progress, self.advance_progress, self.set_status)

    def set_progress(self, current: int, total: int) -> None:
        """忽略绝对进度。"""
        _ = (current, total)

    def advance_progress(self, count: int) -> None:
        """忽略推进进度。"""
        _ = count

    def set_status(self, status: str) -> None:
        """把状态写入 DEBUG 文件日志，不输出进度条。"""
        logger.debug(f"[tag.phase]任务状态[/tag.phase] {status}")


def build_progress_reporter(description: str, args: argparse.Namespace) -> CliProgressReporter | NoopProgressReporter:
    """根据运行模式创建进度回调适配器。"""
    if read_bool_arg(args, "agent_mode"):
        return NoopProgressReporter()
    return CliProgressReporter(description)


def build_parser() -> argparse.ArgumentParser:
    """构建项目主命令行解析器。"""
    parser = CliArgumentParser(description="RPG Maker 翻译工具命令行入口")
    _ = parser.add_argument(
        "--debug",
        action="store_true",
        help="在终端显示 DEBUG 级别日志，默认仅写入文件日志",
    )
    _ = parser.add_argument(
        "--agent-mode",
        action="store_true",
        help="使用适合外部 Agent 读取的简洁日志，不输出 Rich 进度条和 ANSI 样式",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<命令>", required=True, parser_class=CliArgumentParser)

    list_parser = subparsers.add_parser("list", help="列出当前已注册游戏")
    _ = list_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")

    doctor_parser = subparsers.add_parser("doctor", help="检查项目配置、模型连接和目标游戏状态")
    add_optional_target_arguments(doctor_parser, required=False)
    _ = doctor_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")
    _ = doctor_parser.add_argument("--no-check-llm", action="store_true", help="跳过模型连通性检查")

    add_game_parser = subparsers.add_parser("add-game", help="注册新的 RPG Maker 游戏目录")
    _ = add_game_parser.add_argument("--path", required=True, help="RPG Maker 游戏根目录")
    _ = add_game_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")

    export_plugins_parser = subparsers.add_parser(
        "export-plugins-json",
        help="把当前游戏的 js/plugins.js 转成纯 JSON 文件",
    )
    add_optional_target_arguments(export_plugins_parser)
    _ = export_plugins_parser.add_argument("--output", required=True, help="导出的 plugins JSON 文件")

    import_plugin_parser = subparsers.add_parser(
        "import-plugin-rules",
        help="把外部插件规则 JSON 导入游戏数据库",
    )
    add_optional_target_arguments(import_plugin_parser)
    _ = import_plugin_parser.add_argument("--input", required=True, help="外部插件规则 JSON 文件")

    export_event_commands_parser = subparsers.add_parser(
        "export-event-commands-json",
        help="把 data 事件指令参数导出为 JSON 文件",
    )
    add_optional_target_arguments(export_event_commands_parser)
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
    add_optional_target_arguments(import_event_command_parser)
    _ = import_event_command_parser.add_argument("--input", required=True, help="外部事件指令规则 JSON 文件")

    export_note_tag_parser = subparsers.add_parser(
        "export-note-tag-candidates",
        help="导出基础数据库 note 字段中的 Note 标签候选",
    )
    add_optional_target_arguments(export_note_tag_parser)
    _ = export_note_tag_parser.add_argument("--output", required=True, help="Note 标签候选 JSON 输出文件")
    _ = export_note_tag_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")

    validate_note_tag_parser = subparsers.add_parser(
        "validate-note-tag-rules",
        help="校验 Note 标签文本规则 JSON",
    )
    add_optional_target_arguments(validate_note_tag_parser)
    _ = validate_note_tag_parser.add_argument("--input", required=True, help="Note 标签规则 JSON 文件")
    _ = validate_note_tag_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")

    import_note_tag_parser = subparsers.add_parser(
        "import-note-tag-rules",
        help="把外部 Note 标签文本规则 JSON 导入游戏数据库",
    )
    add_optional_target_arguments(import_note_tag_parser)
    _ = import_note_tag_parser.add_argument("--input", required=True, help="Note 标签规则 JSON 文件")
    _ = import_note_tag_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")

    scan_placeholder_parser = subparsers.add_parser(
        "scan-placeholder-candidates",
        help="扫描疑似自定义控制符候选",
    )
    add_optional_target_arguments(scan_placeholder_parser)
    _ = scan_placeholder_parser.add_argument("--output", help="写出 JSON 报告文件")
    _ = scan_placeholder_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")
    scan_placeholder_source_group = scan_placeholder_parser.add_mutually_exclusive_group()
    _ = scan_placeholder_source_group.add_argument(
        "--placeholder-rules",
        help="本次扫描使用的自定义占位符规则 JSON 字符串；传入后不会读取当前游戏数据库规则",
    )
    _ = scan_placeholder_source_group.add_argument(
        "--input",
        help="本次扫描使用的自定义占位符规则 JSON 文件；传入后不会读取当前游戏数据库规则",
    )

    validate_placeholder_parser = subparsers.add_parser(
        "validate-placeholder-rules",
        help="校验自定义占位符规则，并预览样本文本的占位符替换与还原",
    )
    add_optional_target_arguments(validate_placeholder_parser, required=False)
    _ = validate_placeholder_parser.add_argument("--output", help="写出 JSON 报告文件")
    _ = validate_placeholder_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")
    validate_placeholder_source_group = validate_placeholder_parser.add_mutually_exclusive_group()
    _ = validate_placeholder_source_group.add_argument(
        "--placeholder-rules",
        help="本次校验使用的自定义占位符规则 JSON 字符串；传入后不会读取当前游戏数据库规则",
    )
    _ = validate_placeholder_source_group.add_argument(
        "--input",
        help="本次校验使用的自定义占位符规则 JSON 文件；传入后不会读取当前游戏数据库规则",
    )
    _ = validate_placeholder_parser.add_argument(
        "--sample",
        action="append",
        default=[],
        help="用于预览替换和还原效果的原文片段，可重复传入",
    )

    quality_report_parser = subparsers.add_parser(
        "quality-report",
        help="生成当前游戏翻译质量报告",
    )
    add_optional_target_arguments(quality_report_parser)
    _ = quality_report_parser.add_argument("--output", help="写出 JSON 报告文件")
    _ = quality_report_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")

    export_pending_parser = subparsers.add_parser(
        "export-pending-translations",
        help="导出尚未成功入库的正文条目；不传 --limit 时导出全部",
    )
    add_optional_target_arguments(export_pending_parser)
    _ = export_pending_parser.add_argument("--output", required=True, help="人工补译 JSON 输出文件")
    _ = export_pending_parser.add_argument("--limit", type=int, help="最多导出的待补译条目数；省略则导出全部")
    _ = export_pending_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")

    export_untranslated_parser = subparsers.add_parser(
        "export-untranslated-translations",
        help="一键导出全部尚未成功入库的正文原文结构，供 Agent 填写 translation_lines",
    )
    add_optional_target_arguments(export_untranslated_parser)
    _ = export_untranslated_parser.add_argument("--output", required=True, help="全部未翻译正文 JSON 输出文件")
    _ = export_untranslated_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")

    export_quality_fix_parser = subparsers.add_parser(
        "export-quality-fix-template",
        help="根据 quality-report 的问题明细导出人工修复 JSON 骨架",
    )
    add_optional_target_arguments(export_quality_fix_parser)
    _ = export_quality_fix_parser.add_argument("--output", required=True, help="质量问题修复 JSON 输出文件")
    _ = export_quality_fix_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")

    import_manual_parser = subparsers.add_parser(
        "import-manual-translations",
        help="导入 Agent 人工补齐的正文译文，校验并按行宽规范化 long_text 后写入当前游戏数据库",
    )
    add_optional_target_arguments(import_manual_parser)
    _ = import_manual_parser.add_argument("--input", required=True, help="已填写的人工补译 JSON 文件")
    _ = import_manual_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")

    reset_translations_parser = subparsers.add_parser(
        "reset-translations",
        help="按 location_paths 清除已入库译文，让指定条目回到 pending 状态",
    )
    add_optional_target_arguments(reset_translations_parser)
    _ = reset_translations_parser.add_argument("--input", required=True, help='包含 {"location_paths": [...]} 的重置 JSON 文件')
    _ = reset_translations_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")

    validate_japanese_residual_parser = subparsers.add_parser(
        "validate-japanese-residual-rules",
        help="校验允许保留日文片段的例外规则 JSON",
    )
    add_optional_target_arguments(validate_japanese_residual_parser)
    validate_japanese_residual_source_group = validate_japanese_residual_parser.add_mutually_exclusive_group(required=True)
    _ = validate_japanese_residual_source_group.add_argument("--rules", help="日文残留例外规则 JSON 字符串")
    _ = validate_japanese_residual_source_group.add_argument("--input", help="日文残留例外规则 JSON 文件")
    _ = validate_japanese_residual_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")

    import_japanese_residual_parser = subparsers.add_parser(
        "import-japanese-residual-rules",
        help="导入允许保留日文片段的例外规则 JSON",
    )
    add_optional_target_arguments(import_japanese_residual_parser)
    import_japanese_residual_source_group = import_japanese_residual_parser.add_mutually_exclusive_group(required=True)
    _ = import_japanese_residual_source_group.add_argument("--rules", help="日文残留例外规则 JSON 字符串")
    _ = import_japanese_residual_source_group.add_argument("--input", help="日文残留例外规则 JSON 文件")
    _ = import_japanese_residual_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")

    translate_parser = subparsers.add_parser("translate", help="翻译指定游戏的正文")
    add_optional_target_arguments(translate_parser)
    _ = translate_parser.add_argument(
        "--placeholder-rules",
        help="本次翻译使用的自定义占位符规则 JSON 字符串；传入后不会读取当前游戏数据库规则",
    )
    _ = translate_parser.add_argument("--json", action="store_true", dest="json_output", help="输出本轮翻译摘要 JSON")
    add_translation_limit_arguments(translate_parser)
    add_setting_override_arguments(translate_parser)

    write_back_parser = subparsers.add_parser("write-back", help="把译文回写到游戏目录")
    add_optional_target_arguments(write_back_parser)
    _ = write_back_parser.add_argument("--json", action="store_true", dest="json_output", help="输出本轮回写摘要 JSON")
    add_setting_override_arguments(write_back_parser)

    export_name_parser = subparsers.add_parser(
        "export-name-context",
        help="导出 101 名字框和地图显示名上下文 JSON，供外部 Agent 填写译名",
    )
    add_optional_target_arguments(export_name_parser)
    _ = export_name_parser.add_argument(
        "--output-dir",
        required=True,
        help="临时导出目录；建议放在项目目录之外",
    )

    import_name_parser = subparsers.add_parser(
        "import-name-context",
        help="把外部 Agent 填写后的术语表大 JSON 导入游戏数据库",
    )
    add_optional_target_arguments(import_name_parser)
    _ = import_name_parser.add_argument("--input", required=True, help="已填写的大 JSON 路径")

    write_name_parser = subparsers.add_parser(
        "write-name-context",
        help="根据数据库中的术语表写回 101 名字框和 MapXXX.displayName",
    )
    add_optional_target_arguments(write_name_parser)
    add_setting_override_arguments(write_name_parser)

    run_all_parser = subparsers.add_parser("run-all", help="按固定顺序执行正文翻译和回写")
    add_optional_target_arguments(run_all_parser)
    _ = run_all_parser.add_argument(
        "--placeholder-rules",
        help="本次翻译使用的自定义占位符规则 JSON 字符串；传入后不会读取当前游戏数据库规则",
    )
    add_translation_limit_arguments(run_all_parser)
    _ = run_all_parser.add_argument("--skip-write-back", action="store_true", help="跳过最终回写阶段")
    add_setting_override_arguments(run_all_parser)

    build_placeholder_parser = subparsers.add_parser(
        "build-placeholder-rules",
        help="根据当前游戏候选控制符生成可编辑占位符规则草稿",
    )
    add_optional_target_arguments(build_placeholder_parser)
    _ = build_placeholder_parser.add_argument("--output", required=True, help="写出的规则草稿 JSON 文件")
    _ = build_placeholder_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")

    import_placeholder_parser = subparsers.add_parser(
        "import-placeholder-rules",
        help="把当前游戏专用占位符规则写入数据库",
    )
    add_optional_target_arguments(import_placeholder_parser)
    import_placeholder_source_group = import_placeholder_parser.add_mutually_exclusive_group(required=True)
    _ = import_placeholder_source_group.add_argument("--rules", help="占位符规则 JSON 字符串")
    _ = import_placeholder_source_group.add_argument("--input", help="占位符规则 JSON 文件")

    validate_plugin_parser = subparsers.add_parser(
        "validate-plugin-rules",
        help="校验插件文本规则 JSON",
    )
    add_optional_target_arguments(validate_plugin_parser)
    validate_plugin_source_group = validate_plugin_parser.add_mutually_exclusive_group(required=True)
    _ = validate_plugin_source_group.add_argument("--rules", help="插件规则 JSON 字符串")
    _ = validate_plugin_source_group.add_argument("--input", help="插件规则 JSON 文件")
    _ = validate_plugin_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")

    validate_event_parser = subparsers.add_parser(
        "validate-event-command-rules",
        help="校验事件指令文本规则 JSON",
    )
    add_optional_target_arguments(validate_event_parser)
    validate_event_source_group = validate_event_parser.add_mutually_exclusive_group(required=True)
    _ = validate_event_source_group.add_argument("--rules", help="事件指令规则 JSON 字符串")
    _ = validate_event_source_group.add_argument("--input", help="事件指令规则 JSON 文件")
    _ = validate_event_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")

    prepare_workspace_parser = subparsers.add_parser(
        "prepare-agent-workspace",
        help="一次性导出 Agent 分析所需的临时工作区",
    )
    add_optional_target_arguments(prepare_workspace_parser)
    _ = prepare_workspace_parser.add_argument("--output-dir", required=True, help="临时工作区输出目录")
    _ = prepare_workspace_parser.add_argument(
        "--code",
        action="extend",
        nargs="+",
        type=int,
        dest="codes",
        metavar="CODE",
        help="需要导出的事件指令编码数组；传入后覆盖配置文件默认编码数组",
    )
    _ = prepare_workspace_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")

    validate_workspace_parser = subparsers.add_parser(
        "validate-agent-workspace",
        help="校验 Agent 临时工作区产物是否可导入",
    )
    add_optional_target_arguments(validate_workspace_parser)
    _ = validate_workspace_parser.add_argument("--workspace", required=True, help="Agent 临时工作区目录")
    _ = validate_workspace_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")

    cleanup_workspace_parser = subparsers.add_parser(
        "cleanup-agent-workspace",
        help="按 manifest 清理 Agent 临时工作区产物",
    )
    _ = cleanup_workspace_parser.add_argument("--workspace", required=True, help="Agent 临时工作区目录")
    _ = cleanup_workspace_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")

    status_parser = subparsers.add_parser("translation-status", help="查看最新正文翻译运行状态")
    add_optional_target_arguments(status_parser)
    _ = status_parser.add_argument("--json", action="store_true", dest="json_output", help="输出机器可读 JSON")
    return parser


def add_optional_target_arguments(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    """给目标游戏命令增加标题或路径二选一参数。"""
    group = parser.add_mutually_exclusive_group(required=required)
    _ = group.add_argument("--game", help="目标游戏标题")
    _ = group.add_argument("--game-path", help="已注册目标游戏根目录")


def add_translation_limit_arguments(parser: argparse.ArgumentParser) -> None:
    """给翻译命令增加单次运行控制参数。"""
    group = parser.add_argument_group("运行控制")
    _ = group.add_argument("--max-items", type=int, help="本轮最多处理的待翻译条目数")
    _ = group.add_argument("--max-batches", type=int, help="本轮最多处理的模型批次数")
    _ = group.add_argument("--time-limit-seconds", type=int, help="本轮翻译最长运行秒数")
    _ = group.add_argument("--stop-on-error-rate", type=float, help="译文质量错误率达到该值时停止本轮")
    _ = group.add_argument("--stop-on-rate-limit-count", type=int, help="模型限流故障达到该次数时停止本轮")


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
        return await run_list_command(args)
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
    if command == "export-note-tag-candidates":
        return await run_export_note_tag_candidates_command(args)
    if command == "validate-note-tag-rules":
        return await run_validate_note_tag_rules_command(args)
    if command == "import-note-tag-rules":
        return await run_import_note_tag_rules_command(args)
    if command == "scan-placeholder-candidates":
        return await run_scan_placeholder_candidates_command(args)
    if command == "validate-placeholder-rules":
        return await run_validate_placeholder_rules_command(args)
    if command == "build-placeholder-rules":
        return await run_build_placeholder_rules_command(args)
    if command == "import-placeholder-rules":
        return await run_import_placeholder_rules_command(args)
    if command == "validate-plugin-rules":
        return await run_validate_plugin_rules_command(args)
    if command == "validate-event-command-rules":
        return await run_validate_event_command_rules_command(args)
    if command == "prepare-agent-workspace":
        return await run_prepare_agent_workspace_command(args)
    if command == "validate-agent-workspace":
        return await run_validate_agent_workspace_command(args)
    if command == "cleanup-agent-workspace":
        return await run_cleanup_agent_workspace_command(args)
    if command == "quality-report":
        return await run_quality_report_command(args)
    if command == "export-pending-translations":
        return await run_export_pending_translations_command(args)
    if command == "export-untranslated-translations":
        return await run_export_untranslated_translations_command(args)
    if command == "export-quality-fix-template":
        return await run_export_quality_fix_template_command(args)
    if command == "import-manual-translations":
        return await run_import_manual_translations_command(args)
    if command == "reset-translations":
        return await run_reset_translations_command(args)
    if command == "validate-japanese-residual-rules":
        return await run_validate_japanese_residual_rules_command(args)
    if command == "import-japanese-residual-rules":
        return await run_import_japanese_residual_rules_command(args)
    if command == "translation-status":
        return await run_translation_status_command(args)
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


async def run_import_plugin_rules_command(args: argparse.Namespace) -> int:
    """执行 `import-plugin-rules` 命令。"""
    game_title = await resolve_target_game_title(args)
    input_path = read_required_path_arg(args, "input")
    async with HandlerSession() as handler:
        _ = await handler.import_plugin_rules(game_title=game_title, input_path=input_path)
    return 0


async def run_doctor_command(args: argparse.Namespace) -> int:
    """执行 `doctor` 命令。"""
    game_title = await resolve_optional_target_game_title(args)
    check_llm = not read_bool_arg(args, "no_check_llm")
    service = AgentToolkitService()
    report = await service.doctor(game_title=game_title, check_llm=check_llm)
    write_report_outputs(report=report, args=args, title="环境诊断报告")
    return 1 if report.status == "error" else 0


async def run_export_plugins_json_command(args: argparse.Namespace) -> int:
    """执行 `export-plugins-json` 命令。"""
    game_title = await resolve_target_game_title(args)
    output_path = read_required_path_arg(args, "output")
    async with HandlerSession() as handler:
        _ = await handler.export_plugins_json(game_title=game_title, output_path=output_path)
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
    async with HandlerSession() as handler:
        _ = await handler.import_event_command_rules(game_title=game_title, input_path=input_path)
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
    async with HandlerSession() as handler:
        _ = await handler.import_placeholder_rules(game_title=game_title, rules_text=rules_text)
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


async def run_prepare_agent_workspace_command(args: argparse.Namespace) -> int:
    """执行 `prepare-agent-workspace` 命令。"""
    game_title = await resolve_target_game_title(args)
    output_dir = read_required_path_arg(args, "output_dir")
    command_codes = read_int_set_arg(args, "codes")
    service = AgentToolkitService()
    report = await service.prepare_agent_workspace(
        game_title=game_title,
        output_dir=output_dir,
        command_codes=command_codes,
    )
    write_report_outputs(report=report, args=args, title="Agent 工作区准备报告")
    return 1 if report.status == "error" else 0


async def run_validate_agent_workspace_command(args: argparse.Namespace) -> int:
    """执行 `validate-agent-workspace` 命令。"""
    game_title = await resolve_target_game_title(args)
    workspace = read_required_path_arg(args, "workspace")
    service = AgentToolkitService()
    report = await service.validate_agent_workspace(game_title=game_title, workspace=workspace)
    write_report_outputs(report=report, args=args, title="Agent 工作区校验报告")
    return 1 if report.status == "error" else 0


async def run_cleanup_agent_workspace_command(args: argparse.Namespace) -> int:
    """执行 `cleanup-agent-workspace` 命令。"""
    workspace = read_required_path_arg(args, "workspace")
    service = AgentToolkitService()
    report = await service.cleanup_agent_workspace(workspace=workspace)
    write_report_outputs(report=report, args=args, title="Agent 工作区清理报告")
    return 1 if report.status == "error" else 0


async def run_quality_report_command(args: argparse.Namespace) -> int:
    """执行 `quality-report` 命令。"""
    game_title = await resolve_target_game_title(args)
    service = AgentToolkitService()
    report = await service.quality_report(game_title=game_title)
    write_report_outputs(report=report, args=args, title="翻译质量报告")
    return 1 if report.status == "error" else 0


async def run_export_pending_translations_command(args: argparse.Namespace) -> int:
    """执行 `export-pending-translations` 命令。"""
    game_title = await resolve_target_game_title(args)
    output_path = read_required_path_arg(args, "output")
    limit = read_optional_int_arg(args, "limit")
    service = AgentToolkitService()
    report = await service.export_pending_translations(
        game_title=game_title,
        output_path=output_path,
        limit=limit,
    )
    write_report_outputs(report=report, args=args, title="人工补译导出报告", write_output_file=False)
    return 1 if report.status == "error" else 0


async def run_export_untranslated_translations_command(args: argparse.Namespace) -> int:
    """执行 `export-untranslated-translations` 命令。"""
    game_title = await resolve_target_game_title(args)
    output_path = read_required_path_arg(args, "output")
    service = AgentToolkitService()
    report = await service.export_pending_translations(
        game_title=game_title,
        output_path=output_path,
        limit=None,
    )
    write_report_outputs(report=report, args=args, title="全部未翻译正文导出报告", write_output_file=False)
    return 1 if report.status == "error" else 0


async def run_export_quality_fix_template_command(args: argparse.Namespace) -> int:
    """执行 `export-quality-fix-template` 命令。"""
    game_title = await resolve_target_game_title(args)
    output_path = read_required_path_arg(args, "output")
    service = AgentToolkitService()
    report = await service.export_quality_fix_template(
        game_title=game_title,
        output_path=output_path,
    )
    write_report_outputs(report=report, args=args, title="质量修复模板导出报告", write_output_file=False)
    return 1 if report.status == "error" else 0


async def run_import_manual_translations_command(args: argparse.Namespace) -> int:
    """执行 `import-manual-translations` 命令。"""
    game_title = await resolve_target_game_title(args)
    input_path = read_required_path_arg(args, "input")
    service = AgentToolkitService()
    report = await service.import_manual_translations(game_title=game_title, input_path=input_path)
    write_report_outputs(report=report, args=args, title="人工补译导入报告")
    return 1 if report.status == "error" else 0


async def run_reset_translations_command(args: argparse.Namespace) -> int:
    """执行 `reset-translations` 命令。"""
    game_title = await resolve_target_game_title(args)
    input_path = read_required_path_arg(args, "input")
    service = AgentToolkitService()
    report = await service.reset_translations(game_title=game_title, input_path=input_path)
    write_report_outputs(report=report, args=args, title="译文重置报告")
    return 1 if report.status == "error" else 0


async def run_validate_japanese_residual_rules_command(args: argparse.Namespace) -> int:
    """执行 `validate-japanese-residual-rules` 命令。"""
    game_title = await resolve_target_game_title(args)
    rules_text = await read_required_text_source_arg(args, "rules", "input")
    service = AgentToolkitService()
    report = await service.validate_japanese_residual_rules(game_title=game_title, rules_text=rules_text)
    write_report_outputs(report=report, args=args, title="日文残留例外规则校验报告")
    return 1 if report.status == "error" else 0


async def run_import_japanese_residual_rules_command(args: argparse.Namespace) -> int:
    """执行 `import-japanese-residual-rules` 命令。"""
    game_title = await resolve_target_game_title(args)
    rules_text = await read_required_text_source_arg(args, "rules", "input")
    service = AgentToolkitService()
    report = await service.import_japanese_residual_rules(game_title=game_title, rules_text=rules_text)
    write_report_outputs(report=report, args=args, title="日文残留例外规则导入报告")
    return 1 if report.status == "error" else 0


async def run_translation_status_command(args: argparse.Namespace) -> int:
    """执行 `translation-status` 命令。"""
    game_title = await resolve_target_game_title(args)
    service = AgentToolkitService()
    report = await service.translation_status(game_title=game_title)
    write_report_outputs(report=report, args=args, title="正文翻译状态")
    return 1 if report.status == "error" else 0


async def run_translate_command(args: argparse.Namespace) -> int:
    """执行 `translate` 命令。"""
    game_title = await resolve_target_game_title(args)
    placeholder_rules_text = read_optional_str_arg(args, "placeholder_rules")
    setting_overrides = build_setting_overrides(args)
    async with HandlerSession() as handler:
        summary = await translate_text_for_handler(
            handler=handler,
            game_title=game_title,
            setting_overrides=setting_overrides,
            placeholder_rules_text=placeholder_rules_text,
            run_limits=build_translation_run_limits(args),
            args=args,
        )
    ensure_text_translation_not_blocked(summary)
    if read_bool_arg(args, "json_output"):
        report = build_translate_summary_report(summary)
        print(report.to_json_text())
    return 0


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


async def run_export_name_context_command(args: argparse.Namespace) -> int:
    """执行 `export-name-context` 命令。"""
    game_title = await resolve_target_game_title(args)
    output_dir = read_required_path_arg(args, "output_dir")
    async with HandlerSession() as handler:
        summary = await handler.export_name_context(game_title=game_title, output_dir=output_dir)
    logger.success(f"[tag.success]标准名上下文可交给外部 Agent 处理[/tag.success] 大 JSON [tag.path]{summary.registry_path}[/tag.path] 小 JSON 目录 [tag.path]{summary.sample_dir}[/tag.path]")
    return 0


async def run_import_name_context_command(args: argparse.Namespace) -> int:
    """执行 `import-name-context` 命令。"""
    game_title = await resolve_target_game_title(args)
    input_path = read_required_path_arg(args, "input")
    async with HandlerSession() as handler:
        _ = await handler.import_name_context(game_title=game_title, input_path=input_path)
    return 0


async def run_write_name_context_command(args: argparse.Namespace) -> int:
    """执行 `write-name-context` 命令。"""
    game_title = await resolve_target_game_title(args)
    setting_overrides = build_setting_overrides(args)
    async with HandlerSession() as handler:
        with build_progress_reporter("标准名写回", args) as progress:
            _ = await handler.write_name_context(
                game_title=game_title,
                callbacks=progress.progress_callbacks(),
                setting_overrides=setting_overrides,
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


async def translate_text_for_handler(
    *,
    handler: TranslationHandler,
    game_title: str,
    setting_overrides: SettingOverrides,
    placeholder_rules_text: str | None,
    run_limits: TranslationRunLimits,
    args: argparse.Namespace,
) -> TextTranslationSummary:
    """使用已创建的编排器翻译正文。"""
    with build_progress_reporter("正文翻译", args) as progress:
        return await handler.translate_text(
            game_title=game_title,
            setting_overrides=setting_overrides,
            custom_placeholder_rules_text=placeholder_rules_text,
            run_limits=run_limits,
            callbacks=progress.status_callbacks(),
        )


async def write_back_for_handler(
    *,
    handler: TranslationHandler,
    game_title: str,
    setting_overrides: SettingOverrides,
    args: argparse.Namespace,
) -> WriteBackSummary:
    """使用已创建的编排器回写译文。"""
    await ensure_write_back_gate(game_title)
    with build_progress_reporter("回写数据", args) as progress:
        return await handler.write_back(
            game_title=game_title,
            callbacks=progress.progress_callbacks(),
            setting_overrides=setting_overrides,
        )


async def ensure_write_back_gate(game_title: str) -> None:
    """回写前执行质量门禁，避免把部分失败结果写入游戏。"""
    report = await AgentToolkitService().quality_report(game_title=game_title)
    if report.status != "error":
        return
    messages = "；".join(error.message for error in report.errors)
    raise CliBusinessError(f"回写门禁未通过：{messages}")


def ensure_text_translation_success(summary: TextTranslationSummary) -> None:
    """校验正文翻译摘要是否允许流水线继续。"""
    if summary.is_blocked:
        raise CliBusinessError(f"正文翻译被阻断：{summary.blocked_reason}")
    if summary.has_errors:
        raise CliBusinessError(f"正文翻译产生错误条目，已停止后续流程：成功 {summary.success_count} 条，失败 {summary.error_count} 条")


def ensure_text_translation_not_blocked(summary: TextTranslationSummary) -> None:
    """校验单独翻译命令是否遇到阻断级故障。"""
    if summary.is_blocked:
        raise CliBusinessError(f"正文翻译被阻断：{summary.blocked_reason}")
    if summary.has_errors:
        logger.warning(
            f"[tag.warning]正文翻译存在待处理失败项[/tag.warning] 成功 [tag.count]{summary.success_count}[/tag.count] 条，失败 [tag.count]{summary.error_count}[/tag.count] 条；可继续运行 translate 或使用质量报告排查"
        )


def build_translate_summary_report(summary: TextTranslationSummary) -> AgentReport:
    """把正文翻译摘要转换为稳定 JSON 报告。"""
    warnings: list[AgentIssue] = []
    if summary.has_errors:
        warnings.append(
            issue(
                "translation_quality_errors",
                f"本轮翻译产生 {summary.error_count} 条译文质量错误，可续跑或人工补译",
            )
        )
    return AgentReport.from_parts(
        errors=[],
        warnings=warnings,
        summary={
            "run_id": summary.run_id,
            "total_extracted_items": summary.total_extracted_items,
            "pending_count": summary.pending_count,
            "deduplicated_count": summary.deduplicated_count,
            "batch_count": summary.batch_count,
            "success_count": summary.success_count,
            "quality_error_count": summary.error_count,
            "llm_failure_count": summary.llm_failure_count,
        },
        details={},
    )


def build_write_back_summary_report(summary: WriteBackSummary) -> AgentReport:
    """把游戏文件回写摘要转换为稳定 JSON 报告。"""
    return AgentReport.from_parts(
        errors=[],
        warnings=[],
        summary={
            "data_item_count": summary.data_item_count,
            "plugin_item_count": summary.plugin_item_count,
            "name_written_count": summary.name_written_count,
            "target_font_name": summary.target_font_name or "",
            "source_font_count": summary.source_font_count,
            "replaced_font_reference_count": summary.replaced_font_reference_count,
            "font_copied": summary.font_copied,
        },
        details={},
    )


async def resolve_target_game_title(args: argparse.Namespace) -> str:
    """从 `--game` 或 `--game-path` 解析当前命令目标游戏标题。"""
    game_title = read_optional_str_arg(args, "game")
    if game_title is not None:
        return game_title
    game_path = read_optional_path_arg(args, "game_path")
    if game_path is not None:
        return await GameRegistry().resolve_registered_title_by_path(game_path)
    raise CliBusinessError("命令必须提供 --game 或 --game-path")


async def resolve_optional_target_game_title(args: argparse.Namespace) -> str | None:
    """解析可选目标游戏标题。"""
    game_title = read_optional_str_arg(args, "game")
    if game_title is not None:
        return game_title
    game_path = read_optional_path_arg(args, "game_path")
    if game_path is not None:
        return await GameRegistry().resolve_registered_title_by_path(game_path)
    return None


def build_translation_run_limits(args: argparse.Namespace) -> TranslationRunLimits:
    """从 CLI 参数构建单次翻译运行限制。"""
    max_items = read_optional_positive_int_arg(args, "max_items")
    max_batches = read_optional_positive_int_arg(args, "max_batches")
    time_limit_seconds = read_optional_positive_int_arg(args, "time_limit_seconds")
    stop_on_error_rate = read_optional_rate_arg(args, "stop_on_error_rate")
    stop_on_rate_limit_count = read_optional_positive_int_arg(args, "stop_on_rate_limit_count")
    return TranslationRunLimits(
        max_items=max_items,
        max_batches=max_batches,
        time_limit_seconds=time_limit_seconds,
        stop_on_error_rate=stop_on_error_rate,
        stop_on_rate_limit_count=stop_on_rate_limit_count,
    )


def write_report_outputs(
    *,
    report: AgentReport,
    args: argparse.Namespace,
    title: str,
    write_output_file: bool = True,
) -> None:
    """按用户参数输出 Agent 工具包报告。"""
    output_path = read_optional_path_arg(args, "output") if write_output_file else None
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


async def read_optional_text_source_arg(
    args: argparse.Namespace,
    text_arg_name: str,
    input_arg_name: str,
) -> str | None:
    """从命令行 JSON 字符串或 JSON 文件读取可选文本。"""
    text_value = read_optional_str_arg(args, text_arg_name)
    input_path = read_optional_path_arg(args, input_arg_name)
    if text_value is not None:
        return text_value
    if input_path is None:
        return None
    return await read_text_file(input_path)


async def read_required_text_source_arg(
    args: argparse.Namespace,
    text_arg_name: str,
    input_arg_name: str,
) -> str:
    """从命令行 JSON 字符串或 JSON 文件读取必填文本。"""
    text_value = await read_optional_text_source_arg(args, text_arg_name, input_arg_name)
    if text_value is None:
        raise CliBusinessError(f"命令参数必须提供 {text_arg_name} 或 {input_arg_name}")
    if not text_value.strip():
        raise CliBusinessError(f"命令参数 {text_arg_name} 或 {input_arg_name} 内容为空")
    return text_value


async def read_text_file(path: Path) -> str:
    """以 UTF-8 读取命令行输入文件。"""
    try:
        async with aiofiles.open(path, "r", encoding="utf-8-sig") as file:
            return await file.read()
    except OSError as error:
        raise CliBusinessError(f"读取输入文件失败：{path}") from error


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


def read_optional_positive_int_arg(args: argparse.Namespace, name: str) -> int | None:
    """从命名空间读取可选正整数参数。"""
    value = read_optional_int_arg(args, name)
    if value is None:
        return None
    if value <= 0:
        raise CliBusinessError(f"命令参数必须是正整数：{name}")
    return value


def read_optional_float_arg(args: argparse.Namespace, name: str) -> float | None:
    """从命名空间读取可选浮点数参数。"""
    raw_value = read_namespace_value(args, name)
    if raw_value is None:
        return None
    if isinstance(raw_value, bool) or not isinstance(raw_value, float):
        raise CliBusinessError(f"命令参数不是数字：{name}")
    return raw_value


def read_optional_rate_arg(args: argparse.Namespace, name: str) -> float | None:
    """读取 0 到 1 之间的比例参数。"""
    value = read_optional_float_arg(args, name)
    if value is None:
        return None
    if value <= 0 or value > 1:
        raise CliBusinessError(f"命令参数必须大于 0 且小于等于 1：{name}")
    return value


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


SENSITIVE_OR_VERBOSE_ARGUMENTS = {
    "placeholder_rules",
    "rules",
    "system_prompt",
}


def format_namespace(args: argparse.Namespace) -> str:
    """把命令参数格式化为适合日志记录的摘要。"""
    namespace = cast(dict[str, object], vars(args))
    return ", ".join(
        f"{key}={format_log_argument_value(key=key, value=value)}"
        for key, value in sorted(namespace.items())
    )


def read_namespace_value(args: argparse.Namespace, name: str) -> object:
    """从 argparse 命名空间读取原始对象，并在入口处收窄动态类型。"""
    namespace = cast(dict[str, object], vars(args))
    return namespace.get(name)


def format_argv(argv: Sequence[str]) -> str:
    """格式化原始命令行参数。"""
    if not argv:
        return "<空>"
    redacted_items: list[str] = []
    skip_next = False
    for item in argv:
        if skip_next:
            redacted_items.append("<已省略>")
            skip_next = False
            continue
        if item.startswith("--"):
            option_body = item.removeprefix("--")
            if "=" in option_body:
                option_name, _option_value = option_body.split("=", 1)
                if option_name.replace("-", "_") in SENSITIVE_OR_VERBOSE_ARGUMENTS:
                    redacted_items.append(f"--{option_name}=<已省略>")
                    continue
            option_name = option_body.replace("-", "_")
            if option_name in SENSITIVE_OR_VERBOSE_ARGUMENTS:
                redacted_items.append(item)
                skip_next = True
                continue
        redacted_items.append(item)
    return " ".join(redacted_items)


def format_log_argument_value(*, key: str, value: object) -> object:
    """隐藏不适合写入运行首行日志的大段参数。"""
    if key in SENSITIVE_OR_VERBOSE_ARGUMENTS and value is not None:
        return "<已省略>"
    return value


__all__: list[str] = [
    "CliArgumentError",
    "CliBusinessError",
    "build_parser",
    "dispatch_command",
    "format_argv",
    "format_namespace",
]
