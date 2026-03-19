"""
RPG Maker 原始数据结构模型模块。

这里定义的是与游戏原始 JSON 结构高度对应的基础模型，
用于承接 `data/` 目录及相关文件在解析后的结构化结果。
"""

from typing import Any

from pydantic import BaseModel, Field, model_validator


class BaseItem(BaseModel):
    """
    RPG Maker 数据库基础条目通用模型。
    
    提取层会将所有游戏基础设定（如 Actors.json，Skills.json 等）中的单个对象
    统一映射到此模型。它囊括了所有可能包含日文文本供翻译的基础字段。
    
    Attributes:
        id: 数据条目的唯一标识符 ID，提取和回写时用作重要的索引锚点。
        name: 条目名称（如技能名、物品名），常作为短文本翻译。
        note: 备注字段。通常供插件开发者存放指令参数，偶尔包含文本。
        nickname: 角色特有的昵称。
        profile: 角色特有的个人简介，属于短文本。
        description: 物品或技能的描述说明文本。
        message1: 战斗消息提示段 1（如：使用了XX技能）。
        message2: 战斗消息提示段 2。
        message3: 战斗消息提示段 3。
        message4: 战斗消息提示段 4。
    """

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
    """
    RPG Maker 事件指令（Event Command）模型。

    游戏对话与逻辑是由一个接一个的指令构成的。该模型直接映射了每个指令对象。
    在翻译流程中，主要关注 code 为 101 (角色名) 和 401 (正文) 的指令。

    Attributes:
        code: 指令的数字代码，标识了指令行为。如 101 为 NAME 指令，401 为 TEXT 指令。
        parameters: 动态长度的数组，其元素类型和数量取决于 code 的定义。翻译文本通常存储在数组的首位或末位。
    """

    code: int
    parameters: list[int | str | dict | list | Any]

    @model_validator(mode="before")
    @classmethod
    def normalize_end_command_parameters(cls, data: Any) -> Any:
        """
        兼容少量缺失 `parameters` 的事件结束指令。

        设计意图：
            RPG Maker 的事件指令 `code == 0` 表示当前指令列表结束。
            正常情况下它通常会带一个空数组 `parameters: []`，但部分解包或导出后的游戏数据
            会把这个空数组字段省略掉。这里仅对这一个明确可判定的结束指令做补全，
            其他指令依旧保持严格校验，避免把真实脏数据静默吞掉。

        参数:
            data: Pydantic 在模型构建前收到的原始事件命令对象。

        返回:
            Any: 对 `code == 0` 且缺少 `parameters` 的字典补上空数组后的对象；
            其他输入保持原样返回。
        """

        if not isinstance(data, dict):
            return data
        if data.get("code") != 0:
            return data
        if "parameters" in data:
            return data
        normalized_data = dict(data)
        normalized_data["parameters"] = []
        return normalized_data


class Page(BaseModel):
    """
    事件页（Event Page）模型。
    
    在 RPG Maker 中，事件（包括地图事件与战斗事件）是由多张带有触发条件的“页面”组成的。
    每一个 Page 都拥有自己独立的指令流序列。

    Attributes:
        commands: 该事件页承载的全部指令流。由于 JSON 源数据中键名为 `list`，这里通过 alias 进行映射以便于语义理解。
    """

    commands: list[EventCommand] = Field(..., alias="list")


class Event(BaseModel):
    """
    地图事件（Map Event）模型。
    
    代表了放置在特定地图上的单一交互实体（如一个 NPC）。
    它由多个可能随时切换的页面（Page）组成。

    Attributes:
        id: 地图内唯一的事件标识符。
        name: 创作者在编辑器中为事件起的别名（一般不作为游戏文本显示）。
        note: 事件级的备注文本。
        pages: 组成该事件的所有页面。
    """

    id: int
    name: str
    note: str
    pages: list[Page]


