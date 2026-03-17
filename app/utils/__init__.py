"""
工具模块统一导出入口。
通过 `app.utils` 统一导出常用工具函数与日志对象，
避免业务层直接引用过深的子模块路径。
"""

from .command_utils import iter_all_commands
from .japanese_utils import JapaneseDetectMode, has_japanese
from .log_utils import LogLine, console, get_progress, logger, setup_logger
from .probe_utils import run_dialogue_probe
from .source_language_utils import (
    SOURCE_LANGUAGE_LABELS,
    check_source_language_residual,
    get_source_language_label,
    has_non_translatable_path_key,
    is_glossary_text_candidate,
    is_plugin_text_candidate,
    normalize_path_key,
    should_skip_plugin_like_text,
    validate_source_language,
)

__all__: list[str] = [
    "JapaneseDetectMode",
    "LogLine",
    "SOURCE_LANGUAGE_LABELS",
    "check_source_language_residual",
    "console",
    "get_source_language_label",
    "get_progress",
    "has_non_translatable_path_key",
    "has_japanese",
    "is_glossary_text_candidate",
    "is_plugin_text_candidate",
    "iter_all_commands",
    "logger",
    "normalize_path_key",
    "run_dialogue_probe",
    "setup_logger",
    "should_skip_plugin_like_text",
    "validate_source_language",
]
