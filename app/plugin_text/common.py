"""
插件文本分析与提取共用工具模块。

本模块统一提供：
1. `plugins.js` 单插件参数树的叶子展开能力。
2. 受限 JSONPath 的解析、匹配和转换能力。
3. AI 结构化返回的解析与基础校验能力。
4. 插件和提示词的稳定哈希能力。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal, TypedDict

from json_repair import loads as repair_json_loads
from pydantic import BaseModel, ValidationError, field_validator, model_validator

from app.models.schemas import PluginTextTranslateRule, SourceLanguage
from app.utils.source_language_utils import (
    passes_plugin_text_language_filter,
    should_skip_plugin_like_text,
)

JSON_INDEX_SEGMENT_PATTERN: re.Pattern[str] = re.compile(r"\[\d+\]")
JSON_PATH_PATTERN: re.Pattern[str] = re.compile(
    r"^\$(?:\['(?:[^'\\]|\\.)+'\]|\[(?:\d+|\*)\])+$"
)
MARKDOWN_JSON_FENCE_PATTERN: re.Pattern[str] = re.compile(
    r"^```(?:json)?\s*(?P<body>[\s\S]*?)\s*```$",
    re.IGNORECASE,
)
JSON_PATH_SEGMENT_PATTERN: re.Pattern[str] = re.compile(
    r"\['((?:[^'\\]|\\.)+)'\]|\[(\d+|\*)\]"
)
type PluginAnalysisMessageRole = Literal["system", "user", "assistant"]


class PluginAnalysisMessage(TypedDict):
    """
    插件分析阶段发送给模型的单条对话消息。

    Attributes:
        role: 当前消息角色，只允许 system、user、assistant。
        content: 当前消息正文。
    """

    role: PluginAnalysisMessageRole
    content: str


class ResolvedLeaf(BaseModel):
    """
    单个插件参数树展开后的叶子节点。

    Attributes:
        path: 当前叶子的精确 JSONPath。
        value: 当前叶子的原始值。
        value_type: 叶子的基础类型。
        from_json_string: 是否来自字符串化 JSON 容器的二次解析。
    """

    path: str
    value: str | int | float | bool | None
    value_type: Literal["string", "number", "boolean", "null"]
    from_json_string: bool


class PluginAnalysisResponse(BaseModel):
    """
    AI 返回的插件分析结果结构。

    Attributes:
        plugin_name: 当前插件名。
        plugin_index: 当前插件索引。
        path_format: 路径格式标识，当前固定为 `jsonpath`。
        has_translatable_paths: 是否存在可翻译路径。
        plugin_reason: 插件级判断说明。
        translate_rules: 需要进入翻译流程的路径规则。
    """

    plugin_name: str
    plugin_index: int
    path_format: Literal["jsonpath"]
    has_translatable_paths: bool
    plugin_reason: str
    translate_rules: list[PluginTextTranslateRule]

    @field_validator("plugin_name", "plugin_reason")
    @classmethod
    def _validate_non_empty_text(cls, value: str) -> str:
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("文本字段不能为空")
        return normalized_value

    @model_validator(mode="after")
    def _validate_consistency(self) -> "PluginAnalysisResponse":
        if self.has_translatable_paths and not self.translate_rules:
            raise ValueError("存在可翻译路径时 translate_rules 不能为空")
        if not self.has_translatable_paths and self.translate_rules:
            raise ValueError("不存在可翻译路径时 translate_rules 必须为空")
        return self


def extract_plugin_name(plugin: dict[str, Any], plugin_index: int) -> str:
    """
    读取插件名称；缺失时返回稳定兜底名。
    """

    plugin_name = plugin.get("name")
    if isinstance(plugin_name, str) and plugin_name.strip():
        return plugin_name.strip()
    return f"unnamed_plugin_{plugin_index}"


def extract_plugin_description(plugin: dict[str, Any]) -> str:
    """
    读取插件描述文本；缺失时返回空字符串。
    """

    plugin_description = plugin.get("description")
    if isinstance(plugin_description, str):
        return plugin_description.strip()
    return ""


def build_plugin_hash(plugin: dict[str, Any]) -> str:
    """
    计算单个插件对象的稳定结构哈希。
    """

    payload = json.dumps(plugin, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_plugins_file_hash(plugins: list[dict[str, Any]]) -> str:
    """
    计算整份 `plugins.js` 结构哈希。
    """

    payload = json.dumps(
        plugins,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_prompt_hash(
    system_prompt: str,
    source_language: SourceLanguage | None = None,
) -> str:
    """
    计算插件分析提示词哈希。

    Args:
        system_prompt: 当前插件分析系统提示词。
        source_language: 当前游戏源语言；存在时会一并进入哈希。
    """
    if source_language is None:
        prompt_fingerprint = system_prompt
    else:
        prompt_fingerprint = (
            f"{system_prompt}\n\n[plugin_text_source_language={source_language}]"
        )
    return hashlib.sha256(prompt_fingerprint.encode("utf-8")).hexdigest()


def build_prompt_payload(
    *,
    plugin_index: int,
    plugin: dict[str, Any],
    plugin_name: str,
    plugin_description: str,
    source_language: SourceLanguage,
    resolved_leaves: list[ResolvedLeaf],
) -> dict[str, Any]:
    """
    构造发送给模型的结构化载荷。

    Args:
        plugin_index: 当前插件索引。
        plugin: 当前插件原始对象。
        plugin_name: 当前插件名。
        plugin_description: 当前插件描述。
        source_language: 当前游戏源语言。
        resolved_leaves: 当前插件展开后的叶子列表。

    Returns:
        可直接序列化发送给模型的结构化载荷。
    """

    parameters = plugin.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}

    return {
        "plugin_index": plugin_index,
        "source_language": source_language,
        "plugin": {
            "name": plugin_name,
            "description": plugin_description,
            "parameters": parameters,
        },
        "resolved_leaves": [
            leaf.model_dump(mode="json") for leaf in resolved_leaves
        ],
    }


def build_request_messages(
    *,
    system_prompt: str,
    payload: dict[str, Any],
    previous_response: str | None = None,
    validation_errors: list[str] | None = None,
) -> list[PluginAnalysisMessage]:
    """
    构造插件分析对话消息。
    """

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


def resolve_plugin_leaves(plugin: dict[str, Any]) -> list[ResolvedLeaf]:
    """
    递归展开单个插件参数树，提取全部叶子节点。
    """

    parameters = plugin.get("parameters")
    if not isinstance(parameters, dict):
        return []

    leaves: list[ResolvedLeaf] = []
    _walk_plugin_value(
        value=parameters,
        current_path="$['parameters']",
        from_json_string=False,
        leaves=leaves,
    )
    return leaves


def _walk_plugin_value(
    *,
    value: Any,
    current_path: str,
    from_json_string: bool,
    leaves: list[ResolvedLeaf],
) -> None:
    """
    递归遍历插件值，并把叶子节点写入结果列表。
    """

    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{current_path}[{quote_jsonpath_key(str(key))}]"
            _walk_plugin_value(
                value=child,
                current_path=child_path,
                from_json_string=from_json_string,
                leaves=leaves,
            )
        return

    if isinstance(value, list):
        for index, child in enumerate(value):
            _walk_plugin_value(
                value=child,
                current_path=f"{current_path}[{index}]",
                from_json_string=from_json_string,
                leaves=leaves,
            )
        return

    if isinstance(value, str):
        parsed_container = try_parse_container_string(value)
        if parsed_container is not None:
            _walk_plugin_value(
                value=parsed_container,
                current_path=current_path,
                from_json_string=True,
                leaves=leaves,
            )
            return
        leaves.append(
            ResolvedLeaf(
                path=current_path,
                value=value,
                value_type="string",
                from_json_string=from_json_string,
            )
        )
        return

    if isinstance(value, bool):
        leaves.append(
            ResolvedLeaf(
                path=current_path,
                value=value,
                value_type="boolean",
                from_json_string=from_json_string,
            )
        )
        return

    if value is None:
        leaves.append(
            ResolvedLeaf(
                path=current_path,
                value=None,
                value_type="null",
                from_json_string=from_json_string,
            )
        )
        return

    if isinstance(value, int | float):
        leaves.append(
            ResolvedLeaf(
                path=current_path,
                value=value,
                value_type="number",
                from_json_string=from_json_string,
            )
        )
        return

    leaves.append(
        ResolvedLeaf(
            path=current_path,
            value=str(value),
            value_type="string",
            from_json_string=from_json_string,
        )
    )


def try_parse_container_string(value: str) -> dict[str, Any] | list[Any] | None:
    """
    尝试把字符串解析成 JSON 容器。
    """

    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None

    if isinstance(parsed, dict | list):
        return parsed
    return None


def quote_jsonpath_key(key: str) -> str:
    """
    把对象键安全包装成 JSONPath 片段。
    """

    escaped_key = key.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped_key}'"


def build_allowed_templates(resolved_leaves: list[ResolvedLeaf]) -> set[str]:
    """
    根据字符串叶子路径构造允许的模板路径集合。
    """

    allowed_templates: set[str] = set()
    for leaf in resolved_leaves:
        if leaf.value_type != "string":
            continue

        index_matches = list(JSON_INDEX_SEGMENT_PATTERN.finditer(leaf.path))
        if not index_matches:
            allowed_templates.add(leaf.path)
            continue

        for mask in range(1 << len(index_matches)):
            fragments: list[str] = []
            last_end = 0
            for match_index, match in enumerate(index_matches):
                fragments.append(leaf.path[last_end:match.start()])
                fragments.append(
                    "[*]" if mask & (1 << match_index) else match.group(0)
                )
                last_end = match.end()
            fragments.append(leaf.path[last_end:])
            allowed_templates.add("".join(fragments))
    return allowed_templates


def parse_analysis_response(
    *,
    response_text: str,
    expected_plugin_name: str,
    expected_plugin_index: int,
    allowed_templates: set[str],
) -> PluginAnalysisResponse:
    """
    解析并校验 AI 返回的结构化结果。
    """

    parse_candidates = build_parse_candidates(response_text)
    errors: list[str] = []

    for candidate in parse_candidates:
        try:
            parsed = json.loads(candidate)
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
            repaired = repair_json_loads(candidate)
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
    """
    从模型原始响应里提取可尝试解析的候选 JSON 文本。
    """

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
    if (
        first_object_index != -1
        and last_object_index != -1
        and first_object_index < last_object_index
    ):
        append_candidate(normalized_text[first_object_index : last_object_index + 1])

    return candidates


def validate_analysis_response(
    *,
    data: Any,
    expected_plugin_name: str,
    expected_plugin_index: int,
    allowed_templates: set[str],
) -> PluginAnalysisResponse:
    """
    对模型返回结果做结构和业务校验。
    """

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
    source_language: SourceLanguage,
) -> None:
    """
    基于真实叶子值校验 AI 返回路径是否命中了可翻译字符串。

    Args:
        response: 当前插件的 AI 分析结果。
        resolved_leaves: 当前插件的真实叶子节点列表。
        plugin_name: 当前插件名。
        source_language: 当前游戏源语言。
    """

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
            and not should_skip_plugin_like_text(
                text=leaf.value.strip(),
                path_parts=jsonpath_to_path_parts(leaf.path),
                plugin_name=plugin_name,
            )
            and passes_plugin_text_language_filter(
                text=leaf.value.strip(),
                source_language=source_language,
            )
        ]
        if not contentful_leaves:
            errors.append(
                f"{rule.path_template} 命中的值全部被通用过滤或当前源语言规则排除"
            )

    if errors:
        raise ValueError(" | ".join(errors))


def expand_rule_to_leaf_paths(
    *,
    path_template: str,
    resolved_leaves: list[ResolvedLeaf],
) -> list[str]:
    """
    把模板路径展开为命中的精确叶子路径列表。
    """

    matched_paths = [
        leaf.path
        for leaf in resolved_leaves
        if leaf.value_type == "string"
        and jsonpath_matches_template(
            template_path=path_template,
            actual_path=leaf.path,
        )
    ]
    matched_paths.sort()
    return matched_paths


def jsonpath_matches_template(*, template_path: str, actual_path: str) -> bool:
    """
    判断精确路径是否匹配某条模板路径。
    """

    template_parts = jsonpath_to_path_parts(template_path)
    actual_parts = jsonpath_to_path_parts(actual_path)
    if len(template_parts) != len(actual_parts):
        return False

    for template_part, actual_part in zip(template_parts, actual_parts, strict=True):
        if template_part == "*":
            if not isinstance(actual_part, int):
                return False
            continue
        if template_part != actual_part:
            return False
    return True


def jsonpath_to_path_parts(path: str) -> list[str | int]:
    """
    把受限 JSONPath 解析为路径片段列表。
    """

    if not JSON_PATH_PATTERN.fullmatch(path):
        raise ValueError(f"不支持的 JSONPath: {path}")

    parts: list[str | int] = []
    for match in JSON_PATH_SEGMENT_PATTERN.finditer(path):
        key_segment = match.group(1)
        index_segment = match.group(2)
        if key_segment is not None:
            parts.append(_unescape_jsonpath_key(key_segment))
            continue
        if index_segment == "*":
            parts.append("*")
            continue
        parts.append(int(index_segment))
    return parts


def jsonpath_to_location_path(*, json_path: str, plugin_index: int) -> str:
    """
    把插件级 JSONPath 转成现有回写兼容的 `location_path`。
    """

    path_parts = jsonpath_to_path_parts(json_path)
    if not path_parts or path_parts[0] != "parameters":
        raise ValueError(f"插件路径必须从 parameters 开始: {json_path}")

    normalized_parts = ["plugins.js", str(plugin_index)]
    normalized_parts.extend(str(part) for part in path_parts[1:])
    return "/".join(normalized_parts)


def _unescape_jsonpath_key(key: str) -> str:
    """
    反转义 JSONPath 里的对象键。
    """

    return key.replace("\\'", "'").replace("\\\\", "\\")


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
