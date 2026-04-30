"""
标准名上下文提取模块。

提取器扫描两类结构化来源：`101.parameters[4]` 名字框与 `MapXXX.json.displayName`。
它为外部 Agent 准备可审阅的大 JSON 与逐条对话小 JSON。
"""

import hashlib
from datetime import datetime, timezone

from app.rmmz.game_data import EventCommand
from app.rmmz.schema import Code, GameData

from .schemas import NameContextRegistry, NameLocation, NameRegistryEntry, SpeakerDialogueContext

CONTEXT_DIRECTORY_NAME = "speaker_contexts"


class NameContextExtraction:
    """从已加载游戏数据中提取外部标准名上下文。"""

    def __init__(self, game_title: str, game_data: GameData) -> None:
        """初始化提取器。"""
        self.game_title: str = game_title
        self.game_data: GameData = game_data

    def extract_registry_and_contexts(
        self,
    ) -> tuple[NameContextRegistry, list[SpeakerDialogueContext]]:
        """提取大 JSON 注册表与全部 `101` 小 JSON 对话上下文。"""
        entries: list[NameRegistryEntry] = []
        contexts: list[SpeakerDialogueContext] = []
        entries.extend(self._extract_map_display_entries())
        speaker_entries, speaker_contexts = self._extract_speaker_entries()
        entries.extend(speaker_entries)
        contexts.extend(speaker_contexts)
        return (
            NameContextRegistry(
                game_title=self.game_title,
                generated_at=datetime.now(timezone.utc).isoformat(),
                entries=entries,
            ),
            contexts,
        )

    def _extract_map_display_entries(self) -> list[NameRegistryEntry]:
        """提取所有 `MapXXX.json.displayName`。"""
        entries: list[NameRegistryEntry] = []
        for file_name, map_data in sorted(self.game_data.map_data.items()):
            source_text = map_data.displayName.strip()
            if not source_text:
                continue
            location_path = f"{file_name}/displayName"
            entries.append(
                NameRegistryEntry(
                    entry_id=build_name_entry_id(kind="map_display_name", location_path=location_path),
                    kind="map_display_name",
                    source_text=source_text,
                    locations=[
                        NameLocation(
                            location_path=location_path,
                            file_name=file_name,
                            map_display_name=source_text,
                        )
                    ],
                )
            )
        return entries

    def _extract_speaker_entries(self) -> tuple[list[NameRegistryEntry], list[SpeakerDialogueContext]]:
        """提取所有 `101` 名字框并为每次出现保存后续对白上下文。"""
        entries: list[NameRegistryEntry] = []
        contexts: list[SpeakerDialogueContext] = []

        for file_name, map_data in sorted(self.game_data.map_data.items()):
            for event in map_data.events:
                if event is None:
                    continue
                for page_index, page in enumerate(event.pages):
                    self._append_speaker_entries_from_page(
                        entries=entries,
                        contexts=contexts,
                        file_name=file_name,
                        map_display_name=map_data.displayName,
                        event_id=event.id,
                        event_name=event.name,
                        page_index=page_index,
                        commands=page.commands,
                    )

        for common_event in self.game_data.common_events:
            if common_event is None:
                continue
            self._append_speaker_entries_from_page(
                entries=entries,
                contexts=contexts,
                file_name="CommonEvents.json",
                map_display_name=None,
                event_id=common_event.id,
                event_name=None,
                page_index=None,
                commands=common_event.commands,
            )

        for troop in self.game_data.troops:
            if troop is None:
                continue
            for page_index, page in enumerate(troop.pages):
                self._append_speaker_entries_from_page(
                    entries=entries,
                    contexts=contexts,
                    file_name="Troops.json",
                    map_display_name=None,
                    event_id=troop.id,
                    event_name=None,
                    page_index=page_index,
                    commands=page.commands,
                )
        return entries, contexts

    def _append_speaker_entries_from_page(
        self,
        *,
        entries: list[NameRegistryEntry],
        contexts: list[SpeakerDialogueContext],
        file_name: str,
        map_display_name: str | None,
        event_id: int,
        event_name: str | None,
        page_index: int | None,
        commands: list[EventCommand],
    ) -> None:
        """从单个事件页中提取 `101` 名字框。"""
        for command_index, command in enumerate(commands):
            if command.code != Code.NAME:
                continue
            source_text = read_name_box_text(command)
            if source_text is None:
                continue
            location_path = build_speaker_location_path(
                file_name=file_name,
                event_id=event_id,
                page_index=page_index,
                command_index=command_index,
            )
            entry_id = build_name_entry_id(kind="speaker_name", location_path=location_path)
            context_file = f"{CONTEXT_DIRECTORY_NAME}/{entry_id}.json"
            location = NameLocation(
                location_path=location_path,
                file_name=file_name,
                map_display_name=map_display_name,
                event_id=event_id,
                event_name=event_name,
                page_index=page_index,
                command_index=command_index,
                context_file=context_file,
            )
            entries.append(
                NameRegistryEntry(
                    entry_id=entry_id,
                    kind="speaker_name",
                    source_text=source_text,
                    locations=[location],
                )
            )
            contexts.append(
                SpeakerDialogueContext(
                    entry_id=entry_id,
                    source_text=source_text,
                    location=location,
                    dialogue_lines=collect_following_dialogue_lines(commands, command_index),
                )
            )


def build_speaker_location_path(
    *,
    file_name: str,
    event_id: int,
    page_index: int | None,
    command_index: int,
) -> str:
    """构造与正文 `long_text` 条目一致的 `101` 定位路径。"""
    if page_index is None:
        return f"{file_name}/{event_id}/{command_index}"
    return f"{file_name}/{event_id}/{page_index}/{command_index}"


def build_name_entry_id(*, kind: str, location_path: str) -> str:
    """根据类型和位置生成稳定短 ID。"""
    digest = hashlib.sha1(f"{kind}:{location_path}".encode("utf-8")).hexdigest()[:12]
    safe_location = location_path.replace("/", "_").replace(".", "_")
    return f"{kind}_{safe_location}_{digest}"


def read_name_box_text(command: EventCommand) -> str | None:
    """读取 `101.parameters[4]` 名字框文本。"""
    if len(command.parameters) < 5:
        return None
    raw_name = command.parameters[4]
    if not isinstance(raw_name, str):
        return None
    source_text = raw_name.strip()
    if not source_text:
        return None
    return source_text


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
    "CONTEXT_DIRECTORY_NAME",
    "NameContextExtraction",
    "build_name_entry_id",
    "build_speaker_location_path",
    "collect_following_dialogue_lines",
    "read_name_box_text",
]
