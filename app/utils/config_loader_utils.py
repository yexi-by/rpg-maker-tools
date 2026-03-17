"""
配置加载工具模块。

本模块服务于当前多游戏主线，默认读取项目根目录下的 `setting.toml`。
这里不再解析或依赖 `[project]` 配置段，
只负责提示词注入、最终校验以及输出一份便于排查的配置摘要。
"""

import copy
import tomllib
from pathlib import Path
from typing import Any

import tomlkit
from app.config.schemas import Setting
from app.utils.log_utils import logger
from tomlkit.toml_document import TOMLDocument

DEFAULT_SETTING_FILE_NAME: str = "setting.toml"


def resolve_setting_path(setting_path: str | Path | None = None) -> Path:
    """
    解析 `setting.toml` 的绝对路径。

    Args:
        setting_path: 用户显式传入的配置文件路径；为空时使用项目根目录默认文件。

    Returns:
        最终生效的绝对配置文件路径。
    """
    if setting_path is None:
        return Path(__file__).resolve().parents[2] / DEFAULT_SETTING_FILE_NAME
    return Path(setting_path).resolve()


def load_setting(setting_path: str | Path | None = None) -> Setting:
    """
    加载并校验当前主线配置。

    Args:
        setting_path: 可选的配置文件路径。

    Returns:
        完成提示词注入后的最终运行时配置对象。
    """
    resolved_setting_path = resolve_setting_path(setting_path)
    raw_config = _read_toml_data(resolved_setting_path)
    raw_config_snapshot = copy.deepcopy(raw_config)
    _inject_prompt_texts(raw_config=raw_config, base_dir=resolved_setting_path.parent)

    setting = Setting.model_validate(raw_config)
    logger.info(
        _build_setting_summary(
            setting=setting,
            setting_path=resolved_setting_path,
            raw_config=raw_config_snapshot,
        )
    )
    return setting


def load_setting_document(setting_path: str | Path | None = None) -> TOMLDocument:
    """
    读取原始 `setting.toml` 文档对象。

    Args:
        setting_path: 可选的配置文件路径。

    Returns:
        保留原始结构与注释的 TOML 文档对象。
    """
    resolved_setting_path = resolve_setting_path(setting_path)
    if not resolved_setting_path.exists():
        logger.error(
            f"[tag.failure]配置文件未找到[/tag.failure] [tag.path]{resolved_setting_path}[/tag.path]"
        )
        raise FileNotFoundError(f"配置文件未找到: {resolved_setting_path}")

    raw_setting = resolved_setting_path.read_text(encoding="utf-8-sig")
    return tomlkit.parse(raw_setting)


def validate_setting_document(
    document: TOMLDocument,
    setting_path: str | Path | None = None,
) -> Setting:
    """
    校验原始 TOML 文档是否能够转成运行时配置。

    Args:
        document: 待校验的 TOML 文档对象。
        setting_path: 可选的配置文件路径，用于解析相对提示词路径。

    Returns:
        校验通过后的运行时配置对象。
    """
    resolved_setting_path = resolve_setting_path(setting_path)
    raw_config = _document_to_raw_config(document)
    _inject_prompt_texts(raw_config=raw_config, base_dir=resolved_setting_path.parent)
    return Setting.model_validate(raw_config)


def save_setting_value(
    field_path: tuple[str, ...],
    value: str | int | float,
    setting_path: str | Path | None = None,
) -> Setting:
    """
    修改单个配置字段并在校验通过后原子写回文件。

    Args:
        field_path: 目标字段路径，例如 `(\"text_translation\", \"worker_count\")`。
        value: 待写入的新值。
        setting_path: 可选的配置文件路径。

    Returns:
        写回后重新校验得到的运行时配置对象。
    """
    resolved_setting_path = resolve_setting_path(setting_path)
    document = load_setting_document(resolved_setting_path)
    _update_document_value(document=document, field_path=field_path, value=value)
    setting = validate_setting_document(document, resolved_setting_path)
    _write_setting_document(document=document, setting_path=resolved_setting_path)
    return setting


