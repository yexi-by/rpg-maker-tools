"""长文本断行服务。"""

from __future__ import annotations

from dataclasses import dataclass

from app.observability import logger
from app.rmmz.control_codes import LITERAL_LINE_BREAK_MARKER, LITERAL_LINE_BREAK_PLACEHOLDER
from app.rmmz.text_rules import TextRules

WRAPPING_CONTINUATION_INDENT = "　"


@dataclass(frozen=True, slots=True)
class ProtectedSpan:
    """不参与视觉宽度统计的控制片段范围。"""

    start_index: int
    end_index: int


@dataclass(frozen=True, slots=True)
class BoundaryChar:
    """文本边界处可见字符及其所在行列位置。"""

    line_index: int
    char_index: int
    char: str


@dataclass(frozen=True, slots=True)
class WrappingSpan:
    """一组已配对包裹标点在文本中的位置。"""

    left: BoundaryChar
    right: BoundaryChar
    pair: tuple[str, str]


TRANSLATED_WRAPPING_LEFT_CHARS: frozenset[str] = frozenset(
    {"“", "‘", "「", "『", "《", "〈", "（", "(", "\"", "'", "＂"}
)
TRANSLATED_WRAPPING_RIGHT_CHARS: frozenset[str] = frozenset(
    {"”", "’", "」", "』", "》", "〉", "）", ")", "\"", "'", "＂"}
)
TRANSLATED_WRAPPING_QUOTE_PAIRS: tuple[tuple[str, str], ...] = (
    ("“", "”"),
    ("‘", "’"),
    ("\"", "\""),
    ("'", "'"),
    ("＂", "＂"),
    ("『", "』"),
    ("《", "》"),
    ("〈", "〉"),
)


def align_long_text_lines(
    text: str,
    target_lines: int,
    *,
    location_path: str | None,
    text_rules: TextRules,
    original_lines: list[str] | None = None,
) -> list[str]:
    """按模型断句保留译文行，再执行行宽兜底。"""
    _ = target_lines
    lines = text.splitlines()
    if original_lines is not None:
        lines = normalize_translated_wrapping_punctuation(
            original_lines=original_lines,
            translation_lines=lines,
            text_rules=text_rules,
        )

    return split_overwide_lines(
        lines=lines,
        location_path=location_path,
        text_rules=text_rules,
    )


def split_overwide_lines(
    *,
    lines: list[str],
    location_path: str | None,
    text_rules: TextRules,
) -> list[str]:
    """按配置宽度切开过长非空行，并整理跨行包裹标点的续行缩进。"""
    split_lines: list[str] = []
    active_wrapping_pair: tuple[str, str] | None = None
    for line in lines:
        if not line:
            split_lines.append(line)
            continue

        current_wrapping_pair = active_wrapping_pair
        opening_pair = _find_opening_wrapping_pair(line=line, text_rules=text_rules)
        if current_wrapping_pair is None:
            current_wrapping_pair = opening_pair

        first_line_prefix = WRAPPING_CONTINUATION_INDENT if active_wrapping_pair is not None else ""
        wrapped_tail_prefix = WRAPPING_CONTINUATION_INDENT if current_wrapping_pair is not None else ""
        split_lines.extend(
            _split_single_overwide_line(
                line=line,
                location_path=location_path,
                text_rules=text_rules,
                first_line_prefix=first_line_prefix,
                wrapped_tail_prefix=wrapped_tail_prefix,
            )
        )

        if current_wrapping_pair is None:
            active_wrapping_pair = None
            continue
        if _closes_wrapping_pair(
            line=line,
            wrapping_pair=current_wrapping_pair,
            text_rules=text_rules,
        ):
            active_wrapping_pair = None
        else:
            active_wrapping_pair = current_wrapping_pair
    return split_lines


