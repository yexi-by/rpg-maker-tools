"""
正文任务块导出脚本测试。

覆盖目标：
1. 只读 glossary 成功时，导出结果能包含 glossary 命中内容。
2. 数据库缺失时，脚本仍能按空 glossary 导出任务块。
3. 导出消息中确实隐藏了 system prompt，只保留 user prompt。
4. `plugins.js` 来源也会被导出为独立任务块文件。
5. 导出前后数据库文件内容保持不变。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import textwrap
import unittest
from pathlib import Path

from app.database.db import TranslationDB
from app.database.sql import (
    CREATE_GLOSSARY_STATE_TABLE,
    CREATE_PLACE_GLOSSARY_TABLE,
    CREATE_ROLE_GLOSSARY_TABLE,
    INSERT_PLACE_GLOSSARY_ITEM,
    INSERT_ROLE_GLOSSARY_ITEM,
    UPSERT_GLOSSARY_STATE,
)
from scripts.export_translation_task_blocks import export_translation_task_blocks


def _write_prompt_files(base_dir: Path) -> None:
    """
    写入测试所需的提示词文件。

    Args:
        base_dir: 测试根目录。
    """
    (base_dir / "prompts").mkdir(parents=True, exist_ok=True)
    (base_dir / "prompts" / "glossary_role_name_system.txt").write_text(
        "role glossary prompt",
        encoding="utf-8",
    )
    (base_dir / "prompts" / "glossary_display_name_system.txt").write_text(
        "display glossary prompt",
        encoding="utf-8",
    )
    (base_dir / "prompts" / "error_retry_system.txt").write_text(
        "error retry prompt",
        encoding="utf-8",
    )
    (base_dir / "prompts" / "text_translation_system.txt").write_text(
        "绝对不能出现在导出文件里的系统提示词",
        encoding="utf-8",
    )


def _write_setting(base_dir: Path, game_dir: Path) -> Path:
    """
    写入测试用配置文件。

    Args:
        base_dir: 测试根目录。
        game_dir: 游戏目录。

    Returns:
        配置文件路径。
    """
    setting_path: Path = base_dir / "setting.toml"
    setting_path.write_text(
        textwrap.dedent(
            f"""
            [project]
            file_path = "{game_dir.as_posix()}"
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
            system_prompt_file = "prompts/glossary_role_name_system.txt"

            [glossary_translation.display_name]
            chunk_size = 1
            retry_count = 0
            retry_delay = 0
            response_retry_count = 1
            system_prompt_file = "prompts/glossary_display_name_system.txt"

            [translation_context]
            token_size = 9999
            factor = 1
            max_command_items = 1

            [error_translation]
            chunk_size = 2
            system_prompt_file = "prompts/error_retry_system.txt"

            [text_translation]
            worker_count = 1
            rpm = 1
            retry_count = 0
            retry_delay = 0
            system_prompt_file = "prompts/text_translation_system.txt"
            """
        ).strip(),
        encoding="utf-8",
    )
    return setting_path


def _write_game_files(game_dir: Path) -> None:
    """
    构造最小游戏目录。

    Args:
        game_dir: 游戏根目录。
    """
    data_dir: Path = game_dir / "data"
    js_dir: Path = game_dir / "js"
    data_dir.mkdir(parents=True, exist_ok=True)
    js_dir.mkdir(parents=True, exist_ok=True)

    system_data = {
        "gameTitle": "",
        "terms": {
            "basic": [],
            "commands": [],
            "params": [],
            "messages": {},
        },
        "elements": [],
        "skillTypes": [],
        "weaponTypes": [],
        "armorTypes": [],
        "equipTypes": [],
        "variables": [],
        "switches": [],
    }
    common_events = [
        None,
        {
            "id": 1,
            "list": [
                {"code": 101, "indent": 0, "parameters": ["", 0, 0, 2, "勇者"]},
                {"code": 401, "indent": 0, "parameters": ["こんにちは"]},
            ],
        },
    ]
    troops = [None]
    plugins_js = textwrap.dedent(
        """
        var $plugins = [
          {
            "name": "TestPlugin",
            "status": true,
            "parameters": {
              "messageText": "プラグイン本文"
            }
          }
        ];
        """
    ).strip()

    (data_dir / "System.json").write_text(
        json.dumps(system_data, ensure_ascii=False),
        encoding="utf-8",
    )
    (data_dir / "CommonEvents.json").write_text(
        json.dumps(common_events, ensure_ascii=False),
        encoding="utf-8",
    )
    (data_dir / "Troops.json").write_text(
        json.dumps(troops, ensure_ascii=False),
        encoding="utf-8",
    )
    (js_dir / "plugins.js").write_text(plugins_js, encoding="utf-8")


def _write_glossary_db(work_dir: Path) -> Path:
    """
    写入测试用 glossary 数据库。

    Args:
        work_dir: 工作目录。

    Returns:
        数据库文件路径。
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    db_path: Path = work_dir / "test.db"
    db = sqlite3.connect(db_path)
    try:
        db.execute(
            CREATE_ROLE_GLOSSARY_TABLE.format(
                table_name=TranslationDB.GLOSSARY_ROLE_TABLE
            )
        )
        db.execute(
            CREATE_PLACE_GLOSSARY_TABLE.format(
                table_name=TranslationDB.GLOSSARY_PLACE_TABLE
            )
        )
        db.execute(
            CREATE_GLOSSARY_STATE_TABLE.format(
                table_name=TranslationDB.GLOSSARY_STATE_TABLE
            )
        )
        db.execute(
            INSERT_ROLE_GLOSSARY_ITEM.format(
                table_name=TranslationDB.GLOSSARY_ROLE_TABLE
            ),
            ("勇者", "Hero", "男"),
        )
        db.execute(
            INSERT_PLACE_GLOSSARY_ITEM.format(
                table_name=TranslationDB.GLOSSARY_PLACE_TABLE
            ),
            ("城镇", "Town"),
        )
        db.execute(
            UPSERT_GLOSSARY_STATE.format(
                table_name=TranslationDB.GLOSSARY_STATE_TABLE
            ),
            (TranslationDB.GLOSSARY_STATE_KEY, 1),
        )
        db.commit()
    finally:
        db.close()

    return db_path


