"""项目 JSON 值类型与边界收窄工具。"""

from typing import cast

type JsonPrimitive = str | int | float | bool | None
type JsonValue = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]
type JsonArray = list[JsonValue]


def ensure_json_object(value: JsonValue, context: str) -> JsonObject:
    """把 JSON 值收窄为对象。"""
    if not isinstance(value, dict):
        raise TypeError(f"{context} 必须是 JSON 对象")
    return value


def ensure_json_array(value: JsonValue, context: str) -> JsonArray:
    """把 JSON 值收窄为数组。"""
    if not isinstance(value, list):
        raise TypeError(f"{context} 必须是 JSON 数组")
    return value


def ensure_json_string_list(value: JsonValue, context: str) -> list[str]:
    """把 JSON 值收窄为字符串数组。"""
    if not isinstance(value, list):
        raise TypeError(f"{context} 必须是字符串数组")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise TypeError(f"{context}[{index}] 必须是字符串")
        result.append(item)
    return result


def coerce_json_value(value: object) -> JsonValue:
    """把第三方解码得到的动态对象递归收窄为项目 JSON 类型。"""
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, list):
        list_result: list[JsonValue] = []
        for item in cast(list[object], value):
            list_result.append(coerce_json_value(item))
        return list_result
    if isinstance(value, dict):
        object_result: dict[str, JsonValue] = {}
        for key, child in cast(dict[object, object], value).items():
            if not isinstance(key, str):
                raise TypeError("JSON 对象键必须是字符串")
            object_result[key] = coerce_json_value(child)
        return object_result
    raise TypeError(f"不支持的 JSON 值类型: {type(value).__name__}")


__all__: list[str] = [
    "JsonArray",
    "JsonObject",
    "JsonPrimitive",
    "JsonValue",
    "coerce_json_value",
    "ensure_json_array",
    "ensure_json_object",
    "ensure_json_string_list",
]
