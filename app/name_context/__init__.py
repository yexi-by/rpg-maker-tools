"""外部标准名术语模块。"""

from .extraction import NameContextExtraction
from .files import (
    NameContextExportSummary,
    export_name_context_artifacts,
    load_name_context_registry,
)
from .prompt import NamePromptEntry, NamePromptIndex
from .schemas import (
    NameContextRegistry,
    SpeakerDialogueContext,
)
from .write_back import apply_name_context_translations

__all__: list[str] = [
    "NameContextExportSummary",
    "NameContextExtraction",
    "NameContextRegistry",
    "NamePromptEntry",
    "NamePromptIndex",
    "SpeakerDialogueContext",
    "apply_name_context_translations",
    "export_name_context_artifacts",
    "load_name_context_registry",
]
