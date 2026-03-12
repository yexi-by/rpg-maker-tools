"""
LLM 服务公共工具模块。

集中放置各提供商都会复用的重试控制、Base64 处理与 MIME 类型识别能力。
"""

from typing import Type
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
import base64
import filetype


def create_retry_manager(
    error_types: tuple[Type[Exception], ...] = (Exception,),
    retry_count: int = 10,
    retry_delay: int = 2,
) -> AsyncRetrying:
    """
    创建一个通用的异步重试管理器，用于包装易受网络波动影响的第三方网络调用。

    基于 tenacity 库，该管理器提供指数退避（Exponential Backoff）重试策略，
    即每次重试的等待时间会随失败次数成倍增长，直到达到最大延迟上限（10 秒），
    以有效应对被远端服务器限流（Rate Limit）或间歇性网络不稳定的场景。

    Args:
        error_types: 需要触发重试的异常类型元组。默认捕获所有 Exception。
                     在实际业务中通常会传入各 SDK 特有的网络异常（如 TimeoutError）。
        retry_count: 允许发生重试的最大尝试次数。
        retry_delay: 首次重试的初始延迟时间（秒）。

    Returns:
        配置好的 AsyncRetrying 实例。在业务代码中，可以使用 `async for attempt in retrier:` 模式包裹目标操作。
    """

    retry_strategy = retry_if_exception_type(error_types)
    return AsyncRetrying(
        stop=stop_after_attempt(retry_count),
        wait=wait_exponential(multiplier=1, min=retry_delay, max=10),
        retry=retry_strategy,
        reraise=True,
    )


def base64_to_bytes(data: str) -> bytes:
    """
    将包含或不包含 Data URI 前缀的 Base64 字符串解码为原始字节流。

    该函数具有容错能力，如果传入的是符合前端 Data URI 规范（如 `data:image/png;base64,...`）的字符串，
    它会自动裁掉前缀部分，只对实际的 Base64 内容进行解码。

    Args:
        data: 待解码的 Base64 字符串。

    Returns:
        解码后的原始 bytes 数据。
    """
    if "," in data:
        _, data = data.split(",", 1)
    return base64.b64decode(data)


def detect_mime_type(data: bytes | str) -> str:
    """
    通过读取二进制数据的文件头（魔数，Magic Number）智能检测文件的 MIME 类型。

    这一步在构建多模态 LLM 请求（如 OpenAI Vision 或 Gemini Image）时非常重要，
    因为 API 往往要求明确标注上传图片的确切类型（如 `image/png`, `image/jpeg`）。

    Args:
        data: 原始文件数据，支持直接传入 bytes 或包含数据的 Base64 字符串。

    Returns:
        标准的 MIME 类型字符串。

    Raises:
        ValueError: 如果提供的数据头无法匹配任何已知格式时抛出。
    """
    byte_data: bytes = base64_to_bytes(data) if isinstance(data, str) else bytes(data)
    kind = filetype.guess(byte_data)
    if kind is None:
        raise ValueError("无法识别的文件格式")
    return kind.mime
