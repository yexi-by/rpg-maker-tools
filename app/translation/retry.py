"""翻译业务层 LLM 请求重试策略。"""

import asyncio

from app.llm import (
    ChatMessage,
    LLMHandler,
    LLMRequestFailure,
    classify_llm_error,
    format_llm_error,
    is_recoverable_llm_error,
)
from app.observability import logger


async def request_with_recoverable_retry(
    *,
    llm_handler: LLMHandler,
    model: str,
    messages: list[ChatMessage],
    retry_count: int,
    retry_delay: int,
    task_label: str,
    temperature: float | None = None,
) -> str:
    """
    仅对可恢复 LLM 错误执行业务层重试。

    Args:
        llm_handler: 已注册服务的 LLM 门面。
        model: 模型标识。
        messages: 请求消息列表。
        retry_count: 失败后的最大重试次数，不包含首次请求。
        retry_delay: 基础等待秒数；多次重试时线性递增。
        task_label: 日志里展示的业务任务名。
        temperature: 可选采样温度。

    Returns:
        模型返回的文本。

    Raises:
        Exception: 不可恢复错误会立即抛出；可恢复错误耗尽重试后抛出。
    """
    max_attempts = retry_count + 1
    for attempt_index in range(1, max_attempts + 1):
        try:
            return await llm_handler.get_ai_response(
                messages=messages,
                model=model,
                temperature=temperature,
            )
        except Exception as error:
            info = classify_llm_error(error)
            if not is_recoverable_llm_error(error):
                logger.error(
                    f"[tag.failure]LLM 不可恢复错误，已停止流程[/tag.failure] 任务 [tag.count]{task_label}[/tag.count] 原因：{format_llm_error(error)}"
                )
                raise LLMRequestFailure(info=info, attempt_count=attempt_index) from error

            if attempt_index >= max_attempts:
                logger.error(
                    f"[tag.failure]LLM 可恢复错误重试耗尽[/tag.failure] 任务 [tag.count]{task_label}[/tag.count] 尝试 [tag.count]{attempt_index}[/tag.count] 次 原因：{format_llm_error(error)}"
                )
                raise LLMRequestFailure(info=info, attempt_count=attempt_index) from error

            delay_seconds = retry_delay * attempt_index
            logger.warning(
                f"[tag.warning]LLM 可恢复错误，准备重试[/tag.warning] 任务 [tag.count]{task_label}[/tag.count] 第 [tag.count]{attempt_index}[/tag.count] 次失败，等待 [tag.count]{delay_seconds}[/tag.count] 秒 原因：{format_llm_error(error)}"
            )
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

    raise RuntimeError(f"LLM 请求未返回结果: {task_label}")


__all__: list[str] = ["request_with_recoverable_retry"]
