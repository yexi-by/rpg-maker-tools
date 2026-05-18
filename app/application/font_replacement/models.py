"""字体替换执行摘要模型。"""

from dataclasses import dataclass

from app.rmmz.schema import FontReplacementRecord

@dataclass(frozen=True, slots=True)
class FontReplacementSummary:
    """字体替换执行摘要。"""

    target_font_name: str | None
    source_font_count: int
    replaced_reference_count: int
    copied: bool
    records: list[FontReplacementRecord]

@dataclass(frozen=True, slots=True)
class OriginFontRestoreSummary:
    """按原件留档对比还原字体引用的执行摘要。"""

    target_font_names: list[str]
    restored_field_count: int
    restored_reference_count: int

def build_empty_font_replacement_summary() -> FontReplacementSummary:
    """生成未执行字体覆盖时使用的空摘要。"""
    return FontReplacementSummary(
        target_font_name=None,
        source_font_count=0,
        replaced_reference_count=0,
        copied=False,
        records=[],
    )
