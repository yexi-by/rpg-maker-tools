import argparse
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import scripts.analyze_357_translation_need as analyze_module
import scripts.export_translation_task_blocks as export_module
import scripts.generate_357_report as report_module


class ScriptLoggingTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_export_script_logs_summary_with_logger(self) -> None:
        summary = export_module.ExportSummary(
            setting_path=Path("setting.toml"),
            game_path=Path("game"),
            db_path=Path("work") / "test.db",
            output_dir=Path("logs") / "translation_task_blocks",
            glossary_loaded=True,
            glossary_status="glossary 只读加载成功",
            total_extracted_items=2,
            total_batches=1,
            batch_files=[],
        )

        with (
            patch.object(
                export_module,
                "parse_args",
                return_value=argparse.Namespace(
                    setting_path="setting.toml",
                    output_dir="logs/translation_task_blocks",
                ),
            ),
            patch.object(
                export_module,
                "export_translation_task_blocks",
                AsyncMock(return_value=summary),
            ),
            patch.object(export_module.logger, "success") as success_mock,
            patch.object(export_module.logger, "info") as info_mock,
            patch.object(export_module.logger, "exception") as exception_mock,
        ):
            await export_module.main()

        success_mock.assert_called_once()
        info_mock.assert_called_once()
        exception_mock.assert_not_called()
        self.assertIn("正文翻译任务块导出完成", success_mock.call_args.args[0])
        self.assertIn("glossary 状态", info_mock.call_args.args[0])

    async def test_export_script_logs_exception_once_at_main_boundary(self) -> None:
        with (
            patch.object(
                export_module,
                "parse_args",
                return_value=argparse.Namespace(
                    setting_path="setting.toml",
                    output_dir="logs/translation_task_blocks",
                ),
            ),
            patch.object(
                export_module,
                "export_translation_task_blocks",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
            patch.object(export_module.logger, "exception") as exception_mock,
        ):
            with self.assertRaises(RuntimeError):
                await export_module.main()

        exception_mock.assert_called_once_with(
            "[tag.exception]正文翻译任务块导出失败[/tag.exception]"
        )

    async def test_generate_report_script_logs_summary_with_logger(self) -> None:
        with (
            patch.object(
                report_module,
                "parse_args",
                return_value=argparse.Namespace(
                    game_path="game",
                    report_path="logs/357_extraction_report.txt",
                ),
            ),
            patch.object(
                report_module,
                "build_report",
                AsyncMock(
                    return_value={
                        "items": 3,
                        "placeholder_items": 2,
                        "roundtrip_failures": 1,
                        "suspicious_items": 4,
                    }
                ),
            ),
            patch.object(report_module.logger, "success") as success_mock,
            patch.object(report_module.logger, "info") as info_mock,
            patch.object(report_module.logger, "exception") as exception_mock,
        ):
            await report_module.main()

        success_mock.assert_called_once()
        info_mock.assert_called_once()
        exception_mock.assert_not_called()
        self.assertIn("357 提取专项报告已生成", success_mock.call_args.args[0])
        self.assertIn("占位符条目", info_mock.call_args.args[0])

    async def test_generate_report_script_logs_exception_once_at_main_boundary(
        self,
    ) -> None:
        with (
            patch.object(
                report_module,
                "parse_args",
                return_value=argparse.Namespace(
                    game_path="game",
                    report_path="logs/357_extraction_report.txt",
                ),
            ),
            patch.object(
                report_module,
                "build_report",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
            patch.object(report_module.logger, "exception") as exception_mock,
        ):
            with self.assertRaises(RuntimeError):
                await report_module.main()

        exception_mock.assert_called_once_with(
            "[tag.exception]生成 357 提取专项报告失败[/tag.exception]"
        )

    async def test_analyze_script_logs_summary_with_logger(self) -> None:
        with (
            patch.object(
                analyze_module,
                "parse_args",
                return_value=argparse.Namespace(
                    game_path="game",
                    report_path="logs/357_translation_need_analysis.txt",
                ),
            ),
            patch.object(
                analyze_module,
                "build_analysis_report",
                AsyncMock(
                    return_value={
                        "items": 6,
                        "unique_visible_texts": 5,
                        "kana_items": 2,
                        "kanji_only_items": 1,
                        "ascii_items": 1,
                        "other_items": 2,
                        "duplicate_visible_texts": 1,
                    }
                ),
            ),
            patch.object(analyze_module.logger, "success") as success_mock,
            patch.object(analyze_module.logger, "info") as info_mock,
            patch.object(analyze_module.logger, "exception") as exception_mock,
        ):
            await analyze_module.main()

        success_mock.assert_called_once()
        info_mock.assert_called_once()
        exception_mock.assert_not_called()
        self.assertIn("357 翻译必要性分析报告已生成", success_mock.call_args.args[0])
        self.assertIn("可见文本去重后", info_mock.call_args.args[0])

    async def test_analyze_script_logs_exception_once_at_main_boundary(self) -> None:
        with (
            patch.object(
                analyze_module,
                "parse_args",
                return_value=argparse.Namespace(
                    game_path="game",
                    report_path="logs/357_translation_need_analysis.txt",
                ),
            ),
            patch.object(
                analyze_module,
                "build_analysis_report",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
            patch.object(analyze_module.logger, "exception") as exception_mock,
        ):
            with self.assertRaises(RuntimeError):
                await analyze_module.main()

        exception_mock.assert_called_once_with(
            "[tag.exception]生成 357 翻译必要性分析报告失败[/tag.exception]"
        )


if __name__ == "__main__":
    unittest.main()
