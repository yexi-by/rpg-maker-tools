"""Agent 工具箱 QualityAgentMixin 子服务。"""
# pyright: reportPrivateUsage=false
# mixin 通过 AgentToolkitService 组合成同一个服务边界，允许调用同门面的受保护核心方法。

from .common import *


class QualityAgentMixin:
    """承载 AgentToolkitService 的 QualityAgentMixin 命令族。"""

    async def export_quality_fix_template(
        self: AgentServiceContext,
        *,
        game_title: str,
        output_path: Path,
    ) -> AgentReport:
        """从质量报告问题生成可填写的修复表。"""
        async with await self.game_registry.open_game(game_title) as session:
            setting = load_setting(self.setting_path, source_language=session.source_language)
            custom_rules = await self._resolve_custom_rules(
                session=session,
                custom_placeholder_rules_text=None,
            )
            text_rules = TextRules.from_setting(
                setting.text_rules,
                custom_placeholder_rules=custom_rules,
            )
            game_data = await self._load_game_data(session)
            translated_items = await session.read_translated_items()
            scope = await TextScopeService().build(
                session=session,
                game_data=game_data,
                text_rules=text_rules,
                translated_items=translated_items,
            )
            blocking_errors = _text_scope_blocking_errors(scope)
            active_items = {
                item.location_path: item
                for item in scope.active_items()
            }
            active_paths = scope.writable_paths
            translated_by_path = {item.location_path: item for item in translated_items}
            translated_paths = set(translated_by_path)
            active_translated_items = [
                item
                for item in translated_items
                if item.location_path in active_paths
            ]
            latest_run = await session.read_latest_translation_run()
            if latest_run is None:
                quality_error_items: list[TranslationErrorItem] = []
            else:
                quality_error_items = await session.read_translation_quality_errors(latest_run.run_id)
            source_residual_rules = await session.read_source_residual_rules()

        if blocking_errors:
            return AgentReport.from_parts(
                errors=blocking_errors,
                warnings=[],
                summary={
                    "exported_count": 0,
                    "output": str(output_path),
                    "quality_error_count": 0,
                    "source_residual_count": 0,
                    "text_structure_count": 0,
                    "placeholder_risk_count": 0,
                    "overwide_line_count": 0,
                    "write_back_protocol_count": 0,
                },
                details={
                    "coverage": {
                        "stale_plugin_rules": scope.stale_plugin_rules_json(),
                        "write_back_probe_error": scope.write_back_probe_error,
                        "unwritable_items": [entry.to_json_object() for entry in scope.unwritable_entries],
                    }
                },
            )
        pending_paths = active_paths - translated_paths
        quality_error_items = [
            item
            for item in quality_error_items
            if item.location_path in pending_paths
        ]
        source_residual_rule_errors = _validate_source_residual_rule_records(source_residual_rules)
        if source_residual_rule_errors:
            return AgentReport.from_parts(
                errors=source_residual_rule_errors,
                warnings=[],
                summary={
                    "exported_count": 0,
                    "output": str(output_path),
                    "quality_error_count": len(quality_error_items),
                    "source_residual_count": 0,
                    "text_structure_count": 0,
                    "placeholder_risk_count": 0,
                    "overwide_line_count": 0,
                    "write_back_protocol_count": 0,
                },
                details={},
            )
        native_quality_details = collect_agent_service_native_quality_details(
            items=active_translated_items,
            text_rules=text_rules,
            source_residual_rules=source_residual_rules,
        )
        residual_details = native_quality_details.source_residual_items
        text_structure_details = native_quality_details.text_structure_items
        placeholder_details = native_quality_details.placeholder_risk_items
        overwide_details = native_quality_details.overwide_line_items
        write_back_protocol_details = collect_agent_service_native_write_protocol_details(
            game_data=game_data.data,
            plugins_js=[plugin for plugin in game_data.plugins_js],
            items=active_translated_items,
        )
        problem_paths = _collect_quality_fix_problem_paths(
            quality_error_items=quality_error_items,
            residual_details=residual_details,
            text_structure_details=text_structure_details,
            placeholder_details=placeholder_details,
            overwide_details=overwide_details,
            write_back_protocol_details=write_back_protocol_details,
            active_paths=active_paths,
        )
        quality_errors_by_path = {
            item.location_path: item
            for item in quality_error_items
        }
        categories_by_path = _build_quality_fix_categories_by_path(
            quality_error_items=quality_error_items,
            residual_details=residual_details,
            text_structure_details=text_structure_details,
            placeholder_details=placeholder_details,
            overwide_details=overwide_details,
            write_back_protocol_details=write_back_protocol_details,
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
                "source_residual_count": _count_active_quality_details(residual_details, active_paths),
                "text_structure_count": _count_active_quality_details(text_structure_details, active_paths),
                "placeholder_risk_count": _count_active_quality_details(placeholder_details, active_paths),
                "overwide_line_count": _count_active_quality_details(overwide_details, active_paths),
                "write_back_protocol_count": _count_active_quality_details(write_back_protocol_details, active_paths),
            },
            details={
                "location_paths": _string_lines_to_json_array(problem_paths),
                "problem_categories_by_path": categories_by_path,
            },
        )

    async def quality_report(
        self: AgentServiceContext,
        *,
        game_title: str,
        setting_overrides: SettingOverrides | None = None,
        callbacks: QualityProgressCallbacks | None = None,
    ) -> AgentReport:
        """生成目标游戏当前翻译状态和质量风险报告。"""
        set_progress, advance_progress, set_status = callbacks or _noop_quality_progress_callbacks()
        set_progress(0, 1)
        set_status("加载游戏数据和规则")
        errors: list[AgentIssue] = []
        warnings: list[AgentIssue] = []
        async with await self.game_registry.open_game(game_title) as session:
            setting = load_setting(
                self.setting_path,
                overrides=setting_overrides,
                source_language=session.source_language,
            )
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
            note_tag_rules = await session.read_note_tag_text_rules()
            source_residual_rules = await session.read_source_residual_rules()
            terminology_registry = await session.read_terminology_registry()
            latest_run = await session.read_latest_translation_run()
            translated_items = await session.read_translated_items()
            scope = await TextScopeService().build(
                session=session,
                game_data=game_data,
                text_rules=text_rules,
                translated_items=translated_items,
            )
            active_paths = scope.active_paths
            writable_paths = scope.writable_paths
            translated_paths = {item.location_path for item in translated_items}
            active_translated_items = [
                item
                for item in translated_items
                if item.location_path in active_paths
            ]
            pending_paths = writable_paths - translated_paths
            stale_paths = translated_paths - writable_paths
            stale_source_residual_rule_paths = {
                rule.location_path
                for rule in source_residual_rules
                if rule.rule_type == "position" and rule.location_path not in active_paths
            }
            coverage_report = _build_coverage_report(
                scope=scope,
                translated_items=translated_items,
                text_rules=text_rules,
            )
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
        source_residual_rule_errors = _validate_source_residual_rule_records(source_residual_rules)
        filled_terminology_count = 0
        total_terminology_count = 0
        empty_terminology_count = 0
        if terminology_registry is not None:
            total_terminology_count = terminology_registry.total_entry_count()
            filled_terminology_count = terminology_registry.filled_entry_count()
            empty_terminology_count = total_terminology_count - filled_terminology_count

        coverage_blocking_errors = _coverage_hard_stop_errors(coverage_report)
        if coverage_blocking_errors or source_residual_rule_errors:
            errors.extend(coverage_report.errors)
            warnings.extend(coverage_report.warnings)
            errors.extend(source_residual_rule_errors)
            set_progress(1, 1)
            set_status("覆盖审计未通过，质量报告已停止")
            return AgentReport.from_parts(
                errors=errors,
                warnings=warnings,
                summary={
                    "extractable_count": len(active_paths),
                    "translated_count": len(translated_paths & active_paths),
                    "pending_count": len(pending_paths),
                    "stale_translation_count": len(stale_paths),
                    "unwritable_count": len(scope.unwritable_entries),
                    "plugin_rule_count": sum(len(rule.path_templates) for rule in plugin_rules),
                    "stale_plugin_rule_count": stale_plugin_rule_count,
                    "event_command_rule_count": sum(len(rule.path_templates) for rule in event_rules),
                    "note_tag_rule_count": sum(len(rule.tag_names) for rule in note_tag_rules),
                    "source_language": session.source_language,
                    "target_language": session.target_language,
                    "source_residual_rule_count": len(source_residual_rules),
                    "stale_source_residual_rule_count": len(stale_source_residual_rule_paths),
                    "terminology_total_count": total_terminology_count,
                    "terminology_filled_count": filled_terminology_count,
                    "terminology_empty_count": empty_terminology_count,
                    "latest_run_id": latest_run.run_id if latest_run is not None else "",
                    "latest_run_status": latest_run.status if latest_run is not None else "",
                    "llm_failure_count": len(llm_failures),
                    "quality_error_count": len(quality_error_items),
                    "run_quality_error_count": run_quality_error_count,
                    "model_response_error_count": sum(1 for item in quality_error_items if item.model_response.strip()),
                    "source_residual_count": 0,
                    "text_structure_count": 0,
                    "placeholder_risk_count": 0,
                    "overwide_line_count": 0,
                    "write_back_protocol_count": 0,
                    "writable_translation_count": len(translated_paths & writable_paths),
                },
                details={
                    "error_type_counts": dict(Counter(item.error_type for item in quality_error_items)),
                    "llm_failure_counts": dict(Counter(failure.category for failure in llm_failures)),
                    "quality_error_items": [_build_translation_error_quality_detail(item) for item in quality_error_items],
                    "source_residual_items": [],
                    "text_structure_items": [],
                    "placeholder_risk_items": [],
                    "overwide_line_items": [],
                    "write_back_protocol_items": [],
                    "coverage": coverage_report.details,
                },
            )

        protocol_probe_count = _count_protocol_sensitive_translation_items(
            items=active_translated_items,
            active_paths=active_paths,
        )
        total_progress_steps = max(
            8
            + len(active_translated_items) * 4
            + protocol_probe_count
            + len(quality_error_items),
            1,
        )
        set_progress(0, total_progress_steps)
        report_status = f"检查 {len(active_translated_items)} 条已保存译文，还没成功保存译文 {len(pending_paths)} 条"
        set_status(report_status)
        advance_progress(1)

        set_status("整理模型检查失败记录")
        for _item in quality_error_items:
            advance_progress(1)
        set_status(f"调用 Rust 原生质检核心（{native_thread_count()} 线程）")
        native_quality_details = collect_agent_service_native_quality_details(
            items=active_translated_items,
            text_rules=text_rules,
            source_residual_rules=source_residual_rules,
        )
        residual_items = native_quality_details.source_residual_items
        text_structure_items = native_quality_details.text_structure_items
        placeholder_risk_items = native_quality_details.placeholder_risk_items
        overwide_line_items = native_quality_details.overwide_line_items
        advance_progress(len(active_translated_items) * 4)
        set_status("整理源文残留")
        residual_count = len(residual_items)
        set_status("检查写回协议")
        if scope.write_back_probe_error:
            write_back_protocol_items: JsonArray = []
        else:
            write_back_protocol_items = collect_agent_service_native_write_protocol_details(
                game_data=game_data.data,
                plugins_js=[plugin for plugin in game_data.plugins_js],
                items=active_translated_items,
            )
        advance_progress(protocol_probe_count)
        set_status("整理质量报告")
        advance_progress(1)
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
        advance_progress(1)
        errors.extend(coverage_report.errors)
        warnings.extend(coverage_report.warnings)
        if llm_failures and pending_paths:
            errors.append(issue("llm_failures", f"最新翻译运行存在 {len(llm_failures)} 条模型运行故障"))
        elif llm_failures:
            warnings.append(issue("historical_llm_failures", f"最新翻译运行记录过 {len(llm_failures)} 条模型故障，但当前没有正文因此无法继续"))
        if quality_error_items:
            errors.append(issue("translation_quality_errors", f"最新翻译运行有 {len(quality_error_items)} 条模型翻了但项目检查没通过的译文"))
        if placeholder_risk_items:
            errors.append(issue("placeholder_risk", f"发现 {len(placeholder_risk_items)} 条译文里的游戏控制符可能被改坏"))
        if residual_count:
            errors.append(issue("source_residual", f"发现 {residual_count} 条译文存在{setting.text_rules.source_residual_label}残留风险"))
        if text_structure_items:
            errors.append(issue("text_structure", f"发现 {len(text_structure_items)} 条译文改动了游戏文本结构"))
        if overwide_line_items:
            errors.append(issue("overwide_line", f"发现 {len(overwide_line_items)} 行译文超过当前长文本宽度上限"))
        if write_back_protocol_items:
            errors.append(issue("write_back_protocol", f"发现 {len(write_back_protocol_items)} 条译文写回后会破坏游戏或插件解析协议"))
        if terminology_registry is None:
            errors.append(issue("terminology_missing", "当前游戏尚未导入术语表"))
        elif empty_terminology_count:
            errors.append(issue("terminology_empty_translation", f"术语表还有 {empty_terminology_count} 个词条没有填写译名"))
        if stale_source_residual_rule_paths:
            errors.append(issue("stale_source_residual_rules", f"发现 {len(stale_source_residual_rule_paths)} 条不在当前提取范围内的源文残留例外规则"))

        set_progress(total_progress_steps, total_progress_steps)
        set_status("质量报告已完成")
        return AgentReport.from_parts(
            errors=errors,
            warnings=warnings,
            summary={
                "extractable_count": len(active_paths),
                "translated_count": len(translated_paths & active_paths),
                "pending_count": len(pending_paths),
                "stale_translation_count": len(stale_paths),
                "unwritable_count": len(scope.unwritable_entries),
                "plugin_rule_count": sum(len(rule.path_templates) for rule in plugin_rules),
                "stale_plugin_rule_count": stale_plugin_rule_count,
                "event_command_rule_count": sum(len(rule.path_templates) for rule in event_rules),
                "note_tag_rule_count": sum(len(rule.tag_names) for rule in note_tag_rules),
                "source_language": session.source_language,
                "target_language": session.target_language,
                "source_residual_rule_count": len(source_residual_rules),
                "stale_source_residual_rule_count": len(stale_source_residual_rule_paths),
                "terminology_total_count": total_terminology_count,
                "terminology_filled_count": filled_terminology_count,
                "terminology_empty_count": empty_terminology_count,
                "latest_run_id": latest_run.run_id if latest_run is not None else "",
                "latest_run_status": latest_run.status if latest_run is not None else "",
                "llm_failure_count": len(llm_failures),
                "quality_error_count": len(quality_error_items),
                "run_quality_error_count": run_quality_error_count,
                "model_response_error_count": model_response_count,
                "source_residual_count": residual_count,
                "text_structure_count": len(text_structure_items),
                "placeholder_risk_count": len(placeholder_risk_items),
                "overwide_line_count": len(overwide_line_items),
                "write_back_protocol_count": len(write_back_protocol_items),
                "writable_translation_count": len(translated_paths & writable_paths),
            },
            details={
                "error_type_counts": dict(error_type_counts),
                "llm_failure_counts": dict(llm_failure_counts),
                "quality_error_items": quality_error_details,
                "source_residual_items": residual_items,
                "text_structure_items": text_structure_items,
                "placeholder_risk_items": placeholder_risk_items,
                "overwide_line_items": overwide_line_items,
                "write_back_protocol_items": write_back_protocol_items,
                "coverage": coverage_report.details,
            },
        )

    async def translation_status(self: AgentServiceContext, *, game_title: str) -> AgentReport:
        """读取最新正文翻译运行状态，并补充当前还没成功保存译文的数量。"""
        async with await self.game_registry.open_game(game_title) as session:
            latest_run = await session.read_latest_translation_run()
            if latest_run is None:
                return AgentReport.from_parts(
                    errors=[],
                    warnings=[issue("translation_run_missing", "当前游戏尚未产生正文翻译运行记录")],
                    summary={},
                    details={},
                )
            setting = load_setting(self.setting_path, source_language=session.source_language)
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

    async def reset_translations(
        self: AgentServiceContext,
        *,
        game_title: str,
        input_path: Path | None = None,
        reset_all: bool = False,
    ) -> AgentReport:
        """删除已保存译文，使指定条目或当前提取范围全部条目重新交给模型翻译。"""
        if input_path is not None and reset_all:
            return AgentReport.from_parts(
                errors=[issue("reset_translation_source", "--input 与 --all 不能同时使用")],
                warnings=[],
                summary={
                    "input": str(input_path),
                    "mode": "invalid",
                    "requested_count": 0,
                    "reset_count": 0,
                },
                details={},
            )
        if input_path is None and not reset_all:
            return AgentReport.from_parts(
                errors=[issue("reset_translation_source", "必须通过 --input 或 --all 指定重置范围")],
                warnings=[],
                summary={
                    "input": "",
                    "mode": "invalid",
                    "requested_count": 0,
                    "reset_count": 0,
                },
                details={},
            )
        if input_path is not None:
            try:
                requested_paths = await _read_reset_translation_location_paths(input_path)
            except Exception as error:
                return AgentReport.from_parts(
                    errors=[issue("reset_translation_file", f"重置译文文件不可用: {type(error).__name__}: {error}")],
                    warnings=[],
                    summary={
                        "input": str(input_path),
                        "mode": "input",
                        "requested_count": 0,
                        "reset_count": 0,
                    },
                    details={},
                )
        else:
            requested_paths = []

        async with await self.game_registry.open_game(game_title) as session:
            setting = load_setting(self.setting_path, source_language=session.source_language)
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
            active_location_paths = _collect_active_translation_location_paths(translation_data_map.values())
            active_paths = set(active_location_paths)
            location_paths = active_location_paths if reset_all else requested_paths
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
                        "input": str(input_path) if input_path is not None else "",
                        "mode": "all" if reset_all else "input",
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
            warnings.append(issue("reset_translation_already_pending", f"{already_pending_count} 个定位路径当前没有已保存译文"))
        if reset_all and not location_paths:
            warnings.append(issue("reset_translation_no_active_items", "当前提取范围没有可重置条目"))
        if reset_all:
            details: JsonObject = {
                "location_path_count": len(location_paths),
                "location_path_samples": _string_lines_to_json_array(location_paths[:20]),
            }
        else:
            details = {
                "location_paths": _string_lines_to_json_array(location_paths),
            }
        return AgentReport.from_parts(
            errors=[],
            warnings=warnings,
            summary={
                "input": str(input_path) if input_path is not None else "",
                "mode": "all" if reset_all else "input",
                "requested_count": len(location_paths),
                "reset_count": reset_count,
            },
            details=details,
        )
