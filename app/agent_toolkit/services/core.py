"""Agent 工具箱 CoreAgentMixin 子服务。"""
# pyright: reportPrivateUsage=false
# mixin 通过 AgentToolkitService 组合成同一个服务边界，允许调用同门面的受保护核心方法。

from .common import *


class CoreAgentMixin:
    """承载 AgentToolkitService 的 CoreAgentMixin 命令族。"""

    async def _load_game_data(self: AgentServiceContext, session: TargetGameSession) -> GameData:
        """加载单游戏数据并绑定到会话。"""
        from app.rmmz.loader import load_game_data

        game_data = await load_game_data(session.game_path)
        session.set_game_data(game_data)
        return game_data

    async def _load_active_game_data(self: AgentServiceContext, session: TargetGameSession) -> GameData:
        """加载当前激活游戏文件，供真实文件反查使用。"""
        return await load_active_game_data(session.game_path)

    async def _extract_active_translation_data_map(
        self: AgentServiceContext,
        *,
        session: TargetGameSession,
        game_data: GameData,
        text_rules: TextRules,
    ) -> dict[str, TranslationData]:
        """按当前数据库规则提取本轮正文条目，不执行写入探针。"""
        scope = await TextScopeService().build(
            session=session,
            game_data=game_data,
            text_rules=text_rules,
            include_write_probe=False,
        )
        return scope.translation_data_map

    async def _build_source_residual_rule_records(
        self: AgentServiceContext,
        *,
        game_title: str,
        rules_text: str,
    ) -> list[SourceResidualRuleRecord]:
        """解析并按当前游戏提取结果校验源文残留例外规则。"""
        import_file = parse_source_residual_rule_import_text(rules_text)
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
            active_items = [
                item
                for translation_data in translation_data_map.values()
                for item in translation_data.translation_items
            ]
            translated_items = await session.read_translated_items()
        return build_source_residual_rule_records_from_import(
            import_file=import_file,
            active_items=active_items,
            translated_items=translated_items,
            ignore_case=setting.text_rules.source_residual_terms_ignore_case,
        )

    async def _read_fresh_plugin_text_rules(
        self: AgentServiceContext,
        *,
        session: TargetGameSession,
        game_data: GameData,
    ) -> tuple[list[PluginTextRuleRecord], int]:
        """读取仍匹配当前 `plugins.js` 的插件规则，并统计过期规则。"""
        fresh_rules, stale_rules = await read_fresh_plugin_text_rules(
            session=session,
            game_data=game_data,
        )
        return fresh_rules, len(stale_rules)

    async def _resolve_custom_rules(
        self: AgentServiceContext,
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
