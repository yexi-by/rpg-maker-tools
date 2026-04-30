"""
外部标准名上下文模块。

本模块管理 `101` 名字框与 `MapXXX.displayName`：项目负责导出上下文、读取外部
Agent 填写后的临时文件，并通过显式导入命令写入数据库。正文提示词和回写流程的数据源
为数据库中的术语表。
"""

from .extraction import NameContextExtraction
from .files import (
    NameContextExportSummary,
    export_name_context_files,
    load_name_context_registry,
)
from .prompt import NamePromptIndex, NamePromptEntry
from .schemas import (
    NAME_CONTEXT_SCHEMA_VERSION,
    NameContextRegistry,
    NameEntryKind,
    NameLocation,
    NameRegistryEntry,
    SpeakerDialogueContext,
)
from .write_back import apply_name_context_translations

__all__: list[str] = [
    "NAME_CONTEXT_SCHEMA_VERSION",
    "NameContextExportSummary",
    "NameContextExtraction",
    "NameContextRegistry",
    "NameEntryKind",
    "NameLocation",
    "NamePromptEntry",
    "NamePromptIndex",
    "NameRegistryEntry",
    "SpeakerDialogueContext",
    "apply_name_context_translations",
    "export_name_context_files",
    "load_name_context_registry",
]
