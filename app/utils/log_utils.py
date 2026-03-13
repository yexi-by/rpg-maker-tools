"""
日志与进度条工具模块。

本模块统一封装 Loguru、Rich 与标准库 logging 的桥接逻辑，
为项目提供可重配的控制台输出、文件日志以及界面日志回调能力。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

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
    from collections.abc import Callable

    from loguru import Message

# --- 配置常量 ---
LOG_LEVEL = "DEBUG"
THIRD_PARTY_LOG_LEVEL = "WARNING"
DATE_FORMAT = "[%X]"
LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)

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
    "volcengine",
    "requests",
    "aiosqlite",
]

console = Console(theme=CUSTOM_THEME)


@dataclass(slots=True)
class LogLine:
    """
    发往界面层的结构化日志对象。

    Attributes:
        timestamp: 日志时间戳字符串。
        level: 日志级别名称。
        message: 原始日志消息，保留 Rich markup 标记。
        plain_text: 去除 Rich markup 后的纯文本展示内容。
    """

    timestamp: str
    level: str
    message: str
    plain_text: str


class ProjectRichHandler(RichHandler):
    """解析项目 Rich markup，并避免默认高亮覆盖自定义样式。"""

    def render_message(self, record: logging.LogRecord, message: str) -> Text:
        """只按显式 markup 着色消息体，不根据日志级别自动染色正文。"""
        use_markup = getattr(record, "markup", self.markup)
        message_text = Text.from_markup(message) if use_markup else Text(message)

        if self.keywords is None:
            self.keywords = self.KEYWORDS

        if self.keywords:
            message_text.highlight_words(self.keywords, "logging.keyword")

        return message_text


class InterceptHandler(logging.Handler):
    """
    拦截标准库 `logging` 日志，并转发给 Loguru。
    """

    def emit(self, record: logging.LogRecord) -> None:
        """处理单条日志记录，并将其桥接到 Loguru。"""
        if record.name.startswith("volcengine") and record.levelno < logging.WARNING:
            return

        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


class UILogSink:
    """
    将 Loguru 消息转发给界面层回调。

    回调层只消费结构化 `LogLine`，从而避免直接耦合 Rich 控制台渲染细节。
    """

    def __init__(self, callbacks: tuple[Callable[[LogLine], None], ...]) -> None:
        """
        初始化界面日志 sink。

        Args:
            callbacks: 接收结构化日志对象的同步回调元组。
        """
        self.callbacks = callbacks

    def __call__(self, message: Message) -> None:
        """
        处理单条 Loguru 消息并分发到全部界面回调。

        Args:
            message: Loguru 传入的消息对象。
        """
        log_line = build_log_line(message)
        for callback in self.callbacks:
            try:
                callback(log_line)
            except Exception:
                continue


def strip_markup(message: str) -> str:
    """
    去除 Rich markup 标记，返回纯文本消息。

    Args:
        message: 原始日志消息。

    Returns:
        去掉样式标签后的纯文本消息。
    """
    try:
        return Text.from_markup(message).plain
    except Exception:
        return message


def build_log_line(message: Message) -> LogLine:
    """
    把 Loguru 消息对象转换为结构化日志对象。

    Args:
        message: Loguru sink 传入的消息对象。

    Returns:
        供界面层消费的 `LogLine`。
    """
    record = message.record
    timestamp = record["time"].strftime("%Y-%m-%d %H:%M:%S")
    level = record["level"].name
    raw_message = record["message"]
    plain_message = strip_markup(raw_message)
    plain_text = f"[{timestamp}] {level:<8} {plain_message}"
    return LogLine(
        timestamp=timestamp,
        level=level,
        message=raw_message,
        plain_text=plain_text,
    )


def setup_logger(
    level: str = LOG_LEVEL,
    *,
    use_console: bool = True,
    ui_log_callbacks: tuple[Callable[[LogLine], None], ...] = (),
) -> None:
    """
    配置并初始化全局日志系统。

    该函数支持按入口重新配置日志输出目标。
    普通 CLI 可启用 Rich 控制台输出；Textual 界面可关闭控制台输出，
    改由界面回调接收结构化日志。

    Args:
        level: 控制台与界面 sink 的最低日志级别。
        use_console: 是否启用 Rich 控制台输出。
        ui_log_callbacks: 接收结构化日志对象的同步回调元组。
    """
    logger.remove()

    if use_console:
        logger.add(
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
            format="{message}",
            catch=True,
        )

    if ENABLE_FILE_LOG:
        logger.add(
            LOG_FILE_PATH,
            level=LOG_FILE_LEVEL,
            format=LOG_FORMAT,
            rotation=LOG_ROTATION,
            retention=LOG_RETENTION,
            compression=LOG_COMPRESSION,
            enqueue=True,
            encoding="utf-8",
            backtrace=True,
            diagnose=True,
        )

    if ui_log_callbacks:
        logger.add(
            UILogSink(ui_log_callbacks),
            level=level,
            format="{message}",
            catch=True,
        )

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    for module_name in NOISY_MODULES:
        logging.getLogger(module_name).setLevel(logging.WARNING)


def get_progress(transient: bool = False, indeterminate: bool = False) -> Progress:
    """
    获取一个绑定了全局 console 的进度条实例。

    该函数保留给旧 CLI 兼容场景使用；Textual 主路径不再依赖它。

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
    "LogLine",
    "build_log_line",
    "console",
    "get_progress",
    "logger",
    "setup_logger",
    "strip_markup",
]
