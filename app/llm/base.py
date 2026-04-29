"""
LLM 提供商抽象基类模块。

用于约束不同大模型服务商在文本生成场景下的统一接口。
"""

from abc import ABC, abstractmethod
from .schemas import ChatMessage


class LLMProvider(ABC):
    """
    所有 LLM 服务提供商的抽象基类。

    它只保留正文翻译需要的文本生成接口。图像生成属于原项目旁枝能力，
    已在核心 CLI 收缩中删除。
    """

    @abstractmethod
    async def get_ai_response(
        self,
        messages: list[ChatMessage],
        model: str,
        **kwargs: object,
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
        raise NotImplementedError