def _read_toml_data(setting_path: Path) -> dict[str, Any]:
    """
    读取原始 TOML 数据。

    Args:
        setting_path: 待读取的配置文件路径。

    Returns:
        TOML 反序列化后的原始字典。
    """
    if not setting_path.exists():
        logger.error(
            f"[tag.failure]配置文件未找到[/tag.failure] [tag.path]{setting_path}[/tag.path]"
        )
        raise FileNotFoundError(f"配置文件未找到: {setting_path}")

    raw_setting = setting_path.read_text(encoding="utf-8-sig")
    return tomllib.loads(raw_setting)


def _document_to_raw_config(document: TOMLDocument) -> dict[str, Any]:
    """
    把 TOML 文档对象转换为普通字典。

    Args:
        document: 已解析的 TOML 文档。

    Returns:
        适合继续做提示词注入和 Pydantic 校验的普通字典。
    """
    return tomllib.loads(tomlkit.dumps(document))


def _update_document_value(
    *,
    document: TOMLDocument,
    field_path: tuple[str, ...],
    value: str | int | float,
) -> None:
    """
    按路径更新 TOML 文档里的单个字段。

    Args:
        document: 待修改的 TOML 文档。
        field_path: 目标字段路径。
        value: 待写入的新值。
    """
    current: Any = document
    for key in field_path[:-1]:
        if key not in current:
            raise KeyError(f"配置路径不存在: {'.'.join(field_path)}")
        current = current[key]

    current[field_path[-1]] = value


def _write_setting_document(document: TOMLDocument, setting_path: Path) -> None:
    """
    以原子方式写回 TOML 文档。

    Args:
        document: 待写回的 TOML 文档。
        setting_path: 目标配置文件路径。
    """
    raw_setting = tomlkit.dumps(document)
    temp_path = setting_path.with_suffix(f"{setting_path.suffix}.tmp")
    temp_path.write_text(raw_setting, encoding="utf-8")
    temp_path.replace(setting_path)


def _inject_prompt_texts(raw_config: dict[str, Any], base_dir: Path) -> None:
    """
    把提示词文件内容注入配置字典。

    Args:
        raw_config: TOML 原始字典。
        base_dir: 配置文件所在目录，用于解析相对提示词路径。
    """
    _inject_glossary_prompt_texts(raw_config=raw_config, base_dir=base_dir)
    _inject_text_translation_prompt_text(raw_config=raw_config, base_dir=base_dir)
    _inject_error_translation_prompt_text(raw_config=raw_config, base_dir=base_dir)


def _inject_glossary_prompt_texts(raw_config: dict[str, Any], base_dir: Path) -> None:
    """
    注入术语翻译提示词文本。

    Args:
        raw_config: TOML 原始字典。
        base_dir: 配置文件所在目录。
    """
    glossary_translation = raw_config.get("glossary_translation")
    if not isinstance(glossary_translation, dict):
        raise ValueError("配置文件中缺少 glossary_translation 配置段")

    for task_name in ("role_name", "display_name"):
        task_config = glossary_translation.get(task_name)
        if not isinstance(task_config, dict):
            raise ValueError(
                f"配置文件中缺少 glossary_translation.{task_name} 配置段"
            )

        prompt_file = task_config.get("system_prompt_file")
        if not isinstance(prompt_file, str) or not prompt_file.strip():
            raise ValueError(
                f"配置文件中缺少 glossary_translation.{task_name}.system_prompt_file 配置项"
            )

        task_config["system_prompt"] = _read_prompt_text(base_dir, prompt_file)



def _inject_text_translation_prompt_text(
    raw_config: dict[str, Any],
    base_dir: Path,
) -> None:
    """
    注入正文翻译提示词文本。

    Args:
        raw_config: TOML 原始字典。
        base_dir: 配置文件所在目录。
    """
    text_translation = raw_config.get("text_translation")
    if not isinstance(text_translation, dict):
        raise ValueError("配置文件中缺少 text_translation 配置段")

    prompt_file = text_translation.get("system_prompt_file")
    if not isinstance(prompt_file, str) or not prompt_file.strip():
        raise ValueError("配置文件中缺少 text_translation.system_prompt_file 配置项")

    text_translation["system_prompt"] = _read_prompt_text(base_dir, prompt_file)


