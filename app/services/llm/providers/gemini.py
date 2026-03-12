"""Gemini 提供商实现模块。"""

import base64
from typing import cast

from google import genai
from google.genai import types

from ..utils import detect_mime_type

from ..base import LLMProvider
from ..schemas import ChatMessage


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
        self.client = client

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
        chat_messages = []
        system_prompt = ""
        role_map = {
            "user": "user",
            "assistant": "model",
        }
        for msg in messages:
            if msg.role == "system":
                system_prompt = cast(
                    str, msg.text
                )  # 由于系统提示词不会是None，直接断言,
                continue
            role = role_map[msg.role]
            parts = []
            if msg.text:
                parts.append(types.Part.from_text(text=msg.text))
            if msg.image:
                for image_bytes in msg.image:
                    mime_type = detect_mime_type(image_bytes)
                    parts.append(
                        types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
                    )
            content = types.Content(role=role, parts=parts)
            chat_messages.append(content)
        return chat_messages, system_prompt

    async def get_ai_response(
        self,
        messages: list[ChatMessage],
        model: str,
        **kwargs,
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
        response = await self.client.aio.models.generate_content(
            model=model,
            contents=chat_messages,  # type: ignore
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=1,
            ),
        )
        content = response.text
        return content  # type: ignore

    async def get_image(
        self,
        message: ChatMessage,
        model: str,
    ) -> str:
        """调用 Gemini 图文生成接口，并返回生成图片的 Base64 文本。"""
        if not message.text:
            raise ValueError("提示词为空请重新输入")
        contents: list[str | types.Part] = [message.text]
        if message.image:
            for image_bytes in message.image:
                mime_type = detect_mime_type(image_bytes)
                contents.append(
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
                )

        # 流式请求
        async for response in await self.client.aio.models.generate_content_stream(
            model=model,
            contents=contents,  # type: ignore
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            ),
        ):
            if not response.candidates:
                continue

            content = response.candidates[0].content
            if not content or not content.parts:
                continue

            for part in content.parts:
                if part.inline_data and part.inline_data.data:
                    return base64.b64encode(part.inline_data.data).decode("utf-8")

        raise ValueError("未能从响应中获取图片数据")
