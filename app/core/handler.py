"""
多游戏新栈总编排模块。

本模块提供独立于旧 `handler.py` 的新编排入口。
它以 `game_title` 作为显式目标参数，统一串起：
1. 游戏注册与预加载。
2. 术语构建。
3. 正文翻译与错误表重翻。
4. 译文回写。
"""

import asyncio
import copy
import json
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Self

from dishka import AsyncContainer, make_async_container

from app.config.schemas import Setting
from app.core.game_data_manager import GameDataManager
from app.core.di import TranslationProvider
from app.database.db import (
    DEFAULT_ERROR_TABLE_PREFIX,
    GameDatabaseItem,
    GameDatabaseManager,
)
from app.extraction import DataTextExtraction, GlossaryExtraction, PluginTextExtraction
from app.models.schemas import (
    DATA_DIRECTORY_NAME,
    DATA_ORIGIN_DIRECTORY_NAME,
    ErrorRetryItem,
    GameData,
    Glossary,
    ItemType,
    JS_DIRECTORY_NAME,
    PLUGINS_FILE_NAME,
    PLUGINS_ORIGIN_FILE_NAME,
    TranslationData,
    TranslationErrorItem,
    TranslationItem,
)
from app.services.llm import LLMHandler
from app.services.llm.schemas import ChatMessage
from app.translation import (
    GlossaryTranslation,
    TextTranslation,
    TranslationCache,
    iter_error_retry_context_batches,
    iter_translation_context_batches,
)
from app.utils.database_utils import read_game_title, resolve_game_directory
from app.utils.log_utils import logger
from app.write_back.data_text_write_back import write_data_text
from app.write_back.glossary_write_back import write_glossary
from app.write_back.plugin_text_write_back import write_plugin_text



