"""
源语言与插件类文本策略工具模块。

本模块统一承载两类轻量规则，避免调用方在多个文件之间来回跳转：
1. 与 `source_language` 直接相关的文本可读性判断与残留校验。
2. 与语言无关的插件类文本过滤规则，用于剔除配置值、变量名、资源名和脚本表达式。

这样提取层与校验层都只需要依赖这一份策略入口。
"""

import re
from pathlib import Path
from typing import cast

from app.models.schemas import (
    SOURCE_LANGUAGE_VALUES,
    SourceLanguage,
    strip_rm_control_sequences,
)

from .japanese_utils import has_japanese

SOURCE_LANGUAGE_LABELS: dict[SourceLanguage, str] = {
    "ja": "日文",
    "en": "英文",
}

JAPANESE_SEGMENT_PATTERN: re.Pattern[str] = re.compile(r"[\u3040-\u309F\u30A0-\u30FF]+")
ALLOWED_JAPANESE_CHARS: set[str] = {
    "っ",
    "ッ",
    "ー",
    "・",
    "。",
    "～",
    "…",
}
ALLOWED_JAPANESE_TAIL_CHARS: set[str] = {
    "あ",
    "い",
    "う",
    "え",
    "お",
    "っ",
    "ッ",
    "ん",
    "ー",
    "よ",
    "ね",
    "な",
    "か",
}

ENGLISH_LETTER_PATTERN: re.Pattern[str] = re.compile(r"[A-Za-z]")
ENGLISH_WORD_PATTERN: re.Pattern[str] = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")
ENGLISH_SEGMENT_PATTERN: re.Pattern[str] = re.compile(
    r"[A-Za-z][A-Za-z0-9'’-]*[A-Za-z]|[A-Za-z]{3,}"
)
PLACEHOLDER_PATTERN: re.Pattern[str] = re.compile(r"\[[A-Z]+(?:_[^\]]+)?\]")
RESOURCE_LIKE_PATTERN: re.Pattern[str] = re.compile(
    r"(?:[A-Za-z]:)?[A-Za-z0-9_./\\:-]+\.[A-Za-z0-9]{1,8}",
    flags=re.IGNORECASE,
)
PURE_NUMBER_PATTERN: re.Pattern[str] = re.compile(r"[-+]?\d+(?:\.\d+)?")
HEX_COLOR_PATTERN: re.Pattern[str] = re.compile(r"#[0-9A-Fa-f]{6,8}")
CSS_COLOR_FUNCTION_PATTERN: re.Pattern[str] = re.compile(
    r"(?:rgb|rgba|hsl|hsla)\([^)]*\)",
    flags=re.IGNORECASE,
)
RESOURCE_PATH_PATTERN: re.Pattern[str] = re.compile(
    r"(?:[A-Za-z]:)?(?:[A-Za-z0-9_.-]+[\\/])+[A-Za-z0-9_.-]+",
    flags=re.IGNORECASE,
)
SNAKE_CASE_PATTERN: re.Pattern[str] = re.compile(r"[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+")
CAMEL_CASE_PATTERN: re.Pattern[str] = re.compile(
    r"(?:[a-z]+(?:[A-Z][a-z0-9]+)+|[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+)"
)
BRACKET_IDENTIFIER_PATTERN: re.Pattern[str] = re.compile(
    r"\$?[A-Za-z_][A-Za-z0-9_]*\[[^\]]+\](?:\.[A-Za-z_][A-Za-z0-9_]*)*"
)
DOT_IDENTIFIER_PATTERN: re.Pattern[str] = re.compile(
    r"\$?[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+"
)
SCRIPT_CONCAT_PATTERN: re.Pattern[str] = re.compile(
    r"(?:['\"].*['\"]\s*\+\s*|\+\s*['\"].*['\"])",
)
SCRIPT_CALL_PATTERN: re.Pattern[str] = re.compile(
    r"\$?[A-Za-z_][A-Za-z0-9_]*\([^)]*\)"
)
NON_CONTENT_AFTER_CONTROL_PATTERN: re.Pattern[str] = re.compile(
    r"[\s\.\,\:\;\-\+\*\/\\\(\)\[\]\{\}<>=!?'\"%]*"
)

