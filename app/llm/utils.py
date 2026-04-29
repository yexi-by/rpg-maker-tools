"""
LLM 服务公共工具模块。

集中放置各提供商都会复用的重试控制能力。
"""

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


def create_retry_manager(
    error_types: tuple[type[Exception], ...] = (Exception,),
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
