"""
核心数据模型定义模块。

这里集中放置 RPG Maker 项目在提取、翻译流程中会复用的纯数据模型与常量。
文件读取、目录扫描、plugins.js 解析等实例化逻辑统一放到同目录下的 loader 模块中，
避免 `schemas.py` 混入 I/O 和构造流程。
"""

import re
from enum import IntEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from .game_data import BaseItem, CommonEvent, MapData, System, Troop

CONTROL_CHARS_PATTERN = re.compile(
    r"\\([A-Za-z]+)(?:\[([^\]]*)\])?|\\([^\w\s])|%([0-9]+)"
)
# 日文字符匹配模式
JAPANESE_PATTERN = re.compile(r"[\u3040-\u309F\u30A0-\u30FF]")
# 连续日文片段匹配模式
JAPANESE_SEGMENT_PATTERN = re.compile(r"[\u3040-\u309F\u30A0-\u30FF]+")

# 允许保留的日文字符（不视为未翻译）
ALLOWED_JAPANESE_CHARS: set[str] = {
    "っ",  # 促音
    "゛",  # 浊点
    "゜",  # 半浊点
    "・",  # 中点
    "ー",  # 长音
    "〜",  # 波浪号
    "～",  # 全角波浪号
}

# 允许保留的拟声尾音字符。
# 这类字符常作为中文拟声词的拖尾出现，例如“姆啾ぅ”“啾啪ぁ”“咿ッッ”。
# 只有当一整段连续日文片段都由这些尾音字符组成时，才会在残留校验时放行。
ALLOWED_JAPANESE_TAIL_CHARS: set[str] = {
    "ぁ",
    "ぃ",
    "ぅ",
    "ぇ",
    "ぉ",
    "ァ",
    "ィ",
    "ゥ",
    "ェ",
    "ォ",
    "ッ",
    "う",
    "ウ",
}

type ItemType = Literal["long_text", "array", "short_text"]
type ErrorType = Literal["AI漏翻", "控制符不匹配", "日文残留"]
type TranslationErrorItem = tuple[
    str,
    ItemType,
    str | None,
    list[str],
    list[str],
    ErrorType,
    list[str],
]


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

        def replace_func(match: re.Match[str]) -> str:
            """
            对 original_data 中的 RM 文本控制符替换成自定义占位符，
            并构建 placeholder_map 和 placeholder_counts。
            """
            nonlocal symbol_counter
            original: str = match.group(0)

            code: str | None = match.group(1)
            param: str | None = match.group(2)
            symbol: str | None = match.group(3)
            percent_num: str | None = match.group(4)

            placeholder: str = ""

            if code:
                suffix = param if param is not None else "0"
                placeholder = f"[{code.upper()}_{suffix}]"
            elif symbol:
                symbol_counter += 1
                placeholder = f"[S_{symbol_counter}]"
                while placeholder in self.placeholder_map:
                    symbol_counter += 1
                    placeholder = f"[S_{symbol_counter}]"
            elif percent_num:
                placeholder = f"[P_{percent_num}]"
            if placeholder not in self.placeholder_map:
                self.placeholder_map[placeholder] = original

            if placeholder not in self.placeholder_counts:
                self.placeholder_counts[placeholder] = 0
            self.placeholder_counts[placeholder] += 1
            return placeholder

        self.original_lines_with_placeholders = [
            CONTROL_CHARS_PATTERN.sub(replace_func, line)
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

    def check_residual(self) -> None:
        """
        检查翻译后的文本列表是否有日文残留。

        遍历每个文本项，按连续片段匹配日文字符。
        过滤普通白名单字符后，如果整段只剩拟声尾音字符，则视为可接受的中文拟声拖尾；
        否则判定为真实日文残留并返回错误信息。

        Args:
            translated_items: 翻译后的文本列表（行列表或选项列表）

        Returns:
            发现日文残留时返回错误信息，否则返回 None
        """
        for i, item in enumerate(self.translation_lines):
            segments = JAPANESE_SEGMENT_PATTERN.findall(item)
            if not segments:
                continue
            real_residual: list[str] = []
            for segment in segments:
                filtered_segment: list[str] = [
                    char for char in segment if char not in ALLOWED_JAPANESE_CHARS
                ]
                if not filtered_segment:
                    continue
                if all(
                    char in ALLOWED_JAPANESE_TAIL_CHARS for char in filtered_segment
                ):
                    continue
                real_residual.extend(filtered_segment)
            if real_residual:
                raise ValueError(f"发现日文残留(第 {i + 1} 项): {real_residual}")


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
    plugins_js: list[dict[str, Any]]
    writable_plugins_js: list[dict[str, Any]]


__all__: list[str] = [
    "ALLOWED_JAPANESE_CHARS",
    "ALLOWED_JAPANESE_TAIL_CHARS",
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
    "JAPANESE_PATTERN",
    "JAPANESE_SEGMENT_PATTERN",
    "JS_DIRECTORY_NAME",
    "MAP_INFOS_FILE_NAME",
    "MAP_PATTERN",
    "Place",
    "PLUGINS_FILE_NAME",
    "PLUGINS_ORIGIN_FILE_NAME",
    "PLUGINS_JS_PATTERN",
    "Role",
    "SYSTEM_FILE_NAME",
    "TROOPS_FILE_NAME",
    "TranslationData",
    "TranslationErrorItem",
    "TranslationItem",
]