BOOLEAN_TEXTS: set[str] = {"true", "false"}
GENERIC_ENUM_TEXTS: set[str] = {
    "auto",
    "bottom",
    "center",
    "default",
    "false",
    "left",
    "none",
    "off",
    "on",
    "right",
    "top",
    "true",
}
ENGLISH_RESIDUAL_ALLOWED_WORDS: set[str] = {
    "bgm",
    "cg",
    "cpu",
    "dlc",
    "ep",
    "exp",
    "fps",
    "gb",
    "hp",
    "id",
    "kb",
    "lv",
    "mb",
    "mp",
    "ng",
    "npc",
    "ok",
    "pc",
    "se",
    "sp",
    "tp",
    "ui",
    "url",
}
NON_TRANSLATABLE_PATH_KEYWORDS: set[str] = {
    "achievementname",
    "censoredtitle",
    "damagename",
    "file",
    "filename",
    "filepath",
    "font",
    "fontface",
    "fontname",
    "icon",
    "id",
    "image",
    "img",
    "initialtitle",
    "namemap",
    "path",
    "picture",
    "source",
    "src",
    "statname",
    "symbol",
    "url",
}
EXCLUDED_PLUGIN_NAMES: set[str] = {"event_test"}
EXCLUDED_PLUGIN_COMMAND_FIELDS: set[tuple[str, str, str]] = {
    ("auramz/quests", "addquest", "description"),
    ("deploy/steam", "activateachievement", "achievementname"),
    ("thirdparty/steamlinkcommandsaddon", "activateachievement", "achievementname"),
    ("thirdparty/steamlinkcommandsaddon", "getstatint", "statname"),
    ("auramz/event_utils", "setactordamageimage", "damagename"),
    ("auramz/standing_images", "setnamemap", "namemap"),
}
FILE_LIKE_SUFFIXES: set[str] = {
    ".aac",
    ".avi",
    ".bmp",
    ".css",
    ".csv",
    ".flac",
    ".gif",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".m4a",
    ".mid",
    ".midi",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".otf",
    ".png",
    ".svg",
    ".tif",
    ".ttf",
    ".txt",
    ".wav",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
}


def validate_source_language(source_language: str) -> SourceLanguage:
    """
    校验并规范化源语言值。

    Args:
        source_language: 外部输入的源语言字符串。

    Returns:
        通过校验后的 `SourceLanguage`。

    Raises:
        ValueError: 输入值不在当前支持集合中时抛出。
    """
    normalized_source_language = source_language.strip().lower()
    if normalized_source_language not in SOURCE_LANGUAGE_VALUES:
        raise ValueError(
            "source_language 非法，当前仅支持 "
            f"{', '.join(SOURCE_LANGUAGE_VALUES)}：{source_language}"
        )
    return cast(SourceLanguage, normalized_source_language)


def get_source_language_label(source_language: SourceLanguage) -> str:
    """
    返回源语言的人类可读中文标签。

    Args:
        source_language: 已校验的源语言值。

    Returns:
        用于日志、提示词和界面展示的中文名称。
    """
    return SOURCE_LANGUAGE_LABELS[source_language]


def is_glossary_text_candidate(text: str, source_language: SourceLanguage) -> bool:
    """
    判断术语候选文本是否符合当前源语言特征。

    这里专门服务于角色名和地点名提取，因此只判断“像不像自然源语言文本”，
    不负责插件配置值等更强的过滤。

    Args:
        text: 待判断的原始文本。
        source_language: 当前游戏的源语言。

    Returns:
        文本像是当前源语言下的可读术语时返回 `True`。
    """
    normalized_text = text.strip()
    if not normalized_text:
        return False

    if source_language == "ja":
        return has_japanese(text=normalized_text, mode="non_strict")

    return _has_readable_english_text(normalized_text)


