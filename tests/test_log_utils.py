import logging
import unittest

from rich.console import Console

from app.utils.log_utils import CUSTOM_THEME, ProjectRichHandler


def _build_handler() -> ProjectRichHandler:
    return ProjectRichHandler(
        console=Console(theme=CUSTOM_THEME),
        show_time=False,
        show_level=False,
        show_path=False,
        markup=True,
    )


def _build_record(level_name: str, message: str) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="demo.py",
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    record.levelname = level_name
    return record


class LogUtilsTestCase(unittest.TestCase):
    def test_success_message_body_is_not_auto_colored(self) -> None:
        handler = _build_handler()
        message = "完成 3 条"

        text = handler.render_message(_build_record("SUCCESS", message), message)

        self.assertEqual(text.plain, message)
        self.assertEqual([span.style for span in text.spans], [])

    def test_custom_tag_spans_are_preserved_without_default_highlighter(self) -> None:
        handler = _build_handler()
        message = "完成 [tag.count]3[/tag.count] 条"

        text = handler.render_message(_build_record("SUCCESS", message), message)

        self.assertEqual(text.plain, "完成 3 条")
        self.assertEqual([span.style for span in text.spans], ["tag.count"])


if __name__ == "__main__":
    unittest.main()
