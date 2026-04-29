"""
LLM 服务层数据模型模块。

定义消息结构和单个服务配置结构，供不同提供商实现与服务管理器复用。
"""

from typing import Literal

from pydantic import BaseModel

class ChatMessage(BaseModel):
    """
    单条对话消息模型。

    用于统一抹平各个大模型提供商在对话记录数据结构上的差异。
    当前核心 CLI 只保留文本翻译能力，因此消息体不再携带图片字段。
    """

    role: Literal["system", "user", "assistant"]
    text: str


class LLMSettings(BaseModel):
    """
    单个 LLM 服务的连接配置模型。

    在 Handler 注册不同服务商时读取此模型。
    它解耦了应用层和具体提供商（OpenAI / Volcengine / Gemini）之间关于地址与凭据的依赖。

    Attributes:
        name: 当前服务实例在项目内部的标识名称（如 "text", "plugin_text"）。
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