def is_plugin_text_candidate(text: str, source_language: SourceLanguage) -> bool:
    """
    判断插件字符串是否符合当前源语言特征。

    插件提取会先执行一轮“通用配置值过滤”，这里仅负责第二阶段的语言判定。

    Args:
        text: 已做基础清洗的插件字符串。
        source_language: 当前游戏的源语言。

    Returns:
        文本符合当前源语言文本特征时返回 `True`。
    """
    normalized_text = text.strip()
    if not normalized_text:
        return False
    if source_language == "en" and normalized_text.lower() in GENERIC_ENUM_TEXTS:
        return False
    return is_glossary_text_candidate(
        text=normalized_text,
        source_language=source_language,
    )


def check_source_language_residual(
    translation_lines: list[str],
    source_language: SourceLanguage,
) -> None:
    """
    检查译文中是否残留当前游戏的源语言文本。

    Args:
        translation_lines: 已恢复控制符后的最终译文行列表。
        source_language: 当前游戏的源语言。

    Raises:
        ValueError: 发现明显的源语言残留时抛出。
    """
    if source_language == "ja":
        _check_japanese_residual(translation_lines)
        return

    _check_english_residual(translation_lines)


def normalize_path_key(key: str) -> str:
    """
    归一化路径键名，便于黑名单匹配。

    Args:
        key: 原始键名。

    Returns:
        转小写并去掉 `_` 与 `-` 的归一化结果。
    """
    return key.strip().lower().replace("_", "").replace("-", "")


def has_non_translatable_path_key(path_parts: list[str | int]) -> bool:
    """
    判断路径中是否含有明确不可翻译的字段名。

    Args:
        path_parts: 当前值的完整路径片段。

    Returns:
        只要路径中任意一个字符串键命中黑名单，就返回 `True`。
    """
    for part in path_parts:
        if not isinstance(part, str):
            continue
        if normalize_path_key(part) in NON_TRANSLATABLE_PATH_KEYWORDS:
            return True
    return False


def should_skip_plugin_like_text(
    text: str,
    path_parts: list[str | int],
    plugin_name: str | None = None,
    command_name: str | None = None,
) -> bool:
    """
    判断插件类文本是否应当直接排除。

    Args:
        text: 当前叶子字符串。
        path_parts: 当前值对应的完整路径。
        plugin_name: 所属插件名，`plugins.js` 与 `357` 均可传。
        command_name: `357` 插件命令名；`plugins.js` 传 `None` 即可。

    Returns:
        如果这是明显不该翻译的值，返回 `True`。
    """
    normalized_text = text.strip()
    if not normalized_text:
        return True
    if _is_excluded_plugin_name(plugin_name):
        return True
    if _matches_excluded_plugin_command_field(
        path_parts=path_parts,
        plugin_name=plugin_name,
        command_name=command_name,
    ):
        return True
    if has_non_translatable_path_key(path_parts):
        return True
    if _is_boolean_text(normalized_text):
        return True
    if _is_pure_number_text(normalized_text):
        return True
    if _is_color_text(normalized_text):
        return True
    if _looks_like_resource_path(normalized_text):
        return True
    if _looks_like_file_name(normalized_text):
        return True
    if _is_generic_enum_text(normalized_text):
        return True
    if _is_placeholder_only_text(normalized_text):
        return True
    if _looks_like_script_expression(normalized_text):
        return True
    if _looks_like_identifier_text(normalized_text):
        return True
    return False


def _has_readable_english_text(text: str) -> bool:
    """
    判断文本是否像英文自然语言片段。

    该规则刻意保持宽松，只要求至少存在英文字母，且不是明显的资源引用或纯符号串，
    以便兼容英文人名、地名、菜单项和短句。

    Args:
        text: 待判断文本。

    Returns:
        符合英文可读文本特征时返回 `True`。
    """
    if not ENGLISH_LETTER_PATTERN.search(text):
        return False
    if RESOURCE_LIKE_PATTERN.fullmatch(text):
        return False
    return True