def split_overwide_single_text_value_if_needed(
    *,
    original_lines: list[str],
    translation_text: str,
    location_path: str | None,
    text_rules: TextRules,
) -> str:
    """对承载多行显示内容的单值文本执行行宽兜底。

    Note 标签、插件参数等来源在数据库里可能只能作为一个字符串写回，但字符串
    内部的换行会被游戏窗口当作多行显示。只要源文或译文已经带有换行，就按
    显示行拆开执行与 long_text 相同的宽度保护，再重新拼回单个字符串。
    """
    if _has_literal_line_break_marker(original_lines):
        line_break_token = (
            LITERAL_LINE_BREAK_PLACEHOLDER
            if LITERAL_LINE_BREAK_PLACEHOLDER in translation_text
            else LITERAL_LINE_BREAK_MARKER
        )
        normalized_text = translation_text.replace(LITERAL_LINE_BREAK_PLACEHOLDER, line_break_token)
        normalized_text = normalized_text.replace(LITERAL_LINE_BREAK_MARKER, line_break_token)
        normalized_text = normalized_text.replace("\n", line_break_token)
        return line_break_token.join(
            split_overwide_lines(
                lines=normalized_text.split(line_break_token),
                location_path=location_path,
                text_rules=text_rules,
            )
        )
    if "\n" not in translation_text and not _has_embedded_line_break(original_lines):
        return translation_text
    return "\n".join(
        split_overwide_lines(
            lines=translation_text.split("\n"),
            location_path=location_path,
            text_rules=text_rules,
        )
    )


def _has_literal_line_break_marker(lines: list[str]) -> bool:
    """判断源文是否用字面量反斜杠 n 表达游戏内换行。"""
    return any(LITERAL_LINE_BREAK_MARKER in line for line in lines)


def count_line_width_chars(text: str, text_rules: TextRules) -> int:
    """统计参与长文本行宽判断的可见字符数量。"""
    protected_spans = _collect_protected_spans(text=text, text_rules=text_rules)
    count = 0
    for index, char in enumerate(text):
        if _is_inside_protected_span(index=index, protected_spans=protected_spans):
            continue
        if text_rules.is_line_width_counted_char(char):
            count += 1
    return count


def normalize_translated_wrapping_punctuation(
    *,
    original_lines: list[str],
    translation_lines: list[str],
    text_rules: TextRules,
) -> list[str]:
    """把源文包裹标点被模型改写的译文修回源文标点。

    外层包裹标点先按首尾边界修复；内层包裹标点只在源文对数与译文可配对
    引号对数一致时按顺序修复。无法安全一一对应时保持原样，避免误改模型
    正常新增的中文引号。
    """
    normalized_lines = _normalize_translated_outer_wrapping_punctuation(
        original_lines=original_lines,
        translation_lines=translation_lines,
        text_rules=text_rules,
    )
    return _normalize_aligned_wrapping_spans(
        original_lines=original_lines,
        translation_lines=normalized_lines,
        text_rules=text_rules,
    )


def normalize_translated_outer_wrapping_punctuation(
    *,
    original_lines: list[str],
    translation_lines: list[str],
    text_rules: TextRules,
) -> list[str]:
    """兼容旧调用名，执行完整包裹标点规范化。"""
    return normalize_translated_wrapping_punctuation(
        original_lines=original_lines,
        translation_lines=translation_lines,
        text_rules=text_rules,
    )


def _normalize_translated_outer_wrapping_punctuation(
    *,
    original_lines: list[str],
    translation_lines: list[str],
    text_rules: TextRules,
) -> list[str]:
    """按首尾边界修复外层包裹标点。"""
    source_pair = _find_source_outer_wrapping_pair(original_lines=original_lines, text_rules=text_rules)
    if source_pair is None:
        return list(translation_lines)

    normalized_lines = list(translation_lines)
    source_left, source_right = source_pair
    first_boundary = _find_first_visible_boundary(lines=normalized_lines, text_rules=text_rules)
    last_boundary = _find_last_visible_boundary(lines=normalized_lines, text_rules=text_rules)
    if first_boundary is None or last_boundary is None:
        return normalized_lines

    if first_boundary.char != source_left and first_boundary.char in TRANSLATED_WRAPPING_LEFT_CHARS:
        normalized_lines[first_boundary.line_index] = _replace_char_at(
            text=normalized_lines[first_boundary.line_index],
            index=first_boundary.char_index,
            char=source_left,
        )
    if last_boundary.char != source_right and last_boundary.char in TRANSLATED_WRAPPING_RIGHT_CHARS:
        normalized_lines[last_boundary.line_index] = _replace_char_at(
            text=normalized_lines[last_boundary.line_index],
            index=last_boundary.char_index,
            char=source_right,
        )
    return normalized_lines


