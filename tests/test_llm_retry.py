"""LLM 错误分类与业务层重试测试。"""

from pathlib import Path
from typing import override

import httpx
import pytest
from openai import APIConnectionError, APIStatusError

from app.llm import ChatMessage, EmptyLLMResponseError, LLMHandler, is_recoverable_llm_error
from app.observability import setup_logger
from app.translation.retry import request_with_recoverable_retry


class FakeLLMHandler(LLMHandler):
    """用于验证业务层重试策略的 LLM 假实现。"""

    def __init__(self, failures: list[Exception]) -> None:
        """初始化待抛出的错误序列。"""
        super().__init__()
        self.failures: list[Exception] = failures
        self.call_count: int = 0

    @override
    async def get_ai_response(
        self,
        *,
        messages: list[ChatMessage],
        model: str,
        temperature: float | None = None,
    ) -> str:
        """按预设顺序抛出错误，错误耗尽后返回成功文本。"""
        _ = messages
        _ = model
        _ = temperature
        self.call_count += 1
        if self.failures:
            raise self.failures.pop(0)
        return "成功"


def test_llm_error_classification_distinguishes_recoverable_status() -> None:
    """5xx 状态可重试，4xx 鉴权类状态不可重试。"""
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    server_error = APIStatusError(
        "服务端错误",
        response=httpx.Response(500, request=request),
        body=None,
    )
    auth_error = APIStatusError(
        "鉴权失败",
        response=httpx.Response(401, request=request),
        body=None,
    )

    assert is_recoverable_llm_error(server_error)
    assert not is_recoverable_llm_error(auth_error)


@pytest.mark.asyncio
async def test_recoverable_llm_error_retries_in_translation_layer(tmp_path: Path) -> None:
    """可恢复错误由翻译层按业务策略重试。"""
    setup_logger(use_console=False, file_path=tmp_path / "retry.log", enqueue_file_log=False)
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    handler = FakeLLMHandler(
        failures=[APIConnectionError(message="连接失败", request=request)]
    )

    result = await request_with_recoverable_retry(
        llm_handler=handler,
        model="fake-model",
        messages=[ChatMessage(role="user", text="你好")],
        retry_count=1,
        retry_delay=0,
        task_label="测试任务",
    )

    assert result == "成功"
    assert handler.call_count == 2


@pytest.mark.asyncio
async def test_fatal_llm_error_stops_without_retry(tmp_path: Path) -> None:
    """不可恢复错误会立即抛出，调用次数保持为首次请求。"""
    setup_logger(use_console=False, file_path=tmp_path / "fatal.log", enqueue_file_log=False)
    handler = FakeLLMHandler(failures=[EmptyLLMResponseError("空响应")])

    with pytest.raises(EmptyLLMResponseError):
        _ = await request_with_recoverable_retry(
            llm_handler=handler,
            model="fake-model",
            messages=[ChatMessage(role="user", text="你好")],
            retry_count=3,
            retry_delay=0,
            task_label="测试任务",
        )

    assert handler.call_count == 1
