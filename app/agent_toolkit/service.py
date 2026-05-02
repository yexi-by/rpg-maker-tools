"""Agent 自主流程诊断与质量报告服务。"""

from __future__ import annotations

import platform
import json
import re
import shutil
import sys
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Sequence
from pathlib import Path
from typing import cast

import aiofiles

from app.agent_toolkit.placeholder_scan import (
    PlaceholderCandidate,
    count_uncovered_candidates,
    placeholder_candidates_to_details,
    scan_placeholder_candidates,
)
from app.agent_toolkit.reports import AgentIssue, AgentReport, issue
from app.application.file_writer import reset_writable_copies
from app.config import load_custom_placeholder_rules_text
from app.config.environment import load_environment_overrides
from app.japanese_residual import (
    JapaneseResidualRuleSet,
    build_japanese_residual_rule_records_from_import,
    check_japanese_residual_for_item,
    parse_japanese_residual_rule_import_text,
)
from app.llm import ChatMessage, LLMHandler
from app.persistence import GameRegistry, TargetGameSession, ensure_db_directory
from app.plugin_text import (
    PluginTextExtraction,
    build_plugin_hash,
    build_plugin_rule_records_from_import,
    export_plugins_json_file,
    parse_plugin_rule_import_text,
)
from app.rmmz import DataTextExtraction
from app.rmmz.control_codes import CustomPlaceholderRule
from app.rmmz.schema import (
    GameData,
    JapaneseResidualRuleRecord,
    LlmFailureRecord,
    PLUGINS_FILE_NAME,
    PluginTextRuleRecord,
    TranslationData,
    TranslationErrorItem,
    TranslationItem,
)
from app.rmmz.text_rules import JsonArray, JsonObject, JsonValue, TextRules
from app.rmmz.json_types import coerce_json_value, ensure_json_array, ensure_json_object, ensure_json_string_list
from app.rmmz.control_codes import ControlSequenceSpan
from app.rmmz.write_back import write_data_text
from app.translation.line_wrap import count_line_width_chars, split_overwide_lines
from app.utils.config_loader_utils import load_setting, resolve_setting_path
from app.event_command_text import (
    EventCommandTextExtraction,
    build_event_command_rule_records_from_import,
    export_event_commands_json_file,
    parse_event_command_rule_import_text,
    resolve_event_command_codes,
)
from app.name_context import export_name_context_artifacts, load_name_context_registry

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
        setting = load_setting(self.setting_path)
        async with await self.game_registry.open_game(game_title) as session:
            game_data = await self._load_game_data(session)
            custom_rules = await self._resolve_custom_rules(
                session=session,
                custom_placeholder_rules_text=custom_placeholder_rules_text,
            )
        text_rules = TextRules.from_setting(
            setting.text_rules,
            custom_placeholder_rules=custom_rules,
        )

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

    async def validate_placeholder_rules(
        self,
        *,
        game_title: str | None,
        custom_placeholder_rules_text: str | None,
        sample_texts: Sequence[str],
    ) -> AgentReport:
        """校验自定义占位符规则，并预览样本文本的替换与还原结果。"""
        errors: list[AgentIssue] = []
        warnings: list[AgentIssue] = []
        source_label = "--placeholder-rules"
        if custom_placeholder_rules_text is None and game_title is not None:
            source_label = "当前游戏数据库"
        elif custom_placeholder_rules_text is None:
            source_label = "空规则"

        try:
            if game_title is not None:
                async with await self.game_registry.open_game(game_title) as session:
                    custom_rules = await self._resolve_custom_rules(
                        session=session,
                        custom_placeholder_rules_text=custom_placeholder_rules_text,
                    )
                    if not sample_texts:
                        game_data = await self._load_game_data(session)
                        setting = load_setting(self.setting_path)
                        preview_rules = TextRules.from_setting(
                            setting.text_rules,
                            custom_placeholder_rules=custom_rules,
                        )
                        sample_texts = _collect_placeholder_preview_samples(game_data, preview_rules)
                        if not sample_texts:
                            sample_texts = _collect_unprotected_control_warning_samples(game_data, preview_rules)
            elif custom_placeholder_rules_text is None:
                custom_rules = ()
            else:
                custom_rules = load_custom_placeholder_rules_text(custom_placeholder_rules_text)
        except Exception as error:
            return AgentReport.from_parts(
                errors=[
                    issue(
                        "placeholder_rules_invalid",
                        f"自定义占位符规则不可用: {type(error).__name__}: {error}",
                    )
                ],
                warnings=[],
                summary={
                    "source": source_label,
                    "rule_count": 0,
                    "sample_count": len(sample_texts),
                },
                details={},
            )

        try:
            setting = load_setting(self.setting_path)
            text_rules = TextRules.from_setting(
                setting.text_rules,
                custom_placeholder_rules=custom_rules,
            )
        except Exception as error:
            errors.append(issue("setting", f"配置加载失败: {type(error).__name__}: {error}"))
            return AgentReport.from_parts(
                errors=errors,
                warnings=warnings,
                summary={
                    "source": source_label,
                    "rule_count": len(custom_rules),
                    "sample_count": len(sample_texts),
                },
                details={},
            )

        rule_details: JsonArray = []
        for rule in custom_rules:
            placeholder_preview = text_rules.format_custom_placeholder(
                template=rule.placeholder_template,
                index=1,
            )
            _append_placeholder_rule_safety_issues(
                rule=rule,
                errors=errors,
                warnings=warnings,
            )
            rule_details.append(
                {
                    "pattern": rule.pattern_text,
                    "placeholder_template": rule.placeholder_template,
                    "placeholder_preview": placeholder_preview,
                }
            )

        sample_details: JsonArray = []
        for sample_text in sample_texts:
            try:
                sample_details.append(_preview_placeholder_sample(text_rules, sample_text))
            except Exception as error:
                errors.append(
                    issue(
                        "placeholder_preview",
                        f"样本文本预览失败: {type(error).__name__}: {error}",
                    )
                )
        warnings.extend(_build_unprotected_control_warnings(sample_texts, text_rules))

        if not custom_rules:
            warnings.append(issue("placeholder_rules_empty", "当前没有自定义占位符规则"))

        return AgentReport.from_parts(
            errors=errors,
            warnings=warnings,
            summary={
                "source": source_label,
                "rule_count": len(custom_rules),
                "sample_count": len(sample_texts),
            },
            details={
                "rules": rule_details,
                "samples": sample_details,
            },
        )

    async def export_quality_fix_template(
        self,
        *,
        game_title: str,
        output_path: Path,
    ) -> AgentReport:
        """从质量报告问题生成可人工修复的补译 JSON 骨架。"""
        setting = load_setting(self.setting_path)
        async with await self.game_registry.open_game(game_title) as session:
            custom_rules = await self._resolve_custom_rules(
                session=session,
                custom_placeholder_rules_text=None,
            )
            text_rules = TextRules.from_setting(
                setting.text_rules,
                custom_placeholder_rules=custom_rules,
            )
            game_data = await self._load_game_data(session)
            translation_data_map = await self._extract_active_translation_data_map(
                session=session,
                game_data=game_data,
                text_rules=text_rules,
            )
            active_items = {
                item.location_path: item
                for translation_data in translation_data_map.values()
                for item in translation_data.translation_items
            }
            active_paths = set(active_items)
            translated_items = await session.read_translated_items()
            translated_by_path = {item.location_path: item for item in translated_items}
            translated_paths = set(translated_by_path)
            latest_run = await session.read_latest_translation_run()
            if latest_run is None:
                quality_error_items: list[TranslationErrorItem] = []
            else:
                quality_error_items = await session.read_translation_quality_errors(latest_run.run_id)
            japanese_residual_rule_set = JapaneseResidualRuleSet.from_records(
                await session.read_japanese_residual_rules()
            )

        pending_paths = active_paths - translated_paths
        quality_error_items = [
            item
            for item in quality_error_items
            if item.location_path in pending_paths
        ]
        residual_details = _collect_residual_items(
            translated_items,
            text_rules,
            japanese_residual_rule_set,
        )
        placeholder_details = _collect_placeholder_risk_items(translated_items, text_rules)
        overwide_details = _collect_overwide_line_items(translated_items, text_rules)
        problem_paths = _collect_quality_fix_problem_paths(
            quality_error_items=quality_error_items,
            residual_details=residual_details,
            placeholder_details=placeholder_details,
            overwide_details=overwide_details,
            active_paths=active_paths,
        )
        quality_errors_by_path = {
            item.location_path: item
            for item in quality_error_items
        }
        categories_by_path = _build_quality_fix_categories_by_path(
            quality_error_items=quality_error_items,
            residual_details=residual_details,
            placeholder_details=placeholder_details,
            overwide_details=overwide_details,
            active_paths=active_paths,
        )
        payload: JsonObject = {}
        for location_path in problem_paths:
            active_item = active_items[location_path]
            translation_lines = _resolve_quality_fix_translation_lines(
                location_path=location_path,
                quality_errors_by_path=quality_errors_by_path,
                translated_by_path=translated_by_path,
            )
            payload[location_path] = _build_manual_translation_template_entry(
                item=active_item,
                text_rules=text_rules,
                translation_lines=translation_lines,
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(output_path, "w", encoding="utf-8") as file:
            _ = await file.write(f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n")

        warnings: list[AgentIssue] = []
        if not problem_paths:
            warnings.append(issue("quality_fix_empty", "当前没有可导出的质量修复条目"))
        return AgentReport.from_parts(
            errors=[],
            warnings=warnings,
            summary={
                "exported_count": len(problem_paths),
                "output": str(output_path),
                "quality_error_count": len(quality_error_items),
                "japanese_residual_count": _count_active_quality_details(residual_details, active_paths),
                "placeholder_risk_count": _count_active_quality_details(placeholder_details, active_paths),
                "overwide_line_count": _count_active_quality_details(overwide_details, active_paths),
            },
            details={
                "location_paths": _string_lines_to_json_array(problem_paths),
                "problem_categories_by_path": categories_by_path,
            },
        )

    async def quality_report(self, *, game_title: str) -> AgentReport:
        """生成目标游戏当前翻译状态和质量风险报告。"""
        setting = load_setting(self.setting_path)
        errors: list[AgentIssue] = []
        warnings: list[AgentIssue] = []
        async with await self.game_registry.open_game(game_title) as session:
            custom_rules = await self._resolve_custom_rules(
                session=session,
                custom_placeholder_rules_text=None,
            )
            text_rules = TextRules.from_setting(
                setting.text_rules,
                custom_placeholder_rules=custom_rules,
            )
            game_data = await self._load_game_data(session)
            plugin_rules, stale_plugin_rule_count = await self._read_fresh_plugin_text_rules(
                session=session,
                game_data=game_data,
            )
            event_rules = await session.read_event_command_text_rules()
            japanese_residual_rules = await session.read_japanese_residual_rules()
            name_registry = await session.read_name_context_registry()
            latest_run = await session.read_latest_translation_run()
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
            pending_paths = active_paths - translated_paths
            stale_paths = translated_paths - active_paths
            stale_japanese_residual_rule_paths = {
                rule.location_path
                for rule in japanese_residual_rules
                if rule.location_path not in active_paths
            }
            if latest_run is None:
                quality_error_items: list[TranslationErrorItem] = []
                llm_failures: list[LlmFailureRecord] = []
            else:
                quality_error_items = await session.read_translation_quality_errors(latest_run.run_id)
                llm_failures = await session.read_llm_failures(latest_run.run_id)

        run_quality_error_count = len(quality_error_items)
        quality_error_items = [
            item
            for item in quality_error_items
            if item.location_path in pending_paths
        ]
        japanese_residual_rule_set = JapaneseResidualRuleSet.from_records(japanese_residual_rules)
        residual_items = _collect_residual_items(
            translated_items,
            text_rules,
            japanese_residual_rule_set,
        )
        residual_count = len(residual_items)
        placeholder_risk_items = _collect_placeholder_risk_items(translated_items, text_rules)
        overwide_line_items = _collect_overwide_line_items(translated_items, text_rules)
        error_type_counts = Counter(item.error_type for item in quality_error_items)
        quality_error_details: JsonArray = []
        for item in quality_error_items:
            quality_error_details.append(_build_translation_error_quality_detail(item))
        model_response_count = sum(
            1
            for item in quality_error_items
            if item.model_response.strip()
        )
        llm_failure_counts = Counter(failure.category for failure in llm_failures)
        filled_name_count = 0
        total_name_count = 0
        if name_registry is not None:
            total_name_count = len(name_registry.speaker_names) + len(name_registry.map_display_names)
            filled_name_count = sum(
                1
                for value in [*name_registry.speaker_names.values(), *name_registry.map_display_names.values()]
                if value.strip()
            )

        if llm_failures and pending_paths:
            errors.append(issue("llm_failures", f"最新翻译运行存在 {len(llm_failures)} 条模型运行故障"))
        elif llm_failures:
            warnings.append(issue("historical_llm_failures", f"最新翻译运行记录过 {len(llm_failures)} 条模型故障，但当前没有待处理正文由此阻断"))
        if quality_error_items:
            errors.append(issue("translation_quality_errors", f"最新翻译运行存在 {len(quality_error_items)} 条译文质量错误"))
        if pending_paths:
            errors.append(issue("pending_translations", f"存在 {len(pending_paths)} 条正文尚未成功入库"))
        if placeholder_risk_items:
            errors.append(issue("placeholder_risk", f"发现 {len(placeholder_risk_items)} 条占位符风险译文"))
        if residual_count:
            warnings.append(issue("japanese_residual", f"发现 {residual_count} 条译文存在日文残留风险"))
        if overwide_line_items:
            errors.append(issue("overwide_line", f"发现 {len(overwide_line_items)} 行译文超过当前长文本宽度上限"))
        if stale_paths:
            warnings.append(issue("stale_cache", f"发现 {len(stale_paths)} 条不在当前提取范围内的缓存译文"))
        if stale_plugin_rule_count:
            warnings.append(issue("stale_plugin_rules", f"发现 {stale_plugin_rule_count} 个过期插件规则，已从本轮质量统计中排除"))
        if stale_japanese_residual_rule_paths:
            warnings.append(issue("stale_japanese_residual_rules", f"发现 {len(stale_japanese_residual_rule_paths)} 条不在当前提取范围内的日文残留例外规则"))

        return AgentReport.from_parts(
            errors=errors,
            warnings=warnings,
            summary={
                "extractable_count": len(active_paths),
                "translated_count": len(translated_paths & active_paths),
                "pending_count": len(pending_paths),
                "stale_cache_count": len(stale_paths),
                "plugin_rule_count": sum(len(rule.path_templates) for rule in plugin_rules),
                "stale_plugin_rule_count": stale_plugin_rule_count,
                "event_command_rule_count": sum(len(rule.path_templates) for rule in event_rules),
                "japanese_residual_rule_count": len(japanese_residual_rules),
                "stale_japanese_residual_rule_count": len(stale_japanese_residual_rule_paths),
                "name_context_total_count": total_name_count,
                "name_context_filled_count": filled_name_count,
                "latest_run_id": latest_run.run_id if latest_run is not None else "",
                "latest_run_status": latest_run.status if latest_run is not None else "",
                "llm_failure_count": len(llm_failures),
                "quality_error_count": len(quality_error_items),
                "run_quality_error_count": run_quality_error_count,
                "model_response_error_count": model_response_count,
                "japanese_residual_count": residual_count,
                "placeholder_risk_count": len(placeholder_risk_items),
                "overwide_line_count": len(overwide_line_items),
                "writable_translation_count": len(translated_paths & active_paths),
            },
            details={
                "error_type_counts": dict(error_type_counts),
                "llm_failure_counts": dict(llm_failure_counts),
                "quality_error_items": quality_error_details,
                "japanese_residual_items": residual_items,
                "placeholder_risk_items": placeholder_risk_items,
                "overwide_line_items": overwide_line_items,
            },
        )

    async def translation_status(self, *, game_title: str) -> AgentReport:
        """读取最新正文翻译运行状态，并补充当前数据库实时 pending。"""
        async with await self.game_registry.open_game(game_title) as session:
            latest_run = await session.read_latest_translation_run()
            if latest_run is None:
                return AgentReport.from_parts(
                    errors=[],
                    warnings=[issue("translation_run_missing", "当前游戏尚未产生正文翻译运行记录")],
                    summary={},
                    details={},
                )
            setting = load_setting(self.setting_path)
            llm_failures = await session.read_llm_failures(latest_run.run_id)
            quality_errors = await session.read_translation_quality_errors(latest_run.run_id)
            custom_rules = await self._resolve_custom_rules(
                session=session,
                custom_placeholder_rules_text=None,
            )
            text_rules = TextRules.from_setting(setting.text_rules, custom_placeholder_rules=custom_rules)
            game_data = await self._load_game_data(session)
            translation_data_map = await self._extract_active_translation_data_map(
                session=session,
                game_data=game_data,
                text_rules=text_rules,
            )
            active_paths = {
                item.location_path
                for translation_data in translation_data_map.values()
                for item in translation_data.translation_items
            }
            translated_paths = await session.read_translation_location_paths()
            current_pending_paths = active_paths - translated_paths
            run_quality_error_count = len(quality_errors)
            quality_errors = [
                error for error in quality_errors if error.location_path in current_pending_paths
            ]
        return AgentReport.from_parts(
            errors=[],
            warnings=[],
            summary={
                "run_id": latest_run.run_id,
                "status": latest_run.status,
                "total_extracted": latest_run.total_extracted,
                "pending_count": len(current_pending_paths),
                "run_pending_count": latest_run.pending_count,
                "translated_count": len(translated_paths & active_paths),
                "extractable_count": len(active_paths),
                "deduplicated_count": latest_run.deduplicated_count,
                "batch_count": latest_run.batch_count,
                "success_count": latest_run.success_count,
                "quality_error_count": len(quality_errors),
                "run_quality_error_count": run_quality_error_count,
                "llm_failure_count": len(llm_failures),
                "stop_reason": latest_run.stop_reason,
                "last_error": latest_run.last_error,
            },
            details={
                "llm_failure_counts": dict(Counter(failure.category for failure in llm_failures)),
                "quality_error_counts": dict(Counter(error.error_type for error in quality_errors)),
            },
        )

    async def export_pending_translations(
        self,
        *,
        game_title: str,
        output_path: Path,
        limit: int | None,
    ) -> AgentReport:
        """导出尚未入库的翻译条目，供 Agent 做人工补译。"""
        setting = load_setting(self.setting_path)
        async with await self.game_registry.open_game(game_title) as session:
            custom_rules = await self._resolve_custom_rules(
                session=session,
                custom_placeholder_rules_text=None,
            )
            text_rules = TextRules.from_setting(setting.text_rules, custom_placeholder_rules=custom_rules)
            game_data = await self._load_game_data(session)
            translation_data_map = await self._extract_active_translation_data_map(
                session=session,
                game_data=game_data,
                text_rules=text_rules,
            )
            translated_paths = await session.read_translation_location_paths()

        pending_items = [
            item
            for translation_data in translation_data_map.values()
            for item in translation_data.translation_items
            if item.location_path not in translated_paths
        ]
        if limit is not None:
            pending_items = pending_items[: max(limit, 0)]

        payload: JsonObject = {}
        for item in pending_items:
            payload[item.location_path] = _build_manual_translation_template_entry(
                item=item,
                text_rules=text_rules,
                translation_lines=[],
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(output_path, "w", encoding="utf-8") as file:
            _ = await file.write(f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n")

        warnings: list[AgentIssue] = []
        if not pending_items:
            warnings.append(issue("pending_empty", "当前没有待人工补译条目"))
        return AgentReport.from_parts(
            errors=[],
            warnings=warnings,
            summary={
                "pending_exported_count": len(pending_items),
                "output": str(output_path),
            },
            details={},
        )

    async def import_manual_translations(self, *, game_title: str, input_path: Path) -> AgentReport:
        """导入 Agent 人工补齐的译文，并按项目规则校验后入库。"""
        try:
            async with aiofiles.open(input_path, "r", encoding="utf-8-sig") as file:
                raw_payload = cast(object, json.loads(await file.read()))
            payload = ensure_json_object(coerce_json_value(raw_payload), "manual-translations")
        except Exception as error:
            return AgentReport.from_parts(
                errors=[issue("manual_translation_file", f"人工补译文件不可读: {type(error).__name__}: {error}")],
                warnings=[],
                summary={"input": str(input_path), "imported_count": 0},
                details={},
            )

        setting = load_setting(self.setting_path)
        errors: list[AgentIssue] = []
        valid_items: list[TranslationItem] = []
        async with await self.game_registry.open_game(game_title) as session:
            custom_rules = await self._resolve_custom_rules(
                session=session,
                custom_placeholder_rules_text=None,
            )
            text_rules = TextRules.from_setting(setting.text_rules, custom_placeholder_rules=custom_rules)
            game_data = await self._load_game_data(session)
            translation_data_map = await self._extract_active_translation_data_map(
                session=session,
                game_data=game_data,
                text_rules=text_rules,
            )
            japanese_residual_rule_set = JapaneseResidualRuleSet.from_records(
                await session.read_japanese_residual_rules()
            )
            active_items = {
                item.location_path: item
                for translation_data in translation_data_map.values()
                for item in translation_data.translation_items
            }

            for location_path, raw_entry in payload.items():
                if not isinstance(raw_entry, dict):
                    errors.append(issue("manual_translation_entry", f"{location_path} 必须是 JSON 对象"))
                    continue
                entry = ensure_json_object(raw_entry, f"{location_path}")
                item = active_items.get(location_path)
                if item is None:
                    errors.append(issue("manual_translation_location", f"{location_path} 不在当前可提取文本范围内"))
                    continue
                try:
                    raw_lines_value = entry.get("translation_lines")
                    if raw_lines_value is None:
                        raise TypeError(f"{location_path}.translation_lines 必须是字符串数组")
                    translation_lines = ensure_json_string_list(raw_lines_value, f"{location_path}.translation_lines")
                    cloned_item = _prepare_manual_translation_item(
                        item=item,
                        translation_lines=translation_lines,
                        text_rules=text_rules,
                        japanese_residual_rule_set=japanese_residual_rule_set,
                    )
                    valid_items.append(cloned_item)
                except Exception as error:
                    errors.append(
                        issue(
                            "manual_translation_invalid",
                            f"{location_path} 人工补译不可用: {type(error).__name__}: {error}",
                        )
                    )

            if errors:
                return AgentReport.from_parts(
                    errors=errors,
                    warnings=[],
                    summary={
                        "input": str(input_path),
                        "imported_count": 0,
                        "error_count": len(errors),
                    },
                    details={},
                )

            await session.write_translation_items(valid_items)

        return AgentReport.from_parts(
            errors=[],
            warnings=[] if valid_items else [issue("manual_translation_empty", "人工补译文件没有可导入条目")],
            summary={
                "input": str(input_path),
                "imported_count": len(valid_items),
            },
            details={},
        )

    async def build_placeholder_rules(
        self,
        *,
        game_title: str,
        output_path: Path,
    ) -> AgentReport:
        """根据未覆盖候选生成可编辑的自定义占位符规则草稿。"""
        setting = load_setting(self.setting_path)
        empty_rules = TextRules.from_setting(setting.text_rules, custom_placeholder_rules=())
        async with await self.game_registry.open_game(game_title) as session:
            game_data = await self._load_game_data(session)
        candidates = scan_placeholder_candidates(game_data, empty_rules)
        draft_rules = _build_custom_placeholder_rule_draft(candidates)
        warnings = _build_unprotected_control_warnings(
            _collect_unprotected_control_warning_samples(game_data, empty_rules),
            empty_rules,
        )
        if not draft_rules:
            warnings.append(issue("placeholder_draft_empty", "没有发现需要生成草稿的自定义控制符候选"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(output_path, "w", encoding="utf-8") as file:
            _ = await file.write(f"{json.dumps(draft_rules, ensure_ascii=False, indent=2)}\n")
        return AgentReport.from_parts(
            errors=[],
            warnings=warnings,
            summary={
                "candidate_count": len(candidates),
                "draft_rule_count": len(draft_rules),
                "output": str(output_path),
            },
            details={"rules": {key: value for key, value in draft_rules.items()}},
        )

    async def validate_japanese_residual_rules(self, *, game_title: str, rules_text: str) -> AgentReport:
        """校验日文残留例外规则 JSON 文本并报告命中情况。"""
        errors: list[AgentIssue] = []
        warnings: list[AgentIssue] = []
        details: JsonObject = {"rules": []}
        try:
            records = await self._build_japanese_residual_rule_records(
                game_title=game_title,
                rules_text=rules_text,
            )
            details["rules"] = [
                {
                    "location_path": record.location_path,
                    "allowed_terms": list(record.allowed_terms),
                    "reason": record.reason,
                }
                for record in records
            ]
            if not records:
                warnings.append(issue("japanese_residual_rules_empty", "日文残留例外规则为空"))
        except Exception as error:
            errors.append(issue("japanese_residual_rules_invalid", f"日文残留例外规则不可导入: {type(error).__name__}: {error}"))
            records = []
        return AgentReport.from_parts(
            errors=errors,
            warnings=warnings,
            summary={
                "rule_count": len(records),
                "term_count": sum(len(record.allowed_terms) for record in records),
            },
            details=details,
        )

    async def import_japanese_residual_rules(self, *, game_title: str, rules_text: str) -> AgentReport:
        """校验并导入当前游戏的日文残留例外规则。"""
        try:
            records = await self._build_japanese_residual_rule_records(
                game_title=game_title,
                rules_text=rules_text,
            )
            async with await self.game_registry.open_game(game_title) as session:
                await session.replace_japanese_residual_rules(records)
        except Exception as error:
            return AgentReport.from_parts(
                errors=[issue("japanese_residual_rules_invalid", f"日文残留例外规则不可导入: {type(error).__name__}: {error}")],
                warnings=[],
                summary={"rule_count": 0, "term_count": 0},
                details={},
            )
        return AgentReport.from_parts(
            errors=[],
            warnings=[] if records else [issue("japanese_residual_rules_empty", "已导入空日文残留例外规则")],
            summary={
                "rule_count": len(records),
                "term_count": sum(len(record.allowed_terms) for record in records),
            },
            details={
                "rules": [
                    {
                        "location_path": record.location_path,
                        "allowed_terms": list(record.allowed_terms),
                        "reason": record.reason,
                    }
                    for record in records
                ]
            },
        )

    async def reset_translations(self, *, game_title: str, input_path: Path) -> AgentReport:
        """按显式定位路径清除已入库译文，使条目回到 pending 状态。"""
        try:
            location_paths = await _read_reset_translation_location_paths(input_path)
        except Exception as error:
            return AgentReport.from_parts(
                errors=[issue("reset_translation_file", f"重置译文文件不可用: {type(error).__name__}: {error}")],
                warnings=[],
                summary={
                    "input": str(input_path),
                    "requested_count": 0,
                    "reset_count": 0,
                },
                details={},
            )

        setting = load_setting(self.setting_path)
        async with await self.game_registry.open_game(game_title) as session:
            custom_rules = await self._resolve_custom_rules(
                session=session,
                custom_placeholder_rules_text=None,
            )
            text_rules = TextRules.from_setting(setting.text_rules, custom_placeholder_rules=custom_rules)
            game_data = await self._load_game_data(session)
            translation_data_map = await self._extract_active_translation_data_map(
                session=session,
                game_data=game_data,
                text_rules=text_rules,
            )
            active_paths = {
                item.location_path
                for translation_data in translation_data_map.values()
                for item in translation_data.translation_items
            }
            invalid_paths = sorted(set(location_paths) - active_paths)
            if invalid_paths:
                return AgentReport.from_parts(
                    errors=[
                        issue(
                            "reset_translation_location",
                            f"存在 {len(invalid_paths)} 个定位路径不在当前可提取文本范围内",
                        )
                    ],
                    warnings=[],
                    summary={
                        "input": str(input_path),
                        "requested_count": len(location_paths),
                        "reset_count": 0,
                    },
                    details={
                        "invalid_location_paths": _string_lines_to_json_array(invalid_paths),
                    },
                )
            reset_count = await session.delete_translation_items_by_paths(location_paths)

        warnings: list[AgentIssue] = []
        already_pending_count = len(location_paths) - reset_count
        if already_pending_count:
            warnings.append(issue("reset_translation_already_pending", f"{already_pending_count} 个定位路径当前没有已入库译文"))
        return AgentReport.from_parts(
            errors=[],
            warnings=warnings,
            summary={
                "input": str(input_path),
                "requested_count": len(location_paths),
                "reset_count": reset_count,
            },
            details={
                "location_paths": _string_lines_to_json_array(location_paths),
            },
        )

    async def validate_plugin_rules(self, *, game_title: str, rules_text: str) -> AgentReport:
        """校验插件规则 JSON 文本并报告命中情况。"""
        errors: list[AgentIssue] = []
        warnings: list[AgentIssue] = []
        details: JsonObject = {"rules": []}
        try:
            import_file = parse_plugin_rule_import_text(rules_text)
            async with await self.game_registry.open_game(game_title) as session:
                game_data = await self._load_game_data(session)
            records = build_plugin_rule_records_from_import(game_data=game_data, import_file=import_file)
            setting = load_setting(self.setting_path)
            text_rules = TextRules.from_setting(setting.text_rules)
            extracted_map = PluginTextExtraction(
                game_data,
                plugin_rule_records=records,
                text_rules=text_rules,
            ).extract_all_text()
            extracted_items = [
                item
                for translation_data in extracted_map.values()
                for item in translation_data.translation_items
            ]
            details["rules"] = [
                {
                    "plugin_name": record.plugin_name,
                    "path_count": len(record.path_templates),
                    "paths": list(record.path_templates),
                    "hit_count": sum(
                        1
                        for item in extracted_items
                        if item.location_path.startswith(f"{PLUGINS_FILE_NAME}/{record.plugin_index}/")
                    ),
                    "samples": _first_original_line_samples(
                        item
                        for item in extracted_items
                        if item.location_path.startswith(f"{PLUGINS_FILE_NAME}/{record.plugin_index}/")
                    ),
                }
                for record in records
            ]
            if not records:
                warnings.append(issue("plugin_rules_empty", "插件规则为空"))
            if records and not extracted_items:
                warnings.append(issue("plugin_rules_no_hits", "插件规则没有提取到任何可翻译文本"))
        except Exception as error:
            errors.append(issue("plugin_rules_invalid", f"插件规则不可导入: {type(error).__name__}: {error}"))
            records = []
        return AgentReport.from_parts(
            errors=errors,
            warnings=warnings,
            summary={
                "plugin_count": len(records),
                "rule_count": sum(len(record.path_templates) for record in records),
            },
            details=details,
        )

    async def validate_event_command_rules(self, *, game_title: str, rules_text: str) -> AgentReport:
        """校验事件指令规则 JSON 文本并报告命中情况。"""
        errors: list[AgentIssue] = []
        warnings: list[AgentIssue] = []
        details: JsonObject = {"rules": []}
        try:
            import_file = parse_event_command_rule_import_text(rules_text)
            async with await self.game_registry.open_game(game_title) as session:
                game_data = await self._load_game_data(session)
            records = build_event_command_rule_records_from_import(game_data=game_data, import_file=import_file)
            setting = load_setting(self.setting_path)
            text_rules = TextRules.from_setting(setting.text_rules)
            extracted_map = EventCommandTextExtraction(
                game_data,
                rule_records=records,
                text_rules=text_rules,
            ).extract_all_text()
            extracted_items = [
                item
                for translation_data in extracted_map.values()
                for item in translation_data.translation_items
            ]
            try:
                _preview_event_command_write_back(
                    game_data=game_data,
                    extracted_items=extracted_items,
                    text_rules=text_rules,
                )
                details["write_back_preview"] = {
                    "checked_item_count": len(extracted_items),
                    "status": "ok",
                }
            except Exception as error:
                errors.append(
                    issue(
                        "event_command_write_back_invalid",
                        f"事件指令规则命中项无法回写: {type(error).__name__}: {error}",
                    )
                )
                details["write_back_preview"] = {
                    "checked_item_count": len(extracted_items),
                    "status": "error",
                    "reason": f"{type(error).__name__}: {error}",
                }
            rule_details: JsonArray = []
            for record in records:
                record_extracted_map = EventCommandTextExtraction(
                    game_data,
                    rule_records=[record],
                    text_rules=text_rules,
                ).extract_all_text()
                record_items = [
                    item
                    for translation_data in record_extracted_map.values()
                    for item in translation_data.translation_items
                ]
                rule_details.append(
                    {
                        "command_code": record.command_code,
                        "match_count": len(record.parameter_filters),
                        "path_count": len(record.path_templates),
                        "paths": list(record.path_templates),
                        "hit_count": len(record_items),
                        "samples": _first_original_line_samples(record_items),
                    }
                )
            details["rules"] = rule_details
            if not records:
                warnings.append(issue("event_command_rules_empty", "事件指令规则为空"))
            if records and not extracted_items:
                warnings.append(issue("event_command_rules_no_hits", "事件指令规则没有提取到任何可翻译文本"))
        except Exception as error:
            errors.append(issue("event_command_rules_invalid", f"事件指令规则不可导入: {type(error).__name__}: {error}"))
            records = []
        return AgentReport.from_parts(
            errors=errors,
            warnings=warnings,
            summary={
                "rule_group_count": len(records),
                "path_rule_count": sum(len(record.path_templates) for record in records),
            },
            details=details,
        )

    async def prepare_agent_workspace(
        self,
        *,
        game_title: str,
        output_dir: Path,
        command_codes: set[int] | None,
    ) -> AgentReport:
        """导出 Agent 分析所需的全部临时输入文件并生成 manifest。"""
        target_dir = output_dir.resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        setting = load_setting(self.setting_path)
        async with await self.game_registry.open_game(game_title) as session:
            game_data = await self._load_game_data(session)
            custom_rules = await self._resolve_custom_rules(session=session, custom_placeholder_rules_text=None)
        text_rules = TextRules.from_setting(setting.text_rules, custom_placeholder_rules=custom_rules)
        name_summary = await export_name_context_artifacts(game_data=game_data, output_dir=target_dir / "name-context")
        plugins_path = target_dir / "plugins.json"
        await export_plugins_json_file(game_data=game_data, output_path=plugins_path)
        default_command_codes = None if command_codes is not None else setting.event_command_text.default_command_codes
        effective_codes = resolve_event_command_codes(command_codes=command_codes, default_command_codes=default_command_codes)
        event_commands_path = target_dir / "event-commands.json"
        event_command_count = await export_event_commands_json_file(
            game_data=game_data,
            output_path=event_commands_path,
            command_codes=effective_codes,
        )
        placeholder_candidates = scan_placeholder_candidates(game_data, text_rules)
        placeholder_report = AgentReport.from_parts(
            errors=[],
            warnings=[],
            summary={},
            details={"candidates": placeholder_candidates_to_details(placeholder_candidates)},
        )
        placeholder_path = target_dir / "placeholder-candidates.json"
        async with aiofiles.open(placeholder_path, "w", encoding="utf-8") as file:
            _ = await file.write(f"{placeholder_report.to_json_text()}\n")
        placeholder_rule_drafts = _build_custom_placeholder_rule_draft(placeholder_candidates)
        placeholder_rules_path = target_dir / "placeholder-rules.json"
        async with aiofiles.open(placeholder_rules_path, "w", encoding="utf-8") as file:
            _ = await file.write(f"{json.dumps(placeholder_rule_drafts, ensure_ascii=False, indent=2)}\n")
        generated_summary: JsonObject = {
            "speaker_entry_count": name_summary.speaker_entry_count,
            "map_entry_count": name_summary.map_entry_count,
            "plugin_count": len(game_data.plugins_js),
            "event_command_count": event_command_count,
            "placeholder_rule_draft_count": len(placeholder_rule_drafts),
        }
        manifest_files: JsonArray = [
            str(name_summary.registry_path),
            str(name_summary.sample_dir),
            str(plugins_path),
            str(event_commands_path),
            str(placeholder_path),
            str(placeholder_rules_path),
        ]
        manifest: JsonObject = {
            "files": manifest_files,
            "generated": generated_summary,
        }
        manifest_path = target_dir / "manifest.json"
        async with aiofiles.open(manifest_path, "w", encoding="utf-8") as file:
            _ = await file.write(f"{json.dumps(manifest, ensure_ascii=False, indent=2)}\n")
        return AgentReport.from_parts(
            errors=[],
            warnings=[],
            summary={**generated_summary, "workspace": str(target_dir), "manifest": str(manifest_path)},
            details={"manifest": manifest},
        )

    async def validate_agent_workspace(self, *, game_title: str, workspace: Path) -> AgentReport:
        """检查 Agent 临时工作区里的可导入产物。"""
        errors: list[AgentIssue] = []
        warnings: list[AgentIssue] = []
        details: JsonObject = {}
        name_path = workspace / "name-context" / "name_registry.json"
        plugin_rules_path = workspace / "plugin-rules.json"
        event_rules_path = workspace / "event-command-rules.json"
        placeholder_rules_path = workspace / "placeholder-rules.json"
        if name_path.exists():
            registry = await load_name_context_registry(registry_path=name_path)
            name_issues = _validate_name_registry(registry.speaker_names, registry.map_display_names)
            warnings.extend(name_issues)
            details["name_context"] = {
                "speaker_count": len(registry.speaker_names),
                "map_count": len(registry.map_display_names),
            }
        else:
            warnings.append(issue("name_context_missing", "工作区缺少 name-context/name_registry.json"))
        if plugin_rules_path.exists():
            async with aiofiles.open(plugin_rules_path, "r", encoding="utf-8") as file:
                plugin_report = await self.validate_plugin_rules(game_title=game_title, rules_text=await file.read())
            errors.extend(plugin_report.errors)
            warnings.extend(plugin_report.warnings)
            details["plugin_rules"] = plugin_report.details
        else:
            warnings.append(issue("plugin_rules_missing", "工作区缺少 plugin-rules.json"))
        if event_rules_path.exists():
            async with aiofiles.open(event_rules_path, "r", encoding="utf-8") as file:
                event_report = await self.validate_event_command_rules(game_title=game_title, rules_text=await file.read())
            errors.extend(event_report.errors)
            warnings.extend(event_report.warnings)
            details["event_command_rules"] = event_report.details
        else:
            warnings.append(issue("event_command_rules_missing", "工作区缺少 event-command-rules.json"))
        if placeholder_rules_path.exists():
            async with aiofiles.open(placeholder_rules_path, "r", encoding="utf-8") as file:
                placeholder_report = await self.validate_placeholder_rules(
                    game_title=game_title,
                    custom_placeholder_rules_text=await file.read(),
                    sample_texts=[],
                )
            errors.extend(placeholder_report.errors)
            warnings.extend(placeholder_report.warnings)
            details["placeholder_rules"] = placeholder_report.details
        else:
            warnings.append(issue("placeholder_rules_missing", "工作区缺少 placeholder-rules.json"))
        return AgentReport.from_parts(errors=errors, warnings=warnings, summary={"workspace": str(workspace)}, details=details)

    async def cleanup_agent_workspace(self, *, workspace: Path) -> AgentReport:
        """按 manifest 删除 Agent 临时工作区产物。"""
        manifest_path = workspace / "manifest.json"
        if not manifest_path.exists():
            return AgentReport.from_parts(
                errors=[issue("manifest_missing", "工作区缺少 manifest.json，拒绝自动清理")],
                warnings=[],
                summary={"workspace": str(workspace)},
                details={},
            )
        async with aiofiles.open(manifest_path, "r", encoding="utf-8") as file:
            # `json.loads` 在类型存根中返回 Any；这里立刻收窄到项目 JSON 类型边界。
            raw_manifest = cast(object, json.loads(await file.read()))
        manifest = ensure_json_object(coerce_json_value(raw_manifest), "manifest")
        deleted_count = 0
        try:
            files_value = ensure_json_array(manifest.get("files"), "manifest.files")
        except TypeError:
            return AgentReport.from_parts(
                errors=[issue("manifest_invalid", "manifest.files 必须是数组")],
                warnings=[],
                summary={"workspace": str(workspace)},
                details={},
            )
        for raw_path in files_value:
            if not isinstance(raw_path, str):
                continue
            path = Path(raw_path).resolve()
            if not _is_path_inside(path, workspace.resolve()):
                continue
            if path.is_dir():
                shutil.rmtree(path)
                deleted_count += 1
            elif path.exists():
                path.unlink()
                deleted_count += 1
        if manifest_path.exists():
            manifest_path.unlink()
            deleted_count += 1
        return AgentReport.from_parts(
            errors=[],
            warnings=[],
            summary={"workspace": str(workspace), "deleted_count": deleted_count},
            details={},
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
            async with await self.game_registry.open_game(game_title) as session:
                custom_rules = await self._resolve_custom_rules(
                    session=session,
                    custom_placeholder_rules_text=None,
                )
                text_rules = TextRules.from_setting(
                    setting.text_rules,
                    custom_placeholder_rules=custom_rules,
                )
                game_data = await self._load_game_data(session)
                plugin_rules, stale_plugin_rule_count = await self._read_fresh_plugin_text_rules(
                    session=session,
                    game_data=game_data,
                )
                event_rules = await session.read_event_command_text_rules()
                name_registry = await session.read_name_context_registry()
                placeholder_rules = await session.read_placeholder_rules()
                summary["game_registered"] = True
                summary["plugin_rule_count"] = sum(len(rule.path_templates) for rule in plugin_rules)
                summary["stale_plugin_rule_count"] = stale_plugin_rule_count
                summary["event_command_rule_count"] = sum(len(rule.path_templates) for rule in event_rules)
                summary["placeholder_rule_count"] = len(placeholder_rules)
                summary["name_context_imported"] = name_registry is not None
                if not plugin_rules and stale_plugin_rule_count == 0:
                    warnings.append(issue("plugin_rules", "当前游戏尚未导入插件文本规则"))
                if stale_plugin_rule_count:
                    warnings.append(issue("stale_plugin_rules", f"发现 {stale_plugin_rule_count} 个过期插件规则，请重新导出并导入插件规则"))
                if not event_rules:
                    warnings.append(issue("event_command_rules", "当前游戏尚未导入事件指令文本规则"))
                if name_registry is None:
                    warnings.append(issue("name_context", "当前游戏尚未导入术语表"))
                if not placeholder_rules:
                    warnings.append(issue("placeholder_rules", "当前游戏尚未导入自定义占位符规则"))
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
        """检查项目固定目录和终端编码。"""
        _ = warnings
        db_dir = self.game_registry.db_directory
        db_dir_already_exists = db_dir.exists()
        try:
            ensure_db_directory(db_dir)
            _append_check(details, "db_dir", "ok" if db_dir_already_exists else "created")
        except Exception as error:
            errors.append(issue("db_dir", f"数据库目录创建失败: {type(error).__name__}: {error}"))
        if not Path("logs").exists():
            Path("logs").mkdir(exist_ok=True)
        try:
            encoding = sys.stdout.encoding or ""
            details["stdout_encoding"] = encoding
            _append_check(details, "stdout_encoding", "ok" if "utf" in encoding.lower() else "warning")
            if "utf" not in encoding.lower():
                warnings.append(issue("stdout_encoding", "当前 stdout 不是 UTF-8，建议使用 --agent-mode 或 --json"))
        except Exception as error:
            warnings.append(issue("stdout_encoding", f"终端编码检查失败: {type(error).__name__}: {error}"))

    async def _load_game_data(self, session: TargetGameSession) -> GameData:
        """加载单游戏数据并绑定到会话。"""
        from app.rmmz.loader import load_game_data

        game_data = await load_game_data(session.game_path)
        session.set_game_data(game_data)
        return game_data

    async def _extract_active_translation_data_map(
        self,
        *,
        session: TargetGameSession,
        game_data: GameData,
        text_rules: TextRules,
    ) -> dict[str, TranslationData]:
        """按当前数据库规则提取本轮有效正文条目。"""
        plugin_rules, _stale_plugin_rule_count = await self._read_fresh_plugin_text_rules(
            session=session,
            game_data=game_data,
        )
        event_rules = await session.read_event_command_text_rules()
        translation_data_map = DataTextExtraction(game_data, text_rules).extract_all_text()
        _merge_translation_data_map(
            translation_data_map,
            EventCommandTextExtraction(game_data, event_rules, text_rules).extract_all_text(),
        )
        _merge_translation_data_map(
            translation_data_map,
            PluginTextExtraction(game_data, plugin_rules, text_rules).extract_all_text(),
        )
        return translation_data_map

    async def _build_japanese_residual_rule_records(
        self,
        *,
        game_title: str,
        rules_text: str,
    ) -> list[JapaneseResidualRuleRecord]:
        """解析并按当前游戏提取结果校验日文残留例外规则。"""
        import_file = parse_japanese_residual_rule_import_text(rules_text)
        setting = load_setting(self.setting_path)
        async with await self.game_registry.open_game(game_title) as session:
            custom_rules = await self._resolve_custom_rules(
                session=session,
                custom_placeholder_rules_text=None,
            )
            text_rules = TextRules.from_setting(setting.text_rules, custom_placeholder_rules=custom_rules)
            game_data = await self._load_game_data(session)
            translation_data_map = await self._extract_active_translation_data_map(
                session=session,
                game_data=game_data,
                text_rules=text_rules,
            )
            active_items = [
                item
                for translation_data in translation_data_map.values()
                for item in translation_data.translation_items
            ]
            translated_items = await session.read_translated_items()
        return build_japanese_residual_rule_records_from_import(
            import_file=import_file,
            active_items=active_items,
            translated_items=translated_items,
        )

    async def _read_fresh_plugin_text_rules(
        self,
        *,
        session: TargetGameSession,
        game_data: GameData,
    ) -> tuple[list[PluginTextRuleRecord], int]:
        """读取仍匹配当前 `plugins.js` 的插件规则，并统计过期规则。"""
        plugin_rules = await session.read_plugin_text_rules()
        fresh_rules: list[PluginTextRuleRecord] = []
        stale_count = 0
        for rule in plugin_rules:
            if rule.plugin_index >= len(game_data.plugins_js):
                stale_count += 1
                continue
            plugin_hash = build_plugin_hash(game_data.plugins_js[rule.plugin_index])
            if rule.plugin_hash != plugin_hash:
                stale_count += 1
                continue
            fresh_rules.append(rule)
        return fresh_rules, stale_count

    async def _resolve_custom_rules(
        self,
        *,
        session: TargetGameSession,
        custom_placeholder_rules_text: str | None,
    ) -> tuple[CustomPlaceholderRule, ...]:
        """按 CLI 覆盖优先级解析自定义占位符规则。"""
        if custom_placeholder_rules_text is not None:
            return load_custom_placeholder_rules_text(custom_placeholder_rules_text)
        records = await session.read_placeholder_rules()
        return tuple(
            CustomPlaceholderRule.create(
                pattern_text=record.pattern_text,
                placeholder_template=record.placeholder_template,
            )
            for record in records
        )


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


COMMON_ESCAPE_SAMPLES: dict[str, str] = {
    "\\n": "裸 \\n 换行标记",
    "\\r": "裸 \\r 回车标记",
    "\\t": "裸 \\t 制表标记",
}
PLAIN_TEXT_RULE_SAMPLES: tuple[str, ...] = (
    "普通中文文本",
    "日本語本文",
    "plain visible text",
)
SUSPICIOUS_CONTROL_BOUNDARY_CHARS: frozenset[str] = frozenset("」』】）〕〉》")


def _append_placeholder_rule_safety_issues(
    *,
    rule: CustomPlaceholderRule,
    errors: list[AgentIssue],
    warnings: list[AgentIssue],
) -> None:
    """检查自定义占位符规则是否误匹配常见正文或裸转义文本。"""
    for sample_text, label in COMMON_ESCAPE_SAMPLES.items():
        if rule.pattern.fullmatch(sample_text) is None and rule.pattern.search(sample_text) is None:
            continue
        errors.append(
            issue(
                "placeholder_rule_matches_common_escape",
                f"规则 {rule.pattern_text} 会匹配{label}，容易把合法文本误判为占位符",
            )
        )
    for sample_text in PLAIN_TEXT_RULE_SAMPLES:
        if rule.pattern.search(sample_text) is None:
            continue
        warnings.append(
            issue(
                "placeholder_rule_matches_plain_text",
                f"规则 {rule.pattern_text} 会匹配普通正文样例 `{sample_text}`，请确认没有过宽吞掉玩家可见文本",
            )
        )
        return


def _build_unprotected_control_warnings(
    sample_texts: Sequence[str],
    text_rules: TextRules,
) -> list[AgentIssue]:
    """根据样本文本提示非 ASCII 括号或未闭合控制片段风险。"""
    suspicious_candidates: list[str] = []
    for sample_text in sample_texts:
        for candidate in text_rules.iter_unprotected_control_sequence_candidates(sample_text):
            if not _is_suspicious_unprotected_control(candidate.original):
                continue
            if candidate.original in suspicious_candidates:
                continue
            suspicious_candidates.append(candidate.original)
            if len(suspicious_candidates) >= 5:
                break
        if len(suspicious_candidates) >= 5:
            break

    if not suspicious_candidates:
        return []

    formatted_candidates = "；".join(
        f"{candidate} ({_format_code_points(candidate)})"
        for candidate in suspicious_candidates
    )
    return [
        issue(
            "unprotected_control_unicode_boundary",
            f"发现疑似非 ASCII 括号或未闭合控制片段，请核验 Unicode code point 后使用精确规则，禁止猜成 ASCII ]：{formatted_candidates}",
        )
    ]


def _is_suspicious_unprotected_control(candidate: str) -> bool:
    """判断裸露控制符是否包含容易被终端乱码掩盖的边界字符。"""
    if "[" in candidate and "]" not in candidate:
        return True
    return any(char in SUSPICIOUS_CONTROL_BOUNDARY_CHARS for char in candidate)


def _format_code_points(text: str) -> str:
    """把短文本格式化为 Unicode code point 列表。"""
    return " ".join(f"U+{ord(char):04X}" for char in text)


async def _read_reset_translation_location_paths(input_path: Path) -> list[str]:
    """读取 reset-translations 的最小 JSON 输入结构。"""
    async with aiofiles.open(input_path, "r", encoding="utf-8-sig") as file:
        raw_payload = cast(object, json.loads(await file.read()))
    payload = ensure_json_object(coerce_json_value(raw_payload), "reset-translations")
    raw_paths = payload.get("location_paths")
    if raw_paths is None:
        raise TypeError("reset-translations.location_paths 必须是字符串数组")
    location_paths = ensure_json_string_list(raw_paths, "reset-translations.location_paths")
    if not location_paths:
        raise ValueError("location_paths 不能为空")
    duplicate_paths = sorted(
        path
        for path, count in Counter(location_paths).items()
        if count > 1
    )
    if duplicate_paths:
        joined_paths = "、".join(duplicate_paths)
        raise ValueError(f"location_paths 不得重复: {joined_paths}")
    return location_paths


def _build_manual_translation_template_entry(
    *,
    item: TranslationItem,
    text_rules: TextRules,
    translation_lines: list[str],
) -> JsonObject:
    """把当前提取条目转换成人工补译 JSON 条目。"""
    cloned_item = item.model_copy(deep=True)
    cloned_item.build_placeholders(text_rules)
    return {
        "item_type": cloned_item.item_type,
        "role": cloned_item.role,
        "original_lines": _string_lines_to_json_array(cloned_item.original_lines),
        "text_for_model_lines": _string_lines_to_json_array(cloned_item.original_lines_with_placeholders),
        "translation_lines": _string_lines_to_json_array(translation_lines),
    }


def _collect_quality_fix_problem_paths(
    *,
    quality_error_items: list[TranslationErrorItem],
    residual_details: JsonArray,
    placeholder_details: JsonArray,
    overwide_details: JsonArray,
    active_paths: set[str],
) -> list[str]:
    """按质量报告优先级收集需要导出的唯一定位路径。"""
    location_paths: list[str] = []
    for item in quality_error_items:
        _append_unique_active_path(location_paths, item.location_path, active_paths)
    for details in (residual_details, placeholder_details, overwide_details):
        for location_path in _location_paths_from_quality_details(details):
            _append_unique_active_path(location_paths, location_path, active_paths)
    return location_paths


def _build_quality_fix_categories_by_path(
    *,
    quality_error_items: list[TranslationErrorItem],
    residual_details: JsonArray,
    placeholder_details: JsonArray,
    overwide_details: JsonArray,
    active_paths: set[str],
) -> JsonObject:
    """建立质量修复条目到问题类型的映射，方便 Agent 分工处理。"""
    categories: dict[str, list[str]] = {}
    for item in quality_error_items:
        if item.location_path in active_paths:
            categories.setdefault(item.location_path, []).append("quality_error")
    _append_quality_detail_categories(categories, residual_details, active_paths, "japanese_residual")
    _append_quality_detail_categories(categories, placeholder_details, active_paths, "placeholder_risk")
    _append_quality_detail_categories(categories, overwide_details, active_paths, "overwide_line")
    return {
        location_path: _string_lines_to_json_array(path_categories)
        for location_path, path_categories in categories.items()
    }


def _append_quality_detail_categories(
    categories: dict[str, list[str]],
    details: JsonArray,
    active_paths: set[str],
    category: str,
) -> None:
    """把一组质量明细的问题类型追加到映射中。"""
    for location_path in _location_paths_from_quality_details(details):
        if location_path not in active_paths:
            continue
        path_categories = categories.setdefault(location_path, [])
        if category not in path_categories:
            path_categories.append(category)


def _append_unique_active_path(
    location_paths: list[str],
    location_path: str,
    active_paths: set[str],
) -> None:
    """只把当前有效且未出现过的定位路径加入列表。"""
    if location_path not in active_paths:
        return
    if location_path in location_paths:
        return
    location_paths.append(location_path)


def _location_paths_from_quality_details(details: JsonArray) -> list[str]:
    """从质量明细数组提取定位路径。"""
    location_paths: list[str] = []
    for raw_detail in details:
        if not isinstance(raw_detail, dict):
            continue
        raw_location_path = raw_detail.get("location_path")
        if not isinstance(raw_location_path, str):
            continue
        location_paths.append(raw_location_path)
    return location_paths


def _resolve_quality_fix_translation_lines(
    *,
    location_path: str,
    quality_errors_by_path: dict[str, TranslationErrorItem],
    translated_by_path: dict[str, TranslationItem],
) -> list[str]:
    """决定质量修复模板中应预填的译文行。"""
    quality_error = quality_errors_by_path.get(location_path)
    if quality_error is not None:
        return list(quality_error.translation_lines)
    translated_item = translated_by_path.get(location_path)
    if translated_item is None:
        return []
    return list(translated_item.translation_lines)


def _count_active_quality_details(details: JsonArray, active_paths: set[str]) -> int:
    """统计属于当前提取范围的质量明细数量。"""
    return sum(
        1
        for location_path in _location_paths_from_quality_details(details)
        if location_path in active_paths
    )


def _preview_placeholder_sample(text_rules: TextRules, sample_text: str) -> JsonObject:
    """生成单条样本文本的占位符替换和还原预览。"""
    item = TranslationItem(
        location_path="placeholder-preview",
        item_type="short_text",
        original_lines=[sample_text],
    )
    item.build_placeholders(text_rules)
    item.translation_lines_with_placeholders = list(item.original_lines_with_placeholders)
    item.verify_placeholders(text_rules)
    item.restore_placeholders()
    placeholder_map: JsonObject = {
        placeholder: original
        for placeholder, original in item.placeholder_map.items()
    }
    text_for_model = ""
    if item.original_lines_with_placeholders:
        text_for_model = item.original_lines_with_placeholders[0]
    restored_text = ""
    if item.translation_lines:
        restored_text = item.translation_lines[0]
    return {
        "original_text": sample_text,
        "text_for_model": text_for_model,
        "restored_text": restored_text,
        "roundtrip_ok": restored_text == sample_text,
        "placeholder_map": placeholder_map,
    }


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


CUSTOM_MARKER_WITH_PARAMS_PATTERN: re.Pattern[str] = re.compile(
    r"^\\(?P<code>[A-Za-z]+)\d*\[[^\]\r\n]+\]$"
)
CUSTOM_MARKER_WITHOUT_PARAMS_PATTERN: re.Pattern[str] = re.compile(
    r"^\\(?P<code>[A-Za-z]+)\d*$"
)


def _build_custom_placeholder_rule_draft(
    candidates: Sequence[PlaceholderCandidate],
) -> dict[str, str]:
    """把未覆盖候选折叠成适合 Agent 编辑的规则草稿。"""
    draft_rules: dict[str, str] = {}
    for candidate in candidates:
        if candidate.standard_covered or candidate.custom_covered:
            continue
        pattern_text, placeholder_template = _draft_custom_placeholder_rule(candidate.marker)
        _ = draft_rules.setdefault(pattern_text, placeholder_template)
    return draft_rules


def _draft_custom_placeholder_rule(marker: str) -> tuple[str, str]:
    """为单个候选生成通用正则和合法语义化占位符模板。"""
    with_params_match = CUSTOM_MARKER_WITH_PARAMS_PATTERN.fullmatch(marker)
    if with_params_match is not None:
        code = with_params_match.group("code").upper()
        pattern_text = rf"(?i)\\{code}\d*\[[^\]\r\n]+\]"
        return pattern_text, _custom_placeholder_template_for_code(code)

    without_params_match = CUSTOM_MARKER_WITHOUT_PARAMS_PATTERN.fullmatch(marker)
    if without_params_match is not None:
        code = without_params_match.group("code").upper()
        pattern_text = rf"(?i)\\{code}\d*(?![A-Za-z\[])"
        return pattern_text, _custom_placeholder_template_for_code(code)

    return re.escape(marker), "[CUSTOM_UNKNOWN_CONTROL_MARKER_{index}]"


def _custom_placeholder_template_for_code(code: str) -> str:
    """按控制符前缀给出 Agent 可理解的默认占位符名称。"""
    semantic_names: dict[str, str] = {
        "F": "FACE_PORTRAIT",
        "FH": "FACE_PORTRAIT_HIDE",
        "AA": "PLUGIN_AA_MARKER",
        "AC": "PLUGIN_AC_MARKER",
        "AN": "PLUGIN_ACTOR_NAME_MARKER",
        "MT": "PLUGIN_MESSAGE_TAG",
    }
    semantic_name = semantic_names.get(code, f"PLUGIN_{code}_MARKER")
    return f"[CUSTOM_{semantic_name}_{{index}}]"


def _collect_placeholder_preview_samples(game_data: GameData, text_rules: TextRules) -> list[str]:
    """为占位符校验收集少量包含候选控制符的样本文本。"""
    samples: list[str] = []
    translation_data_map = DataTextExtraction(game_data, text_rules).extract_all_text()
    for translation_data in translation_data_map.values():
        for item in translation_data.translation_items:
            for text in item.original_lines:
                if not text_rules.iter_control_sequence_spans(text):
                    continue
                samples.append(text)
                if len(samples) >= 10:
                    return samples
    return samples


def _collect_unprotected_control_warning_samples(game_data: GameData, text_rules: TextRules) -> list[str]:
    """收集疑似存在裸露控制符边界风险的样本文本。"""
    samples: list[str] = []
    translation_data_map = DataTextExtraction(game_data, text_rules).extract_all_text()
    for translation_data in translation_data_map.values():
        for item in translation_data.translation_items:
            for text in item.original_lines:
                if not text_rules.iter_unprotected_control_sequence_candidates(text):
                    continue
                samples.append(text)
                if len(samples) >= 10:
                    return samples
    return samples


def _validate_name_registry(
    speaker_names: dict[str, str],
    map_display_names: dict[str, str],
) -> list[AgentIssue]:
    """检查术语表填写质量。"""
    warnings: list[AgentIssue] = []
    empty_count = sum(1 for value in [*speaker_names.values(), *map_display_names.values()] if not value.strip())
    if empty_count:
        warnings.append(issue("name_context_empty_translation", f"术语表存在 {empty_count} 个空译名"))
    translated_counter = Counter(value.strip() for value in speaker_names.values() if value.strip())
    duplicate_count = sum(1 for count in translated_counter.values() if count > 1)
    if duplicate_count:
        warnings.append(issue("name_context_duplicate_translation", f"术语表存在 {duplicate_count} 组重复译名，需要确认是否合理"))
    variant_mismatch_count = _count_name_variant_mismatches(speaker_names)
    if variant_mismatch_count:
        warnings.append(issue("name_context_variant_mismatch", f"名字框变体存在 {variant_mismatch_count} 处译名不一致风险"))
    return warnings


def _first_original_line_samples(items: Iterable[TranslationItem], limit: int = 5) -> JsonArray:
    """提取少量首行样例，避免报告输出完整上下文。"""
    samples: JsonArray = []
    for item in items:
        if not item.original_lines:
            continue
        samples.append(item.original_lines[0])
        if len(samples) >= limit:
            break
    return samples


def _preview_event_command_write_back(
    *,
    game_data: GameData,
    extracted_items: list[TranslationItem],
    text_rules: TextRules,
) -> None:
    """用规则命中项做内存回写预演，提前暴露路径不兼容问题。"""
    if not extracted_items:
        return
    probe_items: list[TranslationItem] = []
    for item in extracted_items:
        probe_item = item.model_copy(deep=True)
        probe_item.translation_lines = _build_write_back_probe_lines(item)
        probe_items.append(probe_item)

    reset_writable_copies(game_data)
    try:
        write_data_text(game_data, probe_items, text_rules=text_rules)
    finally:
        reset_writable_copies(game_data)


def _build_write_back_probe_lines(item: TranslationItem) -> list[str]:
    """按条目类型生成不会依赖模型结果的回写探针译文。"""
    if item.item_type == "array":
        return ["回写校验" for _line in item.original_lines]
    return ["回写校验"]


def _count_name_variant_mismatches(speaker_names: dict[str, str]) -> int:
    """检查带冒号或声音后缀的名字译名是否延续本体译名。"""
    mismatch_count = 0
    for source_text, translated_text in speaker_names.items():
        base_source = source_text.removesuffix("：").removesuffix(":").removesuffix("の声").strip()
        if base_source == source_text:
            continue
        base_translation = speaker_names.get(base_source)
        if base_translation and base_translation not in translated_text:
            mismatch_count += 1
    return mismatch_count


def _is_path_inside(path: Path, parent: Path) -> bool:
    """判断待删除路径是否位于工作区内部。"""
    try:
        _ = path.relative_to(parent)
        return True
    except ValueError:
        return False


def _collect_residual_items(
    items: list[TranslationItem],
    text_rules: TextRules,
    japanese_residual_rule_set: JapaneseResidualRuleSet,
) -> JsonArray:
    """收集仍存在日文残留风险的译文条目明细。"""
    details: JsonArray = []
    for item in items:
        try:
            check_japanese_residual_for_item(
                item=item,
                text_rules=text_rules,
                rule_set=japanese_residual_rule_set,
            )
        except ValueError as error:
            detail = _build_translation_item_quality_detail(item)
            detail["reason"] = str(error)
            allowed_terms = japanese_residual_rule_set.allowed_terms_for_path(item.location_path)
            if allowed_terms:
                detail["allowed_terms"] = _string_lines_to_json_array(allowed_terms)
                detail["exception_reason"] = japanese_residual_rule_set.reason_for_path(item.location_path)
            details.append(detail)
    return details


def _collect_placeholder_risk_items(items: list[TranslationItem], text_rules: TextRules) -> JsonArray:
    """收集占位符数量或未知控制符存在风险的译文条目明细。"""
    details: JsonArray = []
    for item in items:
        cloned_item = item.model_copy(deep=True)
        try:
            cloned_item.build_placeholders(text_rules)
            cloned_item.translation_lines_with_placeholders = [
                _mask_translation_controls(line=line, item=cloned_item, text_rules=text_rules)
                for line in cloned_item.translation_lines
            ]
            cloned_item.verify_placeholders(text_rules)
        except ValueError as error:
            detail = _build_translation_item_quality_detail(item)
            detail["reason"] = str(error)
            details.append(detail)
    return details


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


def _prepare_manual_translation_item(
    *,
    item: TranslationItem,
    translation_lines: list[str],
    text_rules: TextRules,
    japanese_residual_rule_set: JapaneseResidualRuleSet | None = None,
) -> TranslationItem:
    """把人工译文校验成可写入主译文缓存的条目。"""
    if not translation_lines or not any(line.strip() for line in translation_lines):
        raise ValueError("translation_lines 不能为空")
    if item.item_type == "short_text" and len(translation_lines) != 1:
        raise ValueError("short_text 必须提供 1 行译文")
    if item.item_type == "array" and len(translation_lines) != len(item.original_lines):
        raise ValueError(f"array 必须提供 {len(item.original_lines)} 行译文")
    visible_placeholders = text_rules.collect_placeholder_tokens(translation_lines)
    if visible_placeholders:
        joined_placeholders = "、".join(sorted(visible_placeholders))
        raise ValueError(f"translation_lines 必须使用游戏原始控制符，不得保留程序占位符: {joined_placeholders}")
    normalized_translation_lines = _normalize_manual_translation_lines(
        item=item,
        translation_lines=translation_lines,
        text_rules=text_rules,
    )

    cloned_item = item.model_copy(deep=True)
    cloned_item.build_placeholders(text_rules)
    cloned_item.translation_lines_with_placeholders = [
        _mask_translation_controls(line=line, item=cloned_item, text_rules=text_rules)
        for line in normalized_translation_lines
    ]
    cloned_item.verify_placeholders(text_rules)
    cloned_item.translation_lines = list(normalized_translation_lines)
    check_japanese_residual_for_item(
        item=cloned_item,
        text_rules=text_rules,
        rule_set=japanese_residual_rule_set,
    )
    return cloned_item


def _normalize_manual_translation_lines(
    *,
    item: TranslationItem,
    translation_lines: list[str],
    text_rules: TextRules,
) -> list[str]:
    """人工 long_text 入库前套用与写回一致的行宽兜底。"""
    if item.item_type != "long_text":
        return list(translation_lines)
    return split_overwide_lines(
        lines=list(translation_lines),
        location_path=item.location_path,
        text_rules=text_rules,
    )


def _collect_overwide_line_items(items: list[TranslationItem], text_rules: TextRules) -> JsonArray:
    """收集超过当前行宽上限的长文本译文行明细。"""
    limit = text_rules.setting.long_text_line_width_limit
    details: JsonArray = []
    for item in items:
        if item.item_type != "long_text":
            continue
        for index, line in enumerate(item.translation_lines):
            if not line:
                continue
            width = count_line_width_chars(line, text_rules)
            if width <= limit:
                continue
            detail = _build_translation_item_quality_detail(item)
            detail["line_index"] = index
            detail["line"] = line
            detail["line_width"] = width
            detail["line_width_limit"] = limit
            details.append(detail)
    return details


def _build_translation_item_quality_detail(item: TranslationItem) -> JsonObject:
    """把译文条目转换为质量报告中可定位、可修复的明细。"""
    return {
        "location_path": item.location_path,
        "item_type": item.item_type,
        "role": item.role,
        "original_lines": _string_lines_to_json_array(item.original_lines),
        "translation_lines": _string_lines_to_json_array(item.translation_lines),
    }


def _build_translation_error_quality_detail(item: TranslationErrorItem) -> JsonObject:
    """把译文质量错误转换为质量报告中可定位、可修复的明细。"""
    return {
        "location_path": item.location_path,
        "item_type": item.item_type,
        "role": item.role,
        "original_lines": _string_lines_to_json_array(item.original_lines),
        "translation_lines": _string_lines_to_json_array(item.translation_lines),
        "error_type": item.error_type,
        "error_detail": _string_lines_to_json_array(item.error_detail),
        "model_response": item.model_response,
    }


def _string_lines_to_json_array(lines: list[str]) -> JsonArray:
    """把字符串行列表收窄为 JSON 数组。"""
    return [line for line in lines]


__all__: list[str] = [
    "AgentToolkitService",
    "LlmCheckFunc",
    "run_default_llm_check",
]
