"""应用层任务摘要模型。"""

from dataclasses import dataclass


@dataclass(slots=True)
class PluginTextAnalysisSummary:
    """插件文本路径分析任务摘要。"""

    total_plugins: int
    success_plugins: int
    failed_plugins: int
    reused_success_count: int
    deleted_translation_items: int
    skipped_reason: str | None = None

    @property
    def has_failures(self) -> bool:
        """判断本轮插件分析是否存在失败插件。"""
        return self.failed_plugins > 0


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


__all__: list[str] = [
    "PluginTextAnalysisSummary",
    "TextTranslationSummary",
]
