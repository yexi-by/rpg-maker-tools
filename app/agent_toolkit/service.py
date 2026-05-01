"""Agent 自主流程诊断与质量报告服务。"""

from __future__ import annotations

import platform
from collections import Counter
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.agent_toolkit.placeholder_scan import (
    count_uncovered_candidates,
    placeholder_candidates_to_details,
    scan_placeholder_candidates,
)
from app.agent_toolkit.reports import AgentIssue, AgentReport, issue
from app.config import load_custom_placeholder_rules, load_custom_placeholder_rules_text
from app.config.environment import load_environment_overrides
from app.llm import ChatMessage, LLMHandler
from app.persistence import DEFAULT_ERROR_TABLE_PREFIX, GameRegistry, TargetGameSession
from app.plugin_text import PluginTextExtraction
from app.rmmz import DataTextExtraction
from app.rmmz.schema import GameData, TranslationData, TranslationItem
from app.rmmz.text_rules import JsonArray, JsonObject, JsonValue, TextRules
from app.rmmz.control_codes import ControlSequenceSpan
from app.translation.line_wrap import count_line_width_chars
from app.utils.config_loader_utils import load_setting, resolve_setting_path
from app.event_command_text import EventCommandTextExtraction

type LlmCheckFunc = Callable[[LLMHandler, str], Awaitable[None]]


async def run_default_llm_check(llm_handler: LLMHandler, model: str) -> None:
    """执行一次轻量模型连通性检查。"""
    _ = await llm_handler.get_ai_response(
        messages=[
            ChatMessage(role="system", text="你只需要回复 OK。"),
            ChatMessage(role="user", text="OK"),
        ],
        model=model,
        temperature=0,
    )


