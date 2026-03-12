"""
LLM 服务层数据模型模块。

定义消息结构和单个服务配置结构，供不同提供商实现与服务管理器复用。
"""

from typing import Literal

from pydantic import BaseModel, field_serializer, model_validator

class ChatMessage(BaseModel):
    """
    单条对话消息模型。

    用于统一抹平各个大模型提供商在对话记录数据结构上的差异。
    它不仅支持标准的文本消息通信，还支持传递“文本+多图字节流”的复合媒体消息。
    """

    role: Literal["system", "user", "assistant"]
    text: str
    image: list[bytes] | None = None

    @model_validator(mode="after")
    def check_at_least_one(self) -> "ChatMessage":
        """
        结构校验：确保每一条消息都有内容载体（文本或图片二选一）。
        
        Returns:
            验证通过后返回消息模型本身。
        """
        if self.text is None and self.image is None:
            raise ValueError("必须提供 text 或 image")
        return self

    @field_serializer("image")
    def serialize_image(
        self,
        image: list[bytes] | None,
        _info,
    ) -> list[str] | None:
        """
        自定义序列化规则：为了防止日志输出时满屏被不可读的 Base64 字节流霸屏，
        在打印或转 JSON 时，将图片数组转化为其字节长度的摘要描述。
        """
        if image is None:
            return None
        image_str_lst: list[str] = [
            f"此图片字节码长度为{len(image_bytes)}" for image_bytes in image
        ]
        return image_str_lst


class LLMSettings(BaseModel):
    """
    单个 LLM 服务的连接配置模型。

    在 Handler 注册不同服务商时读取此模型。
    它解耦了应用层和具体提供商（OpenAI / Volcengine / Gemini）之间关于地址与凭据的依赖。

    Attributes:
        name: 当前服务实例在项目内部的标识名称（如 "text", "glossary"）。
        provider_type: 服务所属厂商，用于分配具体的初始化驱动逻辑。
        base_url: 服务的 API 入口地址。
        api_key: 服务鉴权密钥。
        timeout: 发送请求时的最大超时阈值（秒）。
    """

    name: str
    provider_type: Literal["openai", "volcengine", "gemini"]
    base_url: str
    api_key: str
    timeout: int = 600
