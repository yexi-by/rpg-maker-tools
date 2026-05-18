"""主翻译表读写会话能力。"""

import json
from collections.abc import Sequence

from app.rmmz.schema import TranslationItem

from .rows import decode_string_list, row_item_type, row_optional_str, row_str
from .session_base import SessionMixinBase
from .sql import (
    DELETE_TRANSLATION_ITEM_BY_PATH,
    DELETE_TRANSLATION_ITEMS_BY_PREFIX,
    INSERT_TRANSLATION,
    SELECT_TRANSLATED_ITEMS,
    SELECT_TRANSLATION_PATHS,
)


class TranslationRecordSessionMixin(SessionMixinBase):
    """负责已保存译文记录的读写与清理。"""

    async def write_translation_items(
        self,
        items: Sequence[TranslationItem],
    ) -> None:
        """批量写入已完成译文到主翻译表。"""
        if items:
            serialized_items = [
                (
                    translation_item.location_path,
                    translation_item.item_type,
                    translation_item.role,
                    json.dumps(translation_item.original_lines, ensure_ascii=False),
                    json.dumps(translation_item.source_line_paths, ensure_ascii=False),
                    json.dumps(translation_item.translation_lines, ensure_ascii=False),
                )
                for translation_item in items
            ]
            _ = await self.connection.executemany(INSERT_TRANSLATION, serialized_items)
        await self.connection.commit()

    async def read_translation_location_paths(self) -> set[str]:
        """读取主翻译表中的全部已完成路径。"""
        async with self.connection.execute(SELECT_TRANSLATION_PATHS) as cursor:
            rows = await cursor.fetchall()
        return {row_str(row, "location_path", self.db_path) for row in rows}

    async def read_translated_items(self) -> list[TranslationItem]:
        """读取主翻译表中的全部正文译文。"""
        async with self.connection.execute(SELECT_TRANSLATED_ITEMS) as cursor:
            rows = await cursor.fetchall()

        translated_items: list[TranslationItem] = []
        for row in rows:
            original_lines = decode_string_list(row_str(row, "original_lines", self.db_path), "original_lines")
            source_line_paths = decode_string_list(
                row_str(row, "source_line_paths", self.db_path),
                "source_line_paths",
            )
            translation_lines = decode_string_list(
                row_str(row, "translation_lines", self.db_path),
                "translation_lines",
            )
            translated_items.append(
                TranslationItem(
                    location_path=row_str(row, "location_path", self.db_path),
                    item_type=row_item_type(row, "item_type", self.db_path),
                    role=row_optional_str(row, "role", self.db_path),
                    original_lines=original_lines,
                    source_line_paths=source_line_paths,
                    translation_lines=translation_lines,
                )
            )
        return translated_items

    async def delete_translation_items_by_prefixes(self, prefixes: list[str]) -> int:
        """按路径前缀批量删除主翻译表中的记录。"""
        deleted_rows = 0
        for prefix in prefixes:
            cursor = await self.connection.execute(
                DELETE_TRANSLATION_ITEMS_BY_PREFIX,
                (f"{prefix}%",),
            )
            if cursor.rowcount > 0:
                deleted_rows += cursor.rowcount
        await self.connection.commit()
        return deleted_rows

    async def delete_translation_items_except_paths(
        self,
        allowed_paths: set[str],
    ) -> int:
        """删除当前提取规则之外的主翻译表记录。"""
        async with self.connection.execute(SELECT_TRANSLATION_PATHS) as cursor:
            rows = await cursor.fetchall()

        stored_paths = {row_str(row, "location_path", self.db_path) for row in rows}
        stale_paths = sorted(stored_paths - allowed_paths)
        if not stale_paths:
            return 0

        _ = await self.connection.executemany(
            DELETE_TRANSLATION_ITEM_BY_PATH,
            [(path,) for path in stale_paths],
        )
        await self.connection.commit()
        return len(stale_paths)

    async def delete_translation_items_by_paths(
        self,
        location_paths: Sequence[str],
    ) -> int:
        """按精确定位路径批量删除主翻译表记录。"""
        deleted_rows = 0
        for location_path in location_paths:
            cursor = await self.connection.execute(
                DELETE_TRANSLATION_ITEM_BY_PATH,
                (location_path,),
            )
            if cursor.rowcount > 0:
                deleted_rows += cursor.rowcount
        await self.connection.commit()
        return deleted_rows
