"""OpenAI 提供商实现模块。"""

from typing import override

from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)

from ..base import LLMProvider
from ..schemas import ChatMessage


class OpenAIService(LLMProvider):
    """
    基于 OpenAI 官方异步 SDK 的提供商实现。

    实现了统一的接口规范，用于处理标准文本对话以及图文多模态请求。
    """

    def __init__(self, client: AsyncOpenAI) -> None:
        """
        初始化 OpenAI 服务实例。

        Args:
            client: 已经配置好 baseUrl 和 apiKey 的 AsyncOpenAI 实例。
        """
        self.client: AsyncOpenAI = client

    def _format_chat_messages(
        self, messages: list[ChatMessage]
    ) -> list[ChatCompletionMessageParam]:
        """
        将内部的通用消息模型列表转化为 OpenAI API 规范的请求体格式。

        Args:
            messages: 通用 ChatMessage 列表。
            
        Returns:
            符合 OpenAI SDK 要求的 ChatCompletionMessageParam 列表。
        """
        chat_messages: list[ChatCompletionMessageParam] = []
        for msg in messages:
            if msg.role == "system":
                chat_messages.append(ChatCompletionSystemMessageParam(role="system", content=msg.text))
            elif msg.role == "user":
                chat_messages.append(ChatCompletionUserMessageParam(role="user", content=msg.text))
            else:
                chat_messages.append(ChatCompletionAssistantMessageParam(role="assistant", content=msg.text))
        return chat_messages

    @override
    async def get_ai_response(
        self,
        messages: list[ChatMessage],
        model: str,
        **kwargs: object,
    ) -> str:
        """
        调用 OpenAI Chat Completions 接口生成纯文本响应。

        Args:
            messages: 待发送的消息历史记录。
            model: 调用的具体 OpenAI 模型（如 gpt-4o）。
            **kwargs: 扩展参数。

        Returns:
            模型返回的生成文本。
        """
        chat_messages = self._format_chat_messages(messages)
        if "temperature" in kwargs:
            raw_temperature = kwargs["temperature"]
            if isinstance(raw_temperature, bool) or not isinstance(
                raw_temperature,
                int | float,
            ):
                raise TypeError("OpenAI temperature 参数必须是数字")
            response = await self.client.chat.completions.create(
                model=model,
                messages=chat_messages,
                temperature=float(raw_temperature),
            )
        else:
            response = await self.client.chat.completions.create(
                model=model,
                messages=chat_messages,
            )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("OpenAI 响应中未返回文本内容")
        return content
