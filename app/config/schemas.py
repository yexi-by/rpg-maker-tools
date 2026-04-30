"""
配置模型定义模块。

本模块定义 CLI 翻译流程的运行配置：正文模型服务、正文切批、
正文翻译和文本规则。所有配置继续从项目根目录的 `setting.toml` 读取，避免业务代码
散落硬编码判断。
"""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class StrictBaseModel(BaseModel):
    """项目统一使用的严格配置模型基类。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


class LLMSetting(StrictBaseModel):
    """正文翻译使用的 OpenAI 兼容服务连接配置。"""

    base_url: str = Field(title="服务 URL", description="模型服务地址。")
    api_key: str = Field(title="API 密钥", description="访问模型服务所需凭据。")
    model: str = Field(title="模型名称", description="实际调用的模型标识。")
    timeout: int = Field(gt=0, title="超时时间", description="单位为秒。")


class TranslationContextSetting(StrictBaseModel):
    """正文切批上下文配置。"""

    token_size: int = Field(gt=0, title="每批 token 上限")
    factor: float = Field(gt=0, title="字符换算系数")
    max_command_items: int = Field(gt=0, title="连续命令上限")


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
    long_text_line_width_limit: int = Field(default=30, gt=0)
    line_width_count_pattern: str = Field(default=r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
    control_code_prefix: str = Field(default="\\")
    percent_control_prefix: str = Field(default="%")
    percent_control_param_pattern: str = Field(default=r"\d+")
    control_code_name_pattern: str = Field(default=r"[A-Za-z]+")
    control_param_delimiters: list[tuple[str, str]] = Field(
        default_factory=lambda: [("[", "]"), ("<", ">")]
    )
    non_nested_control_param_open_delimiters: list[str] = Field(default_factory=lambda: ["<"])
    complex_control_open_delimiters: list[str] = Field(default_factory=lambda: ["<"])
    complex_control_param_markers: list[str] = Field(
        default_factory=lambda: ["\\", "[", "]", "<", ">"]
    )
    enable_symbol_control_placeholders: bool = Field(default=True)
    placeholder_code_uppercase: bool = Field(default=True)
    no_param_control_placeholder_param: str = Field(default="0")
    percent_placeholder_template: str = Field(default="[P_{param}]")
    symbol_placeholder_template: str = Field(default="[S_{index}]")
    simple_control_placeholder_template: str = Field(default="[{code}_{param}]")
    complex_control_placeholder_template: str = Field(default="[RM_{index}]")
    reuse_identical_complex_controls: bool = Field(default=True)
    simple_control_param_pattern: str = Field(default=r"[A-Za-z0-9_]+")
    translation_placeholder_pattern: str = Field(
        default=r"\[[A-Z]+(?:_[^\]]+)?\]",
    )
    japanese_segment_pattern: str = Field(default=r"[\u3040-\u309F\u30A0-\u30FF]+")
    plugin_command_language_filter_pattern: str = Field(default=r"[ぁ-ゖゝ-ゟァ-ヺヽ-ヿ一-鿿]")
    placeholder_pattern: str = Field(default=r"\[[A-Z]+(?:_[^\]]+)?\]")
    residual_escape_sequence_pattern: str = Field(default=r"\\[nrt]")
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
    script_expression_markers: list[str] = Field(default_factory=lambda: ["$data", "$game"])
    script_ternary_markers: list[str] = Field(default_factory=lambda: [" ? ", " : "])
    script_call_required_markers: list[str] = Field(default_factory=lambda: ["$"])
    path_key_ignored_chars: list[str] = Field(default_factory=lambda: ["_", "-"])
    non_content_after_control_pattern: str = Field(
        default=r"[\s\.\,\:\;\-\+\*\/\\\(\)\[\]\{\}<>=!?'\"%]*",
    )


class Setting(StrictBaseModel):
    """项目运行时总配置。"""

    llm: LLMSetting = Field(title="正文模型服务配置")
    translation_context: TranslationContextSetting = Field(title="正文切批配置")
    text_translation: TextTranslationSetting = Field(title="正文翻译配置")
    text_rules: TextRulesSetting = Field(default_factory=TextRulesSetting, title="文本规则")


__all__: list[str] = [
    "LLMSetting",
    "Setting",
    "StrictBaseModel",
    "TextRulesSetting",
    "TextTranslationSetting",
    "TranslationContextSetting",
]
