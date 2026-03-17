"""
术语翻译模块。

负责处理术语提取层输出的角色样本与地点名，并把模型响应解析为结构化的
`Role` 与 `Place` 对象。

设计约束：
1. 不兼容旧的后台 runner / queue 调用方式。
2. 只保留角色翻译与地点翻译两个对外入口。
3. 角色术语按单角色独立翻译，结果必须完整返回。
4. 每个工作单元都维护自己的重试上下文，不跨任务共享历史。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from itertools import batched
from typing import Literal

from json_repair import repair_json

from app.config import Setting
from app.models.schemas import Place, Role, SourceLanguage, strip_rm_control_sequences
from app.services.llm.handler import LLMHandler
from app.services.llm.schemas import ChatMessage
from app.utils import get_source_language_label
from app.utils.log_utils import logger

type GlossaryProgressPhase = Literal[
    "idle",
    "role_candidates",
    "display_names",
    "done",
]
type Gender = Literal["男", "女", "未知"]
type GlossaryProgressState = dict[
    str,
    GlossaryProgressPhase | int | str | bool | None,
]


class GlossaryTranslation:
    """
    术语翻译服务。

    对外只暴露两个异步生成器入口：
    1. `translate_roles()`：角色术语按角色并发翻译。
    2. `translate_display_names()`：地点名分块并发翻译。
    """

    def __init__(self, setting: Setting) -> None:
        """
        初始化术语翻译服务。

        Args:
            setting: 项目的全局运行时配置。
        """
        self.setting = setting
        self._progress_state: GlossaryProgressState = {
            "phase": "idle",
            "completed": 0,
            "total": 0,
            "last_error": None,
            "is_running": False,
        }

    @property
    def progress_state(self) -> GlossaryProgressState:
        """
        返回当前术语翻译阶段状态的只读副本。

        Returns:
            当前阶段状态字典。
        """
        return dict(self._progress_state)

    async def translate_roles(
        self,
        llm_handler: LLMHandler,
        role_lines: dict[str, list[str]],
        source_language: SourceLanguage,
    ) -> AsyncIterator[list[Role]]:
        """
        执行角色术语翻译。

        Args:
            llm_handler: LLM 服务调用器。
            role_lines: 角色原名到样例台词列表的映射。
            source_language: 当前游戏的源语言。

        Yields:
            最终角色术语列表。
        """
        if not role_lines:
            self._set_progress_state(
                phase="done",
                completed=0,
                total=0,
                last_error=None,
                is_running=False,
            )
            return

        role_tasks = self._build_role_tasks(role_lines)
        self._set_progress_state(
            phase="role_candidates",
            completed=0,
            total=len(role_tasks),
            last_error=None,
            is_running=True,
        )
        logger.info(
            f"[tag.phase]角色术语翻译[/tag.phase] "
            f"角色 [tag.count]{len(role_tasks)}[/tag.count] 条"
        )

        try:
            candidates = await self._translate_role_candidates(
                llm_handler=llm_handler,
                role_tasks=role_tasks,
                source_language=source_language,
            )
            translated_mapping: dict[str, tuple[str, Gender]] = {}
            for candidate in candidates:
                role_name = candidate["原名"]
                translated_mapping[role_name] = (
                    candidate["译名"],
                    self._require_gender(
                        candidate,
                        "性别",
                        f"角色术语结果 {role_name}",
                    ),
                )
            roles = [
                Role(
                    name=role_name,
                    translated_name=translated_mapping[role_name][0],
                    gender=translated_mapping[role_name][1],
                )
                for role_name in role_lines
            ]
            self._set_progress_state(
                phase="done",
                completed=len(roles),
                total=len(roles),
                last_error=None,
                is_running=False,
            )
            yield roles
        except Exception as error:
            self._progress_state["last_error"] = str(error)
            self._progress_state["is_running"] = False
            raise

    async def translate_display_names(
        self,
        llm_handler: LLMHandler,
        display_names: dict[str, str],
        roles: list[Role],
        source_language: SourceLanguage,
    ) -> AsyncIterator[list[Place]]:
        """
        执行地点术语翻译。

        Args:
            llm_handler: LLM 服务调用器。
            display_names: 待翻译地点名字典。
            roles: 已完成角色翻译的角色术语列表。
            source_language: 当前游戏的源语言。

        Yields:
            每个地点块对应的结构化地点列表。
        """
        if not display_names:
            self._set_progress_state(
                phase="done",
                completed=0,
                total=0,
                last_error=None,
                is_running=False,
            )
            return

        chunks = self._build_display_name_chunks(display_names, roles)
        self._set_progress_state(
            phase="display_names",
            completed=0,
            total=len(chunks),
            last_error=None,
            is_running=True,
        )
        logger.info(
            f"[tag.phase]地点术语翻译[/tag.phase] "
            f"分块 [tag.count]{len(chunks)}[/tag.count] 个"
        )

        worker_count = min(self.setting.glossary_translation.worker_count, len(chunks))
        semaphore = asyncio.Semaphore(worker_count)
        token_bucket: asyncio.Queue[int] | None = None
        stop_event: asyncio.Event | None = None
        token_task: asyncio.Task[None] | None = None

        if self.setting.glossary_translation.rpm is not None:
            token_bucket = asyncio.Queue(maxsize=1)
            stop_event = asyncio.Event()
            token_task = asyncio.create_task(
                self._create_token_bucket(
                    token_bucket=token_bucket,
                    rpm=self.setting.glossary_translation.rpm,
                    stop_event=stop_event,
                )
            )

        tasks = [
            asyncio.create_task(
                self._translate_display_name_chunk(
                    llm_handler=llm_handler,
                    chunk_index=chunk_index,
                    chunk=chunk,
                    hit_roles=hit_roles,
                    source_language=source_language,
                    semaphore=semaphore,
                    token_bucket=token_bucket,
                )
            )
            for chunk_index, chunk, hit_roles in chunks
        ]

        try:
            pending_results: dict[int, list[Place]] = {}
            next_chunk_index = 1
            completed = 0
            for task in asyncio.as_completed(tasks):
                chunk_index, places = await task
                pending_results[chunk_index] = places
                completed += 1
                self._progress_state["completed"] = completed

                while next_chunk_index in pending_results:
                    yield pending_results.pop(next_chunk_index)
                    next_chunk_index += 1

            self._set_progress_state(
                phase="done",
                completed=len(chunks),
                total=len(chunks),
                last_error=None,
                is_running=False,
            )
        except Exception as error:
            self._progress_state["last_error"] = str(error)
            self._progress_state["is_running"] = False
            for task in tasks:
                task.cancel()
            raise
        finally:
            if stop_event is not None:
                stop_event.set()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if token_task is not None:
                await token_task

    async def _translate_role_candidates(
        self,
        *,
        llm_handler: LLMHandler,
        role_tasks: list[tuple[int, str, list[str]]],
        source_language: SourceLanguage,
    ) -> list[dict[str, str]]:
        """
        并发执行角色翻译。

        Args:
            llm_handler: LLM 服务调用器。
            role_tasks: 角色任务列表。
            source_language: 当前游戏的源语言。

        Returns:
            按任务顺序排列的候选结果列表。
        """
        worker_count = min(
            self.setting.glossary_translation.worker_count,
            len(role_tasks),
        )
        semaphore = asyncio.Semaphore(worker_count)
        token_bucket: asyncio.Queue[int] | None = None
        stop_event: asyncio.Event | None = None
        token_task: asyncio.Task[None] | None = None

        if self.setting.glossary_translation.rpm is not None:
            token_bucket = asyncio.Queue(maxsize=1)
            stop_event = asyncio.Event()
            token_task = asyncio.create_task(
                self._create_token_bucket(
                    token_bucket=token_bucket,
                    rpm=self.setting.glossary_translation.rpm,
                    stop_event=stop_event,
                )
            )

        tasks = [
            asyncio.create_task(
                self._translate_single_role_candidate(
                    llm_handler=llm_handler,
                    task_index=task_index,
                    role_name=role_name,
                    dialogue_lines=dialogue_lines,
                    source_language=source_language,
                    semaphore=semaphore,
                    token_bucket=token_bucket,
                )
            )
            for task_index, role_name, dialogue_lines in role_tasks
        ]

        try:
            results: list[dict[str, str] | None] = [None] * len(role_tasks)
            completed = 0
            for task in asyncio.as_completed(tasks):
                task_index, result = await task
                results[task_index] = result
                completed += 1
                self._progress_state["completed"] = completed

            if any(result is None for result in results):
                raise RuntimeError("角色候选结果存在缺失")
            return [result for result in results if result is not None]
        except Exception:
            for task in tasks:
                task.cancel()
            raise
        finally:
            if stop_event is not None:
                stop_event.set()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if token_task is not None:
                await token_task

    async def _translate_single_role_candidate(
        self,
        *,
        llm_handler: LLMHandler,
        task_index: int,
        role_name: str,
        dialogue_lines: list[str],
        source_language: SourceLanguage,
        semaphore: asyncio.Semaphore,
        token_bucket: asyncio.Queue[int] | None,
    ) -> tuple[int, dict[str, str]]:
        """
        翻译单个人物候选结果。
        """
        async with semaphore:
            if token_bucket is not None:
                await token_bucket.get()

            task_setting = self.setting.glossary_translation.role_name
            llm_setting = self.setting.llm_services.glossary
            messages = [
                ChatMessage(role="system", text=task_setting.system_prompt),
                ChatMessage(
                    role="user",
                    text=self._build_role_candidate_user_message(
                        role_name=role_name,
                        dialogue_lines=dialogue_lines,
                        source_language=source_language,
                    ),
                ),
            ]
            attempt = 0
            while True:
                result = await llm_handler.get_ai_response(
                    service_name="glossary",
                    model=llm_setting.model,
                    messages=messages,
                    retry_count=task_setting.retry_count,
                    retry_delay=task_setting.retry_delay,
                )
                clean_result = self._repair_json_text(result)
                try:
                    return task_index, self._parse_single_role_response(
                        response_text=clean_result,
                        expected_name=role_name,
                    )
                except Exception as error:
                    attempt += 1
                    if attempt >= task_setting.response_retry_count:
                        self._log_retry_exhausted(
                            request_label=f"角色候选 {role_name}",
                            error=error,
                        )
                        raise ValueError(
                            f"角色候选 {role_name} 响应校验失败：{error}"
                        ) from error

                    self._log_retry_warning(
                        request_label=f"角色候选 {role_name}",
                        attempt=attempt,
                        max_attempts=task_setting.response_retry_count,
                        error=error,
                    )
                    messages.append(ChatMessage(role="assistant", text=clean_result))
                    messages.append(self._build_retry_message(error))

    async def _translate_display_name_chunk(
        self,
        *,
        llm_handler: LLMHandler,
        chunk_index: int,
        chunk: dict[str, str],
        hit_roles: list[Role],
        source_language: SourceLanguage,
        semaphore: asyncio.Semaphore,
        token_bucket: asyncio.Queue[int] | None,
    ) -> tuple[int, list[Place]]:
        """
        翻译单个地点块。
        """
        async with semaphore:
            if token_bucket is not None:
                await token_bucket.get()

            task_setting = self.setting.glossary_translation.display_name
            llm_setting = self.setting.llm_services.glossary
            messages = [
                ChatMessage(role="system", text=task_setting.system_prompt),
                ChatMessage(
                    role="user",
                    text=self._build_display_name_user_message(
                        chunk=chunk,
                        hit_roles=hit_roles,
                        source_language=source_language,
                    ),
                ),
            ]
            attempt = 0
            while True:
                result = await llm_handler.get_ai_response(
                    service_name="glossary",
                    model=llm_setting.model,
                    messages=messages,
                    retry_count=task_setting.retry_count,
                    retry_delay=task_setting.retry_delay,
                )
                clean_result = self._repair_json_text(result)
                try:
                    translated_mapping = self._parse_display_name_response(
                        response_text=clean_result,
                        expected_names=list(chunk.keys()),
                    )
                    places = [
                        Place(name=name, translated_name=translated_mapping[name])
                        for name in chunk
                    ]
                    return chunk_index, places
                except Exception as error:
                    attempt += 1
                    if attempt >= task_setting.response_retry_count:
                        self._log_retry_exhausted(
                            request_label=f"地点术语第 {chunk_index} 块",
                            error=error,
                        )
                        raise ValueError(
                            f"地点术语第 {chunk_index} 块响应校验失败：{error}"
                        ) from error

                    self._log_retry_warning(
                        request_label=f"地点术语第 {chunk_index} 块",
                        attempt=attempt,
                        max_attempts=task_setting.response_retry_count,
                        error=error,
                    )
                    messages.append(ChatMessage(role="assistant", text=clean_result))
                    messages.append(self._build_retry_message(error))

    def _build_role_tasks(
        self,
        role_lines: dict[str, list[str]],
    ) -> list[tuple[int, str, list[str]]]:
        """
        将角色样本整理为单人物任务列表。
        """
        tasks: list[tuple[int, str, list[str]]] = []
        for task_index, (role_name, lines) in enumerate(role_lines.items()):
            normalized_lines = [line.strip() for line in lines if line.strip()]
            if not normalized_lines:
                normalized_lines = ["（无样例台词）"]
            tasks.append((task_index, role_name, normalized_lines))
        return tasks

    def _build_display_name_chunks(
        self,
        display_names: dict[str, str],
        roles: list[Role],
    ) -> list[tuple[int, dict[str, str], list[Role]]]:
        """
        构造地点翻译块。
        """
        chunk_size = self.setting.glossary_translation.display_name.chunk_size
        chunks: list[tuple[int, dict[str, str], list[Role]]] = []
        for chunk_index, chunk in enumerate(
            batched(display_names.items(), chunk_size),
            start=1,
        ):
            chunk_dict = dict(chunk)
            hit_roles = [
                role
                for role in roles
                if any(role.name in display_name for display_name in chunk_dict)
            ]
            chunks.append((chunk_index, chunk_dict, hit_roles))
        return chunks

    def _build_role_candidate_user_message(
        self,
        *,
        role_name: str,
        dialogue_lines: list[str],
        source_language: SourceLanguage,
    ) -> str:
        """
        构造角色翻译的用户消息。
        """
        normalized_lines = [line.strip() for line in dialogue_lines if line.strip()]
        if not normalized_lines:
            normalized_lines = ["（无样例台词）"]

        return "\n".join(
            [
                f"源语言：{get_source_language_label(source_language)}",
                f"原名：{role_name}",
                "样例台词：",
                json.dumps(normalized_lines, ensure_ascii=False, indent=2),
            ]
        )

    def _build_display_name_user_message(
        self,
        *,
        chunk: dict[str, str],
        hit_roles: list[Role],
        source_language: SourceLanguage,
    ) -> str:
        """
        构造地点块翻译的用户消息。
        """
        lines = [f"源语言：{get_source_language_label(source_language)}"]
        if hit_roles:
            lines.extend(
                [
                    "命中的角色术语：",
                    json.dumps(
                        [
                            {
                                "原名": role.name,
                                "译名": role.translated_name,
                                "性别": role.gender,
                            }
                            for role in hit_roles
                        ],
                        ensure_ascii=False,
                        indent=2,
                    ),
                ]
            )
        lines.extend(
            [
                "需要翻译的地点术语：",
                json.dumps(chunk, ensure_ascii=False, indent=2),
            ]
        )
        return "\n".join(lines)

    def _parse_single_role_response(
        self,
        *,
        response_text: str,
        expected_name: str,
    ) -> dict[str, str]:
        """
        解析单人物候选响应。
        """
        data = self._load_json_object(response_text, "角色候选结果")
        self._require_exact_keys(data, {"原名", "译名", "性别"}, "角色候选结果")
        original_name = self._require_non_empty_string(data, "原名", "角色候选结果")
        translated_name = self._require_non_empty_string(data, "译名", "角色候选结果")
        gender = self._require_gender(data, "性别", "角色候选结果")
        if original_name != expected_name:
            raise ValueError(
                f"角色候选原名不匹配，期望 {expected_name}，实际 {original_name}"
            )
        translated_name = self._preserve_role_name_controls(
            original_name=expected_name,
            translated_name=translated_name,
        )
        return {"原名": original_name, "译名": translated_name, "性别": gender}

    def _parse_display_name_response(
        self,
        *,
        response_text: str,
        expected_names: list[str],
    ) -> dict[str, str]:
        """
        解析地点块响应。
        """
        data = self._load_json_object(response_text, "地点术语结果")
        expected_key_set = set(expected_names)
        received_key_set = set(data.keys())
        if received_key_set != expected_key_set:
            missing = sorted(expected_key_set - received_key_set)
            extra = sorted(received_key_set - expected_key_set)
            raise ValueError(f"地点术语 key 不匹配，缺失={missing}，新增={extra}")

        translated_mapping: dict[str, str] = {}
        for name in expected_names:
            item = data[name]
            if not isinstance(item, dict):
                raise ValueError(f"地点术语结果 {name} 必须是对象")
            self._require_exact_keys(item, {"译名"}, f"地点术语结果 {name}")
            translated_mapping[name] = self._require_non_empty_string(
                item,
                "译名",
                f"地点术语结果 {name}",
            )
        return translated_mapping

    @staticmethod
    def _load_json_object(response_text: str, label: str) -> dict[str, object]:
        """
        加载并校验顶层 JSON 对象。
        """
        try:
            data = json.loads(response_text)
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} 不是合法 JSON：{error}") from error
        if not isinstance(data, dict):
            raise ValueError(f"{label} 顶层必须是 JSON 对象")
        return data

    @staticmethod
    def _require_exact_keys(
        data: Mapping[str, object],
        expected_keys: set[str],
        label: str,
    ) -> None:
        """
        校验对象 key 集合完全匹配。
        """
        received_keys = set(data.keys())
        if received_keys != expected_keys:
            missing = sorted(expected_keys - received_keys)
            extra = sorted(received_keys - expected_keys)
            raise ValueError(f"{label} key 不匹配，缺失={missing}，新增={extra}")

    @staticmethod
    def _require_non_empty_string(
        data: Mapping[str, object],
        key: str,
        label: str,
    ) -> str:
        """
        提取并校验非空字符串字段。
        """
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label}.{key} 必须是非空字符串")
        return value.strip()

    @staticmethod
    def _preserve_role_name_controls(
        *,
        original_name: str,
        translated_name: str,
    ) -> str:
        """
        尽量保留角色原名中的控制符前后缀。

        角色发言名常见形态如 `\\c[21]Dad`、`\\V[625] Lorraine`、`Lord \\V[1343]`。
        术语表最终需要回写到 `101` 发言人字段中，因此如果模型只翻译了可见正文，
        这里会把原名中的控制符前缀或后缀自动拼回去。

        Args:
            original_name: 角色术语原名。
            translated_name: 模型返回的角色译名。

        Returns:
            尽量保留原控制符结构后的最终译名。
        """
        normalized_translation: str = translated_name.strip()
        if not normalized_translation:
            return normalized_translation

        visible_original_name: str = strip_rm_control_sequences(original_name).strip()
        if not visible_original_name:
            return normalized_translation
        if original_name == visible_original_name:
            return normalized_translation

        if "\\" in normalized_translation or "%" in normalized_translation:
            return normalized_translation

        visible_index: int = original_name.find(visible_original_name)
        if visible_index < 0:
            return normalized_translation

        prefix: str = original_name[:visible_index]
        suffix: str = original_name[visible_index + len(visible_original_name) :]
        return f"{prefix}{normalized_translation}{suffix}"

    @staticmethod
    def _require_gender(
        data: Mapping[str, object],
        key: str,
        label: str,
    ) -> Gender:
        """
        提取并校验性别字段。
        """
        value = data.get(key)
        if value not in ("男", "女", "未知"):
            raise ValueError(f"{label}.{key} 只能是 男 / 女 / 未知")
        return value

    @staticmethod
    def _repair_json_text(result: str) -> str:
        """
        修复模型输出为 JSON 文本。
        """
        repaired = repair_json(result, return_objects=False)
        repaired_value = repaired[0] if isinstance(repaired, tuple) else repaired
        if isinstance(repaired_value, str):
            return repaired_value
        return json.dumps(repaired_value, ensure_ascii=False)

    @staticmethod
    def _build_retry_message(error: Exception) -> ChatMessage:
        """
        构造结构校验失败后的重试消息。
        """
        return ChatMessage(
            role="user",
            text=(
                "上一次输出未通过本地校验，错误如下：\n"
                f"{error}\n"
                "请仅修正当前任务，并只输出严格合法的 JSON。"
            ),
        )

    async def _create_token_bucket(
        self,
        *,
        token_bucket: asyncio.Queue[int],
        rpm: int,
        stop_event: asyncio.Event,
    ) -> None:
        """
        按固定速率补充 RPM 令牌桶。
        """
        while not token_bucket.full():
            token_bucket.put_nowait(1)

        interval = 60.0 / rpm
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                if not token_bucket.full():
                    token_bucket.put_nowait(1)

    def _set_progress_state(
        self,
        *,
        phase: GlossaryProgressPhase,
        completed: int,
        total: int,
        last_error: str | None,
        is_running: bool,
    ) -> None:
        """
        覆盖写入当前术语翻译状态。
        """
        self._progress_state = {
            "phase": phase,
            "completed": completed,
            "total": total,
            "last_error": last_error,
            "is_running": is_running,
        }

    @staticmethod
    def _log_retry_warning(
        *,
        request_label: str,
        attempt: int,
        max_attempts: int,
        error: Exception,
    ) -> None:
        """
        输出响应重试日志。
        """
        logger.warning(
            f"[tag.warning]{request_label} 第 {attempt}/{max_attempts} 次响应重试[/tag.warning]：{error}"
        )

    @staticmethod
    def _log_retry_exhausted(
        *,
        request_label: str,
        error: Exception,
    ) -> None:
        """
        输出响应重试耗尽日志。
        """
        logger.error(
            f"[tag.exception]{request_label} 响应重试耗尽[/tag.exception]：{error}"
        )


__all__: list[str] = ["GlossaryTranslation"]
