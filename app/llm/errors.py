"""LLM SDK 错误分类工具。"""

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    ConflictError,
    InternalServerError,
    RateLimitError,
)

RECOVERABLE_STATUS_CODES = {408, 409, 425, 429}


class EmptyLLMResponseError(RuntimeError):
    """接口成功返回但响应体没有可用文本。"""


def is_recoverable_llm_error(error: Exception) -> bool:
    """
    判断 LLM 请求错误是否适合重试。

    可恢复错误仅包含连接故障、超时、限流、冲突和服务端 5xx。
    鉴权、权限、参数、模型不存在、响应为空等错误会立即向上抛出。
    """
    if isinstance(error, APIConnectionError | APITimeoutError | RateLimitError | InternalServerError | ConflictError):
        return True
    if isinstance(error, APIStatusError):
        return error.status_code in RECOVERABLE_STATUS_CODES or error.status_code >= 500
    return False


def format_llm_error(error: Exception) -> str:
    """把 LLM 错误压缩成适合日志展示的中文摘要。"""
    if isinstance(error, APIStatusError):
        return f"{type(error).__name__}(HTTP {error.status_code}): {error.message}"
    message = str(error).strip()
    if message:
        return f"{type(error).__name__}: {message}"
    return type(error).__name__


__all__: list[str] = [
    "EmptyLLMResponseError",
    "format_llm_error",
    "is_recoverable_llm_error",
]
