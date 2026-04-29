"""
核心 CLI 翻译编排模块。

本模块串起游戏注册、插件分析、正文翻译、缓存断点续传与游戏文件回写。
术语表、英文兼容和非标准数据文件处理已经从编排层移除。
"""

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar, Self

from app.application.file_writer import reset_writable_copies, write_game_files
from app.application.runtime import load_runtime_setting
from app.application.summaries import PluginTextAnalysisSummary, TextTranslationSummary
from app.config.schemas import Setting
from app.persistence import DEFAULT_ERROR_TABLE_PREFIX, GameDatabaseItem, GameDatabaseManager
from app.plugin_text import PluginTextExtraction
from app.rmmz import DataTextExtraction
from app.rmmz.schema import (
    GameData,
    ItemType,
    PLUGINS_FILE_NAME,
    PluginTextAnalysisState,
    PluginTextRuleRecord,
    TranslationData,
    TranslationErrorItem,
    TranslationItem,
)
from app.plugin_text import PluginTextAnalysis, build_plugin_hash, build_prompt_hash
from app.llm import ChatMessage, LLMHandler
from app.rmmz.text_rules import TextRules
from app.translation import TextTranslation, TranslationCache, iter_translation_context_batches
from app.rmmz.loader import GameDataManager, read_game_title, resolve_game_directory
from app.observability.logging import logger
from app.rmmz.write_back import write_data_text
from app.plugin_text.write_back import write_plugin_text


