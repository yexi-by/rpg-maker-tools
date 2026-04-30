"""
配置加载工具模块。

默认读取项目根目录下的 `setting.toml`，注入正文翻译提示词，并输出
适合排障的中文配置摘要。配置编辑通过直接修改 TOML 完成。
"""

import copy
import tomllib
from pathlib import Path
from typing import cast

from app.config.overrides import SettingOverrides, apply_setting_overrides
from app.config.schemas import Setting
from app.observability.logging import logger

DEFAULT_SETTING_FILE_NAME = "setting.toml"


def resolve_setting_path(setting_path: str | Path | None = None) -> Path:
    """解析 `setting.toml` 的绝对路径。"""
    if setting_path is None:
        return Path(__file__).resolve().parents[2] / DEFAULT_SETTING_FILE_NAME
    return Path(setting_path).resolve()


def load_setting(
    setting_path: str | Path | None = None,
    overrides: SettingOverrides | None = None,
) -> Setting:
    """加载并校验当前配置。"""
    resolved_setting_path = resolve_setting_path(setting_path)
    raw_config = _read_toml_data(resolved_setting_path)
    _inject_prompt_texts(
        raw_config=raw_config,
        base_dir=resolved_setting_path.parent,
        overrides=overrides,
    )
    apply_setting_overrides(raw_config=raw_config, overrides=overrides)
    raw_config_snapshot = copy.deepcopy(raw_config)

    setting = Setting.model_validate(raw_config)
    logger.info(
        _build_setting_summary(
            setting=setting,
            setting_path=resolved_setting_path,
            raw_config=raw_config_snapshot,
            overrides=overrides,
        )
    )
    return setting


def _read_toml_data(setting_path: Path) -> dict[str, object]:
    """读取原始 TOML 数据。"""
    if not setting_path.exists():
        logger.error(
            f"[tag.failure]配置文件未找到[/tag.failure] [tag.path]{setting_path}[/tag.path]"
        )
        raise FileNotFoundError(f"配置文件未找到: {setting_path}")

    raw_setting = setting_path.read_text(encoding="utf-8-sig")
    return cast(dict[str, object], tomllib.loads(raw_setting))


def _inject_prompt_texts(
    raw_config: dict[str, object],
    base_dir: Path,
    overrides: SettingOverrides | None,
) -> None:
    """把提示词文件内容注入配置字典。"""
    _inject_text_translation_prompt_text(
        raw_config=raw_config,
        base_dir=base_dir,
        overrides=overrides,
    )


def _inject_text_translation_prompt_text(
    raw_config: dict[str, object],
    base_dir: Path,
    overrides: SettingOverrides | None,
) -> None:
    """注入正文翻译提示词文本。"""
    text_translation = _read_config_section(raw_config, "text_translation")
    if overrides is not None and overrides.text_translation_system_prompt is not None:
        text_translation["system_prompt_file"] = "<cli>"
        text_translation["system_prompt"] = overrides.text_translation_system_prompt
        return

    prompt_file = text_translation.get("system_prompt_file")
    if not isinstance(prompt_file, str) or not prompt_file.strip():
        raise ValueError("配置文件中缺少 text_translation.system_prompt_file 配置项")

    text_translation["system_prompt"] = _read_prompt_text(base_dir, prompt_file)


def _read_config_section(raw_config: dict[str, object], section_name: str) -> dict[str, object]:
    """读取并收窄顶层配置段。"""
    section = raw_config.get(section_name)
    if not isinstance(section, dict):
        raise ValueError(f"配置文件中缺少 {section_name} 配置段")
    return cast(dict[str, object], section)


def _read_prompt_text(base_dir: Path, prompt_file: str) -> str:
    """读取提示词文件文本。"""
    prompt_path = Path(prompt_file)
    if not prompt_path.is_absolute():
        prompt_path = base_dir / prompt_path

    if not prompt_path.exists():
        raise FileNotFoundError(f"提示词文件未找到: {prompt_path}")

    return prompt_path.read_text(encoding="utf-8")


def _build_setting_summary(
    *,
    setting: Setting,
    setting_path: Path,
    raw_config: dict[str, object],
    overrides: SettingOverrides | None,
) -> str:
    """构造适合直接输出到日志的配置摘要。"""
    text_service = setting.llm
    text_prompt_file = _read_prompt_file_name(raw_config=raw_config, section_path=["text_translation"])

    lines = [
        "[tag.phase]当前正在使用的配置[/tag.phase]",
        f"配置文件: [tag.path]{setting_path}[/tag.path]",
        f"正文接口: OpenAI 兼容 / 模型 [tag.count]{text_service.model}[/tag.count] / 地址 [tag.path]{text_service.base_url}[/tag.path] / 超时 [tag.count]{text_service.timeout}[/tag.count] 秒",
        f"正文切块: 目标 [tag.count]{setting.translation_context.token_size}[/tag.count] token，换算系数 [tag.count]{setting.translation_context.factor}[/tag.count]，同角色最多连续 [tag.count]{setting.translation_context.max_command_items}[/tag.count] 条",
        f"正文翻译: [tag.count]{setting.text_translation.worker_count}[/tag.count] 个 worker，RPM [tag.count]{setting.text_translation.rpm or '不限'}[/tag.count]，失败重试 [tag.count]{setting.text_translation.retry_count}[/tag.count] 次，间隔 [tag.count]{setting.text_translation.retry_delay}[/tag.count] 秒",
        f"事件指令参数: 默认导出编码 [tag.count]{', '.join(map(str, setting.event_command_text.default_command_codes))}[/tag.count]",
        f"写回字体: [tag.path]{setting.write_back.replacement_font_path or '未配置'}[/tag.path]",
        f"文本规则: 行切分标点 [tag.count]{len(setting.text_rules.line_split_punctuations)}[/tag.count] 个，长文本宽度 [tag.count]{setting.text_rules.long_text_line_width_limit}[/tag.count]，包裹标点 [tag.count]{len(setting.text_rules.strip_wrapping_punctuation_pairs)}[/tag.count] 组",
        f"提示词文件: 正文=[tag.path]{text_prompt_file}[/tag.path]",
    ]
    if overrides is not None and overrides.has_any():
        lines.append("CLI 覆盖: 已应用本次命令传入的配置值")
    return "\n".join(lines)


def _read_prompt_file_name(
    *,
    raw_config: dict[str, object],
    section_path: list[str],
    prompt_key: str = "system_prompt_file",
) -> str:
    """从原始配置里读取提示词文件名。"""
    current_map = raw_config
    for key in section_path:
        next_value = current_map.get(key)
        if not isinstance(next_value, dict):
            return "未配置"
        current_map = cast(dict[str, object], next_value)

    prompt_file = current_map.get(prompt_key)
    if not isinstance(prompt_file, str) or not prompt_file.strip():
        return "未配置"
    return prompt_file


__all__: list[str] = [
    "DEFAULT_SETTING_FILE_NAME",
    "load_setting",
    "resolve_setting_path",
]
