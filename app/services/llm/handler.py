"""
LLM 服务管理模块。

负责注册不同提供商实例，并为上层提供统一的请求入口与重试控制。
"""

from google import genai
from google.genai import types
from volcenginesdkarkruntime import AsyncArk

from .utils import create_retry_manager

from .base import LLMProvider
from .providers import GeminiService, OpenAIService, VolcengineService
from .schemas import ChatMessage, LLMSettings
import httpx
from openai import (
    AsyncOpenAI,
    APIConnectionError as OpenAIConnectionError,
    APITimeoutError as OpenAITimeoutError,
    RateLimitError as OpenAIRateLimitError,
)
from volcenginesdkarkruntime._exceptions import (
    ArkAPIConnectionError,
    ArkAPITimeoutError,
    ArkRateLimitError,
)


class LLMHandler:
    """
    LLM 服务统一管理器。

    充当应用层与底层各类 LLM SDK 之间的桥梁。
    它负责接收配置，初始化不同的提供商实例（如 OpenAI, Gemini, Volcengine 等），
    并在实际翻译时，根据服务名称分发请求，同时提供统一的重试和错误处理机制。
    """

    def __init__(self) -> None:
        """初始化空的服务注册表，等待外部注入配置进行注册。"""
        self.services: dict[str, LLMProvider] = {}

    def register_service(self, *, llm_setting: LLMSettings) -> None:
        """
        根据传入的配置参数，实例化对应的 LLM 客户端并注册到内部字典中。

        目前支持的服务商类型包括：`openai`, `volcengine`, `gemini`。
        每个被注册的服务都会被赋予一个唯一的逻辑名称（如 "glossary" 或 "text"），
        以便不同的翻译任务可以调用不同的模型和配置。

        Args:
            llm_setting: 单个 LLM 服务的配置模型实例。

        Raises:
            ValueError: 当试图注册同名服务，或提供了不支持的 provider_type 时抛出。
        """
        if llm_setting.name in self.services:
            raise ValueError(f"服务名称已存在: {llm_setting.name}")
        match llm_setting.provider_type:
            case "openai":
                service_client = AsyncOpenAI(
                    api_key=llm_setting.api_key,
                    base_url=llm_setting.base_url,
                    timeout=llm_setting.timeout,
                )
                self.services[llm_setting.name] = OpenAIService(client=service_client)
            case "volcengine":
                service_client = AsyncArk(
                    api_key=llm_setting.api_key,
                    base_url=llm_setting.base_url,
                    timeout=llm_setting.timeout,
                )
                self.services[llm_setting.name] = VolcengineService(
                    client=service_client,
                )
            case "gemini":
                service_client = genai.Client(
                    api_key=llm_setting.api_key,
                    http_options=types.HttpOptions(
                        base_url=llm_setting.base_url if llm_setting.base_url else None,
                        timeout=llm_setting.timeout * 1000,
                    ),
                )
                self.services[llm_setting.name] = GeminiService(client=service_client)
            case _:
                raise ValueError(
                    f"不支持的 LLM 提供商类型: {llm_setting.provider_type}"
                )

    async def get_ai_response(
        self,
        messages: list[ChatMessage],
        model: str,
        service_name: str,
        retry_count: int = 3,
        retry_delay: int = 1,
        **kwargs,
    ) -> str:
        """
        向指定名称的 LLM 服务发起文本生成请求，并提供自动重试机制。

        此方法封装了各大厂 API 的网络异常类型（如超时、限流、连接重置等），
        当遇到这些错误时，会基于指数退避策略自动重试，以提高请求稳定性。
        如果重试次数耗尽，异常将原样向上抛出。

        Args:
            messages: 当前请求的完整消息历史（按时序排列的 ChatMessage 列表）。
            model: 目标模型的具体标识（如 "gpt-4o", "gemini-1.5-flash"）。
            service_name: 之前在 `register_service` 中注册的唯一服务标识名称。
            retry_count: 网络或限流错误允许的最大重试次数。
            retry_delay: 初始重试延迟时间（秒），随指数退避递增。
            **kwargs: 其他需要透传给底层 SDK 的可选参数。

        Returns:
            LLM 生成的最终文本字符串。

        Raises:
            ValueError: 尚未注册任何服务，或指定的 `service_name` 不存在时抛出；当 LLM 返回空响应时也会抛出。
            Exception: 当发生不在重试白名单内的严重异常，或重试次数耗尽后，抛出对应的异常。
        """
        if not self.services:
            raise ValueError("未注册任何 LLM 服务，请先调用 register_service")
        if service_name not in self.services:
            raise ValueError(f"未注册的 LLM 服务: {service_name}")
        retry_errors = (
            # 1. OpenAI 错误
            OpenAIConnectionError,
            OpenAITimeoutError,
            OpenAIRateLimitError,
            # 2. 火山引擎 (Ark) 错误 - 源码确认：它是独立的类体系
            ArkAPIConnectionError,
            ArkAPITimeoutError,
            ArkRateLimitError,
            # 3. Google GenAI (Gemini) 错误
            # 源码确认：google.genai.errors 只有 APIError(4xx/5xx)，网络层完全透传 httpx
            httpx.TimeoutException,  # 捕获 ConnectTimeout, ReadTimeout
            httpx.NetworkError,  # 捕获 DNS 失败, 连接重置等
            # 4. 通用兜底
            ValueError,
        )
        retrier = create_retry_manager(
            retry_count=retry_count, retry_delay=retry_delay, error_types=retry_errors
        )
        async for attempt in retrier:
            with attempt:
                response = await self.services[service_name].get_ai_response(
                    messages=messages, model=model, **kwargs
                )
                if not response:
                    raise ValueError("LLM 响应为空")
                return response
        raise RuntimeError("LLM 请求失败")  # 死代码
