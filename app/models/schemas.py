"""
核心数据模型定义模块。

这里集中放置 RPG Maker 项目在提取、翻译流程中会复用的纯数据模型与常量。
文件读取、目录扫描、plugins.js 解析等实例化逻辑统一放到同目录下的 loader 模块中，
避免 `schemas.py` 混入 I/O 和构造流程。
"""

import re
from enum import IntEnum
from typing import Any, Callable, Literal, Self

from pydantic import BaseModel, Field, model_validator

from .game_data import BaseItem, CommonEvent, MapData, QuestEntry, System, Troop

CONTROL_CHARS_PATTERN = re.compile(
    r"\\([A-Za-z]+)(?:\[([^\]]*)\])?|\\([^\w\s])|%([0-9]+)"
)
SIMPLE_CONTROL_PARAM_PATTERN = re.compile(r"[A-Za-z0-9_]+")
type ItemType = Literal["long_text", "array", "short_text"]
type SourceLanguage = Literal["ja", "en"]
type ErrorType = Literal["AI漏翻", "控制符不匹配", "源语言残留"]
type TranslationErrorItem = tuple[
    str,
    ItemType,
    str | None,
    list[str],
    list[str],
    ErrorType,
    list[str],
]

SOURCE_LANGUAGE_VALUES: tuple[SourceLanguage, ...] = ("ja", "en")
type ControlSequenceKind = Literal["code", "symbol", "percent"]
type ControlSequenceSpan = tuple[
    int,
    int,
    str,
    ControlSequenceKind,
    str | None,
    str | None,
    bool,
]


def _find_matching_delimiter_end(
    text: str,
    start_index: int,
    open_char: str,
    close_char: str,
) -> int | None:
    """
    在字符串中查找成对分隔符的闭合位置。

    这里专门用于识别 RPG Maker 控制符参数，尤其是 `\\N[\\V[36]]`
    与 `\\js<...>`、`\\js[...]` 这类带嵌套或脚本表达式的复杂控制符。

    Args:
        text: 当前正在扫描的整行文本。
        start_index: 开始匹配的开分隔符位置。
        open_char: 开分隔符字符。
        close_char: 闭分隔符字符。

    Returns:
        成功闭合时返回闭分隔符下标；未找到闭合位置时返回 `None`。
    """
    if open_char == "<" and close_char == ">":
        return _find_angle_delimiter_end(
            text=text,
            start_index=start_index,
        )

    depth: int = 0
    active_quote: str | None = None

    for index in range(start_index, len(text)):
        char = text[index]
        previous_char = text[index - 1] if index > start_index else ""

        if active_quote is not None:
            if char == active_quote and previous_char != "\\":
                active_quote = None
            continue

        if char in {'"', "'"} and previous_char != "\\":
            active_quote = char
            continue

        if char == open_char:
            depth += 1
            continue

        if char == close_char:
            depth -= 1
            if depth == 0:
                return index

    return None


def _find_angle_delimiter_end(
    text: str,
    start_index: int,
) -> int | None:
    """
    查找 `\\js<...>` 这类角括号参数的闭合位置。

    这里不能像中括号那样按深度统计 `<` 与 `>`，
    因为脚本表达式里常见比较运算符 `<`、`<=`、`>`、`>=`。
    如果把这些运算符也计入嵌套层级，就会错误地把
    `\\js<$gameVariables.value(624) < 0 ? 4 : 1>` 判定为“未闭合”。

    Args:
        text: 当前正在扫描的整行文本。
        start_index: 起始 `<` 的下标。

    Returns:
        首个未落在字符串字面量内的 `>` 下标；未找到时返回 `None`。
    """
    active_quote: str | None = None

    for index in range(start_index + 1, len(text)):
        char = text[index]
        previous_char = text[index - 1]

        if active_quote is not None:
            if char == active_quote and previous_char != "\\":
                active_quote = None
            continue

        if char in {'"', "'"} and previous_char != "\\":
            active_quote = char
            continue

        if char == ">":
            return index

    return None


