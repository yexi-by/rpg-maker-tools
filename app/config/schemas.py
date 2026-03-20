"""
配置模型定义模块。

本模块统一定义项目运行时使用的配置结构，并约束：
1. 配置文件字段必须完整且禁止出现未知键。
2. 提示词文件路径与注入后的提示词正文分别建模，方便校验和 UI 展示。
3. 新增插件文本 AI 解析配置后，仍保持现有配置分层方式不变。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictBaseModel(BaseModel):
    """
    项目统一使用的严格配置模型基类。

    设计意图：
    1. 配置加载时尽早暴露拼写错误和多余字段。
    2. 避免新增配置后旧字段继续被静默接受，导致运行行为难以排查。
    """

    model_config = ConfigDict(extra="forbid")


class LLMServiceSetting(StrictBaseModel):
    """
    单个 LLM 服务连接配置。

    Attributes:
        provider_type: 服务提供商类型。
        base_url: API 基础地址。
        api_key: 服务鉴权密钥。
        model: 当前服务默认使用的模型标识。
        timeout: 请求超时时间，单位为秒。
    """

    provider_type: Literal["openai", "volcengine", "gemini"] = Field(
        title="服务提供商",
        description="指定要使用的 LLM API 兼容协议。",
    )
    base_url: str = Field(
        title="服务 URL",
        description="模型服务地址，支持官方和代理接口。",
    )
    api_key: str = Field(
        title="API 密钥",
        description="访问模型服务所需的鉴权凭据。",
    )
    model: str = Field(
        title="模型名称",
        description="当前服务实际调用的模型标识。",
    )
    timeout: int = Field(
        gt=0,
        title="超时时间",
        description="模型请求的最大等待时间，单位为秒。",
    )


class LLMServicesSetting(StrictBaseModel):
    """
    多个 LLM 服务的聚合配置。

    Attributes:
        glossary: 术语相关任务使用的模型服务配置。
        text: 正文翻译使用的模型服务配置。
        plugin_text: plugins.js 插件文本路径分析使用的模型服务配置。
    """

    glossary: LLMServiceSetting = Field(
        title="术语服务",
        description="用于术语提取和术语翻译时使用的模型服务配置。",
    )
    text: LLMServiceSetting = Field(
        title="正文服务",
        description="用于正文翻译时使用的模型服务配置。",
    )
    plugin_text: LLMServiceSetting = Field(
        title="插件解析服务",
        description="用于 plugins.js 插件文本路径分析时使用的模型服务配置。",
    )


class GlossaryExtractionSetting(StrictBaseModel):
    """
    术语提取阶段配置。

    Attributes:
        role_chunk_blocks: 角色对话切块数量。
        role_chunk_lines: 每块保留的对话行数。
    """

    role_chunk_blocks: int = Field(
        gt=0,
        title="角色采样块数",
        description="提取角色术语时对话切块的目标块数。",
    )
    role_chunk_lines: int = Field(
        gt=0,
        title="每块对话行数",
        description="每个角色术语样本块中保留的对话行数。",
    )


class GlossaryTranslationTaskSetting(StrictBaseModel):
    """
    术语翻译子任务配置。

    Attributes:
        chunk_size: 每批发送给模型的条目数量。
        retry_count: 请求失败时的自动重试次数。
        retry_delay: 请求失败后的初始重试延迟，单位为秒。
        response_retry_count: 响应结构校验失败时的重试次数。
        system_prompt_file: 系统提示词文件路径。
        system_prompt: 运行时注入的系统提示词文本。
    """

    chunk_size: int = Field(
        gt=0,
        title="每批条目数",
        description="每次发送给模型处理的术语条目数量。",
    )
    retry_count: int = Field(
        ge=0,
        title="请求重试次数",
        description="请求失败后自动重试的次数。",
    )
    retry_delay: int = Field(
        ge=0,
        title="请求重试间隔",
        description="请求失败后两次重试之间的等待时间，单位为秒。",
    )
    response_retry_count: int = Field(
        gt=0,
        title="响应重试次数",
        description="模型返回结构不合法时的自动重试次数。",
    )
    system_prompt_file: str = Field(
        title="提示词文件",
        description="当前子任务使用的系统提示词文件路径。",
    )
    system_prompt: str = Field(
        title="提示词内容",
        description="运行时注入的系统提示词文本。",
    )


class GlossaryRoleNameTranslationSetting(StrictBaseModel):
    """
    角色术语翻译配置。

    Attributes:
        retry_count: 请求失败时的自动重试次数。
        retry_delay: 请求失败后的初始重试延迟，单位为秒。
        response_retry_count: 响应结构校验失败时的重试次数。
        system_prompt_file: 系统提示词文件路径。
        system_prompt: 运行时注入的系统提示词文本。
    """

    retry_count: int = Field(
        ge=0,
        title="请求重试次数",
        description="角色术语请求失败后自动重试的次数。",
    )
    retry_delay: int = Field(
        ge=0,
        title="请求重试间隔",
        description="角色术语请求失败后两次重试之间的等待时间，单位为秒。",
    )
    response_retry_count: int = Field(
        gt=0,
        title="响应重试次数",
        description="模型返回结构不合法时的自动重试次数。",
    )
    system_prompt_file: str = Field(
        title="提示词文件",
        description="角色术语翻译使用的系统提示词文件路径。",
    )
    system_prompt: str = Field(
        title="提示词内容",
        description="运行时注入的角色术语系统提示词文本。",
    )


class GlossaryTranslationSetting(StrictBaseModel):
    """
    术语翻译阶段配置。

    Attributes:
        worker_count: 术语翻译共享的并发 worker 数量。
        rpm: 术语翻译共享的每分钟请求上限，空值表示不限速。
        role_name: 角色术语翻译配置。
        display_name: 地点显示名翻译配置。
    """

    worker_count: int = Field(
        gt=0,
        title="并发工作数",
        description="角色名和地点名术语翻译共享的并发 worker 数量。",
    )
    rpm: int | None = Field(
        default=None,
        gt=0,
        title="每分钟请求数",
        description="术语翻译阶段的共享每分钟请求上限，空值表示不限速。",
    )
    role_name: GlossaryRoleNameTranslationSetting = Field(
        title="角色术语",
        description="角色术语翻译阶段的重试和提示词配置。",
    )
    display_name: GlossaryTranslationTaskSetting = Field(
        title="地点术语",
        description="地点显示名翻译阶段的分批、重试和提示词配置。",
    )


class TranslationContextSetting(StrictBaseModel):
    """
    正文切批上下文配置。

    Attributes:
        token_size: 每批目标 token 上限。
        factor: 字符数换算 token 的经验系数。
        max_command_items: 同角色连续对话的强制合并上限。
    """

    token_size: int = Field(
        gt=0,
        title="每批 token 上限",
        description="正文切批时每个批次的目标 token 上限。",
    )
    factor: float = Field(
        gt=0,
        title="字符换算系数",
        description="按字符数估算 token 数量时使用的经验系数。",
    )
    max_command_items: int = Field(
        gt=0,
        title="连续命令上限",
        description="同一角色连续正文在切批时强制合并的最大条目数。",
    )


class PluginTextAnalysisSetting(StrictBaseModel):
    """
    插件文本 AI 路径分析配置。

    Attributes:
        worker_count: 同时运行的插件分析 worker 数量。
        rpm: 每分钟请求上限，空值表示不限速。
        retry_count: 网络层请求失败时的自动重试次数。
        retry_delay: 网络层请求失败后的初始重试延迟，单位为秒。
        response_retry_count: 结构或语义校验失败时的重试次数。
        system_prompt_file: 插件分析提示词文件路径。
        system_prompt: 运行时注入的插件分析提示词文本。
    """

    worker_count: int = Field(
        gt=0,
        title="并发分析数",
        description="插件文本路径分析阶段同时运行的 worker 数量。",
    )
    rpm: int | None = Field(
        default=None,
        gt=0,
        title="每分钟请求数",
        description="插件文本路径分析阶段共享的每分钟请求上限，空值表示不限速。",
    )
    retry_count: int = Field(
        ge=0,
        title="网络重试次数",
        description="模型请求因网络或限流失败时的自动重试次数。",
    )
    retry_delay: int = Field(
        ge=0,
        title="网络重试间隔",
        description="模型请求失败后两次重试之间的等待时间，单位为秒。",
    )
    response_retry_count: int = Field(
        gt=0,
        title="响应重试次数",
        description="模型返回结构或语义校验失败时的自动重试次数。",
    )
    system_prompt_file: str = Field(
        title="提示词文件",
        description="插件文本路径分析阶段使用的系统提示词文件路径。",
    )
    system_prompt: str = Field(
        title="提示词内容",
        description="运行时注入的插件文本路径分析提示词文本。",
    )


class TextTranslationSetting(StrictBaseModel):
    """
    正文翻译阶段配置。

    Attributes:
        worker_count: 并发 worker 数量。
        rpm: 每分钟请求上限，空值表示不限速。
        retry_count: 请求失败时的自动重试次数。
        retry_delay: 请求失败后的初始重试延迟，单位为秒。
        system_prompt_file: 系统提示词文件路径。
        system_prompt: 运行时注入的正文翻译提示词文本。
    """

    worker_count: int = Field(
        gt=0,
        title="并发工作数",
        description="正文翻译阶段同时运行的 worker 数量。",
    )
    rpm: int | None = Field(
        default=None,
        gt=0,
        title="每分钟请求数",
        description="正文翻译阶段共享的每分钟请求上限，空值表示不限速。",
    )
    retry_count: int = Field(
        ge=0,
        title="请求重试次数",
        description="正文翻译请求失败后自动重试的次数。",
    )
    retry_delay: int = Field(
        ge=0,
        title="请求重试间隔",
        description="正文翻译请求失败后两次重试之间的等待时间，单位为秒。",
    )
    system_prompt_file: str = Field(
        title="提示词文件",
        description="正文翻译阶段使用的系统提示词文件路径。",
    )
    system_prompt: str = Field(
        title="提示词内容",
        description="运行时注入的正文翻译提示词文本。",
    )


class Setting(StrictBaseModel):
    """
    项目运行时的总配置模型。

    与旧配置的差异：
    1. 不再包含 `project` 配置段。
    2. 游戏路径、数据库文件名和文本缓存信息由数据库元数据维护。

    Attributes:
        llm_services: 模型服务集合。
        glossary_extraction: 术语提取配置。
        glossary_translation: 术语翻译配置。
        translation_context: 正文切批配置。
        plugin_text_analysis: 插件文本路径分析配置。
        text_translation: 正文翻译配置。
    """

    llm_services: LLMServicesSetting = Field(
        title="模型服务配置",
        description="术语、正文和插件解析使用的模型服务集合。",
    )
    glossary_extraction: GlossaryExtractionSetting = Field(
        title="术语提取配置",
        description="角色术语采样阶段使用的切块参数。",
    )
    glossary_translation: GlossaryTranslationSetting = Field(
        title="术语翻译配置",
        description="角色术语与地点术语翻译阶段的并发和提示词参数。",
    )
    translation_context: TranslationContextSetting = Field(
        title="正文切批配置",
        description="正文切批时使用的 token 上限和连续命令合并参数。",
    )
    plugin_text_analysis: PluginTextAnalysisSetting = Field(
        title="插件解析配置",
        description="plugins.js 插件文本路径分析阶段的并发、限速、重试和提示词参数。",
    )
    text_translation: TextTranslationSetting = Field(
        title="正文翻译配置",
        description="正文翻译阶段的并发、限速和重试参数。",
    )


__all__: list[str] = [
    "GlossaryExtractionSetting",
    "GlossaryRoleNameTranslationSetting",
    "GlossaryTranslationSetting",
    "GlossaryTranslationTaskSetting",
    "LLMServiceSetting",
    "LLMServicesSetting",
    "PluginTextAnalysisSetting",
    "Setting",
    "StrictBaseModel",
    "TextTranslationSetting",
    "TranslationContextSetting",
]