def _normalize_aligned_wrapping_spans(
    *,
    original_lines: list[str],
    translation_lines: list[str],
    text_rules: TextRules,
) -> list[str]:
    """在可安全一一对应时修复文本内部被替换的包裹标点。"""
    source_pairs = tuple(text_rules.setting.strip_wrapping_punctuation_pairs)
    source_spans = _collect_wrapping_spans(
        lines=original_lines,
        pair_definitions=source_pairs,
        text_rules=text_rules,
    )
    if not source_spans:
        return list(translation_lines)

    translated_source_spans = _collect_wrapping_spans(
        lines=translation_lines,
        pair_definitions=source_pairs,
        text_rules=text_rules,
    )
    alternative_pairs = _build_alternative_wrapping_pairs(source_pairs)
    translated_alternative_spans = _collect_wrapping_spans(
        lines=translation_lines,
        pair_definitions=alternative_pairs,
        text_rules=text_rules,
    )
    if _has_unpaired_wrapping_chars(
        lines=translation_lines,
        pair_definitions=source_pairs,
        spans=translated_source_spans,
        text_rules=text_rules,
    ):
        return list(translation_lines)
    if _has_unpaired_wrapping_chars(
        lines=translation_lines,
        pair_definitions=alternative_pairs,
        spans=translated_alternative_spans,
        text_rules=text_rules,
    ):
        return list(translation_lines)
    translated_spans = sorted(
        [*translated_source_spans, *translated_alternative_spans],
        key=lambda span: (
            span.left.line_index,
            span.left.char_index,
            span.right.line_index,
            span.right.char_index,
        ),
    )
    if len(source_spans) != len(translated_spans):
        return list(translation_lines)

    normalized_lines = list(translation_lines)
    for source_span, translated_span in zip(source_spans, translated_spans, strict=True):
        source_left, source_right = source_span.pair
        if translated_span.left.char != source_left:
            normalized_lines[translated_span.left.line_index] = _replace_char_at(
                text=normalized_lines[translated_span.left.line_index],
                index=translated_span.left.char_index,
                char=source_left,
            )
        if translated_span.right.char != source_right:
            normalized_lines[translated_span.right.line_index] = _replace_char_at(
                text=normalized_lines[translated_span.right.line_index],
                index=translated_span.right.char_index,
                char=source_right,
            )
    return normalized_lines


def _split_single_overwide_line(
    *,
    line: str,
    location_path: str | None,
    text_rules: TextRules,
    first_line_prefix: str = "",
    wrapped_tail_prefix: str = "",
) -> list[str]:
    """切开单个超宽文本行。"""
    line_width_limit = text_rules.setting.long_text_line_width_limit
    result: list[str] = []
    pending_line = _prepend_continuation_prefix(line=line, prefix=first_line_prefix)
    while count_line_width_chars(pending_line, text_rules) > line_width_limit:
        split_position = _find_preferred_split_position(pending_line, text_rules)
        if split_position is None:
            split_position = _find_hard_split_position(pending_line, text_rules)

        if split_position is None or split_position <= 0 or split_position >= len(pending_line):
            _log_align_warning(
                location_path=location_path,
                line=pending_line,
                reason="无法找到安全切分点，保留当前行",
                text_rules=text_rules,
            )
            break

        head = pending_line[:split_position].rstrip()
        tail = pending_line[split_position:].lstrip()
        if not head or not tail:
            _log_align_warning(
                location_path=location_path,
                line=pending_line,
                reason="切分后出现空片段，保留当前行",
                text_rules=text_rules,
            )
            break

        result.append(head)
        pending_line = _prepend_continuation_prefix(line=tail, prefix=wrapped_tail_prefix)

    result.append(pending_line)
    return result


