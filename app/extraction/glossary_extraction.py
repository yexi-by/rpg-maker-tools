"""
术语表提取模块。

接受 `app.models.schemas.GameData` 作为构造函数参数，提供两类术语提取能力：

1. 提取角色名及其对话采样块，返回 `dict[str, list[str]]`。
2. 提取地图 `displayName`，返回 `dict[str, str]`，且值统一为空字符串。

两个方法都会过滤掉键本身不包含日文的字符串。
日文判断逻辑由 `app.utils.japanese_utils` 提供：

1. 严格模式：只判断平假名和片假名。
2. 非严格模式：判断平假名、片假名和汉字。
3. 日文标点符号不算命中字符。
"""

from app.models.game_data import EventCommand
from app.models.schemas import Code, GameData
from app.utils import has_japanese, iter_all_commands


class GlossaryExtraction:
    """
    术语表提取器。

    该类专门用于在翻译流程早期，从游戏数据中抽离出角色名和地图显示名等全局名词。
    为了提升角色名翻译的准确性（尤其是判别性别和语境），它还会收集每个角色的对话样本，
    并将抽取后的原始数据传递给下游进行 LLM 结构化翻译。
    """

    def __init__(self, game_data: GameData) -> None:
        """
        初始化术语表提取器。

        Args:
            game_data: 已经载入内存的全局游戏数据对象。
        """
        self.game_data: GameData = game_data

    def extract_role_dialogue_chunks(
        self,
        chunk_blocks: int,
        chunk_lines: int,
    ) -> dict[str, list[str]]:
        """
        提取游戏中所有角色的对话，并进行切块采样。

        全量提交一个主角的上万句台词给大模型显然不现实。
        此方法会在收集角色全部对话后，通过切分和采样提取出具有代表性的片段。

        Args:
            chunk_blocks: 期望将全部台词均分为多少块（段）。
            chunk_lines: 从划分出的每一块中，截取前多少行作为样本。

        Returns:
            包含角色原名及对应的对话样本列表的映射字典。
            示例：{"艾尼": ["你好！", "今天天气真好。"]}
        """
        # 步骤 1: 从指令流中收集每一个出现的角色及归属于他们的完整台词。
        role_lines: dict[str, list[str]] = self._collect_role_lines()

        # 步骤 2: 对角色对话执行分块采样
        sampled_roles: dict[str, list[str]] = {}
        for role, lines in role_lines.items():
            sampled_roles[role] = self._sample_lines(
                lines=lines,
                chunk_blocks=chunk_blocks,
                chunk_lines=chunk_lines,
            )

        # 步骤 3: 直接返回采样结果
        return sampled_roles

    def extract_display_names(self) -> dict[str, str]:
        """
        提取地图数据中的显示名称（displayName）。

        此操作会遍历所有的 `MapXXX.json` 文件，如果发现地图存在非空的、且包含日文的
        显示名，则将其记录下来准备作为地点类术语。
        目前返回结构的值默认填充为空字符串，仅作占位使用。

        Returns:
            包含地图显示原名的字典，值为占位符空字符串。示例：{"城堡": ""}
        """
        display_names: dict[str, str] = {}

        # 步骤 1: 遍历全部地图
        for map_data in self.game_data.map_data.values():
            display_name: str = map_data.displayName

            # 步骤 2: 过滤掉无效键
            if not display_name:
                continue
            if not has_japanese(text=display_name, mode="non_strict"):
                continue

            # 步骤 3: 构建值为空字符串的映射
            display_names[display_name] = ""

        return display_names

    def _collect_role_lines(self) -> dict[str, list[str]]:
        """
        顺序扫描指令流，收集角色名与对应的原始对话。

        该方法依赖 `iter_all_commands` 按确定的层级顺序吐出所有事件。
        在遍历过程中，它使用状态机机制：遇到 NAME 指令即切换当前激活的“发言人”，
        随后的 TEXT 指令会被追加给该发言人；如果指令源发生切换（比如进入了另一个事件），
        则立刻重置发言人状态，防止台词串线。

        Returns:
            由角色原名作为键，包含该角色所有正文台词列表作为值的字典。
        """
        role_lines: dict[str, list[str]] = {}
        current_role: str | None = None
        current_context: tuple[str | int, ...] | None = None

        # 步骤 1: 顺序遍历所有事件指令
        for path, _display_name, command in iter_all_commands(self.game_data):
            context_key: tuple[str | int, ...] = tuple(path[:-1])

            # 步骤 2: 如果进入了新的事件上下文，则重置当前角色
            if current_context != context_key:
                current_context = context_key
                current_role = None

            # 步骤 3: 根据指令类型分别处理
            match command.code:
                case Code.NAME:
                    role: str = self._extract_role_from_name_command(command)
                    if not has_japanese(text=role, mode="non_strict"):
                        current_role = None
                        continue

                    current_role = role
                    if current_role not in role_lines:
                        role_lines[current_role] = []

                case Code.TEXT:
                    if current_role is None:
                        continue

                    text: str = self._extract_text_from_text_command(command)
                    if not text:
                        continue

                    role_lines[current_role].append(text)

        return role_lines

    def _extract_role_from_name_command(self, command: EventCommand) -> str:
        """
        从 RM 的 NAME(101) 指令参数中抽取角色名称字符串。

        Args:
            command: 事件指令对象，code 应当为 101。

        Returns:
            去除首尾空格后的角色名。如果数据格式不符则返回空字符串。
        """
        # 步骤 1: 校验参数数量是否符合标准 RM MV/MZ 的 101 规范
        if len(command.parameters) < 5:
            return ""

        # 步骤 2: 读取角色名参数
        role_value = command.parameters[4]
        if not isinstance(role_value, str):
            return ""

        # 步骤 3: 返回去除首尾空白后的角色名
        return role_value.strip()

    def _extract_text_from_text_command(self, command: EventCommand) -> str:
        """
        从 RM 的 TEXT(401) 指令参数中抽取正文内容，并进行简单的字符清理。

        为什么在这里去除全角引号「」：
        因为在提取纯对话样本给大模型判断角色语气时，外围的括号是无用信息，
        反而可能干扰自然语言的纯粹性。保留纯正文本对后续基于样本判断性别更为有利。

        Args:
            command: 事件指令对象，code 应当为 401。

        Returns:
            清洗后的正文文本字符串。
        """
        # 步骤 1: 校验参数是否存在
        if not command.parameters:
            return ""

        # 步骤 2: 读取文本内容
        text_value = command.parameters[0]
        if not isinstance(text_value, str):
            return ""

        # 步骤 3: 清理文本中的日文引号与空白
        cleaned_text: str = text_value.replace("「", "").replace("」", "").strip()
        return cleaned_text

    def _sample_lines(
        self,
        lines: list[str],
        chunk_blocks: int,
        chunk_lines: int,
    ) -> list[str]:
        """
        对角色对话列表执行分块采样。

        步骤 1: 判断原始列表是否为空。
        步骤 2: 按 chunk_blocks 将列表均匀切块。
        步骤 3: 每块取前 chunk_lines 行。
        步骤 4: 合并并返回采样结果。

        Args:
            lines: 原始对话列表。
            chunk_blocks: 目标块数。
            chunk_lines: 每块采样行数。

        Returns:
            采样后的对话列表。
        """
        # 步骤 1: 空输入直接返回空列表
        if not lines:
            return []

        total_lines: int = len(lines)

        # 步骤 2: 当总行数小于块数时，直接返回前若干行，避免无意义切块
        if total_lines < chunk_blocks:
            return lines[:chunk_lines]

        block_size: int = total_lines // chunk_blocks
        if block_size == 0:
            return lines[:chunk_lines]

        # 步骤 3: 逐块取样
        sampled_lines: list[str] = []
        for index in range(chunk_blocks):
            start_index: int = index * block_size
            end_index: int = start_index + chunk_lines

            if start_index >= total_lines:
                break

            end_index = min(end_index, total_lines)
            sampled_lines.extend(lines[start_index:end_index])

        # 步骤 4: 返回采样结果
        return sampled_lines


__all__: list[str] = ["GlossaryExtraction"]