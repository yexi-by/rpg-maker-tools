"""
配置模块导出。

主线已经切换到多游戏新栈，
这里统一导出不包含 `project` 段的新配置模型和新的配置加载器。
"""

from .schemas import (
    ErrorTranslationSetting,
    GlossaryExtractionSetting,
    GlossaryTranslationSetting,
    GlossaryTranslationTaskSetting,
    LLMServicesSetting,
    LLMServiceSetting,
    Setting,
    TextTranslationSetting,
    TranslationContextSetting,
)

__all__: list[str] = [
    "ErrorTranslationSetting",
    "GlossaryExtractionSetting",
    "GlossaryTranslationSetting",
    "GlossaryTranslationTaskSetting",
    "LLMServicesSetting",
    "LLMServiceSetting",
    "Setting",
    "TextTranslationSetting",
    "TranslationContextSetting",
]
