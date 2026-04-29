"""
配置模型定义模块。

本模块只保留 CLI 核心化重构后仍需要的运行配置：模型服务、正文切批、插件解析、
正文翻译和文本规则。所有配置继续从项目根目录的 `setting.toml` 读取，避免业务代码
散落硬编码判断。
"""

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictBaseModel(BaseModel):
    """项目统一使用的严格配置模型基类。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


class LLMServiceSetting(StrictBaseModel):
    """单个 LLM 服务连接配置。"""

    provider_type: Literal["openai", "volcengine", "gemini"] = Field(
        title="服务提供商",
        description="指定要使用的 LLM API 兼容协议。",
    )
    base_url: str = Field(title="服务 URL", description="模型服务地址。")
    api_key: str = Field(title="API 密钥", description="访问模型服务所需凭据。")
    model: str = Field(title="模型名称", description="实际调用的模型标识。")
    timeout: int = Field(gt=0, title="超时时间", description="单位为秒。")


class LLMServicesSetting(StrictBaseModel):
    """CLI 核心流程需要的模型服务集合。"""

    text: LLMServiceSetting = Field(
        title="正文服务",
        description="用于正文翻译的模型服务配置。",
    )
    plugin_text: LLMServiceSetting = Field(
        title="插件解析服务",
        description="用于 plugins.js 插件文本路径分析的模型服务配置。",
    )


class TranslationContextSetting(StrictBaseModel):
    """正文切批上下文配置。"""

    token_size: int = Field(gt=0, title="每批 token 上限")
    factor: float = Field(gt=0, title="字符换算系数")
    max_command_items: int = Field(gt=0, title="连续命令上限")


class PluginTextAnalysisSetting(StrictBaseModel):
    """插件文本 AI 路径分析配置。"""

    worker_count: int = Field(gt=0, title="并发分析数")
    rpm: int | None = Field(default=None, gt=0, title="每分钟请求数")
    retry_count: int = Field(ge=0, title="网络重试次数")
    retry_delay: int = Field(ge=0, title="网络重试间隔")
    response_retry_count: int = Field(gt=0, title="响应重试次数")
    system_prompt_file: str = Field(title="提示词文件")
    system_prompt: str = Field(title="提示词内容")


class TextTranslationSetting(StrictBaseModel):
    """正文翻译阶段配置。"""

    worker_count: int = Field(gt=0, title="并发工作数")
    rpm: int | None = Field(default=None, gt=0, title="每分钟请求数")
    retry_count: int = Field(ge=0, title="请求重试次数")
    retry_delay: int = Field(ge=0, title="请求重试间隔")
    system_prompt_file: str = Field(title="提示词文件")
    system_prompt: str = Field(title="提示词内容")


class TextRulesSetting(StrictBaseModel):
    """可配置的文本判断规则。"""

    strip_wrapping_punctuation_pairs: list[tuple[str, str]] = Field(
        default_factory=lambda: [("「", "」")],
        title="提取时剥离的成对标点",
    )
    plugin_command_text_keywords: list[str] = Field(
        default_factory=lambda: ["text", "message", "name", "desc"],
        title="357 插件命令文本字段关键词",
    )
    plugin_command_excluded_keys: list[str] = Field(
        default_factory=lambda: ["filename", "fontname"],
        title="357 插件命令排除键",
    )
    non_translatable_path_keywords: list[str] = Field(
        default_factory=lambda: [
            "file",
            "filename",
            "filepath",
            "font",
            "fontface",
            "fontname",
            "icon",
            "id",
            "image",
            "img",
            "path",
            "picture",
            "source",
            "src",
            "symbol",
            "url",
        ],
        title="不可翻译路径键",
    )
    excluded_plugin_names: list[str] = Field(
        default_factory=list,
        title="整插件排除名单",
    )
    excluded_plugin_command_fields: list[str] = Field(
        default_factory=list,
        title="插件命令字段排除三元组",
    )
    boolean_texts: list[str] = Field(default_factory=lambda: ["true", "false"])
    generic_enum_texts: list[str] = Field(
        default_factory=lambda: [
            "auto",
            "bottom",
            "center",
            "default",
            "false",
            "left",
            "none",
            "off",
            "on",
            "right",
            "top",
            "true",
        ]
    )
    file_like_suffixes: list[str] = Field(
        default_factory=lambda: [
            ".aac",
            ".avi",
            ".bmp",
            ".css",
            ".csv",
            ".flac",
            ".gif",
            ".jpeg",
            ".jpg",
            ".js",
            ".json",
            ".m4a",
            ".mid",
            ".midi",
            ".mov",
            ".mp3",
            ".mp4",
            ".ogg",
            ".otf",
            ".png",
            ".svg",
            ".tif",
            ".ttf",
            ".txt",
            ".wav",
            ".webm",
            ".webp",
            ".woff",
            ".woff2",
        ]
    )
    no_param_alpha_control_codes: list[str] = Field(default_factory=lambda: ["G"])
    allowed_japanese_chars: list[str] = Field(
        default_factory=lambda: ["っ", "ッ", "ー", "・", "。", "～", "…"]
    )
    allowed_japanese_tail_chars: list[str] = Field(
        default_factory=lambda: [
            "あ",
            "い",
            "う",
            "え",
            "お",
            "っ",
            "ッ",
            "ん",
            "ー",
            "よ",
            "ね",
            "な",
            "か",
        ]
    )
    line_split_punctuations: list[str] = Field(
        default_factory=lambda: ["，", "。", ",", "."]
    )
    long_text_hanzi_limit: int = Field(default=30, gt=0)
    simple_control_param_pattern: str = Field(default=r"[A-Za-z0-9_]+")
    translation_placeholder_pattern: str = Field(
        default=r"\[[A-Z]+(?:_[^\]]+)?\]",
    )
    japanese_segment_pattern: str = Field(default=r"[\u3040-\u309F\u30A0-\u30FF]+")
    placeholder_pattern: str = Field(default=r"\[[A-Z]+(?:_[^\]]+)?\]")
    resource_like_pattern: str = Field(
        default=r"(?:[A-Za-z]:)?[A-Za-z0-9_./\\:-]+\.[A-Za-z0-9]{1,8}",
    )
    pure_number_pattern: str = Field(default=r"[-+]?\d+(?:\.\d+)?")
    hex_color_pattern: str = Field(default=r"#[0-9A-Fa-f]{6,8}")
    css_color_function_pattern: str = Field(
        default=r"(?:rgb|rgba|hsl|hsla)\([^)]*\)",
    )
    resource_path_pattern: str = Field(
        default=r"(?:[A-Za-z]:)?(?:[A-Za-z0-9_.-]+[\\/])+[A-Za-z0-9_.-]+",
    )
    snake_case_pattern: str = Field(default=r"[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+")
    camel_case_pattern: str = Field(
        default=r"(?:[a-z]+(?:[A-Z][a-z0-9]+)+|[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+)",
    )
    bracket_identifier_pattern: str = Field(
        default=r"\$?[A-Za-z_][A-Za-z0-9_]*\[[^\]]+\](?:\.[A-Za-z_][A-Za-z0-9_]*)*",
    )
    dot_identifier_pattern: str = Field(
        default=r"\$?[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+",
    )
    script_concat_pattern: str = Field(default=r"(?:['\"].*['\"]\s*\+\s*|\+\s*['\"].*['\"])")
    script_call_pattern: str = Field(default=r"\$?[A-Za-z_][A-Za-z0-9_]*\([^)]*\)")
    non_content_after_control_pattern: str = Field(
        default=r"[\s\.\,\:\;\-\+\*\/\\\(\)\[\]\{\}<>=!?'\"%]*",
    )


class Setting(StrictBaseModel):
    """项目运行时总配置。"""

    llm_services: LLMServicesSetting = Field(title="模型服务配置")
    translation_context: TranslationContextSetting = Field(title="正文切批配置")
    plugin_text_analysis: PluginTextAnalysisSetting = Field(title="插件解析配置")
    text_translation: TextTranslationSetting = Field(title="正文翻译配置")
    text_rules: TextRulesSetting = Field(default_factory=TextRulesSetting, title="文本规则")


__all__: list[str] = [
    "LLMServiceSetting",
    "LLMServicesSetting",
    "PluginTextAnalysisSetting",
    "Setting",
    "StrictBaseModel",
    "TextRulesSetting",
    "TextTranslationSetting",
    "TranslationContextSetting",
]
