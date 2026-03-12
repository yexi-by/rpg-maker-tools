"""
配置模式定义模块。
使用 Pydantic 定义所有配置项的数据模型和校验规则。
这里只负责描述“运行时配置长什么样”，不负责读取文件、注入 Prompt 或解析相对路径。
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictBaseModel(BaseModel):
    """
    全项目统一的严格数据基类。

    设计意图：
    由于配置经常会因为版本迭代而产生字段的增删改，
    为了防止旧配置文件的废弃字段被 Pydantic 静默吸收（导致拼写错误难以排查），
    这里通过 `extra="forbid"` 强制约束：只要出现了未定义的字段，反序列化立即报错。
    """

    model_config = ConfigDict(extra="forbid")


class ProjectSetting(StrictBaseModel):
    """
    运行时的项目环境配置。

    与 `ProjectFileSetting` 不同的是，这里的 `work_path` 和 `file_path`
    在经历了 loader 的解析后，已经被转换并固化为 Python 的原生 `Path` 绝对路径对象，
    供业务层直接无脑使用，无需二次解析。

    Attributes:
        file_path: 解析后的游戏根目录绝对路径。
        work_path: 解析后的项目工作区绝对路径（存放 DB、日志）。
        db_name: 数据库文件名。
        translation_table_name: 存放最终翻译成果的 SQLite 表名。
    """

    file_path: Path
    work_path: Path
    db_name: str
    translation_table_name: str


class LLMServiceSetting(StrictBaseModel):
    """
    单个 LLM 服务配置。

    Attributes:
        provider_type: 提供商类型。
        base_url: 服务基础地址。
        api_key: 服务鉴权密钥。
        model: 当前服务默认使用的模型名称。
        timeout: 请求超时时间，单位为秒。
    """

    provider_type: Literal["openai", "volcengine", "gemini"] = Field(
        title="服务提供商", description="指定使用的大语言模型 API 格式和对应的适配器。"
    )
    base_url: str = Field(
        title="基础 URL",
        description="大模型 API 的请求地址，如使用第三方中转服务请在此修改。",
    )
    api_key: str = Field(
        title="API 密钥", description="调用大模型接口所需的身份验证令牌 (Token/Key)。"
    )
    model: str = Field(
        title="模型名称",
        description="具体要调用的模型标识符（如 gpt-4o, gemini-1.5-pro 等）。",
    )
    timeout: int = Field(
        gt=0,
        title="超时时间(秒)",
        description="单次网络请求最大等待时间，超时未响应将触发重试或失败。",
    )


class LLMServicesSetting(StrictBaseModel):
    """
    LLM 服务集合配置。

    Attributes:
        glossary: 术语翻译共用的 LLM 服务配置。
        text: 正文翻译使用的 LLM 服务配置。
    """

    glossary: LLMServiceSetting
    text: LLMServiceSetting


class GlossaryExtractionSetting(StrictBaseModel):
    """
    术语提取阶段配置。

    Attributes:
        role_chunk_blocks: 角色对话采样时切分的块数。
        role_chunk_lines: 每个采样块保留的对话行数。
    """

    role_chunk_blocks: int = Field(
        gt=0,
        title="对话采样块数",
        description="在提取角色台词作为术语参考时，为了保证语境丰富度，将对话在时间线上切分为多个块。",
    )
    role_chunk_lines: int = Field(
        gt=0, title="单块采样行数", description="每个采样块中截取的台词行数。"
    )


class GlossaryTranslationTaskSetting(StrictBaseModel):
    """
    单个术语翻译子任务配置。

    说明：
    1. 一个子任务对应一种术语翻译场景，例如角色名或地图显示名。
    2. `retry_count` / `retry_delay` 用于控制 LLM 请求层重试。
    3. `response_retry_count` 用于控制结构化返回校验失败时的纠错轮次。
    4. 任一分块达到 `response_retry_count` 上限后，整个术语表任务应直接失败。

    Attributes:
        chunk_size: 每轮发送给模型的术语条目数。
        retry_count: 单次 LLM 请求的重试次数。
        retry_delay: 单次 LLM 请求的重试间隔，单位为秒。
        response_retry_count: 结构校验失败时允许追加纠错消息并重试的轮数。
        system_prompt: 当前子任务专属系统提示词。
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
        role_name: 角色名术语翻译配置。
        display_name: 地图显示名术语翻译配置。
    """

    role_name: GlossaryTranslationTaskSetting
    display_name: GlossaryTranslationTaskSetting


class TranslationContextSetting(StrictBaseModel):
    """
    控制正文切批算法的上下文参数。

    为了防止单次发给大模型的文本过多导致报错或超出上下文窗口，
    系统会使用这个配置将大文件切分为多个小批次。

    Attributes:
        token_size: 每个批次期望的 Token 上限目标。
        factor: 由于本地不内置各大厂准确的 Tokenizer 计算器，这里提供一个经验估算因子，用于将字符长度换算为粗略的 Token 数。
        max_command_items: 当切批遇到同一个角色的连续长段落时，允许短暂无视 token_size 限制继续追加的最大条目数量。
    """

    token_size: int = Field(
        gt=0,
        title="每批 Token 上限",
        description="将长文本文件切割成小批次发送时，每个批次允许包含的最大估算 Token 数。",
    )
    factor: float = Field(
        gt=0,
        title="字符换算因子",
        description="因为没有本地 Tokenizer，这里设定一个把字符数转换为 Token 数的估算系数。",
    )
    max_command_items: int = Field(
        gt=0,
        title="强制连块最大条目数",
        description="当切批时遇到同一角色大段连续对话，允许暂时无视 Token 上限继续合并的最多条目数量。",
    )


class ErrorTranslationSetting(StrictBaseModel):
    """
    错误重翻译配置。

    Attributes:
        chunk_size: 错误重翻译时每批包含的错误条目数。
        system_prompt: 错误重翻译专属系统提示词。
    """

    chunk_size: int = Field(gt=0)
    system_prompt: str


class TextTranslationSetting(StrictBaseModel):
    """
    运行时正文并发翻译配置。

    与 FileSetting 不同，此处的 `system_prompt` 已经包含了从外部 txt 文件中读取并注入完毕的完整多行文本。

    Attributes:
        worker_count: 启动的并发协程数。
        rpm: 全局令牌桶限流速率（Requests Per Minute），为空表示火力全开。
        retry_count: 单个批次的网络重试次数。
        retry_delay: 重试的初始惩罚时间。
        system_prompt: 读取完毕的完整系统提示词文本。
    """

    worker_count: int = Field(gt=0)
    rpm: int | None = Field(gt=0)
    retry_count: int = Field(ge=0)
    retry_delay: int = Field(ge=0)
    system_prompt: str


class Setting(StrictBaseModel):
    """
    全局唯一的运行时配置超级聚合根（Aggregate Root）。

    这是经过所有加载、校验、提示词注入和相对路径转换步骤后，
    生成的最终态配置对象。整个应用在生命周期内只会挂载并使用这一个单例。

    Attributes:
        project: 包含所有绝对路径的项目基础环境配置。
        llm_services: 大模型连接凭据集合。
        glossary_extraction: 术语采样算法参数。
        glossary_translation: 术语翻译网络与校验参数。
        translation_context: 正文发包切批算法参数。
        error_translation: 错误纠正任务参数。
        text_translation: 主线正文翻译任务参数。
    """

    project: ProjectSetting
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
    "LLMServicesSetting",
    "LLMServiceSetting",
    "ProjectSetting",
    "Setting",
    "StrictBaseModel",
    "TextTranslationSetting",
    "TranslationContextSetting",
]
