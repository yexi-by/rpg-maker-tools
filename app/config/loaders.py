"""
运行配置加载模块。

默认只识别项目根目录下的 `setting.toml`。
本模块负责统一路径解析、提示词正文注入以及最终配置校验。
"""

import copy
import tomllib
from pathlib import Path
from typing import Any

from app.utils.log_utils import logger

from .schemas import Setting

DEFAULT_SETTING_FILE_NAME: str = "setting.toml"


def resolve_setting_path(setting_path: str | Path | None = None) -> Path:
    """
    解析 `setting.toml` 的绝对路径。

    当未显式传入路径时，始终使用项目根目录下的 `setting.toml`。
    """
    if setting_path is None:
        resolved_path: Path = (
            Path(__file__).resolve().parents[2] / DEFAULT_SETTING_FILE_NAME
        )
        return resolved_path

    resolved_path = Path(setting_path).resolve()
    return resolved_path


def load_setting(setting_path: str | Path | None = None) -> Setting:
    """加载并校验运行配置。"""
    resolved_setting_path: Path = resolve_setting_path(setting_path)
    raw_config: dict[str, Any] = _read_toml_data(resolved_setting_path)
    raw_config_snapshot: dict[str, Any] = copy.deepcopy(raw_config)

    _resolve_project_paths(raw_config=raw_config, base_dir=resolved_setting_path.parent)
    _inject_prompt_texts(raw_config=raw_config, base_dir=resolved_setting_path.parent)

    setting: Setting = Setting.model_validate(raw_config)
    setting.project.work_path.mkdir(parents=True, exist_ok=True)
    logger.info(
        _build_setting_summary(
            setting=setting,
            setting_path=resolved_setting_path,
            raw_config=raw_config_snapshot,
        )
    )
    return setting


def _read_toml_data(setting_path: Path) -> dict[str, Any]:
    if not setting_path.exists():
        logger.error(
            f"[tag.failure]配置文件未找到[/tag.failure] [tag.path]{setting_path}[/tag.path]"
        )
        raise FileNotFoundError(f"配置文件未找到: {setting_path}")

    raw_setting: str = setting_path.read_text(encoding="utf-8-sig")
    return tomllib.loads(raw_setting)


def _resolve_project_paths(raw_config: dict[str, Any], base_dir: Path) -> None:
    project_config = raw_config.get("project")
    if not isinstance(project_config, dict):
        raise ValueError("配置文件中缺少 project 配置段")

    work_path = project_config.get("work_path")
    if not isinstance(work_path, str) or not work_path.strip():
        raise ValueError("配置文件中缺少 project.work_path 配置项")

    work_path_obj = Path(work_path)
    if work_path_obj.is_absolute():
        raise ValueError("project.work_path 必须是相对 setting.toml 的路径")

    project_config["work_path"] = str((base_dir / work_path_obj).resolve())


def _inject_prompt_texts(raw_config: dict[str, Any], base_dir: Path) -> None:
    _inject_glossary_prompt_texts(raw_config=raw_config, base_dir=base_dir)
    _inject_text_translation_prompt_text(raw_config=raw_config, base_dir=base_dir)
    _inject_error_translation_prompt_text(raw_config=raw_config, base_dir=base_dir)


def _inject_glossary_prompt_texts(raw_config: dict[str, Any], base_dir: Path) -> None:
    glossary_translation = raw_config.get("glossary_translation")
    if not isinstance(glossary_translation, dict):
        raise ValueError("配置文件中缺少 glossary_translation 配置段")

    for task_name in ("role_name", "display_name"):
        task_config = glossary_translation.get(task_name)
        if not isinstance(task_config, dict):
            raise ValueError(
                f"配置文件中缺少 glossary_translation.{task_name} 配置段"
            )

        prompt_file = task_config.pop("system_prompt_file", None)
        if not isinstance(prompt_file, str) or not prompt_file.strip():
            raise ValueError(
                f"配置文件中缺少 glossary_translation.{task_name}.system_prompt_file 配置项"
            )

        task_config["system_prompt"] = _read_prompt_text(base_dir, prompt_file)


def _inject_text_translation_prompt_text(
    raw_config: dict[str, Any],
    base_dir: Path,
) -> None:
    text_translation = raw_config.get("text_translation")
    if not isinstance(text_translation, dict):
        raise ValueError("配置文件中缺少 text_translation 配置段")

    prompt_file = text_translation.pop("system_prompt_file", None)
    if not isinstance(prompt_file, str) or not prompt_file.strip():
        raise ValueError("配置文件中缺少 text_translation.system_prompt_file 配置项")

    text_translation["system_prompt"] = _read_prompt_text(base_dir, prompt_file)


def _inject_error_translation_prompt_text(
    raw_config: dict[str, Any],
    base_dir: Path,
) -> None:
    error_translation = raw_config.get("error_translation")
    if not isinstance(error_translation, dict):
        raise ValueError("配置文件中缺少 error_translation 配置段")

    prompt_file = error_translation.pop("system_prompt_file", None)
    if not isinstance(prompt_file, str) or not prompt_file.strip():
        raise ValueError("配置文件中缺少 error_translation.system_prompt_file 配置项")

    error_translation["system_prompt"] = _read_prompt_text(base_dir, prompt_file)


def _read_prompt_text(base_dir: Path, prompt_file: str) -> str:
    prompt_path: Path = Path(prompt_file)
    if not prompt_path.is_absolute():
        prompt_path = base_dir / prompt_path

    if not prompt_path.exists():
        raise FileNotFoundError(f"提示词文件未找到: {prompt_path}")

    return prompt_path.read_text(encoding="utf-8")


