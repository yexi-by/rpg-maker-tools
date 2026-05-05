"""正文提示词使用的术语表索引。"""

import re
from dataclasses import dataclass

from app.rmmz.schema import SYSTEM_FILE_NAME, GameData, TranslationItem

from .extraction import BASE_NAME_CATEGORIES, is_translatable_terminology_source
from .schemas import TerminologyCategory, TerminologyRegistry

PROMPT_MEANINGFUL_TERM_PATTERN: re.Pattern[str] = re.compile(
    r"[\w\u3040-\u30FF\u3400-\u9FFF]",
    re.UNICODE,
)


@dataclass(frozen=True, slots=True)
class TerminologyPromptEntry:
    """注入正文用户提示词的一条术语映射。"""

    category: TerminologyCategory
    source_text: str
    translated_text: str


class TerminologyPromptIndex:
    """把数据库术语表转成按批次查询的提示词索引。"""

    def __init__(
        self,
        *,
        entries: list[TerminologyPromptEntry],
        owner_entries: dict[str, list[TerminologyPromptEntry]],
        system_entries: list[TerminologyPromptEntry],
    ) -> None:
        """初始化索引。"""
        self.entries: list[TerminologyPromptEntry] = entries
        self._speaker_by_source: dict[str, TerminologyPromptEntry] = {}
        self._map_by_source: dict[str, TerminologyPromptEntry] = {}
        self._entries_by_source: dict[str, list[TerminologyPromptEntry]] = {}
        self._owner_entries: dict[str, list[TerminologyPromptEntry]] = owner_entries
        self._system_entries: list[TerminologyPromptEntry] = system_entries
        self._build_indexes(entries)

    @classmethod
    def from_registry(
        cls,
        registry: TerminologyRegistry,
        game_data: GameData | None = None,
    ) -> "TerminologyPromptIndex":
        """从已填写译名的术语表构建索引，空译名会被忽略。"""
        entries: list[TerminologyPromptEntry] = []
        category_map = registry.as_category_map()
        for category, category_entries in category_map.items():
            for source_text, translated_text in category_entries.items():
                source = source_text.strip()
                translated = translated_text.strip()
                if is_translatable_terminology_source(source) and translated:
                    entries.append(TerminologyPromptEntry(category, source, translated))
        return cls(
            entries=entries,
            owner_entries=_build_owner_entries(registry=registry, game_data=game_data),
            system_entries=_build_system_entries(registry),
        )

    def select_for_batch(
        self,
        *,
        display_name: str,
        items: list[TranslationItem],
    ) -> list[TerminologyPromptEntry]:
        """根据当前地图、正文批次和数据库条目挑选相关术语。"""
        selected: list[TerminologyPromptEntry] = []
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
            if item.role is not None:
                speaker_entry = self._speaker_by_source.get(item.role)
                if speaker_entry is not None:
                    selected.append(speaker_entry)
            selected.extend(self._select_owner_entries(item.location_path))

        for source_text, entries in self._entries_by_source.items():
            if source_text in joined_original_text:
                selected.extend(entries)

        return deduplicate_prompt_entries(selected)

    def _select_owner_entries(self, location_path: str) -> list[TerminologyPromptEntry]:
        """按正文条目所在数据库对象选择同条目名称术语。"""
        if location_path.startswith(f"{SYSTEM_FILE_NAME}/"):
            return self._system_entries
        parts = location_path.split("/")
        if len(parts) < 2:
            return []
        owner_key = "/".join(parts[:2])
        return self._owner_entries.get(owner_key, [])

    def _build_indexes(self, entries: list[TerminologyPromptEntry]) -> None:
        """构造按原文查询的索引。"""
        for entry in entries:
            self._entries_by_source.setdefault(entry.source_text, []).append(entry)
            if entry.category == "speaker_names":
                self._speaker_by_source[entry.source_text] = entry
            elif entry.category == "map_display_names":
                self._map_by_source[entry.source_text] = entry


def _build_owner_entries(
    *,
    registry: TerminologyRegistry,
    game_data: GameData | None,
) -> dict[str, list[TerminologyPromptEntry]]:
    """为数据库条目正文建立同条目术语索引。"""
    if game_data is None:
        return {}
    category_map = registry.as_category_map()
    owner_entries: dict[str, list[TerminologyPromptEntry]] = {}
    for file_name, category in BASE_NAME_CATEGORIES.items():
        translations = category_map[category]
        for item in game_data.base_data.get(file_name, []):
            if item is None:
                continue
            name = item.name.strip()
            translated_name = translations.get(name, "").strip()
            if translated_name:
                owner_entries.setdefault(f"{file_name}/{item.id}", []).append(
                    TerminologyPromptEntry(category, name, translated_name)
                )
            if file_name != "Actors.json":
                continue
            nickname = item.nickname.strip()
            translated_nickname = registry.actor_nicknames.get(nickname, "").strip()
            if translated_nickname:
                owner_entries.setdefault(f"{file_name}/{item.id}", []).append(
                    TerminologyPromptEntry("actor_nicknames", nickname, translated_nickname)
                )
    return owner_entries


def _build_system_entries(registry: TerminologyRegistry) -> list[TerminologyPromptEntry]:
    """收集 System 正文翻译时可参考的系统类型术语。"""
    entries: list[TerminologyPromptEntry] = []
    for category in (
        "system_elements",
        "system_skill_types",
        "system_weapon_types",
        "system_armor_types",
        "system_equip_types",
    ):
        for source_text, translated_text in registry.as_category_map()[category].items():
            source = source_text.strip()
            translated = translated_text.strip()
            if source and translated:
                entries.append(TerminologyPromptEntry(category, source, translated))
    return entries


def format_terminology_prompt_section(entries: list[TerminologyPromptEntry]) -> str:
    """把术语映射格式化为用户提示词片段。"""
    prompt_entries = [
        entry
        for entry in entries
        if not _is_prompt_noise_entry(entry)
    ]
    if not prompt_entries:
        return ""

    sections = ["[[术语表]]"]
    sections.extend(format_prompt_entry(entry) for entry in prompt_entries)
    return "\n".join(sections)


def _is_prompt_noise_entry(entry: TerminologyPromptEntry) -> bool:
    """过滤不会提升翻译质量的术语提示噪音。"""
    source = entry.source_text.strip()
    translated = entry.translated_text.strip()
    if not source or not translated:
        return True
    if source == translated:
        return True
    return PROMPT_MEANINGFUL_TERM_PATTERN.search(source) is None


def format_prompt_entry(entry: TerminologyPromptEntry) -> str:
    """格式化单条术语映射。"""
    return f"{entry.source_text} => {entry.translated_text}"


def deduplicate_prompt_entries(entries: list[TerminologyPromptEntry]) -> list[TerminologyPromptEntry]:
    """按术语映射去重并保持原有顺序。"""
    seen: set[tuple[TerminologyCategory, str, str]] = set()
    unique_entries: list[TerminologyPromptEntry] = []
    for entry in entries:
        key = (entry.category, entry.source_text, entry.translated_text)
        if key in seen:
            continue
        seen.add(key)
        unique_entries.append(entry)
    return unique_entries


__all__: list[str] = [
    "TerminologyPromptEntry",
    "TerminologyPromptIndex",
    "deduplicate_prompt_entries",
    "format_terminology_prompt_section",
]
