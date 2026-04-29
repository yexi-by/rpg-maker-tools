"""日志系统表现层测试。"""

from pathlib import Path

from app.observability import logger, setup_logger


def test_file_log_keeps_debug_and_exception_traceback(tmp_path: Path) -> None:
    """文件日志保留 DEBUG 与完整异常链，供排障复盘使用。"""
    log_path = tmp_path / "app.log"
    setup_logger(level="INFO", use_console=False, file_path=log_path, enqueue_file_log=False)

    logger.debug("[tag.phase]调试细节[/tag.phase] 只应写入文件")
    try:
        raise RuntimeError("模拟未知异常")
    except RuntimeError:
        logger.bind(file_only=True).exception("[tag.exception]未知异常[/tag.exception]")

    content = log_path.read_text(encoding="utf-8")
    assert "调试细节" in content
    assert "Traceback" in content
    assert "RuntimeError: 模拟未知异常" in content
