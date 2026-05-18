"""字体替换模块共享常量与正则。"""

import re

FONTS_DIRECTORY_NAME = "fonts"
GAMEFONT_CSS_FILE_NAME = "gamefont.css"
GAMEFONT_CSS_ORIGIN_FILE_NAME = "gamefont_origin.css"
FONT_FILE_SUFFIXES = frozenset({".ttf", ".otf", ".woff", ".woff2"})
FONT_FILE_REFERENCE_PATTERN: re.Pattern[str] = re.compile(
    r"[\w .+\-\u0080-\uffff]+?\.(?:ttf|otf|woff2?)",
    re.IGNORECASE,
)
BARE_FONT_REFERENCE_PATTERN: re.Pattern[str] = re.compile(r"[A-Za-z0-9_ .+\-]{1,128}")
CSS_FONT_FACE_BLOCK_PATTERN: re.Pattern[str] = re.compile(
    r"(?P<head>@font-face\s*\{)(?P<body>.*?)(?P<tail>\})",
    re.IGNORECASE | re.DOTALL,
)
CSS_FONT_FAMILY_PATTERN: re.Pattern[str] = re.compile(
    r"font-family\s*:\s*(?P<quote>['\"]?)(?P<family>[^;'\"\r\n]+)(?P=quote)\s*;",
    re.IGNORECASE,
)
CSS_URL_PATTERN: re.Pattern[str] = re.compile(
    r"url\(\s*(?P<quote>['\"]?)(?P<path>[^)'\"\r\n]+)(?P=quote)\s*\)",
    re.IGNORECASE,
)
