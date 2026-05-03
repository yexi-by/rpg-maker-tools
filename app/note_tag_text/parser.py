"""RMMZ Note 元标签解析与安全替换工具。"""

import re
from dataclasses import dataclass

from app.rmmz.text_protocol import encode_visible_text_like, ensure_encoded_text_valid

NOTE_TAG_PATTERN: re.Pattern[str] = re.compile(
    r"<(?P<tag>[^<>:\r\n]+)(?::(?P<value>[^<>]*))?>",
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class NoteTagMatch:
    """单个 `<标签:值>` 片段的解析结果。"""

    tag_name: str
    value: str
    full_span: tuple[int, int]
    value_span: tuple[int, int] | None

    @property
    def has_value(self) -> bool:
        """判断标签是否带有冒号值。"""
        return self.value_span is not None


def iter_note_tag_matches(note_text: str) -> list[NoteTagMatch]:
    """扫描 Note 字段中的 RPG Maker 元标签。"""
    matches: list[NoteTagMatch] = []
    for match in NOTE_TAG_PATTERN.finditer(note_text):
        tag_name = match.group("tag").strip()
        if not tag_name:
            continue
        value = match.group("value")
        value_span: tuple[int, int] | None = None
        if value is not None:
            value_span = match.span("value")
        matches.append(
            NoteTagMatch(
                tag_name=tag_name,
                value=value if value is not None else "",
                full_span=match.span(),
                value_span=value_span,
            )
        )
    return matches


def replace_note_tag_value(note_text: str, tag_name: str, translated_text: str) -> str:
    """替换单个精确 Note 标签值，并保留其它 Note 内容。"""
    matches = [
        match
        for match in iter_note_tag_matches(note_text)
        if match.tag_name == tag_name and match.value_span is not None
    ]
    if not matches:
        raise ValueError(f"Note 标签不存在或没有值: {tag_name}")
    if len(matches) > 1:
        raise ValueError(f"Note 标签重复，无法按唯一定位路径回写: {tag_name}")

    match = matches[0]
    if match.value_span is None:
        raise ValueError(f"Note 标签没有可替换值: {tag_name}")
    start, end = match.value_span
    written_text = encode_visible_text_like(
        original_raw_text=match.value,
        translated_visible_text=translated_text,
    )
    ensure_encoded_text_valid(
        original_raw_text=match.value,
        written_raw_text=written_text,
        context=f"Note 标签 {tag_name}",
    )
    return f"{note_text[:start]}{written_text}{note_text[end:]}"


__all__: list[str] = [
    "NoteTagMatch",
    "iter_note_tag_matches",
    "replace_note_tag_value",
]
