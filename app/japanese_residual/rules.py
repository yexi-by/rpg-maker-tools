"""日文残留例外规则解析与校验。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, cast

import aiofiles
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from app.rmmz.schema import JapaneseResidualRuleRecord, TranslationItem
from app.rmmz.text_rules import TextRules, coerce_json_value


class StrictJapaneseResidualRuleModel(BaseModel):
    """日文残留例外规则严格模型基类。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


class JapaneseResidualRuleSpec(StrictJapaneseResidualRuleModel):
    """单个文本位置允许保留的日文片段。"""

    allowed_terms: list[str] = Field(default_factory=list)
    reason: str

    @field_validator("allowed_terms")
    @classmethod
    def _validate_allowed_terms(cls, value: list[str]) -> list[str]:
        """清理并校验允许保留的日文片段。"""
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


type JapaneseResidualRuleImportFile = dict[str, JapaneseResidualRuleSpec]
_JAPANESE_RESIDUAL_RULE_IMPORT_ADAPTER: TypeAdapter[JapaneseResidualRuleImportFile] = TypeAdapter(
    JapaneseResidualRuleImportFile
)


@dataclass(frozen=True, slots=True)
class JapaneseResidualRuleSet:
    """按定位路径索引的日文残留例外规则集合。"""

    records_by_path: dict[str, JapaneseResidualRuleRecord]

    @classmethod
    def from_records(cls, records: Sequence[JapaneseResidualRuleRecord]) -> "JapaneseResidualRuleSet":
        """从数据库记录构建路径索引。"""
        return cls(records_by_path={record.location_path: record for record in records})

    def allowed_terms_for_path(self, location_path: str) -> list[str]:
        """读取指定路径允许保留的日文片段。"""
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


async def load_japanese_residual_rule_import_file(input_path: Path) -> JapaneseResidualRuleImportFile:
    """读取外部日文残留例外规则 JSON 文件。"""
    resolved_path = input_path.resolve()
    if not resolved_path.exists():
        raise FileNotFoundError(f"日文残留例外规则文件不存在: {resolved_path}")
    async with aiofiles.open(resolved_path, "r", encoding="utf-8-sig") as file:
        raw_text = await file.read()
    return parse_japanese_residual_rule_import_text(raw_text)


def parse_japanese_residual_rule_import_text(raw_text: str) -> JapaneseResidualRuleImportFile:
    """解析外部日文残留例外规则 JSON 文本。"""
    # JSON 解析边界只能先得到动态对象，下一行立即交给 coerce_json_value 收窄。
    decoded_raw = cast(object, json.loads(raw_text))
    decoded = coerce_json_value(decoded_raw)
    return _JAPANESE_RESIDUAL_RULE_IMPORT_ADAPTER.validate_python(decoded)


def build_japanese_residual_rule_records_from_import(
    *,
    import_file: JapaneseResidualRuleImportFile,
    active_items: Sequence[TranslationItem],
    translated_items: Sequence[TranslationItem],
) -> list[JapaneseResidualRuleRecord]:
    """把外部例外规则转换成数据库记录，并校验定位和片段来源。"""
    active_items_by_path = {item.location_path: item for item in active_items}
    translated_items_by_path = {item.location_path: item for item in translated_items}
    records: list[JapaneseResidualRuleRecord] = []
    for location_path, spec in import_file.items():
        normalized_path = location_path.strip()
        if not normalized_path:
            raise ValueError("日文残留例外规则不能包含空 location_path")
        active_item = active_items_by_path.get(normalized_path)
        if active_item is None:
            raise ValueError(f"日文残留例外规则定位不在当前可提取文本范围内: {location_path}")
        _validate_allowed_terms_appear_in_item(
            location_path=normalized_path,
            allowed_terms=spec.allowed_terms,
            active_item=active_item,
            translated_item=translated_items_by_path.get(normalized_path),
        )
        records.append(
            JapaneseResidualRuleRecord(
                location_path=normalized_path,
                allowed_terms=list(spec.allowed_terms),
                reason=spec.reason,
            )
        )
    return records


def check_japanese_residual_for_item(
    *,
    item: TranslationItem,
    text_rules: TextRules,
    rule_set: JapaneseResidualRuleSet | None,
) -> None:
    """按例外规则遮蔽允许片段后检查单条译文日文残留。"""
    allowed_terms = [] if rule_set is None else rule_set.allowed_terms_for_path(item.location_path)
    checked_lines = mask_japanese_residual_allowed_terms(
        lines=item.translation_lines,
        allowed_terms=allowed_terms,
    )
    text_rules.check_japanese_residual(checked_lines)


def mask_japanese_residual_allowed_terms(*, lines: list[str], allowed_terms: list[str]) -> list[str]:
    """用空格遮蔽允许保留的日文片段，供残留检测复用。"""
    if not allowed_terms:
        return list(lines)
    sorted_terms = sorted(allowed_terms, key=len, reverse=True)
    masked_lines: list[str] = []
    for line in lines:
        masked_line = line
        for term in sorted_terms:
            masked_line = masked_line.replace(term, " ")
        masked_lines.append(masked_line)
    return masked_lines


def _validate_allowed_terms_appear_in_item(
    *,
    location_path: str,
    allowed_terms: list[str],
    active_item: TranslationItem,
    translated_item: TranslationItem | None,
) -> None:
    """确认例外片段来自当前条目的原文或已入库译文。"""
    visible_text_parts = [*active_item.original_lines]
    if translated_item is not None:
        visible_text_parts.extend(translated_item.translation_lines)
    visible_text = "\n".join(visible_text_parts)
    missing_terms = [term for term in allowed_terms if term not in visible_text]
    if missing_terms:
        joined_terms = "、".join(missing_terms)
        raise ValueError(f"{location_path} 的 allowed_terms 未出现在当前条目原文或译文中: {joined_terms}")


__all__: list[str] = [
    "JapaneseResidualRuleImportFile",
    "JapaneseResidualRuleSet",
    "JapaneseResidualRuleSpec",
    "build_japanese_residual_rule_records_from_import",
    "check_japanese_residual_for_item",
    "load_japanese_residual_rule_import_file",
    "mask_japanese_residual_allowed_terms",
    "parse_japanese_residual_rule_import_text",
]