class AgentToolkitService:
    """面向外部 Agent 的只读诊断与报告服务。"""

    def __init__(
        self,
        *,
        game_registry: GameRegistry | None = None,
        llm_handler: LLMHandler | None = None,
        llm_check: LlmCheckFunc = run_default_llm_check,
        setting_path: str | Path | None = None,
    ) -> None:
        """初始化服务依赖。"""
        self.game_registry: GameRegistry = game_registry or GameRegistry()
        self.llm_handler: LLMHandler = llm_handler or LLMHandler()
        self.llm_check: LlmCheckFunc = llm_check
        self.setting_path: str | Path | None = setting_path

    async def doctor(self, *, game_title: str | None, check_llm: bool) -> AgentReport:
        """检查项目配置、模型连接和可选目标游戏状态。"""
        errors: list[AgentIssue] = []
        warnings: list[AgentIssue] = []
        summary: JsonObject = {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "setting_path": str(resolve_setting_path(self.setting_path)),
        }
        details: JsonObject = {
            "environment_overrides": [],
            "checks": [],
        }

        python_major, python_minor = _current_python_major_minor()
        if (python_major, python_minor) < (3, 14):
            errors.append(issue("python_version", "当前 Python 版本低于项目要求的 3.14"))
        else:
            _append_check(details, "python_version", "ok")

        try:
            setting = load_setting(self.setting_path)
            _append_check(details, "setting", "ok")
            summary["llm_model"] = setting.llm.model
            environment_overrides = load_environment_overrides()
            enabled_names: list[JsonValue] = list(environment_overrides.enabled_names())
            details["environment_overrides"] = enabled_names
            if not setting.llm.base_url.strip():
                errors.append(issue("llm_base_url", "模型服务地址为空"))
            if not setting.llm.api_key.strip():
                errors.append(issue("llm_api_key", "模型 API Key 为空"))
            if check_llm:
                try:
                    self.llm_handler.configure(
                        base_url=setting.llm.base_url,
                        api_key=setting.llm.api_key,
                        timeout=setting.llm.timeout,
                    )
                    await self.llm_check(self.llm_handler, setting.llm.model)
                    _append_check(details, "llm", "ok")
                except Exception as error:
                    errors.append(issue("llm", f"模型连通性检查失败: {type(error).__name__}: {error}"))
            else:
                warnings.append(issue("llm_skipped", "已跳过模型连通性检查"))
        except Exception as error:
            errors.append(issue("setting", f"配置加载失败: {type(error).__name__}: {error}"))
            setting = None

        self._check_static_paths(errors=errors, warnings=warnings, details=details)

        if game_title is not None:
            await self._check_game(
                game_title=game_title,
                setting_available=setting is not None,
                errors=errors,
                warnings=warnings,
                summary=summary,
                details=details,
            )

        return AgentReport.from_parts(
            errors=errors,
            warnings=warnings,
            summary=summary,
            details=details,
        )

    async def scan_placeholder_candidates(
        self,
        *,
        game_title: str,
        custom_placeholder_rules_text: str | None,
    ) -> AgentReport:
        """扫描目标游戏中疑似需要自定义保护的控制符。"""
        custom_rules = (
            load_custom_placeholder_rules()
            if custom_placeholder_rules_text is None
            else load_custom_placeholder_rules_text(custom_placeholder_rules_text)
        )
        setting = load_setting(self.setting_path)
        text_rules = TextRules.from_setting(
            setting.text_rules,
            custom_placeholder_rules=custom_rules,
        )
        async with await self.game_registry.open_game(game_title) as session:
            game_data = await self._load_game_data(session)

        candidates = scan_placeholder_candidates(game_data, text_rules)
        uncovered_count = count_uncovered_candidates(candidates)
        warnings: list[AgentIssue] = []
        if uncovered_count:
            warnings.append(issue("uncovered_placeholder", f"发现 {uncovered_count} 个未覆盖的疑似自定义控制符"))

        return AgentReport.from_parts(
            errors=[],
            warnings=warnings,
            summary={
                "candidate_count": len(candidates),
                "uncovered_count": uncovered_count,
                "custom_rule_count": len(custom_rules),
            },
            details={
                "candidates": placeholder_candidates_to_details(candidates),
            },
        )

    async def quality_report(self, *, game_title: str) -> AgentReport:
        """生成目标游戏当前翻译状态和质量风险报告。"""
        setting = load_setting(self.setting_path)
        text_rules = TextRules.from_setting(
            setting.text_rules,
            custom_placeholder_rules=load_custom_placeholder_rules(),
        )
        errors: list[AgentIssue] = []
        warnings: list[AgentIssue] = []
        async with await self.game_registry.open_game(game_title) as session:
            game_data = await self._load_game_data(session)
            plugin_rules = await session.read_plugin_text_rules()
            event_rules = await session.read_event_command_text_rules()
            name_registry = await session.read_name_context_registry()
            translation_data_map = DataTextExtraction(game_data, text_rules).extract_all_text()
            _merge_translation_data_map(
                translation_data_map,
                EventCommandTextExtraction(game_data, event_rules, text_rules).extract_all_text(),
            )
            _merge_translation_data_map(
                translation_data_map,
                PluginTextExtraction(game_data, plugin_rules, text_rules).extract_all_text(),
            )
            active_paths = {
                item.location_path
                for translation_data in translation_data_map.values()
                for item in translation_data.translation_items
            }
            translated_items = await session.read_translated_items()
            translated_paths = {item.location_path for item in translated_items}
            stale_paths = translated_paths - active_paths
            latest_error_table, error_rows = await _read_latest_error_rows(session)

        residual_count = _count_residual_items(translated_items, text_rules)
        placeholder_risk_count = _count_placeholder_risk_items(translated_items, text_rules)
        overwide_line_count = _count_overwide_lines(translated_items, text_rules)
        error_type_counts = _count_error_types(error_rows)
        model_response_count = sum(1 for row in error_rows if _row_has_model_response(row))
        filled_name_count = 0
        total_name_count = 0
        if name_registry is not None:
            total_name_count = len(name_registry.speaker_names) + len(name_registry.map_display_names)
            filled_name_count = sum(
                1
                for value in [*name_registry.speaker_names.values(), *name_registry.map_display_names.values()]
                if value.strip()
            )

        if error_rows:
            errors.append(issue("translation_errors", f"最新错误表存在 {len(error_rows)} 条错误记录"))
        if placeholder_risk_count:
            errors.append(issue("placeholder_risk", f"发现 {placeholder_risk_count} 条占位符风险译文"))
        if residual_count:
            warnings.append(issue("japanese_residual", f"发现 {residual_count} 条译文存在日文残留风险"))
        if overwide_line_count:
            warnings.append(issue("overwide_line", f"发现 {overwide_line_count} 行译文超过当前长文本宽度上限"))
        if stale_paths:
            warnings.append(issue("stale_cache", f"发现 {len(stale_paths)} 条不在当前提取范围内的缓存译文"))

        return AgentReport.from_parts(
            errors=errors,
            warnings=warnings,
            summary={
                "extractable_count": len(active_paths),
                "translated_count": len(translated_paths & active_paths),
                "pending_count": len(active_paths - translated_paths),
                "stale_cache_count": len(stale_paths),
                "plugin_rule_count": sum(len(rule.path_templates) for rule in plugin_rules),
                "event_command_rule_count": sum(len(rule.path_templates) for rule in event_rules),
                "name_context_total_count": total_name_count,
                "name_context_filled_count": filled_name_count,
                "latest_error_table": latest_error_table or "",
                "latest_error_count": len(error_rows),
                "model_response_error_count": model_response_count,
                "japanese_residual_count": residual_count,
                "placeholder_risk_count": placeholder_risk_count,
                "overwide_line_count": overwide_line_count,
                "writable_translation_count": len(translated_paths & active_paths),
            },
            details={
                "error_type_counts": dict(error_type_counts),
            },
        )

    async def _check_game(
        self,
        *,
        game_title: str,
        setting_available: bool,
        errors: list[AgentIssue],
        warnings: list[AgentIssue],
        summary: JsonObject,
        details: JsonObject,
    ) -> None:
        """检查目标游戏数据库、文件和导入状态。"""
        _ = details
        if not setting_available:
            warnings.append(issue("game_skipped", "配置不可用，已跳过目标游戏深度检查"))
            return
        try:
            setting = load_setting(self.setting_path)
            text_rules = TextRules.from_setting(
                setting.text_rules,
                custom_placeholder_rules=load_custom_placeholder_rules(),
            )
            async with await self.game_registry.open_game(game_title) as session:
                game_data = await self._load_game_data(session)
                plugin_rules = await session.read_plugin_text_rules()
                event_rules = await session.read_event_command_text_rules()
                name_registry = await session.read_name_context_registry()
                summary["game_registered"] = True
                summary["plugin_rule_count"] = sum(len(rule.path_templates) for rule in plugin_rules)
                summary["event_command_rule_count"] = sum(len(rule.path_templates) for rule in event_rules)
                summary["name_context_imported"] = name_registry is not None
                if not plugin_rules:
                    warnings.append(issue("plugin_rules", "当前游戏尚未导入插件文本规则"))
                if not event_rules:
                    warnings.append(issue("event_command_rules", "当前游戏尚未导入事件指令文本规则"))
                if name_registry is None:
                    warnings.append(issue("name_context", "当前游戏尚未导入术语表"))
                font_path = setting.write_back.replacement_font_path
                if font_path is not None and not Path(font_path).exists():
                    warnings.append(issue("replacement_font", "配置的替换字体文件不存在"))
                candidates = scan_placeholder_candidates(game_data, text_rules)
                uncovered_count = count_uncovered_candidates(candidates)
                summary["uncovered_placeholder_count"] = uncovered_count
                if uncovered_count:
                    warnings.append(issue("uncovered_placeholder", f"存在 {uncovered_count} 个未覆盖的疑似自定义控制符"))
        except Exception as error:
            errors.append(issue("game", f"目标游戏检查失败: {type(error).__name__}: {error}"))

    def _check_static_paths(
        self,
        *,
        errors: list[AgentIssue],
        warnings: list[AgentIssue],
        details: JsonObject,
    ) -> None:
        """检查项目固定目录和自定义占位符规则文件。"""
        _ = warnings
        db_dir = self.game_registry.db_directory
        if not db_dir.exists():
            errors.append(issue("db_dir", "数据库目录不存在"))
        else:
            _append_check(details, "db_dir", "ok")
        if not Path("logs").exists():
            Path("logs").mkdir(exist_ok=True)
        try:
            _ = load_custom_placeholder_rules()
            _append_check(details, "custom_placeholder_rules", "ok")
        except Exception as error:
            errors.append(issue("custom_placeholder_rules", f"自定义占位符规则不可读: {type(error).__name__}: {error}"))

    async def _load_game_data(self, session: TargetGameSession) -> GameData:
        """加载单游戏数据并绑定到会话。"""
        from app.rmmz.loader import load_game_data

        game_data = await load_game_data(session.game_path)
        session.set_game_data(game_data)
        return game_data


