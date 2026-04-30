"""
核心 CLI 翻译编排模块。

本模块串起游戏注册、外部规则导入、正文翻译、缓存断点续传与游戏文件回写。
"""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar, Self

from app.application.file_writer import reset_writable_copies, write_game_files
from app.application.runtime import load_runtime_setting
from app.application.summaries import (
    NameContextImportSummary,
    NameContextWriteSummary,
    PluginJsonExportSummary,
    PluginRuleImportSummary,
    TextTranslationSummary,
)
from app.config.schemas import Setting
from app.name_context import (
    NameContextExportSummary,
    NamePromptIndex,
    apply_name_context_translations,
    export_name_context_files,
    load_name_context_registry,
)
from app.persistence import DEFAULT_ERROR_TABLE_PREFIX, GameDatabaseItem, GameDatabaseManager
from app.plugin_text import (
    PluginTextExtraction,
    build_plugin_hash,
    build_plugin_rule_records_from_import,
    export_plugins_json_file,
    load_plugin_rule_import_file,
)
from app.rmmz import DataTextExtraction
from app.rmmz.schema import (
    GameData,
    ItemType,
    PLUGINS_FILE_NAME,
    PluginTextRuleRecord,
    TranslationData,
    TranslationErrorItem,
    TranslationItem,
)
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

    async def import_plugin_rules(
        self,
        game_title: str,
        input_path: Path,
    ) -> PluginRuleImportSummary:
        """把外部插件规则 JSON 导入当前游戏数据库。"""
        game_data = self._get_game_data(game_title)
        import_file = await load_plugin_rule_import_file(input_path)
        rule_records = build_plugin_rule_records_from_import(
            game_title=game_title,
            game_data=game_data,
            import_file=import_file,
        )
        old_rules = {
            rule.plugin_index: rule
            for rule in await self.game_database_manager.read_plugin_text_rules(game_title)
        }
        deleted_translation_items = 0
        for rule_record in rule_records:
            old_rule = old_rules.get(rule_record.plugin_index)
            if self._should_refresh_plugin_translation_items(old_rule, rule_record):
                deleted_translation_items += await self.game_database_manager.delete_translation_items_by_prefixes(
                    game_title,
                    [f"{PLUGINS_FILE_NAME}/{rule_record.plugin_index}/"],
                )
        await self.game_database_manager.replace_plugin_text_rules(game_title, rule_records)
        imported_rule_count = sum(len(record.translate_rules) for record in rule_records)
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
        game_data = self._get_game_data(game_title)
        resolved_output_path = output_path.resolve()
        await export_plugins_json_file(game_data=game_data, output_path=resolved_output_path)
        logger.success(f"[tag.success]插件配置 JSON 导出完成[/tag.success] 游戏 [tag.count]{game_title}[/tag.count] 插件 [tag.count]{len(game_data.plugins_js)}[/tag.count] 个 文件 [tag.path]{resolved_output_path}[/tag.path]")
        return PluginJsonExportSummary(
            output_path=str(resolved_output_path),
            plugin_count=len(game_data.plugins_js),
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
        name_prompt_index = await self._load_name_prompt_index(game_title=game_title)

        translated_paths = await self.game_database_manager.read_translation_location_paths(game_title)
        plugin_rules = await self._read_fresh_plugin_text_rules(
            game_title=game_title,
            game_data=game_data,
        )
        translation_data_map = DataTextExtraction(game_data, text_rules).extract_all_text()
        plugin_translation_data_map = PluginTextExtraction(
            game_data=game_data,
            plugin_rule_records=plugin_rules,
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
            name_prompt_index=name_prompt_index,
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
            logger.info(f"[tag.phase]已清理错误表[/tag.phase] 游戏 [tag.count]{game_title}[/tag.count] 数量 [tag.count]{deleted_error_tables}[/tag.count]")

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
        name_written_count = await self._apply_optional_name_context_write_back(
            game_title=game_title,
            game_data=game_data,
        )

        write_game_files(game_data=game_data, game_root=game_database_item.game_path)
        logger.success(f"[tag.success]游戏文本回写完成[/tag.success] 游戏 [tag.count]{game_title}[/tag.count] data 文本 [tag.count]{data_item_count}[/tag.count] 条，插件文本 [tag.count]{plugin_item_count}[/tag.count] 条，标准名 [tag.count]{name_written_count}[/tag.count] 条")

    async def export_name_context(
        self,
        game_title: str,
        output_dir: Path,
    ) -> NameContextExportSummary:
        """导出 `101` 名字框与地图显示名上下文文件。"""
        game_data = self._get_game_data(game_title)
        summary = await export_name_context_files(
            game_title=game_title,
            game_data=game_data,
            output_dir=output_dir,
        )
        logger.success(f"[tag.success]标准名上下文导出完成[/tag.success] 游戏 [tag.count]{game_title}[/tag.count] 大 JSON [tag.path]{summary.registry_path}[/tag.path] 小 JSON [tag.count]{summary.context_file_count}[/tag.count] 个")
        return summary

    async def import_name_context(
        self,
        game_title: str,
        input_path: Path,
    ) -> NameContextImportSummary:
        """把外部 Agent 填写后的术语表 JSON 导入当前游戏数据库。"""
        registry = await load_name_context_registry(registry_path=input_path)
        if registry.game_title != game_title:
            raise ValueError(
                f"术语表导入文件的 game_title 不匹配，期望 {game_title}，实际 {registry.game_title}"
            )
        await self.game_database_manager.replace_name_context_registry(game_title, registry)
        filled_count = sum(1 for entry in registry.entries if entry.translated_text.strip())
        logger.success(f"[tag.success]术语表导入完成[/tag.success] 游戏 [tag.count]{game_title}[/tag.count] 条目 [tag.count]{len(registry.entries)}[/tag.count] 条，已填写 [tag.count]{filled_count}[/tag.count] 条")
        return NameContextImportSummary(
            imported_entry_count=len(registry.entries),
            filled_entry_count=filled_count,
        )

    async def write_name_context(
        self,
        game_title: str,
        callbacks: tuple[Callable[[int, int], None], Callable[[int], None]],
    ) -> NameContextWriteSummary:
        """根据数据库中的术语表写回 `101` 名字框与地图显示名。"""
        set_progress, advance_progress = callbacks
        game_data = self._get_game_data(game_title)
        game_database_item = self._get_game_database_item(game_title)
        translated_items = await self.game_database_manager.read_translated_items(game_title)

        reset_writable_copies(game_data)
        if translated_items:
            write_data_text(game_data, translated_items)
            write_plugin_text(game_data, translated_items)

        registry = await self.game_database_manager.read_name_context_registry(game_title)
        if registry is None:
            raise RuntimeError("当前游戏数据库中没有已导入术语表，请先执行 import-name-context")
        written_count = apply_name_context_translations(game_data, registry)
        set_progress(0, max(written_count, 1))
        advance_progress(written_count)
        write_game_files(game_data=game_data, game_root=game_database_item.game_path)
        logger.success(f"[tag.success]标准名写回完成[/tag.success] 游戏 [tag.count]{game_title}[/tag.count] 写回 [tag.count]{written_count}[/tag.count] 条，保留已有正文译文 [tag.count]{len(translated_items)}[/tag.count] 条")
        return NameContextWriteSummary(written_count=written_count, preserved_translation_count=len(translated_items))

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

    async def _load_name_prompt_index(
        self,
        *,
        game_title: str,
    ) -> NamePromptIndex | None:
        """读取数据库术语表，并转换为正文提示词索引。"""
        registry = await self.game_database_manager.read_name_context_registry(game_title)
        if registry is None:
            logger.info(f"[tag.skip]数据库没有已导入术语表，正文提示词不注入标准名[/tag.skip] 游戏 [tag.count]{game_title}[/tag.count]")
            return None

        index = NamePromptIndex.from_registry(registry)
        logger.info(f"[tag.phase]已加载术语表[/tag.phase] 游戏 [tag.count]{game_title}[/tag.count] 可注入译名 [tag.count]{len(index.entries)}[/tag.count] 条")
        return index

    async def _apply_optional_name_context_write_back(
        self,
        *,
        game_title: str,
        game_data: GameData,
    ) -> int:
        """在正文回写时顺手写回数据库术语表中的标准名。"""
        registry = await self.game_database_manager.read_name_context_registry(game_title)
        if registry is None:
            logger.info(f"[tag.skip]数据库未发现术语表，跳过 101 名字框和地图名写回[/tag.skip] 游戏 [tag.count]{game_title}[/tag.count]")
            return 0
        return apply_name_context_translations(game_data, registry)

    async def _read_fresh_plugin_text_rules(
        self,
        *,
        game_title: str,
        game_data: GameData,
    ) -> list[PluginTextRuleRecord]:
        """读取匹配当前 plugins.js 的外部插件规则。"""
        plugin_rules = await self.game_database_manager.read_plugin_text_rules(game_title)
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
            logger.warning(f"[tag.warning]检测到过期插件规则，正文翻译将跳过这些规则[/tag.warning] 游戏 [tag.count]{game_title}[/tag.count] 数量 [tag.count]{stale_count}[/tag.count]")
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
            or old_rule.translate_rules != new_rule.translate_rules
        )

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
        name_prompt_index: NamePromptIndex | None,
    ) -> list[tuple[list[TranslationItem], list[ChatMessage]]]:
        """构建正文翻译批次。"""
        batches: list[tuple[list[TranslationItem], list[ChatMessage]]] = []
        for file_name, translation_data in translation_data_map.items():
            batches.extend(
                iter_translation_context_batches(
                    translation_data=translation_data,
                    token_size=setting.translation_context.token_size,
                    factor=setting.translation_context.factor,
                    max_command_items=setting.translation_context.max_command_items,
                    system_prompt=setting.text_translation.system_prompt,
                    text_rules=text_rules,
                    file_name=file_name,
                    name_prompt_index=name_prompt_index,
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
    "NameContextImportSummary",
    "NameContextWriteSummary",
    "PluginJsonExportSummary",
    "PluginRuleImportSummary",
    "TextTranslationSummary",
    "TranslationHandler",
]
