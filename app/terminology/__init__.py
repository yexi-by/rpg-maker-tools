"""术语表工程模块。"""

from .extraction import TerminologyExtraction
from .files import (
    TerminologyExportSummary,
    export_terminology_artifacts,
    load_terminology_registry,
)
from .prompt import TerminologyPromptEntry, TerminologyPromptIndex
from .schemas import (
    DatabaseTermContext,
    SpeakerDialogueContext,
    TerminologyCategory,
    TerminologyRegistry,
)
from .write_back import apply_terminology_translations

__all__: list[str] = [
    "DatabaseTermContext",
    "SpeakerDialogueContext",
    "TerminologyCategory",
    "TerminologyExportSummary",
    "TerminologyExtraction",
    "TerminologyPromptEntry",
    "TerminologyPromptIndex",
    "TerminologyRegistry",
    "apply_terminology_translations",
    "export_terminology_artifacts",
    "load_terminology_registry",
]
