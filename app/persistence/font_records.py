"""字体替换记录会话能力。"""

from collections.abc import Sequence

from app.rmmz.schema import FontReplacementRecord

from .rows import row_str
from .session_base import SessionMixinBase
from .sql import DELETE_ALL_FONT_REPLACEMENT_RECORDS, INSERT_FONT_REPLACEMENT_RECORD, SELECT_FONT_REPLACEMENT_RECORDS


class FontRecordSessionMixin(SessionMixinBase):
    """负责候选覆盖字体记录的保存、读取和清理。"""

    async def replace_font_replacement_records(
        self,
        records: Sequence[FontReplacementRecord],
    ) -> None:
        """用本次字体覆盖记录替换当前游戏的候选覆盖字体记录。"""
        _ = await self.connection.execute(DELETE_ALL_FONT_REPLACEMENT_RECORDS)
        if records:
            serialized_records = [
                (
                    record.file_name,
                    record.value_path,
                    record.original_text,
                    record.replaced_text,
                    record.replacement_font_name,
                )
                for record in records
            ]
            _ = await self.connection.executemany(
                INSERT_FONT_REPLACEMENT_RECORD,
                serialized_records,
            )
        await self.connection.commit()

    async def read_font_replacement_records(self) -> list[FontReplacementRecord]:
        """读取当前游戏最近一次字体覆盖产生的候选覆盖字体记录。"""
        async with self.connection.execute(SELECT_FONT_REPLACEMENT_RECORDS) as cursor:
            rows = await cursor.fetchall()
        return [
            FontReplacementRecord(
                file_name=row_str(row, "file_name", self.db_path),
                value_path=row_str(row, "value_path", self.db_path),
                original_text=row_str(row, "original_text", self.db_path),
                replaced_text=row_str(row, "replaced_text", self.db_path),
                replacement_font_name=row_str(row, "replacement_font_name", self.db_path),
            )
            for row in rows
        ]

    async def clear_font_replacement_records(self) -> int:
        """清空当前游戏已经完成处理的字体覆盖记录。"""
        cursor = await self.connection.execute(DELETE_ALL_FONT_REPLACEMENT_RECORDS)
        await self.connection.commit()
        return max(cursor.rowcount, 0)