def _iter_control_sequence_spans(text: str) -> list[ControlSequenceSpan]:
    """
    顺序扫描一行文本，识别其中的 RPG Maker 控制符片段。

    与单条正则不同，这里显式处理了以下复杂情况：
    1. `\\js<...>` 这类角括号脚本表达式。
    2. `\\js[...]` 与 `\\N[\\V[36]]` 这类带嵌套中括号的参数。
    3. `\\{`、`\\.` 这类符号型控制符。
    4. `%1` 这类百分号占位符。

    Args:
        text: 待扫描原文。

    Returns:
        识别出的控制符区间列表，按出现顺序排列。
    """
    spans: list[ControlSequenceSpan] = []
    index: int = 0

    while index < len(text):
        current_char = text[index]

        if current_char == "%":
            end_index: int = index + 1
            while end_index < len(text) and text[end_index].isdigit():
                end_index += 1
            if end_index > index + 1:
                original = text[index:end_index]
                spans.append(
                    (index, end_index, original, "percent", None, original[1:], False)
                )
                index = end_index
                continue

        if current_char != "\\" or index + 1 >= len(text):
            index += 1
            continue

        next_char = text[index + 1]
        if next_char.isalpha():
            code_end: int = index + 1
            while code_end < len(text) and text[code_end].isalpha():
                code_end += 1

            code: str = text[index + 1 : code_end]
            if code_end < len(text) and text[code_end] in {"[", "<"}:
                open_char = text[code_end]
                close_char = "]" if open_char == "[" else ">"
                match_end = _find_matching_delimiter_end(
                    text=text,
                    start_index=code_end,
                    open_char=open_char,
                    close_char=close_char,
                )
                if match_end is not None:
                    original = text[index : match_end + 1]
                    param = text[code_end + 1 : match_end]
                    is_complex = (
                        open_char == "<"
                        or "\\" in param
                        or "[" in param
                        or "]" in param
                        or "<" in param
                        or ">" in param
                        or not SIMPLE_CONTROL_PARAM_PATTERN.fullmatch(param)
                    )
                    spans.append(
                        (
                            index,
                            match_end + 1,
                            original,
                            "code",
                            code,
                            param,
                            is_complex,
                        )
                    )
                    index = match_end + 1
                    continue

            original = text[index:code_end]
            spans.append(
                (index, code_end, original, "code", code, None, False)
            )
            index = code_end
            continue

        original = text[index : index + 2]
        spans.append((index, index + 2, original, "symbol", None, None, False))
        index += 2

    return spans


def replace_rm_control_sequences(
    text: str,
    replacer: Callable[[ControlSequenceSpan], str],
) -> str:
    """
    按顺序替换文本中的 RPG Maker 控制符。

    Args:
        text: 原始文本。
        replacer: 针对每个识别出的控制符区间返回替换结果的回调。

    Returns:
        完成替换后的新文本。
    """
    spans = _iter_control_sequence_spans(text)
    if not spans:
        return text

    parts: list[str] = []
    last_end: int = 0
    for span in spans:
        start_index, end_index = span[0], span[1]
        parts.append(text[last_end:start_index])
        parts.append(replacer(span))
        last_end = end_index
    parts.append(text[last_end:])
    return "".join(parts)


def strip_rm_control_sequences(text: str) -> str:
    """
    从文本中剥离所有 RPG Maker 控制符与复杂脚本控制串。

    该函数用于源语言残留检测与“仅占位符文本”判断，
    目的是避免把 `\\js<...>` 内部的变量名、脚本标识符误当成正文英文。

    Args:
        text: 待清洗文本。

    Returns:
        去除控制符后的纯文本。
    """
    return replace_rm_control_sequences(text, lambda _span: "")


class Code(IntEnum):
    """RPG Maker 常用事件指令代码枚举。"""

    NAME = 101
    TEXT = 401
    CHOICES = 102
    SCROLL_TEXT = 405
    PLUGIN_TEXT = 357


class BaseGlossary(BaseModel):
    """
    结构化术语基础模型。

    用于承接所有具备“原名 + 译名”二元结构的术语条目，
    充当基类，供角色名、地点名等具有额外属性的子域术语复用。

    Attributes:
        name: 术语在游戏内的原始名称。
        translated_name: 经过 LLM 翻译后的中文名称。
    """

    name: str
    translated_name: str


class Role(BaseGlossary):
    """
    角色术语模型。

    专门用于承载人物角色的术语。在基础“原名+译名”之上，
    增加了“性别”属性。通过保留性别上下文，可以在随后的正文翻译中，
    指导 LLM 采用更加符合角色身份的语气（如男性的硬朗或女性的柔和）。

    Attributes:
        gender: 从角色对话上下文中提取出的性别标识。
    """

    gender: Literal["男", "女", "未知"]


class Place(BaseGlossary):
    """
    地点术语模型。

    当前专门用于承接地图的显示名称翻译结果，
    使用基础的二元结构（原名+译名）即可满足回写和替换需求。
    """