class TranslationHandler:
    """
    多游戏新栈的翻译业务总编排器。

    Attributes:
        _container: Dishka 根容器，用于派生请求级依赖。
        game_data_manager: APP 级游戏数据管理器。
        game_database_manager: APP 级数据库管理器。
        llm_handler: APP 级共享模型调度器。
    """

    ERROR_TABLE_PREFIX: str = DEFAULT_ERROR_TABLE_PREFIX

    def __init__(
        self,
        container: AsyncContainer,
        game_data_manager: GameDataManager,
        game_database_manager: GameDatabaseManager,
        llm_handler: LLMHandler,
    ) -> None:
        """
        初始化多游戏编排器。

        Args:
            container: 已构建好的 Dishka 根容器。
            game_data_manager: APP 级游戏数据管理器。
            game_database_manager: APP 级数据库管理器。
            llm_handler: APP 级共享模型调度器。
        """
        self._container: AsyncContainer = container
        self.game_data_manager: GameDataManager = game_data_manager
        self.game_database_manager: GameDatabaseManager = game_database_manager
        self.llm_handler: LLMHandler = llm_handler

    @classmethod
    async def create(cls, provider: TranslationProvider) -> Self:
        """
        创建新栈编排器，并预加载当前数据库管理器里已有游戏的数据。

        Args:
            provider: 新栈依赖提供器。

        Returns:
            已完成 APP 级依赖绑定与游戏数据预加载的编排器实例。
        """
        container: AsyncContainer = make_async_container(provider)
        try:
            game_data_manager = await container.get(GameDataManager)
            game_database_manager = await container.get(GameDatabaseManager)
            llm_handler = await container.get(LLMHandler)
            handler = cls(
                container=container,
                game_data_manager=game_data_manager,
                game_database_manager=game_database_manager,
                llm_handler=llm_handler,
            )
            preloaded_count = 0
            skipped_count = 0

            for item in game_database_manager.items.values():
                try:
                    await game_data_manager.load_game_data(item.game_path)
                    preloaded_count += 1
                except Exception as error:
                    skipped_count += 1
                    logger.warning(
                        f"[tag.warning]游戏预加载失败，已跳过该游戏[/tag.warning] "
                        f"标题 [tag.count]{item.game_title}[/tag.count] "
                        f"路径 [tag.path]{item.game_path}[/tag.path] "
                        f"原因：{cls._format_exception_summary(error)}"
                    )

            logger.info(
                "[tag.phase]新编排器初始化完成[/tag.phase] "
                f"成功预加载 [tag.count]{preloaded_count}[/tag.count] 个游戏，"
                f"跳过 [tag.count]{skipped_count}[/tag.count] 个无效路径"
            )
            return handler
        except Exception:
            await container.close()
            raise

    async def close(self) -> None:
        """
        关闭根容器并释放 APP 级资源。

        Returns:
            None。
        """
        await self._container.close()

    async def add_game(self, game_path: str | Path) -> str:
        """
        注册一个新的游戏到多游戏新栈。

        Args:
            game_path: RPG Maker 游戏根目录路径。

        Returns:
            最终登记到管理器中的 `game_title`。
        """
        resolved_game_path = resolve_game_directory(game_path)
        game_title = read_game_title(resolved_game_path)

        try:
            await self.game_database_manager.create_database(resolved_game_path)
            game_database_item = self._get_game_database_item(game_title)
            await self.game_data_manager.load_game_data(game_database_item.game_path)

            logger.success(
                f"[tag.success]游戏已加入新栈[/tag.success] "
                f"标题 [tag.count]{game_title}[/tag.count] "
                f"路径 [tag.path]{game_database_item.game_path}[/tag.path]"
            )
            return game_title
        except Exception:
            raise

    async def build_glossary(
        self,
        game_title: str,
        callbacks: tuple[Callable[[int, int], None], Callable[[int], None]],
    ) -> None:
        """
        为指定游戏构建术语表。

        Args:
            game_title: 目标游戏标题。
            callbacks: 术语构建进度回调元组。
        """
        set_progress, advance_progress = callbacks

        try:
            async with self._container() as request_container:
                setting = await request_container.get(Setting)
                glossary_translation = await request_container.get(GlossaryTranslation)

                game_data = self._get_game_data(game_title)
                glossary_extraction = GlossaryExtraction(game_data)
                sampled_role_lines = glossary_extraction.extract_role_dialogue_chunks(
                    chunk_blocks=setting.glossary_extraction.role_chunk_blocks,
                    chunk_lines=setting.glossary_extraction.role_chunk_lines,
                )
                display_names = glossary_extraction.extract_display_names()
                expected_role_names: set[str] = set(sampled_role_lines)
                expected_display_names: set[str] = set(display_names)
                existing_glossary = await self.game_database_manager.read_glossary(
                    game_title
                )
                total = len(expected_role_names) + len(expected_display_names)
                current = 0

                set_progress(0, total)

                if existing_glossary is not None and self._is_glossary_complete(
                    glossary=existing_glossary,
                    expected_role_names=expected_role_names,
                    expected_display_names=expected_display_names,
                ):
                    logger.info(
                        f"[tag.skip]术语表已完整存在，跳过构建[/tag.skip] "
                        f"游戏 [tag.count]{game_title}[/tag.count]"
                    )
                    set_progress(total, total)
                    return

                if not expected_role_names and not expected_display_names:
                    await self.game_database_manager.replace_glossary(
                        game_title,
                        Glossary(),
                    )
                    logger.info(
                        f"[tag.skip]未提取到可翻译术语，已写入空术语表[/tag.skip] "
                        f"游戏 [tag.count]{game_title}[/tag.count]"
                    )
                    return

                logger.info(
                    f"[tag.phase]术语提取[/tag.phase] 游戏 [tag.count]{game_title}[/tag.count] "
                    f"角色名 [tag.count]{len(expected_role_names)}[/tag.count] 条，"
                    f"地点名 [tag.count]{len(expected_display_names)}[/tag.count] 条"
                )

                roles = []
                if sampled_role_lines:
                    logger.info(
                        f"[tag.phase]角色术语翻译[/tag.phase] 游戏 [tag.count]{game_title}[/tag.count]"
                    )
                    async for role_chunk in glossary_translation.translate_role_names(
                        llm_handler=self.llm_handler,
                        role_lines=sampled_role_lines,
                    ):
                        roles.extend(role_chunk)
                        current += len(role_chunk)
                        advance_progress(len(role_chunk))

                places = []
                if display_names:
                    logger.info(
                        f"[tag.phase]地点术语翻译[/tag.phase] 游戏 [tag.count]{game_title}[/tag.count]"
                    )
                    async for (
                        place_chunk
                    ) in glossary_translation.translate_display_names(
                        llm_handler=self.llm_handler,
                        display_names=display_names,
                        roles=roles,
                    ):
                        places.extend(place_chunk)
                        current += len(place_chunk)
                        advance_progress(len(place_chunk))

                glossary = Glossary(roles=roles, places=places)
                await self.game_database_manager.replace_glossary(game_title, glossary)
                logger.success(
                    f"[tag.success]术语表构建完成[/tag.success] 游戏 [tag.count]{game_title}[/tag.count] "
                    f"角色术语 [tag.count]{len(glossary.roles)}[/tag.count] 条，"
                    f"地点术语 [tag.count]{len(glossary.places)}[/tag.count] 条"
                )
                if current < total:
                    set_progress(current, total)
        except Exception:
            raise

    async def translate_text(
        self,
        game_title: str,
        callbacks: tuple[
            Callable[[int, int], None],
            Callable[[int], None],
            Callable[[str], None],
        ],
    ) -> None:
        """
        为指定游戏执行正文翻译。

        Args:
            game_title: 目标游戏标题。
            callbacks: 正文翻译回调元组。
        """
        set_progress, advance_progress, set_status = callbacks

        try:
            async with self._container() as request_container:
                setting = await request_container.get(Setting)
                text_translation = await request_container.get(TextTranslation)
                translation_cache = await request_container.get(TranslationCache)

                game_data = self._get_game_data(game_title)
                glossary_extraction = GlossaryExtraction(game_data)
                data_text_extraction = DataTextExtraction(game_data)
                plugin_text_extraction = PluginTextExtraction(game_data)

                glossary = await self.game_database_manager.read_glossary(game_title)
                role_lines = glossary_extraction.extract_role_dialogue_chunks(
                    chunk_blocks=setting.glossary_extraction.role_chunk_blocks,
                    chunk_lines=setting.glossary_extraction.role_chunk_lines,
                )
                display_names = glossary_extraction.extract_display_names()

                if glossary is None or not self._is_glossary_complete(
                    glossary=glossary,
                    expected_role_names=set(role_lines),
                    expected_display_names=set(display_names),
                ):
                    logger.warning(
                        f"[tag.warning]术语表缺失或不完整，正文翻译流程已终止[/tag.warning] "
                        f"游戏 [tag.count]{game_title}[/tag.count]"
                    )
                    set_progress(0, 0)
                    set_status("术语表缺失或不完整，正文翻译流程已终止")
                    return

                translation_data_map: dict[str, TranslationData] = {}
                translation_data_map.update(data_text_extraction.extract_all_text())
                translation_data_map.update(plugin_text_extraction.extract_all_text())

                total_extracted_items = self._count_translation_items(
                    translation_data_map
                )
                if total_extracted_items == 0:
                    logger.info(
                        f"[tag.skip]未提取到可翻译正文[/tag.skip] 游戏 [tag.count]{game_title}[/tag.count]"
                    )
                    set_progress(0, 0)
                    set_status("未提取到可翻译正文")
                    return

                translated_location_paths = (
                    await self.game_database_manager.read_translation_location_paths(
                        game_title
                    )
                )
                pending_translation_data = self._filter_pending_translation_data(
                    translation_data_map=translation_data_map,
                    translated_location_paths=translated_location_paths,
                )
                pending_count = self._count_translation_items(pending_translation_data)
                set_progress(0, pending_count)

                logger.info(
                    f"[tag.phase]正文提取[/tag.phase] 游戏 [tag.count]{game_title}[/tag.count] "
                    f"共 [tag.count]{total_extracted_items}[/tag.count] 条"
                )
                set_status(f"正文提取完成，共 {total_extracted_items} 条")

                if pending_count == 0:
                    logger.info(
                        f"[tag.skip]没有需要新增翻译的正文[/tag.skip] 游戏 [tag.count]{game_title}[/tag.count]"
                    )
                    set_status("没有需要新增翻译的正文")
                    return

                deduplicated_translation_data = self._deduplicate_translation_data(
                    translation_data_map=pending_translation_data,
                    translation_cache=translation_cache,
                )
                deduplicated_count = self._count_translation_items(
                    deduplicated_translation_data
                )
                saved_count = pending_count - deduplicated_count
                saved_ratio = 0.0
                if pending_count > 0:
                    saved_ratio = saved_count / pending_count

                logger.info(
                    f"[tag.phase]正文缓存[/tag.phase] 游戏 [tag.count]{game_title}[/tag.count] "
                    f"过滤后 [tag.count]{pending_count}[/tag.count] 条，"
                    f"实际送模 [tag.count]{deduplicated_count}[/tag.count] 条，"
                    f"节省 [tag.count]{saved_count}[/tag.count] 条 ({saved_ratio:.2%})"
                )
                set_status(
                    f"去重后实际送模 {deduplicated_count} 条，节省 "
                    f"{saved_count} 条（{saved_ratio:.2%}）"
                )

                batches = self._build_translation_batches(
                    translation_data_map=deduplicated_translation_data,
                    glossary=glossary,
                    setting=setting,
                )
                if not batches:
                    logger.warning(
                        f"[tag.warning]没有构建出可用的翻译批次[/tag.warning] "
                        f"游戏 [tag.count]{game_title}[/tag.count]"
                    )
                    set_status("没有构建出可用的翻译批次")
                    return

                logger.info(
                    f"[tag.phase]正文上下文[/tag.phase] 游戏 [tag.count]{game_title}[/tag.count] "
                    f"共构建 [tag.count]{len(batches)}[/tag.count] 个翻译批次"
                )
                set_status(f"已构建 {len(batches)} 个翻译批次")

                success_count, error_count = await self._run_text_translation_batches(
                    game_title=game_title,
                    text_translation=text_translation,
                    batches=batches,
                    start_log_message="正文翻译任务已启动",
                    finish_log_message="正文翻译流程结束",
                    advance_progress=advance_progress,
                    set_status=set_status,
                    translation_cache=translation_cache,
                )
                set_status(
                    f"正文翻译完成，成功 {success_count} 条，失败 {error_count} 条"
                )
        except Exception as error:
            set_status(f"正文翻译失败：{error}")
            raise

    async def retry_error_table(
        self,
        game_title: str,
        callbacks: tuple[
            Callable[[int, int], None],
            Callable[[int], None],
            Callable[[str], None],
        ],
    ) -> None:
        """
        为指定游戏重翻最近一张错误表。

        Args:
            game_title: 目标游戏标题。
            callbacks: 错误重翻回调元组。
        """
        set_progress, advance_progress, set_status = callbacks

        try:
            async with self._container() as request_container:
                setting = await request_container.get(Setting)
                text_translation = await request_container.get(TextTranslation)
                translation_cache = await request_container.get(TranslationCache)

                game_data = self._get_game_data(game_title)
                glossary_extraction = GlossaryExtraction(game_data)
                glossary = await self.game_database_manager.read_glossary(game_title)
                role_lines = glossary_extraction.extract_role_dialogue_chunks(
                    chunk_blocks=setting.glossary_extraction.role_chunk_blocks,
                    chunk_lines=setting.glossary_extraction.role_chunk_lines,
                )
                display_names = glossary_extraction.extract_display_names()

                if glossary is None or not self._is_glossary_complete(
                    glossary=glossary,
                    expected_role_names=set(role_lines),
                    expected_display_names=set(display_names),
                ):
                    logger.warning(
                        f"[tag.warning]术语表缺失或不完整，错误表重翻译流程已终止[/tag.warning] "
                        f"游戏 [tag.count]{game_title}[/tag.count]"
                    )
                    set_progress(0, 0)
                    set_status("术语表缺失或不完整，错误表重翻译流程已终止")
                    return

                latest_error_table_name = (
                    await self.game_database_manager.read_latest_error_table_name(
                        game_title,
                        self.ERROR_TABLE_PREFIX,
                    )
                )
                if latest_error_table_name is None:
                    logger.info(
                        f"[tag.skip]数据库中没有可重翻译的错误表[/tag.skip] "
                        f"游戏 [tag.count]{game_title}[/tag.count]"
                    )
                    set_progress(0, 0)
                    set_status("数据库中没有可重翻译的错误表")
                    return

                error_retry_items = (
                    await self.game_database_manager.read_error_retry_items(
                        game_title,
                        latest_error_table_name,
                    )
                )
                set_progress(0, len(error_retry_items))

                if not error_retry_items:
                    logger.info(
                        f"[tag.skip]最新错误表中没有可重翻译记录[/tag.skip] "
                        f"游戏 [tag.count]{game_title}[/tag.count] "
                        f"表名 [tag.path]{latest_error_table_name}[/tag.path]"
                    )
                    set_status("最新错误表中没有可重翻译记录")
                    return

                logger.info(
                    f"[tag.phase]错误表读取[/tag.phase] 游戏 [tag.count]{game_title}[/tag.count] "
                    f"表名 [tag.path]{latest_error_table_name}[/tag.path] "
                    f"共 [tag.count]{len(error_retry_items)}[/tag.count] 条记录"
                )
                set_status(
                    f"错误表读取完成，表名 {latest_error_table_name}，"
                    f"共 {len(error_retry_items)} 条记录"
                )

                batches = self._build_error_retry_batches(
                    error_retry_items=error_retry_items,
                    glossary=glossary,
                    setting=setting,
                )
                if not batches:
                    logger.warning(
                        f"[tag.warning]没有构建出可用的错误重翻译批次[/tag.warning] "
                        f"游戏 [tag.count]{game_title}[/tag.count]"
                    )
                    set_status("没有构建出可用的错误重翻译批次")
                    return

                logger.info(
                    f"[tag.phase]错误重翻译上下文[/tag.phase] 游戏 [tag.count]{game_title}[/tag.count] "
                    f"共构建 [tag.count]{len(batches)}[/tag.count] 个翻译批次"
                )
                set_status(f"已构建 {len(batches)} 个错误重翻译批次")

                success_count, error_count = await self._run_text_translation_batches(
                    game_title=game_title,
                    text_translation=text_translation,
                    batches=batches,
                    start_log_message="错误表重翻译任务已启动",
                    finish_log_message="错误表重翻译流程结束",
                    advance_progress=advance_progress,
                    set_status=set_status,
                    translation_cache=translation_cache,
                )
                set_status(
                    f"错误表重翻译完成，成功 {success_count} 条，失败 {error_count} 条"
                )
        except Exception as error:
            set_status(f"错误表重翻译失败：{error}")
            raise

    async def write_back(
        self,
        game_title: str,
        callbacks: tuple[Callable[[int, int], None], Callable[[int], None]],
    ) -> None:
        """
        将指定游戏的术语与正文译文回写到游戏目录。

        Args:
            game_title: 目标游戏标题。
            callbacks: 回写进度回调元组。
        """
        set_progress, advance_progress = callbacks
        game_data = self._get_game_data(game_title)
        game_database_item = self._get_game_database_item(game_title)
        glossary = await self.game_database_manager.read_glossary(game_title)
        translated_items = await self.game_database_manager.read_translated_items(
            game_title
        )
        glossary_count = 0
        if glossary is not None:
            glossary_count = len(glossary.roles) + len(glossary.places)
        total = glossary_count + len(translated_items)
        set_progress(0, total)

        try:
            if glossary is None and not translated_items:
                logger.warning(
                    f"[tag.warning]未找到可回写数据[/tag.warning] 游戏 [tag.count]{game_title}[/tag.count]"
                )
                return

            self._reset_writable_copies(game_data)

            if glossary is not None:
                write_glossary(game_data, glossary)
                logger.success(
                    f"[tag.success]术语表回写完成[/tag.success] 游戏 [tag.count]{game_title}[/tag.count] "
                    f"角色术语 [tag.count]{len(glossary.roles)}[/tag.count] 条，"
                    f"地点术语 [tag.count]{len(glossary.places)}[/tag.count] 条"
                )
                glossary_delta = len(glossary.roles) + len(glossary.places)
                if glossary_delta > 0:
                    advance_progress(glossary_delta)
            else:
                logger.warning(
                    f"[tag.warning]数据库中不存在术语表，本次只回写正文数据[/tag.warning] "
                    f"游戏 [tag.count]{game_title}[/tag.count]"
                )

            if translated_items:
                write_data_text(game_data, translated_items)
                write_plugin_text(game_data, translated_items)
                logger.success(
                    f"[tag.success]正文回写完成[/tag.success] 游戏 [tag.count]{game_title}[/tag.count] "
                    f"共处理 [tag.count]{len(translated_items)}[/tag.count] 条译文"
                )
                advance_progress(len(translated_items))
            else:
                logger.warning(
                    f"[tag.warning]数据库中没有可回写的正文译文[/tag.warning] "
                    f"游戏 [tag.count]{game_title}[/tag.count]"
                )

            self._write_game_files(
                game_data=game_data,
                game_root=game_database_item.game_path,
            )
            active_data_dir, origin_data_dir, _active_plugins_path, _origin_plugins_path = (
                self._build_game_layout_paths(game_database_item.game_path)
            )
            logger.success(
                f"[tag.success]激活版游戏文件已重建[/tag.success] "
                f"游戏 [tag.count]{game_title}[/tag.count] "
                f"原件数据 [tag.path]{origin_data_dir}[/tag.path] "
                f"激活数据 [tag.path]{active_data_dir}[/tag.path]"
            )
        except Exception:
            raise

    def _get_game_data(self, game_title: str) -> GameData:
        """
        根据标题读取已加载的游戏数据。

        Args:
            game_title: 目标游戏标题。

        Returns:
            已加载的 `GameData`。

        Raises:
            ValueError: 不存在对应标题的游戏数据时抛出。
        """
        game_data = self.game_data_manager.items.get(game_title)
        if game_data is None:
            raise ValueError(f"未找到游戏数据: {game_title}")
        return game_data

    def _get_game_database_item(self, game_title: str) -> GameDatabaseItem:
        """
        根据标题读取已加载的数据库对象。

        Args:
            game_title: 目标游戏标题。

        Returns:
            对应的数据库对象。

        Raises:
            ValueError: 不存在对应标题的数据库对象时抛出。
        """
        game_database_item = self.game_database_manager.items.get(game_title)
        if game_database_item is None:
            raise ValueError(f"未找到游戏数据库: {game_title}")
        return game_database_item

    @staticmethod
    def _is_glossary_complete(
        glossary: Glossary,
        expected_role_names: set[str],
        expected_display_names: set[str],
    ) -> bool:
        """
        校验术语表是否与当前提取结果完全一致。

        Args:
            glossary: 当前数据库中的术语表。
            expected_role_names: 当前游戏重新提取出的角色名集合。
            expected_display_names: 当前游戏重新提取出的地点名集合。

        Returns:
            术语表完整可用时返回 `True`。
        """
        if {role.name for role in glossary.roles} != expected_role_names:
            return False
        if {place.name for place in glossary.places} != expected_display_names:
            return False
        if any(not role.translated_name.strip() for role in glossary.roles):
            return False
        if any(role.gender not in ("男", "女", "未知") for role in glossary.roles):
            return False
        if any(not place.translated_name.strip() for place in glossary.places):
            return False
        return True

    @staticmethod
    def _filter_pending_translation_data(
        translation_data_map: dict[str, TranslationData],
        translated_location_paths: set[str],
    ) -> dict[str, TranslationData]:
        """
        过滤已经存在于主翻译表中的正文条目。

        Args:
            translation_data_map: 当前提取出的全部正文数据。
            translated_location_paths: 主翻译表里已经完成的路径集合。

        Returns:
            只包含待翻译条目的新字典。
        """
        pending_translation_data: dict[str, TranslationData] = {}

        for file_name, translation_data in translation_data_map.items():
            pending_items = [
                item
                for item in translation_data.translation_items
                if item.location_path not in translated_location_paths
            ]
            if not pending_items:
                continue

            pending_translation_data[file_name] = TranslationData(
                display_name=translation_data.display_name,
                translation_items=pending_items,
            )

        return pending_translation_data

    @staticmethod
    def _deduplicate_translation_data(
        translation_data_map: dict[str, TranslationData],
        translation_cache: TranslationCache,
    ) -> dict[str, TranslationData]:
        """
        通过请求级缓存过滤本轮重复正文。

        Args:
            translation_data_map: 经过断点过滤后的待翻译正文。
            translation_cache: 当前请求使用的正文去重缓存。

        Returns:
            去重后的正文数据字典。
        """
        deduplicated_translation_data: dict[str, TranslationData] = {}

        for file_name, translation_data in translation_data_map.items():
            deduplicated_items = [
                item
                for item in translation_data.translation_items
                if translation_cache.remember_or_defer(item)
            ]
            if not deduplicated_items:
                continue

            deduplicated_translation_data[file_name] = TranslationData(
                display_name=translation_data.display_name,
                translation_items=deduplicated_items,
            )

        return deduplicated_translation_data

    @staticmethod
    def _count_translation_items(
        translation_data_map: dict[str, TranslationData],
    ) -> int:
        """
        统计翻译数据字典中的条目总数。

        Args:
            translation_data_map: 文件到正文数据的映射。

        Returns:
            全部 `TranslationItem` 的总数。
        """
        return sum(
            len(translation_data.translation_items)
            for translation_data in translation_data_map.values()
        )

    @staticmethod
    def _build_translation_batches(
        translation_data_map: dict[str, TranslationData],
        glossary: Glossary,
        setting: Setting,
    ) -> list[tuple[list[TranslationItem], list[ChatMessage]]]:
        """
        把待翻译正文构造成上下文批次。

        Args:
            translation_data_map: 待翻译正文。
            glossary: 已完成的术语表。
            setting: 当前请求配置。

        Returns:
            可直接送入正文翻译器的批次列表。
        """
        context_setting = setting.translation_context
        batches: list[tuple[list[TranslationItem], list[ChatMessage]]] = []

        for translation_data in translation_data_map.values():
            batches.extend(
                iter_translation_context_batches(
                    translation_data=translation_data,
                    token_size=context_setting.token_size,
                    factor=context_setting.factor,
                    max_command_items=context_setting.max_command_items,
                    system_prompt=setting.text_translation.system_prompt,
                    glossary=glossary,
                )
            )

        return batches

    @staticmethod
    def _build_error_retry_batches(
        error_retry_items: list[ErrorRetryItem],
        glossary: Glossary,
        setting: Setting,
    ) -> list[tuple[list[TranslationItem], list[ChatMessage]]]:
        """
        把错误表条目构造成错误重翻译批次。

        Args:
            error_retry_items: 当前错误表读取出的全部错误条目。
            glossary: 已完成的术语表。
            setting: 当前请求配置。

        Returns:
            可直接送入正文翻译器的错误重翻译批次列表。
        """
        return list(
            iter_error_retry_context_batches(
                error_retry_items=error_retry_items,
                chunk_size=setting.error_translation.chunk_size,
                system_prompt=setting.error_translation.system_prompt,
                glossary=glossary,
            )
        )

    async def _run_text_translation_batches(
        self,
        *,
        game_title: str,
        text_translation: TextTranslation,
        batches: list[tuple[list[TranslationItem], list[ChatMessage]]],
        start_log_message: str,
        finish_log_message: str,
        advance_progress: Callable[[int], None],
        set_status: Callable[[str], None],
        translation_cache: TranslationCache | None = None,
    ) -> tuple[int, int]:
        """
        统一执行一轮正文类翻译任务。

        Args:
            game_title: 目标游戏标题。
            text_translation: 正文翻译器实例。
            batches: 已构造完成的翻译批次。
            start_log_message: 启动日志文案。
            finish_log_message: 完成日志文案。
            advance_progress: 推进进度条的回调。
            set_status: 更新状态文本的回调。
            translation_cache: 可选的请求级正文去重缓存。

        Returns:
            成功条数与失败条数。
        """
        error_table_name = await self.game_database_manager.start_error_table(
            game_title,
            self.ERROR_TABLE_PREFIX,
        )
        logger.info(
            f"[tag.phase]错误表已创建[/tag.phase] 游戏 [tag.count]{game_title}[/tag.count] "
            f"表名 [tag.path]{error_table_name}[/tag.path]"
        )
        set_status(f"错误表已创建：{error_table_name}")

        text_translation.start_translation(
            llm_handler=self.llm_handler,
            batches=batches,
        )
        logger.info(
            f"[tag.phase]任务启动[/tag.phase] 游戏 [tag.count]{game_title}[/tag.count] "
            f"{start_log_message}"
        )
        set_status(start_log_message)

        db_write_lock = asyncio.Lock()
        background_tasks: list[asyncio.Task[int]] = [
            asyncio.create_task(
                self._consume_right_items(
                    game_title=game_title,
                    text_translation=text_translation,
                    db_write_lock=db_write_lock,
                    advance_progress=advance_progress,
                    translation_cache=translation_cache,
                )
            ),
            asyncio.create_task(
                self._consume_error_items(
                    game_title=game_title,
                    text_translation=text_translation,
                    error_table_name=error_table_name,
                    db_write_lock=db_write_lock,
                    advance_progress=advance_progress,
                    translation_cache=translation_cache,
                )
            ),
        ]

        try:
            success_count, error_count = await asyncio.gather(*background_tasks)
        finally:
            for task in background_tasks:
                if task.done():
                    continue
                task.cancel()
            await asyncio.gather(*background_tasks, return_exceptions=True)

        logger.success(
            f"[tag.success]{finish_log_message}[/tag.success] "
            f"游戏 [tag.count]{game_title}[/tag.count] "
            f"共处理 [tag.count]{success_count + error_count}[/tag.count] 条"
        )
        return success_count, error_count

    async def _consume_right_items(
        self,
        *,
        game_title: str,
        text_translation: TextTranslation,
        db_write_lock: asyncio.Lock,
        advance_progress: Callable[[int], None],
        translation_cache: TranslationCache | None,
    ) -> int:
        """
        消费正文翻译成功队列并写入主翻译表。
        """
        success_count = 0

        async for items in text_translation.iter_right_items():
            expanded_items = self._expand_cached_translation_items(
                items=items,
                translation_cache=translation_cache,
            )
            serialized_items: list[
                tuple[str, ItemType, str | None, list[str], list[str]]
            ] = [
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
                await self.game_database_manager.write_translation_items(
                    game_title,
                    serialized_items,
                )

            success_count += len(expanded_items)
            advance_progress(len(expanded_items))
            logger.success(
                f"[tag.success]已写入正文翻译结果[/tag.success] "
                f"游戏 [tag.count]{game_title}[/tag.count] "
                f"[tag.count]{len(expanded_items)}[/tag.count] 条"
            )

        return success_count

    async def _consume_error_items(
        self,
        *,
        game_title: str,
        text_translation: TextTranslation,
        error_table_name: str,
        db_write_lock: asyncio.Lock,
        advance_progress: Callable[[int], None],
        translation_cache: TranslationCache | None,
    ) -> int:
        """
        消费正文翻译错误队列并写入当前错误表。
        """
        error_count = 0

        async for error_items in text_translation.iter_error_items():
            expanded_error_items = self._expand_cached_error_items(
                error_items=error_items,
                translation_cache=translation_cache,
            )

            async with db_write_lock:
                await self.game_database_manager.write_error_items(
                    game_title,
                    error_table_name,
                    expanded_error_items,
                )

            error_count += len(expanded_error_items)
            advance_progress(len(expanded_error_items))
            logger.error(
                f"[tag.failure]已写入错误记录[/tag.failure] "
                f"游戏 [tag.count]{game_title}[/tag.count] "
                f"[tag.count]{len(expanded_error_items)}[/tag.count] 条"
            )

        return error_count

    @staticmethod
    def _expand_cached_error_items(
        error_items: list[TranslationErrorItem],
        translation_cache: TranslationCache | None,
    ) -> list[TranslationErrorItem]:
        """
        在错误落库前展开与失败正文同键的重复条目。
        """
        if translation_cache is None:
            return error_items

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
        translation_cache: TranslationCache | None,
    ) -> list[TranslationItem]:
        """
        在成功写库前展开与首条正文同键的重复条目。

        Args:
            items: 当前批次已经成功校验的正文条目。
            translation_cache: 可选的请求级正文去重缓存。

        Returns:
            已经补回重复条目的完整写库列表。
        """
        if translation_cache is None:
            return items

        expanded_items: list[TranslationItem] = []
        for item in items:
            expanded_items.append(item)
            duplicate_items = translation_cache.pop_duplicate_items(item)
            for duplicate_item in duplicate_items:
                duplicate_item.translation_lines = list(item.translation_lines)
                expanded_items.append(duplicate_item)

        return expanded_items

    @staticmethod
    def _reset_writable_copies(game_data: GameData) -> None:
        """
        重置游戏数据的可写副本。

        Args:
            game_data: 目标游戏数据对象。
        """
        game_data.writable_data = copy.deepcopy(game_data.data)
        game_data.writable_plugins_js = copy.deepcopy(game_data.plugins_js)

    @staticmethod
    def _write_game_files(game_data: GameData, game_root: Path) -> None:
        """
        基于原件重建新的激活版 `data/` 与 `plugins.js`。

        Args:
            game_data: 已完成回写修改的游戏数据对象。
            game_root: 当前游戏根目录。
        """
        js_dir = game_root / JS_DIRECTORY_NAME
        js_dir.mkdir(parents=True, exist_ok=True)
        (
            active_data_dir,
            origin_data_dir,
            active_plugins_path,
            origin_plugins_path,
        ) = TranslationHandler._build_game_layout_paths(game_root)
        has_origin_backup = TranslationHandler._validate_origin_backup_state(
            origin_data_dir=origin_data_dir,
            origin_plugins_path=origin_plugins_path,
        )

        staged_data_dir = Path(
            tempfile.mkdtemp(prefix="write_back_data_", dir=game_root)
        )
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".js",
            prefix="write_back_plugins_",
            dir=js_dir,
            delete=False,
        ) as temp_plugins_file:
            staged_plugins_path = Path(temp_plugins_file.name)

        try:
            if not has_origin_backup:
                TranslationHandler._create_origin_backup(
                    active_data_dir=active_data_dir,
                    origin_data_dir=origin_data_dir,
                    active_plugins_path=active_plugins_path,
                    origin_plugins_path=origin_plugins_path,
                )

            TranslationHandler._stage_game_files(
                game_data=game_data,
                source_data_dir=origin_data_dir,
                source_plugins_path=origin_plugins_path,
                staged_data_dir=staged_data_dir,
                staged_plugins_path=staged_plugins_path,
            )

            if has_origin_backup:
                TranslationHandler._replace_active_layout(
                    game_root=game_root,
                    active_data_dir=active_data_dir,
                    active_plugins_path=active_plugins_path,
                    staged_data_dir=staged_data_dir,
                    staged_plugins_path=staged_plugins_path,
                )
            else:
                TranslationHandler._create_active_layout_from_stage(
                    active_data_dir=active_data_dir,
                    active_plugins_path=active_plugins_path,
                    staged_data_dir=staged_data_dir,
                    staged_plugins_path=staged_plugins_path,
                )
        except Exception:
            if not has_origin_backup:
                TranslationHandler._rollback_initial_backup_failure(
                    active_data_dir=active_data_dir,
                    origin_data_dir=origin_data_dir,
                    active_plugins_path=active_plugins_path,
                    origin_plugins_path=origin_plugins_path,
                )
            raise
        finally:
            TranslationHandler._cleanup_path(staged_data_dir)
            TranslationHandler._cleanup_path(staged_plugins_path)

    @staticmethod
    def _stage_game_files(
        game_data: GameData,
        source_data_dir: Path,
        source_plugins_path: Path,
        staged_data_dir: Path,
        staged_plugins_path: Path,
    ) -> None:
        """
        基于原件生成一份可供切换的激活版临时目录。

        Args:
            game_data: 已完成内存回写的游戏数据对象。
            source_data_dir: 本轮构建激活版所依赖的原件数据目录。
            source_plugins_path: 本轮构建激活版所依赖的原件插件配置路径。
            staged_data_dir: 临时激活版 `data/` 目录路径。
            staged_plugins_path: 临时激活版 `plugins.js` 文件路径。
        """
        shutil.rmtree(staged_data_dir, ignore_errors=True)
        shutil.copytree(source_data_dir, staged_data_dir)
        shutil.copy2(source_plugins_path, staged_plugins_path)

        for file_name, data in game_data.writable_data.items():
            if file_name == PLUGINS_FILE_NAME:
                TranslationHandler._write_plugins_file(
                    plugins_path=staged_plugins_path,
                    data=data,
                )
                continue

            target_path = staged_data_dir / file_name
            target_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    @staticmethod
    def _build_game_layout_paths(game_root: Path) -> tuple[Path, Path, Path, Path]:
        """
        构造当前游戏目录下激活版与原件备份的全部核心路径。

        Args:
            game_root: 游戏根目录。
        Returns:
            依次返回：激活数据目录、原件数据目录、激活插件配置、原件插件配置。
        """
        active_data_dir = game_root / DATA_DIRECTORY_NAME
        origin_data_dir = game_root / DATA_ORIGIN_DIRECTORY_NAME
        active_plugins_path = game_root / JS_DIRECTORY_NAME / PLUGINS_FILE_NAME
        origin_plugins_path = game_root / JS_DIRECTORY_NAME / PLUGINS_ORIGIN_FILE_NAME
        return (
            active_data_dir,
            origin_data_dir,
            active_plugins_path,
            origin_plugins_path,
        )

    @staticmethod
    def _validate_origin_backup_state(
        origin_data_dir: Path,
        origin_plugins_path: Path,
    ) -> bool:
        """
        校验原件备份是否处于一致状态。

        Args:
            origin_data_dir: 原件数据目录路径。
            origin_plugins_path: 原件插件配置路径。

        Returns:
            两者同时存在时返回 `True`，两者同时不存在时返回 `False`。

        Raises:
            ValueError: 只存在其一时抛出。
        """
        has_origin_data_dir = origin_data_dir.exists()
        has_origin_plugins_path = origin_plugins_path.exists()
        if has_origin_data_dir != has_origin_plugins_path:
            raise ValueError(
                "检测到半成品翻译布局：`data_origin/` 与 `js/plugins_origin.js` 必须同时存在或同时不存在"
            )
        return has_origin_data_dir

    @staticmethod
    def _create_origin_backup(
        active_data_dir: Path,
        origin_data_dir: Path,
        active_plugins_path: Path,
        origin_plugins_path: Path,
    ) -> None:
        """
        首次回写前，将当前激活版目录重命名为原件备份。

        Args:
            active_data_dir: 当前激活数据目录。
            origin_data_dir: 目标原件数据目录。
            active_plugins_path: 当前激活插件配置文件。
            origin_plugins_path: 目标原件插件配置文件。
        """
        if not active_data_dir.exists():
            raise FileNotFoundError(f"激活数据目录不存在: {active_data_dir}")
        if not active_plugins_path.exists():
            raise FileNotFoundError(f"激活插件配置文件不存在: {active_plugins_path}")

        active_data_dir.rename(origin_data_dir)
        try:
            active_plugins_path.rename(origin_plugins_path)
        except Exception:
            origin_data_dir.rename(active_data_dir)
            raise

    @staticmethod
    def _create_active_layout_from_stage(
        active_data_dir: Path,
        active_plugins_path: Path,
        staged_data_dir: Path,
        staged_plugins_path: Path,
    ) -> None:
        """
        在首次备份完成后，把临时激活版写回默认运行路径。

        Args:
            active_data_dir: 当前激活数据目录。
            active_plugins_path: 当前激活插件配置文件。
            staged_data_dir: 已生成好的临时激活版 `data/` 目录。
            staged_plugins_path: 已生成好的临时激活版 `plugins.js` 文件。
        """
        staged_data_dir.rename(active_data_dir)
        try:
            staged_plugins_path.replace(active_plugins_path)
        except Exception:
            shutil.rmtree(active_data_dir, ignore_errors=True)
            raise

    @staticmethod
    def _replace_active_layout(
        game_root: Path,
        active_data_dir: Path,
        active_plugins_path: Path,
        staged_data_dir: Path,
        staged_plugins_path: Path,
    ) -> None:
        """
        在已翻译布局上重新生成新的激活版目录。

        Args:
            game_root: 游戏根目录。
            active_data_dir: 当前激活数据目录。
            active_plugins_path: 当前激活插件配置文件。
            staged_data_dir: 已生成好的临时激活版 `data/` 目录。
            staged_plugins_path: 已生成好的临时激活版 `plugins.js` 文件。
        """
        rollback_root = Path(
            tempfile.mkdtemp(prefix="write_back_rollback_", dir=game_root)
        )
        rollback_data_dir = rollback_root / "data"
        rollback_plugins_path = rollback_root / PLUGINS_FILE_NAME
        data_swapped = False
        plugins_swapped = False

        try:
            if active_data_dir.exists():
                active_data_dir.rename(rollback_data_dir)

            staged_data_dir.rename(active_data_dir)
            data_swapped = True

            if active_plugins_path.exists():
                active_plugins_path.rename(rollback_plugins_path)

            staged_plugins_path.replace(active_plugins_path)
            plugins_swapped = True
        except Exception:
            if data_swapped and active_data_dir.exists():
                shutil.rmtree(active_data_dir, ignore_errors=True)
            if rollback_data_dir.exists():
                rollback_data_dir.rename(active_data_dir)

            if plugins_swapped and active_plugins_path.exists():
                active_plugins_path.unlink()
            if rollback_plugins_path.exists():
                rollback_plugins_path.rename(active_plugins_path)
            raise
        finally:
            shutil.rmtree(rollback_root, ignore_errors=True)

    @staticmethod
    def _rollback_initial_backup_failure(
        active_data_dir: Path,
        origin_data_dir: Path,
        active_plugins_path: Path,
        origin_plugins_path: Path,
    ) -> None:
        """
        首次回写失败后，把原件备份恢复回默认运行路径。

        Args:
            active_data_dir: 当前激活数据目录。
            origin_data_dir: 原件数据目录。
            active_plugins_path: 当前激活插件配置文件。
            origin_plugins_path: 原件插件配置文件。
        """
        if active_data_dir.exists():
            shutil.rmtree(active_data_dir, ignore_errors=True)
        if active_plugins_path.exists():
            active_plugins_path.unlink()

        if origin_data_dir.exists():
            origin_data_dir.rename(active_data_dir)
        if origin_plugins_path.exists():
            origin_plugins_path.rename(active_plugins_path)

    @staticmethod
    def _write_plugins_file(plugins_path: Path, data: object) -> None:
        """
        将插件配置文本写入目标文件。

        Args:
            plugins_path: 目标 `plugins.js` 路径。
            data: 已序列化的 JS 文本，或仍为普通 JSON 对象的兜底内容。
        """
        if isinstance(data, str):
            plugins_path.write_text(data, encoding="utf-8")
            return

        plugins_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _cleanup_path(target_path: Path) -> None:
        """
        清理临时目录或临时文件。

        Args:
            target_path: 待清理的路径。
        """
        if target_path.is_dir():
            shutil.rmtree(target_path, ignore_errors=True)
        elif target_path.exists():
            target_path.unlink()

    @staticmethod
    def _format_exception_summary(error: Exception) -> str:
        """
        将异常压缩为适合日志首行展示的稳定摘要。

        Args:
            error: 当前捕获到的异常对象。

        Returns:
            `异常类型: 异常信息` 形式的简短摘要；若异常消息为空则仅返回类型名。
        """
        current_error: BaseException = error
        while isinstance(current_error, BaseExceptionGroup):
            if not current_error.exceptions:
                break
            current_error = current_error.exceptions[0]

        message = str(current_error).strip()
        if message:
            return f"{type(current_error).__name__}: {message}"
        return type(current_error).__name__


__all__: list[str] = ["TranslationHandler"]
