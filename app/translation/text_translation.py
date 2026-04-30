"""
正文翻译异步调度模块。

该层负责并发调度、限流和把模型结果交给校验器。数据库写入和日志渲染由应用层处理。
"""

import asyncio
from collections.abc import AsyncIterator

from app.config import Setting
from app.rmmz.schema import TranslationErrorItem, TranslationItem
from app.llm.handler import LLMHandler
from app.llm.schemas import ChatMessage
from app.rmmz.text_rules import TextRules

from .retry import request_with_recoverable_retry
from .verify import verify_translation_batch


class TextTranslation:
    """正文翻译异步调度服务。"""

    def __init__(self, setting: Setting, text_rules: TextRules) -> None:
        """初始化正文翻译调度服务。"""
        self.setting: Setting = setting
        self.text_rules: TextRules = text_rules
        self.right_queue: asyncio.Queue[list[TranslationItem] | None] | None = None
        self.error_queue: asyncio.Queue[list[TranslationErrorItem] | None] | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self.runner_error: Exception | None = None

    def start_translation(
        self,
        *,
        llm_handler: LLMHandler,
        batches: list[tuple[list[TranslationItem], list[ChatMessage]]],
    ) -> None:
        """启动正文翻译后台并发执行流程。"""
        if self._runner_task is not None:
            raise RuntimeError("当前 TextTranslation 实例已经启动过翻译任务，请重新创建实例")

        self.right_queue = asyncio.Queue()
        self.error_queue = asyncio.Queue()
        self.runner_error = None
        self._runner_task = asyncio.create_task(
            self._run_translation(llm_handler=llm_handler, batches=batches)
        )

    async def iter_right_items(self) -> AsyncIterator[list[TranslationItem]]:
        """持续产出后台验证通过的翻译条目。"""
        if self.right_queue is None:
            raise RuntimeError("请先调用 start_translation() 启动正文翻译")

        while True:
            items = await self.right_queue.get()
            if items is None:
                break
            yield items

        if self.runner_error is not None:
            raise self.runner_error

    async def iter_error_items(self) -> AsyncIterator[list[TranslationErrorItem]]:
        """持续产出后台验证失败的翻译条目。"""
        if self.error_queue is None:
            raise RuntimeError("请先调用 start_translation() 启动正文翻译")

        while True:
            items = await self.error_queue.get()
            if items is None:
                break
            yield items

        if self.runner_error is not None:
            raise self.runner_error

    async def _run_translation(
        self,
        *,
        llm_handler: LLMHandler,
        batches: list[tuple[list[TranslationItem], list[ChatMessage]]],
    ) -> None:
        """管理并发翻译的核心调度器。"""
        if self.right_queue is None or self.error_queue is None:
            raise RuntimeError("正文翻译队列未初始化")
        right_queue = self.right_queue
        error_queue = self.error_queue

        text_task_setting = self.setting.text_translation
        text_llm_setting = self.setting.llm
        worker_count = min(text_task_setting.worker_count, max(len(batches), 1))
        rpm = text_task_setting.rpm
        stop_event = asyncio.Event()

        task_queue: asyncio.Queue[tuple[list[TranslationItem], list[ChatMessage]] | None] = asyncio.Queue()
        for batch in batches:
            await task_queue.put(batch)
        for _ in range(worker_count):
            await task_queue.put(None)

        token_bucket: asyncio.Queue[int] | None = None
        if rpm is not None:
            token_bucket = asyncio.Queue(maxsize=1)

        try:
            async with asyncio.TaskGroup() as task_group:
                if token_bucket is not None and rpm is not None:
                    _ = task_group.create_task(
                        self._create_token_bucket(
                            token_bucket=token_bucket,
                            rpm=rpm,
                            stop_event=stop_event,
                        )
                    )

                for _ in range(worker_count):
                    _ = task_group.create_task(
                        self._worker(
                            task_queue=task_queue,
                            right_queue=right_queue,
                            error_queue=error_queue,
                            llm_handler=llm_handler,
                            model=text_llm_setting.model,
                            retry_count=text_task_setting.retry_count,
                            retry_delay=text_task_setting.retry_delay,
                            token_bucket=token_bucket,
                        )
                    )

                _ = task_group.create_task(
                    self._wait_task_queue_done(task_queue=task_queue, stop_event=stop_event)
                )
        except Exception as error:
            self.runner_error = error
        finally:
            stop_event.set()
            await right_queue.put(None)
            await error_queue.put(None)

    async def _worker(
        self,
        *,
        task_queue: asyncio.Queue[tuple[list[TranslationItem], list[ChatMessage]] | None],
        right_queue: asyncio.Queue[list[TranslationItem] | None],
        error_queue: asyncio.Queue[list[TranslationErrorItem] | None],
        llm_handler: LLMHandler,
        model: str,
        retry_count: int,
        retry_delay: int,
        token_bucket: asyncio.Queue[int] | None,
    ) -> None:
        """持续消费正文翻译批次。"""
        while True:
            batch = await task_queue.get()
            try:
                if batch is None:
                    return

                items, messages = batch
                if token_bucket is not None:
                    _ = await token_bucket.get()

                ai_result = await request_with_recoverable_retry(
                    llm_handler=llm_handler,
                    model=model,
                    messages=messages,
                    retry_count=retry_count,
                    retry_delay=retry_delay,
                    task_label="正文翻译",
                )
                await verify_translation_batch(
                    ai_result=ai_result,
                    items=items,
                    right_queue=right_queue,
                    error_queue=error_queue,
                    text_rules=self.text_rules,
                )
            finally:
                task_queue.task_done()

    async def _wait_task_queue_done(
        self,
        *,
        task_queue: asyncio.Queue[tuple[list[TranslationItem], list[ChatMessage]] | None],
        stop_event: asyncio.Event,
    ) -> None:
        """等待任务队列消费完成，并通知限流协程退出。"""
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


__all__: list[str] = ["TextTranslation"]