class ExportTranslationTaskBlocksTestCase(unittest.TestCase):
    def test_export_task_blocks_uses_read_only_glossary_and_hides_system_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            game_dir = base_dir / "game"
            output_dir = base_dir / "logs" / "translation_task_blocks"

            _write_prompt_files(base_dir)
            setting_path = _write_setting(base_dir, game_dir)
            _write_game_files(game_dir)
            db_path = _write_glossary_db(base_dir / "work")
            original_db_bytes: bytes = db_path.read_bytes()

            summary = asyncio.run(
                export_translation_task_blocks(
                    setting_path=setting_path,
                    output_dir=output_dir,
                )
            )

            self.assertTrue(summary.glossary_loaded)
            self.assertEqual(summary.total_extracted_items, 2)
            self.assertEqual(summary.total_batches, 2)
            self.assertEqual(original_db_bytes, db_path.read_bytes())

            index_path = output_dir / "_index.txt"
            common_event_batch = output_dir / "0001_CommonEvents_chunk001.txt"
            plugin_batch = output_dir / "0002_plugins_js_chunk001.txt"

            self.assertTrue(index_path.exists())
            self.assertTrue(common_event_batch.exists())
            self.assertTrue(plugin_batch.exists())

            common_event_text: str = common_event_batch.read_text(encoding="utf-8")
            plugin_text: str = plugin_batch.read_text(encoding="utf-8")
            index_text: str = index_path.read_text(encoding="utf-8")

            self.assertIn("原名: 勇者 | 译名: Hero | 性别: 男", common_event_text)
            self.assertIn('"role": "user"', common_event_text)
            self.assertNotIn('"role": "system"', common_event_text)
            self.assertNotIn("绝对不能出现在导出文件里的系统提示词", common_event_text)
            self.assertIn('"location_path": "CommonEvents.json/1/0"', common_event_text)
            self.assertIn('"placeholder_map"', common_event_text)
            self.assertIn("来源类型: plugins.js", plugin_text)
            self.assertIn("总任务块数: 2", index_text)
            self.assertIn("0002_plugins_js_chunk001.txt", index_text)

    def test_export_task_blocks_keeps_working_when_database_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            game_dir = base_dir / "game"
            output_dir = base_dir / "logs" / "translation_task_blocks"

            _write_prompt_files(base_dir)
            setting_path = _write_setting(base_dir, game_dir)
            _write_game_files(game_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            stale_file = output_dir / "stale.txt"
            stale_file.write_text("old", encoding="utf-8")

            summary = asyncio.run(
                export_translation_task_blocks(
                    setting_path=setting_path,
                    output_dir=output_dir,
                )
            )

            self.assertFalse(summary.glossary_loaded)
            self.assertIn("数据库不存在", summary.glossary_status)
            self.assertFalse(stale_file.exists())

            common_event_batch = output_dir / "0001_CommonEvents_chunk001.txt"
            batch_text: str = common_event_batch.read_text(encoding="utf-8")
            self.assertIn("是否命中 glossary: False", batch_text)
            self.assertNotIn("原名: 勇者 | 译名: Hero | 性别: 男", batch_text)


if __name__ == "__main__":
    unittest.main()
