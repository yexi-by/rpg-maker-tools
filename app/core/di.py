"""依赖注入提供器定义。"""

from dishka import Provider, Scope, provide

from app.config import LLMServiceSetting, Setting, load_setting
from app.database.db import TranslationDB
from app.extraction import DataTextExtraction, GlossaryExtraction, PluginTextExtraction
from app.models.loaders import load_game_data
from app.models.schemas import GameData
from app.services.llm import LLMHandler, LLMSettings
from app.translation import GlossaryTranslation, TextTranslation, TranslationCache


def _build_llm_settings(name: str, service_setting: LLMServiceSetting) -> LLMSettings:
    """把运行配置中的单个服务转换为模型连接配置对象。"""
    return LLMSettings(
        name=name,
        provider_type=service_setting.provider_type,
        base_url=service_setting.base_url,
        api_key=service_setting.api_key,
        timeout=service_setting.timeout,
    )


class TranslationProvider(Provider):
    """统一声明进程级与请求级依赖的提供规则。"""

    @provide(scope=Scope.APP)
    def get_setting(self) -> Setting:
        """加载项目根目录中的运行配置。"""
        return load_setting()

    @provide(scope=Scope.APP)
    async def get_game_data(self, setting: Setting) -> GameData:
        """在进程内只加载一次游戏数据。"""
        return await load_game_data(setting.project.file_path)

    @provide(scope=Scope.APP)
    def get_llm_handler(self, setting: Setting) -> LLMHandler:
        """创建共享的模型调度器，并注册术语与正文服务。"""
        handler = LLMHandler()
        handler.register_service(
            llm_setting=_build_llm_settings(
                name="glossary",
                service_setting=setting.llm_services.glossary,
            )
        )
        handler.register_service(
            llm_setting=_build_llm_settings(
                name="text",
                service_setting=setting.llm_services.text,
            )
        )
        return handler

    @provide(scope=Scope.APP)
    async def get_translation_db(self, setting: Setting) -> TranslationDB:
        """创建共享的翻译数据库门面。"""
        return await TranslationDB.new(setting)

    @provide(scope=Scope.REQUEST)
    def get_glossary_extraction(self, game_data: GameData) -> GlossaryExtraction:
        """创建请求级术语提取器。"""
        return GlossaryExtraction(game_data)

    @provide(scope=Scope.REQUEST)
    def get_data_text_extraction(self, game_data: GameData) -> DataTextExtraction:
        """创建请求级数据目录正文提取器。"""
        return DataTextExtraction(game_data)

    @provide(scope=Scope.REQUEST)
    def get_plugin_text_extraction(
        self, game_data: GameData
    ) -> PluginTextExtraction:
        """创建请求级插件正文提取器。"""
        return PluginTextExtraction(game_data)

    @provide(scope=Scope.REQUEST)
    def get_glossary_translation(self, setting: Setting) -> GlossaryTranslation:
        """创建请求级术语翻译器。"""
        return GlossaryTranslation(setting)

    @provide(scope=Scope.REQUEST)
    def get_text_translation(self, setting: Setting) -> TextTranslation:
        """创建请求级正文翻译器。"""
        return TextTranslation(setting)

    @provide(scope=Scope.REQUEST)
    def get_translation_cache(self) -> TranslationCache:
        """创建单次正文翻译使用的请求级去重缓存。"""
        return TranslationCache()


__all__: list[str] = ["TranslationProvider"]