class MapData(BaseModel):
    """
    地图数据（Map Data）模型。
    
    对应 `data/MapXXX.json`，是容纳地图属性及所有地图事件的顶层容器。

    Attributes:
        displayName: 地图在游戏屏幕左上角的 UI 显示名称，这是术语表中“地点名”提取的核心来源。
        note: 地图级备注。
        events: 存放该地图上所有事件的稀疏数组。索引 0 永远为 None，其它为空的槽位代表该 ID 的事件已被开发者删除。
    """

    displayName: str
    note: str
    events: list[Event | None]


class Terms(BaseModel):
    """
    系统基础词汇（Terms）模型。
    
    对应 System.json 中的 terms 字段。
    包含构成整个游戏菜单骨架的各类基础 UI 词汇和战斗模板字符串。

    Attributes:
        basic: 基础状态缩写与全称列表（如 Lv, HP）。
        commands: 各类主菜单或战斗菜单的指令词（如 攻击、防御）。
        params: 角色属性面板使用的参数名。
        messages: 系统固定触发的战斗或获得物品消息的带格式占位符文本。
    """

    basic: list[str]
    commands: list[str | None]
    params: list[str]
    messages: dict[str, str]


class System(BaseModel):
    """
    系统全局配置（System）模型。
    
    对应整个游戏唯一的 `data/System.json`。
    它掌管着游戏最核心的基础分类定义以及 UI 词汇。这里的每一个分类名称都必须作为短文本提交给大模型翻译。

    Attributes:
        gameTitle: 全局游戏标题（显示在窗口边框）。
        terms: 基础 UI 词汇表。
        elements: 全局元素/属性类型列表（如 火、水）。
        skillTypes: 全局技能分类列表。
        weaponTypes: 武器分类列表。
        armorTypes: 护甲分类列表。
        equipTypes: 装备槽位列表（如 头部、饰品）。
        variables: 全局游戏变量名称（常常被插件通过名称调用，属于重灾区）。
        switches: 全局游戏开关名称。
    """

    gameTitle: str
    # currencyUnit: str
    terms: Terms
    elements: list[str]
    skillTypes: list[str]
    weaponTypes: list[str]
    armorTypes: list[str]
    equipTypes: list[str]
    variables: list[str]  
    switches: list[str]  


class Troop(BaseModel):
    """
    敌群战役（Troop）模型。
    
    对应 `data/Troops.json` 中的元素，代表了一组在战斗中遭遇的敌人集合。
    这里包含了特有的“战斗事件”（如 Boss 战开场台词、阶段转换对话等），其结构与地图事件完全一致。

    Attributes:
        id: 敌群的 ID。
        pages: 掌控该场战斗特殊流程的所有事件页。
    """

    id: int
    pages: list[Page]


class CommonEvent(BaseModel):
    """
    全局公共事件（Common Event）模型。
    
    对应 `data/CommonEvents.json` 中的元素。
    它是跨越所有地图都可以被调用的全局逻辑块，例如：使用了某个回城道具后触发的长段剧情。
    公共事件没有 Page 结构，它的顶层直接包含完整的事件指令流。

    Attributes:
        id: 公共事件 ID。
        commands: 构成整个公共事件的事件指令列表。
    """

    id: int
    commands: list[EventCommand] = Field(..., alias="list")


class QuestEntry(BaseModel):
    """
    自定义任务数据条目模型。

    对应部分游戏额外扩展出来的 `Quests.json` 文件。
    该文件不属于 RPG Maker 标准数据结构，因此单独定义轻量模型，
    只承接当前已确认需要翻译或需要保留的字段。

    Attributes:
        title_cte: 任务标题文本。
        summaries_cte: 任务摘要文本字典，键通常为字符串数字。
        rewards_cte: 任务奖励文本字典，键通常为字符串数字。
        objectives_cte: 任务目标文本字典，键通常为字符串数字。
        condition: 任务出现条件脚本。该字段只保留，不参与翻译。
        roland_quest: 特定支线标记值。该字段只保留，不参与翻译。
    """

    title_cte: str
    summaries_cte: dict[str, str]
    rewards_cte: dict[str, str]
    objectives_cte: dict[str, str]
    condition: str | None = None
    roland_quest: int | None = None
