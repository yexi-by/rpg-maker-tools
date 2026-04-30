"""标准名上下文数据模型。"""

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

NAME_CONTEXT_SCHEMA_VERSION = 1

type NameEntryKind = Literal["speaker_name", "map_display_name"]


class StrictNameContextModel(BaseModel):
    """标准名上下文严格模型基类。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


class NameLocation(StrictNameContextModel):
    """标准名在游戏数据中的一次出现位置。"""

    location_path: str
    file_name: str
    map_display_name: str | None = None
    event_id: int | None = None
    event_name: str | None = None
    page_index: int | None = None
    command_index: int | None = None
    context_file: str | None = None


class NameRegistryEntry(StrictNameContextModel):
    """大 JSON 中的一条外部标准名记录。"""

    entry_id: str
    kind: NameEntryKind
    source_text: str
    translated_text: str = ""
    locations: list[NameLocation] = Field(default_factory=list)
    note: str = ""


class NameContextRegistry(StrictNameContextModel):
    """外部 Agent 填写的大 JSON 根对象。"""

    schema_version: int = NAME_CONTEXT_SCHEMA_VERSION
    game_title: str
    generated_at: str
    entries: list[NameRegistryEntry] = Field(default_factory=list)


class SpeakerDialogueContext(StrictNameContextModel):
    """单个 `101` 名字框对应的小 JSON 对话上下文。"""

    schema_version: int = NAME_CONTEXT_SCHEMA_VERSION
    entry_id: str
    source_text: str
    location: NameLocation
    dialogue_lines: list[str] = Field(default_factory=list)


__all__: list[str] = [
    "NAME_CONTEXT_SCHEMA_VERSION",
    "NameContextRegistry",
    "NameEntryKind",
    "NameLocation",
    "NameRegistryEntry",
    "SpeakerDialogueContext",
]