class GlossaryBuildChunk(BaseModel):
    """
    术语构建的流式分块模型。

    当 `build_glossary()` 逐块构建大术语表时，通过 `yield` 返回此模型。
    这样界面层能够持续刷新进度，而不必等到整个庞大字典返回才更新。

    Attributes:
        kind: 当前分块包含的是角色术语还是地点术语。
        items: 具体的结构化术语列表。
    """

    kind: Literal["roles", "places"]
    items: list[Role] | list[Place]


class Glossary(BaseModel):
    """
    结构化术语表聚合模型。

    统一承接角色术语与地点术语的完整结构化结果，
    供数据库、正文翻译、错误重翻与回写流程共享。
    """

    roles: list[Role] = Field(default_factory=list)
    places: list[Place] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_names(self) -> Self:
        """
        校验角色名和地点名在各自集合内不重复。

        Returns:
            当前术语表对象自身。

        Raises:
            ValueError: 角色名或地点名出现重复时抛出。
        """
        role_names: list[str] = [role.name for role in self.roles]
        if len(role_names) != len(set(role_names)):
            raise ValueError("Glossary.roles 中存在重复角色名")

        place_names: list[str] = [place.name for place in self.places]
        if len(place_names) != len(set(place_names)):
            raise ValueError("Glossary.places 中存在重复地点名")

        return self

    def role_map(self) -> dict[str, str]:
        """
        将角色术语转换为原名到译名的映射。

        Returns:
            角色原名到译名的字典映射。
        """
        return {role.name: role.translated_name for role in self.roles}

    def place_map(self) -> dict[str, str]:
        """
        将地点术语转换为原名到译名的映射。

        Returns:
            地点原名到译名的字典映射。
        """
        return {place.name: place.translated_name for place in self.places}

    def find_hit_roles(self, texts: list[str]) -> list[Role]:
        """
        根据文本列表筛出命中的角色术语。

        Args:
            texts: 当前批次需要命中检测的文本集合。

        Returns:
            按术语表原始顺序保留的命中角色对象列表。
        """
        return [
            role
            for role in self.roles
            if any(role.name in text for text in texts if text)
        ]

    def find_hit_places(self, texts: list[str]) -> list[Place]:
        """
        根据文本列表筛出命中的地点术语。

        Args:
            texts: 当前批次需要命中检测的文本集合。

        Returns:
            按术语表原始顺序保留的命中地点对象列表。
        """
        return [
            place
            for place in self.places
            if any(place.name in text for text in texts if text)
        ]