def _strip_non_content_for_residual(text: str) -> str:
    """
    在残留校验前剥离控制符和占位符噪音。

    Args:
        text: 待清洗文本。

    Returns:
        去掉 RPG Maker 控制符与占位符后的纯文本。
    """
    cleaned_text = strip_rm_control_sequences(text)
    cleaned_text = PLACEHOLDER_PATTERN.sub("", cleaned_text)
    return cleaned_text


def _check_japanese_residual(translation_lines: list[str]) -> None:
    """
    执行日文残留校验。

    Args:
        translation_lines: 已恢复控制符的译文行列表。

    Raises:
        ValueError: 检测到真实日文残留时抛出。
    """
    for index, line in enumerate(translation_lines, start=1):
        segments = JAPANESE_SEGMENT_PATTERN.findall(_strip_non_content_for_residual(line))
        if not segments:
            continue

        real_residual: list[str] = []
        for segment in segments:
            filtered_segment = [
                char for char in segment if char not in ALLOWED_JAPANESE_CHARS
            ]
            if not filtered_segment:
                continue
            if all(char in ALLOWED_JAPANESE_TAIL_CHARS for char in filtered_segment):
                continue
            real_residual.extend(filtered_segment)

        if real_residual:
            raise ValueError(f"发现日文残留(第 {index} 行): {real_residual}")


def _check_english_residual(translation_lines: list[str]) -> None:
    """
    执行英文残留校验。

    这里不追求语言学完备性，而是拦截明显未翻的英文单词或短句，同时放过常见缩写。

    Args:
        translation_lines: 已恢复控制符的译文行列表。

    Raises:
        ValueError: 检测到明显英文残留时抛出。
    """
    for index, line in enumerate(translation_lines, start=1):
        cleaned_line = _strip_non_content_for_residual(line)
        for segment in ENGLISH_SEGMENT_PATTERN.findall(cleaned_line):
            normalized_segment = segment.strip(" -'’")
            if not normalized_segment:
                continue
            if RESOURCE_LIKE_PATTERN.fullmatch(normalized_segment):
                continue

            meaningful_words = _extract_meaningful_english_words(normalized_segment)
            if meaningful_words:
                raise ValueError(f"发现英文残留(第 {index} 行): {meaningful_words}")


def _extract_meaningful_english_words(segment: str) -> list[str]:
    """
    从英文片段中筛出真正应视为残留的单词。

    Args:
        segment: 已去掉控制符的英文候选片段。

    Returns:
        需要当作未翻残留处理的单词列表。
    """
    meaningful_words: list[str] = []
    for word in ENGLISH_WORD_PATTERN.findall(segment):
        normalized_word = word.lower()
        if normalized_word in ENGLISH_RESIDUAL_ALLOWED_WORDS:
            continue
        if word.isupper() and len(word) <= 4:
            continue
        if len(word) <= 2:
            continue
        meaningful_words.append(word)
    return meaningful_words


def _is_excluded_plugin_name(plugin_name: str | None) -> bool:
    """
    判断插件名是否属于整插件排除名单。

    Args:
        plugin_name: 当前插件名。

    Returns:
        命中整插件黑名单时返回 `True`。
    """
    if plugin_name is None:
        return False
    return plugin_name.strip().lower() in EXCLUDED_PLUGIN_NAMES


def _matches_excluded_plugin_command_field(
    path_parts: list[str | int],
    plugin_name: str | None,
    command_name: str | None,
) -> bool:
    """
    判断是否命中定向的插件命令字段黑名单。

    Args:
        path_parts: 当前值的完整路径片段。
        plugin_name: 当前插件名。
        command_name: 当前插件命令名。

    Returns:
        命中定向黑名单时返回 `True`。
    """
    if plugin_name is None or command_name is None:
        return False

    field_key = _find_last_string_key(path_parts)
    if field_key is None:
        return False

    normalized_triplet = (
        plugin_name.strip().lower(),
        command_name.strip().lower(),
        field_key,
    )
    return normalized_triplet in EXCLUDED_PLUGIN_COMMAND_FIELDS