class TranslationHandler:
    """核心 CLI 翻译业务总编排器。"""

    ERROR_TABLE_PREFIX: ClassVar[str] = DEFAULT_ERROR_TABLE_PREFIX

    def __init__(
        self,
        game_data_manager: GameDataManager,
        game_database_manager: GameDatabaseManager,
        llm_handler: LLMHandler,
    ) -> None:
        """初始化编排器。"""
        self.game_data_manager: GameDataManager = game_data_manager
        self.game_database_manager: GameDatabaseManager = game_database_manager
        self.llm_handler: LLMHandler = llm_handler

    @classmethod
    async def create(cls) -> Self:
        """创建编排器，并预加载已注册游戏数据。"""
        game_data_manager = GameDataManager()
        game_database_manager = await GameDatabaseManager.new()
        llm_handler = LLMHandler()
        handler = cls(game_data_manager, game_database_manager, llm_handler)
        try:
            preloaded_count = 0
            skipped_count = 0
            for item in game_database_manager.items.values():
                try:
                    await game_data_manager.load_game_data(item.game_path)
                    preloaded_count += 1
                except Exception as error:
                    skipped_count += 1
                    logger.warning(f"[tag.warning]游戏预加载失败，已跳过该游戏[/tag.warning] 标题 [tag.count]{item.game_title}[/tag.count] 路径 [tag.path]{item.game_path}[/tag.path] 原因：{cls._format_exception_summary(error)}")

            logger.info(f"[tag.phase]编排器初始化完成[/tag.phase] 成功预加载 [tag.count]{preloaded_count}[/tag.count] 个游戏，跳过 [tag.count]{skipped_count}[/tag.count] 个无效路径")
            return handler
        except Exception:
            await game_database_manager.close()
            raise

    async def close(self) -> None:
        """关闭数据库连接并释放资源。"""
        await self.game_database_manager.close()

    def _load_runtime_setting(self) -> Setting:
        """加载配置并按本轮命令重置模型服务。"""
        return load_runtime_setting(self.llm_handler)

    async def add_game(self, game_path: str | Path) -> str:
        """注册一个新的游戏。"""
        resolved_game_path = resolve_game_directory(game_path)
        game_title = read_game_title(resolved_game_path)
        previous_game_data = self.game_data_manager.items.get(game_title)
        try:
            await self.game_data_manager.load_game_data(resolved_game_path)
            await self.game_database_manager.create_database(resolved_game_path)
            game_database_item = self._get_game_database_item(game_title)
            logger.success(f"[tag.success]游戏已加入核心 CLI[/tag.success] 标题 [tag.count]{game_title}[/tag.count] 路径 [tag.path]{game_database_item.game_path}[/tag.path]")
            return game_title
        except Exception:
            if previous_game_data is None:
                _ = self.game_data_manager.items.pop(game_title, None)
            else:
                self.game_data_manager.items[game_title] = previous_game_data
            raise

    async def analyze_plugin_text(
        self,
        game_title: str,
        callbacks: tuple[
            Callable[[int, int], None],
            Callable[[int], None],
            Callable[[str], None],
        ],
    ) -> PluginTextAnalysisSummary:
        """为指定游戏执行插件文本路径分析。"""
        set_progress, advance_progress, set_status = callbacks
        setting = self._load_runtime_setting()
        text_rules = TextRules.from_setting(setting.text_rules)
        game_data = self._get_game_data(game_title)
        existing_rules = {
            rule.plugin_index: rule
            for rule in await self.game_database_manager.read_plugin_text_rules(game_title)
        }
        plugin_text_analysis = PluginTextAnalysis(setting=setting, text_rules=text_rules)
        analysis_plan = plugin_text_analysis.build_plan(
            plugins=game_data.plugins_js,
            existing_rules=existing_rules,
        )
        set_progress(analysis_plan.reused_success_count, analysis_plan.total_plugins)

        if analysis_plan.total_plugins == 0:
            skipped_reason = "plugins.js 中没有插件可供分析"
            logger.info(f"[tag.skip]{skipped_reason}[/tag.skip] 游戏 [tag.count]{game_title}[/tag.count]")
            set_status(skipped_reason)
            return PluginTextAnalysisSummary(0, 0, 0, 0, 0, skipped_reason)

        if not analysis_plan.jobs:
            await self.game_database_manager.write_plugin_text_analysis_state(
                game_title,
                PluginTextAnalysisState(
                    plugins_file_hash=analysis_plan.plugins_file_hash,
                    prompt_hash=analysis_plan.prompt_hash,
                    total_plugins=analysis_plan.total_plugins,
                    success_plugins=analysis_plan.reused_success_count,
                    failed_plugins=0,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                ),
            )
            skipped_reason = "插件规则已是最新"
            logger.info(f"[tag.skip]{skipped_reason}[/tag.skip] 游戏 [tag.count]{game_title}[/tag.count]")
            set_status(skipped_reason)
            return PluginTextAnalysisSummary(
                total_plugins=analysis_plan.total_plugins,
                success_plugins=analysis_plan.reused_success_count,
                failed_plugins=0,
                reused_success_count=analysis_plan.reused_success_count,
                deleted_translation_items=0,
                skipped_reason=skipped_reason,
            )

        plugin_text_analysis.start_analysis(llm_handler=self.llm_handler, plan=analysis_plan)
        success_plugins = analysis_plan.reused_success_count
        failed_plugins = 0
        deleted_translation_items = 0

        async for execution in plugin_text_analysis.iter_results():
            rule_record = execution.rule_record
            old_rule = existing_rules.get(rule_record.plugin_index)
            await self.game_database_manager.upsert_plugin_text_rule(game_title, rule_record)
            if self._should_refresh_plugin_translation_items(old_rule, rule_record):
                deleted_translation_items += await self.game_database_manager.delete_translation_items_by_prefixes(
                    game_title,
                    [f"{PLUGINS_FILE_NAME}/{rule_record.plugin_index}/"],
                )
            if rule_record.status == "success":
                success_plugins += 1
                logger.success(f"[tag.success]插件解析成功[/tag.success] 插件 [tag.count]{rule_record.plugin_name}[/tag.count] 规则 [tag.count]{len(rule_record.translate_rules)}[/tag.count] 条")
            else:
                failed_plugins += 1
                logger.error(f"[tag.failure]插件解析失败[/tag.failure] 插件 [tag.count]{rule_record.plugin_name}[/tag.count] 原因：{rule_record.last_error or '未知错误'}")
            set_status(self._format_plugin_rule_status(rule_record))
            advance_progress(1)

        await self.game_database_manager.write_plugin_text_analysis_state(
            game_title,
            PluginTextAnalysisState(
                plugins_file_hash=analysis_plan.plugins_file_hash,
                prompt_hash=analysis_plan.prompt_hash,
                total_plugins=analysis_plan.total_plugins,
                success_plugins=success_plugins,
                failed_plugins=failed_plugins,
                updated_at=datetime.now(timezone.utc).isoformat(),
            ),
        )
        logger.success(f"[tag.success]插件解析完成[/tag.success] 游戏 [tag.count]{game_title}[/tag.count] 成功 [tag.count]{success_plugins}[/tag.count] 个，失败 [tag.count]{failed_plugins}[/tag.count] 个，清理旧译文 [tag.count]{deleted_translation_items}[/tag.count] 条")
        return PluginTextAnalysisSummary(
            total_plugins=analysis_plan.total_plugins,
            success_plugins=success_plugins,
            failed_plugins=failed_plugins,
            reused_success_count=analysis_plan.reused_success_count,
            deleted_translation_items=deleted_translation_items,
        )

    async def translate_text(
        self,
        game_title: str,
        callbacks: tuple[
            Callable[[int, int], None],
            Callable[[int], None],
            Callable[[str], None],
        ],
    ) -> TextTranslationSummary:
        """翻译指定游戏的正文。"""
        set_progress, advance_progress, set_status = callbacks
        setting = self._load_runtime_setting()
        translation_cache = TranslationCache()
        text_rules = TextRules.from_setting(setting.text_rules)
        game_data = self._get_game_data(game_title)

        translated_paths = await self.game_database_manager.read_translation_location_paths(game_title)
        plugin_rules = await self._read_fresh_plugin_text_rules(
            game_title=game_title,
            game_data=game_data,
            setting=setting,
        )
        translation_data_map = DataTextExtraction(game_data, text_rules).extract_all_text()
        plugin_translation_data_map = PluginTextExtraction(
            game_data=game_data,
            plugin_rule_records=plugin_rules,
            text_rules=text_rules,
        ).extract_all_text()
        translation_data_map.update(plugin_translation_data_map)

        total_extracted_items = self._count_translation_items(translation_data_map)
        pending_translation_data_map = self._filter_pending_translation_data(
            translation_data_map=translation_data_map,
            translated_paths=translated_paths,
        )
        pending_count = self._count_translation_items(pending_translation_data_map)
        set_progress(0, pending_count)

        if total_extracted_items == 0:
            blocked_reason = "没有提取到任何可翻译正文"
            logger.warning(f"[tag.warning]{blocked_reason}[/tag.warning] 游戏 [tag.count]{game_title}[/tag.count]")
            return TextTranslationSummary(0, 0, 0, 0, 0, 0, blocked_reason)

        if pending_count == 0:
            logger.info(f"[tag.skip]正文译文已全部存在，跳过翻译[/tag.skip] 游戏 [tag.count]{game_title}[/tag.count]")
            set_progress(total_extracted_items, total_extracted_items)
            return TextTranslationSummary(total_extracted_items, 0, 0, 0, 0, 0)

        deduplicated_translation_data_map = self._deduplicate_translation_data(
            translation_data_map=pending_translation_data_map,
            translation_cache=translation_cache,
        )
        deduplicated_count = self._count_translation_items(deduplicated_translation_data_map)
        batches = self._build_translation_batches(
            translation_data_map=deduplicated_translation_data_map,
            setting=setting,
            text_rules=text_rules,
        )
        if not batches:
            blocked_reason = "正文去重后没有可送入模型的批次"
            logger.warning(f"[tag.warning]{blocked_reason}[/tag.warning] 游戏 [tag.count]{game_title}[/tag.count]")
            return TextTranslationSummary(
                total_extracted_items,
                pending_count,
                deduplicated_count,
                0,
                0,
                0,
                blocked_reason,
            )

        old_error_tables = await self.game_database_manager.read_error_table_names(game_title)
        deleted_error_tables = await self.game_database_manager.delete_error_tables(game_title, old_error_tables)
        if deleted_error_tables:
            logger.info(f"[tag.phase]已清理旧错误表[/tag.phase] 游戏 [tag.count]{game_title}[/tag.count] 数量 [tag.count]{deleted_error_tables}[/tag.count]")

        error_table_name = await self.game_database_manager.start_error_table(game_title)
        set_status(f"待翻译 {pending_count} 条，去重后 {deduplicated_count} 条，批次 {len(batches)} 个")
        logger.info(f"[tag.phase]正文翻译开始[/tag.phase] 游戏 [tag.count]{game_title}[/tag.count] 提取 [tag.count]{total_extracted_items}[/tag.count] 条，待翻译 [tag.count]{pending_count}[/tag.count] 条，去重后 [tag.count]{deduplicated_count}[/tag.count] 条，批次 [tag.count]{len(batches)}[/tag.count] 个")
        text_translation = TextTranslation(setting=setting, text_rules=text_rules)
        success_count, error_count = await self._run_text_translation_batches(
            text_translation=text_translation,
            game_title=game_title,
            batches=batches,
            error_table_name=error_table_name,
            advance_progress=advance_progress,
            translation_cache=translation_cache,
        )
        return TextTranslationSummary(
            total_extracted_items=total_extracted_items,
            pending_count=pending_count,
            deduplicated_count=deduplicated_count,
            batch_count=len(batches),
            success_count=success_count,
            error_count=error_count,
        )

    async def write_back(
        self,
        game_title: str,
        callbacks: tuple[Callable[[int, int], None], Callable[[int], None]],
    ) -> None:
        """把数据库中的有效译文回写到游戏目录。"""
        set_progress, advance_progress = callbacks
        game_data = self._get_game_data(game_title)
        game_database_item = self._get_game_database_item(game_title)
        translated_items = await self.game_database_manager.read_translated_items(game_title)
        set_progress(0, len(translated_items))

        if not translated_items:
            logger.warning(f"[tag.warning]当前没有可回写译文[/tag.warning] 游戏 [tag.count]{game_title}[/tag.count]")
            return

        reset_writable_copies(game_data)
        data_item_count = sum(
            1 for item in translated_items if not item.location_path.startswith(f"{PLUGINS_FILE_NAME}/")
        )
        plugin_item_count = len(translated_items) - data_item_count
        write_data_text(game_data, translated_items)
        if data_item_count:
            advance_progress(data_item_count)
        write_plugin_text(game_data, translated_items)
        if plugin_item_count:
            advance_progress(plugin_item_count)

        write_game_files(game_data=game_data, game_root=game_database_item.game_path)
        logger.success(f"[tag.success]游戏文本回写完成[/tag.success] 游戏 [tag.count]{game_title}[/tag.count] data 文本 [tag.count]{data_item_count}[/tag.count] 条，插件文本 [tag.count]{plugin_item_count}[/tag.count] 条")

    def _get_game_data(self, game_title: str) -> GameData:
        """根据游戏标题读取已加载的游戏数据。"""
        game_data = self.game_data_manager.items.get(game_title)
        if game_data is None:
            raise ValueError(f"未找到已加载游戏数据: {game_title}")
        return game_data

    def _get_game_database_item(self, game_title: str) -> GameDatabaseItem:
        """根据游戏标题读取数据库对象。"""
        item = self.game_database_manager.items.get(game_title)
        if item is None:
            raise ValueError(f"未找到游戏数据库: {game_title}")
        return item

    async def _read_fresh_plugin_text_rules(
        self,
        *,
        game_title: str,
        game_data: GameData,
        setting: Setting,
    ) -> list[PluginTextRuleRecord]:
        """读取仍然匹配当前 plugins.js 和提示词的成功插件规则。"""
        prompt_hash = build_prompt_hash(setting.plugin_text_analysis.system_prompt)
        plugin_rules = await self.game_database_manager.read_plugin_text_rules(game_title)
        fresh_rules: list[PluginTextRuleRecord] = []
        stale_count = 0
        for rule in plugin_rules:
            if rule.status != "success":
                stale_count += 1
                continue
            if rule.plugin_index >= len(game_data.plugins_js):
                stale_count += 1
                continue
            plugin_hash = build_plugin_hash(game_data.plugins_js[rule.plugin_index])
            if rule.plugin_hash != plugin_hash or rule.prompt_hash != prompt_hash:
                stale_count += 1
                continue
            fresh_rules.append(rule)

        if stale_count:
            logger.warning(f"[tag.warning]检测到过期或失败的插件规则，正文翻译将跳过这些规则[/tag.warning] 游戏 [tag.count]{game_title}[/tag.count] 数量 [tag.count]{stale_count}[/tag.count]")
        return fresh_rules

    @staticmethod
    def _should_refresh_plugin_translation_items(
        old_rule: PluginTextRuleRecord | None,
        new_rule: PluginTextRuleRecord,
    ) -> bool:
        """判断插件规则变化后是否需要清理旧插件译文。"""
        if old_rule is None:
            return False
        return (
            old_rule.plugin_hash != new_rule.plugin_hash
            or old_rule.prompt_hash != new_rule.prompt_hash
            or old_rule.status != new_rule.status
            or old_rule.translate_rules != new_rule.translate_rules
        )

    @staticmethod
    def _format_plugin_rule_status(rule_record: PluginTextRuleRecord) -> str:
        """格式化插件规则状态。"""
        if rule_record.status == "success":
            return f"{rule_record.plugin_name}: {len(rule_record.translate_rules)} 条规则"
        return f"{rule_record.plugin_name}: 解析失败"

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
    def _count_translation_items(translation_data_map: dict[str, TranslationData]) -> int:
        """统计翻译数据中的条目数量。"""
        return sum(len(data.translation_items) for data in translation_data_map.values())

    @staticmethod
    def _build_translation_batches(
        *,
        translation_data_map: dict[str, TranslationData],
        setting: Setting,
        text_rules: TextRules,
    ) -> list[tuple[list[TranslationItem], list[ChatMessage]]]:
        """构建正文翻译批次。"""
        batches: list[tuple[list[TranslationItem], list[ChatMessage]]] = []
        for translation_data in translation_data_map.values():
            batches.extend(
                iter_translation_context_batches(
                    translation_data=translation_data,
                    token_size=setting.translation_context.token_size,
                    factor=setting.translation_context.factor,
                    max_command_items=setting.translation_context.max_command_items,
                    system_prompt=setting.text_translation.system_prompt,
                    text_rules=text_rules,
                )
            )
        return batches

    async def _run_text_translation_batches(
        self,
        *,
        text_translation: TextTranslation,
        game_title: str,
        batches: list[tuple[list[TranslationItem], list[ChatMessage]]],
        error_table_name: str,
        advance_progress: Callable[[int], None],
        translation_cache: TranslationCache,
    ) -> tuple[int, int]:
        """启动正文翻译并并发消费成功/失败队列。"""
        text_translation.start_translation(llm_handler=self.llm_handler, batches=batches)
        db_write_lock = asyncio.Lock()
        success_task = asyncio.create_task(
            self._consume_right_items(
                game_title=game_title,
                text_translation=text_translation,
                db_write_lock=db_write_lock,
                advance_progress=advance_progress,
                translation_cache=translation_cache,
            )
        )
        error_task = asyncio.create_task(
            self._consume_error_items(
                game_title=game_title,
                text_translation=text_translation,
                error_table_name=error_table_name,
                db_write_lock=db_write_lock,
                advance_progress=advance_progress,
                translation_cache=translation_cache,
            )
        )
        try:
            success_count, error_count = await asyncio.gather(success_task, error_task)
        finally:
            for task in (success_task, error_task):
                if not task.done():
                    _ = task.cancel()
            _ = await asyncio.gather(success_task, error_task, return_exceptions=True)

        logger.success(f"[tag.success]正文翻译结束[/tag.success] 游戏 [tag.count]{game_title}[/tag.count] 成功 [tag.count]{success_count}[/tag.count] 条，失败 [tag.count]{error_count}[/tag.count] 条")
        return success_count, error_count

    async def _consume_right_items(
        self,
        *,
        game_title: str,
        text_translation: TextTranslation,
        db_write_lock: asyncio.Lock,
        advance_progress: Callable[[int], None],
        translation_cache: TranslationCache,
    ) -> int:
        """消费正文翻译成功队列并写入主翻译表。"""
        success_count = 0
        async for items in text_translation.iter_right_items():
            expanded_items = self._expand_cached_translation_items(items, translation_cache)
            serialized_items: list[tuple[str, ItemType, str | None, list[str], list[str]]] = [
                (
                    item.location_path,
                    item.item_type,
                    item.role,
                    list(item.original_lines),
                    list(item.translation_lines),
                )
                for item in expanded_items
            ]
            async with db_write_lock:
                await self.game_database_manager.write_translation_items(game_title, serialized_items)
            success_count += len(expanded_items)
            advance_progress(len(expanded_items))
            logger.success(f"[tag.success]已写入正文翻译结果[/tag.success] 游戏 [tag.count]{game_title}[/tag.count] [tag.count]{len(expanded_items)}[/tag.count] 条")
        return success_count

    async def _consume_error_items(
        self,
        *,
        game_title: str,
        text_translation: TextTranslation,
        error_table_name: str,
        db_write_lock: asyncio.Lock,
        advance_progress: Callable[[int], None],
        translation_cache: TranslationCache,
    ) -> int:
        """消费正文翻译错误队列并写入错误表。"""
        error_count = 0
        async for error_items in text_translation.iter_error_items():
            expanded_error_items = self._expand_cached_error_items(error_items, translation_cache)
            async with db_write_lock:
                await self.game_database_manager.write_error_items(
                    game_title,
                    error_table_name,
                    expanded_error_items,
                )
            error_count += len(expanded_error_items)
            advance_progress(len(expanded_error_items))
            logger.error(f"[tag.failure]已写入错误记录[/tag.failure] 游戏 [tag.count]{game_title}[/tag.count] [tag.count]{len(expanded_error_items)}[/tag.count] 条")
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
            (
                _location_path,
                item_type,
                role,
                original_lines,
                translation_lines,
                error_type,
                error_detail,
            ) = error_item
            duplicate_items = translation_cache.pop_duplicate_items_by_fields(
                original_lines=original_lines,
                item_type=item_type,
                role=role,
            )
            for duplicate_item in duplicate_items:
                expanded_error_items.append(
                    (
                        duplicate_item.location_path,
                        duplicate_item.item_type,
                        duplicate_item.role,
                        list(duplicate_item.original_lines),
                        list(translation_lines),
                        error_type,
                        list(error_detail),
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
    def _format_exception_summary(error: Exception) -> str:
        """将异常压缩为适合日志首行展示的稳定摘要。"""
        message = str(error).strip()
        if message:
            return f"{type(error).__name__}: {message}"
        return type(error).__name__


__all__: list[str] = [
    "PluginTextAnalysisSummary",
    "TextTranslationSummary",
    "TranslationHandler",
]
