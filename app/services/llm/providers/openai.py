"""OpenAI 提供商实现模块。"""

import base64

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from ..utils import detect_mime_type

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
        self.client = client

    def _format_chat_messages(
        self, messages: list[ChatMessage]
    ) -> list[ChatCompletionMessageParam]:
        """
        将内部的通用消息模型列表转化为 OpenAI API 规范的请求体格式。

        对包含图片的复合消息，会进行 Base64 编码并组装成 `image_url` 结构。
        
        Args:
            messages: 通用 ChatMessage 列表。
            
        Returns:
            符合 OpenAI SDK 要求的 ChatCompletionMessageParam 列表。
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
        调用 OpenAI Chat Completions 接口生成纯文本响应。

        Args:
            messages: 待发送的消息历史记录。
            model: 调用的具体 OpenAI 模型（如 gpt-4o）。
            **kwargs: 扩展参数。

        Returns:
            模型返回的生成文本。
        """
        chat_messages = self._format_chat_messages(messages)
        response = await self.client.chat.completions.create(
            model=model, messages=chat_messages, **kwargs
        )
        content = response.choices[0].message.content
        return content  # type:ignore

    async def get_image(self, message: ChatMessage, model: str) -> str:
        """
        处理与图像相关的生成任务。

        注意：OpenAI 原生的图像生成应使用 `client.images.generate`（DALL-E 系列），
        但此处仍沿用 chat 接口（适用于一些能够输出 Base64 的特定模型微调，或第三方中转代理商）。

        Args:
            message: 单条包含图像生成提示词的消息。
            model: 使用的模型。

        Returns:
            由模型返回的图片数据。
            
        Raises:
            ValueError: 响应中未能成功获取有效内容时抛出。
        """
        chat_messages = self._format_chat_messages(messages=[message])
        response = await self.client.chat.completions.create(
            model=model, messages=chat_messages
        )
        image_data = response.choices[0].message.content
        if not image_data:
            raise ValueError("未能从响应中获取图片数据")
        return image_data