class TranslationItem(BaseModel):
    """
    单个翻译条目。

    该类承载了游戏正文中最小的翻译单元（一段长文本、一组选项或一段短文本）。
    它封装了控制符的占位替换与还原逻辑，能够确保 LLM 在翻译时不会破坏 RM 控制符结构。

    字段语义约定：
    1. `original_lines`: 永远保存提取阶段得到的原始文本，绝不允许被修改或覆盖。
    2. `original_lines_with_placeholders`: 经过预处理，将 RM 控制符替换为简短的自定义占位符后的原文，这是发送给模型的实际内容。
    3. `translation_lines_with_placeholders`: 保存模型返回但尚未恢复控制符的中间态译文。
    4. `translation_lines`: 将占位符恢复为 RM 控制符后的最终译文，这也是最终写入数据库和回写文件的目标文本。
    """

    role: str | None = None
    location_path: str
    item_type: ItemType
    original_lines: list[str] = Field(default_factory=list)
    original_lines_with_placeholders: list[str] = Field(default_factory=list)
    translation_lines_with_placeholders: list[str] = Field(default_factory=list)
    translation_lines: list[str] = Field(default_factory=list)
    placeholder_map: dict[str, str] = Field(default_factory=dict)
    placeholder_counts: dict[str, int] = Field(default_factory=dict)

    def build_placeholders(self) -> None:
        """
        为原文中的 RM 控制符构建简短占位符。

        LLM 经常在翻译时破坏或漏掉 RPG Maker 复杂的控制符（如 \\C[2], \\V[1] 等）。
        此方法会在请求 LLM 前，将原文中所有识别到的控制符替换成更具结构化特征的占位符，
        从而提升翻译的稳定性。

        步骤 1: 清空已有的占位符映射。
        步骤 2: 遍历 `original_lines`。
        步骤 3: 使用正则表达式匹配 RM 控制符，将其转化为 `[C_2]`, `[S_1]` 这种形式的占位符。
        步骤 4: 将替换结果存入 `original_lines_with_placeholders`。
        """
        self.original_lines_with_placeholders.clear()
        self.placeholder_map.clear()
        self.placeholder_counts.clear()
        symbol_counter: int = 0
        complex_control_counter: int = 0
        complex_placeholder_map: dict[str, str] = {}

        def replace_func(span: ControlSequenceSpan) -> str:
            """
            对 original_data 中的 RM 文本控制符替换成自定义占位符，
            并构建 placeholder_map 和 placeholder_counts。
            """
            nonlocal symbol_counter, complex_control_counter
            (
                _start_index,
                _end_index,
                original,
                kind,
                code,
                param,
                is_complex,
            ) = span

            placeholder: str = ""

            if kind == "percent":
                if param is None:
                    raise ValueError(f"百分号控制符缺少参数: {original}")
                placeholder = f"[P_{param}]"
            elif kind == "symbol":
                symbol_counter += 1
                placeholder = f"[S_{symbol_counter}]"
                while placeholder in self.placeholder_map:
                    symbol_counter += 1
                    placeholder = f"[S_{symbol_counter}]"
            elif code is not None:
                if is_complex:
                    existing_placeholder = complex_placeholder_map.get(original)
                    if existing_placeholder is not None:
                        placeholder = existing_placeholder
                    else:
                        complex_control_counter += 1
                        placeholder = f"[RM_{complex_control_counter}]"
                        complex_placeholder_map[original] = placeholder
                else:
                    suffix = param if param is not None else "0"
                    placeholder = f"[{code.upper()}_{suffix}]"

            if placeholder not in self.placeholder_map:
                self.placeholder_map[placeholder] = original

            if placeholder not in self.placeholder_counts:
                self.placeholder_counts[placeholder] = 0
            self.placeholder_counts[placeholder] += 1
            return placeholder

        self.original_lines_with_placeholders = [
            replace_rm_control_sequences(line, replace_func)
            for line in self.original_lines
        ]

    def verify_placeholders(self) -> None:
        """
        对 `translation_lines_with_placeholders` 的自定义占位符总量进行校验。
        如果校验失败，抛出 ValueError 包含详细的错误信息。
        """
        if not self.placeholder_map:
            return None
        errors: list[str] = []
        combined_text: str = "".join(self.translation_lines_with_placeholders).lower()
        for placeholder, expected_count in self.placeholder_counts.items():
            actual_count: int = combined_text.count(placeholder.lower())
            if actual_count != expected_count:
                errors.append(
                    f"占位符 {placeholder} 数量错误 (期望: {expected_count}, 实际: {actual_count})"
                )
        if errors:
            raise ValueError(";\n".join(errors))

    def restore_placeholders(self) -> None:
        """
        将 `translation_lines_with_placeholders` 中的自定义占位符还原成 RM 文本控制符。

        恢复后的最终结果会写入 `translation_lines`，而不是覆盖中间态译文。
        """
        if not self.translation_lines_with_placeholders:
            self.translation_lines = []
            return None

        if not self.placeholder_map:
            self.translation_lines = list(self.translation_lines_with_placeholders)
            return None

        sorted_placeholders: list[str] = sorted(
            self.placeholder_map.keys(), key=len, reverse=True
        )
        new_translation_lines: list[str] = []
        for line in self.translation_lines_with_placeholders:
            for placeholder in sorted_placeholders:
                original_code = self.placeholder_map[placeholder]
                pattern = re.compile(re.escape(placeholder), re.IGNORECASE)
                # python3.14里\N 被作为输出特殊 Unicode 字符 需要用lambda 放弃对其的解析
                line = pattern.sub(lambda _: original_code, line)
            new_translation_lines.append(line)
        self.translation_lines = new_translation_lines

class TranslationData(BaseModel):
    """
    单个文件维度的翻译数据集合。

    用于聚合提取阶段得到的同一文件内的所有 `TranslationItem`，
    并记录该文件可能存在的上下文（如地图显示名称）。
    """

    display_name: str | None
    translation_items: list[TranslationItem] = Field(default_factory=list)


# ==================== 文件名常量 ====================


class ErrorRetryItem(BaseModel):
    """
    错误表重翻译条目。

    承载了之前翻译失败的所有上下文状态（包括旧译文、错误类型、具体错误详情），
    作为重新构建 prompt 和执行翻译的重要输入。
    """

    translation_item: TranslationItem
    previous_translation_lines: list[str] = Field(default_factory=list)
    error_type: ErrorType
    error_detail: list[str] = Field(default_factory=list)