def _inject_error_translation_prompt_text(
    raw_config: dict[str, Any],
    base_dir: Path,
) -> None:
    """
    注入错误重翻提示词文本。

    Args:
        raw_config: TOML 原始字典。
        base_dir: 配置文件所在目录。
    """
    error_translation = raw_config.get("error_translation")
    if not isinstance(error_translation, dict):
        raise ValueError("配置文件中缺少 error_translation 配置段")

    prompt_file = error_translation.get("system_prompt_file")
    if not isinstance(prompt_file, str) or not prompt_file.strip():
        raise ValueError("配置文件中缺少 error_translation.system_prompt_file 配置项")

    error_translation["system_prompt"] = _read_prompt_text(base_dir, prompt_file)


def _read_prompt_text(base_dir: Path, prompt_file: str) -> str:
    """
    读取提示词文件文本。

    Args:
        base_dir: 配置文件所在目录。
        prompt_file: 提示词文件路径，可为相对或绝对路径。

    Returns:
        读取到的完整提示词文本。
    """
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
    raw_config: dict[str, Any],
) -> str:
    """
    构造适合直接输出到日志的配置摘要。

    Args:
        setting: 已完成校验的配置对象。
        setting_path: 实际生效的配置文件路径。
        raw_config: 注入提示词前的原始 TOML 字典。

    Returns:
        多行文本形式的配置摘要。
    """
    glossary_service = setting.llm_services.glossary
    text_service = setting.llm_services.text

    role_prompt_file = _read_prompt_file_name(
        raw_config=raw_config,
        section_path=["glossary_translation", "role_name"],
    )
    display_prompt_file = _read_prompt_file_name(
        raw_config=raw_config,
        section_path=["glossary_translation", "display_name"],
    )
    text_prompt_file = _read_prompt_file_name(
        raw_config=raw_config,
        section_path=["text_translation"],
    )
    error_prompt_file = _read_prompt_file_name(
        raw_config=raw_config,
        section_path=["error_translation"],
    )

    lines = [
        "[tag.phase]当前正在使用的配置[/tag.phase]",
        f"配置文件: [tag.path]{setting_path}[/tag.path]",
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
            f"每块 [tag.count]{setting.glossary_extraction.role_chunk_lines}[/tag.count] 行"
        ),
        (
            "术语翻译: "
            f"[tag.count]{setting.glossary_translation.worker_count}[/tag.count] 个 worker，"
            f"RPM [tag.count]{setting.glossary_translation.rpm or '不限'}[/tag.count]，"
            f"地点名每批 [tag.count]{setting.glossary_translation.display_name.chunk_size}[/tag.count] 条"
        ),
        (
            "正文切块: "
            f"目标 [tag.count]{setting.translation_context.token_size}[/tag.count] token，"
            f"换算系数 [tag.count]{setting.translation_context.factor}[/tag.count]，"
            f"同角色最多连续 [tag.count]{setting.translation_context.max_command_items}[/tag.count] 条"
        ),
        (
            "正文翻译: "
            f"[tag.count]{setting.text_translation.worker_count}[/tag.count] 个 worker，"
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
    prompt_key: str = "system_prompt_file",
) -> str:
    """
    从原始配置里读取提示词文件名。

    Args:
        raw_config: 原始 TOML 字典。
        section_path: 目标配置段路径。

    Returns:
        对应的提示词文件名，缺失时返回“未配置”。
    """
    current: Any = raw_config
    for key in section_path:
        if not isinstance(current, dict):
            return "未配置"
        current = current.get(key)

    if not isinstance(current, dict):
        return "未配置"

    prompt_file = current.get(prompt_key)
    if not isinstance(prompt_file, str) or not prompt_file.strip():
        return "未配置"
    return prompt_file


def _describe_provider(provider_type: str) -> str:
    """
    将服务提供商类型转成中文可读文本。

    Args:
        provider_type: 原始提供商类型值。

    Returns:
        用于日志展示的中文文本。
    """
    if provider_type == "openai":
        return "OpenAI 兼容接口"
    if provider_type == "gemini":
        return "Gemini 接口"
    if provider_type == "volcengine":
        return "火山引擎接口"
    return provider_type


__all__: list[str] = [
    "DEFAULT_SETTING_FILE_NAME",
    "load_setting",
    "load_setting_document",
    "resolve_setting_path",
    "save_setting_value",
    "validate_setting_document",
]
