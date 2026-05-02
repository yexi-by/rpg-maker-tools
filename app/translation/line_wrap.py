"""长文本断行服务。"""

from __future__ import annotations

from dataclasses import dataclass

from app.observability import logger
from app.rmmz.text_rules import TextRules

WRAPPING_CONTINUATION_INDENT = "　"


@dataclass(frozen=True, slots=True)
class ProtectedSpan:
    """不参与视觉宽度统计的控制片段范围。"""

    start_index: int
    end_index: int


def align_long_text_lines(
    text: str,
    target_lines: int,
    *,
    location_path: str | None,
    text_rules: TextRules,
) -> list[str]:
    """按模型断句保留译文行，再执行行宽兜底。"""
    _ = target_lines
    lines = text.splitlines()

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
        return _extend_split_position_through_trailing_punctuation(
            text=text,
            position=split_position,
            text_rules=text_rules,
            protected_spans=protected_spans,
        )
    return None


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
    "split_overwide_lines",
]
