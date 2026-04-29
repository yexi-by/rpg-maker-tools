"""Gemini 提供商实现模块。"""

from collections.abc import Awaitable
from typing import Protocol, cast, override

from google import genai
from google.genai import types

from ..base import LLMProvider
from ..schemas import ChatMessage


class _GeminiTextResponse(Protocol):
    """Gemini 文本响应的最小协议。"""

    @property
    def text(self) -> str | None:
        """返回模型生成文本。"""
        ...


class _GeminiModels(Protocol):
    """Gemini 异步模型接口的最小协议。"""

    def generate_content(
        self,
        *,
        model: str,
        contents: list[types.Content],
        config: types.GenerateContentConfig,
    ) -> Awaitable[_GeminiTextResponse]:
        """生成文本响应。"""
        ...


class GeminiService(LLMProvider):
    """
    基于 Google 官方 GenAI SDK (`google-genai`) 的服务实现。

    适配了 Gemini 模型的消息格式要求（将 System Prompt 单独剥离，使用 Content/Part 组装对话体）。
    """

    def __init__(self, client: genai.Client) -> None:
        """
        初始化 Gemini 服务实例。

        Args:
            client: 已配置 API Key 的 GenAI 客户端实例。
        """
        self.client: genai.Client = client

    def _format_chat_messages(
        self, messages: list[ChatMessage]
    ) -> tuple[list[types.Content], str]:
        """
        将内部通用的消息模型转换为 Gemini 特有的消息结构。

        Gemini 的接口要求系统提示词（System Instruction）与用户对话轮次（Contents）分离。
        此方法会在遍历中抽离出 role="system" 的文本。

        Args:
            messages: 通用 ChatMessage 列表。

        Returns:
            二元组：
            1. types.Content 列表，对应用户与模型的交替对话历史。
            2. string，单独抽出的系统提示词（保证非空）。
        """
        chat_messages: list[types.Content] = []
        system_prompt = ""
        role_map = {
            "user": "user",
            "assistant": "model",
        }
        for msg in messages:
            if msg.role == "system":
                system_prompt = msg.text
                continue
            role = role_map[msg.role]
            parts: list[types.Part] = []
            if msg.text:
                parts.append(types.Part.from_text(text=msg.text))
            content = types.Content(role=role, parts=parts)
            chat_messages.append(content)
        return chat_messages, system_prompt

    @override
    async def get_ai_response(
        self,
        messages: list[ChatMessage],
        model: str,
        **kwargs: object,
    ) -> str:
        """
        调用 Gemini 内容生成接口（generate_content）并返回文本结果。

        Args:
            messages: 待发送的消息历史记录。
            model: 调用的具体 Gemini 模型名称。
            **kwargs: 其他备用参数。

        Returns:
            模型返回的文本内容。
        """
        chat_messages, system_prompt = self._format_chat_messages(messages=messages)
        models = cast(_GeminiModels, self.client.aio.models)
        response = await models.generate_content(
            model=model,
            contents=chat_messages,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=1,
            ),
        )
        content = response.text
        if content is None:
            raise ValueError("Gemini 响应中未返回文本内容")
        return content