def _find_last_string_key(path_parts: list[str | int]) -> str | None:
    """
    找出路径中最后一个字符串键名。

    Args:
        path_parts: 当前值的完整路径片段。

    Returns:
        最末尾的字符串键名；如果不存在则返回 `None`。
    """
    for part in reversed(path_parts):
        if isinstance(part, str):
            return normalize_path_key(part)
    return None


def _is_boolean_text(text: str) -> bool:
    """
    判断是否为布尔字面量文本。

    Args:
        text: 待判断文本。

    Returns:
        布尔字面量返回 `True`。
    """
    return text.lower() in BOOLEAN_TEXTS


def _is_pure_number_text(text: str) -> bool:
    """
    判断是否为纯数字文本。

    Args:
        text: 待判断文本。

    Returns:
        纯数字返回 `True`。
    """
    return PURE_NUMBER_PATTERN.fullmatch(text) is not None


def _is_color_text(text: str) -> bool:
    """
    判断是否为颜色配置文本。

    Args:
        text: 待判断文本。

    Returns:
        命中颜色模式时返回 `True`。
    """
    return (
        HEX_COLOR_PATTERN.fullmatch(text) is not None
        or CSS_COLOR_FUNCTION_PATTERN.fullmatch(text) is not None
    )


def _looks_like_resource_path(text: str) -> bool:
    """
    判断是否像资源路径或资源引用。

    Args:
        text: 待判断文本。

    Returns:
        明显是资源路径时返回 `True`。
    """
    return RESOURCE_PATH_PATTERN.fullmatch(text) is not None


def _looks_like_file_name(text: str) -> bool:
    """
    判断是否像单独的文件名。

    Args:
        text: 待判断文本。

    Returns:
        命中常见资源文件后缀时返回 `True`。
    """
    return Path(text).suffix.lower() in FILE_LIKE_SUFFIXES


def _is_generic_enum_text(text: str) -> bool:
    """
    判断是否为明显的通用枚举值。

    Args:
        text: 待判断文本。

    Returns:
        命中通用枚举值时返回 `True`。
    """
    return text.lower() in GENERIC_ENUM_TEXTS


def _is_placeholder_only_text(text: str) -> bool:
    """
    判断文本去掉控制符后是否已经没有正文内容。

    Args:
        text: 待判断文本。

    Returns:
        去掉控制符后只剩空白和标点时返回 `True`。
    """
    stripped_text = strip_rm_control_sequences(text)
    return NON_CONTENT_AFTER_CONTROL_PATTERN.fullmatch(stripped_text) is not None


def _looks_like_script_expression(text: str) -> bool:
    """
    判断是否像脚本表达式或数据访问表达式。

    Args:
        text: 待判断文本。

    Returns:
        明显为脚本表达式时返回 `True`。
    """
    if "$data" in text or "$game" in text:
        return True
    if SCRIPT_CONCAT_PATTERN.search(text):
        return True
    if " ? " in text and " : " in text:
        return True
    if BRACKET_IDENTIFIER_PATTERN.fullmatch(text):
        return True
    if DOT_IDENTIFIER_PATTERN.fullmatch(text) and text.startswith("$"):
        return True
    if "$" in text and SCRIPT_CALL_PATTERN.search(text):
        return True
    return False


def _looks_like_identifier_text(text: str) -> bool:
    """
    判断是否像内部标识符而不是玩家可见文本。

    Args:
        text: 待判断文本。

    Returns:
        命中常见标识符形态时返回 `True`。
    """
    return (
        SNAKE_CASE_PATTERN.fullmatch(text) is not None
        or CAMEL_CASE_PATTERN.fullmatch(text) is not None
        or DOT_IDENTIFIER_PATTERN.fullmatch(text) is not None
        or BRACKET_IDENTIFIER_PATTERN.fullmatch(text) is not None
    )


__all__: list[str] = [
    "SOURCE_LANGUAGE_LABELS",
    "check_source_language_residual",
    "get_source_language_label",
    "has_non_translatable_path_key",
    "is_glossary_text_candidate",
    "is_plugin_text_candidate",
    "normalize_path_key",
    "should_skip_plugin_like_text",
    "validate_source_language",
]
