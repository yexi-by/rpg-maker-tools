"""Agent 工具箱 PlaceholderRuleAgentMixin 子服务。"""
# pyright: reportPrivateUsage=false
# mixin 通过 AgentToolkitService 组合成同一个服务边界，允许调用同门面的受保护核心方法。

from .common import *


class PlaceholderRuleAgentMixin:
    """承载 AgentToolkitService 的 PlaceholderRuleAgentMixin 命令族。"""

    async def scan_placeholder_candidates(
        self: AgentServiceContext,
        *,
        game_title: str,
        custom_placeholder_rules_text: str | None,
    ) -> AgentReport:
        """扫描目标游戏中疑似需要自定义保护的控制符。"""
        async with await self.game_registry.open_game(game_title) as session:
            setting = load_setting(self.setting_path, source_language=session.source_language)
            game_data = await self._load_game_data(session)
            custom_rules = await self._resolve_custom_rules(
                session=session,
                custom_placeholder_rules_text=custom_placeholder_rules_text,
            )
            text_rules = TextRules.from_setting(
                setting.text_rules,
                custom_placeholder_rules=custom_rules,
            )
            translation_data_map = await self._extract_active_translation_data_map(
                session=session,
                game_data=game_data,
                text_rules=text_rules,
            )

        candidates = scan_placeholder_candidates(translation_data_map, text_rules)
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
        self: AgentServiceContext,
        *,
        game_title: str | None,
        custom_placeholder_rules_text: str | None,
        sample_texts: Sequence[str],
    ) -> AgentReport:
        """校验自定义占位符规则，并预览样本文本的替换与还原结果。"""
        errors: list[AgentIssue] = []
        warnings: list[AgentIssue] = []
        setting_source_language: SourceLanguage = DEFAULT_SOURCE_LANGUAGE
        source_label = "--placeholder-rules"
        if custom_placeholder_rules_text is None and game_title is not None:
            source_label = "当前游戏数据库"
        elif custom_placeholder_rules_text is None:
            source_label = "空规则"

        try:
            if game_title is not None:
                async with await self.game_registry.open_game(game_title) as session:
                    setting_source_language = session.source_language
                    custom_rules = await self._resolve_custom_rules(
                        session=session,
                        custom_placeholder_rules_text=custom_placeholder_rules_text,
                    )
                    if not sample_texts:
                        game_data = await self._load_game_data(session)
                        setting = load_setting(self.setting_path, source_language=session.source_language)
                        preview_rules = TextRules.from_setting(
                            setting.text_rules,
                            custom_placeholder_rules=custom_rules,
                        )
                        translation_data_map = await self._extract_active_translation_data_map(
                            session=session,
                            game_data=game_data,
                            text_rules=preview_rules,
                        )
                        sample_texts = _collect_placeholder_preview_samples(translation_data_map, preview_rules)
                        if not sample_texts:
                            sample_texts = _collect_unprotected_control_warning_samples(translation_data_map, preview_rules)
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
            setting = load_setting(self.setting_path, source_language=setting_source_language)
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
                sample_preview = _preview_placeholder_sample(text_rules, sample_text)
                sample_details.append(sample_preview)
                if _placeholder_preview_loses_visible_source_text(
                    text_rules=text_rules,
                    sample_preview=sample_preview,
                ):
                    errors.append(
                        issue(
                            "placeholder_rule_loses_translatable_text",
                            "占位符规则把含源语言正文的样本文本整体遮蔽，模型将看不到需要翻译的内容",
                        )
                    )
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

    async def import_placeholder_rules(self: AgentServiceContext, *, game_title: str, rules_text: str) -> AgentReport:
        """校验并导入当前游戏专用自定义占位符规则。"""
        validation_report = await self.validate_placeholder_rules(
            game_title=game_title,
            custom_placeholder_rules_text=rules_text,
            sample_texts=[],
        )
        if validation_report.errors:
            return AgentReport.from_parts(
                errors=validation_report.errors,
                warnings=validation_report.warnings,
                summary={
                    "game": game_title,
                    "imported_rule_count": 0,
                    "validated_rule_count": validation_report.summary.get("rule_count", 0),
                    "sample_count": validation_report.summary.get("sample_count", 0),
                },
                details={
                    "validation": {
                        "summary": validation_report.summary,
                        "details": validation_report.details,
                    }
                },
            )

        custom_rules = load_custom_placeholder_rules_text(rules_text)
        rule_records = [
            PlaceholderRuleRecord(
                pattern_text=rule.pattern_text,
                placeholder_template=rule.placeholder_template,
            )
            for rule in custom_rules
        ]
        async with await self.game_registry.open_game(game_title) as session:
            await session.replace_placeholder_rules(rule_records)
        return AgentReport.from_parts(
            errors=[],
            warnings=validation_report.warnings
            if rule_records
            else [
                *validation_report.warnings,
                issue("placeholder_rules_empty", "已导入空自定义占位符规则"),
            ],
            summary={
                "game": game_title,
                "imported_rule_count": len(rule_records),
                "validated_rule_count": validation_report.summary.get("rule_count", len(rule_records)),
                "sample_count": validation_report.summary.get("sample_count", 0),
            },
            details={
                "validation": {
                    "summary": validation_report.summary,
                    "details": validation_report.details,
                }
            },
        )

    async def build_placeholder_rules(
        self: AgentServiceContext,
        *,
        game_title: str,
        output_path: Path,
    ) -> AgentReport:
        """根据未覆盖候选生成可编辑的自定义占位符规则草稿。"""
        async with await self.game_registry.open_game(game_title) as session:
            setting = load_setting(self.setting_path, source_language=session.source_language)
            empty_rules = TextRules.from_setting(setting.text_rules, custom_placeholder_rules=())
            game_data = await self._load_game_data(session)
            translation_data_map = await self._extract_active_translation_data_map(
                session=session,
                game_data=game_data,
                text_rules=empty_rules,
            )
        candidates = scan_placeholder_candidates(translation_data_map, empty_rules)
        manual_boundary_markers = _joined_text_boundary_markers(candidates)
        draft_rules = _build_custom_placeholder_rule_draft(candidates)
        warnings = _build_unprotected_control_warnings(
            _collect_unprotected_control_warning_samples(translation_data_map, empty_rules),
            empty_rules,
        )
        warnings.extend(_build_joined_text_boundary_warnings(manual_boundary_markers))
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
                "manual_boundary_candidate_count": len(manual_boundary_markers),
                "output": str(output_path),
            },
            details={
                "rules": {key: value for key, value in draft_rules.items()},
                "manual_boundary_candidates": [marker for marker in manual_boundary_markers],
            },
        )
