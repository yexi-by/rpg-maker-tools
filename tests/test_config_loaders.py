"""
Configuration loader tests.

Coverage:
1. `load_setting()` resolves relative `work_path`.
2. Prompt file references are injected into the runtime model.
3. UTF-8 BOM files are accepted.
4. Absolute `work_path` and legacy fields are rejected.
"""

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import load_setting


def _write_sample_setting(base_dir: Path) -> Path:
    (base_dir / "role.txt").write_text("role prompt", encoding="utf-8")
    (base_dir / "display.txt").write_text("display prompt", encoding="utf-8")
    (base_dir / "text.txt").write_text("text prompt", encoding="utf-8")
    (base_dir / "error.txt").write_text("error retry prompt", encoding="utf-8")

    setting_path: Path = base_dir / "setting.toml"
    setting_path.write_text(
        textwrap.dedent(
            f"""
            [project]
            file_path = "{(base_dir / "game").as_posix()}"
            work_path = "work"
            db_name = "test.db"
            translation_table_name = "translations"

            [llm_services.glossary]
            provider_type = "openai"
            base_url = ""
            api_key = "test"
            model = "test-model"
            timeout = 1

            [llm_services.text]
            provider_type = "openai"
            base_url = ""
            api_key = "test"
            model = "test-model"
            timeout = 1

            [glossary_extraction]
            role_chunk_blocks = 1
            role_chunk_lines = 1

            [glossary_translation.role_name]
            chunk_size = 1
            retry_count = 0
            retry_delay = 0
            response_retry_count = 1
            system_prompt_file = "role.txt"

            [glossary_translation.display_name]
            chunk_size = 1
            retry_count = 0
            retry_delay = 0
            response_retry_count = 1
            system_prompt_file = "display.txt"

            [translation_context]
            token_size = 10
            factor = 1
            max_command_items = 1

            [error_translation]
            chunk_size = 2
            system_prompt_file = "error.txt"

            [text_translation]
            worker_count = 1
            rpm = 1
            retry_count = 0
            retry_delay = 0
            system_prompt_file = "text.txt"
            """
        ).strip(),
        encoding="utf-8",
    )
    return setting_path


class ConfigLoaderTestCase(unittest.TestCase):
    def test_load_setting_resolves_relative_work_path_and_injects_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            setting_path = _write_sample_setting(base_dir)
            setting = load_setting(setting_path)

        self.assertEqual(setting.project.work_path, (base_dir / "work").resolve())
        self.assertEqual(setting.glossary_translation.role_name.system_prompt, "role prompt")
        self.assertEqual(
            setting.glossary_translation.display_name.system_prompt,
            "display prompt",
        )
        self.assertEqual(setting.text_translation.system_prompt, "text prompt")
        self.assertEqual(setting.error_translation.system_prompt, "error retry prompt")
        self.assertEqual(setting.error_translation.chunk_size, 2)

    def test_bom_prefixed_setting_file_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            setting_path = _write_sample_setting(base_dir)
            original_text = setting_path.read_text(encoding="utf-8")
            setting_path.write_text(original_text, encoding="utf-8-sig")

            runtime_setting = load_setting(setting_path)
            raw_bytes = setting_path.read_bytes()

        self.assertEqual(runtime_setting.project.work_path, (base_dir / "work").resolve())
        self.assertTrue(raw_bytes.startswith(b"\xef\xbb\xbf"))

    def test_load_setting_rejects_absolute_work_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            setting_path = _write_sample_setting(base_dir)
            absolute_text = setting_path.read_text(encoding="utf-8").replace(
                'work_path = "work"',
                f'work_path = "{(base_dir / "work").resolve().as_posix()}"',
            )
            setting_path.write_text(absolute_text, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "project.work_path"):
                load_setting(setting_path)

    def test_load_setting_rejects_legacy_error_translation_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            setting_path = _write_sample_setting(base_dir)
            legacy_text = setting_path.read_text(encoding="utf-8").replace(
                '[error_translation]\nchunk_size = 2\nsystem_prompt_file = "error.txt"',
                '[error_translation]\nchunk_size = 2\ntoken_size = 10\nsystem_prompt_file = "error.txt"',
            )
            setting_path.write_text(legacy_text, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "token_size"):
                load_setting(setting_path)

    def test_load_setting_logs_human_readable_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            setting_path = _write_sample_setting(base_dir)

            with patch("app.config.loaders.logger.info") as mock_info:
                setting = load_setting(setting_path)

        logged_messages: list[str] = [
            str(call.args[0]) for call in mock_info.call_args_list if call.args
        ]
        summary_text: str = "\n".join(logged_messages)

        self.assertEqual(setting.project.work_path, (base_dir / "work").resolve())
        self.assertIn("当前正在使用的配置", summary_text)
        self.assertIn(str(setting_path.resolve()), summary_text)
        self.assertIn(str(base_dir / "game"), summary_text)
        self.assertIn("正文切块:", summary_text)
        self.assertIn("10", summary_text)
        self.assertIn("token", summary_text)
        self.assertIn("提示词文件:", summary_text)
        self.assertIn("role.txt", summary_text)
        self.assertIn("display.txt", summary_text)
        self.assertIn("text.txt", summary_text)
        self.assertIn("error.txt", summary_text)


if __name__ == "__main__":
    unittest.main()
