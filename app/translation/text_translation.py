"""
正文翻译模块。

负责消费 `list[TranslationItem], list[ChatMessage]` 组成的正文翻译批次，
并通过后台任务、任务队列、正确队列、错误队列组织正文翻译流程。

设计约束：
1. 这一层只负责正文翻译调度，不负责上下文构建。
2. 这一层不接日志系统、不接进度回调、不做数据库写入。
3. 构造函数只接收 `Setting`，运行时只显式传入 `LLMHandler` 与翻译批次。
4. `worker_count / rpm / retry_count / retry_delay` 从 `Setting.text_translation` 中读取。
5. 正文翻译使用的模型从 `Setting.llm_services.text` 中读取。
5. 校验职责委托给同目录下的 `verify.py`，当前先保留空接口。
"""

import asyncio
from collections.abc import AsyncIterator

from app.config import Setting
from app.models.schemas import SourceLanguage, TranslationErrorItem, TranslationItem
from app.services.llm.handler import LLMHandler
from app.services.llm.schemas import ChatMessage

from .verify import verify_translation_batch


class TextTranslation:
    """
    正文翻译异步调度服务。

    主要职责是启动后台的多 Worker 消费者模型，并发处理大量的正文翻译请求。
    通过使用 Python 的 `asyncio.Queue` 实现生产者-消费者模式，同时支持 RPM 令牌桶限流控制。
    
    设计约定：
    为了状态管理的清晰，这个类被设计为“一次性会话（One-off Session）”对象。
    即一个 `TextTranslation` 实例在它的生命周期内只能调用一次 `start_translation`。
    如果需要执行新一轮正文翻译，需要抛弃旧实例，重新实例化一个新的 `TextTranslation`。
    """

    def __init__(self, setting: Setting) -> None:
        """
        初始化正文翻译调度服务。

        此时仅挂载配置，并不初始化任何异步队列，以确保实例能够在非异步环境中被创建。

        Args:
            setting: 包含 worker 数量、并发限制等配置的全局对象。
        """
        self.setting: Setting = setting
        self.right_queue: asyncio.Queue[list[TranslationItem] | None] | None = None
        self.error_queue: asyncio.Queue[list[TranslationErrorItem] | None] | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self.runner_error: Exception | None = None

    def start_translation(
        self,
        *,
        llm_handler: LLMHandler,
        batches: list[tuple[list[TranslationItem], list[ChatMessage]]],
        source_language: SourceLanguage,
    ) -> None:
        """
        启动正文翻译的后台并发执行流程。

        该方法非阻塞。它会立刻创建出供上层消费的 `right_queue` (成功结果) 与 `error_queue` (失败结果)，
        并向后台事件循环推入一个专门用于分发任务和管理 worker 生命周期的 Runner 协程。

        Args:
            llm_handler: 用于实际发起请求的 LLM 服务管理器。
            batches: 已经切割好并组装成目标提示词的批次列表。每一项包含原条目列表与上下文。
            source_language: 当前游戏的源语言。

        Raises:
            RuntimeError: 当尝试在同一个实例上二次调用本方法时抛出，强制执行一次性约定。
        """
        if self._runner_task is not None:
            raise RuntimeError(
                "当前 TextTranslation 实例已经启动过翻译任务，请重新创建实例"
            )

        self.right_queue = asyncio.Queue()
        self.error_queue = asyncio.Queue()
        self.runner_error = None
        self._runner_task = asyncio.create_task(
            self._run_translation(
                llm_handler=llm_handler,
                batches=batches,
                source_language=source_language,
            )
        )

    async def iter_right_items(self) -> AsyncIterator[list[TranslationItem]]:
        """
        作为消费者，持续等待并吐出后台验证通过的正确翻译条目列表。

        当后台所有 worker 消费完毕并优雅退出时，或者整个 task_group 发生崩溃时，
        队列会被推入 `None` 作为结束信号，此时生成器将正常结束。

        Yields:
            当前批次翻译成功且顺利恢复了占位符的 `TranslationItem` 列表。

        Raises:
            RuntimeError: 如果未调用 start_translation 就尝试迭代。
            Exception: 如果后台协程发生了未被捕获的崩溃异常，会在迭代结束时原样向外抛出。
        """
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
        """
        迭代输出错误队列中的正文翻译结果。

        Yields:
            当前批次对应的错误记录列表。

        Raises:
            RuntimeError: 尚未启动正文翻译任务时抛出。
            Exception: 后台正文翻译任务发生异常时原样抛出。
        """
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
        source_language: SourceLanguage,
    ) -> None:
        """
        管理并发翻译的核心调度器。

        使用 Python 3.11 引入的 `TaskGroup` 保证所有并发 Worker 以及令牌限流器的生命周期同生共死。
        它负责将所有批次推入 `task_queue`，并在末尾塞入与 Worker 数量等同的 `None` 毒药（Poison Pill），
        以便每个 Worker 处理完队列任务后能自然优雅地退出。

        最后无论成功与否，它都会通过 finally 块确保对外暴露的输出队列得到 `None` 结束信号，防止上层死锁。

        Args:
            llm_handler: 已实例化的服务请求句柄。
            batches: 全部待翻译的批次列表。
        """
        if self.right_queue is None or self.error_queue is None:
            raise RuntimeError("正文翻译队列未初始化")
        right_queue: asyncio.Queue[list[TranslationItem] | None] = self.right_queue
        error_queue: asyncio.Queue[list[TranslationErrorItem] | None] = self.error_queue

        text_task_setting = self.setting.text_translation
        text_llm_setting = self.setting.llm_services.text
        worker_count: int = text_task_setting.worker_count
        rpm: int | None = text_task_setting.rpm
        retry_count: int = text_task_setting.retry_count
        retry_delay: int = text_task_setting.retry_delay
        service_name: str = "text"
        model: str = text_llm_setting.model
        stop_event: asyncio.Event = asyncio.Event()

        task_queue: asyncio.Queue[
            tuple[list[TranslationItem], list[ChatMessage]] | None
        ] = asyncio.Queue()

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
                    task_group.create_task(
                        self._create_token_bucket(
                            token_bucket=token_bucket,
                            rpm=rpm,
                            stop_event=stop_event,
                        )
                    )

                for _ in range(worker_count):
                    task_group.create_task(
                        self._worker(
                            task_queue=task_queue,
                            right_queue=right_queue,
                            error_queue=error_queue,
                            llm_handler=llm_handler,
                            service_name=service_name,
                            model=model,
                            retry_count=retry_count,
                            retry_delay=retry_delay,
                            token_bucket=token_bucket,
                            source_language=source_language,
                        )
                    )

                task_group.create_task(
                    self._wait_task_queue_done(
                        task_queue=task_queue,
                        stop_event=stop_event,
                    )
                )
        except Exception as error:
            self.runner_error = error
        finally:
            stop_event.set()
            await self.right_queue.put(None)
            await self.error_queue.put(None)

    async def _worker(
        self,
        *,
        task_queue: asyncio.Queue[
            tuple[list[TranslationItem], list[ChatMessage]] | None
        ],
        right_queue: asyncio.Queue[list[TranslationItem] | None],
        error_queue: asyncio.Queue[list[TranslationErrorItem] | None],
        llm_handler: LLMHandler,
        service_name: str,
        model: str,
        retry_count: int,
        retry_delay: int,
        token_bucket: asyncio.Queue[int] | None,
        source_language: SourceLanguage,
    ) -> None:
        """
        持续运行的后台工作协程，从任务队列中获取批次并发送翻译请求。

        如果在配置中启用了 RPM（每分钟请求数）限制，Worker 在每次发出网络请求前，
        都必须先从 `token_bucket` 队列中获取一枚令牌，如果桶为空则自动阻塞挂起。
        收到模型返回的文本后，它会将解析和校验职责委托给 `verify_translation_batch` 函数，
        由后者负责推入正确的输出队列。

        如果从任务队列获取到了 `None` 毒药，意味着所有任务已经分配完毕，Worker 主动结束循环。

        Args:
            task_queue: 生产者推送任务的内部任务队列。
            right_queue: 输出给上层业务消费者正确条目的队列。
            error_queue: 输出给上层业务消费者错误条目的队列。
            llm_handler: 注入的 LLM 网络服务实现。
            service_name: 配置的 LLM 名称。
            model: 实际调用的具体大模型标识。
            retry_count: LLM 连接失败重试上限。
            retry_delay: LLM 重试初始延迟。
            token_bucket: 充当 RPM 限流信号量的令牌桶队列。
        """
        while True:
            batch = await task_queue.get()
            try:
                if batch is None:
                    return

                items, messages = batch
                if token_bucket is not None:
                    await token_bucket.get()

                ai_result: str = await llm_handler.get_ai_response(
                    service_name=service_name,
                    model=model,
                    messages=messages,
                    retry_count=retry_count,
                    retry_delay=retry_delay,
                )
                await verify_translation_batch(
                    ai_result=ai_result,
                    items=items,
                    right_queue=right_queue,
                    error_queue=error_queue,
                    source_language=source_language,
                )
            finally:
                task_queue.task_done()

    async def _wait_task_queue_done(
        self,
        *,
        task_queue: asyncio.Queue[
            tuple[list[TranslationItem], list[ChatMessage]] | None
        ],
        stop_event: asyncio.Event,
    ) -> None:
        """
        等待任务队列消费完成，并通知长期后台任务结束。

        Args:
            task_queue: 正文翻译任务队列。
            stop_event: 用于通知长期后台任务结束的事件。
        """
        await task_queue.join()
        stop_event.set()

    async def _create_token_bucket(
        self,
        *,
        token_bucket: asyncio.Queue[int],
        rpm: int,
        stop_event: asyncio.Event,
    ) -> None:
        """
        专用于限流的后台协程，以恒定速率向令牌桶中填充令牌。

        实现原理：基于 `rpm` (Requests Per Minute) 计算出生产单个令牌需要等待的秒数 `interval`。
        使用带有超时的 `asyncio.wait_for` 监听 `stop_event` 退出事件，只要超时，就往队列里塞一个令牌（如果桶满则忽略）。

        Args:
            token_bucket: 充当令牌桶、最大容量通常为 1 的队列。
            rpm: 每分钟允许发起的请求总数。
            stop_event: 来自主调度的退出事件。一旦置位，立即终止发放令牌并退出。
        """
        while not token_bucket.full():
            token_bucket.put_nowait(1)

        interval: float = 60.0 / rpm
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                if not token_bucket.full():
                    token_bucket.put_nowait(1)


__all__: list[str] = ["TextTranslation"]
