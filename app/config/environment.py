"""运行环境变量配置适配模块。"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

LLM_BASE_URL_ENV_NAME = "RPG_MAKER_TOOLS_LLM_BASE_URL"
LLM_API_KEY_ENV_NAME = "RPG_MAKER_TOOLS_LLM_API_KEY"


@dataclass(frozen=True, slots=True)
class EnvironmentOverrides:
    """从环境变量读取到的运行配置覆盖值。"""

    llm_base_url: str | None = None
    llm_api_key: str | None = None

    def has_any(self) -> bool:
        """判断当前环境是否提供了覆盖值。"""
        return self.llm_base_url is not None or self.llm_api_key is not None

    def enabled_names(self) -> list[str]:
        """返回已经生效的环境变量名。"""
        names: list[str] = []
        if self.llm_base_url is not None:
            names.append(LLM_BASE_URL_ENV_NAME)
        if self.llm_api_key is not None:
            names.append(LLM_API_KEY_ENV_NAME)
        return names


def load_environment_overrides(
    environ: Mapping[str, str] | None = None,
) -> EnvironmentOverrides:
    """读取模型连接相关环境变量。"""
    source = os.environ if environ is None else environ
    return EnvironmentOverrides(
        llm_base_url=_read_non_empty_env(source, LLM_BASE_URL_ENV_NAME),
        llm_api_key=_read_non_empty_env(source, LLM_API_KEY_ENV_NAME),
    )


def apply_environment_overrides(
    raw_config: dict[str, object],
    overrides: EnvironmentOverrides,
) -> None:
    """把环境变量覆盖值写入原始配置字典。"""
    if not overrides.has_any():
        return

    llm = _read_or_create_section(raw_config, "llm")
    if overrides.llm_base_url is not None:
        llm["base_url"] = overrides.llm_base_url
    if overrides.llm_api_key is not None:
        llm["api_key"] = overrides.llm_api_key


def _read_non_empty_env(source: Mapping[str, str], name: str) -> str | None:
    """读取非空环境变量；空白值按未设置处理。"""
    value = source.get(name)
    if value is None:
        return None
    stripped_value = value.strip()
    if not stripped_value:
        return None
    return stripped_value


def _read_or_create_section(
    raw_config: dict[str, object],
    section_name: str,
) -> dict[str, object]:
    """读取配置段；缺失时创建空配置段等待后续校验。"""
    section = raw_config.get(section_name)
    if section is None:
        new_section: dict[str, object] = {}
        raw_config[section_name] = new_section
        return new_section
    if not isinstance(section, dict):
        raise ValueError(f"配置文件中 {section_name} 必须是表")
    return cast(dict[str, object], section)


__all__: list[str] = [
    "EnvironmentOverrides",
    "LLM_API_KEY_ENV_NAME",
    "LLM_BASE_URL_ENV_NAME",
    "apply_environment_overrides",
    "load_environment_overrides",
]
