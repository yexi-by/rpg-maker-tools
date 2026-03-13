"""
项目启动入口。

本模块统一管理 CLI 与 TUI 两种启动方式。
当前默认启动 Textual 工作台，便于直接进入图形化终端界面。
"""


import argparse
from collections.abc import Sequence

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
