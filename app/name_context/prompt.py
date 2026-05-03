"""正文提示词使用的术语表索引。"""

import re
from dataclasses import dataclass

from app.rmmz.schema import TranslationItem

from .extraction import is_translatable_name_context_source
from .schemas import NameContextRegistry

PROMPT_MEANINGFUL_TERM_PATTERN: re.Pattern[str] = re.compile(
    r"[\w\u3040-\u30FF\u3400-\u9FFF]",
    re.UNICODE,
)


@dataclass(frozen=True, slots=True)
class NamePromptEntry:
    """注入正文用户提示词的一条术语映射。"""

    category: str
    source_text: str
    translated_text: str


class NamePromptIndex:
    """把数据库术语表转成按批次查询的提示词索引。"""

    def __init__(self, entries: list[NamePromptEntry]) -> None:
        """初始化索引。"""
        self.entries: list[NamePromptEntry] = entries
        self._speaker_by_source: dict[str, NamePromptEntry] = {}
        self._map_by_source: dict[str, NamePromptEntry] = {}
        self._build_indexes(entries)

    @classmethod
    def from_registry(cls, registry: NameContextRegistry) -> "NamePromptIndex":
        """从已填写译名的术语表构建索引，空译名会被忽略。"""
        entries: list[NamePromptEntry] = []
        for source_text, translated_text in registry.speaker_names.items():
            source = source_text.strip()
            translated = translated_text.strip()
            if is_translatable_name_context_source(source) and translated:
                entries.append(NamePromptEntry("角色名", source, translated))
        for source_text, translated_text in registry.map_display_names.items():
            source = source_text.strip()
            translated = translated_text.strip()
            if is_translatable_name_context_source(source) and translated:
                entries.append(NamePromptEntry("地图名", source, translated))
        return cls(entries)

    def select_for_batch(
        self,
        *,
        display_name: str,
        items: list[TranslationItem],
    ) -> list[NamePromptEntry]:
        """根据当前地图和正文批次挑选相关术语。"""
        selected: list[NamePromptEntry] = []
        if display_name:
            map_entry = self._map_by_source.get(display_name)
            if map_entry is not None:
                selected.append(map_entry)

        joined_original_text = "\n".join(
            line
            for item in items
            for line in item.original_lines
        )
        for item in items:
            if item.role is None:
                continue
            speaker_entry = self._speaker_by_source.get(item.role)
            if speaker_entry is not None:
                selected.append(speaker_entry)

        for source_text, entry in self._speaker_by_source.items():
            if source_text in joined_original_text:
                selected.append(entry)
        for source_text, entry in self._map_by_source.items():
            if source_text in joined_original_text:
                selected.append(entry)

        return deduplicate_prompt_entries(selected)

    def _build_indexes(self, entries: list[NamePromptEntry]) -> None:
        """构造按原文查询的索引。"""
        for entry in entries:
            if entry.category == "角色名":
                self._speaker_by_source[entry.source_text] = entry
            elif entry.category == "地图名":
                self._map_by_source[entry.source_text] = entry


def format_name_prompt_section(entries: list[NamePromptEntry]) -> str:
    """把术语映射格式化为用户提示词片段。"""
    prompt_entries = [
        entry
        for entry in entries
        if not _is_prompt_noise_entry(entry)
    ]
    if not prompt_entries:
        return ""

    sections = ["# 术语表"]
    sections.extend(format_prompt_entry(entry) for entry in prompt_entries)
    return "\n".join(sections)


def _is_prompt_noise_entry(entry: NamePromptEntry) -> bool:
    """过滤不会提升翻译质量的术语提示噪音。"""
    source = entry.source_text.strip()
    translated = entry.translated_text.strip()
    if not source or not translated:
        return True
    if source == translated:
        return True
    return PROMPT_MEANINGFUL_TERM_PATTERN.search(source) is None


def format_prompt_entry(entry: NamePromptEntry) -> str:
    """格式化单条术语映射。"""
    return f"{entry.source_text} => {entry.translated_text}"


def deduplicate_prompt_entries(entries: list[NamePromptEntry]) -> list[NamePromptEntry]:
    """按术语映射去重并保持原有顺序。"""
    seen: set[tuple[str, str, str]] = set()
    unique_entries: list[NamePromptEntry] = []
    for entry in entries:
        key = (entry.category, entry.source_text, entry.translated_text)
        if key in seen:
            continue
        seen.add(key)
        unique_entries.append(entry)
    return unique_entries


__all__: list[str] = [
    "NamePromptEntry",
    "NamePromptIndex",
    "deduplicate_prompt_entries",
    "format_name_prompt_section",
]
