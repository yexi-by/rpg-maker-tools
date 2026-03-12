"""
日文字符判断工具模块。

提供严格模式与非严格模式两种日文检测能力。
严格模式只判断平假名和片假名，非严格模式额外判断汉字。
日文标点符号不计入命中范围。
"""

import re
from typing import Literal


type JapaneseDetectMode = Literal["strict", "non_strict"]


# 严格模式：只判断平假名与片假名，不包含日文标点符号。
# 说明：
# 1. \u3041-\u3096 为常用平假名字符。
# 2. \u309D-\u309F 为平假名迭代符等扩展字符。
# 3. \u30A1-\u30FA 为常用片假名字符。
# 4. \u30FD-\u30FF 为片假名迭代符等扩展字符。
STRICT_JAPANESE_PATTERN: re.Pattern[str] = re.compile(
    r"[\u3041-\u3096\u309D-\u309F\u30A1-\u30FA\u30FD-\u30FF]"
)

# 非严格模式：在严格模式基础上额外判断汉字。
NON_STRICT_JAPANESE_PATTERN: re.Pattern[str] = re.compile(
    r"[\u3041-\u3096\u309D-\u309F\u30A1-\u30FA\u30FD-\u30FF\u4E00-\u9FFF]"
)


def has_japanese(text: str, mode: JapaneseDetectMode) -> bool:
    """
    判断给定字符串是否包含日文字符。

    根据不同的检测模式使用相应的正则表达式：
    1. 严格模式（strict）：只判断是否存在平假名或片假名。这通常用于区分纯汉字词和包含日文特征的词汇。
    2. 非严格模式（non_strict）：在严格模式基础上，也把中日共用的汉字字符视为命中日文的一部分。通常用于角色名、地图名的初步筛选，避免因名字全是汉字（但在日文语境中）而被错误过滤。

    Args:
        text: 需要检测的目标字符串。
        mode: 检测模式。'strict' 为严格模式（仅平假名/片假名），'non_strict' 为非严格模式（包含汉字）。

    Returns:
        bool: 如果字符串中包含符合当前模式的字符，则返回 True，否则返回 False。
    """
    # 步骤 1: 过滤空字符串，提升效率
    if not text:
        return False

    # 步骤 2: 选择对应的编译好的正则模式
    pattern: re.Pattern[str]
    match mode:
        case "strict":
            pattern = STRICT_JAPANESE_PATTERN
        case "non_strict":
            pattern = NON_STRICT_JAPANESE_PATTERN

    # 步骤 3: 执行正则搜索，如果找到匹配即说明含有相应字符
    return pattern.search(text) is not None


__all__: list[str] = ["JapaneseDetectMode", "has_japanese"]