"""
Textual 工作台应用。

本模块提供两级键盘优先界面：
1. 一级界面只负责展示游戏列表和添加游戏。
2. 二级界面只负责展示当前游戏可执行的翻译功能。

任务进度与日志统一在二级界面下半区展示。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from queue import Empty, SimpleQueue
from typing import cast

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Footer,
    Input,
    Label,
    ListItem,
    ListView,
    ProgressBar,
    RichLog,
    Static,
)

from app.core.di import TranslationProvider
from app.core.handler import TranslationHandler
from app.utils import LogLine, logger, setup_logger


class AddGamePathScreen(ModalScreen[str | None]):
    """
    添加游戏路径输入弹窗。
    """

    CSS = """
    AddGamePathScreen {
        align: center middle;
        background: $background 70%;
    }

    #dialog {
        width: 72;
        height: auto;
        padding: 1 2;
        border: round $panel;
        background: $surface;
    }

    #dialog-title {
        margin-bottom: 1;
        text-style: bold;
    }

    #dialog-actions {
        height: auto;
        margin-top: 1;
        align-horizontal: right;
    }

    #dialog-actions Button {
        margin-left: 1;
    }
    """

    BINDINGS = [("escape", "cancel", "关闭")]

    def compose(self) -> ComposeResult:
        """
        组装弹窗控件。
        """
        with Vertical(id="dialog"):
            yield Static("添加 RPG Maker 游戏", id="dialog-title")
            yield Static("请输入游戏根目录路径。")
            yield Input(placeholder="例如：D:/games/your-project", id="game-path-input")
            yield Button("确认", id="confirm")
            yield Button("取消", id="cancel")

    def on_mount(self) -> None:
        """
        挂载后聚焦输入框。
        """
        self.query_one("#game-path-input", Input).focus()

    def action_cancel(self) -> None:
        """
        关闭弹窗并返回空结果。
        """
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """
        处理弹窗按钮点击。

        Args:
            event: 按钮点击事件。
        """
        button_id = event.button.id
        if button_id == "confirm":
            self._submit()
            return
        self.dismiss(None)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        """
        回车提交输入框内容。
        """
        self._submit()

    def _submit(self) -> None:
        """
        提交路径输入。
        """
        game_path = self.query_one("#game-path-input", Input).value.strip()
        if not game_path:
            return
        self.dismiss(game_path)


class GameListScreen(Screen[None]):
    """
    游戏列表一级界面。
    """

    BINDINGS = [
        ("up", "move_up", "上移"),
        ("down", "move_down", "下移"),
        ("enter", "select_current", "进入"),
        ("tab", "focus_next", "下一项"),
        ("shift+tab", "focus_previous", "上一项"),
        ("g", "add_game", "添加游戏"),
    ]

    def compose(self) -> ComposeResult:
        """
        组装游戏列表界面。
        """
        yield Static("选择游戏", id="title-bar")
        with Vertical(id="screen-layout"):
            with Vertical(id="list-panel"):
                yield Static("游戏列表", classes="section-title")
                yield ListView(id="game-list")
            with Vertical(id="bottom-panel"):
                yield Static("请选择一个游戏，按 Enter 进入。", id="list-status")
                yield Button("添加游戏", id="action-add-game")
        yield Footer(compact=True)

    @property
    def workbench(self) -> TranslationWorkbenchApp:
        """
        返回强类型的工作台应用实例。
        """
        return cast(TranslationWorkbenchApp, self.app)

    def on_mount(self) -> None:
        """
        挂载后刷新列表并聚焦。
        """
        self.workbench.game_list_screen = self
        self.refresh_list()
        self.refresh_status()
        self.query_one("#game-list", ListView).focus()

    def on_show(self) -> None:
        """
        重新显示时同步界面状态。
        """
        self.refresh_list()
        self.refresh_status()

    def action_move_up(self) -> None:
        """
        用方向键上移游戏选择。
        """
        game_list = self.query_one("#game-list", ListView)
        game_list.focus()
        game_list.action_cursor_up()

    def action_move_down(self) -> None:
        """
        用方向键下移游戏选择。
        """
        game_list = self.query_one("#game-list", ListView)
        game_list.focus()
        game_list.action_cursor_down()

    def action_select_current(self) -> None:
        """
        执行当前焦点动作。
        """
        focused = self.focused
        if isinstance(focused, Button):
            focused.press()
            return
        self._open_selected_game()

    def action_add_game(self) -> None:
        """
        打开添加游戏弹窗。
        """
        if self.workbench.task_running:
            return
        self.app.push_screen(AddGamePathScreen(), self._handle_add_game_result)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """
        处理界面按钮点击。

        Args:
            event: 按钮点击事件。
        """
        button_id = event.button.id
        if button_id == "action-add-game":
            self.action_add_game()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """
        根据当前高亮项更新选中游戏。

        Args:
            event: 列表高亮事件。
        """
        if event.list_view.id != "game-list":
            return
        item = event.item
        if item is None or item.name is None:
            self.workbench.selected_game_title = None
        else:
            self.workbench.selected_game_title = item.name
        self.refresh_status()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """
        按 Enter 选择当前高亮游戏。

        Args:
            event: 列表选中事件。
        """
        if event.list_view.id != "game-list":
            return
        self._open_selected_game()

    def refresh_list(self) -> None:
        """
        根据当前应用状态刷新游戏列表。
        """
        game_list = self.query_one("#game-list", ListView)
        game_list.clear()

        handler = self.workbench.handler
        if handler is None:
            game_list.disabled = True
            return

        database_items = sorted(
            handler.game_database_manager.items.values(),
            key=lambda item: item.game_title,
        )
        if not database_items:
            self.workbench.selected_game_title = None
            game_list.disabled = self.workbench.task_running
            return

        game_list.extend(
            ListItem(Label(item.game_title), name=item.game_title)
            for item in database_items
        )

        available_titles = [item.game_title for item in database_items]
        selected_game_title = self.workbench.selected_game_title
        if selected_game_title not in available_titles:
            selected_game_title = available_titles[0]
            self.workbench.selected_game_title = selected_game_title

        game_list.index = available_titles.index(selected_game_title)
        game_list.disabled = self.workbench.task_running

    def refresh_status(self) -> None:
        """
        刷新底部状态文本与按钮状态。
        """
        status = self.query_one("#list-status", Static)
        add_button = self.query_one("#action-add-game", Button)
        status.update(self.workbench.list_status_text)
        add_button.disabled = (
            self.workbench.task_running or self.workbench.handler is None
        )

    def _handle_add_game_result(self, game_path: str | None) -> None:
        """
        处理添加游戏弹窗返回值。

        Args:
            game_path: 用户输入的游戏目录路径。
        """
        if not game_path:
            return
        self.workbench.start_add_game(game_path)

    def _open_selected_game(self) -> None:
        """
        打开当前选中游戏的功能界面。
        """
        selected_game_title = self.workbench.selected_game_title
        if selected_game_title is None or self.workbench.task_running:
            return
        self.workbench.open_game_actions(selected_game_title)


class GameActionScreen(Screen[None]):
    """
    游戏功能二级界面。
    """

    BINDINGS = [
        ("up", "move_up", "上移"),
        ("down", "move_down", "下移"),
        ("enter", "select_current", "执行"),
        ("tab", "focus_next", "下一项"),
        ("shift+tab", "focus_previous", "上一项"),
        ("escape", "back", "返回"),
        ("l", "focus_logs", "日志"),
    ]

    def __init__(self, game_title: str) -> None:
        """
        初始化功能界面。

        Args:
            game_title: 当前选中的游戏标题。
        """
        super().__init__()
        self.game_title = game_title
        self.selected_action_id: str = "build_glossary"

    def compose(self) -> ComposeResult:
        """
        组装功能界面。
        """
        yield Static(f"游戏：{self.game_title}", id="title-bar")
        with Vertical(id="screen-layout"):
            with Vertical(id="list-panel"):
                yield Static("翻译功能", classes="section-title")
                yield ListView(
                    ListItem(Label("构建术语"), name="build_glossary"),
                    ListItem(Label("正文翻译"), name="translate_text"),
                    ListItem(Label("错误重翻"), name="retry_error_table"),
                    ListItem(Label("回写"), name="write_back"),
                    id="action-list",
                )
                yield Button("返回上一级", id="action-back")
            with Vertical(id="task-panel"):
                yield Static("当前任务：未开始", id="task-title")
                yield Static(f"游戏：{self.game_title}", id="task-phase")
                yield Static("状态：请选择功能", id="task-status")
                yield Static("细节：使用上下键选择功能，Enter 执行，Esc 返回", id="task-detail")
                yield ProgressBar(total=1, show_eta=False, id="task-progress")
            with Vertical(id="log-panel"):
                yield Static("实时日志", classes="section-title")
                yield RichLog(id="log-output")
        yield Footer(compact=True)

    @property
    def workbench(self) -> TranslationWorkbenchApp:
        """
        返回强类型的工作台应用实例。
        """
        return cast(TranslationWorkbenchApp, self.app)

    def on_mount(self) -> None:
        """
        挂载后刷新状态并聚焦功能列表。
        """
        self.workbench.action_screen = self
        self.refresh_state()
        self.render_logs()
        self.query_one("#action-list", ListView).focus()

    def on_unmount(self) -> None:
        """
        卸载时清理应用中的二级界面引用。
        """
        if self.workbench.action_screen is self:
            self.workbench.action_screen = None

    def action_move_up(self) -> None:
        """
        用方向键上移功能选择。
        """
        action_list = self.query_one("#action-list", ListView)
        action_list.focus()
        action_list.action_cursor_up()

    def action_move_down(self) -> None:
        """
        用方向键下移功能选择。
        """
        action_list = self.query_one("#action-list", ListView)
        action_list.focus()
        action_list.action_cursor_down()

    def action_select_current(self) -> None:
        """
        执行当前焦点动作。
        """
        focused = self.focused
        if isinstance(focused, Button):
            focused.press()
            return
        self._run_selected_action()

    def action_back(self) -> None:
        """
        返回一级界面。
        """
        if self.workbench.task_running:
            self.workbench.bell()
            return
        self.app.pop_screen()

    def action_focus_logs(self) -> None:
        """
        聚焦日志面板。
        """
        self.query_one("#log-output", RichLog).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """
        处理界面按钮点击。

        Args:
            event: 按钮点击事件。
        """
        button_id = event.button.id
        if button_id == "action-back":
            self.action_back()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """
        根据当前高亮项更新功能选择。

        Args:
            event: 列表高亮事件。
        """
        if event.list_view.id != "action-list":
            return
        item = event.item
        if item is None or item.name is None:
            return
        self.selected_action_id = item.name

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """
        按 Enter 执行当前高亮功能。

        Args:
            event: 列表选中事件。
        """
        if event.list_view.id != "action-list":
            return
        self._run_selected_action()

    def refresh_state(self) -> None:
        """
        根据应用状态刷新任务区与按钮状态。
        """
        back_button = self.query_one("#action-back", Button)
        action_list = self.query_one("#action-list", ListView)
        back_button.disabled = self.workbench.task_running
        action_list.disabled = self.workbench.task_running

        self.query_one("#task-title", Static).update(self.workbench.task_title_text)
        self.query_one("#task-phase", Static).update(self.workbench.task_phase_text)
        self.query_one("#task-status", Static).update(self.workbench.task_status_text)
        self.query_one("#task-detail", Static).update(self.workbench.task_detail_text)

        progress = self.query_one("#task-progress", ProgressBar)
        total = max(self.workbench.progress_total, 1)
        current = min(self.workbench.progress_current, total)
        progress.update(total=total, progress=current)

    def render_logs(self) -> None:
        """
        将应用当前缓存的日志写入面板。
        """
        log_output = self.query_one("#log-output", RichLog)
        for log_line in self.workbench.log_lines:
            self._write_log_line(log_output, log_line)

    def append_log_line(self, log_line: LogLine) -> None:
        """
        追加一条实时日志。

        Args:
            log_line: 结构化日志对象。
        """
        log_output = self.query_one("#log-output", RichLog)
        self._write_log_line(log_output, log_line)

    def _run_selected_action(self) -> None:
        """
        执行当前选中的翻译功能。
        """
        self.workbench.start_game_task(self.selected_action_id, self.game_title)

    @staticmethod
    def _write_log_line(log_output: RichLog, log_line: LogLine) -> None:
        """
        按日志级别写入一条日志。

        Args:
            log_output: 日志控件。
            log_line: 结构化日志对象。
        """
        style = {
            "DEBUG": "dim",
            "INFO": "cyan",
            "SUCCESS": "green",
            "WARNING": "yellow",
            "ERROR": "bold red",
            "CRITICAL": "bold white on red",
        }.get(log_line.level.upper(), "white")
        log_output.write(Text(log_line.plain_text, style=style))


class TranslationWorkbenchApp(App[None]):
    """
    翻译工作台应用。
    """

    CSS = """
    Screen {
        layout: vertical;
        background: $background;
    }

    #title-bar {
        height: auto;
        padding: 1 2;
        text-style: bold;
        color: $text;
        background: $surface;
        border-bottom: solid $panel;
    }

    #screen-layout {
        height: 1fr;
        padding: 1;
    }

    #list-panel,
    #task-panel,
    #log-panel,
    #bottom-panel {
        padding: 1;
        border: round $panel;
        background: $surface;
    }

    #list-panel {
        height: 1fr;
    }

    #game-list,
    #action-list {
        height: 1fr;
        margin-bottom: 1;
        border: round $panel-lighten-1;
    }

    #bottom-panel {
        height: auto;
        margin-top: 1;
    }

    #list-status {
        margin-bottom: 1;
        color: $text-muted;
    }

    #task-panel {
        height: auto;
        margin-top: 1;
    }

    #task-title {
        text-style: bold;
    }

    #task-phase,
    #task-status,
    #task-detail {
        margin-top: 1;
        color: $text-muted;
    }

    #task-progress {
        margin-top: 1;
    }

    #log-panel {
        height: 16;
        margin-top: 1;
    }

    #log-output {
        height: 1fr;
        border: round $panel-lighten-1;
    }
    """

    BINDINGS = [("q", "request_quit", "退出")]

    def __init__(
        self,
        handler_factory: Callable[[], Awaitable[TranslationHandler]] | None = None,
    ) -> None:
        """
        初始化工作台应用。

        Args:
            handler_factory: 可选的编排器工厂，便于注入测试替身。
        """
        super().__init__()
        self._handler_factory = handler_factory or self._create_default_handler
        self.handler: TranslationHandler | None = None
        self.game_list_screen: GameListScreen | None = None
        self.action_screen: GameActionScreen | None = None
        self.selected_game_title: str | None = None
        self.task_running: bool = False
        self.progress_current: int = 0
        self.progress_total: int = 0
        self.task_title_text: str = "当前任务：未开始"
        self.task_phase_text: str = "游戏：未选择"
        self.task_status_text: str = "状态：请选择功能"
        self.task_detail_text: str = "细节：使用上下键选择功能，Enter 执行，Esc 返回"
        self.list_status_text: str = "请选择一个游戏，按 Enter 进入。"
        self.log_lines: list[LogLine] = []
        self._ui_queue: SimpleQueue[tuple] = SimpleQueue()
        self._log_queue: SimpleQueue[LogLine] = SimpleQueue()
        self._shutdown_started: bool = False

    async def on_mount(self) -> None:
        """
        挂载后初始化日志与编排器。
        """
        self.set_interval(0.1, self._drain_queues)
        setup_logger(use_console=False, ui_log_callbacks=(self._enqueue_log_line,))

        try:
            self.handler = await self._handler_factory()
        except Exception:
            logger.exception("[tag.exception]工作台初始化失败[/tag.exception]")
            self.list_status_text = "初始化失败，请查看日志。"
        self.push_screen(GameListScreen())

    async def action_request_quit(self) -> None:
        """
        退出应用并清理资源。
        """
        await self._shutdown()
        self.exit()

    async def _create_default_handler(self) -> TranslationHandler:
        """
        创建默认翻译编排器。
        """
        return await TranslationHandler.create(TranslationProvider())

    async def _shutdown(self) -> None:
        """
        关闭工作台资源并恢复默认日志配置。
        """
        if self._shutdown_started:
            return
        self._shutdown_started = True
        try:
            if self.handler is not None:
                await self.handler.close()
        finally:
            self.handler = None
            setup_logger()

    def open_game_actions(self, game_title: str) -> None:
        """
        打开指定游戏的功能界面。

        Args:
            game_title: 目标游戏标题。
        """
        self.selected_game_title = game_title
        self._reset_task_view(game_title)
        self.push_screen(GameActionScreen(game_title))

    def start_add_game(self, game_path: str) -> None:
        """
        启动添加游戏任务。

        Args:
            game_path: 用户输入的游戏目录路径。
        """
        if self.task_running:
            return
        self.list_status_text = "开始注册游戏..."
        self.task_running = True
        self._refresh_visible_screen()
        self.run_worker(
            self._run_add_game_task(game_path),
            group="translation-workbench",
            exclusive=True,
            exit_on_error=False,
        )

    def start_game_task(self, action_id: str, game_title: str) -> None:
        """
        针对当前游戏启动指定任务。

        Args:
            action_id: 功能标识。
            game_title: 目标游戏标题。
        """
        if self.task_running:
            return

        coroutine: Awaitable[None] | None = None
        task_label = ""
        if action_id == "build_glossary":
            task_label = "构建术语"
            coroutine = self._run_build_glossary_task(game_title)
        elif action_id == "translate_text":
            task_label = "正文翻译"
            coroutine = self._run_translate_text_task(game_title)
        elif action_id == "retry_error_table":
            task_label = "错误重翻"
            coroutine = self._run_retry_error_table_task(game_title)
        elif action_id == "write_back":
            task_label = "回写"
            coroutine = self._run_write_back_task(game_title)

        if coroutine is None:
            return

        self._set_task_context(task_label, game_title)
        self.task_running = True
        self._refresh_visible_screen()
        self.run_worker(
            coroutine,
            group="translation-workbench",
            exclusive=True,
            exit_on_error=False,
        )

    async def _run_add_game_task(self, game_path: str) -> None:
        """
        执行添加游戏任务。

        Args:
            game_path: 用户输入的游戏目录路径。
        """
        if self.handler is None:
            self._ui_queue.put(("list_status", "编排器未初始化"))
            self._ui_queue.put(("task_done",))
            return

        try:
            game_title = await self.handler.add_game(game_path)
            self._ui_queue.put(("reload_games", game_title))
            self._ui_queue.put(("list_status", f"已添加游戏：{game_title}"))
        except Exception as error:
            logger.exception("[tag.exception]添加游戏任务失败[/tag.exception]")
            self._ui_queue.put(("list_status", f"添加游戏失败：{error}"))
        finally:
            self._ui_queue.put(("task_done",))

    async def _run_build_glossary_task(self, game_title: str) -> None:
        """
        执行术语构建任务。
        """
        if self.handler is None:
            self._ui_queue.put(("finished", "术语构建失败：编排器未初始化"))
            return

        try:
            self._queue_detail("开始构建术语")
            await self.handler.build_glossary(
                game_title=game_title,
                callbacks=(self._queue_set_progress, self._queue_advance_progress),
            )
            self._ui_queue.put(("finished", "术语构建完成"))
        except Exception as error:
            logger.exception("[tag.exception]术语构建任务失败[/tag.exception]")
            self._ui_queue.put(("finished", f"术语构建失败：{error}"))

    async def _run_translate_text_task(self, game_title: str) -> None:
        """
        执行正文翻译任务。
        """
        if self.handler is None:
            self._ui_queue.put(("finished", "正文翻译失败：编排器未初始化"))
            return

        try:
            self._queue_detail("开始正文翻译")
            await self.handler.translate_text(
                game_title=game_title,
                callbacks=(
                    self._queue_set_progress,
                    self._queue_advance_progress,
                    self._queue_detail,
                ),
            )
            self._ui_queue.put(("finished", "正文翻译完成"))
        except Exception as error:
            logger.exception("[tag.exception]正文翻译任务失败[/tag.exception]")
            self._ui_queue.put(("finished", f"正文翻译失败：{error}"))

    async def _run_retry_error_table_task(self, game_title: str) -> None:
        """
        执行错误重翻任务。
        """
        if self.handler is None:
            self._ui_queue.put(("finished", "错误重翻失败：编排器未初始化"))
            return

        try:
            self._queue_detail("开始错误重翻")
            await self.handler.retry_error_table(
                game_title=game_title,
                callbacks=(
                    self._queue_set_progress,
                    self._queue_advance_progress,
                    self._queue_detail,
                ),
            )
            self._ui_queue.put(("finished", "错误重翻完成"))
        except Exception as error:
            logger.exception("[tag.exception]错误重翻任务失败[/tag.exception]")
            self._ui_queue.put(("finished", f"错误重翻失败：{error}"))

    async def _run_write_back_task(self, game_title: str) -> None:
        """
        执行回写任务。
        """
        if self.handler is None:
            self._ui_queue.put(("finished", "回写失败：编排器未初始化"))
            return

        try:
            self._queue_detail("开始回写")
            await self.handler.write_back(
                game_title=game_title,
                callbacks=(self._queue_set_progress, self._queue_advance_progress),
            )
            self._ui_queue.put(("finished", "回写完成"))
        except Exception as error:
            logger.exception("[tag.exception]回写任务失败[/tag.exception]")
            self._ui_queue.put(("finished", f"回写失败：{error}"))

    def _enqueue_log_line(self, log_line: LogLine) -> None:
        """
        写入日志队列。

        Args:
            log_line: 结构化日志对象。
        """
        self._log_queue.put(log_line)

    def _queue_set_progress(self, current: int, total: int) -> None:
        """
        写入设置进度消息。
        """
        self._ui_queue.put(("set_progress", current, total))

    def _queue_advance_progress(self, delta: int) -> None:
        """
        写入推进进度消息。
        """
        self._ui_queue.put(("advance_progress", delta))

    def _queue_detail(self, text: str) -> None:
        """
        写入任务详情消息。
        """
        self._ui_queue.put(("detail", text))

    def _drain_queues(self) -> None:
        """
        批量消费界面消息和日志队列。
        """
        self._drain_ui_queue()
        self._drain_log_queue()

    def _drain_ui_queue(self) -> None:
        """
        批量处理界面消息队列。
        """
        while True:
            try:
                item = self._ui_queue.get_nowait()
            except Empty:
                break

            kind = item[0]

            if kind == "set_progress":
                self.progress_current = int(item[1])
                self.progress_total = int(item[2])
                self._update_progress_status()
                self._refresh_visible_screen()
                continue

            if kind == "advance_progress":
                self.progress_current += int(item[1])
                self._update_progress_status()
                self._refresh_visible_screen()
                continue

            if kind == "detail":
                self.task_detail_text = f"细节：{item[1]}"
                self._refresh_visible_screen()
                continue

            if kind == "finished":
                self.task_running = False
                self.task_status_text = f"状态：{item[1]}"
                self.task_detail_text = "细节：任务已结束"
                self._refresh_visible_screen()
                continue

            if kind == "list_status":
                self.list_status_text = str(item[1])
                self._refresh_visible_screen()
                continue

            if kind == "reload_games":
                self.selected_game_title = item[1]
                if self.game_list_screen is not None:
                    self.game_list_screen.refresh_list()
                    self.game_list_screen.refresh_status()
                continue

            if kind == "task_done":
                self.task_running = False
                self._refresh_visible_screen()

    def _drain_log_queue(self) -> None:
        """
        批量处理日志队列。
        """
        while True:
            try:
                log_line = self._log_queue.get_nowait()
            except Empty:
                break
            self.log_lines.append(log_line)
            if self.action_screen is not None and self.screen is self.action_screen:
                self.action_screen.append_log_line(log_line)

    def _reset_task_view(self, game_title: str) -> None:
        """
        重置二级界面的任务展示状态。

        Args:
            game_title: 当前选中的游戏标题。
        """
        self.progress_current = 0
        self.progress_total = 0
        self.task_title_text = "当前任务：未开始"
        self.task_phase_text = f"游戏：{game_title}"
        self.task_status_text = "状态：请选择功能"
        self.task_detail_text = "细节：使用上下键选择功能，Enter 执行，Esc 返回"
        self.log_lines = []

    def _set_task_context(self, task_label: str, game_title: str) -> None:
        """
        设置任务上下文文本并清空进度。

        Args:
            task_label: 当前任务标题。
            game_title: 当前任务对应的游戏标题。
        """
        self.progress_current = 0
        self.progress_total = 0
        self.task_title_text = f"当前任务：{task_label}"
        self.task_phase_text = f"游戏：{game_title}"
        self.task_status_text = "状态：执行中"
        self.task_detail_text = "细节：等待进度更新"
        self.log_lines = []

    def _update_progress_status(self) -> None:
        """
        根据当前进度刷新状态文本。
        """
        if self.progress_total <= 0:
            self.task_status_text = "状态：等待任务数据"
            return
        self.task_status_text = (
            f"状态：处理中 {self.progress_current}/{self.progress_total}"
        )

    def _refresh_visible_screen(self) -> None:
        """
        刷新当前可见界面。
        """
        if self.game_list_screen is not None and self.screen is self.game_list_screen:
            self.game_list_screen.refresh_status()
            return

        if self.action_screen is not None and self.screen is self.action_screen:
            self.action_screen.refresh_state()


__all__: list[str] = [
    "AddGamePathScreen",
    "GameActionScreen",
    "GameListScreen",
    "TranslationWorkbenchApp",
]
