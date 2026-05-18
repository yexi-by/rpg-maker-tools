"""按原件留档还原字体引用。"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from app.application.file_writer import replace_json_file, replace_plugins_file, replace_text_file
from app.rmmz.loader import resolve_game_layout
from app.rmmz.schema import GameData
from app.rmmz.text_rules import JsonValue

from .constants import FONTS_DIRECTORY_NAME, GAMEFONT_CSS_FILE_NAME, GAMEFONT_CSS_ORIGIN_FILE_NAME
from .css import restore_gamefont_css_text_by_origin
from .files import read_json_value_file, read_plugins_js_file, serialize_plugins_js
from .models import OriginFontRestoreSummary
from .references import (
    build_font_reference_tokens,
    normalize_font_name_list,
    restore_font_references_in_json_value_by_origin,
)

def restore_font_references_from_origin_backups(
    *,
    game_root: Path | None = None,
    game_data: GameData | None = None,
    replacement_font_names: list[str],
) -> OriginFontRestoreSummary:
    """对比激活版和原件留档，把覆盖字体引用替回原来的字体引用。"""
    if game_data is not None:
        layout = game_data.layout
    elif game_root is not None:
        layout = resolve_game_layout(game_root)
    else:
        raise ValueError("字体还原需要游戏数据或游戏目录")

    target_font_names = normalize_font_name_list(
        build_font_reference_tokens(replacement_font_names)
    )
    if not target_font_names:
        raise ValueError("字体还原缺少候选覆盖字体名称")

    active_data_dir = layout.data_dir
    origin_data_dir = layout.data_origin_dir
    active_plugins_path = layout.plugins_path
    origin_plugins_path = layout.plugins_origin_path
    active_gamefont_css_path = layout.content_root / FONTS_DIRECTORY_NAME / GAMEFONT_CSS_FILE_NAME
    origin_gamefont_css_path = active_gamefont_css_path.with_name(GAMEFONT_CSS_ORIGIN_FILE_NAME)
    if (
        not origin_data_dir.exists()
        and not origin_plugins_path.exists()
        and not origin_gamefont_css_path.exists()
    ):
        raise FileNotFoundError("字体还原需要 data_origin、plugins_origin.js 或 gamefont_origin.css 原件留档")

    restored_field_count = 0
    restored_reference_count = 0
    if origin_data_dir.exists():
        if not origin_data_dir.is_dir():
            raise NotADirectoryError(f"原件数据留档不是目录: {origin_data_dir}")
        for origin_file_path in sorted(origin_data_dir.glob("*.json"), key=lambda path: path.name):
            active_file_path = active_data_dir / origin_file_path.name
            if not active_file_path.exists():
                raise FileNotFoundError(f"激活数据文件不存在，无法对比还原字体: {active_file_path}")
            active_value = read_json_value_file(active_file_path)
            origin_value = read_json_value_file(origin_file_path)
            updated_value, field_count, reference_count = restore_font_references_in_json_value_by_origin(
                active_value=active_value,
                origin_value=origin_value,
                target_font_names=target_font_names,
            )
            if field_count == 0:
                continue
            replace_json_file(
                target_path=active_file_path,
                data=updated_value,
                temp_dir=layout.content_root,
            )
            restored_field_count += field_count
            restored_reference_count += reference_count

    if origin_plugins_path.exists():
        if not active_plugins_path.exists():
            raise FileNotFoundError(f"激活插件配置不存在，无法对比还原字体: {active_plugins_path}")
        active_plugins = read_plugins_js_file(active_plugins_path)
        origin_plugins = read_plugins_js_file(origin_plugins_path)
        updated_plugins_value, field_count, reference_count = restore_font_references_in_json_value_by_origin(
            active_value=cast(JsonValue, active_plugins),
            origin_value=cast(JsonValue, origin_plugins),
            target_font_names=target_font_names,
        )
        if field_count > 0:
            if not isinstance(updated_plugins_value, list):
                raise TypeError("字体还原后的插件配置不是数组")
            updated_plugins: list[dict[str, JsonValue]] = []
            for index, plugin_value in enumerate(updated_plugins_value):
                if not isinstance(plugin_value, dict):
                    raise TypeError(f"字体还原后的第 {index} 个插件不是对象")
                updated_plugins.append(plugin_value)
            replace_plugins_file(
                plugins_path=active_plugins_path,
                data=serialize_plugins_js(updated_plugins),
                temp_dir=active_plugins_path.parent,
            )
            restored_field_count += field_count
            restored_reference_count += reference_count

    if origin_gamefont_css_path.exists():
        if not active_gamefont_css_path.exists():
            raise FileNotFoundError(f"激活字体样式表不存在，无法对比还原字体: {active_gamefont_css_path}")
        active_css_text = active_gamefont_css_path.read_text(encoding="utf-8")
        origin_css_text = origin_gamefont_css_path.read_text(encoding="utf-8")
        updated_css_text, field_count, reference_count = restore_gamefont_css_text_by_origin(
            active_css_text=active_css_text,
            origin_css_text=origin_css_text,
            target_font_names=target_font_names,
        )
        if field_count > 0:
            replace_text_file(
                target_path=active_gamefont_css_path,
                content=updated_css_text,
                temp_dir=active_gamefont_css_path.parent,
            )
            restored_field_count += field_count
            restored_reference_count += reference_count

    return OriginFontRestoreSummary(
        target_font_names=target_font_names,
        restored_field_count=restored_field_count,
        restored_reference_count=restored_reference_count,
    )