def _append_check(details: JsonObject, name: str, status: str) -> None:
    """把检查项追加到报告明细。"""
    checks_value = details.get("checks")
    if isinstance(checks_value, list):
        checks: JsonArray = checks_value
    else:
        checks = []
        details["checks"] = checks
    check_item: JsonObject = {"name": name, "status": status}
    checks.append(check_item)


def _current_python_major_minor() -> tuple[int, int]:
    """读取当前 Python 主次版本号。"""
    version_parts = platform.python_version_tuple()
    return int(version_parts[0]), int(version_parts[1])


def _merge_translation_data_map(
    target: dict[str, TranslationData],
    source: dict[str, TranslationData],
) -> None:
    """合并两个文件维度翻译数据映射。"""
    for file_name, translation_data in source.items():
        existing_data = target.get(file_name)
        if existing_data is not None:
            existing_data.translation_items.extend(translation_data.translation_items)
        else:
            target[file_name] = translation_data


async def _read_latest_error_rows(session: TargetGameSession) -> tuple[str | None, list[dict[str, object]]]:
    """读取最新错误表及其所有行。"""
    table_names = await session.read_error_table_names(DEFAULT_ERROR_TABLE_PREFIX)
    if not table_names:
        return None, []
    latest_table_name = table_names[-1]
    return latest_table_name, await session.read_table(latest_table_name)


