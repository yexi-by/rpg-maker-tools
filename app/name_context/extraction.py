"""标准名术语提取模块。"""

import re

from app.rmmz.game_data import EventCommand
from app.rmmz.schema import Code, GameData

from .schemas import NameContextRegistry, SpeakerDialogueContext

SPEAKER_SAMPLE_DIRECTORY_NAME = "speaker_contexts"
ACTOR_NAME_CONTROL_PATTERN: re.Pattern[str] = re.compile(r"\\N\[\d+\]", re.IGNORECASE)
FILE_NAME_WHITESPACE_PATTERN: re.Pattern[str] = re.compile(r"\s+")
FILE_NAME_CHAR_TRANSLATION = str.maketrans(
    {
        "<": "＜",
        ">": "＞",
        ":": "：",
        '"': "＂",
        "/": "／",
        "\\": "＼",
        "|": "｜",
        "?": "？",
        "*": "＊",
    }
)


class NameContextExtraction:
    """从游戏数据中提取名字框、地图显示名和对白样本。"""

    def __init__(self, game_data: GameData) -> None:
        """初始化提取器。"""
        self.game_data: GameData = game_data

    def extract_registry_and_contexts(
        self,
    ) -> tuple[NameContextRegistry, list[SpeakerDialogueContext]]:
        """提取术语表和按名字聚合的对白样本。"""
        speaker_dialogue_map = self._collect_speaker_dialogue_map()
        speaker_names = {name: "" for name in sorted(speaker_dialogue_map)}
        map_display_names = {name: "" for name in self._collect_map_display_names()}
        contexts = [
            SpeakerDialogueContext(name=name, dialogue_lines=lines)
            for name, lines in sorted(speaker_dialogue_map.items())
        ]
        return (
            NameContextRegistry(
                speaker_names=speaker_names,
                map_display_names=map_display_names,
            ),
            contexts,
        )

    def _collect_map_display_names(self) -> list[str]:
        """收集所有非空地图显示名。"""
        display_names: set[str] = set()
        for map_data in self.game_data.map_data.values():
            source_text = map_data.displayName.strip()
            if is_translatable_name_context_source(source_text):
                display_names.add(source_text)
        return sorted(display_names)

    def _collect_speaker_dialogue_map(self) -> dict[str, list[str]]:
        """按名字框原文聚合后续对白。"""
        dialogue_map: dict[str, list[str]] = {}

        for map_data in self.game_data.map_data.values():
            for event in map_data.events:
                if event is None:
                    continue
                for page in event.pages:
                    self._append_page_dialogue(dialogue_map, page.commands)

        for common_event in self.game_data.common_events:
            if common_event is None:
                continue
            self._append_page_dialogue(dialogue_map, common_event.commands)

        for troop in self.game_data.troops:
            if troop is None:
                continue
            for page in troop.pages:
                self._append_page_dialogue(dialogue_map, page.commands)

        return dialogue_map

    def _append_page_dialogue(
        self,
        dialogue_map: dict[str, list[str]],
        commands: list[EventCommand],
    ) -> None:
        """从单个事件页收集名字框后的连续对白。"""
        for command_index, command in enumerate(commands):
            if command.code != Code.NAME:
                continue
            source_text = read_name_box_text(command)
            if source_text is None:
                continue
            lines = collect_following_dialogue_lines(commands, command_index)
            dialogue_map.setdefault(source_text, []).extend(lines)


def build_speaker_sample_file_name(name: str) -> str:
    """根据名字生成稳定且适合文件系统的对白样本文件名。"""
    normalized = name.strip().translate(FILE_NAME_CHAR_TRANSLATION)
    normalized = FILE_NAME_WHITESPACE_PATTERN.sub("_", normalized).strip("._")
    if not normalized:
        normalized = "speaker"
    return f"{normalized}.json"


def read_name_box_text(command: EventCommand) -> str | None:
    """读取 `101.parameters[4]` 名字框文本。"""
    if len(command.parameters) < 5:
        return None
    raw_name = command.parameters[4]
    if not isinstance(raw_name, str):
        return None
    source_text = raw_name.strip()
    if not is_translatable_name_context_source(source_text):
        return None
    return source_text


def is_translatable_name_context_source(source_text: str) -> bool:
    """判断标准名原文是否适合交给外部 Agent 填写译名。"""
    normalized_text = source_text.strip()
    if not normalized_text:
        return False
    return ACTOR_NAME_CONTROL_PATTERN.search(normalized_text) is None


def collect_following_dialogue_lines(commands: list[EventCommand], command_index: int) -> list[str]:
    """收集 `101` 后连续 `401` 指令的实际对白。"""
    lines: list[str] = []
    next_index = command_index + 1
    while next_index < len(commands):
        command = commands[next_index]
        if command.code != Code.TEXT:
            break
        if command.parameters and isinstance(command.parameters[0], str):
            lines.append(command.parameters[0])
        next_index += 1
    return lines


__all__: list[str] = [
    "NameContextExtraction",
    "ACTOR_NAME_CONTROL_PATTERN",
    "FILE_NAME_CHAR_TRANSLATION",
    "FILE_NAME_WHITESPACE_PATTERN",
    "SPEAKER_SAMPLE_DIRECTORY_NAME",
    "build_speaker_sample_file_name",
    "collect_following_dialogue_lines",
    "is_translatable_name_context_source",
    "read_name_box_text",
]
