"""通用小工具公共导出入口。"""

from .config_loader_utils import load_setting
from .japanese_utils import JapaneseDetectMode, has_japanese

__all__: list[str] = [
    "JapaneseDetectMode",
    "has_japanese",
    "load_setting",
]
