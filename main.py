"""
项目启动入口。

本模块统一管理 CLI 与 TUI 两种启动方式。
当前默认启动 Textual 工作台，便于直接进入图形化终端界面。
"""


import argparse
import warnings
from collections.abc import Sequence


def _suppress_known_third_party_warnings() -> None:
    """
    屏蔽已确认不影响当前项目运行的第三方已知警告。

    这里不能粗暴关闭全部 `UserWarning`，否则会把真正有价值的运行时提示一起吞掉。
    当前只精确屏蔽 `volcenginesdkarkruntime` 在 Python 3.14 下发出的那一条
    兼容性提示，避免每次启动 TUI 都污染终端输出。
    """
    warnings.filterwarnings(
        action="ignore",
        message=(
            r"Core Pydantic V1 functionality isn't compatible with Python 3\.14 "
            r"or greater\."
        ),
        category=UserWarning,
        module=r"volcenginesdkarkruntime\._compat",
    )


_suppress_known_third_party_warnings()

from app.tui import TranslationWorkbenchApp
from app.utils import logger


def build_parser() -> argparse.ArgumentParser:
    """
    构建命令行参数解析器。

    Returns:
        项目启动参数解析器。
    """
    parser = argparse.ArgumentParser(description="RPG Maker 翻译工具启动入口")
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("tui", "cli"),
        default="tui",
        help="启动模式，默认值为 tui",
    )
    return parser


def run_tui() -> None:
    """
    启动 Textual 工作台。
    """
    TranslationWorkbenchApp().run()


def main(argv: Sequence[str] | None = None) -> None:
    """
    根据启动参数选择运行模式。

    Args:
        argv: 可选的命令行参数序列。
    """
    args = build_parser().parse_args(argv)

    try:
        run_tui()
    except Exception:
        logger.exception("[tag.exception]程序启动失败[/tag.exception]")
        raise


if __name__ == "__main__":
    main()
