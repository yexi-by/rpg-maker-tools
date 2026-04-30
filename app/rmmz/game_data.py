"""
RPG Maker 原始数据结构模型模块。

这里定义与 RPG Maker MZ 标准 `data/*.json` 高度对应的基础模型，供加载、
提取和回写流程共享。
"""

from typing import cast

from pydantic import BaseModel, Field, model_validator

from app.rmmz.text_rules import JsonValue


class BaseItem(BaseModel):
    """RPG Maker 数据库基础条目通用模型。"""

    id: int
    name: str
    note: str = ""
    nickname: str = ""
    profile: str = ""
    description: str = ""
    message1: str = ""
    message2: str = ""
    message3: str = ""
    message4: str = ""


class EventCommand(BaseModel):
    """RPG Maker 事件指令模型。"""

    code: int
    parameters: list[JsonValue]

    @model_validator(mode="before")
    @classmethod
    def normalize_end_command_parameters(cls, data: object) -> object:
        """为少量省略 `parameters` 的结束指令补齐空数组。"""
        if not isinstance(data, dict):
            return data
        raw_data = cast(dict[object, object], data)
        if raw_data.get("code") != 0 or "parameters" in raw_data:
            return raw_data
        normalized_data: dict[object, object] = dict(raw_data)
        normalized_data["parameters"] = []
        return normalized_data


class Page(BaseModel):
    """事件页模型。"""

    commands: list[EventCommand] = Field(..., alias="list")


class Event(BaseModel):
    """地图事件模型。"""

    id: int
    name: str
    note: str
    pages: list[Page]


class MapData(BaseModel):
    """地图数据模型，对应 `data/MapXXX.json`。"""

    displayName: str
    note: str
    events: list[Event | None]


class Terms(BaseModel):
    """系统基础词汇模型。"""

    basic: list[str]
    commands: list[str | None]
    params: list[str]
    messages: dict[str, str]


class System(BaseModel):
    """系统全局配置模型，对应 `data/System.json`。"""

    gameTitle: str
    terms: Terms
    elements: list[str]
    skillTypes: list[str]
    weaponTypes: list[str]
    armorTypes: list[str]
    equipTypes: list[str]


class Troop(BaseModel):
    """敌群战役模型，对应 `data/Troops.json`。"""

    id: int
    pages: list[Page]


class CommonEvent(BaseModel):
    """全局公共事件模型，对应 `data/CommonEvents.json`。"""

    id: int
    commands: list[EventCommand] = Field(..., alias="list")


__all__: list[str] = [
    "BaseItem",
    "CommonEvent",
    "Event",
    "EventCommand",
    "MapData",
    "Page",
    "System",
    "Terms",
    "Troop",
]
