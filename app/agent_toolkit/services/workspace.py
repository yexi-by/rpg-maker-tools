"""Agent 工具箱 WorkspaceAgentMixin 子服务。"""
# pyright: reportPrivateUsage=false
# mixin 通过 AgentToolkitService 组合成同一个服务边界，允许调用同门面的受保护核心方法。

from .common import *


class WorkspaceAgentMixin:
    """承载 AgentToolkitService 的 WorkspaceAgentMixin 命令族。"""

    async def scan_plugin_source_text(self: AgentServiceContext, *, game_title: str, output_path: Path) -> AgentReport:
        """扫描插件源码中的硬编码文本候选，只输出候选不自动判断语义。"""
        async with await self.game_registry.open_game(game_title) as session:
            game_data = await self._load_game_data(session)
        candidates = await _collect_plugin_source_text_candidates(game_data.layout.js_dir)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(output_path, "w", encoding="utf-8") as file:
            _ = await file.write(f"{json.dumps(candidates, ensure_ascii=False, indent=2)}\n")
        warnings: list[AgentIssue] = []
        if not candidates:
            warnings.append(issue("plugin_source_text_empty", "没有扫描到插件源码硬编码文本候选"))
        return AgentReport.from_parts(
            errors=[],
            warnings=warnings,
            summary={
                "candidate_count": len(candidates),
                "output": str(output_path),
            },
            details={
                "candidates": candidates[:50],
            },
        )

    async def prepare_agent_workspace(
        self: AgentServiceContext,
        *,
        game_title: str,
        output_dir: Path,
        command_codes: set[int] | None,
    ) -> AgentReport:
        """导出 Agent 分析所需的全部临时输入文件并生成 manifest。"""
        target_dir = output_dir.resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        async with await self.game_registry.open_game(game_title) as session:
            setting = load_setting(self.setting_path, source_language=session.source_language)
            game_data = await self._load_game_data(session)
            terminology_registry = await session.read_terminology_registry()
            terminology_glossary = await session.read_terminology_glossary()
            plugin_rules, stale_plugin_rule_count = await self._read_fresh_plugin_text_rules(
                session=session,
                game_data=game_data,
            )
            note_tag_rules = await session.read_note_tag_text_rules()
            event_rules = await session.read_event_command_text_rules()
            placeholder_records = await session.read_placeholder_rules()
            custom_rules = await self._resolve_custom_rules(session=session, custom_placeholder_rules_text=None)
            text_rules = TextRules.from_setting(setting.text_rules, custom_placeholder_rules=custom_rules)
            translation_data_map = await self._extract_active_translation_data_map(
                session=session,
                game_data=game_data,
                text_rules=text_rules,
            )
        terminology_summary = await export_terminology_artifacts(game_data=game_data, output_dir=target_dir / "terminology")
        if terminology_registry is not None:
            exported_registry = await load_terminology_registry(field_terms_path=terminology_summary.field_terms_path)
            merged_registry = _merge_terminology_registry(
                exported_registry=exported_registry,
                stored_registry=terminology_registry,
            )
            await write_field_terms_json(terminology_summary.field_terms_path, merged_registry)
        if terminology_glossary is not None:
            await write_glossary_json(terminology_summary.glossary_path, terminology_glossary)
        terminology_subtasks_dir = target_dir / "terminology" / "subtasks"
        terminology_subtask_summary = await _write_terminology_subtask_files(
            field_terms_path=terminology_summary.field_terms_path,
            subtasks_dir=terminology_subtasks_dir,
        )
        plugins_path = target_dir / "plugins.json"
        await export_plugins_json_file(game_data=game_data, output_path=plugins_path)
        plugin_rules_path = target_dir / "plugin-rules.json"
        await _write_json_value(plugin_rules_path, _plugin_rule_records_to_import_json(plugin_rules))
        note_tag_candidates_path = target_dir / "note-tag-candidates.json"
        note_tag_report = await export_note_tag_candidates_file(
            game_data=game_data,
            output_path=note_tag_candidates_path,
            text_rules=text_rules,
        )
        note_tag_rules_path = target_dir / "note-tag-rules.json"
        await _write_json_object(note_tag_rules_path, _note_tag_rule_records_to_import_json(note_tag_rules))
        default_command_codes = (
            None
            if command_codes is not None
            else setting.event_command_text.default_codes_for_engine(game_data.layout.engine_kind)
        )
        effective_codes = resolve_event_command_codes(command_codes=command_codes, default_command_codes=default_command_codes)
        event_commands_path = target_dir / "event-commands.json"
        event_command_count = await export_event_commands_json_file(
            game_data=game_data,
            output_path=event_commands_path,
            command_codes=effective_codes,
        )
        event_rules_path = target_dir / "event-command-rules.json"
        await _write_json_object(event_rules_path, _event_command_rule_records_to_import_json(event_rules))
        placeholder_candidates = scan_placeholder_candidates(translation_data_map, text_rules)
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
        placeholder_rule_payload: JsonObject = (
            _placeholder_rule_records_to_import_json(placeholder_records)
            if placeholder_records
            else {key: value for key, value in placeholder_rule_drafts.items()}
        )
        await _write_json_object(placeholder_rules_path, placeholder_rule_payload)
        generated_summary: JsonObject = {
            "engine": game_data.layout.engine_label,
            "engine_kind": game_data.layout.engine_kind,
            "engine_version": game_data.layout.engine_version,
            "source_language": session.source_language,
            "target_language": session.target_language,
            "content_root": str(game_data.layout.content_root),
            "data_dir": str(game_data.layout.data_dir),
            "event_command_codes": list(sorted(effective_codes)),
            "speaker_entry_count": terminology_summary.speaker_entry_count,
            "map_entry_count": terminology_summary.map_entry_count,
            "terminology_entry_count": terminology_summary.entry_count,
            "terminology_database_entry_count": terminology_summary.database_entry_count,
            "terminology_subtask_count": len(TERMINOLOGY_SUBTASK_GROUPS),
            "glossary_term_count": terminology_glossary.term_count() if terminology_glossary is not None else 0,
            "plugin_count": len(game_data.plugins_js),
            "plugin_rule_count": sum(len(rule.path_templates) for rule in plugin_rules),
            "stale_plugin_rule_count": stale_plugin_rule_count,
            "note_tag_candidate_count": note_tag_report.candidate_tag_count,
            "note_tag_rule_count": sum(len(rule.tag_names) for rule in note_tag_rules),
            "event_command_count": event_command_count,
            "event_command_rule_count": sum(len(rule.path_templates) for rule in event_rules),
            "placeholder_rule_count": len(placeholder_records),
            "placeholder_rule_draft_count": len(placeholder_rule_drafts),
        }
        manifest_files: JsonArray = [
            str(terminology_summary.field_terms_path),
            str(terminology_summary.glossary_path),
            str(terminology_summary.contexts_dir),
            str(terminology_subtasks_dir),
            str(plugins_path),
            str(plugin_rules_path),
            str(note_tag_candidates_path),
            str(note_tag_rules_path),
            str(event_commands_path),
            str(event_rules_path),
            str(placeholder_path),
            str(placeholder_rules_path),
        ]
        manifest: JsonObject = {
            "files": manifest_files,
            "generated": generated_summary,
            "layout": {
                "engine": game_data.layout.engine_label,
                "engine_kind": game_data.layout.engine_kind,
                "engine_version": game_data.layout.engine_version,
                "game_root": str(game_data.layout.game_root),
                "content_root": str(game_data.layout.content_root),
                "data_dir": str(game_data.layout.data_dir),
                "js_dir": str(game_data.layout.js_dir),
                "plugins_path": str(game_data.layout.plugins_path),
            },
            "workflow": _agent_workflow_manifest(terminology_subtask_summary),
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

    async def validate_agent_workspace(self: AgentServiceContext, *, game_title: str, workspace: Path) -> AgentReport:
        """检查 Agent 临时工作区里的可导入文件。"""
        errors: list[AgentIssue] = []
        warnings: list[AgentIssue] = []
        details: JsonObject = {}
        field_terms_path = workspace / "terminology" / "field-terms.json"
        glossary_path = workspace / "terminology" / "glossary.json"
        plugin_rules_path = workspace / "plugin-rules.json"
        note_tag_rules_path = workspace / "note-tag-rules.json"
        event_rules_path = workspace / "event-command-rules.json"
        placeholder_rules_path = workspace / "placeholder-rules.json"
        if field_terms_path.exists():
            registry: TerminologyRegistry | None = None
            try:
                registry = await load_terminology_registry(field_terms_path=field_terms_path)
                async with await self.game_registry.open_game(game_title) as session:
                    game_data = await self._load_game_data(session)
                expected_registry, _speaker_contexts, _database_contexts = TerminologyExtraction(
                    game_data=game_data,
                ).extract_registry_and_contexts()
                _validate_terminology_registry_shape(
                    imported_registry=registry,
                    expected_registry=expected_registry,
                    errors=errors,
                )
            except Exception as error:
                errors.append(issue("terminology_validate_failed", f"术语表结构校验失败: {type(error).__name__}: {error}"))
            if registry is not None:
                terminology_issues = _validate_terminology_registry(registry)
                errors.extend(issue_item for issue_item in terminology_issues if issue_item.code == "terminology_empty_translation")
                warnings.extend(issue_item for issue_item in terminology_issues if issue_item.code != "terminology_empty_translation")
                details["terminology"] = {
                    "entry_count": registry.total_entry_count(),
                    "filled_count": registry.filled_entry_count(),
                    "speaker_count": len(registry.speaker_names),
                    "map_count": len(registry.map_display_names),
                }
        else:
            errors.append(issue("terminology_missing", "工作区缺少 terminology/field-terms.json"))
        if glossary_path.exists():
            glossary: TerminologyGlossary | None = None
            try:
                glossary = await load_terminology_glossary(glossary_path=glossary_path)
            except Exception as error:
                errors.append(issue("glossary_validate_failed", f"正文术语表结构校验失败: {type(error).__name__}: {error}"))
            if glossary is not None:
                details["glossary"] = {
                    "term_count": glossary.term_count(),
                }
        else:
            errors.append(issue("glossary_missing", "工作区缺少 terminology/glossary.json"))
        if plugin_rules_path.exists():
            async with aiofiles.open(plugin_rules_path, "r", encoding="utf-8") as file:
                plugin_report = await self.validate_plugin_rules(game_title=game_title, rules_text=await file.read())
            errors.extend(plugin_report.errors)
            warnings.extend(plugin_report.warnings)
            details["plugin_rules"] = plugin_report.details
        else:
            warnings.append(issue("plugin_rules_missing", "工作区缺少 plugin-rules.json"))
        if note_tag_rules_path.exists():
            async with aiofiles.open(note_tag_rules_path, "r", encoding="utf-8") as file:
                note_tag_report = await self.validate_note_tag_rules(game_title=game_title, rules_text=await file.read())
            errors.extend(note_tag_report.errors)
            warnings.extend(note_tag_report.warnings)
            details["note_tag_rules"] = note_tag_report.details
        else:
            errors.append(issue("note_tag_rules_missing", "工作区缺少 note-tag-rules.json"))
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
                placeholder_rules_text = await file.read()
                placeholder_report = await self.validate_placeholder_rules(
                    game_title=game_title,
                    custom_placeholder_rules_text=placeholder_rules_text,
                    sample_texts=[],
                )
            errors.extend(placeholder_report.errors)
            warnings.extend(placeholder_report.warnings)
            details["placeholder_rules"] = placeholder_report.details
            try:
                placeholder_coverage_report = await self.scan_placeholder_candidates(
                    game_title=game_title,
                    custom_placeholder_rules_text=placeholder_rules_text,
                )
                errors.extend(placeholder_coverage_report.errors)
                details["placeholder_coverage"] = {
                    "summary": placeholder_coverage_report.summary,
                    "details": placeholder_coverage_report.details,
                }
                uncovered_value = placeholder_coverage_report.summary.get("uncovered_count")
                if isinstance(uncovered_value, bool) or not isinstance(uncovered_value, int):
                    errors.append(issue("placeholder_coverage_invalid", "占位符候选扫描缺少有效的 uncovered_count"))
                elif uncovered_value > 0:
                    errors.append(
                        issue(
                            "placeholder_coverage_uncovered",
                            f"还有 {uncovered_value} 个当前正文会使用但未被规则覆盖的游戏控制符",
                        )
                    )
            except Exception as error:
                errors.append(
                    issue(
                        "placeholder_coverage_scan_failed",
                        f"占位符覆盖扫描失败: {type(error).__name__}: {error}",
                    )
                )
        else:
            warnings.append(issue("placeholder_rules_missing", "工作区缺少 placeholder-rules.json"))
        return AgentReport.from_parts(errors=errors, warnings=warnings, summary={"workspace": str(workspace)}, details=details)

    async def cleanup_agent_workspace(self: AgentServiceContext, *, workspace: Path) -> AgentReport:
        """按 manifest 删除 Agent 临时工作区文件。"""
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
