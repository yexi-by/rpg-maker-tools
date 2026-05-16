"""LLM 请求体额外参数解析与校验。"""

import json
from json import JSONDecodeError
from typing import cast

type LLMRequestBodyValue = (
    str | int | float | bool | None | list["LLMRequestBodyValue"] | dict[str, "LLMRequestBodyValue"]
)
type LLMRequestBodyExtra = dict[str, LLMRequestBodyValue]


def normalize_request_body_extra(raw_value: object, *, context: str) -> LLMRequestBodyExtra:
    """
    把配置中的模型请求体额外参数收窄为 JSON 对象。

    Args:
        raw_value: TOML 或 JSON 解码后的动态配置值。本函数是配置边界，会立即收窄为 JSON 对象。
        context: 用于错误信息的配置项路径。

    Returns:
        可以透传给 OpenAI 兼容接口请求体的 JSON 对象。

    Raises:
        ValueError: 配置不是 JSON 对象，或启用了当前不支持的流式返回。
    """
    if raw_value is None:
        return {}

    if isinstance(raw_value, str):
        decoded_value = _decode_json_text(raw_value=raw_value, context=context)
    else:
        decoded_value = _coerce_json_value(raw_value)

    request_body_extra = _ensure_json_object(decoded_value, context)
    _reject_streaming_parameters(request_body_extra=request_body_extra, context=context)
    return request_body_extra


def _decode_json_text(*, raw_value: str, context: str) -> LLMRequestBodyValue:
    """解析写在配置里的 JSON 字符串。"""
    stripped_value = raw_value.strip()
    if not stripped_value:
        return {}

    try:
        decoded_value = cast(object, json.loads(stripped_value))
    except JSONDecodeError as error:
        raise ValueError(f"{context} 必须是合法 JSON 对象字符串: {error.msg}") from error
    return _coerce_json_value(decoded_value)


def _coerce_json_value(value: object) -> LLMRequestBodyValue:
    """把解码得到的动态对象递归收窄为 JSON 值。"""
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, list):
        list_result: list[LLMRequestBodyValue] = []
        for item in cast(list[object], value):
            list_result.append(_coerce_json_value(item))
        return list_result
    if isinstance(value, dict):
        object_result: dict[str, LLMRequestBodyValue] = {}
        for key, child in cast(dict[object, object], value).items():
            if not isinstance(key, str):
                raise ValueError("JSON 对象键必须是字符串")
            object_result[key] = _coerce_json_value(child)
        return object_result
    raise ValueError(f"JSON 值类型无法处理: {type(value).__name__}")


def _ensure_json_object(value: LLMRequestBodyValue, context: str) -> LLMRequestBodyExtra:
    """把 JSON 值收窄为对象。"""
    if not isinstance(value, dict):
        raise ValueError(f"{context} 必须是 JSON 对象")
    return value


def _reject_streaming_parameters(*, request_body_extra: LLMRequestBodyExtra, context: str) -> None:
    """当前翻译流程依赖完整 JSON 响应，因此配置层必须拒绝流式参数。"""
    if "stream_options" in request_body_extra:
        raise ValueError(
            f"{context} 当前不支持 LLM 流式返回，请删除 stream_options；"
            + "本项目需要先拿到完整模型 JSON 再检查并保存译文。"
        )

    stream_value = request_body_extra.get("stream")
    if stream_value is not None and stream_value is not False:
        raise ValueError(
            f"{context} 当前不支持 LLM 流式返回，请删除 stream=true；"
            + "本项目需要先拿到完整模型 JSON 再检查并保存译文。"
        )


__all__: list[str] = [
    "LLMRequestBodyExtra",
    "LLMRequestBodyValue",
    "normalize_request_body_extra",
]
