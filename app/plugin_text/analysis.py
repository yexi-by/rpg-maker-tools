"""
插件文本路径分析服务。

该服务根据当前 `plugins.js` 与数据库快照构造待分析计划，并用并发 worker 调用大模型
生成插件文本路径规则。语言兼容分支已删除，语义校验统一使用日文核心文本规则。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import Setting
from app.rmmz.schema import PluginTextRuleRecord
from app.llm import ChatMessage, LLMHandler
from app.rmmz.text_rules import JsonValue, TextRules

from .common import (
    ResolvedLeaf,
    build_allowed_templates,
    build_plugin_hash,
    build_plugins_file_hash,
    build_prompt_hash,
    build_prompt_payload,
    build_request_messages,
    extract_plugin_description,
    extract_plugin_name,
    parse_analysis_response,
    resolve_plugin_leaves,
    validate_analysis_semantics,
)


@dataclass(slots=True)
class PluginAnalysisJob:
    """单个插件分析任务描述。"""

    plugin_index: int
    plugin: dict[str, JsonValue]
    plugin_name: str
    plugin_description: str
    plugin_hash: str
    resolved_leaves: list[ResolvedLeaf]
    allowed_templates: set[str]


@dataclass(slots=True)
class PluginAnalysisPlan:
    """当前游戏插件分析计划。"""

    jobs: list[PluginAnalysisJob]
    total_plugins: int
    reused_success_count: int
    plugins_file_hash: str
    prompt_hash: str


@dataclass(slots=True)
class PluginAnalysisExecution:
    """单个插件分析执行结果。"""

    rule_record: PluginTextRuleRecord
    attempt_count: int


class PluginTextAnalysis:
    """插件文本路径分析服务。"""

    def __init__(self, setting: Setting, text_rules: TextRules) -> None:
        """初始化插件分析服务。"""
        self.setting: Setting = setting
        self.text_rules: TextRules = text_rules
        self.result_queue: asyncio.Queue[PluginAnalysisExecution | None] | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self.runner_error: Exception | None = None

    def build_plan(
        self,
        *,
        plugins: list[dict[str, JsonValue]],
        existing_rules: dict[int, PluginTextRuleRecord],
    ) -> PluginAnalysisPlan:
        """根据当前插件列表和数据库快照构造分析计划。"""
        prompt_hash = build_prompt_hash(self.setting.plugin_text_analysis.system_prompt)
        plugins_file_hash = build_plugins_file_hash(plugins)
        jobs: list[PluginAnalysisJob] = []
        reused_success_count = 0

        for plugin_index, plugin in enumerate(plugins):
            plugin_name = extract_plugin_name(plugin, plugin_index)
            plugin_hash = build_plugin_hash(plugin)
            existing_rule = existing_rules.get(plugin_index)
            if (
                existing_rule is not None
                and existing_rule.status == "success"
                and existing_rule.plugin_hash == plugin_hash
                and existing_rule.prompt_hash == prompt_hash
            ):
                reused_success_count += 1
                continue

            resolved_leaves = resolve_plugin_leaves(plugin)
            jobs.append(
                PluginAnalysisJob(
                    plugin_index=plugin_index,
                    plugin=plugin,
                    plugin_name=plugin_name,
                    plugin_description=extract_plugin_description(plugin),
                    plugin_hash=plugin_hash,
                    resolved_leaves=resolved_leaves,
                    allowed_templates=build_allowed_templates(resolved_leaves),
                )
            )

        return PluginAnalysisPlan(
            jobs=jobs,
            total_plugins=len(plugins),
            reused_success_count=reused_success_count,
            plugins_file_hash=plugins_file_hash,
            prompt_hash=prompt_hash,
        )

    def start_analysis(self, *, llm_handler: LLMHandler, plan: PluginAnalysisPlan) -> None:
        """启动后台插件分析任务。"""
        if self._runner_task is not None:
            raise RuntimeError("当前 PluginTextAnalysis 实例已经启动过分析任务")

        self.result_queue = asyncio.Queue()
        self.runner_error = None
        self._runner_task = asyncio.create_task(
            self._run_analysis(llm_handler=llm_handler, plan=plan)
        )

    async def iter_results(self) -> AsyncIterator[PluginAnalysisExecution]:
        """逐条消费后台插件分析结果。"""
        if self.result_queue is None:
            raise RuntimeError("请先调用 start_analysis() 启动插件分析")

        while True:
            result = await self.result_queue.get()
            if result is None:
                break
            yield result

        if self.runner_error is not None:
            raise self.runner_error

    async def _run_analysis(self, *, llm_handler: LLMHandler, plan: PluginAnalysisPlan) -> None:
        """运行后台插件分析主循环。"""
        if self.result_queue is None:
            raise RuntimeError("插件分析结果队列尚未初始化")

        plugin_setting = self.setting.plugin_text_analysis
        worker_count = min(plugin_setting.worker_count, len(plan.jobs))
        if worker_count <= 0:
            await self.result_queue.put(None)
            return

        task_queue: asyncio.Queue[PluginAnalysisJob | None] = asyncio.Queue()
        for job in plan.jobs:
            await task_queue.put(job)
        for _ in range(worker_count):
            await task_queue.put(None)

        stop_event = asyncio.Event()
        token_bucket: asyncio.Queue[int] | None = None
        if plugin_setting.rpm is not None:
            token_bucket = asyncio.Queue(maxsize=1)

        try:
            async with asyncio.TaskGroup() as task_group:
                if token_bucket is not None and plugin_setting.rpm is not None:
                    _ = task_group.create_task(
                        self._create_token_bucket(
                            token_bucket=token_bucket,
                            rpm=plugin_setting.rpm,
                            stop_event=stop_event,
                        )
                    )

                for _ in range(worker_count):
                    _ = task_group.create_task(
                        self._worker(
                            llm_handler=llm_handler,
                            task_queue=task_queue,
                            token_bucket=token_bucket,
                            plan=plan,
                        )
                    )

                _ = task_group.create_task(
                    self._wait_task_queue_done(task_queue=task_queue, stop_event=stop_event)
                )
        except Exception as error:
            self.runner_error = error
        finally:
            stop_event.set()
            await self.result_queue.put(None)

    async def _worker(
        self,
        *,
        llm_handler: LLMHandler,
        task_queue: asyncio.Queue[PluginAnalysisJob | None],
        token_bucket: asyncio.Queue[int] | None,
        plan: PluginAnalysisPlan,
    ) -> None:
        """消费单个插件分析任务并把结果写入结果队列。"""
        if self.result_queue is None:
            raise RuntimeError("插件分析结果队列尚未初始化")

        while True:
            job = await task_queue.get()
            try:
                if job is None:
                    return
                if token_bucket is not None:
                    _ = await token_bucket.get()

                result = await self._analyze_single_plugin(
                    llm_handler=llm_handler,
                    job=job,
                    prompt_hash=plan.prompt_hash,
                )
                await self.result_queue.put(result)
            finally:
                task_queue.task_done()

    async def _analyze_single_plugin(
        self,
        *,
        llm_handler: LLMHandler,
        job: PluginAnalysisJob,
        prompt_hash: str,
    ) -> PluginAnalysisExecution:
        """分析单个插件并返回最终规则快照。"""
        plugin_setting = self.setting.plugin_text_analysis
        llm_setting = self.setting.llm_services.plugin_text
        payload = build_prompt_payload(
            plugin_index=job.plugin_index,
            plugin=job.plugin,
            plugin_name=job.plugin_name,
            plugin_description=job.plugin_description,
            resolved_leaves=job.resolved_leaves,
        )

        previous_response: str | None = None
        validation_errors: list[str] = []
        attempt_count = 0
        last_error = ""

        for attempt_index in range(1, plugin_setting.response_retry_count + 1):
            attempt_count = attempt_index
            messages = [
                ChatMessage(role=message["role"], text=message["content"])
                for message in build_request_messages(
                    system_prompt=plugin_setting.system_prompt,
                    payload=payload,
                    previous_response=previous_response,
                    validation_errors=validation_errors or None,
                )
            ]
            response_text = await llm_handler.get_ai_response(
                messages=messages,
                model=llm_setting.model,
                service_name="plugin_text",
                retry_count=plugin_setting.retry_count,
                retry_delay=plugin_setting.retry_delay,
                temperature=0.1,
            )
            try:
                response = parse_analysis_response(
                    response_text=response_text,
                    expected_plugin_name=job.plugin_name,
                    expected_plugin_index=job.plugin_index,
                    allowed_templates=job.allowed_templates,
                )
                validate_analysis_semantics(
                    response=response,
                    resolved_leaves=job.resolved_leaves,
                    plugin_name=job.plugin_name,
                    text_rules=self.text_rules,
                )
                return PluginAnalysisExecution(
                    rule_record=PluginTextRuleRecord(
                        plugin_index=job.plugin_index,
                        plugin_name=job.plugin_name,
                        plugin_hash=job.plugin_hash,
                        prompt_hash=prompt_hash,
                        status="success",
                        plugin_reason=response.plugin_reason,
                        translate_rules=response.translate_rules,
                        last_error=None,
                        updated_at=datetime.now(timezone.utc).isoformat(),
                    ),
                    attempt_count=attempt_count,
                )
            except Exception as error:
                previous_response = response_text
                validation_errors = _split_validation_errors(str(error))
                last_error = str(error)

        return PluginAnalysisExecution(
            rule_record=PluginTextRuleRecord(
                plugin_index=job.plugin_index,
                plugin_name=job.plugin_name,
                plugin_hash=job.plugin_hash,
                prompt_hash=prompt_hash,
                status="failed",
                plugin_reason="",
                translate_rules=[],
                last_error=last_error,
                updated_at=datetime.now(timezone.utc).isoformat(),
            ),
            attempt_count=attempt_count,
        )

    async def _wait_task_queue_done(
        self,
        *,
        task_queue: asyncio.Queue[PluginAnalysisJob | None],
        stop_event: asyncio.Event,
    ) -> None:
        """等待任务队列消费完成并通知限速协程退出。"""
        await task_queue.join()
        stop_event.set()

    async def _create_token_bucket(
        self,
        *,
        token_bucket: asyncio.Queue[int],
        rpm: int,
        stop_event: asyncio.Event,
    ) -> None:
        """按 RPM 节奏持续补充请求令牌。"""
        while not token_bucket.full():
            token_bucket.put_nowait(1)

        interval = 60.0 / rpm
        while not stop_event.is_set():
            try:
                _ = await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                if not token_bucket.full():
                    token_bucket.put_nowait(1)


def _split_validation_errors(error_text: str) -> list[str]:
    """把校验错误摘要拆成更适合反馈给模型的条目。"""
    errors = [part.strip() for part in error_text.split(" | ")]
    return [error for error in errors if error]


__all__: list[str] = [
    "PluginAnalysisExecution",
    "PluginAnalysisPlan",
    "PluginTextAnalysis",
]
