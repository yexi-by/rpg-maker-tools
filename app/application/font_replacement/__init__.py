"""写回阶段字体替换公共入口。"""

from .files import read_plugins_js_file, resolve_replacement_font_path
from .models import (
    FontReplacementSummary,
    OriginFontRestoreSummary,
    build_empty_font_replacement_summary,
)
from .references import collect_replacement_font_names
from .restore import restore_font_references_from_origin_backups
from .service import apply_font_replacement

__all__: list[str] = [
    "FontReplacementSummary",
    "OriginFontRestoreSummary",
    "apply_font_replacement",
    "build_empty_font_replacement_summary",
    "collect_replacement_font_names",
    "read_plugins_js_file",
    "resolve_replacement_font_path",
    "restore_font_references_from_origin_backups",
]
