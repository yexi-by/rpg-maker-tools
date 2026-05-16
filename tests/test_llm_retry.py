"""LLM 错误分类与业务层重试测试。"""

from pathlib import Path
from typing import cast, override

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, AsyncOpenAI

from app.llm import (
    ChatMessage,
    EmptyLLMResponseError,
    LLMHandler,
    LLMRequestFailure,
    is_recoverable_llm_error,
)
from app.llm_request_body_extra import LLMRequestBodyExtra
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


class FakeCompletionMessage:
    """模拟 OpenAI SDK 返回的消息对象。"""

    def __init__(self, content: str) -> None:
        """保存模型文本内容。"""
        self.content: str = content


class FakeChoice:
    """模拟 OpenAI SDK 返回的候选项。"""

    def __init__(self, content: str) -> None:
        """保存候选项消息。"""
        self.message: FakeCompletionMessage = FakeCompletionMessage(content)


class FakeChatCompletionResponse:
    """模拟 OpenAI SDK 的非流式响应。"""

    def __init__(self, content: str) -> None:
        """保存唯一候选项。"""
        self.choices: list[FakeChoice] = [FakeChoice(content)]


class FakeCompletions:
    """记录 Chat Completions 请求参数的假实现。"""

    def __init__(self) -> None:
        """初始化请求记录。"""
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> FakeChatCompletionResponse:
        """记录请求参数并返回固定文本。"""
        self.calls.append(dict(kwargs))
        return FakeChatCompletionResponse("成功")


class FakeChat:
    """模拟 OpenAI SDK 的 chat 命名空间。"""

    def __init__(self, completions: FakeCompletions) -> None:
        """保存 completions 假实现。"""
        self.completions: FakeCompletions = completions


class FakeOpenAIClient:
    """模拟 OpenAI SDK 客户端。"""

    def __init__(self, completions: FakeCompletions) -> None:
        """保存 chat 命名空间。"""
        self.chat: FakeChat = FakeChat(completions)


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

    with pytest.raises(LLMRequestFailure) as caught_error:
        _ = await request_with_recoverable_retry(
            llm_handler=handler,
            model="fake-model",
            messages=[ChatMessage(role="user", text="你好")],
            retry_count=3,
            retry_delay=0,
            task_label="测试任务",
        )

    assert handler.call_count == 1
    assert caught_error.value.info.retryable is False
    assert caught_error.value.attempt_count == 1


@pytest.mark.asyncio
async def test_llm_handler_passes_request_body_extra_to_sdk() -> None:
    """LLM 门面把配置里的额外请求体参数原样透传给 SDK。"""
    handler = LLMHandler()
    fake_completions = FakeCompletions()
    handler.client = cast(AsyncOpenAI, cast(object, FakeOpenAIClient(fake_completions)))
    request_body_extra: LLMRequestBodyExtra = {
        "reasoning_effort": "high",
        "thinking": {"type": "enabled"},
    }
    handler.request_body_extra = request_body_extra

    result = await handler.get_ai_response(
        messages=[ChatMessage(role="user", text="你好")],
        model="fake-model",
    )

    assert result == "成功"
    assert fake_completions.calls[0]["extra_body"] == request_body_extra


def test_llm_handler_rejects_streaming_request_body_extra() -> None:
    """LLM 门面拒绝当前流程不支持的流式返回参数。"""
    handler = LLMHandler()

    with pytest.raises(ValueError, match="当前不支持 LLM 流式返回"):
        handler.configure(
            base_url="https://example.invalid/v1",
            api_key="fake-key",
            timeout=10,
            request_body_extra={"stream": True},
        )
