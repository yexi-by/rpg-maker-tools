"""自定义占位符候选扫描服务。"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.plugin_text.paths import resolve_plugin_leaves
from app.rmmz.commands import iter_all_commands
from app.rmmz.control_codes import iter_standard_control_spans
from app.rmmz.schema import GameData
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


def scan_placeholder_candidates(game_data: GameData, text_rules: TextRules) -> list[PlaceholderCandidate]:
    """扫描当前游戏数据中的反斜杠控制符候选。"""
    candidates: dict[str, PlaceholderCandidate] = {}
    for source_name, text in _iter_scan_texts(game_data):
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


def _iter_scan_texts(game_data: GameData) -> Iterable[tuple[str, str]]:
    """遍历 data、插件配置和事件指令参数中的字符串。"""
    for value in game_data.data.values():
        yield from _iter_json_strings("data", value)

    for plugin in game_data.plugins_js:
        for leaf in resolve_plugin_leaves(plugin):
            if isinstance(leaf.value, str):
                yield "plugins", leaf.value

    for _path, _display_name, command in iter_all_commands(game_data):
        for text in _iter_json_strings("event_commands", command.parameters):
            yield text


def _iter_json_strings(source_name: str, value: JsonValue) -> Iterable[tuple[str, str]]:
    """递归遍历 JSON 值中的字符串叶子。"""
    if isinstance(value, str):
        yield source_name, value
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_json_strings(source_name, item)
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_json_strings(source_name, item)


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
