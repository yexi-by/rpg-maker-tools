"""Rust 原生质检核心适配层。

本模块负责把 Python 业务对象转换为 Rust 扩展可处理的纯 JSON 数据，并把
Rust 多线程扫描结果恢复成项目现有质量报告明细结构。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import import_module
from typing import Protocol, cast

from app.rmmz.schema import COMMON_EVENTS_FILE_NAME, MAP_PATTERN, PLUGINS_FILE_NAME, TROOPS_FILE_NAME, SourceResidualRuleRecord, TranslationItem
from app.rmmz.text_rules import JsonArray, JsonObject, JsonValue, TextRules, coerce_json_value, ensure_json_array, ensure_json_object


class NativeModule(Protocol):
    """PyO3 扩展暴露给 Python 的最小接口。"""

    def collect_note_tag_sources(self, payload_json: str) -> str:
        """运行 Rust 多线程 Note 标签来源扫描。"""
        raise NotImplementedError

    def scan_font_replacements(self, payload_json: str) -> str:
        """运行 Rust 多线程字体引用替换扫描。"""
        raise NotImplementedError

    def scan_quality(self, payload_json: str) -> str:
        """运行 Rust 多线程质检。"""
        raise NotImplementedError

    def scan_write_protocol(self, payload_json: str) -> str:
        """运行 Rust 多线程写入协议预演。"""
        raise NotImplementedError

    def native_thread_count(self) -> int:
        """返回 Rust 当前使用的线程数。"""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class NativeQualityDetails:
    """Rust 质检核心返回的问题明细。"""

    source_residual_items: JsonArray
    text_structure_items: JsonArray
    placeholder_risk_items: JsonArray
    overwide_line_items: JsonArray


def collect_native_quality_details(
    *,
    items: list[TranslationItem],
    text_rules: TextRules,
    source_residual_rules: list[SourceResidualRuleRecord],
) -> NativeQualityDetails:
    """调用 Rust 多线程核心收集正文质量问题。"""
    native_module = _load_native_module()
    payload = {
        "items": [_build_item_payload(item) for item in items],
        "text_rules": _build_text_rules_payload(text_rules),
        "source_residual_rules": [
            {
                "location_path": record.location_path,
                "allowed_terms": list(record.allowed_terms),
                "reason": record.reason,
            }
            for record in source_residual_rules
        ],
    }
    result_text = native_module.scan_quality(json.dumps(payload, ensure_ascii=False))
    result = ensure_json_object(
        # json.loads 的返回值来自动态 JSON 边界，立即交给 coerce_json_value 收窄。
        coerce_json_value(cast(object, json.loads(result_text))),
        "native_quality_result",
    )
    return NativeQualityDetails(
        source_residual_items=ensure_json_array(
            result["source_residual_items"],
            "native_quality_result.source_residual_items",
        ),
        text_structure_items=ensure_json_array(
            result["text_structure_items"],
            "native_quality_result.text_structure_items",
        ),
        placeholder_risk_items=ensure_json_array(
            result["placeholder_risk_items"],
            "native_quality_result.placeholder_risk_items",
        ),
        overwide_line_items=ensure_json_array(
            result["overwide_line_items"],
            "native_quality_result.overwide_line_items",
        ),
    )


def native_thread_count() -> int:
    """读取 Rust 质检核心当前会使用的线程数。"""
    native_module = _load_native_module()
    count = native_module.native_thread_count()
    return count


def collect_native_write_protocol_details(
    *,
    game_data: JsonObject,
    plugins_js: JsonArray,
    items: list[TranslationItem],
) -> JsonArray:
    """调用 Rust 多线程核心检查写入协议风险。"""
    native_module = _load_native_module()
    payload = {"entries": _build_protocol_entries(game_data=game_data, plugins_js=plugins_js, items=items)}
    result_text = native_module.scan_write_protocol(json.dumps(payload, ensure_ascii=False))
    return ensure_json_array(
        # json.loads 的返回值来自动态 JSON 边界，立即交给 coerce_json_value 收窄。
        coerce_json_value(cast(object, json.loads(result_text))),
        "native_write_protocol_result",
    )


def collect_native_note_tag_sources(
    *,
    game_data: JsonObject,
    file_pattern: str | None,
) -> JsonArray:
    """调用 Rust 核心扫描所有可用 Note 标签来源。"""
    native_module = _load_native_module()
    payload: JsonObject = {
        "data": game_data,
        "file_pattern": file_pattern,
    }
    result_text = native_module.collect_note_tag_sources(json.dumps(payload, ensure_ascii=False))
    return ensure_json_array(
        # json.loads 的返回值来自动态 JSON 边界，立即交给 coerce_json_value 收窄。
        coerce_json_value(cast(object, json.loads(result_text))),
        "native_note_tag_sources",
    )


def collect_native_font_replacements(
    *,
    game_data: JsonObject,
    plugins_js: JsonArray,
    old_font_names: list[str],
    replacement_font_name: str,
) -> JsonObject:
    """调用 Rust 核心扫描本轮字体引用替换清单。"""
    native_module = _load_native_module()
    payload: JsonObject = {
        "data": game_data,
        "plugins": plugins_js,
        "old_font_names": [name for name in old_font_names],
        "replacement_font_name": replacement_font_name,
    }
    result_text = native_module.scan_font_replacements(json.dumps(payload, ensure_ascii=False))
    return ensure_json_object(
        # json.loads 的返回值来自动态 JSON 边界，立即交给 coerce_json_value 收窄。
        coerce_json_value(cast(object, json.loads(result_text))),
        "native_font_replacements",
    )


def _load_native_module() -> NativeModule:
    """加载 PyO3 扩展，缺失时给出可执行的修复提示。"""
    try:
        native_module = import_module("app._native")
    except ImportError as error:
        raise RuntimeError("Rust 原生扩展不可用，请先执行 uv run maturin develop") from error
    return cast(NativeModule, cast(object, native_module))


def _build_item_payload(item: TranslationItem) -> JsonObject:
    """把单条译文压成 Rust 只需要读取的最小结构。"""
    return {
        "location_path": item.location_path,
        "item_type": item.item_type,
        "role": item.role,
        "original_lines": [line for line in item.original_lines],
        "translation_lines": [line for line in item.translation_lines],
    }


def _build_protocol_entries(
    *,
    game_data: JsonObject,
    plugins_js: JsonArray,
    items: list[TranslationItem],
) -> JsonArray:
    """把写入协议检查压成 Rust 可并行处理的最小目标值列表。"""
    entries: JsonArray = []
    for item in items:
        location_path = item.location_path
        if location_path.startswith(f"{PLUGINS_FILE_NAME}/"):
            entries.append(_build_plugin_protocol_entry(plugins_js=plugins_js, item=item))
            continue
        if "/note/" in location_path:
            entries.append(_build_note_protocol_entry(game_data=game_data, item=item))
            continue
        if "/parameters/" in location_path:
            entries.append(_build_event_parameter_protocol_entry(game_data=game_data, item=item))
    return entries


def _build_plugin_protocol_entry(*, plugins_js: JsonArray, item: TranslationItem) -> JsonObject:
    """构造插件参数写入协议检查条目。"""
    parts = item.location_path.split("/")
    if len(parts) < 3:
        return _empty_protocol_entry(item)
    plugin = ensure_json_object(plugins_js[int(parts[1])], item.location_path)
    parameters = ensure_json_object(plugin["parameters"], f"{item.location_path}.parameters")
    return {
        "item": _build_item_payload(item),
        "mode": "nested",
        "current_value": parameters[parts[2]],
        "path_parts": [part for part in parts[3:]],
        "note_text": None,
        "tag_name": None,
    }


def _build_event_parameter_protocol_entry(*, game_data: JsonObject, item: TranslationItem) -> JsonObject:
    """构造事件指令参数写入协议检查条目。"""
    parts = item.location_path.split("/")
    command, value_parts = _locate_event_command_for_protocol(game_data=game_data, parts=parts, context=item.location_path)
    if len(value_parts) < 2 or value_parts[0] != "parameters":
        raise ValueError(f"事件指令路径缺少 parameters 段: {item.location_path}")
    parameters = ensure_json_array(command["parameters"], f"{item.location_path}.parameters")
    return {
        "item": _build_item_payload(item),
        "mode": "nested",
        "current_value": parameters[int(value_parts[1])],
        "path_parts": [part for part in value_parts[2:]],
        "note_text": None,
        "tag_name": None,
    }


def _locate_event_command_for_protocol(
    *,
    game_data: JsonObject,
    parts: list[str],
    context: str,
) -> tuple[JsonObject, list[str]]:
    """按定位路径找到事件指令对象和参数路径尾段。"""
    file_name = parts[0]
    data = game_data[file_name]
    if MAP_PATTERN.fullmatch(file_name):
        map_object = ensure_json_object(data, file_name)
        events = ensure_json_array(map_object["events"], f"{file_name}.events")
        event = ensure_json_object(events[int(parts[1])], f"{context}.event")
        pages = ensure_json_array(event["pages"], f"{context}.pages")
        page = ensure_json_object(pages[int(parts[2])], f"{context}.page")
        commands = ensure_json_array(page["list"], f"{context}.list")
        return ensure_json_object(commands[int(parts[3])], f"{context}.command"), parts[4:]
    if file_name == COMMON_EVENTS_FILE_NAME:
        events = ensure_json_array(data, file_name)
        event = ensure_json_object(events[int(parts[1])], f"{context}.event")
        commands = ensure_json_array(event["list"], f"{context}.list")
        return ensure_json_object(commands[int(parts[2])], f"{context}.command"), parts[3:]
    if file_name == TROOPS_FILE_NAME:
        troops = ensure_json_array(data, file_name)
        troop = ensure_json_object(troops[int(parts[1])], f"{context}.troop")
        pages = ensure_json_array(troop["pages"], f"{context}.pages")
        page = ensure_json_object(pages[int(parts[2])], f"{context}.page")
        commands = ensure_json_array(page["list"], f"{context}.list")
        return ensure_json_object(commands[int(parts[3])], f"{context}.command"), parts[4:]
    raise ValueError(f"无法识别的事件值路径: {context}")


def _build_note_protocol_entry(*, game_data: JsonObject, item: TranslationItem) -> JsonObject:
    """构造 Note 标签写入协议检查条目。"""
    parts = item.location_path.split("/")
    tag_name = parts[-1]
    owner = _locate_note_owner(value=game_data[parts[0]], owner_parts=parts[1:-2], context=item.location_path)
    note_value = owner.get("note")
    if not isinstance(note_value, str):
        raise ValueError(f"Note 字段不是字符串: {item.location_path}")
    return {
        "item": _build_item_payload(item),
        "mode": "note",
        "current_value": None,
        "path_parts": [],
        "note_text": note_value,
        "tag_name": tag_name,
    }


def _locate_note_owner(*, value: JsonValue, owner_parts: list[str], context: str) -> JsonObject:
    """根据 Note 标签路径定位持有 note 字段的 JSON 对象。"""
    current_value = value
    for part in owner_parts:
        if isinstance(current_value, dict):
            current_value = current_value[part]
            continue
        if isinstance(current_value, list):
            index = int(part)
            if index < len(current_value) and current_value[index] is not None:
                current_value = current_value[index]
                continue
            matched_value = next(
                (
                    candidate
                    for candidate in current_value
                    if isinstance(candidate, dict) and candidate.get("id") == index
                ),
                None,
            )
            if matched_value is None:
                raise ValueError(f"Note 路径数组索引不存在: {context}")
            current_value = matched_value
            continue
        raise ValueError(f"Note 路径无法继续定位: {context}")
    return ensure_json_object(current_value, f"{context}.note_owner")


def _empty_protocol_entry(item: TranslationItem) -> JsonObject:
    """生成会在 Rust 侧自然跳过的空协议检查条目。"""
    return {
        "item": _build_item_payload(item),
        "mode": "none",
        "current_value": None,
        "path_parts": [],
        "note_text": None,
        "tag_name": None,
    }


def _build_text_rules_payload(text_rules: TextRules) -> JsonObject:
    """把文本规则压成 Rust 质检核心的输入结构。"""
    setting = text_rules.setting
    return {
        "custom_placeholder_rules": [
            {
                "pattern_text": rule.pattern_text,
                "placeholder_template": rule.placeholder_template,
            }
            for rule in text_rules.custom_placeholder_rules
        ],
        "source_residual_allowed_chars": [char for char in setting.source_residual_allowed_chars],
        "source_residual_allowed_tail_chars": [char for char in setting.source_residual_allowed_tail_chars],
        "source_residual_segment_pattern": setting.source_residual_segment_pattern,
        "source_residual_label": setting.source_residual_label,
        "allowed_source_residual_terms": [term for term in setting.allowed_source_residual_terms],
        "source_residual_terms_ignore_case": setting.source_residual_terms_ignore_case,
        "line_width_count_pattern": setting.line_width_count_pattern,
        "residual_escape_sequence_pattern": setting.residual_escape_sequence_pattern,
        "long_text_line_width_limit": setting.long_text_line_width_limit,
    }


__all__ = [
    "NativeQualityDetails",
    "collect_native_font_replacements",
    "collect_native_note_tag_sources",
    "collect_native_quality_details",
    "collect_native_write_protocol_details",
    "native_thread_count",
]
