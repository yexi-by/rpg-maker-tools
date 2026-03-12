"""火山引擎 Ark 提供商实现模块。"""

import base64
from typing import cast

from volcenginesdkarkruntime import AsyncArk
from volcenginesdkarkruntime.types.chat import (
    ChatCompletion,
    ChatCompletionMessageParam,
)

from ..utils import detect_mime_type

from ..base import LLMProvider
from ..schemas import ChatMessage


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
        self.client = client

    def _format_chat_messages(
        self, messages: list[ChatMessage]
    ) -> list[ChatCompletionMessageParam]:
        """
        将内部的通用消息模型列表转化为 Ark 规范的请求体格式。

        对包含图片的复合消息，会进行 Base64 编码并组装成 `image_url` 结构。

        Args:
            messages: 通用 ChatMessage 列表。

        Returns:
            符合 Ark SDK 要求的 ChatCompletionMessageParam 列表。
        """
        chat_messages = []
        for msg in messages:
            msg_dict = {}
            content_lst = []
            msg_dict["role"] = msg.role
            if msg.text:
                content_lst.append({"type": "text", "text": msg.text})
            if msg.image:
                for image_bytes in msg.image:
                    mime_type = detect_mime_type(image_bytes)
                    base64_image = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('utf-8')}"
                    content_lst.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": base64_image, "detail": "auto"},
                        }
                    )
            msg_dict["content"] = content_lst
            chat_messages.append(msg_dict)
        return chat_messages

    async def get_ai_response(
        self,
        messages: list[ChatMessage],
        model: str,
        **kwargs,
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
            ChatCompletion,
            await self.client.chat.completions.create(
                messages=chat_messages, model=model, stream=False
            ),
        )
        content = response.choices[0].message.content
        return content  # type:ignore

    async def get_image(
        self,
        message: ChatMessage,
        model: str,
    ) -> str:
        """
        调用火山引擎图片生成接口（如 Doubao-vision 相关的 endpoint）生成并返回图片。

        Args:
            message: 单条包含图像生成提示词以及可选参考图的消息。
            model: 调用的图像生成模型 Endpoint ID。

        Returns:
            返回由火山引擎生成的图片的 Base64 字符串。
            
        Raises:
            ValueError: 提示词为空时抛出。
        """
        if not message.text:
            raise ValueError("提示词为空请重新输入")

        prompt = message.text
        images = None
        if message.image:
            images = []
            for img_bytes in message.image:
                mime_type = detect_mime_type(img_bytes)
                base64_str = base64.b64encode(img_bytes).decode("utf-8")
                images.append(f"data:{mime_type};base64,{base64_str}")

        # 调用图片生成接口
        response = await self.client.images.generate(
            model=model,
            prompt=prompt,
            image=images,  # 传入 list[str] 或 None，支持多图融合
            response_format="b64_json",  # 指定返回 base64 格式
            size="2K",
            watermark=False,
        )

        # 从响应中提取 base64 编码的图片数据
        b64_string = response.data[0].b64_json

        return b64_string
