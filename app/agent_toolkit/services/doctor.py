"""Agent 工具箱 DoctorAgentMixin 子服务。"""
# pyright: reportPrivateUsage=false
# mixin 通过 AgentToolkitService 组合成同一个服务边界，允许调用同门面的受保护核心方法。

from .common import *


class DoctorAgentMixin:
    """承载 AgentToolkitService 的 DoctorAgentMixin 命令族。"""

    async def doctor(self: AgentServiceContext, *, game_title: str | None, check_llm: bool) -> AgentReport:
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
                        request_body_extra=setting.llm.request_body_extra,
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

    async def _check_game(
        self: AgentServiceContext,
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
                plugin_rules, stale_plugin_rule_count = await self._read_fresh_plugin_text_rules(
                    session=session,
                    game_data=game_data,
                )
                event_rules = await session.read_event_command_text_rules()
                note_tag_rules = await session.read_note_tag_text_rules()
                terminology_registry = await session.read_terminology_registry()
                terminology_glossary = await session.read_terminology_glossary()
                placeholder_rules = await session.read_placeholder_rules()
                summary["game_registered"] = True
                summary["source_language"] = session.source_language
                summary["target_language"] = session.target_language
                summary["plugin_rule_count"] = sum(len(rule.path_templates) for rule in plugin_rules)
                summary["stale_plugin_rule_count"] = stale_plugin_rule_count
                summary["event_command_rule_count"] = sum(len(rule.path_templates) for rule in event_rules)
                summary["note_tag_rule_count"] = sum(len(rule.tag_names) for rule in note_tag_rules)
                summary["placeholder_rule_count"] = len(placeholder_rules)
                summary["terminology_imported"] = terminology_registry is not None
                summary["glossary_imported"] = terminology_glossary is not None
                if not plugin_rules and stale_plugin_rule_count == 0:
                    warnings.append(issue("plugin_rules", "当前游戏尚未导入插件文本规则"))
                if stale_plugin_rule_count:
                    warnings.append(issue("stale_plugin_rules", f"发现 {stale_plugin_rule_count} 个过期插件规则，请重新导出并导入插件规则"))
                if not event_rules:
                    warnings.append(issue("event_command_rules", "当前游戏尚未导入事件指令文本规则"))
                if not note_tag_rules:
                    warnings.append(issue("note_tag_rules", "当前游戏尚未导入 Note 标签文本规则"))
                if terminology_registry is None:
                    warnings.append(issue("terminology", "当前游戏尚未导入字段译名表"))
                if terminology_glossary is None:
                    warnings.append(issue("glossary", "当前游戏尚未导入正文术语表"))
                if not placeholder_rules:
                    warnings.append(issue("placeholder_rules", "当前游戏尚未导入自定义占位符规则"))
                font_path = setting.write_back.replacement_font_path
                if font_path is not None:
                    try:
                        _ = resolve_replacement_font_path(font_path)
                    except (FileNotFoundError, ValueError) as error:
                        warnings.append(issue("replacement_font", f"配置的候选覆盖字体文件不可用: {error}"))
                translation_data_map = await self._extract_active_translation_data_map(
                    session=session,
                    game_data=game_data,
                    text_rules=text_rules,
                )
                candidates = scan_placeholder_candidates(translation_data_map, text_rules)
                uncovered_count = count_uncovered_candidates(candidates)
                summary["uncovered_placeholder_count"] = uncovered_count
                if uncovered_count:
                    warnings.append(issue("uncovered_placeholder", f"存在 {uncovered_count} 个未覆盖的疑似自定义控制符"))
        except Exception as error:
            errors.append(issue("game", f"目标游戏检查失败: {type(error).__name__}: {error}"))

    def _check_static_paths(
        self: AgentServiceContext,
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
            _ = ensure_db_directory(db_dir)
            _append_check(details, "db_dir", "ok" if db_dir_already_exists else "created")
        except Exception as error:
            errors.append(issue("db_dir", f"数据库目录创建失败: {type(error).__name__}: {error}"))
        logs_dir = resolve_app_path("logs")
        if not logs_dir.exists():
            _ = logs_dir.mkdir(exist_ok=True)
        try:
            encoding = sys.stdout.encoding or ""
            details["stdout_encoding"] = encoding
            _append_check(details, "stdout_encoding", "ok" if "utf" in encoding.lower() else "warning")
            if "utf" not in encoding.lower():
                warnings.append(issue("stdout_encoding", "当前 stdout 不是 UTF-8，建议使用 --agent-mode 或 --json"))
        except Exception as error:
            warnings.append(issue("stdout_encoding", f"终端编码检查失败: {type(error).__name__}: {error}"))
