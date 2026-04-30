"""标准名术语数据模型。"""

from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictNameContextModel(BaseModel):
    """标准名术语严格模型基类。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


class NameContextRegistry(StrictNameContextModel):
    """外部 Agent 填写的术语表。"""

    speaker_names: dict[str, str] = Field(default_factory=dict)
    map_display_names: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source_names(self) -> Self:
        """确保术语表不包含空原文。"""
        for source_text in self.speaker_names:
            if not source_text.strip():
                raise ValueError("speaker_names 不能包含空原文")
        for source_text in self.map_display_names:
            if not source_text.strip():
                raise ValueError("map_display_names 不能包含空原文")
        return self


class SpeakerDialogueContext(StrictNameContextModel):
    """单个名字对应的对白样本。"""

    name: str
    dialogue_lines: list[str] = Field(default_factory=list)


__all__: list[str] = [
    "NameContextRegistry",
    "SpeakerDialogueContext",
]