def _count_error_types(error_rows: list[dict[str, object]]) -> Counter[str]:
    """按错误类型统计错误表行。"""
    counter: Counter[str] = Counter()
    for row in error_rows:
        error_type = row.get("error_type")
        if isinstance(error_type, str):
            counter[error_type] += 1
    return counter


def _row_has_model_response(row: dict[str, object]) -> bool:
    """判断错误表行是否包含模型原始返回。"""
    model_response = row.get("model_response")
    return isinstance(model_response, str) and bool(model_response.strip())


def _count_residual_items(items: list[TranslationItem], text_rules: TextRules) -> int:
    """统计存在日文残留风险的译文条目。"""
    count = 0
    for item in items:
        try:
            text_rules.check_japanese_residual(item.translation_lines)
        except ValueError:
            count += 1
    return count


def _count_placeholder_risk_items(items: list[TranslationItem], text_rules: TextRules) -> int:
    """统计占位符数量或未知控制符存在风险的译文条目。"""
    count = 0
    for item in items:
        cloned_item = item.model_copy(deep=True)
        try:
            cloned_item.build_placeholders(text_rules)
            cloned_item.translation_lines_with_placeholders = [
                _mask_translation_controls(line=line, item=cloned_item, text_rules=text_rules)
                for line in cloned_item.translation_lines
            ]
            cloned_item.verify_placeholders(text_rules)
        except ValueError:
            count += 1
    return count


def _mask_translation_controls(*, line: str, item: TranslationItem, text_rules: TextRules) -> str:
    """把译文中的控制符转换成占位符以便复用数量校验。"""
    reverse_map = {original: placeholder for placeholder, original in item.placeholder_map.items()}

    def replacer(span: ControlSequenceSpan) -> str:
        """把已知控制符还原成对应占位符，未知控制符标记为风险。"""
        placeholder = reverse_map.get(span.original)
        if placeholder is not None:
            return placeholder
        return "[CUSTOM_UNEXPECTED_1]"

    return text_rules.replace_rm_control_sequences(line, replacer)


def _count_overwide_lines(items: list[TranslationItem], text_rules: TextRules) -> int:
    """统计超过当前行宽上限的长文本译文行数。"""
    limit = text_rules.setting.long_text_line_width_limit
    count = 0
    for item in items:
        if item.item_type != "long_text":
            continue
        for line in item.translation_lines:
            if line and count_line_width_chars(line, text_rules) > limit:
                count += 1
    return count


__all__: list[str] = [
    "AgentToolkitService",
    "LlmCheckFunc",
    "run_default_llm_check",
]
