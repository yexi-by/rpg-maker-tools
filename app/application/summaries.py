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
class EventCommandJsonExportSummary:
    """事件指令参数 JSON 导出任务摘要。"""

    output_path: str
    command_count: int


@dataclass(slots=True)
class EventCommandRuleImportSummary:
    """事件指令规则导入任务摘要。"""

    imported_rule_group_count: int
    imported_path_rule_count: int
    deleted_translation_items: int


@dataclass(slots=True)
class NoteTagJsonExportSummary:
    """Note 标签候选 JSON 导出任务摘要。"""

    output_path: str
    candidate_tag_count: int
    translatable_value_count: int


@dataclass(slots=True)
class NoteTagRuleImportSummary:
    """Note 标签规则导入任务摘要。"""

    imported_file_count: int
    imported_tag_count: int
    deleted_translation_items: int


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
    llm_failure_count: int = 0
    run_id: str = ""
    blocked_reason: str | None = None

    @property
    def is_blocked(self) -> bool:
        """判断正文翻译是否因为业务前置条件无法继续。"""
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


@dataclass(slots=True)
class WriteBackSummary:
    """游戏文件回写任务摘要。"""

    data_item_count: int
    plugin_item_count: int
    name_written_count: int
    target_font_name: str | None
    source_font_count: int
    replaced_font_reference_count: int
    font_copied: bool


@dataclass(slots=True)
class FontRestoreSummary:
    """字体引用还原任务摘要。"""

    restored_record_count: int
    restored_reference_count: int
    target_font_name: str | None


__all__: list[str] = [
    "EventCommandJsonExportSummary",
    "EventCommandRuleImportSummary",
    "FontRestoreSummary",
    "NameContextImportSummary",
    "NameContextWriteSummary",
    "NoteTagJsonExportSummary",
    "NoteTagRuleImportSummary",
    "PluginJsonExportSummary",
    "PluginRuleImportSummary",
    "TextTranslationSummary",
    "WriteBackSummary",
]
