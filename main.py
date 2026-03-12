"""命令行入口。"""

from app.utils import logger
from cli import run_cli


def main() -> None:
    """启动交互式命令行。"""
    try:
        run_cli()
    except Exception:
        logger.exception("[tag.exception]命令行启动失败[/tag.exception]")
        raise


if __name__ == "__main__":
    main()
