"""应用层任务摘要模型。"""

from dataclasses import dataclass


@dataclass(slots=True)
class PluginRuleImportSummary:
    """外部插件规则导入任务摘要。"""

    imported_plugin_count: int
    imported_rule_count: int
    deleted_translation_items: int


@dataclass(slots=True)
class PluginJsonExportSummary:
    """插件配置 JSON 导出任务摘要。"""

    output_path: str
    plugin_count: int


@dataclass(slots=True)
class NameContextImportSummary:
    """外部术语表导入任务摘要。"""

    imported_entry_count: int
    filled_entry_count: int


@dataclass(slots=True)
class TextTranslationSummary:
    """正文翻译任务摘要。"""

    total_extracted_items: int
    pending_count: int
    deduplicated_count: int
    batch_count: int
    success_count: int
    error_count: int
    blocked_reason: str | None = None

    @property
    def is_blocked(self) -> bool:
        """判断正文翻译是否被业务前置条件阻断。"""
        return self.blocked_reason is not None

    @property
    def has_errors(self) -> bool:
        """判断正文翻译是否产生错误条目。"""
        return self.error_count > 0


@dataclass(slots=True)
class NameContextWriteSummary:
    """数据库术语表写回任务摘要。"""

    written_count: int
    preserved_translation_count: int


__all__: list[str] = [
    "NameContextImportSummary",
    "NameContextWriteSummary",
    "PluginJsonExportSummary",
    "PluginRuleImportSummary",
    "TextTranslationSummary",
]
