"""
翻译总编排模块。

总编排器只负责业务流程编排。
进程级依赖在启动阶段准备好并注入，请求级依赖在执行具体动作时按次获取。
"""

import asyncio
import copy
import json
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite
from dishka import AsyncContainer, make_async_container
from rich.progress import Progress, TaskID

from app.config import Setting
from app.core.di import TranslationProvider
from app.database.db import TranslationDB
from app.extraction import DataTextExtraction, GlossaryExtraction, PluginTextExtraction
from app.models.schemas import (
    ErrorRetryItem,
    GameData,
    Glossary,
    GlossaryBuildChunk,
    ItemType,
    PLUGINS_FILE_NAME,
    Place,
    Role,
    TranslationData,
    TranslationItem,
)
from app.services.llm import LLMHandler
from app.services.llm.schemas import ChatMessage
from app.translation import (
    GlossaryTranslation,
    TextTranslation,
    iter_error_retry_context_batches,
    iter_translation_context_batches,
)
from app.utils import get_progress, logger
from app.write_back.data_text_write_back import write_data_text
from app.write_back.glossary_write_back import write_glossary
from app.write_back.plugin_text_write_back import write_plugin_text


class TranslationHandler:
    """
    翻译业务总编排器。

    该对象已经绑定好进程级依赖，只暴露四类业务动作：
    术语表构建、正文翻译、错误表重翻译、回写。
    """

    ERROR_TABLE_PREFIX: str = "translation_errors"

    def __init__(
        self,
        provider: TranslationProvider,
        setting: Setting,
        game_data: GameData,
        llm_handler: LLMHandler,
        translation_db: TranslationDB,
    ) -> None:
        """
        初始化总编排器。

        参数:
            provider: 统一依赖提供器，请求级依赖仍经由它创建。
            setting: 已解析完成的运行时配置。
            game_data: 已加载完成的游戏数据。
            llm_handler: 已注册服务的大模型门面。
            translation_db: 已初始化完成的翻译数据库门面。
        """
        self.provider: TranslationProvider = provider
        self.setting: Setting = setting
        self.game_data: GameData = game_data
        self.llm_handler: LLMHandler = llm_handler
        self.translation_db: TranslationDB = translation_db
        self._app_container: AsyncContainer | None = None

    @classmethod
    async def create(cls, provider: TranslationProvider) -> "TranslationHandler":
        """通过提供器构造编排器，并缓存进程级容器。"""
        app_container: AsyncContainer = make_async_container(provider)
        handler = cls(
            provider=provider,
            setting=await app_container.get(Setting),
            game_data=await app_container.get(GameData),
            llm_handler=await app_container.get(LLMHandler),
            translation_db=await app_container.get(TranslationDB),
        )
        handler._app_container = app_container
        return handler

    async def build_glossary(self) -> AsyncIterator[GlossaryBuildChunk]:
        """
        构建并持久化结构化术语表工作流。

        此方法从游戏文件中提取原始角色和地图名称，交由大模型进行翻译，并将最终的结果落入数据库中。
        它是一个流式生成器，会不断产出当前已翻译完成的术语分块，便于界面层实时渲染进度。

        产出:
            术语分块对象，包含当前批次翻译完成的角色术语或地点术语。
        """
        async with self._open_request_scope() as request_container:
            glossary_extraction: GlossaryExtraction = await request_container.get(
                GlossaryExtraction
            )
            glossary_translation: GlossaryTranslation = await request_container.get(
                GlossaryTranslation
            )

            sampled_role_lines: dict[str, list[str]] = (
                glossary_extraction.extract_role_dialogue_chunks(
                    chunk_blocks=self.setting.glossary_extraction.role_chunk_blocks,
                    chunk_lines=self.setting.glossary_extraction.role_chunk_lines,
                )
            )
            display_names: dict[str, str] = glossary_extraction.extract_display_names()

            expected_role_names: set[str] = set(sampled_role_lines.keys())
            expected_display_names: set[str] = set(display_names.keys())
            existing_glossary: (
                Glossary | None
            ) = await self.translation_db.read_glossary()
            role_chunk_count: int = 0
            display_chunk_count: int = 0

            if expected_role_names:
                role_chunk_size: int = (
                    self.setting.glossary_translation.role_name.chunk_size
                )
                role_chunk_count = (
                    len(expected_role_names) + role_chunk_size - 1
                ) // role_chunk_size
            if expected_display_names:
                display_chunk_size: int = (
                    self.setting.glossary_translation.display_name.chunk_size
                )
                display_chunk_count = (
                    len(expected_display_names) + display_chunk_size - 1
                ) // display_chunk_size

            glossary_total_steps: int = role_chunk_count + display_chunk_count + 2

            with get_progress() as progress:
                progress_task_id: TaskID = progress.add_task(
                    description="术语表准备中",
                    total=glossary_total_steps,
                )

                if existing_glossary is not None and self._is_glossary_complete(
                    glossary=existing_glossary,
                    expected_role_names=expected_role_names,
                    expected_display_names=expected_display_names,
                ):
                    logger.info(
                        "[tag.skip]术语表已完整存在，跳过构建[/tag.skip]"
                    )
                    self._finish_progress_task(
                        progress=progress,
                        task_id=progress_task_id,
                        description="术语表已完整，跳过构建",
                    )
                    return

                if not expected_role_names and not expected_display_names:
                    empty_glossary: Glossary = Glossary()
                    await self.translation_db.replace_glossary(empty_glossary)
                    logger.info(
                        "[tag.skip]未提取到可翻译术语，已写入空术语表[/tag.skip]"
                    )
                    self._finish_progress_task(
                        progress=progress,
                        task_id=progress_task_id,
                        description="未提取到术语，已写入空术语表",
                    )
                    return

                logger.info(
                    f"[tag.phase]术语提取[/tag.phase] 角色名 [tag.count]{len(expected_role_names)}[/tag.count] 条，"
                    f"地点名 [tag.count]{len(expected_display_names)}[/tag.count] 条"
                )
                progress.advance(progress_task_id, 1)

                roles: list[Role] = []
                if sampled_role_lines:
                    logger.info("[tag.phase]角色术语[/tag.phase] 开始翻译")
                    progress.update(progress_task_id, description="角色术语翻译中")
                    async for role_chunk in glossary_translation.translate_role_names(
                        llm_handler=self.llm_handler,
                        role_lines=sampled_role_lines,
                    ):
                        roles.extend(role_chunk)
                        progress.advance(progress_task_id, 1)
                        yield GlossaryBuildChunk(kind="roles", items=role_chunk)
                    logger.success(
                        f"[tag.success]角色术语翻译完成[/tag.success] 共 [tag.count]{len(roles)}[/tag.count] 条"
                    )

                places: list[Place] = []
                if display_names:
                    logger.info("[tag.phase]地点术语[/tag.phase] 开始翻译")
                    progress.update(progress_task_id, description="地点术语翻译中")
                    async for (
                        place_chunk
                    ) in glossary_translation.translate_display_names(
                        llm_handler=self.llm_handler,
                        display_names=display_names,
                        roles=roles,
                    ):
                        places.extend(place_chunk)
                        progress.advance(progress_task_id, 1)
                        yield GlossaryBuildChunk(kind="places", items=place_chunk)
                    logger.success(
                        f"[tag.success]地点术语翻译完成[/tag.success] 共 [tag.count]{len(places)}[/tag.count] 条"
                    )

                progress.update(progress_task_id, description="术语表写入数据库中")
                glossary: Glossary = Glossary(roles=roles, places=places)
                await self.translation_db.replace_glossary(glossary)
                logger.success("[tag.success]术语表构建完成并已写入数据库[/tag.success]")
                progress.advance(progress_task_id, 1)
                progress.update(progress_task_id, description="术语表构建完成")

    async def translate_text(self) -> None:
        """
        启动全量正文（含插件）翻译工作流。

        这是系统的核心翻译枢纽，流程如下：
        1. 依赖性校验：如果术语表不存在或不完整，直接阻断流程。
        2. 全量提取：从数据目录和 `plugins.js` 中抽取所有潜在待翻译的文本。
        3. 增量过滤：读取数据库中已有的译文路径，从待翻译集合中剔除已翻译项。
        4. 批次构造：基于令牌限制、段落连续性要求及已完成的术语表，构造请求上下文。
        5. 并发调度：将任务委托给多个后台协程并发执行，编排器自身负责收集结果、更新进度条、写入主数据库或错误表。
        """
        async with self._open_request_scope() as request_container:
            glossary_extraction: GlossaryExtraction = await request_container.get(
                GlossaryExtraction
            )
            data_text_extraction: DataTextExtraction = await request_container.get(
                DataTextExtraction
            )
            plugin_text_extraction: PluginTextExtraction = await request_container.get(
                PluginTextExtraction
            )
            text_translation: TextTranslation = await request_container.get(
                TextTranslation
            )

            # 步骤 2: 正文翻译前必须重新校验术语表完整性，避免基于过期术语继续翻译正文。
            glossary: Glossary | None = await self.translation_db.read_glossary()
            role_lines: dict[str, list[str]] = (
                glossary_extraction.extract_role_dialogue_chunks(
                    chunk_blocks=self.setting.glossary_extraction.role_chunk_blocks,
                    chunk_lines=self.setting.glossary_extraction.role_chunk_lines,
                )
            )
            display_names: dict[str, str] = glossary_extraction.extract_display_names()

            if glossary is None or not self._is_glossary_complete(
                glossary=glossary,
                expected_role_names=set(role_lines.keys()),
                expected_display_names=set(display_names.keys()),
            ):
                logger.warning(
                    "[tag.warning]术语表缺失或不完整，正文翻译流程已终止[/tag.warning]"
                )
                return

            logger.info("[tag.phase]正文翻译[/tag.phase] 术语表检查通过，开始提取正文")

            # 步骤 3: 汇总 data 与 plugins.js 两路提取结果，后续流程统一按同一种正文模型处理。
            translation_data_map: dict[str, TranslationData] = {}
            translation_data_map.update(data_text_extraction.extract_all_text())
            translation_data_map.update(plugin_text_extraction.extract_all_text())

            total_extracted_items: int = self._count_translation_items(
                translation_data_map
            )
            if total_extracted_items == 0:
                logger.info("[tag.skip]未提取到可翻译正文[/tag.skip]")
                return

            logger.info(
                f"[tag.phase]正文提取[/tag.phase] 共 [tag.count]{total_extracted_items}[/tag.count] 条"
            )

            # 步骤 4: 主翻译表在数据库初始化阶段已经建好，这里只需要读取已完成译文路径。
            translated_location_paths: set[
                str
            ] = await self.translation_db.read_translation_location_paths()
            logger.info(
                f"[tag.phase]正文断点[/tag.phase] 已读取 [tag.count]{len(translated_location_paths)}[/tag.count] 条已完成路径"
            )

            # 步骤 5: 只要路径已经存在于完成译文表中，就直接从待翻译集合里剔除。
            pending_translation_data: dict[str, TranslationData] = (
                self._filter_pending_translation_data(
                    translation_data_map=translation_data_map,
                    translated_location_paths=translated_location_paths,
                )
            )
            pending_count: int = self._count_translation_items(pending_translation_data)
            if pending_count == 0:
                logger.info("[tag.skip]没有需要新增翻译的正文[/tag.skip]")
                return

            logger.info(
                f"[tag.phase]正文过滤[/tag.phase] 剩余 [tag.count]{pending_count}[/tag.count] 条待翻译正文"
            )

            # 步骤 6: 把待翻译正文构造成“条目列表 + 消息上下文”的批次，交给正文翻译器调度。
            batches: list[tuple[list[TranslationItem], list[ChatMessage]]] = (
                self._build_translation_batches(
                    translation_data_map=pending_translation_data,
                    glossary=glossary,
                )
            )
            if not batches:
                logger.warning("[tag.warning]没有构建出可用的翻译批次[/tag.warning]")
                return

            logger.info(
                f"[tag.phase]正文上下文[/tag.phase] 共构建 [tag.count]{len(batches)}[/tag.count] 个翻译批次"
            )

            await self._run_text_translation_batches(
                text_translation=text_translation,
                batches=batches,
                total_items=pending_count,
                progress_description="正在翻译正文",
                completed_description=f"正文翻译完成，共处理 {pending_count} 条",
                start_log_message="正文翻译任务已启动",
                finish_log_message="正文翻译流程结束",
            )

    async def retry_error_table(self) -> None:
        """
        对系统中最近一次生成的错误记录表执行“修Bug”重翻译工作流。

        当正文翻译出现漏翻、控制符丢失、日文残留等情况时，这些脏数据不会被写入主库，而是落入错误表。
        此工作流会读取最新的错误表，将错误详情连同原文一起发给大模型强制纠错。
        如果本次纠错成功，则存入主库；如果再次失败，则会生成一张基于当前时间戳的新错误表。
        """
        async with self._open_request_scope() as request_container:
            glossary_extraction: GlossaryExtraction = await request_container.get(
                GlossaryExtraction
            )
            text_translation: TextTranslation = await request_container.get(
                TextTranslation
            )

            glossary: Glossary | None = await self.translation_db.read_glossary()
            role_lines: dict[str, list[str]] = (
                glossary_extraction.extract_role_dialogue_chunks(
                    chunk_blocks=self.setting.glossary_extraction.role_chunk_blocks,
                    chunk_lines=self.setting.glossary_extraction.role_chunk_lines,
                )
            )
            display_names: dict[str, str] = glossary_extraction.extract_display_names()

            if glossary is None or not self._is_glossary_complete(
                glossary=glossary,
                expected_role_names=set(role_lines.keys()),
                expected_display_names=set(display_names.keys()),
            ):
                logger.warning(
                    "[tag.warning]术语表缺失或不完整，错误表重翻译流程已终止[/tag.warning]"
                )
                return

            latest_error_table_name: (
                str | None
            ) = await self.translation_db.read_latest_error_table_name(
                self.ERROR_TABLE_PREFIX
            )
            if latest_error_table_name is None:
                logger.info("[tag.skip]数据库中没有可重翻译的错误表[/tag.skip]")
                return

            error_retry_items: list[
                ErrorRetryItem
            ] = await self.translation_db.read_error_retry_items(
                latest_error_table_name
            )
            if not error_retry_items:
                logger.info(
                    f"[tag.skip]最新错误表 [tag.path]{latest_error_table_name}[/tag.path] 中没有可重翻译的记录[/tag.skip]"
                )
                return

            logger.info(
                f"[tag.phase]错误表读取[/tag.phase] [tag.path]{latest_error_table_name}[/tag.path] 共 "
                f"[tag.count]{len(error_retry_items)}[/tag.count] 条记录"
            )
            batches: list[tuple[list[TranslationItem], list[ChatMessage]]] = (
                self._build_error_retry_batches(
                    error_retry_items=error_retry_items,
                    glossary=glossary,
                )
            )
            if not batches:
                logger.warning("[tag.warning]没有构建出可用的错误重翻译批次[/tag.warning]")
                return

            logger.info(
                f"[tag.phase]错误重翻译上下文[/tag.phase] 共构建 [tag.count]{len(batches)}[/tag.count] 个翻译批次"
            )
            await self._run_text_translation_batches(
                text_translation=text_translation,
                batches=batches,
                total_items=len(error_retry_items),
                progress_description="正在重翻错误表",
                completed_description=f"错误表重翻译完成，共处理 {len(error_retry_items)} 条",
                start_log_message="错误表重翻译任务已启动",
                finish_log_message="错误表重翻译流程结束",
            )

    async def write_back(self) -> None:
        """
        执行文件回写工作流。

        将数据库中积累的最终翻译成果（包括术语表和所有的正文、插件译文），
        还原写入到 `GameData` 的可写副本中，最后序列化落盘到游戏原始的 `data` 与 `js` 目录。
        为了避免多次重复回写导致数据错乱污染，该方法每次执行前都会从原始内存数据进行一次干净的深拷贝重置。
        """
        with get_progress() as progress:
            progress_task_id: TaskID = progress.add_task(
                description="回写准备中",
                total=5,
            )

            # 步骤 1: 先分别读取术语表和正文译文，两者任一存在时都允许继续回写。
            glossary: Glossary | None = await self.translation_db.read_glossary()
            translated_items: list[
                TranslationItem
            ] = await self._load_translated_items()
            progress.advance(progress_task_id, 1)

            if glossary is None and not translated_items:
                logger.warning(
                    "[tag.warning]未找到术语表，也没有可回写的正文翻译数据[/tag.warning]"
                )
                self._finish_progress_task(
                    progress=progress,
                    task_id=progress_task_id,
                    description="无可回写数据，流程结束",
                )
                return

            # 步骤 2: 每次回写前都恢复干净副本，确保重复执行时不会把旧结果叠加写回。
            progress.update(progress_task_id, description="重置可写副本中")
            self._reset_writable_copies()
            logger.info("[tag.phase]回写准备[/tag.phase] 已重置可写副本")
            progress.advance(progress_task_id, 1)

            # 步骤 3: 术语表负责改动系统名词等全局位置，因此优先执行。
            progress.update(progress_task_id, description="术语表回写中")
            if glossary is not None:
                write_glossary(self.game_data, glossary)
                logger.success(
                    f"[tag.success]术语表回写完成[/tag.success] 角色术语 [tag.count]{len(glossary.roles)}[/tag.count] 条，"
                    f"地点术语 [tag.count]{len(glossary.places)}[/tag.count] 条"
                )
            else:
                logger.warning(
                    "[tag.warning]数据库中不存在术语表，本次只回写正文数据[/tag.warning]"
                )
            progress.advance(progress_task_id, 1)

            # 步骤 4: 正文回写拆成 data 与 plugin 两路，但都基于同一份已翻译条目列表。
            progress.update(progress_task_id, description="正文回写中")
            if translated_items:
                write_data_text(self.game_data, translated_items)
                write_plugin_text(self.game_data, translated_items)
                logger.success(
                    f"[tag.success]正文回写完成[/tag.success] 共处理 [tag.count]{len(translated_items)}[/tag.count] 条已翻译数据"
                )
            else:
                logger.warning("[tag.warning]数据库中没有可回写的正文译文[/tag.warning]")
            progress.advance(progress_task_id, 1)

            # 步骤 5: 最后才统一把可写副本真正落到游戏目录，避免中途写出半更新状态。
            progress.update(progress_task_id, description="游戏文件写回中")
            self._write_game_files()
            logger.success("[tag.success]游戏文件已写回原始目录[/tag.success]")
            progress.advance(progress_task_id, 1)
            progress.update(progress_task_id, description="回写完成")

    def _open_request_scope(self) -> Any:
        """从已缓存的进程级容器或测试替身中打开一次请求作用域。"""
        if self._app_container is not None:
            return self._app_container()

        open_request_scope = getattr(self.provider, "open_request_scope", None)
        if callable(open_request_scope):
            return open_request_scope()

        raise RuntimeError("总编排器必须通过异步工厂方法创建")

    @staticmethod
    def _finish_progress_task(
        progress: Progress,
        task_id: TaskID,
        description: str,
    ) -> None:
        """
        把指定进度任务直接推进到完成状态。

        这个辅助函数只用于提前结束的分支，
        避免在多个返回点手写重复的“补满剩余进度”逻辑。
        """
        task_total = progress.tasks[task_id].total
        progress.update(task_id, description=description)
        if task_total is not None:
            progress.update(task_id, completed=task_total)

    def _is_glossary_complete(
        self,
        glossary: Glossary,
        expected_role_names: set[str],
        expected_display_names: set[str],
    ) -> bool:
        """
        校验术语表是否与当前提取结果完全一致且不存在空值。

        参数:
            glossary: 当前数据库中的术语表。
            expected_role_names: 本次提取得到的角色名键集合。
            expected_display_names: 本次提取得到的地图显示名键集合。

        返回:
            术语表完整时返回 `True`，否则返回 `False`。
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

    async def _read_table_safe(self, table_name: str) -> list[dict[str, Any]]:
        """
        安全读取数据库表。

        当表尚不存在时，这里直接返回空列表，避免把“首次运行未建表”当成异常分支。

        参数:
            table_name: 目标表名。

        返回:
            表中全部原始字典行。
        """
        try:
            return await self.translation_db.read_table(table_name)
        except aiosqlite.OperationalError as error:
            if "no such table" in str(error).lower():
                return []
            raise

    def _filter_pending_translation_data(
        self,
        translation_data_map: dict[str, TranslationData],
        translated_location_paths: set[str],
    ) -> dict[str, TranslationData]:
        """
        过滤已经翻译完成的正文条目。

        过滤规则：
        1. 若 `location_path` 已存在于完成译文表中，直接跳过。
        2. 若路径不存在，则保留到待翻译集合。

        参数:
            translation_data_map: 当前提取结果。
            translated_location_paths: 数据库中已完成译文的路径集合。

        返回:
            仅保留待翻译条目的新字典。
        """
        pending_translation_data: dict[str, TranslationData] = {}

        for file_name, translation_data in translation_data_map.items():
            pending_items: list[TranslationItem] = []

            for item in translation_data.translation_items:
                if item.location_path in translated_location_paths:
                    continue

                pending_items.append(item)

            if pending_items:
                pending_translation_data[file_name] = TranslationData(
                    display_name=translation_data.display_name,
                    translation_items=pending_items,
                )

        return pending_translation_data

    def _deserialize_lines(self, raw_value: Any) -> list[str]:
        """
        把数据库中的序列化文本或其他输入转换成字符串列表。

        参数:
            raw_value: 数据库字段原值。

        返回:
            归一化后的字符串列表。
        """
        if isinstance(raw_value, list):
            return [item for item in raw_value if isinstance(item, str)]

        if not isinstance(raw_value, str):
            return []

        try:
            parsed: Any = json.loads(raw_value)
        except json.JSONDecodeError:
            return []

        if not isinstance(parsed, list):
            return []

        return [item for item in parsed if isinstance(item, str)]

    def _normalize_item_type(self, raw_value: Any) -> ItemType | None:
        """
        将外部输入归一化为合法的 `ItemType`。

        参数:
            raw_value: 原始输入值。

        返回:
            合法时返回 `ItemType`，否则返回 `None`。
        """
        if raw_value == "long_text":
            return "long_text"
        if raw_value == "array":
            return "array"
        if raw_value == "short_text":
            return "short_text"
        return None

    def _has_completed_translation(self, translation_lines: list[str]) -> bool:
        """
        判断一条数据库记录是否已经包含有效译文。

        参数:
            translation_lines: 数据库中的译文行列表。

        返回:
            只要存在至少一行非空译文，就视为已翻译。
        """
        return any(line.strip() for line in translation_lines)

    def _count_translation_items(
        self,
        translation_data_map: dict[str, TranslationData],
    ) -> int:
        """
        统计翻译数据字典中的条目总数。

        参数:
            translation_data_map: 文件到翻译数据的映射。

        返回:
            全部 `TranslationItem` 的总数量。
        """
        return sum(
            len(translation_data.translation_items)
            for translation_data in translation_data_map.values()
        )

    def _build_translation_batches(
        self,
        translation_data_map: dict[str, TranslationData],
        glossary: Glossary,
    ) -> list[tuple[list[TranslationItem], list[ChatMessage]]]:
        """
        把待翻译正文构造成上下文批次。

        参数:
            translation_data_map: 待翻译正文数据。
            glossary: 已完成的术语表。

        返回:
            可直接交给 `TextTranslation.start_translation(...)` 的批次列表。
        """
        context_setting = self.setting.translation_context
        batches: list[tuple[list[TranslationItem], list[ChatMessage]]] = []

        for translation_data in translation_data_map.values():
            batches.extend(
                iter_translation_context_batches(
                    translation_data=translation_data,
                    token_size=context_setting.token_size,
                    factor=context_setting.factor,
                    max_command_items=context_setting.max_command_items,
                    system_prompt=self.setting.text_translation.system_prompt,
                    glossary=glossary,
                )
            )

        return batches

    def _build_error_retry_batches(
        self,
        error_retry_items: list[ErrorRetryItem],
        glossary: Glossary,
    ) -> list[tuple[list[TranslationItem], list[ChatMessage]]]:
        """
        把整张错误表构造成错误重翻译上下文批次。
        参数:
            error_retry_items: 最新错误表对应的错误重翻译条目列表。        glossary: 已完成的术语表。
        返回:
            可直接交给 `TextTranslation.start_translation(...)` 的批次列表。
        """
        error_context_setting = self.setting.error_translation
        return list(
            iter_error_retry_context_batches(
                error_retry_items=error_retry_items,
                chunk_size=error_context_setting.chunk_size,
                system_prompt=error_context_setting.system_prompt,
                glossary=glossary,
            )
        )

    async def _run_text_translation_batches(
        self,
        *,
        text_translation: TextTranslation,
        batches: list[tuple[list[TranslationItem], list[ChatMessage]]],
        total_items: int,
        progress_description: str,
        completed_description: str,
        start_log_message: str,
        finish_log_message: str,
    ) -> None:
        """
        统一执行一轮正文类翻译任务。
        Args:
            text_translation: 正文翻译服务实例。
            batches: 已构造完成的翻译批次。
            total_items: 本轮总条目数。
            progress_description: 进度条初始描述。
            completed_description: 进度条完成描述。
            start_log_message: 启动日志文案。
            finish_log_message: 收尾日志文案。
        返回:
            None。
        """
        error_table_name: str = self._build_error_table_name()
        await self.translation_db.create_error_table(error_table_name, [])
        logger.info(
            f"[tag.phase]错误表[/tag.phase] 已创建 [tag.path]{error_table_name}[/tag.path]"
        )

        with get_progress() as progress:
            progress_task_id: TaskID = progress.add_task(
                description=progress_description,
                total=total_items,
            )

            text_translation.start_translation(
                llm_handler=self.llm_handler,
                batches=batches,
            )
            logger.info(f"[tag.phase]任务启动[/tag.phase] {start_log_message}")

            db_write_lock: asyncio.Lock = asyncio.Lock()
            background_tasks: list[asyncio.Task[None]] = [
                asyncio.create_task(
                    self._consume_right_items(
                        text_translation=text_translation,
                        db_write_lock=db_write_lock,
                        progress=progress,
                        progress_task_id=progress_task_id,
                    )
                ),
                asyncio.create_task(
                    self._consume_error_items(
                        text_translation=text_translation,
                        error_table_name=error_table_name,
                        db_write_lock=db_write_lock,
                        progress=progress,
                        progress_task_id=progress_task_id,
                    )
                ),
            ]

            try:
                await asyncio.gather(*background_tasks)
            finally:
                for task in background_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(
                    *background_tasks,
                    return_exceptions=True,
                )

            progress.update(
                progress_task_id,
                description=completed_description,
                completed=total_items,
            )

        logger.success(f"[tag.success]{finish_log_message}[/tag.success]")

    def _build_error_table_name(self) -> str:
        """
        生成当前翻译任务对应的错误表名。

        Returns:
            形如 `translation_errors_20260310_041800` 的表名。
        """
        timestamp: str = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{self.ERROR_TABLE_PREFIX}_{timestamp}"

    async def _consume_right_items(
        self,
        text_translation: TextTranslation,
        db_write_lock: asyncio.Lock,
        progress: Progress,
        progress_task_id: TaskID,
    ) -> None:
        """
        持续消费正确队列并写入翻译主表。

        参数:
            text_translation: 正文翻译服务实例。
            db_write_lock: 数据库写锁，避免两个后台消费者并发写同一连接。
            progress: 编排层统一创建的进度条对象。
            progress_task_id: 正文翻译对应的进度任务编号。
        """
        async for items in text_translation.iter_right_items():
            # 队列里是结构化模型，入库前先转成数据库层要求的扁平元组。
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
                for item in items
            ]

            # 正确队列和错误队列共用一个数据库连接，所以这里必须串行写入。
            async with db_write_lock:
                await self.translation_db.write_translation_items(serialized_items)

            progress.advance(progress_task_id, len(items))
            logger.success(
                f"[tag.success]已写入正文翻译结果[/tag.success] [tag.count]{len(items)}[/tag.count] 条"
            )

    async def _consume_error_items(
        self,
        text_translation: TextTranslation,
        error_table_name: str,
        db_write_lock: asyncio.Lock,
        progress: Progress,
        progress_task_id: TaskID,
    ) -> None:
        """
        持续消费错误队列并写入当前错误表。

        参数:
            text_translation: 正文翻译服务实例。
            error_table_name: 当前翻译流程对应的错误表名。
            db_write_lock: 数据库写锁。
            progress: 编排层统一创建的进度条对象。
            progress_task_id: 正文翻译对应的进度任务编号。
        """
        async for error_items in text_translation.iter_error_items():
            # 错误记录按本次运行独立落到错误表，避免污染主翻译表。
            async with db_write_lock:
                await self.translation_db.create_error_table(
                    error_table_name,
                    error_items,
                )

            progress.advance(progress_task_id, len(error_items))
            logger.error(
                f"[tag.failure]已写入错误记录[/tag.failure] [tag.count]{len(error_items)}[/tag.count] 条"
            )

    async def _load_translated_items(self) -> list[TranslationItem]:
        """
        从数据库中读取所有已经有译文的条目。

        返回:
            可直接交给写回层的 `TranslationItem` 列表。
        """
        table_name: str = self.setting.project.translation_table_name
        rows: list[dict[str, Any]] = await self._read_table_safe(table_name)
        translated_items: list[TranslationItem] = []

        for row in rows:
            location_path = row.get("location_path")
            item_type: ItemType | None = self._normalize_item_type(row.get("item_type"))

            # 先过滤掉关键字段异常的数据行，避免把脏数据继续带到回写层。
            if not isinstance(location_path, str):
                continue
            if item_type is None:
                continue

            translation_lines: list[str] = self._deserialize_lines(
                row.get("translation_lines")
            )
            if not self._has_completed_translation(translation_lines):
                continue

            translated_items.append(
                TranslationItem(
                    location_path=location_path,
                    item_type=item_type,
                    role=row.get("role") if isinstance(row.get("role"), str) else None,
                    original_lines=self._deserialize_lines(row.get("original_lines")),
                    translation_lines=translation_lines,
                )
            )

        # 回写前按路径排序，是为了让相同输入在重复运行时尽量保持稳定处理顺序。
        translated_items.sort(key=lambda item: item.location_path)
        return translated_items

    def _reset_writable_copies(self) -> None:
        """
        重置 `GameData` 的可写副本。

        为什么要做这一步：
        1. 回写函数都会原地修改 `writable_data` / `writable_plugins_js`。
        2. 同一个编排器重复执行回写时，应始终从原始数据重新开始，避免多次叠写。
        """
        self.game_data.writable_data = copy.deepcopy(self.game_data.data)
        self.game_data.writable_plugins_js = copy.deepcopy(self.game_data.plugins_js)

    def _write_game_files(self) -> None:
        """
        将回写后的可写副本真正写回游戏目录。

        `plugins.js` 直接写文本，其余数据文件统一序列化为结构化文本。
        """
        game_root: Path = Path(self.setting.project.file_path)
        data_dir: Path = game_root / "data"
        js_dir: Path = game_root / "js"

        data_dir.mkdir(parents=True, exist_ok=True)
        js_dir.mkdir(parents=True, exist_ok=True)

        for file_name, data in self.game_data.writable_data.items():
            # `plugins.js` 在内存里可能保持字符串，也可能保持结构化数据，这里统一兜底序列化。
            if file_name == PLUGINS_FILE_NAME:
                plugins_path: Path = js_dir / PLUGINS_FILE_NAME
                if isinstance(data, str):
                    plugins_path.write_text(data, encoding="utf-8")
                else:
                    plugins_path.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                continue

            # 其余文件都视为数据目录下的结构化文件，统一用可读性较好的缩进格式写回。
            target_path: Path = data_dir / file_name
            target_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )


__all__: list[str] = ["TranslationHandler"]
