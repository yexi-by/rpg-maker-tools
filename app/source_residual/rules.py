"""源文残留例外规则解析与校验。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, cast

import aiofiles
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from app.rmmz.schema import SourceResidualRuleRecord, TranslationItem
from app.rmmz.text_rules import TextRules, coerce_json_value


class StrictSourceResidualRuleModel(BaseModel):
    """源文残留例外规则严格模型基类。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


class SourceResidualRuleSpec(StrictSourceResidualRuleModel):
    """单个文本位置允许保留的源文片段。"""

    allowed_terms: list[str] = Field(default_factory=list)
    reason: str

    @field_validator("allowed_terms")
    @classmethod
    def _validate_allowed_terms(cls, value: list[str]) -> list[str]:
        """清理并校验允许保留的源文片段。"""
        normalized_terms: list[str] = []
        seen_terms: set[str] = set()
        for term in value:
            normalized_term = term.strip()
            if not normalized_term or normalized_term in seen_terms:
                continue
            normalized_terms.append(normalized_term)
            seen_terms.add(normalized_term)
        if not normalized_terms:
            raise ValueError("allowed_terms 不能为空")
        return normalized_terms

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, value: str) -> str:
        """校验例外原因必须显式填写。"""
        normalized_reason = value.strip()
        if not normalized_reason:
            raise ValueError("reason 不能为空")
        return normalized_reason


type SourceResidualRuleImportFile = dict[str, SourceResidualRuleSpec]
_SOURCE_RESIDUAL_RULE_IMPORT_ADAPTER: TypeAdapter[SourceResidualRuleImportFile] = TypeAdapter(
    SourceResidualRuleImportFile
)


@dataclass(frozen=True, slots=True)
class SourceResidualRuleSet:
    """按定位路径索引的源文残留例外规则集合。"""

    records_by_path: dict[str, SourceResidualRuleRecord]

    @classmethod
    def from_records(cls, records: Sequence[SourceResidualRuleRecord]) -> "SourceResidualRuleSet":
        """从数据库记录构建路径索引。"""
        return cls(records_by_path={record.location_path: record for record in records})

    def allowed_terms_for_path(self, location_path: str) -> list[str]:
        """读取指定路径允许保留的源文片段。"""
        record = self.records_by_path.get(location_path)
        if record is None:
            return []
        return list(record.allowed_terms)

    def reason_for_path(self, location_path: str) -> str:
        """读取指定路径的例外原因。"""
        record = self.records_by_path.get(location_path)
        if record is None:
            return ""
        return record.reason


async def load_source_residual_rule_import_file(input_path: Path) -> SourceResidualRuleImportFile:
    """读取外部源文残留例外规则 JSON 文件。"""
    resolved_path = input_path.resolve()
    if not resolved_path.exists():
        raise FileNotFoundError(f"源文残留例外规则文件不存在: {resolved_path}")
    async with aiofiles.open(resolved_path, "r", encoding="utf-8-sig") as file:
        raw_text = await file.read()
    return parse_source_residual_rule_import_text(raw_text)


def parse_source_residual_rule_import_text(raw_text: str) -> SourceResidualRuleImportFile:
    """解析外部源文残留例外规则 JSON 文本。"""
    # JSON 解析边界只能先得到动态对象，下一行立即交给 coerce_json_value 收窄。
    decoded_raw = cast(object, json.loads(raw_text))
    decoded = coerce_json_value(decoded_raw)
    return _SOURCE_RESIDUAL_RULE_IMPORT_ADAPTER.validate_python(decoded)


def build_source_residual_rule_records_from_import(
    *,
    import_file: SourceResidualRuleImportFile,
    active_items: Sequence[TranslationItem],
    translated_items: Sequence[TranslationItem],
    ignore_case: bool = False,
) -> list[SourceResidualRuleRecord]:
    """把外部例外规则转换成数据库记录，并校验定位和片段来源。"""
    active_items_by_path = {item.location_path: item for item in active_items}
    translated_items_by_path = {item.location_path: item for item in translated_items}
    records: list[SourceResidualRuleRecord] = []
    for location_path, spec in import_file.items():
        normalized_path = location_path.strip()
        if not normalized_path:
            raise ValueError("源文残留例外规则不能包含空 location_path")
        active_item = active_items_by_path.get(normalized_path)
        if active_item is None:
            raise ValueError(f"源文残留例外规则定位不在当前可提取文本范围内: {location_path}")
        _validate_allowed_terms_appear_in_item(
            location_path=normalized_path,
            allowed_terms=spec.allowed_terms,
            active_item=active_item,
            translated_item=translated_items_by_path.get(normalized_path),
            ignore_case=ignore_case,
        )
        records.append(
            SourceResidualRuleRecord(
                location_path=normalized_path,
                allowed_terms=list(spec.allowed_terms),
                reason=spec.reason,
            )
        )
    return records


def check_source_residual_for_item(
    *,
    item: TranslationItem,
    text_rules: TextRules,
    rule_set: SourceResidualRuleSet | None,
) -> None:
    """按例外规则遮蔽允许片段后检查单条译文源文残留。"""
    allowed_terms = [] if rule_set is None else rule_set.allowed_terms_for_path(item.location_path)
    text_rules.check_source_residual(item.translation_lines, allowed_terms=allowed_terms)


def _validate_allowed_terms_appear_in_item(
    *,
    location_path: str,
    allowed_terms: list[str],
    active_item: TranslationItem,
    translated_item: TranslationItem | None,
    ignore_case: bool,
) -> None:
    """确认例外片段来自当前条目的原文或已保存译文。"""
    visible_text_parts = [*active_item.original_lines]
    if translated_item is not None:
        visible_text_parts.extend(translated_item.translation_lines)
    visible_text = "\n".join(visible_text_parts)
    if ignore_case:
        visible_text_for_check = visible_text.casefold()
        missing_terms = [
            term
            for term in allowed_terms
            if term.casefold() not in visible_text_for_check
        ]
    else:
        missing_terms = [term for term in allowed_terms if term not in visible_text]
    if missing_terms:
        joined_terms = "、".join(missing_terms)
        raise ValueError(f"{location_path} 的 allowed_terms 未出现在当前条目原文或译文中: {joined_terms}")


__all__: list[str] = [
    "SourceResidualRuleImportFile",
    "SourceResidualRuleSet",
    "SourceResidualRuleSpec",
    "build_source_residual_rule_records_from_import",
    "check_source_residual_for_item",
    "load_source_residual_rule_import_file",
    "parse_source_residual_rule_import_text",
]
