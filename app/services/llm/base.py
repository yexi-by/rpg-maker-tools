"""
LLM 提供商抽象基类模块。

用于约束不同大模型服务商在文本生成和图像生成场景下的统一接口。
"""

from abc import ABC, abstractmethod
from .schemas import ChatMessage


class LLMProvider(ABC):
    """
    所有 LLM 服务提供商的抽象基类。

    它定义了文本生成和多模态图像生成的规范接口。通过多态机制，
    上层的 Handler 无需关心底层使用的是 OpenAI、Gemini 还是其他厂商的 SDK。
    """

    @abstractmethod
    async def get_ai_response(
        self,
        messages: list,
        model: str,
        **kwargs,
    ) -> str:
        """
        发起异步文本生成请求，将应用层的统一消息格式转译并发送给模型。

        Args:
            messages: 按时序排列的对话消息记录（系统提示词、用户指令、助手回复等）。
            model: 需要调用的具体模型名称。
            **kwargs: 其他允许透传给各家原生 SDK 的拓展参数（如 temperature 等）。

        Returns:
            模型最终生成的完整文本字符串。
        """
        pass

    async def get_image(
        self,
        message: ChatMessage,
        model: str,
    ) -> str:
        """
        发起图像生成请求。

        该接口并非所有大模型厂商都提供，因此默认为未实现。
        如果调用的实例支持图片生成（例如提供商内部实现了相关图生图或文生图功能），则必须覆写此方法。

        Args:
            message: 包含生成提示词（Prompt）以及可能作为底图参考的 ChatMessage。
            model: 需要调用的生成模型名称。

        Returns:
            生成的图片，以 Base64 编码字符串的形式返回。

        Raises:
            NotImplementedError: 如果被调用的提供商实现没有重写此方法。
        """
        raise NotImplementedError(f"{self.__class__.__name__} 不支持图像生成功能")
