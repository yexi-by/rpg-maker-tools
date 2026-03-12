import io
import unittest
from unittest.mock import AsyncMock, patch

from rich.console import Console

import cli.app as cli_app_module
from app.utils.log_utils import CUSTOM_THEME


class FakeHandler:
    def __init__(self, *, fail_action: str | None = None) -> None:
        self.fail_action = fail_action
        self.calls: list[str] = []

    async def build_glossary(self):
        self.calls.append("build_glossary")
        if self.fail_action == "build_glossary":
            raise RuntimeError("glossary boom")
        yield {"kind": "roles"}

    async def translate_text(self) -> None:
        self.calls.append("translate_text")
        if self.fail_action == "translate_text":
            raise RuntimeError("translate boom")

    async def retry_error_table(self) -> None:
        self.calls.append("retry_error_table")
        if self.fail_action == "retry_error_table":
            raise RuntimeError("retry boom")

    async def write_back(self) -> None:
        self.calls.append("write_back")
        if self.fail_action == "write_back":
            raise RuntimeError("write boom")


class FakeProvider:
    pass


class CliAppTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_cli_runs_selected_action_and_returns_to_menu(self) -> None:
        handler = FakeHandler()
        provider = FakeProvider()
        output = io.StringIO()
        test_console = Console(
            file=output,
            force_terminal=False,
            color_system=None,
            theme=CUSTOM_THEME,
        )
        answers = iter(["1", "6"])
        create_mock = AsyncMock(return_value=handler)

        with (
            patch.object(cli_app_module, "console", test_console),
            patch.object(cli_app_module.TranslationHandler, "create", create_mock),
        ):
            app = cli_app_module.CliApp(
                provider_factory=lambda: provider,
                input_func=lambda _: next(answers),
            )
            await app.run()

        self.assertEqual(handler.calls, ["build_glossary"])
        create_mock.assert_awaited_once_with(provider)
        self.assertIn("CLI 会话已结束", output.getvalue())

    async def test_cli_run_all_dispatches_full_pipeline(self) -> None:
        handler = FakeHandler()
        provider = FakeProvider()
        test_console = Console(
            file=io.StringIO(),
            force_terminal=False,
            color_system=None,
            theme=CUSTOM_THEME,
        )
        answers = iter(["5", "6"])

        with (
            patch.object(cli_app_module, "console", test_console),
            patch.object(
                cli_app_module.TranslationHandler,
                "create",
                AsyncMock(return_value=handler),
            ),
        ):
            app = cli_app_module.CliApp(
                provider_factory=lambda: provider,
                input_func=lambda _: next(answers),
            )
            await app.run()

        self.assertEqual(
            handler.calls,
            ["build_glossary", "translate_text", "write_back"],
        )

    async def test_cli_logs_action_failure_and_keeps_session_alive(self) -> None:
        handler = FakeHandler(fail_action="translate_text")
        provider = FakeProvider()
        output = io.StringIO()
        test_console = Console(
            file=output,
            force_terminal=False,
            color_system=None,
            theme=CUSTOM_THEME,
        )
        answers = iter(["2", "6"])

        with (
            patch.object(cli_app_module, "console", test_console),
            patch.object(
                cli_app_module.TranslationHandler,
                "create",
                AsyncMock(return_value=handler),
            ),
            patch.object(cli_app_module.logger, "exception") as logger_exception_mock,
        ):
            app = cli_app_module.CliApp(
                provider_factory=lambda: provider,
                input_func=lambda _: next(answers),
            )
            await app.run()

        self.assertEqual(handler.calls, ["translate_text"])
        logger_exception_mock.assert_called_once_with(
            "[tag.exception]翻译正文执行失败[/tag.exception]"
        )
        rendered = output.getvalue()
        self.assertIn("翻译正文执行失败", rendered)
        self.assertIn("CLI 会话已结束", rendered)


if __name__ == "__main__":
    unittest.main()
