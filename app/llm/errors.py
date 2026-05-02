"""LLM SDK 错误分类工具。"""

from dataclasses import dataclass

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    ConflictError,
    InternalServerError,
    RateLimitError,
)

from app.rmmz.schema import LlmFailureCategory

RECOVERABLE_STATUS_CODES = {408, 409, 425, 429}


class EmptyLLMResponseError(RuntimeError):
    """接口成功返回但响应体没有可用文本。"""


@dataclass(frozen=True, slots=True)
class LlmErrorInfo:
    """适合业务层记录的模型错误摘要。"""

    category: LlmFailureCategory
    error_type: str
    message: str
    retryable: bool


class LLMRequestFailure(RuntimeError):
    """模型请求最终失败，供翻译运行记录为运行级故障。"""

    def __init__(self, *, info: LlmErrorInfo, attempt_count: int) -> None:
        """保存模型请求失败摘要和实际尝试次数。"""
        super().__init__(info.message)
        self.info: LlmErrorInfo = info
        self.attempt_count: int = attempt_count


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


def classify_llm_error(error: Exception) -> LlmErrorInfo:
    """把 SDK 异常转换为稳定的运行级故障分类。"""
    retryable = is_recoverable_llm_error(error)
    category: LlmFailureCategory = "unknown"
    if isinstance(error, RateLimitError):
        category = "rate_limit"
    elif isinstance(error, APITimeoutError):
        category = "timeout"
    elif isinstance(error, APIConnectionError):
        category = "connection"
    elif isinstance(error, ConflictError):
        category = "conflict"
    elif isinstance(error, InternalServerError):
        category = "server"
    elif isinstance(error, APIStatusError):
        if error.status_code == 429:
            category = "rate_limit"
        elif error.status_code >= 500:
            category = "server"
        elif retryable:
            category = "unknown"
        else:
            category = "fatal"
    elif not retryable:
        category = "fatal"
    return LlmErrorInfo(
        category=category,
        error_type=type(error).__name__,
        message=format_llm_error(error),
        retryable=retryable,
    )


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
    "LLMRequestFailure",
    "LlmErrorInfo",
    "classify_llm_error",
    "format_llm_error",
    "is_recoverable_llm_error",
]
