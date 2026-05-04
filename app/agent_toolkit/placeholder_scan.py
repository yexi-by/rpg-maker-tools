"""自定义占位符候选扫描服务。"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.rmmz.control_codes import iter_standard_control_spans
from app.rmmz.schema import TranslationData, TranslationItem
from app.rmmz.text_rules import JsonValue, TextRules

CONTROL_CANDIDATE_PATTERN: re.Pattern[str] = re.compile(
    r"\\(?:[A-Za-z]+\d*(?:\[[^\]\r\n]*\])?|[{}\\$.\|!><^])"
)


@dataclass(slots=True)
class PlaceholderCandidate:
    """单个疑似控制符候选。"""

    marker: str
    count: int = 0
    sources: set[str] = field(default_factory=set)
    standard_covered: bool = False
    custom_covered: bool = False


def scan_placeholder_candidates(
    translation_data_map: dict[str, TranslationData],
    text_rules: TextRules,
) -> list[PlaceholderCandidate]:
    """扫描当前会进入正文翻译的文本中的反斜杠控制符候选。"""
    candidates: dict[str, PlaceholderCandidate] = {}
    for source_name, text in _iter_scan_texts(translation_data_map):
        for match in CONTROL_CANDIDATE_PATTERN.finditer(text):
            marker = match.group(0)
            candidate = candidates.get(marker)
            if candidate is None:
                candidate = PlaceholderCandidate(marker=marker)
                candidates[marker] = candidate
            candidate.count += 1
            candidate.sources.add(source_name)

    for candidate in candidates.values():
        candidate.standard_covered = _is_standard_covered(candidate.marker)
        candidate.custom_covered = _is_custom_covered(candidate.marker, text_rules)

    return sorted(
        candidates.values(),
        key=lambda item: (item.standard_covered, item.custom_covered, item.marker.lower()),
    )


def placeholder_candidates_to_details(candidates: list[PlaceholderCandidate]) -> list[JsonValue]:
    """把候选集合转换成报告 JSON 明细。"""
    details: list[JsonValue] = []
    for candidate in candidates:
        sources: list[JsonValue] = list(sorted(candidate.sources))
        item: dict[str, JsonValue] = {
            "marker": candidate.marker,
            "count": candidate.count,
            "sources": sources,
            "standard_covered": candidate.standard_covered,
            "custom_covered": candidate.custom_covered,
            "covered": candidate.standard_covered or candidate.custom_covered,
        }
        details.append(item)
    return details


def count_uncovered_candidates(candidates: list[PlaceholderCandidate]) -> int:
    """统计未被标准或自定义规则覆盖的候选数量。"""
    return sum(
        1
        for candidate in candidates
        if not candidate.standard_covered and not candidate.custom_covered
    )


def _iter_scan_texts(
    translation_data_map: dict[str, TranslationData],
) -> Iterable[tuple[str, str]]:
    """遍历当前提取规则确认会进入模型正文的原文行。"""
    for translation_data in translation_data_map.values():
        for item in translation_data.translation_items:
            yield from _iter_item_scan_texts(item)


def _iter_item_scan_texts(item: TranslationItem) -> Iterable[tuple[str, str]]:
    """逐行返回单个正文条目的原文。"""
    for line_index, text in enumerate(item.original_lines):
        yield f"{item.location_path}#{line_index}", text


def _is_standard_covered(marker: str) -> bool:
    """判断候选是否完整命中内置 RMMZ 标准控制符规则。"""
    return any(
        span.start_index == 0 and span.end_index == len(marker)
        for span in iter_standard_control_spans(marker)
    )


def _is_custom_covered(marker: str, text_rules: TextRules) -> bool:
    """判断候选是否完整命中自定义占位符规则。"""
    return any(rule.pattern.fullmatch(marker) is not None for rule in text_rules.custom_placeholder_rules)


__all__: list[str] = [
    "PlaceholderCandidate",
    "count_uncovered_candidates",
    "placeholder_candidates_to_details",
    "scan_placeholder_candidates",
]