DATA_DIRECTORY_NAME: str = "data"
DATA_ORIGIN_DIRECTORY_NAME: str = "data_origin"
JS_DIRECTORY_NAME: str = "js"
SYSTEM_FILE_NAME: str = "System.json"
PLUGINS_FILE_NAME: str = "plugins.js"
PLUGINS_ORIGIN_FILE_NAME: str = "plugins_origin.js"
QUESTS_FILE_NAME: str = "Quests.json"
COMMON_EVENTS_FILE_NAME: str = "CommonEvents.json"
TROOPS_FILE_NAME: str = "Troops.json"
MAP_INFOS_FILE_NAME: str = "MapInfos.json"

# plugins.js 提取模式
PLUGINS_JS_PATTERN: re.Pattern[str] = re.compile(
    r"var\s+\$plugins\s*=\s*(\[.*?\])\s*;\s*$", re.DOTALL | re.MULTILINE
)

# 地图文件名匹配模式（如 Map001.json）
MAP_PATTERN: re.Pattern[str] = re.compile(r"Map\d+\.json")

# 所有已知的固定文件名集合
FIXED_FILE_NAMES: set[str] = {
    "Actors.json",
    "Animations.json",
    "Armors.json",
    "Classes.json",
    "Enemies.json",
    "Items.json",
    "Skills.json",
    "States.json",
    "Weapons.json",
    "Tilesets.json",
    QUESTS_FILE_NAME,
    MAP_INFOS_FILE_NAME,
    COMMON_EVENTS_FILE_NAME,
    TROOPS_FILE_NAME,
    SYSTEM_FILE_NAME,
}


class GameData(BaseModel):
    """
    游戏数据聚合模型。

    将 RPG Maker 游戏的 data/ 目录下所有 JSON 文件解析后，
    按类型分类存储为强类型字段，供后续翻译提取和写回使用。

    Attributes:
        data: 原始 JSON 数据映射，键为文件名，值为解析后的原始对象。
        writable_data: 用于回写流程的原始数据副本。所有回写都应优先修改这里，避免污染原始数据。
        map_data: 地图数据字典，键为文件名（如 Map001.json），值为 MapData 模型。
        system: System.json 对应的系统配置模型。
        common_events: CommonEvents.json 解析后的公共事件列表（稀疏数组，索引 0 为 None）。
        troops: Troops.json 解析后的敌群列表（稀疏数组，索引 0 为 None）。
        base_data: 其余数据库文件（Actors/Items/Skills 等），键为文件名，值为条目列表。
        quests: 可选的 `Quests.json` 结构化任务数据。文件不存在时为 `None`。
        plugins_js: plugins.js 清洗后的插件列表，用于后续递归提取插件参数文本。
        writable_plugins_js: 用于插件文本回写的插件列表副本。修改完成后会重新序列化回 `writable_data["plugins.js"]`。
    """

    data: dict[str, Any]
    writable_data: dict[str, Any]
    map_data: dict[str, MapData]
    system: System
    common_events: list[CommonEvent | None]
    troops: list[Troop | None]
    base_data: dict[str, list[BaseItem | None]]
    quests: dict[str, QuestEntry] | None = None
    plugins_js: list[dict[str, Any]]
    writable_plugins_js: list[dict[str, Any]]


__all__: list[str] = [
    "BaseGlossary",
    "Code",
    "COMMON_EVENTS_FILE_NAME",
    "CONTROL_CHARS_PATTERN",
    "DATA_DIRECTORY_NAME",
    "DATA_ORIGIN_DIRECTORY_NAME",
    "ErrorType",
    "FIXED_FILE_NAMES",
    "GameData",
    "Glossary",
    "GlossaryBuildChunk",
    "ItemType",
    "JS_DIRECTORY_NAME",
    "MAP_INFOS_FILE_NAME",
    "MAP_PATTERN",
    "Place",
    "PLUGINS_FILE_NAME",
    "PLUGINS_ORIGIN_FILE_NAME",
    "QUESTS_FILE_NAME",
    "PLUGINS_JS_PATTERN",
    "Role",
    "SOURCE_LANGUAGE_VALUES",
    "SourceLanguage",
    "SYSTEM_FILE_NAME",
    "TROOPS_FILE_NAME",
    "TranslationData",
    "TranslationErrorItem",
    "TranslationItem",
    "replace_rm_control_sequences",
    "strip_rm_control_sequences",
]
