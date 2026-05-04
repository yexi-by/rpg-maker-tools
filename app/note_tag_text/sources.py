"""Note 标签来源扫描工具。"""

from dataclasses import dataclass
from fnmatch import fnmatchcase

from app.rmmz.schema import MAP_PATTERN, PLUGINS_FILE_NAME, GameData
from app.rmmz.text_rules import JsonValue

MAP_NOTE_FILE_PATTERN = "Map*.json"


@dataclass(frozen=True, slots=True)
class NoteTagSource:
    """单个 `note` 字段及其可回写定位信息。"""

    file_name: str
    owner_path: tuple[str, ...]
    note_text: str
    location_prefix: str


def collect_note_tag_sources(game_data: GameData, file_pattern: str | None = None) -> list[NoteTagSource]:
    """收集标准 `data/*.json` 中所有对象的 `note` 字段。"""
    sources: list[NoteTagSource] = []
    for file_name in sorted(_iter_data_file_names(game_data=game_data, file_pattern=file_pattern)):
        value = game_data.data[file_name]
        if isinstance(value, str):
            continue
        sources.extend(_collect_note_tag_sources_in_value(file_name=file_name, value=value, owner_path=()))
    return sources


def candidate_file_pattern(file_name: str) -> str:
    """返回候选导出使用的文件模式。"""
    if MAP_PATTERN.fullmatch(file_name):
        return MAP_NOTE_FILE_PATTERN
    return file_name


def note_file_pattern_matches(*, file_name: str, file_pattern: str) -> bool:
    """判断规则文件模式是否命中具体 data 文件名。"""
    return fnmatchcase(file_name, file_pattern)


def matched_note_file_names(*, game_data: GameData, file_pattern: str) -> list[str]:
    """返回规则文件模式命中的标准 data 文件名。"""
    return sorted(_iter_data_file_names(game_data=game_data, file_pattern=file_pattern))


def _iter_data_file_names(*, game_data: GameData, file_pattern: str | None) -> list[str]:
    """列出可参与 Note 标签扫描的标准 data JSON 文件。"""
    file_names: list[str] = []
    for file_name, value in game_data.data.items():
        if file_name == PLUGINS_FILE_NAME or not file_name.endswith(".json"):
            continue
        if isinstance(value, str):
            continue
        if file_pattern is not None and not note_file_pattern_matches(file_name=file_name, file_pattern=file_pattern):
            continue
        file_names.append(file_name)
    return file_names


def _collect_note_tag_sources_in_value(
    *,
    file_name: str,
    value: JsonValue,
    owner_path: tuple[str, ...],
) -> list[NoteTagSource]:
    """递归收集 JSON 值中的 note 字段。"""
    sources: list[NoteTagSource] = []
    if isinstance(value, dict):
        note_value = value.get("note")
        if isinstance(note_value, str) and note_value:
            sources.append(
                NoteTagSource(
                    file_name=file_name,
                    owner_path=owner_path,
                    note_text=note_value,
                    location_prefix=_format_location_prefix(file_name=file_name, owner_path=owner_path),
                )
            )
        for key, child_value in value.items():
            if key == "note":
                continue
            sources.extend(
                _collect_note_tag_sources_in_value(
                    file_name=file_name,
                    value=child_value,
                    owner_path=(*owner_path, key),
                )
            )
        return sources

    if isinstance(value, list):
        for index, child_value in enumerate(value):
            if child_value is None:
                continue
            sources.extend(
                _collect_note_tag_sources_in_value(
                    file_name=file_name,
                    value=child_value,
                    owner_path=(*owner_path, str(index)),
                )
            )
    return sources


def _format_location_prefix(*, file_name: str, owner_path: tuple[str, ...]) -> str:
    """把 note 所在对象路径转换成翻译条目前缀。"""
    if not owner_path:
        return file_name
    return "/".join((file_name, *owner_path))


__all__: list[str] = [
    "MAP_NOTE_FILE_PATTERN",
    "NoteTagSource",
    "candidate_file_pattern",
    "collect_note_tag_sources",
    "matched_note_file_names",
    "note_file_pattern_matches",
]
