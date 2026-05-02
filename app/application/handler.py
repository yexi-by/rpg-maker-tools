"""
核心 CLI 翻译编排模块。

本模块串起游戏注册、外部规则导入、正文翻译、缓存断点续传与游戏文件回写。
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from app.application.file_writer import reset_writable_copies, write_game_files
from app.application.font_replacement import apply_font_replacement
from app.application.runtime import load_runtime_setting
from app.application.summaries import (
    EventCommandJsonExportSummary,
    EventCommandRuleImportSummary,
    NameContextImportSummary,
    NameContextWriteSummary,
    PluginJsonExportSummary,
    PluginRuleImportSummary,
    TextTranslationSummary,
)
from app.config import (
    SettingOverrides,
    load_custom_placeholder_rules_text,
)
from app.config.schemas import Setting
from app.event_command_text import (
    EventCommandTextExtraction,
    build_event_command_rule_records_from_import,
    command_matches_filters,
    event_command_rule_key,
    export_event_commands_json_file,
    load_event_command_rule_import_file,
    resolve_event_command_codes,
)
from app.name_context import (
    NameContextExportSummary,
    NamePromptIndex,
    apply_name_context_translations,
    export_name_context_artifacts,
    load_name_context_registry,
)
from app.persistence import GameRegistry, TargetGameSession
from app.persistence.repository import current_timestamp_text
from app.plugin_text import (
    PluginTextExtraction,
    build_plugin_hash,
    build_plugin_rule_records_from_import,
    export_plugins_json_file,
    load_plugin_rule_import_file,
)
from app.rmmz import DataTextExtraction
from app.rmmz.commands import iter_all_commands
from app.rmmz.schema import (
    GameData,
    EventCommandTextRuleRecord,
    LlmFailureRecord,
    PlaceholderRuleRecord,
    PLUGINS_FILE_NAME,
    PluginTextRuleRecord,
    TranslationData,
    TranslationErrorItem,
    TranslationItem,
    TranslationRunRecord,
)
from app.rmmz.control_codes import CustomPlaceholderRule
from app.llm import LLMHandler, LLMRequestFailure
from app.rmmz.text_rules import TextRules
from app.translation import TextTranslation, TranslationBatch, TranslationCache, iter_translation_context_batches
from app.rmmz.loader import load_game_data, read_game_title, resolve_game_directory
from app.observability.logging import logger
from app.rmmz.write_back import write_data_text
from app.plugin_text.write_back import write_plugin_text
from app.utils.config_loader_utils import load_setting


@dataclass(frozen=True, slots=True)
class TranslationRunLimits:
    """正文翻译单次运行控制参数。"""

    max_items: int | None = None
    max_batches: int | None = None
    time_limit_seconds: int | None = None
    stop_on_error_rate: float | None = None
    stop_on_rate_limit_count: int | None = None


class TranslationRunInterrupted(Exception):
    """正文翻译运行被模型故障或控制条件中断。"""

    def __init__(
        self,
        *,
        reason: str,
        success_count: int,
        quality_error_count: int,
        llm_failure: LLMRequestFailure | None = None,
    ) -> None:
        """保存中断原因和已落库数量。"""
        super().__init__(reason)
        self.reason: str = reason
        self.success_count: int = success_count
        self.quality_error_count: int = quality_error_count
        self.llm_failure: LLMRequestFailure | None = llm_failure


@dataclass(slots=True)
class TranslationProgressState:
    """正文翻译运行期间共享的落库计数。"""

    success_count: int = 0
    quality_error_count: int = 0


class TranslationHandler:
    """核心 CLI 翻译业务总编排器。"""

    def __init__(
        self,
        game_registry: GameRegistry,
        llm_handler: LLMHandler,
    ) -> None:
        """初始化编排器。"""
        self.game_registry: GameRegistry = game_registry
        self.llm_handler: LLMHandler = llm_handler

    @classmethod
    async def create(cls) -> Self:
        """创建编排器，不打开任何游戏数据库。"""
        game_registry = GameRegistry()
        llm_handler = LLMHandler()
        logger.info("[tag.phase]编排器初始化完成[/tag.phase] 数据库将在目标命令执行时按需打开")
        return cls(game_registry, llm_handler)

    async def close(self) -> None:
        """释放编排器持有的运行时资源。"""
        self.llm_handler.clean()

    def _load_runtime_setting(self, setting_overrides: SettingOverrides | None = None) -> Setting:
        """加载配置并按本轮命令重置模型服务。"""
        return load_runtime_setting(self.llm_handler, overrides=setting_overrides)

    def _load_setting(self, setting_overrides: SettingOverrides | None = None) -> Setting:
        """加载当前配置，不改动模型服务连接状态。"""
        return load_setting(overrides=setting_overrides)

    def _load_text_rules(
        self,
        setting: Setting,
        custom_placeholder_rules_text: str | None = None,
        placeholder_rule_records: list[PlaceholderRuleRecord] | None = None,
    ) -> TextRules:
        """加载文本过滤规则和自定义占位符规则。"""
        if custom_placeholder_rules_text is not None:
            custom_rules = load_custom_placeholder_rules_text(custom_placeholder_rules_text)
            source_label = "CLI 参数"
        elif placeholder_rule_records is not None:
            custom_rules = tuple(
                CustomPlaceholderRule.create(
                    pattern_text=record.pattern_text,
                    placeholder_template=record.placeholder_template,
                )
                for record in placeholder_rule_records
            )
            source_label = "当前游戏数据库"
        else:
            custom_rules = ()
            source_label = "空规则"

        if custom_rules:
            logger.info(f"[tag.phase]已加载自定义占位符规则[/tag.phase] 来源 {source_label} 数量 [tag.count]{len(custom_rules)}[/tag.count] 条")
        elif custom_placeholder_rules_text is not None:
            logger.info("[tag.skip]CLI 指定的自定义占位符规则为空对象[/tag.skip]")
        return TextRules.from_setting(
            setting.text_rules,
            custom_placeholder_rules=custom_rules,
        )

    async def _load_session_game_data(self, session: TargetGameSession) -> GameData:
        """加载目标游戏数据并绑定到当前命令会话。"""
        game_data = await load_game_data(session.game_path)
        session.set_game_data(game_data)
        return session.require_game_data()

    async def resolve_game_title_by_path(self, game_path: str | Path) -> str:
        """根据已注册游戏目录解析可用于 CLI 的游戏标题。"""
        return await self.game_registry.resolve_registered_title_by_path(game_path)

    async def add_game(self, game_path: str | Path) -> str:
        """注册一个新的游戏。"""
        resolved_game_path = resolve_game_directory(game_path)
        game_title = read_game_title(resolved_game_path)
        _ = await load_game_data(resolved_game_path)
        record = await self.game_registry.register_game(resolved_game_path)
        logger.success(f"[tag.success]游戏已加入核心 CLI[/tag.success] 标题 [tag.count]{game_title}[/tag.count] 路径 [tag.path]{record.game_path}[/tag.path]")
        return game_title

    async def import_plugin_rules(
        self,
        game_title: str,
        input_path: Path,
    ) -> PluginRuleImportSummary:
        """把外部插件规则 JSON 导入当前游戏数据库。"""
        async with await self.game_registry.open_game(game_title) as session:
            game_data = await self._load_session_game_data(session)
            import_file = await load_plugin_rule_import_file(input_path)
            rule_records = build_plugin_rule_records_from_import(
                game_data=game_data,
                import_file=import_file,
            )
            old_rules = {
                rule.plugin_index: rule
                for rule in await session.read_plugin_text_rules()
            }
            deleted_translation_items = 0
            for rule_record in rule_records:
                old_rule = old_rules.get(rule_record.plugin_index)
                if self._should_refresh_plugin_translation_items(old_rule, rule_record):
                    deleted_translation_items += await session.delete_translation_items_by_prefixes(
                        [f"{PLUGINS_FILE_NAME}/{rule_record.plugin_index}/"],
                    )
            await session.replace_plugin_text_rules(rule_records)
        imported_rule_count = sum(len(record.path_templates) for record in rule_records)
        logger.success(f"[tag.success]插件规则导入完成[/tag.success] 游戏 [tag.count]{game_title}[/tag.count] 插件 [tag.count]{len(rule_records)}[/tag.count] 个，规则 [tag.count]{imported_rule_count}[/tag.count] 条，清理失效译文 [tag.count]{deleted_translation_items}[/tag.count] 条")
        return PluginRuleImportSummary(
            imported_plugin_count=len(rule_records),
            imported_rule_count=imported_rule_count,
            deleted_translation_items=deleted_translation_items,
        )

    async def export_plugins_json(
        self,
        game_title: str,
        output_path: Path,
    ) -> PluginJsonExportSummary:
        """把当前游戏的 plugins.js 导出为纯 JSON。"""
        async with await self.game_registry.open_game(game_title) as session:
            game_data = await self._load_session_game_data(session)
            resolved_output_path = output_path.resolve()
            await export_plugins_json_file(game_data=game_data, output_path=resolved_output_path)
            logger.success(f"[tag.success]插件配置 JSON 导出完成[/tag.success] 游戏 [tag.count]{game_title}[/tag.count] 插件 [tag.count]{len(game_data.plugins_js)}[/tag.count] 个 文件 [tag.path]{resolved_output_path}[/tag.path]")
            return PluginJsonExportSummary(
                output_path=str(resolved_output_path),
                plugin_count=len(game_data.plugins_js),
            )

    async def export_event_commands_json(
        self,
        game_title: str,
        output_path: Path,
        command_codes: set[int] | None,
    ) -> EventCommandJsonExportSummary:
        """把指定事件指令的原始参数导出为 JSON。"""
        async with await self.game_registry.open_game(game_title) as session:
            game_data = await self._load_session_game_data(session)
            resolved_output_path = output_path.resolve()
            default_command_codes: list[int] | None = None
            if command_codes is None:
                setting = self._load_setting()
                default_command_codes = setting.event_command_text.default_command_codes
            effective_command_codes = resolve_event_command_codes(
                command_codes=command_codes,
                default_command_codes=default_command_codes,
            )
            command_count = await export_event_commands_json_file(
                game_data=game_data,
                output_path=resolved_output_path,
                command_codes=effective_command_codes,
            )
            code_label = ", ".join(map(str, sorted(effective_command_codes)))
            logger.success(f"[tag.success]事件指令参数 JSON 导出完成[/tag.success] 游戏 [tag.count]{game_title}[/tag.count] 编码 [tag.count]{code_label}[/tag.count] 指令 [tag.count]{command_count}[/tag.count] 条 文件 [tag.path]{resolved_output_path}[/tag.path]")
            return EventCommandJsonExportSummary(
                output_path=str(resolved_output_path),
                command_count=command_count,
            )

    async def import_event_command_rules(
        self,
        game_title: str,
        input_path: Path,
    ) -> EventCommandRuleImportSummary:
        """把外部事件指令规则 JSON 导入当前游戏数据库。"""
        async with await self.game_registry.open_game(game_title) as session:
            game_data = await self._load_session_game_data(session)
            import_file = await load_event_command_rule_import_file(input_path)
            rule_records = build_event_command_rule_records_from_import(
                game_data=game_data,
                import_file=import_file,
            )
            old_rules = {
                event_command_rule_key(rule): rule
                for rule in await session.read_event_command_text_rules()
            }
            deleted_translation_items = 0
            for rule_record in rule_records:
                old_rule = old_rules.get(event_command_rule_key(rule_record))
                if self._should_refresh_event_command_translation_items(old_rule, rule_record):
                    deleted_translation_items += await session.delete_translation_items_by_prefixes(
                        self._event_command_rule_prefixes(game_data=game_data, rule_record=rule_record),
                    )
            await session.replace_event_command_text_rules(rule_records)
        imported_path_rule_count = sum(len(record.path_templates) for record in rule_records)
        logger.success(f"[tag.success]事件指令规则导入完成[/tag.success] 游戏 [tag.count]{game_title}[/tag.count] 规则组 [tag.count]{len(rule_records)}[/tag.count] 个，路径规则 [tag.count]{imported_path_rule_count}[/tag.count] 条，清理失效译文 [tag.count]{deleted_translation_items}[/tag.count] 条")
        return EventCommandRuleImportSummary(
            imported_rule_group_count=len(rule_records),
            imported_path_rule_count=imported_path_rule_count,
            deleted_translation_items=deleted_translation_items,
        )

    async def import_placeholder_rules(
        self,
        game_title: str,
        rules_text: str,
    ) -> int:
        """把当前游戏专用自定义占位符规则写入数据库。"""
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
        logger.success(f"[tag.success]自定义占位符规则导入完成[/tag.success] 游戏 [tag.count]{game_title}[/tag.count] 规则 [tag.count]{len(rule_records)}[/tag.count] 条")
        return len(rule_records)

    async def translate_text(
        self,
        game_title: str,
        setting_overrides: SettingOverrides | None,
        custom_placeholder_rules_text: str | None,
        run_limits: TranslationRunLimits | None,
        callbacks: tuple[
            Callable[[int, int], None],
            Callable[[int], None],
            Callable[[str], None],
        ],
    ) -> TextTranslationSummary:
        """翻译指定游戏的正文。"""
        setting = self._load_runtime_setting(setting_overrides)
        translation_cache = TranslationCache()
        async with await self.game_registry.open_game(game_title) as session:
            placeholder_rule_records: list[PlaceholderRuleRecord] | None = None
            if custom_placeholder_rules_text is None:
                placeholder_rule_records = await session.read_placeholder_rules()
            text_rules = self._load_text_rules(
                setting=setting,
                custom_placeholder_rules_text=custom_placeholder_rules_text,
                placeholder_rule_records=placeholder_rule_records,
            )
            return await self._translate_text_in_session(
                session=session,
                setting=setting,
                text_rules=text_rules,
                translation_cache=translation_cache,
                run_limits=run_limits or TranslationRunLimits(),
                callbacks=callbacks,
            )

    async def _translate_text_in_session(
        self,
        *,
        session: TargetGameSession,
        setting: Setting,
        text_rules: TextRules,
        translation_cache: TranslationCache,
        run_limits: TranslationRunLimits,
        callbacks: tuple[
            Callable[[int, int], None],
            Callable[[int], None],
            Callable[[str], None],
        ],
    ) -> TextTranslationSummary:
        """在单游戏数据库会话中翻译正文。"""
        set_progress, advance_progress, set_status = callbacks
        game_title = session.game_title
        game_data = await self._load_session_game_data(session)
        name_prompt_index = await self._load_name_prompt_index(session=session)

        plugin_rules = await self._read_fresh_plugin_text_rules(
            session=session,
            game_data=game_data,
        )
        event_command_rules = await session.read_event_command_text_rules()
        translation_data_map = DataTextExtraction(game_data, text_rules).extract_all_text()
        event_command_translation_data_map = EventCommandTextExtraction(
            game_data=game_data,
            rule_records=event_command_rules,
            text_rules=text_rules,
        ).extract_all_text()
        plugin_translation_data_map = PluginTextExtraction(
            game_data=game_data,
            plugin_rule_records=plugin_rules,
            text_rules=text_rules,
        ).extract_all_text()
        self._merge_translation_data_map(translation_data_map, event_command_translation_data_map)
        self._merge_translation_data_map(translation_data_map, plugin_translation_data_map)
        active_translation_paths = self._collect_translation_data_paths(translation_data_map)
        deleted_stale_items = await session.delete_translation_items_except_paths(
            active_translation_paths,
        )
        if deleted_stale_items:
            logger.warning(f"[tag.warning]已清理不符合当前提取规则的缓存译文[/tag.warning] 游戏 [tag.count]{game_title}[/tag.count] 数量 [tag.count]{deleted_stale_items}[/tag.count]")

        total_extracted_items = self._count_translation_items(translation_data_map)
        translated_paths = await session.read_translation_location_paths()
        pending_translation_data_map = self._filter_pending_translation_data(
            translation_data_map=translation_data_map,
            translated_paths=translated_paths,
        )
        pending_translation_data_map = self._limit_translation_data(
            translation_data_map=pending_translation_data_map,
            max_items=run_limits.max_items,
        )
        pending_count = self._count_translation_items(pending_translation_data_map)
        set_progress(0, pending_count)

        if total_extracted_items == 0:
            blocked_reason = "没有提取到任何可翻译正文"
            logger.warning(f"[tag.warning]{blocked_reason}[/tag.warning] 游戏 [tag.count]{game_title}[/tag.count]")
            return TextTranslationSummary(
                total_extracted_items=0,
                pending_count=0,
                deduplicated_count=0,
                batch_count=0,
                success_count=0,
                error_count=0,
                blocked_reason=blocked_reason,
            )

        if pending_count == 0:
            logger.info(f"[tag.skip]正文译文已全部存在，跳过翻译[/tag.skip] 游戏 [tag.count]{game_title}[/tag.count]")
            set_progress(total_extracted_items, total_extracted_items)
            return TextTranslationSummary(
                total_extracted_items=total_extracted_items,
                pending_count=0,
                deduplicated_count=0,
                batch_count=0,
                success_count=0,
                error_count=0,
            )

        deduplicated_translation_data_map = self._deduplicate_translation_data(
            translation_data_map=pending_translation_data_map,
            translation_cache=translation_cache,
        )
        deduplicated_count = self._count_translation_items(deduplicated_translation_data_map)
        batches = self._build_translation_batches(
            translation_data_map=deduplicated_translation_data_map,
            setting=setting,
            text_rules=text_rules,
            name_prompt_index=name_prompt_index,
        )
        if run_limits.max_batches is not None:
            batches = batches[: run_limits.max_batches]
        deduplicated_count = sum(len(batch.items) for batch in batches)
        if not batches:
            blocked_reason = "正文去重后没有可送入模型的批次"
            logger.warning(f"[tag.warning]{blocked_reason}[/tag.warning] 游戏 [tag.count]{game_title}[/tag.count]")
            return TextTranslationSummary(
                total_extracted_items=total_extracted_items,
                pending_count=pending_count,
                deduplicated_count=deduplicated_count,
                batch_count=0,
                success_count=0,
                error_count=0,
                blocked_reason=blocked_reason,
            )

        run_record = await session.start_translation_run(
            total_extracted=total_extracted_items,
            pending_count=pending_count,
            deduplicated_count=deduplicated_count,
            batch_count=len(batches),
        )
        set_status(f"待翻译 {pending_count} 条，去重后 {deduplicated_count} 条，批次 {len(batches)} 个")
        logger.info(f"[tag.phase]正文翻译开始[/tag.phase] 游戏 [tag.count]{game_title}[/tag.count] 提取 [tag.count]{total_extracted_items}[/tag.count] 条，待翻译 [tag.count]{pending_count}[/tag.count] 条，去重后 [tag.count]{deduplicated_count}[/tag.count] 条，批次 [tag.count]{len(batches)}[/tag.count] 个")
        text_translation = TextTranslation(setting=setting, text_rules=text_rules)
        try:
            success_count, error_count = await self._run_text_translation_batches(
                text_translation=text_translation,
                session=session,
                batches=batches,
                run_record=run_record,
                advance_progress=advance_progress,
                translation_cache=translation_cache,
                time_limit_seconds=run_limits.time_limit_seconds,
                stop_on_error_rate=run_limits.stop_on_error_rate,
            )
            finished_run = run_record.model_copy(
                update={
                    "status": "completed" if error_count == 0 else "blocked",
                    "success_count": success_count,
                    "quality_error_count": error_count,
                    "finished_at": current_timestamp_text(),
                    "stop_reason": "" if error_count == 0 else "存在最终译文质量错误",
                    "last_error": "" if error_count == 0 else "quality_errors",
                }
            )
            await session.write_translation_run(finished_run)
        except TranslationRunInterrupted as error:
            llm_failure_count = 0
            if error.llm_failure is not None:
                await session.write_llm_failure(
                    self._build_llm_failure_record(
                        run_id=run_record.run_id,
                        failure=error.llm_failure,
                    )
                )
                llm_failure_count = 1
            interrupted_run = run_record.model_copy(
                update={
                    "status": "blocked",
                    "success_count": error.success_count,
                    "quality_error_count": error.quality_error_count,
                    "llm_failure_count": llm_failure_count,
                    "finished_at": current_timestamp_text(),
                    "stop_reason": error.reason,
                    "last_error": str(error),
                }
            )
            await session.write_translation_run(interrupted_run)
            return TextTranslationSummary(
                total_extracted_items=total_extracted_items,
                pending_count=pending_count,
                deduplicated_count=deduplicated_count,
                batch_count=len(batches),
                success_count=error.success_count,
                error_count=error.quality_error_count,
                llm_failure_count=llm_failure_count,
                run_id=run_record.run_id,
                blocked_reason=error.reason,
            )
        return TextTranslationSummary(
            total_extracted_items=total_extracted_items,
            pending_count=pending_count,
            deduplicated_count=deduplicated_count,
            batch_count=len(batches),
            success_count=success_count,
            error_count=error_count,
            run_id=run_record.run_id,
        )

    async def write_back(
        self,
        game_title: str,
        callbacks: tuple[Callable[[int, int], None], Callable[[int], None]],
        setting_overrides: SettingOverrides | None = None,
    ) -> None:
        """把数据库中的有效译文回写到游戏目录。"""
        async with await self.game_registry.open_game(game_title) as session:
            set_progress, advance_progress = callbacks
            game_data = await self._load_session_game_data(session)
            setting = self._load_setting(setting_overrides=setting_overrides)
            text_rules = self._load_text_rules(
                setting=setting,
                placeholder_rule_records=await session.read_placeholder_rules(),
            )
            translated_items = await session.read_translated_items()
            translated_items = await self._filter_writable_translation_items(
                session=session,
                game_data=game_data,
                text_rules=text_rules,
                translated_items=translated_items,
            )
            set_progress(0, len(translated_items))

            if not translated_items:
                logger.warning(f"[tag.warning]当前没有可回写译文[/tag.warning] 游戏 [tag.count]{game_title}[/tag.count]")
                return

            reset_writable_copies(game_data)
            data_item_count = sum(
                1 for item in translated_items if not item.location_path.startswith(f"{PLUGINS_FILE_NAME}/")
            )
            plugin_item_count = len(translated_items) - data_item_count
            write_data_text(game_data, translated_items, text_rules=text_rules)
            if data_item_count:
                advance_progress(data_item_count)
            write_plugin_text(game_data, translated_items)
            if plugin_item_count:
                advance_progress(plugin_item_count)
            name_written_count = await self._apply_optional_name_context_write_back(
                session=session,
                game_data=game_data,
            )
            font_summary = apply_font_replacement(
                game_data=game_data,
                game_root=session.game_path,
                replacement_font_path=setting.write_back.replacement_font_path,
            )

            write_game_files(game_data=game_data, game_root=session.game_path)
            if font_summary.target_font_name is not None:
                logger.info(f"[tag.phase]字体引用已同步[/tag.phase] 游戏 [tag.count]{game_title}[/tag.count] 目标字体 [tag.path]{font_summary.target_font_name}[/tag.path] 原字体 [tag.count]{font_summary.source_font_count}[/tag.count] 个，替换引用 [tag.count]{font_summary.replaced_reference_count}[/tag.count] 处")
            logger.success(f"[tag.success]游戏文本回写完成[/tag.success] 游戏 [tag.count]{game_title}[/tag.count] data 文本 [tag.count]{data_item_count}[/tag.count] 条，插件文本 [tag.count]{plugin_item_count}[/tag.count] 条，标准名 [tag.count]{name_written_count}[/tag.count] 条")

    async def export_name_context(
        self,
        game_title: str,
        output_dir: Path,
    ) -> NameContextExportSummary:
        """导出 `101` 名字框与地图显示名上下文文件。"""
        async with await self.game_registry.open_game(game_title) as session:
            game_data = await self._load_session_game_data(session)
            summary = await export_name_context_artifacts(
                game_data=game_data,
                output_dir=output_dir,
            )
            logger.success(f"[tag.success]标准名上下文导出完成[/tag.success] 游戏 [tag.count]{game_title}[/tag.count] 大 JSON [tag.path]{summary.registry_path}[/tag.path] 小 JSON [tag.count]{summary.sample_file_count}[/tag.count] 个")
            return summary

    async def import_name_context(
        self,
        game_title: str,
        input_path: Path,
    ) -> NameContextImportSummary:
        """把外部 Agent 填写后的术语表 JSON 导入当前游戏数据库。"""
        async with await self.game_registry.open_game(game_title) as session:
            registry = await load_name_context_registry(registry_path=input_path)
            await session.replace_name_context_registry(registry)
        imported_count = len(registry.speaker_names) + len(registry.map_display_names)
        filled_count = sum(
            1
            for translated_text in [*registry.speaker_names.values(), *registry.map_display_names.values()]
            if translated_text.strip()
        )
        logger.success(f"[tag.success]术语表导入完成[/tag.success] 游戏 [tag.count]{game_title}[/tag.count] 条目 [tag.count]{imported_count}[/tag.count] 条，已填写 [tag.count]{filled_count}[/tag.count] 条")
        return NameContextImportSummary(
            imported_entry_count=imported_count,
            filled_entry_count=filled_count,
        )

    async def write_name_context(
        self,
        game_title: str,
        callbacks: tuple[Callable[[int, int], None], Callable[[int], None]],
        setting_overrides: SettingOverrides | None = None,
    ) -> NameContextWriteSummary:
        """根据数据库中的术语表写回 `101` 名字框与地图显示名。"""
        async with await self.game_registry.open_game(game_title) as session:
            set_progress, advance_progress = callbacks
            game_data = await self._load_session_game_data(session)
            setting = self._load_setting(setting_overrides=setting_overrides)
            text_rules = self._load_text_rules(
                setting=setting,
                placeholder_rule_records=await session.read_placeholder_rules(),
            )
            translated_items = await session.read_translated_items()
            translated_items = await self._filter_writable_translation_items(
                session=session,
                game_data=game_data,
                text_rules=text_rules,
                translated_items=translated_items,
            )

            reset_writable_copies(game_data)
            if translated_items:
                write_data_text(game_data, translated_items, text_rules=text_rules)
                write_plugin_text(game_data, translated_items)

            registry = await session.read_name_context_registry()
            if registry is None:
                raise RuntimeError("当前游戏数据库中没有已导入术语表，请先执行 import-name-context")
            written_count = apply_name_context_translations(game_data, registry)
            set_progress(0, max(written_count, 1))
            advance_progress(written_count)
            font_summary = apply_font_replacement(
                game_data=game_data,
                game_root=session.game_path,
                replacement_font_path=setting.write_back.replacement_font_path,
            )
            write_game_files(game_data=game_data, game_root=session.game_path)
            if font_summary.target_font_name is not None:
                logger.info(f"[tag.phase]字体引用已同步[/tag.phase] 游戏 [tag.count]{game_title}[/tag.count] 目标字体 [tag.path]{font_summary.target_font_name}[/tag.path] 原字体 [tag.count]{font_summary.source_font_count}[/tag.count] 个，替换引用 [tag.count]{font_summary.replaced_reference_count}[/tag.count] 处")
            logger.success(f"[tag.success]标准名写回完成[/tag.success] 游戏 [tag.count]{game_title}[/tag.count] 写回 [tag.count]{written_count}[/tag.count] 条，保留已有正文译文 [tag.count]{len(translated_items)}[/tag.count] 条")
            return NameContextWriteSummary(written_count=written_count, preserved_translation_count=len(translated_items))

    async def _filter_writable_translation_items(
        self,
        *,
        session: TargetGameSession,
        game_data: GameData,
        text_rules: TextRules,
        translated_items: list[TranslationItem],
    ) -> list[TranslationItem]:
        """仅保留当前提取规则仍认为需要翻译的译文条目。"""
        game_title = session.game_title
        writable_paths = await self._collect_extractable_translation_paths(
            session=session,
            game_data=game_data,
            text_rules=text_rules,
        )
        deleted_stale_items = await session.delete_translation_items_except_paths(
            writable_paths,
        )
        if deleted_stale_items:
            logger.warning(f"[tag.warning]已清理不符合当前提取规则的缓存译文[/tag.warning] 游戏 [tag.count]{game_title}[/tag.count] 数量 [tag.count]{deleted_stale_items}[/tag.count]")
            translated_items = await session.read_translated_items()
        writable_items = [
            item
            for item in translated_items
            if item.location_path in writable_paths
            and text_rules.should_translate_source_lines(item.original_lines)
        ]
        skipped_count = len(translated_items) - len(writable_items)
        if skipped_count:
            logger.warning(f"[tag.warning]已跳过不符合当前提取规则的缓存译文[/tag.warning] 游戏 [tag.count]{game_title}[/tag.count] 数量 [tag.count]{skipped_count}[/tag.count]")
        return writable_items

    async def _collect_extractable_translation_paths(
        self,
        *,
        session: TargetGameSession,
        game_data: GameData,
        text_rules: TextRules,
    ) -> set[str]:
        """收集本轮提取规则允许写回的正文路径。"""
        plugin_rules = await self._read_fresh_plugin_text_rules(
            session=session,
            game_data=game_data,
        )
        event_command_rules = await session.read_event_command_text_rules()

        translation_data_map = DataTextExtraction(game_data, text_rules).extract_all_text()
        self._merge_translation_data_map(
            translation_data_map,
            EventCommandTextExtraction(
                game_data=game_data,
                rule_records=event_command_rules,
                text_rules=text_rules,
            ).extract_all_text(),
        )
        self._merge_translation_data_map(
            translation_data_map,
            PluginTextExtraction(
                game_data=game_data,
                plugin_rule_records=plugin_rules,
                text_rules=text_rules,
            ).extract_all_text(),
        )
        return {
            item.location_path
            for translation_data in translation_data_map.values()
            for item in translation_data.translation_items
        }

    async def _load_name_prompt_index(
        self,
        *,
        session: TargetGameSession,
    ) -> NamePromptIndex | None:
        """读取数据库术语表，并转换为正文提示词索引。"""
        registry = await session.read_name_context_registry()
        if registry is None:
            logger.info(f"[tag.skip]数据库没有已导入术语表，正文提示词不注入标准名[/tag.skip] 游戏 [tag.count]{session.game_title}[/tag.count]")
            return None

        index = NamePromptIndex.from_registry(registry)
        logger.info(f"[tag.phase]已加载术语表[/tag.phase] 游戏 [tag.count]{session.game_title}[/tag.count] 可注入译名 [tag.count]{len(index.entries)}[/tag.count] 条")
        return index

    async def _apply_optional_name_context_write_back(
        self,
        *,
        session: TargetGameSession,
        game_data: GameData,
    ) -> int:
        """在正文回写时顺手写回数据库术语表中的标准名。"""
        registry = await session.read_name_context_registry()
        if registry is None:
            logger.info(f"[tag.skip]数据库未发现术语表，跳过 101 名字框和地图名写回[/tag.skip] 游戏 [tag.count]{session.game_title}[/tag.count]")
            return 0
        return apply_name_context_translations(game_data, registry)

    async def _read_fresh_plugin_text_rules(
        self,
        *,
        session: TargetGameSession,
        game_data: GameData,
    ) -> list[PluginTextRuleRecord]:
        """读取匹配当前 plugins.js 的外部插件规则。"""
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

        if stale_count:
            logger.warning(f"[tag.warning]检测到过期插件规则，正文翻译将跳过这些规则[/tag.warning] 游戏 [tag.count]{session.game_title}[/tag.count] 数量 [tag.count]{stale_count}[/tag.count]")
        return fresh_rules

    @staticmethod
    def _should_refresh_plugin_translation_items(
        old_rule: PluginTextRuleRecord | None,
        new_rule: PluginTextRuleRecord,
    ) -> bool:
        """判断插件规则变化后是否需要清理失效插件译文。"""
        if old_rule is None:
            return False
        return (
            old_rule.plugin_hash != new_rule.plugin_hash
            or old_rule.path_templates != new_rule.path_templates
        )

    @staticmethod
    def _should_refresh_event_command_translation_items(
        old_rule: EventCommandTextRuleRecord | None,
        new_rule: EventCommandTextRuleRecord,
    ) -> bool:
        """判断事件指令规则变化后是否需要清理失效译文。"""
        if old_rule is None:
            return False
        return (
            old_rule.command_code != new_rule.command_code
            or old_rule.parameter_filters != new_rule.parameter_filters
            or old_rule.path_templates != new_rule.path_templates
        )

    @staticmethod
    def _event_command_rule_prefixes(
        *,
        game_data: GameData,
        rule_record: EventCommandTextRuleRecord,
    ) -> list[str]:
        """根据事件指令规则找出需要清理的正文路径前缀。"""
        prefixes: list[str] = []
        for path, _display_name, command in iter_all_commands(game_data):
            if command.code != rule_record.command_code:
                continue
            if not command_matches_filters(
                parameters=command.parameters,
                filters=rule_record.parameter_filters,
            ):
                continue
            prefixes.append("/".join(map(str, path)))
        return prefixes

    @staticmethod
    def _filter_pending_translation_data(
        *,
        translation_data_map: dict[str, TranslationData],
        translated_paths: set[str],
    ) -> dict[str, TranslationData]:
        """过滤掉数据库中已经存在译文的条目。"""
        pending_translation_data_map: dict[str, TranslationData] = {}
        for file_name, translation_data in translation_data_map.items():
            pending_items = [
                item
                for item in translation_data.translation_items
                if item.location_path not in translated_paths
            ]
            if not pending_items:
                continue
            pending_translation_data_map[file_name] = TranslationData(
                display_name=translation_data.display_name,
                translation_items=pending_items,
            )
        return pending_translation_data_map

    @staticmethod
    def _deduplicate_translation_data(
        *,
        translation_data_map: dict[str, TranslationData],
        translation_cache: TranslationCache,
    ) -> dict[str, TranslationData]:
        """按正文内容执行请求级去重。"""
        deduplicated_translation_data_map: dict[str, TranslationData] = {}
        for file_name, translation_data in translation_data_map.items():
            deduplicated_items = [
                item
                for item in translation_data.translation_items
                if translation_cache.remember_or_defer(item)
            ]
            if not deduplicated_items:
                continue
            deduplicated_translation_data_map[file_name] = TranslationData(
                display_name=translation_data.display_name,
                translation_items=deduplicated_items,
            )
        return deduplicated_translation_data_map

    @staticmethod
    def _limit_translation_data(
        *,
        translation_data_map: dict[str, TranslationData],
        max_items: int | None,
    ) -> dict[str, TranslationData]:
        """按本轮上限截取待翻译条目，便于 Agent 分批运行。"""
        if max_items is None:
            return translation_data_map
        if max_items <= 0:
            raise ValueError("max_items 必须是正整数")

        remaining_count = max_items
        limited_data_map: dict[str, TranslationData] = {}
        for file_name, translation_data in translation_data_map.items():
            if remaining_count <= 0:
                break
            selected_items = translation_data.translation_items[:remaining_count]
            if selected_items:
                limited_data_map[file_name] = TranslationData(
                    display_name=translation_data.display_name,
                    translation_items=selected_items,
                )
                remaining_count -= len(selected_items)
        return limited_data_map

    @staticmethod
    def _count_translation_items(translation_data_map: dict[str, TranslationData]) -> int:
        """统计翻译数据中的条目数量。"""
        return sum(len(data.translation_items) for data in translation_data_map.values())

    @staticmethod
    def _collect_translation_data_paths(translation_data_map: dict[str, TranslationData]) -> set[str]:
        """收集翻译数据中的全部正文路径。"""
        return {
            item.location_path
            for translation_data in translation_data_map.values()
            for item in translation_data.translation_items
        }

    @staticmethod
    def _merge_translation_data_map(
        target: dict[str, TranslationData],
        source: dict[str, TranslationData],
    ) -> None:
        """按文件名合并翻译数据，保留已有标准文本条目。"""
        for file_name, source_data in source.items():
            target_data = target.get(file_name)
            if target_data is None:
                target[file_name] = source_data
                continue
            target_data.translation_items.extend(source_data.translation_items)

    @staticmethod
    def _build_translation_batches(
        *,
        translation_data_map: dict[str, TranslationData],
        setting: Setting,
        text_rules: TextRules,
        name_prompt_index: NamePromptIndex | None,
    ) -> list[TranslationBatch]:
        """构建正文翻译批次。"""
        batches: list[TranslationBatch] = []
        for translation_data in translation_data_map.values():
            batches.extend(
                iter_translation_context_batches(
                    translation_data=translation_data,
                    token_size=setting.translation_context.token_size,
                    factor=setting.translation_context.factor,
                    max_command_items=setting.translation_context.max_command_items,
                    system_prompt=setting.text_translation.system_prompt,
                    text_rules=text_rules,
                    name_prompt_index=name_prompt_index,
                )
            )
        return batches

    async def _run_text_translation_batches(
        self,
        *,
        text_translation: TextTranslation,
        session: TargetGameSession,
        batches: list[TranslationBatch],
        run_record: TranslationRunRecord,
        advance_progress: Callable[[int], None],
        translation_cache: TranslationCache,
        time_limit_seconds: int | None,
        stop_on_error_rate: float | None,
    ) -> tuple[int, int]:
        """启动正文翻译并并发消费成功/失败队列。"""
        game_title = session.game_title
        text_translation.start_translation(llm_handler=self.llm_handler, batches=batches)
        db_write_lock = asyncio.Lock()
        progress_state = TranslationProgressState()
        success_task = asyncio.create_task(
            self._consume_right_items(
                session=session,
                text_translation=text_translation,
                run_record=run_record,
                progress_state=progress_state,
                db_write_lock=db_write_lock,
                advance_progress=advance_progress,
                translation_cache=translation_cache,
            )
        )
        error_task = asyncio.create_task(
            self._consume_error_items(
                session=session,
                text_translation=text_translation,
                run_record=run_record,
                progress_state=progress_state,
                db_write_lock=db_write_lock,
                advance_progress=advance_progress,
                translation_cache=translation_cache,
                stop_on_error_rate=stop_on_error_rate,
            )
        )
        results: tuple[int | BaseException, int | BaseException]
        try:
            gather_task = asyncio.gather(success_task, error_task, return_exceptions=True)
            if time_limit_seconds is None:
                results = await gather_task
            else:
                results = await asyncio.wait_for(gather_task, timeout=time_limit_seconds)
        except asyncio.TimeoutError as error:
            raise TranslationRunInterrupted(
                reason=f"达到本轮翻译时间上限: {time_limit_seconds} 秒",
                success_count=progress_state.success_count,
                quality_error_count=progress_state.quality_error_count,
            ) from error
        finally:
            for task in (success_task, error_task):
                if not task.done():
                    _ = task.cancel()
            await text_translation.stop()
            _ = await asyncio.gather(success_task, error_task, return_exceptions=True)

        runner_error: Exception | None = None
        for result in results:
            if isinstance(result, Exception):
                runner_error = result
                break
        if runner_error is not None:
            if isinstance(runner_error, TranslationRunInterrupted):
                raise runner_error
            if isinstance(runner_error, LLMRequestFailure):
                raise TranslationRunInterrupted(
                    reason=f"模型请求失败: {runner_error.info.message}",
                    success_count=progress_state.success_count,
                    quality_error_count=progress_state.quality_error_count,
                    llm_failure=runner_error,
                ) from runner_error
            raise runner_error

        success_count = progress_state.success_count
        error_count = progress_state.quality_error_count
        logger.success(f"[tag.success]正文翻译结束[/tag.success] 游戏 [tag.count]{game_title}[/tag.count] 成功 [tag.count]{success_count}[/tag.count] 条，失败 [tag.count]{error_count}[/tag.count] 条")
        return success_count, error_count

    async def _consume_right_items(
        self,
        *,
        session: TargetGameSession,
        text_translation: TextTranslation,
        run_record: TranslationRunRecord,
        progress_state: TranslationProgressState,
        db_write_lock: asyncio.Lock,
        advance_progress: Callable[[int], None],
        translation_cache: TranslationCache,
    ) -> int:
        """消费正文翻译成功队列并写入主翻译表。"""
        game_title = session.game_title
        success_count = 0
        async for items in text_translation.iter_right_items():
            expanded_items = self._expand_cached_translation_items(items, translation_cache)
            async with db_write_lock:
                await session.write_translation_items(expanded_items)
                success_count += len(expanded_items)
                progress_state.success_count += len(expanded_items)
                await session.write_translation_run(
                    run_record.model_copy(update={"success_count": progress_state.success_count})
                )
            advance_progress(len(expanded_items))
            logger.success(f"[tag.success]已写入正文翻译结果[/tag.success] 游戏 [tag.count]{game_title}[/tag.count] [tag.count]{len(expanded_items)}[/tag.count] 条")
        return success_count

    async def _consume_error_items(
        self,
        *,
        session: TargetGameSession,
        text_translation: TextTranslation,
        run_record: TranslationRunRecord,
        progress_state: TranslationProgressState,
        db_write_lock: asyncio.Lock,
        advance_progress: Callable[[int], None],
        translation_cache: TranslationCache,
        stop_on_error_rate: float | None,
    ) -> int:
        """消费正文翻译质量错误队列并写入固定质量错误表。"""
        game_title = session.game_title
        error_count = 0
        async for error_items in text_translation.iter_error_items():
            expanded_error_items = self._expand_cached_error_items(error_items, translation_cache)
            async with db_write_lock:
                await session.write_translation_quality_errors(
                    run_record.run_id,
                    expanded_error_items,
                )
                error_count += len(expanded_error_items)
                progress_state.quality_error_count += len(expanded_error_items)
                await session.write_translation_run(
                    run_record.model_copy(
                        update={
                            "success_count": progress_state.success_count,
                            "quality_error_count": progress_state.quality_error_count,
                        }
                    )
                )
            advance_progress(len(expanded_error_items))
            logger.error(f"[tag.failure]已写入译文质量错误[/tag.failure] 游戏 [tag.count]{game_title}[/tag.count] [tag.count]{len(expanded_error_items)}[/tag.count] 条")
            if stop_on_error_rate is not None:
                processed_count = progress_state.success_count + progress_state.quality_error_count
                if processed_count > 0 and progress_state.quality_error_count / processed_count >= stop_on_error_rate:
                    raise TranslationRunInterrupted(
                        reason=f"译文质量错误率达到停止阈值: {stop_on_error_rate}",
                        success_count=progress_state.success_count,
                        quality_error_count=progress_state.quality_error_count,
                    )
        return error_count

    @staticmethod
    def _expand_cached_error_items(
        error_items: list[TranslationErrorItem],
        translation_cache: TranslationCache,
    ) -> list[TranslationErrorItem]:
        """在错误落库前展开失败正文同键的重复条目。"""
        expanded_error_items: list[TranslationErrorItem] = []
        for error_item in error_items:
            expanded_error_items.append(error_item)
            duplicate_items = translation_cache.pop_duplicate_items_by_fields(
                original_lines=error_item.original_lines,
                item_type=error_item.item_type,
                role=error_item.role,
            )
            for duplicate_item in duplicate_items:
                expanded_error_items.append(
                    TranslationErrorItem(
                        location_path=duplicate_item.location_path,
                        item_type=duplicate_item.item_type,
                        role=duplicate_item.role,
                        original_lines=list(duplicate_item.original_lines),
                        translation_lines=list(error_item.translation_lines),
                        error_type=error_item.error_type,
                        error_detail=list(error_item.error_detail),
                        model_response=error_item.model_response,
                    )
                )
        return expanded_error_items

    @staticmethod
    def _expand_cached_translation_items(
        items: list[TranslationItem],
        translation_cache: TranslationCache,
    ) -> list[TranslationItem]:
        """在成功写库前展开与首条正文同键的重复条目。"""
        expanded_items: list[TranslationItem] = []
        for item in items:
            expanded_items.append(item)
            duplicate_items = translation_cache.pop_duplicate_items(item)
            for duplicate_item in duplicate_items:
                duplicate_item.translation_lines = list(item.translation_lines)
                expanded_items.append(duplicate_item)
        return expanded_items

    @staticmethod
    def _build_llm_failure_record(
        *,
        run_id: str,
        failure: LLMRequestFailure,
    ) -> LlmFailureRecord:
        """把模型请求异常转换成数据库运行级故障记录。"""
        return LlmFailureRecord(
            run_id=run_id,
            category=failure.info.category,
            error_type=failure.info.error_type,
            error_message=failure.info.message,
            retryable=failure.info.retryable,
            attempt_count=failure.attempt_count,
            created_at=current_timestamp_text(),
        )

    @staticmethod
    def _format_exception_summary(error: Exception) -> str:
        """将异常压缩为适合日志首行展示的稳定摘要。"""
        message = str(error).strip()
        if message:
            return f"{type(error).__name__}: {message}"
        return type(error).__name__


__all__: list[str] = [
    "EventCommandJsonExportSummary",
    "EventCommandRuleImportSummary",
    "NameContextImportSummary",
    "NameContextWriteSummary",
    "PluginJsonExportSummary",
    "PluginRuleImportSummary",
    "TextTranslationSummary",
    "TranslationHandler",
]
