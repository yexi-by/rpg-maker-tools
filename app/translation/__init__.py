"""
翻译层统一导出入口。

当前用于集中导出术语翻译、正文翻译与上下文构建能力，避免上层依赖过深模块路径。
"""

from .context import iter_error_retry_context_batches, iter_translation_context_batches
from .glossary_translation import GlossaryTranslation
from .text_translation import TextTranslation
from .verify import verify_translation_batch


__all__: list[str] = [
    "GlossaryTranslation",
    "TextTranslation",
    "iter_error_retry_context_batches",
    "iter_translation_context_batches",
    "verify_translation_batch",
]
