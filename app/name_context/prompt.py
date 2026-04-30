"""正文提示词使用的外部标准名索引。"""

from dataclasses import dataclass

from app.rmmz.schema import TranslationItem

from .schemas import NameContextRegistry, NameEntryKind


@dataclass(frozen=True, slots=True)
class NamePromptEntry:
    """注入正文用户提示词的一条标准名映射。"""

    kind: NameEntryKind
    source_text: str
    translated_text: str
    location_path: str
    file_name: str


class NamePromptIndex:
    """把外部标准名大 JSON 转成按批次查询的提示词索引。"""

    def __init__(self, entries: list[NamePromptEntry]) -> None:
        """初始化索引。"""
        self.entries: list[NamePromptEntry] = entries
        self._by_location: dict[str, list[NamePromptEntry]] = {}
        self._map_entries_by_file: dict[str, list[NamePromptEntry]] = {}
        self._unique_entries_by_source: dict[str, NamePromptEntry] = {}
        self._build_indexes(entries)

    @classmethod
    def from_registry(cls, registry: NameContextRegistry) -> "NamePromptIndex":
        """从已填写译名的大 JSON 构建索引，空译名会被忽略。"""
        entries: list[NamePromptEntry] = []
        for registry_entry in registry.entries:
            translated_text = registry_entry.translated_text.strip()
            if not translated_text:
                continue
            source_text = registry_entry.source_text.strip()
            if not source_text:
                continue
            for location in registry_entry.locations:
                entries.append(
                    NamePromptEntry(
                        kind=registry_entry.kind,
                        source_text=source_text,
                        translated_text=translated_text,
                        location_path=location.location_path,
                        file_name=location.file_name,
                    )
                )
        return cls(entries)

    def select_for_batch(self, *, file_name: str, items: list[TranslationItem]) -> list[NamePromptEntry]:
        """根据当前文件和正文批次挑选最相关的标准名。"""
        selected: list[NamePromptEntry] = []
        selected.extend(self._map_entries_by_file.get(file_name, []))
        for item in items:
            selected.extend(self._by_location.get(item.location_path, []))

        joined_original_text = "\n".join(
            line
            for item in items
            for line in item.original_lines
        )
        for source_text, entry in self._unique_entries_by_source.items():
            if source_text and source_text in joined_original_text:
                selected.append(entry)

        return deduplicate_prompt_entries(selected)

    def _build_indexes(self, entries: list[NamePromptEntry]) -> None:
        """构造按位置、地图和原文查询的索引。"""
        source_candidates: dict[str, list[NamePromptEntry]] = {}
        for entry in entries:
            self._by_location.setdefault(entry.location_path, []).append(entry)
            if entry.kind == "map_display_name":
                self._map_entries_by_file.setdefault(entry.file_name, []).append(entry)
            source_candidates.setdefault(entry.source_text, []).append(entry)

        for source_text, candidates in source_candidates.items():
            translated_values = {candidate.translated_text for candidate in candidates}
            if len(translated_values) == 1:
                self._unique_entries_by_source[source_text] = candidates[0]


def format_name_prompt_section(entries: list[NamePromptEntry]) -> str:
    """把标准名映射格式化为用户提示词片段。"""
    if not entries:
        return ""

    character_lines = [format_prompt_entry(entry) for entry in entries if entry.kind == "speaker_name"]
    map_lines = [format_prompt_entry(entry) for entry in entries if entry.kind == "map_display_name"]
    sections = [
        "[[术语表]]",
        "以下为本批次必须遵守的标准译名。原文出现左侧词条时，译文必须使用右侧译名，不要自行改译。",
    ]
    if character_lines:
        sections.append("[角色名]")
        sections.extend(character_lines)
    if map_lines:
        sections.append("[地图名]")
        sections.extend(map_lines)
    return "\n".join(sections)


def format_prompt_entry(entry: NamePromptEntry) -> str:
    """格式化单条标准名映射。"""
    return f"- {entry.source_text} => {entry.translated_text}"


def deduplicate_prompt_entries(entries: list[NamePromptEntry]) -> list[NamePromptEntry]:
    """按完整映射去重并保持原有顺序。"""
    seen: set[tuple[NameEntryKind, str, str, str]] = set()
    unique_entries: list[NamePromptEntry] = []
    for entry in entries:
        key = (entry.kind, entry.source_text, entry.translated_text, entry.location_path)
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
