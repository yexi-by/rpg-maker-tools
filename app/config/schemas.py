"""
新配置模型定义模块。

本模块服务于多游戏新栈，结构上与旧配置保持大体一致，
但明确移除了旧单游戏流程依赖的 `project` 配置段。
这里的职责只包括定义配置结构与校验规则，不负责读取文件或注入提示词。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictBaseModel(BaseModel):
    """
    新配置栈统一使用的严格基础模型。

    设计意图：
    1. 配置文件升级时，尽早暴露拼写错误或废弃字段。
    2. 避免旧配置里的无效字段被静默吞掉，导致后续行为难以排查。
    """

    model_config = ConfigDict(extra="forbid")


class LLMServiceSetting(StrictBaseModel):
    """
    单个 LLM 服务配置。

    Attributes:
        provider_type: 服务提供商类型。
        base_url: 请求基地址。
        api_key: 鉴权密钥。
        model: 本服务默认使用的模型标识。
        timeout: 单次请求超时时间，单位为秒。
    """

    provider_type: Literal["openai", "volcengine", "gemini"] = Field(
        title="服务提供商",
        description="指定要使用的 LLM API 兼容协议。",
    )
    base_url: str = Field(
        title="基础 URL",
        description="服务端点地址，支持官方或兼容代理。",
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
        description="单次网络请求允许等待的最大秒数。",
    )


class LLMServicesSetting(StrictBaseModel):
    """
    多个 LLM 服务的聚合配置。

    Attributes:
        glossary: 术语翻译服务配置。
        text: 正文翻译服务配置。
    """

    glossary: LLMServiceSetting
    text: LLMServiceSetting


class GlossaryExtractionSetting(StrictBaseModel):
    """
    术语提取阶段配置。

    Attributes:
        role_chunk_blocks: 角色样本切分块数。
        role_chunk_lines: 每块保留的对话行数。
    """

    role_chunk_blocks: int = Field(
        gt=0,
        title="角色样本块数",
        description="按时间线切分角色对话时的目标块数。",
    )
    role_chunk_lines: int = Field(
        gt=0,
        title="每块样本行数",
        description="每个角色样本块保留的对话行数。",
    )


class GlossaryTranslationTaskSetting(StrictBaseModel):
    """
    单个术语翻译子任务配置。

    Attributes:
        chunk_size: 每轮发送给模型的术语条目数。
        retry_count: 网络请求失败时的重试次数。
        retry_delay: 网络请求重试延迟，单位为秒。
        response_retry_count: 结构校验失败时的纠错轮数。
        system_prompt: 当前任务的完整系统提示词。
    """

    chunk_size: int = Field(gt=0)
    retry_count: int = Field(ge=0)
    retry_delay: int = Field(ge=0)
    response_retry_count: int = Field(gt=0)
    system_prompt: str


class GlossaryTranslationSetting(StrictBaseModel):
    """
    术语翻译配置。

    Attributes:
        role_name: 角色名翻译配置。
        display_name: 地图显示名翻译配置。
    """

    role_name: GlossaryTranslationTaskSetting
    display_name: GlossaryTranslationTaskSetting


class TranslationContextSetting(StrictBaseModel):
    """
    正文切批上下文配置。

    Attributes:
        token_size: 每批目标 token 上限。
        factor: 字符长度换算 token 的经验系数。
        max_command_items: 同角色连续段落允许强制合并的最大条目数。
    """

    token_size: int = Field(
        gt=0,
        title="每批 token 上限",
        description="构建正文上下文批次时的目标 token 上限。",
    )
    factor: float = Field(
        gt=0,
        title="字符换算系数",
        description="将字符数量粗略折算为 token 数的经验系数。",
    )
    max_command_items: int = Field(
        gt=0,
        title="最大连续条目数",
        description="构建上下文时允许强制合并的最大条目数。",
    )


class ErrorTranslationSetting(StrictBaseModel):
    """
    错误重翻配置。

    Attributes:
        chunk_size: 每批错误项数量。
        system_prompt: 错误重翻使用的完整系统提示词。
    """

    chunk_size: int = Field(gt=0)
    system_prompt: str


class TextTranslationSetting(StrictBaseModel):
    """
    正文翻译运行配置。

    Attributes:
        worker_count: 并发 worker 数量。
        rpm: 每分钟请求上限，为空表示不限速。
        retry_count: 网络失败重试次数。
        retry_delay: 网络失败重试延迟，单位为秒。
        system_prompt: 正文翻译使用的完整系统提示词。
    """

    worker_count: int = Field(gt=0)
    rpm: int | None = Field(gt=0)
    retry_count: int = Field(ge=0)
    retry_delay: int = Field(ge=0)
    system_prompt: str


class Setting(StrictBaseModel):
    """
    多游戏新栈使用的运行时配置根模型。

    与旧配置的差异：
    1. 不再包含 `project` 配置段。
    2. 游戏路径、数据库文件和译文表名等信息，改由运行时参数和数据库元数据负责。

    Attributes:
        llm_services: 模型服务配置集合。
        glossary_extraction: 术语提取参数。
        glossary_translation: 术语翻译参数。
        translation_context: 正文切批参数。
        error_translation: 错误重翻参数。
        text_translation: 正文翻译参数。
    """

    llm_services: LLMServicesSetting
    glossary_extraction: GlossaryExtractionSetting
    glossary_translation: GlossaryTranslationSetting
    translation_context: TranslationContextSetting
    error_translation: ErrorTranslationSetting
    text_translation: TextTranslationSetting


__all__: list[str] = [
    "ErrorTranslationSetting",
    "GlossaryExtractionSetting",
    "GlossaryTranslationSetting",
    "GlossaryTranslationTaskSetting",
    "LLMServiceSetting",
    "LLMServicesSetting",
    "Setting",
    "StrictBaseModel",
    "TextTranslationSetting",
    "TranslationContextSetting",
]
