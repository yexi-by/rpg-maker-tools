"""应用运行时配置装配。"""

from app.config.schemas import LLMServiceSetting, Setting
from app.llm import LLMHandler, LLMSettings
from app.utils.config_loader_utils import load_setting


def load_runtime_setting(llm_handler: LLMHandler) -> Setting:
    """加载配置，并把文本翻译与插件分析模型服务注册到 LLM 门面。"""
    setting = load_setting()
    llm_handler.clean()
    llm_handler.register_service(
        llm_setting=build_llm_settings(
            name="text",
            service_setting=setting.llm_services.text,
        )
    )
    llm_handler.register_service(
        llm_setting=build_llm_settings(
            name="plugin_text",
            service_setting=setting.llm_services.plugin_text,
        )
    )
    return setting


def build_llm_settings(name: str, service_setting: LLMServiceSetting) -> LLMSettings:
    """把单个服务配置转换为 `LLMHandler` 可注册的连接配置。"""
    return LLMSettings(
        name=name,
        provider_type=service_setting.provider_type,
        base_url=service_setting.base_url,
        api_key=service_setting.api_key,
        timeout=service_setting.timeout,
    )


__all__: list[str] = [
    "build_llm_settings",
    "load_runtime_setting",
]
