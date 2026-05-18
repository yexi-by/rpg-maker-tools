"""写入游戏文件阶段的字体替换入口。"""

from __future__ import annotations

from pathlib import Path

from app.rmmz.schema import GameData

from .constants import FONTS_DIRECTORY_NAME
from .css import replace_gamefont_css_references
from .files import collect_replaced_source_font_names, copy_replacement_font, resolve_replacement_font_path
from .models import FontReplacementSummary, OriginFontRestoreSummary, build_empty_font_replacement_summary
from .native_changes import replace_font_references
from .references import collect_replacement_font_names
from .restore import restore_font_references_from_origin_backups

def apply_font_replacement(
    *,
    game_data: GameData,
    game_root: Path | None = None,
    replacement_font_path: str | None,
) -> FontReplacementSummary:
    """复制目标字体，并把即将写出的文件引用切换到目标字体。"""
    _ = game_root
    if replacement_font_path is None or not replacement_font_path.strip():
        return build_empty_font_replacement_summary()

    source_font_path = resolve_replacement_font_path(replacement_font_path)
    target_font_name = source_font_path.name
    font_dir = game_data.layout.content_root / FONTS_DIRECTORY_NAME
    old_font_names = collect_replaced_source_font_names(
        font_dir=font_dir,
        replacement_font_name=target_font_name,
    )
    copy_replacement_font(
        source_font_path=source_font_path,
        font_dir=font_dir,
    )
    replaced_reference_count, records = replace_font_references(
        game_data=game_data,
        old_font_names=old_font_names,
        replacement_font_name=target_font_name,
    )
    css_replaced_count, css_records = replace_gamefont_css_references(
        font_dir=font_dir,
        replacement_font_name=target_font_name,
    )
    records.extend(css_records)
    return FontReplacementSummary(
        target_font_name=target_font_name,
        source_font_count=len(old_font_names),
        replaced_reference_count=replaced_reference_count + css_replaced_count,
        copied=True,
        records=records,
    )

__all__ = [
    "FontReplacementSummary",
    "OriginFontRestoreSummary",
    "apply_font_replacement",
    "build_empty_font_replacement_summary",
    "collect_replacement_font_names",
    "resolve_replacement_font_path",
    "restore_font_references_from_origin_backups",
]
