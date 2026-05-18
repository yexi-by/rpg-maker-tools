"""源语言档案。"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from app.language import SourceLanguage, SourceTextExclusionProfile


@dataclass(frozen=True, slots=True)
class LanguageProfile:
    """描述某种源语言进入简体中文本地化流程时使用的规则。"""

    source_language: SourceLanguage
    residual_label: str
    prompt_file: str
    source_text_required_pattern: str
    source_text_exclusion_profile: SourceTextExclusionProfile
    source_residual_segment_pattern: str
    source_residual_allowed_chars: tuple[str, ...] = field(default_factory=tuple)
    source_residual_allowed_tail_chars: tuple[str, ...] = field(default_factory=tuple)
    allowed_source_residual_terms: tuple[str, ...] = field(default_factory=tuple)
    source_residual_terms_ignore_case: bool = False


JAPANESE_PROFILE = LanguageProfile(
    source_language="ja",
    residual_label="日文",
    prompt_file="prompts/text_translation_system.md",
    source_text_required_pattern=r"[\u3040-\u309F\u30A0-\u30FF\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]+",
    source_text_exclusion_profile="none",
    source_residual_segment_pattern=r"[\u3040-\u309F\u30A0-\u30FF]+",
    source_residual_allowed_chars=("っ", "ッ", "ー", "・", "。", "～", "…"),
    source_residual_allowed_tail_chars=(
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
    ),
)

ENGLISH_PROFILE = LanguageProfile(
    source_language="en",
    residual_label="英文",
    prompt_file="prompts/text_translation_en_to_zh_system.md",
    source_text_required_pattern=r"[A-Za-z][A-Za-z0-9'’_-]*",
    source_text_exclusion_profile="english_protocol_noise",
    source_residual_segment_pattern=r"[A-Za-z][A-Za-z0-9'’_-]*",
    allowed_source_residual_terms=(
        "HP",
        "MP",
        "TP",
        "AP",
        "JP",
        "EXP",
        "XP",
        "Lv",
        "LV",
        "OK",
    ),
    source_residual_terms_ignore_case=True,
)

LANGUAGE_PROFILES: dict[SourceLanguage, LanguageProfile] = {
    "ja": JAPANESE_PROFILE,
    "en": ENGLISH_PROFILE,
}


def language_profile(source_language: SourceLanguage) -> LanguageProfile:
    """读取指定源语言的语言档案。"""
    return LANGUAGE_PROFILES[source_language]


def apply_language_profile_to_raw_config(
    *,
    raw_config: dict[str, object],
    source_language: SourceLanguage,
) -> None:
    """把语言档案写入原始配置字典，后续 CLI 覆盖仍可继续覆盖这些值。"""
    profile = language_profile(source_language)
    text_translation = _read_or_create_section(raw_config, "text_translation")
    current_prompt_file = text_translation.get("system_prompt_file")
    if (
        not isinstance(current_prompt_file, str)
        or current_prompt_file in {JAPANESE_PROFILE.prompt_file, ENGLISH_PROFILE.prompt_file}
    ):
        text_translation["system_prompt_file"] = profile.prompt_file

    text_rules = _read_or_create_section(raw_config, "text_rules")
    text_rules["source_language"] = profile.source_language
    text_rules["source_residual_label"] = profile.residual_label
    text_rules["source_text_required_pattern"] = profile.source_text_required_pattern
    text_rules["source_text_exclusion_profile"] = profile.source_text_exclusion_profile
    text_rules["source_residual_segment_pattern"] = profile.source_residual_segment_pattern
    text_rules["source_residual_allowed_chars"] = list(profile.source_residual_allowed_chars)
    text_rules["source_residual_allowed_tail_chars"] = list(profile.source_residual_allowed_tail_chars)
    text_rules["allowed_source_residual_terms"] = list(profile.allowed_source_residual_terms)
    text_rules["source_residual_terms_ignore_case"] = profile.source_residual_terms_ignore_case


def resolve_profile_prompt_path(*, base_dir: Path, source_language: SourceLanguage) -> Path:
    """解析语言档案对应的系统提示词路径。"""
    prompt_path = Path(language_profile(source_language).prompt_file)
    if prompt_path.is_absolute():
        return prompt_path
    return base_dir / prompt_path


def _read_or_create_section(raw_config: dict[str, object], section_name: str) -> dict[str, object]:
    """读取配置段；缺失时创建空配置段。"""
    section = raw_config.get(section_name)
    if section is None:
        new_section: dict[str, object] = {}
        raw_config[section_name] = new_section
        return new_section
    if not isinstance(section, dict):
        raise ValueError(f"配置文件中 {section_name} 必须是表")
    return cast(dict[str, object], section)


__all__: list[str] = [
    "LanguageProfile",
    "apply_language_profile_to_raw_config",
    "language_profile",
    "resolve_profile_prompt_path",
]
