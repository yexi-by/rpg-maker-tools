"""Agent 工具箱 ManualTranslationAgentMixin 子服务。"""
# pyright: reportPrivateUsage=false
# mixin 通过 AgentToolkitService 组合成同一个服务边界，允许调用同门面的受保护核心方法。

from .common import *


class ManualTranslationAgentMixin:
    """承载 AgentToolkitService 的 ManualTranslationAgentMixin 命令族。"""

    async def export_pending_translations(
        self: AgentServiceContext,
        *,
        game_title: str,
        output_path: Path,
        limit: int | None,
    ) -> AgentReport:
        """导出还没成功保存译文的条目，供 Agent 手动填写译文。"""
        async with await self.game_registry.open_game(game_title) as session:
            setting = load_setting(self.setting_path, source_language=session.source_language)
            custom_rules = await self._resolve_custom_rules(
                session=session,
                custom_placeholder_rules_text=None,
            )
            text_rules = TextRules.from_setting(setting.text_rules, custom_placeholder_rules=custom_rules)
            game_data = await self._load_game_data(session)
            translated_items = await session.read_translated_items()
            scope = await TextScopeService().build(
                session=session,
                game_data=game_data,
                text_rules=text_rules,
                translated_items=translated_items,
            )
            translated_paths = {item.location_path for item in translated_items}
        blocking_errors = _text_scope_blocking_errors(scope)
        if blocking_errors:
            return AgentReport.from_parts(
                errors=blocking_errors,
                warnings=[],
                summary={
                    "pending_exported_count": 0,
                    "output": str(output_path),
                },
                details={},
            )

        pending_items = [
            item
            for translation_data in scope.translation_data_map.values()
            for item in translation_data.translation_items
            if item.location_path in scope.writable_paths and item.location_path not in translated_paths
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
            warnings.append(issue("pending_empty", "当前没有需要手动填写译文的条目"))
        return AgentReport.from_parts(
            errors=[],
            warnings=warnings,
            summary={
                "pending_exported_count": len(pending_items),
                "output": str(output_path),
            },
            details={},
        )

    async def import_manual_translations(self: AgentServiceContext, *, game_title: str, input_path: Path) -> AgentReport:
        """导入 Agent 手动填写的译文，并按项目规则校验后保存。"""
        try:
            async with aiofiles.open(input_path, "r", encoding="utf-8-sig") as file:
                raw_payload = cast(object, json.loads(await file.read()))
            payload = ensure_json_object(coerce_json_value(raw_payload), "manual-translations")
        except Exception as error:
            return AgentReport.from_parts(
                errors=[issue("manual_translation_file", f"手动填写译文表不可读: {type(error).__name__}: {error}")],
                warnings=[],
                summary={"input": str(input_path), "imported_count": 0},
                details={},
            )

        errors: list[AgentIssue] = []
        valid_items: list[TranslationItem] = []
        async with await self.game_registry.open_game(game_title) as session:
            setting = load_setting(self.setting_path, source_language=session.source_language)
            custom_rules = await self._resolve_custom_rules(
                session=session,
                custom_placeholder_rules_text=None,
            )
            text_rules = TextRules.from_setting(setting.text_rules, custom_placeholder_rules=custom_rules)
            game_data = await self._load_game_data(session)
            translated_items = await session.read_translated_items()
            scope = await TextScopeService().build(
                session=session,
                game_data=game_data,
                text_rules=text_rules,
                translated_items=translated_items,
            )
            blocking_errors = _text_scope_blocking_errors(scope)
            if blocking_errors:
                return AgentReport.from_parts(
                    errors=blocking_errors,
                    warnings=[],
                    summary={
                        "input": str(input_path),
                        "imported_count": 0,
                        "error_count": len(blocking_errors),
                    },
                    details={},
                )
            source_residual_rules = await session.read_source_residual_rules()
            active_items = {
                item.location_path: item
                for translation_data in scope.translation_data_map.values()
                for item in translation_data.translation_items
                if item.location_path in scope.writable_paths
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
                        source_residual_rules=source_residual_rules,
                    )
                    valid_items.append(cloned_item)
                except Exception as error:
                    errors.append(
                        issue(
                            "manual_translation_invalid",
                            f"{location_path} 手动填写译文不可用: {type(error).__name__}: {error}",
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
            imported_paths = {item.location_path for item in valid_items}
            _ = await session.delete_translation_quality_errors_by_paths(imported_paths)
            latest_run = await session.read_latest_translation_run()
            if latest_run is not None:
                remaining_quality_errors = await session.read_translation_quality_errors(latest_run.run_id)
                llm_failures = await session.read_llm_failures(latest_run.run_id)
                translated_paths = await session.read_translation_location_paths()
                current_pending_paths = set(active_items) - translated_paths
                if not current_pending_paths and not remaining_quality_errors and not llm_failures:
                    await session.write_translation_run(
                        latest_run.model_copy(
                            update={
                                "status": "completed",
                                "quality_error_count": 0,
                                "llm_failure_count": 0,
                                "finished_at": current_timestamp_text(),
                                "stop_reason": "",
                                "last_error": "",
                            }
                        )
                    )

        return AgentReport.from_parts(
            errors=[],
            warnings=[] if valid_items else [issue("manual_translation_empty", "手动填写译文表没有可导入条目")],
            summary={
                "input": str(input_path),
                "imported_count": len(valid_items),
            },
            details={},
        )
