"""
配置模块公共导出入口。
"""

from .custom_placeholder_rules import (
    CUSTOM_PLACEHOLDER_RULES_FILE_NAME,
    load_custom_placeholder_rules,
    load_custom_placeholder_rules_file,
    load_custom_placeholder_rules_text,
    parse_custom_placeholder_rules,
    resolve_custom_placeholder_rules_path,
)
from .environment import (
    LLM_API_KEY_ENV_NAME,
    LLM_BASE_URL_ENV_NAME,
    EnvironmentOverrides,
    apply_environment_overrides,
    load_environment_overrides,
)
from .overrides import SettingOverrides, apply_setting_overrides
from .schemas import (
    EventCommandTextSetting,
    LLMSetting,
    Setting,
    StrictBaseModel,
    TextRulesSetting,
    TextTranslationSetting,
    TranslationContextSetting,
    WriteBackSetting,
)

__all__: list[str] = [
    "CUSTOM_PLACEHOLDER_RULES_FILE_NAME",
    "EnvironmentOverrides",
    "EventCommandTextSetting",
    "LLM_API_KEY_ENV_NAME",
    "LLM_BASE_URL_ENV_NAME",
    "LLMSetting",
    "SettingOverrides",
    "Setting",
    "StrictBaseModel",
    "TextRulesSetting",
    "TextTranslationSetting",
    "TranslationContextSetting",
    "WriteBackSetting",
    "apply_environment_overrides",
    "apply_setting_overrides",
    "load_custom_placeholder_rules",
    "load_custom_placeholder_rules_file",
    "load_custom_placeholder_rules_text",
    "load_environment_overrides",
    "parse_custom_placeholder_rules",
    "resolve_custom_placeholder_rules_path",
]