def _build_alternative_wrapping_pairs(source_pairs: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    """生成可被自动修回源文包裹标点的译文替代引号对。"""
    source_pair_set = set(source_pairs)
    return tuple(pair for pair in TRANSLATED_WRAPPING_QUOTE_PAIRS if pair not in source_pair_set)


def _has_embedded_line_break(lines: list[str]) -> bool:
    """判断文本行列表中是否存在作为字段内容保存的换行。"""
    return any("\n" in line for line in lines)


def _has_unpaired_wrapping_chars(
    *,
    lines: list[str],
    pair_definitions: tuple[tuple[str, str], ...],
    spans: list[WrappingSpan],
    text_rules: TextRules,
) -> bool:
    """判断文本中是否存在未被配对算法消费的包裹标点字符。"""
    wrapping_chars = {char for pair in pair_definitions for char in pair}
    if not wrapping_chars:
        return False
    paired_positions = {
        (span.left.line_index, span.left.char_index)
        for span in spans
    } | {
        (span.right.line_index, span.right.char_index)
        for span in spans
    }
    for boundary in _collect_visible_chars(lines=lines, text_rules=text_rules):
        if boundary.char not in wrapping_chars:
            continue
        if (boundary.line_index, boundary.char_index) not in paired_positions:
            return True
    return False


def _collect_wrapping_spans(
    *,
    lines: list[str],
    pair_definitions: tuple[tuple[str, str], ...],
    text_rules: TextRules,
) -> list[WrappingSpan]:
    """按可见字符顺序收集已配对的包裹标点。"""
    visible_chars = _collect_visible_chars(lines=lines, text_rules=text_rules)
    different_char_pairs = tuple(pair for pair in pair_definitions if pair[0] != pair[1])
    same_char_pairs = tuple(pair for pair in pair_definitions if pair[0] == pair[1])
    spans = _collect_different_char_wrapping_spans(
        visible_chars=visible_chars,
        pair_definitions=different_char_pairs,
    )
    spans.extend(
        _collect_same_char_wrapping_spans(
            visible_chars=visible_chars,
            pair_definitions=same_char_pairs,
        )
    )
    return sorted(
        spans,
        key=lambda span: (
            span.left.line_index,
            span.left.char_index,
            span.right.line_index,
            span.right.char_index,
        ),
    )


def _collect_different_char_wrapping_spans(
    *,
    visible_chars: list[BoundaryChar],
    pair_definitions: tuple[tuple[str, str], ...],
) -> list[WrappingSpan]:
    """收集左右字符不同的包裹标点对。"""
    left_to_pair = {left: (left, right) for left, right in pair_definitions}
    right_chars = {right for _left, right in pair_definitions}
    spans: list[WrappingSpan] = []
    stack: list[tuple[BoundaryChar, tuple[str, str]]] = []
    for boundary in visible_chars:
        pair = left_to_pair.get(boundary.char)
        if pair is not None:
            stack.append((boundary, pair))
            continue
        if boundary.char not in right_chars:
            continue
        if not stack:
            continue
        left_boundary, expected_pair = stack[-1]
        if expected_pair[1] != boundary.char:
            continue
        _ = stack.pop()
        spans.append(WrappingSpan(left=left_boundary, right=boundary, pair=expected_pair))
    return spans


def _collect_same_char_wrapping_spans(
    *,
    visible_chars: list[BoundaryChar],
    pair_definitions: tuple[tuple[str, str], ...],
) -> list[WrappingSpan]:
    """收集左右字符相同的直引号包裹对。"""
    quote_chars = {left for left, _right in pair_definitions}
    open_boundaries: dict[str, BoundaryChar] = {}
    spans: list[WrappingSpan] = []
    for boundary in visible_chars:
        if boundary.char not in quote_chars:
            continue
        open_boundary = open_boundaries.get(boundary.char)
        if open_boundary is None:
            open_boundaries[boundary.char] = boundary
            continue
        spans.append(WrappingSpan(left=open_boundary, right=boundary, pair=(boundary.char, boundary.char)))
        del open_boundaries[boundary.char]
    return spans


def _collect_visible_chars(
    *,
    lines: list[str],
    text_rules: TextRules,
) -> list[BoundaryChar]:
    """收集多行文本中不属于控制符且非空白的可见字符位置。"""
    visible_chars: list[BoundaryChar] = []
    for line_index, line in enumerate(lines):
        protected_spans = _collect_protected_spans(text=line, text_rules=text_rules)
        index = 0
        while index < len(line):
            containing_span = _find_containing_span(index=index, protected_spans=protected_spans)
            if containing_span is not None:
                index = containing_span.end_index
                continue
            char = line[index]
            if not char.isspace():
                visible_chars.append(
                    BoundaryChar(
                        line_index=line_index,
                        char_index=index,
                        char=char,
                    )
                )
            index += 1
    return visible_chars


def _find_source_outer_wrapping_pair(
    *,
    original_lines: list[str],
    text_rules: TextRules,
) -> tuple[str, str] | None:
    """按源文首尾可见字符判断是否存在需要保留的外层包裹标点。"""
    first_boundary = _find_first_visible_boundary(lines=original_lines, text_rules=text_rules)
    last_boundary = _find_last_visible_boundary(lines=original_lines, text_rules=text_rules)
    if first_boundary is None or last_boundary is None:
        return None
    for left, right in text_rules.setting.strip_wrapping_punctuation_pairs:
        if first_boundary.char == left and last_boundary.char == right:
            return left, right
    return None


def _find_first_visible_boundary(
    *,
    lines: list[str],
    text_rules: TextRules,
) -> BoundaryChar | None:
    """查找多行文本首个不属于控制符或空白的可见字符。"""
    for line_index, line in enumerate(lines):
        boundary = _find_visible_boundary_in_line(line=line, text_rules=text_rules, reverse=False)
        if boundary is None:
            continue
        return BoundaryChar(
            line_index=line_index,
            char_index=boundary.char_index,
            char=boundary.char,
        )
    return None


def _find_last_visible_boundary(
    *,
    lines: list[str],
    text_rules: TextRules,
) -> BoundaryChar | None:
    """查找多行文本末个不属于控制符或空白的可见字符。"""
    for reverse_line_index, line in enumerate(reversed(lines)):
        boundary = _find_visible_boundary_in_line(line=line, text_rules=text_rules, reverse=True)
        if boundary is None:
            continue
        return BoundaryChar(
            line_index=len(lines) - reverse_line_index - 1,
            char_index=boundary.char_index,
            char=boundary.char,
        )
    return None


def _find_visible_boundary_in_line(
    *,
    line: str,
    text_rules: TextRules,
    reverse: bool,
) -> BoundaryChar | None:
    """在单行内查找首个或末个可见边界字符。"""
    protected_spans = _collect_protected_spans(text=line, text_rules=text_rules)
    if reverse:
        return _find_visible_boundary_from_right(line=line, protected_spans=protected_spans)
    return _find_visible_boundary_from_left(line=line, protected_spans=protected_spans)


def _find_visible_boundary_from_left(*, line: str, protected_spans: list[ProtectedSpan]) -> BoundaryChar | None:
    """从左侧查找不在受保护片段中的可见字符。"""
    index = 0
    while index < len(line):
        containing_span = _find_containing_span(index=index, protected_spans=protected_spans)
        if containing_span is not None:
            index = containing_span.end_index
            continue
        char = line[index]
        if char.isspace():
            index += 1
            continue
        return BoundaryChar(line_index=0, char_index=index, char=char)
    return None


def _find_visible_boundary_from_right(*, line: str, protected_spans: list[ProtectedSpan]) -> BoundaryChar | None:
    """从右侧查找不在受保护片段中的可见字符。"""
    index = len(line) - 1
    while index >= 0:
        containing_span = _find_containing_span(index=index, protected_spans=protected_spans)
        if containing_span is not None:
            index = containing_span.start_index - 1
            continue
        char = line[index]
        if char.isspace():
            index -= 1
            continue
        return BoundaryChar(line_index=0, char_index=index, char=char)
    return None


def _find_containing_span(*, index: int, protected_spans: list[ProtectedSpan]) -> ProtectedSpan | None:
    """返回包含指定字符下标的受保护片段。"""
    for span in protected_spans:
        if span.start_index <= index < span.end_index:
            return span
    return None


def _replace_char_at(*, text: str, index: int, char: str) -> str:
    """替换文本指定下标处的单个字符。"""
    return f"{text[:index]}{char}{text[index + 1:]}"


def _find_opening_wrapping_pair(*, line: str, text_rules: TextRules) -> tuple[str, str] | None:
    """返回当前行开头命中的包裹标点配置。"""
    stripped_line = _build_wrapping_check_line(line=line, text_rules=text_rules)
    for left, right in text_rules.setting.strip_wrapping_punctuation_pairs:
        if stripped_line.startswith(left):
            return left, right
    return None


def _closes_wrapping_pair(
    *,
    line: str,
    wrapping_pair: tuple[str, str],
    text_rules: TextRules,
) -> bool:
    """判断当前逻辑行是否结束了跨行包裹标点块。"""
    _, right = wrapping_pair
    stripped_line = _build_wrapping_check_line(line=line, text_rules=text_rules)
    return stripped_line.endswith(right)


def _build_wrapping_check_line(*, line: str, text_rules: TextRules) -> str:
    """去掉控制符后生成包裹标点状态判定用文本。"""
    return text_rules.strip_rm_control_sequences(line).strip()


def _prepend_continuation_prefix(*, line: str, prefix: str) -> str:
    """给包裹标点续行补视觉缩进，避免重复添加已有空白。"""
    if not prefix or not line:
        return line
    if line.startswith(prefix):
        return line
    first_char = line[0]
    if first_char.isspace():
        return line
    return f"{prefix}{line}"


def _find_preferred_split_position(text: str, text_rules: TextRules) -> int | None:
    """在宽度上限附近寻找自然标点切分点。"""
    protected_spans = _collect_protected_spans(text=text, text_rules=text_rules)
    width_limit = text_rules.setting.long_text_line_width_limit
    min_preferred_width = max(1, int(width_limit * 0.45))
    before_limit_positions: list[int] = []
    preferred_before_limit_positions: list[int] = []
    punctuations = set(text_rules.setting.line_split_punctuations)
    line_width_count = 0

    for index, char in enumerate(text):
        if _is_inside_protected_span(index=index, protected_spans=protected_spans):
            continue
        if text_rules.is_line_width_counted_char(char):
            line_width_count += 1

        if char in punctuations and line_width_count >= min_preferred_width:
            if line_width_count <= width_limit:
                preferred_before_limit_positions.append(index + 1)
        if char in punctuations and line_width_count <= width_limit:
            before_limit_positions.append(index + 1)

        if line_width_count > width_limit:
            break

    return _select_split_position_with_readable_tail(
        text=text,
        candidates=preferred_before_limit_positions or before_limit_positions,
        text_rules=text_rules,
    )


def _select_split_position_with_readable_tail(
    *,
    text: str,
    candidates: list[int],
    text_rules: TextRules,
) -> int | None:
    """选择不会把极短语气标点甩到下一行的切分位置。"""
    if not candidates:
        return None
    min_tail_width = min(4, max(1, text_rules.setting.long_text_line_width_limit // 4))
    for position in reversed(candidates):
        tail = text[position:].lstrip()
        if count_line_width_chars(tail, text_rules) >= min_tail_width:
            return position
    return candidates[-1]


def _find_hard_split_position(text: str, text_rules: TextRules) -> int | None:
    """在没有可用标点时按计数字符上限切分。"""
    protected_spans = _collect_protected_spans(text=text, text_rules=text_rules)
    line_width_count = 0
    limit = text_rules.setting.long_text_line_width_limit
    for index, char in enumerate(text):
        if _is_inside_protected_span(index=index, protected_spans=protected_spans):
            continue
        if not text_rules.is_line_width_counted_char(char):
            continue
        line_width_count += 1
        if line_width_count < limit:
            continue
        split_position = _move_split_position_outside_protected_span(
            position=index + 1,
            protected_spans=protected_spans,
        )
        extended_position = _extend_split_position_through_trailing_punctuation(
            text=text,
            position=split_position,
            text_rules=text_rules,
            protected_spans=protected_spans,
        )
        if extended_position >= len(text) and count_line_width_chars(text, text_rules) > limit:
            readable_position = _find_readable_hard_split_position(
                text=text,
                max_position=split_position,
                text_rules=text_rules,
                protected_spans=protected_spans,
            )
            if readable_position is not None:
                return readable_position
        return extended_position
    return None


def _find_readable_hard_split_position(
    *,
    text: str,
    max_position: int,
    text_rules: TextRules,
    protected_spans: list[ProtectedSpan],
) -> int | None:
    """尾部标点导致硬切失败时，回退到能保留可读尾段的位置。"""
    min_tail_width = min(4, max(1, text_rules.setting.long_text_line_width_limit // 4))
    punctuations = set(text_rules.setting.line_split_punctuations)
    candidates: list[int] = []
    for index, char in enumerate(text):
        position = index + 1
        if position > max_position:
            break
        if position >= len(text):
            break
        if _is_inside_protected_span(index=index, protected_spans=protected_spans):
            continue
        if not text_rules.is_line_width_counted_char(char):
            continue
        tail = text[position:].lstrip()
        if not tail:
            continue
        if tail[0] in punctuations:
            continue
        if count_line_width_chars(tail, text_rules) < min_tail_width:
            continue
        candidates.append(position)
    if not candidates:
        return None
    return candidates[-1]


def _extend_split_position_through_trailing_punctuation(
    *,
    text: str,
    position: int,
    text_rules: TextRules,
    protected_spans: list[ProtectedSpan],
) -> int:
    """硬切后把紧邻标点留在上一行，避免下一行以标点开头。"""
    punctuations = set(text_rules.setting.line_split_punctuations)
    next_position = position
    while next_position < len(text):
        if _is_inside_protected_span(index=next_position, protected_spans=protected_spans):
            break
        if text[next_position] not in punctuations:
            break
        next_position += 1
    return next_position


def _collect_protected_spans(text: str, text_rules: TextRules) -> list[ProtectedSpan]:
    """收集占位符和 RPG Maker 控制符范围。"""
    spans = [
        ProtectedSpan(start_index=match.start(), end_index=match.end())
        for match in text_rules.placeholder_token_pattern.finditer(text)
    ]
    spans.extend(
        ProtectedSpan(start_index=span.start_index, end_index=span.end_index)
        for span in text_rules.iter_control_sequence_spans(text)
    )
    return sorted(spans, key=lambda span: (span.start_index, span.end_index))


def _is_inside_protected_span(*, index: int, protected_spans: list[ProtectedSpan]) -> bool:
    """判断字符位置是否位于受保护片段内部。"""
    return any(span.start_index <= index < span.end_index for span in protected_spans)


def _move_split_position_outside_protected_span(
    *,
    position: int,
    protected_spans: list[ProtectedSpan],
) -> int:
    """把切分点移动到受保护片段之后，避免破坏控制符。"""
    for span in protected_spans:
        if span.start_index < position < span.end_index:
            return span.end_index
    return position


def _build_warning_preview(text: str, max_length: int = 40) -> str:
    """生成日志预览文本，避免告警刷屏。"""
    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}..."


def _log_align_warning(*, location_path: str | None, line: str, reason: str, text_rules: TextRules) -> None:
    """记录长文本自动补切行失败的告警日志。"""
    logger.warning(
        "长文本自动补切行告警: 路径={}，计数字符数={}，上限={}，原因={}，内容预览={}",
        location_path or "<unknown>",
        count_line_width_chars(line, text_rules),
        text_rules.setting.long_text_line_width_limit,
        reason,
        _build_warning_preview(line),
    )


__all__: list[str] = [
    "align_long_text_lines",
    "count_line_width_chars",
    "normalize_translated_outer_wrapping_punctuation",
    "normalize_translated_wrapping_punctuation",
    "split_overwide_single_text_value_if_needed",
    "split_overwide_lines",
]
