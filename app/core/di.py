"""
依赖注入提供器定义。

本模块服务于当前多游戏主线，只负责建立新的生命周期模板：
1. APP 级共享 LLM 与多游戏管理器。
2. REQUEST 级热加载 `setting.toml` 并重配共享 LLM。
3. 不在容器里隐式推导当前游戏，目标游戏统一由 handler 显式传入 `game_title`。
"""

from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide

from app.config.schemas import LLMServiceSetting, Setting
from app.core.game_data_manager import GameDataManager
from app.database.db import GameDatabaseManager
from app.services.llm import LLMHandler, LLMSettings
from app.translation import GlossaryTranslation, TextTranslation, TranslationCache
from app.utils.config_loader_utils import load_setting


def _build_llm_settings(name: str, service_setting: LLMServiceSetting) -> LLMSettings:
    """
    把单个服务配置转换为 `LLMHandler` 可注册的连接配置对象。

    Args:
        name: 在 `LLMHandler` 内部使用的服务名。
        service_setting: 当前服务对应的配置对象。

    Returns:
        转换后的模型连接配置对象。
    """
    return LLMSettings(
        name=name,
        provider_type=service_setting.provider_type,
        base_url=service_setting.base_url,
        api_key=service_setting.api_key,
        timeout=service_setting.timeout,
    )


class TranslationProvider(Provider):
    """
    多游戏新栈的依赖提供器。

    生命周期设计：
    1. `LLMHandler`、`GameDataManager`、`GameDatabaseManager` 为 APP 级共享单例。
    2. `Setting`、翻译器和缓存为 REQUEST 级对象。
    3. 数据库管理器在容器关闭时自动释放全部 SQLite 连接。
    """

    @provide(scope=Scope.APP)
    def get_llm_handler(self) -> LLMHandler:
        """
        创建共享的模型调度器。

        Returns:
            尚未注册任何服务的空 `LLMHandler`。
        """
        return LLMHandler()

    @provide(scope=Scope.APP)
    def get_game_data_manager(self) -> GameDataManager:
        """
        创建全局游戏数据管理器。

        Returns:
            APP 级共享的 `GameDataManager`。
        """
        return GameDataManager()

    @provide(scope=Scope.APP)
    async def get_game_database_manager(self) -> AsyncIterator[GameDatabaseManager]:
        """
        创建全局游戏数据库管理器，并在容器结束时关闭连接。

        Yields:
            APP 级共享的 `GameDatabaseManager`。
        """
        manager = await GameDatabaseManager.new()
        try:
            yield manager
        finally:
            await manager.close()

    @provide(scope=Scope.REQUEST)
    def get_setting(self, llm_handler: LLMHandler) -> Setting:
        """
        加载本次请求使用的配置，并重配共享 `LLMHandler`。

        Args:
            llm_handler: APP 级共享的模型调度器。

        Returns:
            本次请求独立使用的配置对象。
        """
        setting = load_setting()
        llm_handler.clean()
        llm_handler.register_service(
            llm_setting=_build_llm_settings(
                name="glossary",
                service_setting=setting.llm_services.glossary,
            )
        )
        llm_handler.register_service(
            llm_setting=_build_llm_settings(
                name="text",
                service_setting=setting.llm_services.text,
            )
        )
        return setting

    @provide(scope=Scope.REQUEST)
    def get_glossary_translation(self, setting: Setting) -> GlossaryTranslation:
        """
        创建请求级术语翻译器。

        Args:
            setting: 当前请求的配置对象。

        Returns:
            绑定当前请求配置的 `GlossaryTranslation`。
        """
        return GlossaryTranslation(setting=setting)

    @provide(scope=Scope.REQUEST)
    def get_text_translation(self, setting: Setting) -> TextTranslation:
        """
        创建请求级正文翻译器。

        Args:
            setting: 当前请求的配置对象。

        Returns:
            绑定当前请求配置的 `TextTranslation`。
        """
        return TextTranslation(setting)

    @provide(scope=Scope.REQUEST)
    def get_translation_cache(self) -> TranslationCache:
        """
        创建请求级正文去重缓存。

        Returns:
            当前请求使用的 `TranslationCache`。
        """
        return TranslationCache()


__all__: list[str] = ["TranslationProvider"]
