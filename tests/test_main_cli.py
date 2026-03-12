from unittest.mock import patch
import unittest

import main as main_module


class MainCliTestCase(unittest.TestCase):
    def test_main_launches_cli(self) -> None:
        with patch.object(main_module, "run_cli") as run_cli_mock:
            main_module.main()

        run_cli_mock.assert_called_once_with()

    def test_main_logs_and_reraises_cli_startup_error(self) -> None:
        with (
            patch.object(main_module, "run_cli", side_effect=RuntimeError("boom")),
            patch.object(main_module.logger, "exception") as logger_exception_mock,
        ):
            with self.assertRaises(RuntimeError):
                main_module.main()

        logger_exception_mock.assert_called_once_with(
            "[tag.exception]命令行启动失败[/tag.exception]"
        )


if __name__ == "__main__":
    unittest.main()
