"""火山引擎 Ark 提供商实现模块。"""

from typing import Protocol, cast, override

from volcenginesdkarkruntime import AsyncArk

from ..base import LLMProvider
from ..schemas import ChatMessage


class _ArkMessage(Protocol):
    """火山 Ark 响应消息的最小文本协议。"""

    content: str | None


class _ArkChoice(Protocol):
    """火山 Ark 响应候选项的最小文本协议。"""

    message: _ArkMessage


class _ArkChatCompletion(Protocol):
    """火山 Ark 聊天响应的最小文本协议。"""

    choices: list[_ArkChoice]


class VolcengineService(LLMProvider):
    """
    基于火山引擎 Ark SDK 的提供商实现。

    用于接入火山引擎上的各种大模型服务（如 Doubao 系列）。
    Ark SDK 在消息体结构和生成接口上高度兼容 OpenAI 的规范，
    因此其消息转换逻辑与 OpenAI 的实现基本一致。
    """

    def __init__(self, client: AsyncArk) -> None:
        """
        初始化火山引擎服务实例。

        Args:
            client: 已配置好 baseUrl 和 apiKey 的 AsyncArk 实例。
        """
        self.client: AsyncArk = client

    def _format_chat_messages(self, messages: list[ChatMessage]) -> list[dict[str, str]]:
        """
        将内部的通用消息模型列表转化为 Ark 规范的请求体格式。

        Args:
            messages: 通用 ChatMessage 列表。

        Returns:
            符合 Ark SDK 文本聊天接口要求的消息列表。
        """
        chat_messages: list[dict[str, str]] = []
        for msg in messages:
            chat_messages.append({"role": msg.role, "content": msg.text})
        return chat_messages

    @override
    async def get_ai_response(
        self,
        messages: list[ChatMessage],
        model: str,
        **kwargs: object,
    ) -> str:
        """
        调用火山引擎 Chat Completions 接口生成文本响应。

        Args:
            messages: 待发送的消息历史记录。
            model: 调用的具体 Ark 模型接入点（Endpoint ID）。
            **kwargs: 扩展参数。

        Returns:
            模型返回的文本内容。
        """
        chat_messages = self._format_chat_messages(messages)
        response = cast(
            _ArkChatCompletion,
            await self.client.chat.completions.create(
                messages=chat_messages, model=model, stream=False
            ),
        )
        content = response.choices[0].message.content
        if content is None:
            raise ValueError("火山引擎响应中未返回文本内容")
        return content
