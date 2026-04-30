"""
日志与进度条工具模块。

本模块统一封装 Loguru、Rich 与标准库 logging 的桥接逻辑，
为项目提供可重配的控制台输出、文件日志和进度条能力。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, cast, override

from loguru import logger
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.text import Text
from rich.theme import Theme

if TYPE_CHECKING:
    from loguru import Record

# --- 配置常量 ---
LOG_LEVEL = "INFO"
THIRD_PARTY_LOG_LEVEL = "WARNING"
DATE_FORMAT = "[%X]"
FILE_LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)
CONSOLE_LOG_FORMAT = "{message}"

# --- 文件日志配置 ---
ENABLE_FILE_LOG = True
LOG_FILE_PATH = "logs/app.log"
LOG_FILE_LEVEL = "DEBUG"
LOG_ROTATION = "10 MB"
LOG_RETENTION = "1 week"
LOG_COMPRESSION = "zip"

CUSTOM_THEME = Theme(
    {
        "logging.level.debug": "dim",
        "logging.level.info": "cyan",
        "logging.level.warning": "bold yellow",
        "logging.level.error": "bold red",
        "logging.level.critical": "bold white on red",
        "logging.level.success": "bold green",
        "logging.keyword": "bold cyan",
        "log.time": "dim",
        "log.path": "blue",
        "tag.phase": "bold cyan",
        "tag.count": "bold magenta",
        "tag.path": "bold blue",
        "tag.skip": "yellow",
        "tag.success": "bold green",
        "tag.warning": "bold yellow",
        "tag.failure": "bold red",
        "tag.exception": "bold white on red",
        "tag.menu.title": "bold cyan",
        "tag.menu.index": "bold magenta",
        "tag.menu.prompt": "bold green",
    }
)

NOISY_MODULES = [
    "httpcore",
    "httpx",
    "openai",
    "urllib3",
    "aiosqlite",
]

console = Console(theme=CUSTOM_THEME)


class ProjectRichHandler(RichHandler):
    """解析项目 Rich markup，并避免默认高亮覆盖自定义样式。"""

    @override
    def render_message(self, record: logging.LogRecord, message: str) -> Text:
        """只按显式 markup 着色消息体，不根据日志级别自动染色正文。"""
        use_markup = getattr(record, "markup", self.markup)
        message_text = Text.from_markup(message) if use_markup else Text(message)

        keywords = self.keywords or self.KEYWORDS
        if keywords:
            _ = message_text.highlight_words(keywords, "logging.keyword")

        return message_text


class InterceptHandler(logging.Handler):
    """
    拦截标准库 `logging` 日志，并转发给 Loguru。
    """

    @override
    def emit(self, record: logging.LogRecord) -> None:
        """处理单条日志记录，并将其桥接到 Loguru。"""
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level,
            record.getMessage(),
        )


def should_show_in_console(record: Record) -> bool:
    """
    判断单条日志是否应该出现在终端。

    Args:
        record: Loguru 传入的日志记录。

    Returns:
        `True` 表示允许输出到终端。
    """
    extra = cast(dict[str, object], record["extra"])
    return not bool(extra.get("file_only", False))


def build_console_sink_format(_record: Record) -> str:
    """为终端 sink 构造轻量格式，避免默认视图暴露模块路径噪音。"""
    return CONSOLE_LOG_FORMAT


def build_file_sink_format(record: Record) -> str:
    """
    为 Loguru sink 构造格式字符串。

    为什么使用可调用格式器：
    只有在当前记录确实携带异常时，才显式追加 `{exception}`，
    这样既能记录 traceback，又能保持普通日志紧凑。

    Args:
        record: Loguru 传入的单条日志记录字典。

    Returns:
        当前日志记录对应的格式字符串。
    """
    if record["exception"] is None:
        return FILE_LOG_FORMAT
    return f"{FILE_LOG_FORMAT}\n{{exception}}"


def setup_logger(
    level: str = LOG_LEVEL,
    *,
    use_console: bool = True,
    file_path: str | Path = LOG_FILE_PATH,
    enqueue_file_log: bool = True,
) -> None:
    """
    配置并初始化全局日志系统。

    CLI 默认启用 Rich 控制台输出，同时始终保留文件日志。

    Args:
        level: 控制台 sink 的最低日志级别。
        use_console: 是否启用 Rich 控制台输出。
        file_path: 文件日志路径，测试可传入临时路径避免污染真实日志。
        enqueue_file_log: 是否启用异步文件写入队列。
    """
    _ = logger.remove()

    if use_console:
        _ = logger.add(
            ProjectRichHandler(
                console=console,
                show_time=True,
                show_path=False,
                omit_repeated_times=False,
                rich_tracebacks=True,
                tracebacks_show_locals=True,
                markup=True,
                keywords=[
                    "GET",
                    "POST",
                    "HEAD",
                    "PUT",
                    "DELETE",
                    "OPTIONS",
                    "PATCH",
                ],
            ),
            level=level,
            format=build_console_sink_format,
            filter=should_show_in_console,
            catch=True,
        )

    if ENABLE_FILE_LOG:
        _ = logger.add(
            file_path,
            level=LOG_FILE_LEVEL,
            format=build_file_sink_format,
            rotation=LOG_ROTATION,
            retention=LOG_RETENTION,
            compression=LOG_COMPRESSION,
            enqueue=enqueue_file_log,
            encoding="utf-8",
            backtrace=True,
            diagnose=True,
        )

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    for module_name in NOISY_MODULES:
        logging.getLogger(module_name).setLevel(logging.WARNING)


def get_progress(transient: bool = False, indeterminate: bool = False) -> Progress:
    """
    获取一个绑定了全局 console 的进度条实例。

    Args:
        transient: 完成后是否自动清除进度条。
        indeterminate: 是否使用不确定进度样式。
    """
    if indeterminate:
        status_column = MofNCompleteColumn()
    else:
        status_column = TextColumn("[progress.percentage]{task.percentage:>3.0f}%")

    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=40),
        status_column,
        TimeElapsedColumn(),
        console=console,
        transient=transient,
    )


setup_logger()

__all__ = [
    "LOG_FILE_PATH",
    "build_console_sink_format",
    "build_file_sink_format",
    "console",
    "get_progress",
    "logger",
    "setup_logger",
]
