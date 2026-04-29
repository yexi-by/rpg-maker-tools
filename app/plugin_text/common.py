"""
插件文本分析与提取共用工具模块。

本模块提供插件参数树展开、受限 JSONPath 匹配、AI 返回解析和哈希能力。
日文核心版不再把语言分支写入提示词或缓存指纹。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal, TypedDict, cast

from json_repair import loads as repair_json_loads
from pydantic import BaseModel, ValidationError, field_validator, model_validator

from app.rmmz.schema import PluginTextTranslateRule
from app.rmmz.text_rules import JsonValue, TextRules, coerce_json_value
from app.plugin_text.paths import (
    ResolvedLeaf,
    build_allowed_templates,
    expand_rule_to_leaf_paths,
    jsonpath_matches_template,
    jsonpath_to_location_path,
    jsonpath_to_path_parts,
    resolve_plugin_leaves,
)

MARKDOWN_JSON_FENCE_PATTERN: re.Pattern[str] = re.compile(
    r"^```(?:json)?\s*(?P<body>[\s\S]*?)\s*```$",
    re.IGNORECASE,
)
type PluginAnalysisMessageRole = Literal["system", "user", "assistant"]


class PluginAnalysisMessage(TypedDict):
    """插件分析阶段发送给模型的单条消息。"""

    role: PluginAnalysisMessageRole
    content: str


class PluginAnalysisResponse(BaseModel):
    """AI 返回的插件分析结果结构。"""

    plugin_name: str
    plugin_index: int
    path_format: Literal["jsonpath"]
    has_translatable_paths: bool
    plugin_reason: str
    translate_rules: list[PluginTextTranslateRule]

    @field_validator("plugin_name", "plugin_reason")
    @classmethod
    def _validate_non_empty_text(cls, value: str) -> str:
        """确保关键文本字段非空。"""
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("文本字段不能为空")
        return normalized_value

    @model_validator(mode="after")
    def _validate_consistency(self) -> "PluginAnalysisResponse":
        """校验可翻译路径标记与规则列表保持一致。"""
        if self.has_translatable_paths and not self.translate_rules:
            raise ValueError("存在可翻译路径时 translate_rules 不能为空")
        if not self.has_translatable_paths and self.translate_rules:
            raise ValueError("不存在可翻译路径时 translate_rules 必须为空")
        return self


def extract_plugin_name(plugin: dict[str, JsonValue], plugin_index: int) -> str:
    """读取插件名称；缺失时返回稳定兜底名。"""
    plugin_name = plugin.get("name")
    if isinstance(plugin_name, str) and plugin_name.strip():
        return plugin_name.strip()
    return f"unnamed_plugin_{plugin_index}"


def extract_plugin_description(plugin: dict[str, JsonValue]) -> str:
    """读取插件描述文本；缺失时返回空字符串。"""
    plugin_description = plugin.get("description")
    if isinstance(plugin_description, str):
        return plugin_description.strip()
    return ""


def build_plugin_hash(plugin: dict[str, JsonValue]) -> str:
    """计算单个插件对象的稳定结构哈希。"""
    payload = json.dumps(plugin, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_plugins_file_hash(plugins: list[dict[str, JsonValue]]) -> str:
    """计算整份 `plugins.js` 的结构哈希。"""
    payload = json.dumps(plugins, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_prompt_hash(system_prompt: str) -> str:
    """计算插件分析提示词哈希。"""
    return hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()


def build_prompt_payload(
    *,
    plugin_index: int,
    plugin: dict[str, JsonValue],
    plugin_name: str,
    plugin_description: str,
    resolved_leaves: list[ResolvedLeaf],
) -> dict[str, JsonValue]:
    """构造发送给模型的结构化载荷。"""
    parameters = plugin.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}

    return {
        "plugin_index": plugin_index,
        "plugin": {
            "name": plugin_name,
            "description": plugin_description,
            "parameters": parameters,
        },
        "resolved_leaves": [leaf.model_dump(mode="json") for leaf in resolved_leaves],
    }


def build_request_messages(
    *,
    system_prompt: str,
    payload: dict[str, JsonValue],
    previous_response: str | None = None,
    validation_errors: list[str] | None = None,
) -> list[PluginAnalysisMessage]:
    """构造插件分析对话消息。"""
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    user_prompt = (
        "下面是单个插件的结构化输入，请只返回严格 JSON。\n"
        "不要解释，不要 Markdown，不要翻译文本本身。\n\n"
        f"{payload_json}"
    )
    messages: list[PluginAnalysisMessage] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    if previous_response is None or not validation_errors:
        return messages

    error_text = "\n".join(f"- {error}" for error in validation_errors)
    retry_prompt = (
        "你上一次的返回没有通过校验。请基于同一份输入修正结果，并且仍然只返回严格 JSON。\n"
        "下面是上一次返回与校验错误，请逐条修正：\n"
        f"上一次返回：\n{previous_response}\n\n"
        f"校验错误：\n{error_text}"
    )
    messages.append({"role": "assistant", "content": previous_response})
    messages.append({"role": "user", "content": retry_prompt})
    return messages


def parse_analysis_response(
    *,
    response_text: str,
    expected_plugin_name: str,
    expected_plugin_index: int,
    allowed_templates: set[str],
) -> PluginAnalysisResponse:
    """解析并校验 AI 返回的结构化结果。"""
    parse_candidates = build_parse_candidates(response_text)
    errors: list[str] = []

    for candidate in parse_candidates:
        try:
            decoded = cast(object, json.loads(candidate))
            parsed = coerce_json_value(decoded)
            return validate_analysis_response(
                data=parsed,
                expected_plugin_name=expected_plugin_name,
                expected_plugin_index=expected_plugin_index,
                allowed_templates=allowed_templates,
            )
        except (json.JSONDecodeError, ValidationError, ValueError) as error:
            errors.append(f"raw_json: {error}")

    for candidate in parse_candidates:
        try:
            repaired = coerce_json_value(repair_json_loads(candidate))
            return validate_analysis_response(
                data=repaired,
                expected_plugin_name=expected_plugin_name,
                expected_plugin_index=expected_plugin_index,
                allowed_templates=allowed_templates,
            )
        except (ValidationError, ValueError, TypeError) as error:
            errors.append(f"json_repair: {error}")

    raise ValueError(" | ".join(errors))


def build_parse_candidates(response_text: str) -> list[str]:
    """从模型原始响应里提取可尝试解析的候选 JSON 文本。"""
    normalized_text = response_text.strip()
    candidates: list[str] = []
    seen_texts: set[str] = set()

    def append_candidate(text: str) -> None:
        candidate_text = text.strip()
        if not candidate_text or candidate_text in seen_texts:
            return
        seen_texts.add(candidate_text)
        candidates.append(candidate_text)

    append_candidate(normalized_text)

    fenced_match = MARKDOWN_JSON_FENCE_PATTERN.fullmatch(normalized_text)
    if fenced_match is not None:
        append_candidate(fenced_match.group("body"))

    first_object_index = normalized_text.find("{")
    last_object_index = normalized_text.rfind("}")
    if first_object_index != -1 and last_object_index != -1 and first_object_index < last_object_index:
        append_candidate(normalized_text[first_object_index : last_object_index + 1])

    return candidates


def validate_analysis_response(
    *,
    data: object,
    expected_plugin_name: str,
    expected_plugin_index: int,
    allowed_templates: set[str],
) -> PluginAnalysisResponse:
    """对模型返回结果做结构和业务校验。"""
    result = PluginAnalysisResponse.model_validate(data)
    if result.plugin_name != expected_plugin_name:
        raise ValueError(
            f"plugin_name 不匹配，期望 {expected_plugin_name}，实际 {result.plugin_name}"
        )
    if result.plugin_index != expected_plugin_index:
        raise ValueError(
            f"plugin_index 不匹配，期望 {expected_plugin_index}，实际 {result.plugin_index}"
        )

    normalized_rules: list[PluginTextTranslateRule] = []
    seen_templates: set[str] = set()
    for rule in result.translate_rules:
        if rule.path_template not in allowed_templates:
            raise ValueError(f"path_template 不在当前插件允许集合中: {rule.path_template}")
        if rule.path_template in seen_templates:
            continue
        seen_templates.add(rule.path_template)
        normalized_rules.append(rule)

    return PluginAnalysisResponse(
        plugin_name=result.plugin_name,
        plugin_index=result.plugin_index,
        path_format=result.path_format,
        has_translatable_paths=bool(normalized_rules),
        plugin_reason=result.plugin_reason,
        translate_rules=normalized_rules,
    )


def validate_analysis_semantics(
    *,
    response: PluginAnalysisResponse,
    resolved_leaves: list[ResolvedLeaf],
    plugin_name: str,
    text_rules: TextRules,
) -> None:
    """基于真实叶子值校验 AI 返回路径是否命中可翻译字符串。"""
    if not response.translate_rules:
        return

    string_leaves = [leaf for leaf in resolved_leaves if leaf.value_type == "string"]
    errors: list[str] = []

    for rule in response.translate_rules:
        matched_leaves = [
            leaf
            for leaf in string_leaves
            if jsonpath_matches_template(
                template_path=rule.path_template,
                actual_path=leaf.path,
            )
        ]
        if not matched_leaves:
            errors.append(f"{rule.path_template} 未命中任何字符串叶子")
            continue

        contentful_leaves = [
            leaf
            for leaf in matched_leaves
            if isinstance(leaf.value, str)
            and leaf.value.strip()
            and not text_rules.should_skip_plugin_like_text(
                text=leaf.value.strip(),
                path_parts=jsonpath_to_path_parts(leaf.path),
                plugin_name=plugin_name,
            )
            and text_rules.passes_plugin_text_language_filter(leaf.value.strip())
        ]
        if not contentful_leaves:
            errors.append(f"{rule.path_template} 命中的值全部被通用过滤或日文规则排除")

    if errors:
        raise ValueError(" | ".join(errors))


__all__: list[str] = [
    "PluginAnalysisResponse",
    "ResolvedLeaf",
    "build_allowed_templates",
    "build_plugin_hash",
    "build_plugins_file_hash",
    "build_prompt_hash",
    "build_prompt_payload",
    "build_request_messages",
    "expand_rule_to_leaf_paths",
    "extract_plugin_description",
    "extract_plugin_name",
    "jsonpath_matches_template",
    "jsonpath_to_location_path",
    "jsonpath_to_path_parts",
    "parse_analysis_response",
    "resolve_plugin_leaves",
    "validate_analysis_semantics",
]