def _build_setting_summary(
    *,
    setting: Setting,
    setting_path: Path,
    raw_config: dict[str, Any],
) -> str:
    """
    构造“说人话”的当前配置摘要。

    设计意图：
    1. 用户真正关心的是“现在会按什么规则跑”，而不是内部拿到了哪个路径对象。
    2. 这里统一把核心配置翻译成人能快速扫读的摘要，避免终端只剩技术黑话。

    Args:
        setting: 已完成校验的运行时配置对象。
        setting_path: 实际生效的配置文件路径。
        raw_config: 注入提示词前的原始 TOML 字典，用于保留提示词文件名信息。

    Returns:
        适合直接输出到终端 logger 的多行摘要文本。
    """
    glossary_service = setting.llm_services.glossary
    text_service = setting.llm_services.text
    db_path: Path = setting.project.work_path / setting.project.db_name

    role_prompt_file: str = _read_prompt_file_name(
        raw_config=raw_config,
        section_path=["glossary_translation", "role_name"],
    )
    display_prompt_file: str = _read_prompt_file_name(
        raw_config=raw_config,
        section_path=["glossary_translation", "display_name"],
    )
    text_prompt_file: str = _read_prompt_file_name(
        raw_config=raw_config,
        section_path=["text_translation"],
    )
    error_prompt_file: str = _read_prompt_file_name(
        raw_config=raw_config,
        section_path=["error_translation"],
    )

    lines: list[str] = [
        "[tag.phase]当前正在使用的配置[/tag.phase]",
        f"配置文件: [tag.path]{setting_path}[/tag.path]",
        f"游戏目录: [tag.path]{setting.project.file_path}[/tag.path]",
        f"工作目录: [tag.path]{setting.project.work_path}[/tag.path]",
        f"数据库文件: [tag.path]{db_path}[/tag.path]",
        f"译文表名: [tag.count]{setting.project.translation_table_name}[/tag.count]",
        (
            "术语接口: "
            f"{_describe_provider(glossary_service.provider_type)} / "
            f"模型 [tag.count]{glossary_service.model}[/tag.count] / "
            f"地址 [tag.path]{glossary_service.base_url}[/tag.path] / "
            f"超时 [tag.count]{glossary_service.timeout}[/tag.count] 秒"
        ),
        (
            "正文接口: "
            f"{_describe_provider(text_service.provider_type)} / "
            f"模型 [tag.count]{text_service.model}[/tag.count] / "
            f"地址 [tag.path]{text_service.base_url}[/tag.path] / "
            f"超时 [tag.count]{text_service.timeout}[/tag.count] 秒"
        ),
        (
            "术语采样: "
            f"切 [tag.count]{setting.glossary_extraction.role_chunk_blocks}[/tag.count] 块，"
            f"每块取 [tag.count]{setting.glossary_extraction.role_chunk_lines}[/tag.count] 行"
        ),
        (
            "术语翻译: "
            f"角色名每批 [tag.count]{setting.glossary_translation.role_name.chunk_size}[/tag.count] 条，"
            f"地点名每批 [tag.count]{setting.glossary_translation.display_name.chunk_size}[/tag.count] 条"
        ),
        (
            "正文切块: "
            f"目标 [tag.count]{setting.translation_context.token_size}[/tag.count] token，"
            f"估算系数 [tag.count]{setting.translation_context.factor}[/tag.count]，"
            f"同角色最多顺延 [tag.count]{setting.translation_context.max_command_items}[/tag.count] 条"
        ),
        (
            "正文翻译: "
            f"[tag.count]{setting.text_translation.worker_count}[/tag.count] 个并发 worker，"
            f"RPM [tag.count]{setting.text_translation.rpm or '不限'}[/tag.count]，"
            f"失败重试 [tag.count]{setting.text_translation.retry_count}[/tag.count] 次，"
            f"间隔 [tag.count]{setting.text_translation.retry_delay}[/tag.count] 秒"
        ),
        (
            "错误重翻: "
            f"每批 [tag.count]{setting.error_translation.chunk_size}[/tag.count] 条"
        ),
        (
            "提示词文件: "
            f"角色术语=[tag.path]{role_prompt_file}[/tag.path]，"
            f"地点术语=[tag.path]{display_prompt_file}[/tag.path]，"
            f"正文=[tag.path]{text_prompt_file}[/tag.path]，"
            f"错误重翻=[tag.path]{error_prompt_file}[/tag.path]"
        ),
    ]
    return "\n".join(lines)


def _read_prompt_file_name(
    *,
    raw_config: dict[str, Any],
    section_path: list[str],
) -> str:
    """
    从原始配置字典中读取提示词文件名。

    Args:
        raw_config: 原始 TOML 字典。
        section_path: 到目标配置段的路径。

    Returns:
        `system_prompt_file` 的字符串值；缺失时返回“未配置”。
    """
    current: Any = raw_config
    for key in section_path:
        if not isinstance(current, dict):
            return "未配置"
        current = current.get(key)

    if not isinstance(current, dict):
        return "未配置"

    prompt_file = current.get("system_prompt_file")
    if not isinstance(prompt_file, str) or not prompt_file.strip():
        return "未配置"
    return prompt_file


def _describe_provider(provider_type: str) -> str:
    """
    将服务提供商类型转成人类可读文本。

    Args:
        provider_type: 原始 provider_type 值。

    Returns:
        便于终端展示的中文描述。
    """
    if provider_type == "openai":
        return "OpenAI 兼容接口"
    if provider_type == "gemini":
        return "Gemini 接口"
    if provider_type == "volcengine":
        return "火山引擎接口"
    return provider_type


__all__: list[str] = ["DEFAULT_SETTING_FILE_NAME", "load_setting", "resolve_setting_path"]
