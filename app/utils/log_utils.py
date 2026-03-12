"""
日志与进度条工具模块。
统一封装 Loguru、Rich 与标准库 logging 的桥接逻辑，为项目提供风格一致的日志输出与进度条展示能力。
"""

import logging

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

# --- 配置常量 ---
# 日志级别
LOG_LEVEL = "DEBUG"
# 第三方库的默认日志级别
THIRD_PARTY_LOG_LEVEL = "WARNING"
# 日志时间格式
DATE_FORMAT = "[%X]"
# Loguru 格式 (当不使用 RichHandler 时备用，或者用于文件输出)
# 注意：RichHandler 自带了格式化，这里主要用于配置 handler 的 formatter 参数或者文件日志
LOG_FORMAT = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"

# --- 文件日志配置 ---
ENABLE_FILE_LOG = True
LOG_FILE_PATH = "logs/app.log"
LOG_FILE_LEVEL = "DEBUG"  # 文件通常记录更详细的日志
LOG_ROTATION = "10 MB"  # 单个文件最大 10MB，或者 "1 week", "00:00" 等
LOG_RETENTION = "1 week"  # 保留一周的日志
LOG_COMPRESSION = "zip"  # 压缩旧日志
# 自定义主题：统一 RichHandler 级别列与消息内 markup 的配色
CUSTOM_THEME = Theme(
    {
        # RichHandler 的级别列会读取 logging.level.<level_name> 样式。
        # Rich 默认未覆盖 SUCCESS，这里显式补齐并顺手统一其他级别的观感。
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

# --- 全局单例 ---
# 所有的日志输出和进度条都应该共享这一个 Console 实例
# 这样才能保证进度条不会被日志输出打断/顶掉
console = Console(theme=CUSTOM_THEME)


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
        # 双重保险：过滤掉嘈杂模块的低级别日志
        # 有些库可能在 setup_logger 之后重置了日志级别，或者使用了子 logger
        if record.name.startswith("volcengine") and record.levelno < logging.WARNING:
            return

        # 获取对应的 Loguru 日志级别
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # 查找调用者的帧，以便 Loguru 能正确记录源文件和行号
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def setup_logger(level: str = LOG_LEVEL) -> None:
    """
    配置并初始化全局日志系统。
    
    该函数将整合 Loguru、Rich 控制台渲染与标准库 logging，
    确保项目中所有的日志输出（包括第三方库的日志）都能以统一、带色彩的格式
    输出到终端，并异步记录到文件。

    Args:
        level: 控制台输出的最低日志级别。
    """
    # 步骤 1: 移除 Loguru 默认自带的终端输出 handler
    logger.remove()

    # 步骤 2: 添加绑定了全局 Rich Console 的 RichHandler。
    # markup=True 允许在日志消息中使用 rich 的样式标记（如 [red]...[/red]）。
    # rich_tracebacks=True 提供带有局部变量和代码高亮的异常堆栈，极大方便调试。
    logger.add(
        ProjectRichHandler(
            console=console,
            show_time=True,
            show_path=False,  # 隐藏冗长的路径，保持简洁
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
            ],  # 高亮 HTTP 动词
        ),
        level=level,
        format="{message}",  # RichHandler 会自动处理时间、级别等，这里只需要 message
        catch=True,
    )

    # 添加文件日志
    if ENABLE_FILE_LOG:
        logger.add(
            LOG_FILE_PATH,
            level=LOG_FILE_LEVEL,
            format=LOG_FORMAT,
            rotation=LOG_ROTATION,
            retention=LOG_RETENTION,
            compression=LOG_COMPRESSION,
            enqueue=True,  # 异步写入
            encoding="utf-8",
            backtrace=True,
            diagnose=True,
        )

    # 拦截标准库 logging
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    for module_name in NOISY_MODULES:
        logging.getLogger(module_name).setLevel(logging.WARNING)


def get_progress(transient: bool = False, indeterminate: bool = False) -> Progress:
    """
    获取一个绑定了全局 console 的进度条实例

    Args:
        transient: 完成后是否自动清除进度条，默认为 True (保持界面整洁)
        indeterminate: 是否使用不确定进度样式（显示 M/N 完成数而非百分比），默认为 False
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


# 默认执行初始化，这样导入模块时就配置好了
setup_logger()

# 导出对象
__all__ = ["logger", "console", "get_progress", "setup_logger"]
