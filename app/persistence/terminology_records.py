"""术语表记录会话能力。"""

from app.terminology.schemas import TERMINOLOGY_CATEGORIES, TerminologyCategory, TerminologyGlossary, TerminologyRegistry

from .rows import row_str
from .session_base import SessionMixinBase
from .session_utils import parse_terminology_category
from .sql import (
    DELETE_ALL_TERMINOLOGY_GLOSSARY_TERMS,
    DELETE_ALL_TERMINOLOGY_TERMS,
    INSERT_TERMINOLOGY_GLOSSARY_TERM,
    INSERT_TERMINOLOGY_TERM,
    SELECT_TERMINOLOGY_GLOSSARY_TERMS,
    SELECT_TERMINOLOGY_IMPORT_STATE,
    SELECT_TERMINOLOGY_TERMS,
    TERMINOLOGY_IMPORT_STATE_KEY,
    UPSERT_TERMINOLOGY_IMPORT_STATE,
)


class TerminologyRecordSessionMixin(SessionMixinBase):
    """负责字段译名表和正文术语表的读写。"""

    async def replace_terminology_registry(
        self,
        registry: TerminologyRegistry,
    ) -> None:
        """用一次外部导入结果替换当前游戏的全部术语表条目。"""
        _ = await self.connection.execute(DELETE_ALL_TERMINOLOGY_TERMS)
        _ = await self.connection.execute(
            UPSERT_TERMINOLOGY_IMPORT_STATE,
            (TERMINOLOGY_IMPORT_STATE_KEY, 1),
        )
        for category, entries in registry.as_category_map().items():
            for source_text, translated_text in entries.items():
                _ = await self.connection.execute(
                    INSERT_TERMINOLOGY_TERM,
                    (category, source_text, translated_text),
                )
        await self.connection.commit()

    async def read_terminology_registry(self) -> TerminologyRegistry | None:
        """从数据库读取当前游戏已导入的字段译名表。"""
        async with self.connection.execute(SELECT_TERMINOLOGY_TERMS) as cursor:
            rows = await cursor.fetchall()
        if not rows:
            async with self.connection.execute(
                SELECT_TERMINOLOGY_IMPORT_STATE,
                (TERMINOLOGY_IMPORT_STATE_KEY,),
            ) as cursor:
                state_row = await cursor.fetchone()
            if state_row is None:
                return None
            return TerminologyRegistry()

        category_map: dict[TerminologyCategory, dict[str, str]] = {
            category: {}
            for category in TERMINOLOGY_CATEGORIES
        }
        for row in rows:
            category = parse_terminology_category(row_str(row, "category", self.db_path), self.db_path)
            source_text = row_str(row, "source_text", self.db_path)
            translated_text = row_str(row, "translated_text", self.db_path)
            category_map[category][source_text] = translated_text
        return TerminologyRegistry.from_category_map(category_map)

    async def replace_terminology_glossary(
        self,
        glossary: TerminologyGlossary,
    ) -> None:
        """用一次外部导入结果替换当前游戏的正文术语表。"""
        _ = await self.connection.execute(DELETE_ALL_TERMINOLOGY_GLOSSARY_TERMS)
        _ = await self.connection.execute(
            UPSERT_TERMINOLOGY_IMPORT_STATE,
            (TERMINOLOGY_IMPORT_STATE_KEY, 1),
        )
        for source_text, translated_text in glossary.terms.items():
            _ = await self.connection.execute(
                INSERT_TERMINOLOGY_GLOSSARY_TERM,
                (source_text, translated_text),
            )
        await self.connection.commit()

    async def read_terminology_glossary(self) -> TerminologyGlossary | None:
        """从数据库读取当前游戏已导入的正文术语表。"""
        async with self.connection.execute(SELECT_TERMINOLOGY_GLOSSARY_TERMS) as cursor:
            term_rows = await cursor.fetchall()
        if not term_rows:
            async with self.connection.execute(
                SELECT_TERMINOLOGY_IMPORT_STATE,
                (TERMINOLOGY_IMPORT_STATE_KEY,),
            ) as cursor:
                state_row = await cursor.fetchone()
            if state_row is None:
                return None
            return TerminologyGlossary()

        return TerminologyGlossary(
            terms={
                row_str(row, "source_text", self.db_path): row_str(row, "translated_text", self.db_path)
                for row in term_rows
            },
        )
