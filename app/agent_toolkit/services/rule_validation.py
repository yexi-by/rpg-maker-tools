"""Agent 工具箱 RuleValidationAgentMixin 子服务。"""
# pyright: reportPrivateUsage=false
# mixin 通过 AgentToolkitService 组合成同一个服务边界，允许调用同门面的受保护核心方法。

from .common import *


class RuleValidationAgentMixin:
    """承载 AgentToolkitService 的 RuleValidationAgentMixin 命令族。"""

    async def export_note_tag_candidates(
        self: AgentServiceContext,
        *,
        game_title: str,
        output_path: Path,
    ) -> AgentReport:
        """导出标准 data JSON Note 标签候选，供外部 Agent 判断可见文本标签。"""
        async with await self.game_registry.open_game(game_title) as session:
            setting = load_setting(self.setting_path, source_language=session.source_language)
            custom_rules = await self._resolve_custom_rules(
                session=session,
                custom_placeholder_rules_text=None,
            )
            text_rules = TextRules.from_setting(setting.text_rules, custom_placeholder_rules=custom_rules)
            game_data = await self._load_game_data(session)
        report = await export_note_tag_candidates_file(
            game_data=game_data,
            output_path=output_path,
            text_rules=text_rules,
        )
        warnings: list[AgentIssue] = []
        if report.candidate_tag_count == 0:
            warnings.append(issue("note_tag_candidates_empty", "当前游戏没有发现 data Note 标签候选"))
        return AgentReport.from_parts(
            errors=[],
            warnings=warnings,
            summary={
                "candidate_tag_count": report.candidate_tag_count,
                "candidate_value_count": report.candidate_value_count,
                "translatable_value_count": report.translatable_value_count,
                "output": str(output_path),
            },
            details=report.details,
        )

    async def validate_note_tag_rules(self: AgentServiceContext, *, game_title: str, rules_text: str) -> AgentReport:
        """校验 Note 标签规则 JSON 文本并报告命中情况。"""
        errors: list[AgentIssue] = []
        warnings: list[AgentIssue] = []
        details: JsonObject = {"rules": []}
        try:
            import_file = parse_note_tag_rule_import_text(rules_text)
            async with await self.game_registry.open_game(game_title) as session:
                setting = load_setting(self.setting_path, source_language=session.source_language)
                custom_rules = await self._resolve_custom_rules(
                    session=session,
                    custom_placeholder_rules_text=None,
                )
                text_rules = TextRules.from_setting(setting.text_rules, custom_placeholder_rules=custom_rules)
                game_data = await self._load_game_data(session)
                translated_paths: set[str] = await session.read_translation_location_paths()
            records = build_note_tag_rule_records_from_import(
                game_data=game_data,
                import_file=import_file,
                text_rules=text_rules,
            )
            extracted_map = NoteTagTextExtraction(
                game_data=game_data,
                rule_records=records,
                text_rules=text_rules,
            ).extract_all_text()
            extracted_items = [
                item
                for translation_data in extracted_map.values()
                for item in translation_data.translation_items
            ]
            unwritable_items = _collect_write_protocol_unwritable_items(
                game_data=game_data,
                extracted_items=extracted_items,
            )
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
                        "note_tag_write_back_invalid",
                        f"Note 标签规则命中项无法回写: {type(error).__name__}: {error}",
                    )
                )
                details["write_back_preview"] = {
                    "checked_item_count": len(extracted_items),
                    "status": "error",
                    "reason": f"{type(error).__name__}: {error}",
                }
            if unwritable_items:
                errors.append(issue("note_tag_write_back_unwritable", f"Note 标签规则存在 {len(unwritable_items)} 个不可写命中项"))
            unwritable_items_by_path = _json_items_by_location_path(unwritable_items)
            details["rules"] = [
                {
                    "file_name": record.file_name,
                    "tag_count": len(record.tag_names),
                    "tag_names": list(record.tag_names),
                    **_build_rule_metric_detail(
                        record_items=record_items,
                        translated_paths=translated_paths,
                        unwritable_items_by_path=unwritable_items_by_path,
                    ),
                }
                for record in records
                for record_items in [[
                    item
                    for item in extracted_items
                    if _note_tag_item_matches_rule(item=item, rule_record=record)
                ]]
            ]
            if not records:
                warnings.append(issue("note_tag_rules_empty", "Note 标签规则为空"))
        except Exception as error:
            errors.append(issue("note_tag_rules_invalid", f"Note 标签规则不可导入: {type(error).__name__}: {error}"))
            records = []
            extracted_items = []
            translated_paths = set()
            unwritable_items = []
        return AgentReport.from_parts(
            errors=errors,
            warnings=warnings,
            summary={
                "file_count": len(records),
                "tag_count": sum(len(record.tag_names) for record in records),
                "hit_count": len(extracted_items),
                "extractable_count": len(extracted_items),
                "translated_count": sum(1 for item in extracted_items if item.location_path in translated_paths),
                "writable_count": len(extracted_items) - len(unwritable_items),
                "unwritable_count": len(unwritable_items),
            },
            details=details,
        )

    async def import_note_tag_rules(self: AgentServiceContext, *, game_title: str, rules_text: str) -> AgentReport:
        """校验并导入当前游戏的 Note 标签文本规则。"""
        try:
            import_file = parse_note_tag_rule_import_text(rules_text)
            async with await self.game_registry.open_game(game_title) as session:
                setting = load_setting(self.setting_path, source_language=session.source_language)
                custom_rules = await self._resolve_custom_rules(
                    session=session,
                    custom_placeholder_rules_text=None,
                )
                text_rules = TextRules.from_setting(setting.text_rules, custom_placeholder_rules=custom_rules)
                game_data = await self._load_game_data(session)
                records = build_note_tag_rule_records_from_import(
                    game_data=game_data,
                    import_file=import_file,
                    text_rules=text_rules,
                )
                old_records = await session.read_note_tag_text_rules()
                old_note_paths = collect_translation_data_paths(
                    NoteTagTextExtraction(
                        game_data=game_data,
                        rule_records=old_records,
                        text_rules=text_rules,
                    ).extract_all_text()
                )
                new_note_paths = collect_translation_data_paths(
                    NoteTagTextExtraction(
                        game_data=game_data,
                        rule_records=records,
                        text_rules=text_rules,
                    ).extract_all_text()
                )
                stale_paths = sorted(old_note_paths - new_note_paths)
                deleted_translation_items = 0
                if stale_paths:
                    deleted_translation_items = await session.delete_translation_items_by_paths(stale_paths)
                await session.replace_note_tag_text_rules(records)
        except Exception as error:
            return AgentReport.from_parts(
                errors=[issue("note_tag_rules_invalid", f"Note 标签规则不可导入: {type(error).__name__}: {error}")],
                warnings=[],
                summary={"file_count": 0, "tag_count": 0, "deleted_translation_items": 0},
                details={},
            )
        return AgentReport.from_parts(
            errors=[],
            warnings=[] if records else [issue("note_tag_rules_empty", "已导入空 Note 标签规则")],
            summary={
                "file_count": len(records),
                "tag_count": sum(len(record.tag_names) for record in records),
                "deleted_translation_items": deleted_translation_items,
            },
            details={
                "rules": [
                    {
                        "file_name": record.file_name,
                        "tag_names": list(record.tag_names),
                    }
                    for record in records
                ]
            },
        )

    async def validate_source_residual_rules(self: AgentServiceContext, *, game_title: str, rules_text: str) -> AgentReport:
        """校验源文残留例外规则 JSON 文本并报告命中情况。"""
        errors: list[AgentIssue] = []
        warnings: list[AgentIssue] = []
        details: JsonObject = {"rules": []}
        try:
            records = await self._build_source_residual_rule_records(
                game_title=game_title,
                rules_text=rules_text,
            )
            details["rules"] = [
                {
                    "rule_id": record.rule_id,
                    "rule_type": record.rule_type,
                    "location_path": record.location_path,
                    "pattern": record.pattern_text,
                    "allowed_terms": list(record.allowed_terms),
                    "check_group": record.check_group,
                    "reason": record.reason,
                }
                for record in records
            ]
            if not records:
                warnings.append(issue("source_residual_rules_empty", "源文残留例外规则为空"))
        except Exception as error:
            errors.append(issue("source_residual_rules_invalid", f"源文残留例外规则不可导入: {type(error).__name__}: {error}"))
            records = []
        return AgentReport.from_parts(
            errors=errors,
            warnings=warnings,
            summary={
                "rule_count": len(records),
                "position_rule_count": sum(1 for record in records if record.rule_type == "position"),
                "structural_rule_count": sum(1 for record in records if record.rule_type == "structural"),
                "term_count": sum(len(record.allowed_terms) for record in records),
            },
            details=details,
        )

    async def import_source_residual_rules(self: AgentServiceContext, *, game_title: str, rules_text: str) -> AgentReport:
        """校验并导入当前游戏的源文残留例外规则。"""
        try:
            records = await self._build_source_residual_rule_records(
                game_title=game_title,
                rules_text=rules_text,
            )
            async with await self.game_registry.open_game(game_title) as session:
                await session.replace_source_residual_rules(records)
        except Exception as error:
            return AgentReport.from_parts(
                errors=[issue("source_residual_rules_invalid", f"源文残留例外规则不可导入: {type(error).__name__}: {error}")],
                warnings=[],
                summary={"rule_count": 0, "position_rule_count": 0, "structural_rule_count": 0, "term_count": 0},
                details={},
            )
        return AgentReport.from_parts(
            errors=[],
            warnings=[] if records else [issue("source_residual_rules_empty", "已导入空源文残留例外规则")],
            summary={
                "rule_count": len(records),
                "position_rule_count": sum(1 for record in records if record.rule_type == "position"),
                "structural_rule_count": sum(1 for record in records if record.rule_type == "structural"),
                "term_count": sum(len(record.allowed_terms) for record in records),
            },
            details={
                "rules": [
                    {
                        "rule_id": record.rule_id,
                        "rule_type": record.rule_type,
                        "location_path": record.location_path,
                        "pattern": record.pattern_text,
                        "allowed_terms": list(record.allowed_terms),
                        "check_group": record.check_group,
                        "reason": record.reason,
                    }
                    for record in records
                ]
            },
        )

    async def validate_plugin_rules(self: AgentServiceContext, *, game_title: str, rules_text: str) -> AgentReport:
        """校验插件规则 JSON 文本并报告命中情况。"""
        errors: list[AgentIssue] = []
        warnings: list[AgentIssue] = []
        details: JsonObject = {"rules": []}
        try:
            import_file = parse_plugin_rule_import_text(rules_text)
            async with await self.game_registry.open_game(game_title) as session:
                setting = load_setting(self.setting_path, source_language=session.source_language)
                custom_rules = await self._resolve_custom_rules(
                    session=session,
                    custom_placeholder_rules_text=None,
                )
                game_data = await self._load_game_data(session)
                translated_paths: set[str] = await session.read_translation_location_paths()
            records = build_plugin_rule_records_from_import(game_data=game_data, import_file=import_file)
            text_rules = TextRules.from_setting(setting.text_rules, custom_placeholder_rules=custom_rules)
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
            unwritable_items = _collect_write_protocol_unwritable_items(
                game_data=game_data,
                extracted_items=extracted_items,
            )
            if unwritable_items:
                errors.append(issue("plugin_rules_unwritable", f"插件规则存在 {len(unwritable_items)} 个不可写命中项"))
            unwritable_items_by_path = _json_items_by_location_path(unwritable_items)
            details["rules"] = [
                {
                    "plugin_index": record.plugin_index,
                    "plugin_name": record.plugin_name,
                    "plugin_hash": record.plugin_hash,
                    "path_count": len(record.path_templates),
                    "paths": list(record.path_templates),
                    **_build_rule_metric_detail(
                        record_items=record_items,
                        translated_paths=translated_paths,
                        unwritable_items_by_path=unwritable_items_by_path,
                    ),
                }
                for record in records
                for record_items in [[
                    item
                    for item in extracted_items
                    if item.location_path.startswith(f"{PLUGINS_FILE_NAME}/{record.plugin_index}/")
                ]]
            ]
            if not records:
                warnings.append(issue("plugin_rules_empty", "插件规则为空"))
            if records and not extracted_items:
                warnings.append(issue("plugin_rules_no_hits", "插件规则没有提取到任何可翻译文本"))
        except Exception as error:
            errors.append(issue("plugin_rules_invalid", f"插件规则不可导入: {type(error).__name__}: {error}"))
            records = []
            extracted_items = []
            translated_paths = set()
            unwritable_items = []
        return AgentReport.from_parts(
            errors=errors,
            warnings=warnings,
            summary={
                "plugin_count": len(records),
                "rule_count": sum(len(record.path_templates) for record in records),
                "hit_count": len(extracted_items),
                "extractable_count": len(extracted_items),
                "translated_count": sum(1 for item in extracted_items if item.location_path in translated_paths),
                "writable_count": len(extracted_items) - len(unwritable_items),
                "unwritable_count": len(unwritable_items),
            },
            details=details,
        )

    async def validate_event_command_rules(self: AgentServiceContext, *, game_title: str, rules_text: str) -> AgentReport:
        """校验事件指令规则 JSON 文本并报告命中情况。"""
        errors: list[AgentIssue] = []
        warnings: list[AgentIssue] = []
        details: JsonObject = {"rules": []}
        try:
            import_file = parse_event_command_rule_import_text(rules_text)
            async with await self.game_registry.open_game(game_title) as session:
                setting = load_setting(self.setting_path, source_language=session.source_language)
                custom_rules = await self._resolve_custom_rules(
                    session=session,
                    custom_placeholder_rules_text=None,
                )
                game_data = await self._load_game_data(session)
                translated_paths: set[str] = await session.read_translation_location_paths()
            records = build_event_command_rule_records_from_import(game_data=game_data, import_file=import_file)
            text_rules = TextRules.from_setting(setting.text_rules, custom_placeholder_rules=custom_rules)
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
            unwritable_items = _collect_write_protocol_unwritable_items(
                game_data=game_data,
                extracted_items=extracted_items,
            )
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
            if unwritable_items:
                errors.append(issue("event_command_rules_unwritable", f"事件指令规则存在 {len(unwritable_items)} 个不可写命中项"))
            unwritable_items_by_path = _json_items_by_location_path(unwritable_items)
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
                        **_build_rule_metric_detail(
                            record_items=record_items,
                            translated_paths=translated_paths,
                            unwritable_items_by_path=unwritable_items_by_path,
                        ),
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
            extracted_items = []
            translated_paths = set()
            unwritable_items = []
        return AgentReport.from_parts(
            errors=errors,
            warnings=warnings,
            summary={
                "rule_group_count": len(records),
                "path_rule_count": sum(len(record.path_templates) for record in records),
                "hit_count": len(extracted_items),
                "extractable_count": len(extracted_items),
                "translated_count": sum(1 for item in extracted_items if item.location_path in translated_paths),
                "writable_count": len(extracted_items) - len(unwritable_items),
                "unwritable_count": len(unwritable_items),
            },
            details=details,
        )
