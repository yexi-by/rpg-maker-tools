"""
Textual 工作台应用。

本模块提供一个面向键盘操作的多视图工作台：
1. 首页用于选择游戏、添加游戏和进入设置页。
2. 设置页用于直接编辑项目根目录下的 `setting.toml`。
3. 二级翻译页用于对当前游戏执行翻译任务并查看进度。

日志历史由应用统一维护，切换页面后不会被清空。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from queue import Empty, SimpleQueue
from typing import Any, Literal, cast, get_args, get_origin

from pydantic import BaseModel
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    ContentSwitcher,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    ProgressBar,
    RichLog,
    Select,
    Static,
)

from app.config.schemas import Setting
from app.core.di import TranslationProvider
from app.core.handler import TranslationHandler
from app.models.schemas import SourceLanguage
from app.utils import LogLine, get_source_language_label, logger, setup_logger
from app.utils.config_loader_utils import load_setting_document, save_setting_value
from tomlkit.toml_document import TOMLDocument


SettingFieldKind = Literal["string", "int", "float", "enum"]


@dataclass(slots=True)
class SettingFieldSpec:
    """
    单个设置字段的界面描述。
    """

    path: tuple[str, ...]
    kind: SettingFieldKind
    secret: bool = False
    options: tuple[tuple[str, str], ...] = ()

    @property
    def widget_id(self) -> str:
        """
        返回字段控件的唯一标识。
        """
        return f"setting-field-{'__'.join(self.path)}"


@dataclass(slots=True)
class SettingSectionSpec:
    """
    设置卡片中的单个逻辑分组。
    """

    title: str
    fields: tuple[SettingFieldSpec, ...]


@dataclass(slots=True)
class SettingCardSpec:
    """
    设置页中的单张配置卡片定义。
    """

    title: str
    sections: tuple[SettingSectionSpec, ...]


PROVIDER_OPTIONS: tuple[tuple[str, str], ...] = (
    ("OpenAI 兼容接口", "openai"),
    ("Gemini 接口", "gemini"),
    ("火山引擎接口", "volcengine"),
)
SOURCE_LANGUAGE_OPTIONS: tuple[tuple[str, SourceLanguage], ...] = (
    ("日文 (ja)", "ja"),
    ("英文 (en)", "en"),
)

SETTING_CARDS: tuple[SettingCardSpec, ...] = (
    SettingCardSpec(
        title="模型服务",
        sections=(
            SettingSectionSpec(
                title="术语服务",
                fields=(
                    SettingFieldSpec(
                        path=("llm_services", "glossary", "provider_type"),
                        kind="enum",
                        options=PROVIDER_OPTIONS,
                    ),
                    SettingFieldSpec(
                        path=("llm_services", "glossary", "base_url"),
                        kind="string",
                    ),
                    SettingFieldSpec(
                        path=("llm_services", "glossary", "api_key"),
                        kind="string",
                        secret=True,
                    ),
                    SettingFieldSpec(
                        path=("llm_services", "glossary", "model"),
                        kind="string",
                    ),
                    SettingFieldSpec(
                        path=("llm_services", "glossary", "timeout"),
                        kind="int",
                    ),
                ),
            ),
            SettingSectionSpec(
                title="正文服务",
                fields=(
                    SettingFieldSpec(
                        path=("llm_services", "text", "provider_type"),
                        kind="enum",
                        options=PROVIDER_OPTIONS,
                    ),
                    SettingFieldSpec(
                        path=("llm_services", "text", "base_url"),
                        kind="string",
                    ),
                    SettingFieldSpec(
                        path=("llm_services", "text", "api_key"),
                        kind="string",
                        secret=True,
                    ),
                    SettingFieldSpec(
                        path=("llm_services", "text", "model"),
                        kind="string",
                    ),
                    SettingFieldSpec(
                        path=("llm_services", "text", "timeout"),
                        kind="int",
                    ),
                ),
            ),
        ),
    ),
    SettingCardSpec(
        title="术语流程",
        sections=(
            SettingSectionSpec(
                title="术语提取",
                fields=(
                    SettingFieldSpec(
                        path=("glossary_extraction", "role_chunk_blocks"),
                        kind="int",
                    ),
                    SettingFieldSpec(
                        path=("glossary_extraction", "role_chunk_lines"),
                        kind="int",
                    ),
                ),
            ),
            SettingSectionSpec(
                title="术语并发",
                fields=(
                    SettingFieldSpec(
                        path=("glossary_translation", "worker_count"),
                        kind="int",
                    ),
                    SettingFieldSpec(
                        path=("glossary_translation", "rpm"),
                        kind="int",
                    ),
                ),
            ),
            SettingSectionSpec(
                title="角色名翻译",
                fields=(
                    SettingFieldSpec(
                        path=("glossary_translation", "role_name", "retry_count"),
                        kind="int",
                    ),
                    SettingFieldSpec(
                        path=("glossary_translation", "role_name", "retry_delay"),
                        kind="int",
                    ),
                    SettingFieldSpec(
                        path=(
                            "glossary_translation",
                            "role_name",
                            "response_retry_count",
                        ),
                        kind="int",
                    ),
                    SettingFieldSpec(
                        path=(
                            "glossary_translation",
                            "role_name",
                            "system_prompt_file",
                        ),
                        kind="string",
                    ),
                ),
            ),
            SettingSectionSpec(
                title="显示名翻译",
                fields=(
                    SettingFieldSpec(
                        path=("glossary_translation", "display_name", "chunk_size"),
                        kind="int",
                    ),
                    SettingFieldSpec(
                        path=("glossary_translation", "display_name", "retry_count"),
                        kind="int",
                    ),
                    SettingFieldSpec(
                        path=("glossary_translation", "display_name", "retry_delay"),
                        kind="int",
                    ),
                    SettingFieldSpec(
                        path=(
                            "glossary_translation",
                            "display_name",
                            "response_retry_count",
                        ),
                        kind="int",
                    ),
                    SettingFieldSpec(
                        path=(
                            "glossary_translation",
                            "display_name",
                            "system_prompt_file",
                        ),
                        kind="string",
                    ),
                ),
            ),
        ),
    ),
    SettingCardSpec(
        title="正文流程",
        sections=(
            SettingSectionSpec(
                title="上下文切批",
                fields=(
                    SettingFieldSpec(
                        path=("translation_context", "token_size"),
                        kind="int",
                    ),
                    SettingFieldSpec(
                        path=("translation_context", "factor"),
                        kind="float",
                    ),
                    SettingFieldSpec(
                        path=("translation_context", "max_command_items"),
                        kind="int",
                    ),
                ),
            ),
            SettingSectionSpec(
                title="正文翻译",
                fields=(
                    SettingFieldSpec(
                        path=("text_translation", "worker_count"),
                        kind="int",
                    ),
                    SettingFieldSpec(
                        path=("text_translation", "rpm"),
                        kind="int",
                    ),
                    SettingFieldSpec(
                        path=("text_translation", "retry_count"),
                        kind="int",
                    ),
                    SettingFieldSpec(
                        path=("text_translation", "retry_delay"),
                        kind="int",
                    ),
                    SettingFieldSpec(
                        path=("text_translation", "system_prompt_file"),
                        kind="string",
                    ),
                ),
            ),
            SettingSectionSpec(
                title="错误重翻",
                fields=(
                    SettingFieldSpec(
                        path=("error_translation", "chunk_size"),
                        kind="int",
                    ),
                    SettingFieldSpec(
                        path=("error_translation", "system_prompt_file"),
                        kind="string",
                    ),
                ),
            ),
        ),
    ),
)




def _iter_setting_fields() -> tuple[SettingFieldSpec, ...]:
    """
    展开全部设置字段定义。

    Returns:
        按页面展示顺序展开后的字段规格列表。
    """
    return tuple(
        field
        for card in SETTING_CARDS
        for section in card.sections
        for field in section.fields
    )


ALL_SETTING_FIELDS: tuple[SettingFieldSpec, ...] = _iter_setting_fields()


class AddGamePathScreen(ModalScreen[tuple[str, SourceLanguage] | None]):
    """
    添加游戏路径输入弹窗。
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
            yield Static("请选择源语言。")
            yield Select(
                options=SOURCE_LANGUAGE_OPTIONS,
                allow_blank=False,
                value="ja",
                id="dialog-source-language-select",
            )
            with Vertical(id="dialog-actions"):
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
        source_language = self.query_one(
            "#dialog-source-language-select",
            Select,
        ).value
        self.dismiss((game_path, cast(SourceLanguage, str(source_language))))


class GlossaryRebuildConfirmScreen(ModalScreen[tuple[str, bool] | None]):
    """
    术语表重建确认弹窗。

    当当前游戏已经存在完整术语表时，构建操作不会立即开始，而是先由这个弹窗
    询问用户是否重建。这里明确告知覆盖语义，避免用户误以为一旦点击重建就会
    立刻删除旧表。
    """

    BINDINGS = [("escape", "cancel", "关闭")]

    def __init__(self, game_title: str) -> None:
        """
        初始化重建确认弹窗。

        Args:
            game_title: 当前准备重建术语表的游戏标题。
        """
        super().__init__()
        self.game_title = game_title

    def compose(self) -> ComposeResult:
        """
        组装重建确认弹窗控件。
        """
        with Vertical(id="dialog"):
            yield Static("检测到已存在完整术语表", id="dialog-title")
            yield Static(f"游戏：{self.game_title}")
            yield Static("是否重建术语表？")
            yield Static(
                "只有新术语表构建成功后才会覆盖旧术语表；如果中途失败，将继续沿用旧术语表。"
            )
            with Vertical(id="dialog-actions"):
                yield Button("重建", id="confirm-glossary-rebuild")
                yield Button("取消", id="cancel-glossary-rebuild")

    def action_cancel(self) -> None:
        """
        关闭弹窗并返回空结果。
        """
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """
        处理确认弹窗按钮点击。

        Args:
            event: 按钮点击事件。
        """
        if event.button.id == "confirm-glossary-rebuild":
            self.dismiss((self.game_title, True))
            return
        self.dismiss(None)


class HomeDashboard(Vertical):
    """
    首页容器面板，自带特定的快捷键绑定。
    """

    BINDINGS = [
        Binding("g", "app.add_game", "添加游戏"),
        Binding("s", "app.open_settings", "设置"),
    ]


class TranslationWorkbenchApp(App[None]):
    """
    翻译工作台应用。
    """

    CSS_PATH = "styles.tcss"

    BINDINGS = [
        Binding("up", "move_up", "上移"),
        Binding("down", "move_down", "下移"),
        Binding("enter", "activate_current", "进入"),
        Binding("tab", "focus_next", "下一项"),
        Binding("shift+tab", "focus_previous", "上一项"),
        Binding("escape", "go_back", "返回"),
        Binding("l", "focus_logs", "日志"),
        Binding("q", "request_quit", "退出"),
    ]

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
        self.current_view: Literal["home", "settings", "actions"] = "home"
        self.selected_game_title: str | None = None
        self.selected_game_source_language: SourceLanguage | None = None
        self.selected_action_id: str = "build_glossary"
        self.task_running: bool = False
        self.source_language_updating: bool = False
        self._glossary_rebuild_checking: bool = False
        self.progress_current: int = 0
        self.progress_total: int = 0
        self.home_status_text: str = "请选择一个游戏，按 Enter 进入。"
        self.task_title_text: str = "当前任务：未开始"
        self.task_phase_text: str = "游戏：未选择 | 源语言：未选择"
        self.task_status_text: str = "状态：请选择功能"
        self.task_detail_text: str = "细节：使用上下键选择功能，Enter 执行，Esc 返回"
        self.settings_dirty_fields: set[str] = set()
        self.settings_error_text: str = ""
        self.settings_success_text: str = ""
        self.log_history: list[LogLine] = []
        self._log_queue: SimpleQueue[LogLine] = SimpleQueue()
        self._ui_queue: SimpleQueue[tuple[Any, ...]] = SimpleQueue()
        self._shutdown_started: bool = False
        self._settings_event_suspended: bool = False
        self._actions_source_language_ready: bool = False
        self._setting_document: TOMLDocument | None = None
        self._setting_specs_by_widget_id: dict[str, SettingFieldSpec] = {
            spec.widget_id: spec for spec in ALL_SETTING_FIELDS
        }
        self._secret_widget_ids: set[str] = {
            spec.widget_id for spec in ALL_SETTING_FIELDS if spec.secret
        }

    def compose(self) -> ComposeResult:
        """
        组装主界面。
        """
        yield Header(show_clock=True)

        with Horizontal():
            with Vertical(id="left-pane"):
                with ContentSwitcher(initial="home-view", id="view-switcher"):
                    with HomeDashboard(id="home-view"):
                        with Horizontal(id="home-command-bar"):
                            yield Button("添加游戏", id="action-add-game", classes="command-button")
                            yield Button("设置", id="action-open-settings", classes="command-button")

                        with Horizontal(id="home-main-row"):
                            with Vertical(id="home-list-panel"):
                                yield Static("游戏列表", classes="section-title")
                                yield ListView(id="game-list")

                    with Vertical(id="settings-view"):
                        with Horizontal(id="settings-header-bar"):
                            yield Static("", id="settings-note")
                            yield Button("返回首页", id="settings-back-button", variant="primary")
                        with VerticalScroll(id="settings-scroll"):
                            for card_index, card_spec in enumerate(SETTING_CARDS):
                                yield self._build_setting_card(card_spec, card_index)

                    with Vertical(id="actions-view"):
                        with Horizontal(id="actions-main-row"):
                            with Vertical(id="actions-sidebar"):
                                yield Static("翻译功能", classes="section-title")
                                with Vertical(id="action-button-group"):
                                    yield Button("构建术语", id="btn-build_glossary", classes="action-btn")
                                    yield Button("正文翻译", id="btn-translate_text", classes="action-btn")
                                    yield Button("错误重翻", id="btn-retry_error_table", classes="action-btn")
                                    yield Button("回写数据", id="btn-write_back", classes="action-btn")
                                yield Static("", classes="sidebar-spacer")
                                yield Button("返回首页", id="actions-back-button", variant="error")

                            with Vertical(id="actions-workspace"):
                                with Vertical(id="actions-task-panel"):
                                    with Horizontal(id="task-source-language-row"):
                                        yield Static("源语言", id="task-source-language-label")
                                        yield Select(
                                            options=SOURCE_LANGUAGE_OPTIONS,
                                            allow_blank=False,
                                            value="ja",
                                            id="actions-source-language-select",
                                            classes="setting-select",
                                        )
                                    yield Static(self.task_title_text, id="task-title")
                                    yield Static(self.task_phase_text, id="task-phase")
                                    yield Static(self.task_status_text, id="task-status")
                                    yield Static(self.task_detail_text, id="task-detail")
                                    yield ProgressBar(
                                        total=1,
                                        show_eta=True,
                                        show_percentage=True,
                                        id="task-progress",
                                    )

            with Vertical(id="right-pane"):
                yield Static("全局后台日志", id="global-log-title")
                yield RichLog(id="global-log-output")

        yield Footer(compact=True)

    def _build_setting_card(
        self,
        card_spec: SettingCardSpec,
        card_index: int,
    ) -> Vertical:
        """
        构造单张设置卡片。
        """
        children: list[Any] = [Static(card_spec.title, classes="section-title")]

        for section in card_spec.sections:
            children.append(Static(section.title, classes="section-subtitle"))
            grid_children = []
            for field_spec in section.fields:
                grid_children.append(
                    Vertical(
                        Static(
                            self._get_schema_field_title(field_spec.path),
                            classes="settings-field-title",
                        ),
                        self._build_setting_control(field_spec),
                        Static(
                            self._get_schema_field_description(field_spec.path),
                            classes="settings-field-desc",
                        ),
                        classes="settings-field-block",
                    )
                )
            children.append(Grid(*grid_children, classes="settings-fields-grid"))

        return Vertical(
            *children,
            id=f"settings-card-{card_index}",
            classes="settings-card",
        )

    def _build_setting_control(self, field_spec: SettingFieldSpec) -> Input | Select:
        """
        根据字段类型构造对应的输入控件。
        """
        if field_spec.kind == "enum":
            return Select(
                options=field_spec.options,
                allow_blank=False,
                id=field_spec.widget_id,
                classes="setting-select",
            )

        return Input(
            id=field_spec.widget_id,
            password=field_spec.secret,
            classes="setting-input",
        )

    async def on_mount(self) -> None:
        """
        挂载后初始化日志、编排器、配置和界面状态。
        """
        self.set_interval(0.1, self._drain_queues)
        setup_logger(use_console=False, ui_log_callbacks=(self._enqueue_log_line,))

        try:
            self.handler = await self._handler_factory()
        except Exception as error:
            logger.exception(
                f"[tag.exception]工作台初始化失败[/tag.exception]："
                f"{self._format_exception_summary(error)}"
            )
            self.home_status_text = "编排器初始化失败，请查看日志。"

        self._reload_setting_document()
        self._refresh_home_view()
        self._refresh_action_list()
        self._refresh_action_panel()
        self._refresh_settings_inputs()
        self._refresh_header()
        self._refresh_settings_note()
        self.query_one("#game-list", ListView).focus()

    async def action_request_quit(self) -> None:
        """
        退出应用并清理资源。
        """
        await self._shutdown()
        self.exit()

    def action_move_up(self) -> None:
        """
        在首页中向上移动列表高亮。
        """
        if self.current_view == "home":
            game_list = self.query_one("#game-list", ListView)
            game_list.focus()
            game_list.action_cursor_up()
            return

    def action_move_down(self) -> None:
        """
        在首页中向下移动列表高亮。
        """
        if self.current_view == "home":
            game_list = self.query_one("#game-list", ListView)
            game_list.focus()
            game_list.action_cursor_down()
            return

    def action_activate_current(self) -> None:
        """
        执行当前焦点对应的默认动作。
        """
        focused = self.focused
        if isinstance(focused, Button) and not focused.disabled:
            focused.press()
            return

        if self.current_view == "home":
            self._open_selected_game()
            return

        if self.current_view == "actions":
            self._run_selected_action()
            return

        if self.current_view == "settings" and isinstance(focused, Select):
            focused.action_show_overlay()

    def action_go_back(self) -> None:
        """
        从设置页或二级翻译页返回首页。
        """
        if self.current_view == "home":
            return

        if self.task_running or self._glossary_rebuild_checking:
            self.bell()
            return

        self._switch_to_home_view()

    def action_add_game(self) -> None:
        """
        打开添加游戏弹窗。
        """
        if self.current_view != "home" or self.task_running:
            return
        self.push_screen(AddGamePathScreen(), self._handle_add_game_result)

    def action_open_settings(self) -> None:
        """
        打开设置页。
        """
        if self.task_running:
            self.bell()
            return
        self._switch_to_settings_view()

    def action_focus_logs(self) -> None:
        """
        聚焦全局日志窗口。
        """
        self.query_one("#global-log-output", RichLog).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """
        处理界面按钮点击。

        Args:
            event: 按钮点击事件。
        """
        button_id = event.button.id
        if button_id == "action-add-game":
            self.action_add_game()
            return

        if button_id == "action-open-settings":
            self.action_open_settings()
            return

        if button_id == "actions-back-button":
            self.action_go_back()
            return
            
        if button_id == "settings-back-button":
            self.action_go_back()
            return

        if button_id and button_id.startswith("btn-"):
            action_id = button_id[4:]
            self.selected_action_id = action_id
            self._run_selected_action()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """
        根据当前高亮项同步选择状态。

        Args:
            event: 列表高亮事件。
        """
        if event.list_view.id == "game-list":
            item = event.item
            if item is None or item.name is None:
                self.selected_game_title = None
            else:
                self.selected_game_title = item.name
            self._refresh_header()
            return

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """
        回车执行当前高亮项的默认动作。

        Args:
            event: 列表选中事件。
        """
        if event.list_view.id == "game-list":
            self._open_selected_game()
            return

    def on_input_changed(self, event: Input.Changed) -> None:
        """
        处理设置页输入框变化。

        Args:
            event: 输入框变化事件。
        """
        if self._settings_event_suspended:
            return

        widget_id = event.input.id
        if widget_id is None:
            return

        field_spec = self._setting_specs_by_widget_id.get(widget_id)
        if field_spec is None:
            return

        self._try_save_setting(field_spec=field_spec, raw_value=event.value)

    def on_select_changed(self, event: Select.Changed) -> None:
        """
        处理下拉选择变化。

        Args:
            event: 下拉选择变化事件。
        """
        widget_id = event.select.id
        if widget_id is None:
            return

        if widget_id == "actions-source-language-select":
            self._handle_actions_source_language_change(event)
            return

        if self._settings_event_suspended:
            return

        field_spec = self._setting_specs_by_widget_id.get(widget_id)
        if field_spec is None:
            return

        self._try_save_setting(field_spec=field_spec, raw_value=str(event.value))

    def _handle_actions_source_language_change(self, event: Select.Changed) -> None:
        """
        处理任务页源语言切换。

        Args:
            event: 源语言下拉选择变化事件。
        """
        if self._settings_event_suspended:
            return
        if not self._actions_source_language_ready:
            return
        if not event.select.has_focus:
            return
        if self.task_running or self.source_language_updating:
            self._sync_actions_source_language_select()
            return
        if self.handler is None or self.selected_game_title is None:
            self._sync_actions_source_language_select()
            return

        new_source_language = cast(SourceLanguage, str(event.value))
        old_source_language = self.selected_game_source_language
        if old_source_language is None:
            old_source_language = self.handler.get_game_source_language(
                self.selected_game_title
            )
        if new_source_language == old_source_language:
            return

        self.source_language_updating = True
        self.task_detail_text = (
            "细节：正在保存源语言为 "
            f"{get_source_language_label(new_source_language)} ({new_source_language})"
        )
        self._refresh_action_list()
        self._refresh_action_panel()
        self.run_worker(
            self._run_update_source_language_task(
                game_title=self.selected_game_title,
                old_source_language=old_source_language,
                new_source_language=new_source_language,
            ),
            group="translation-workbench",
            exclusive=False,
            exit_on_error=False,
        )

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        """
        在密钥字段获得焦点时取消遮罩，便于用户校对内容。

        Args:
            event: 子控件聚焦事件。
        """
        widget = event.widget
        if isinstance(widget, Input) and widget.id in self._secret_widget_ids:
            widget.password = False

    def on_descendant_blur(self, event: events.DescendantBlur) -> None:
        """
        在密钥字段失焦后恢复遮罩，减少敏感信息暴露。

        Args:
            event: 子控件失焦事件。
        """
        widget = event.widget
        if isinstance(widget, Input) and widget.id in self._secret_widget_ids:
            widget.password = True

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

    def _switch_to_home_view(self) -> None:
        """
        切换到首页。
        """
        self._actions_source_language_ready = False
        self.current_view = "home"
        self.query_one("#view-switcher", ContentSwitcher).current = "home-view"
        self._refresh_home_view()
        self._refresh_header()
        self.query_one("#game-list", ListView).focus()

    def _switch_to_settings_view(self) -> None:
        """
        切换到设置页。
        """
        self.current_view = "settings"
        self._reload_setting_document()
        self._refresh_settings_inputs()
        self.query_one("#view-switcher", ContentSwitcher).current = "settings-view"
        self._refresh_settings_note()
        self._refresh_header()
        self._focus_first_setting_widget()

    def _switch_to_actions_view(self, game_title: str) -> None:
        """
        切换到二级翻译页。
        """
        self._actions_source_language_ready = False
        self.current_view = "actions"
        self.selected_game_source_language = self._load_game_source_language(game_title)
        self._reset_task_panel(game_title)
        self.query_one("#view-switcher", ContentSwitcher).current = "actions-view"
        self._refresh_action_list()
        self._refresh_action_panel()
        self._refresh_header()
        self.query_one("#btn-build_glossary", Button).focus()

    def _refresh_header(self) -> None:
        """
        根据当前视图刷新应用标题与副标题。
        """
        self.title = self._build_page_title()
        self.sub_title = self._build_page_status()

    def _build_page_title(self) -> str:
        """
        生成当前视图对应的标题文本。
        """
        if self.current_view == "settings":
            return "设置页面"
        if self.current_view == "actions":
            if (
                self.selected_game_title is not None
                and self.selected_game_source_language is not None
            ):
                language_label = get_source_language_label(
                    self.selected_game_source_language
                )
                return (
                    f"游戏：{self.selected_game_title} | "
                    f"源语言：{language_label} ({self.selected_game_source_language})"
                )
            return f"游戏：{self.selected_game_title or '未选择'}"
        return "选择游戏"

    def _build_page_status(self) -> str:
        """
        生成当前视图对应的状态文本。
        """
        if self.current_view == "settings":
            if self.settings_error_text:
                return self.settings_error_text
            if self.settings_dirty_fields:
                return "当前字段存在未保存或格式错误内容，文件已保留最后一次合法值。"
            if self.settings_success_text:
                return self.settings_success_text
            return "Tab 切换字段，滚动页面浏览所有设置项。"

        if self.current_view == "actions":
            return self.task_status_text

        return self.home_status_text

    def _refresh_home_view(self) -> None:
        """
        刷新首页游戏列表与命令按钮状态。
        """
        self._refresh_game_list()
        add_button = self.query_one("#action-add-game", Button)
        settings_button = self.query_one("#action-open-settings", Button)
        disabled = self.task_running or self.handler is None
        add_button.disabled = disabled
        settings_button.disabled = self.task_running
        if self.handler is not None and not self.home_status_text.startswith("已添加游戏："):
            game_count = len(self.handler.game_data_manager.items)
            self.home_status_text = (
                f"当前已载入 {game_count} 个游戏，方向键选择，Enter 进入。"
            )

    def _refresh_game_list(self) -> None:
        """
        刷新首页游戏列表内容。
        """
        game_list = self.query_one("#game-list", ListView)
        game_list.clear()

        if self.handler is None:
            game_list.disabled = True
            self.selected_game_title = None
            return

        available_titles = sorted(self.handler.game_data_manager.items)
        if not available_titles:
            self.selected_game_title = None
            game_list.disabled = self.task_running
            return

        game_list.extend(
            ListItem(Label(game_title), name=game_title)
            for game_title in available_titles
        )

        if self.selected_game_title not in available_titles:
            self.selected_game_title = available_titles[0]

        game_list.index = available_titles.index(self.selected_game_title)
        game_list.disabled = self.task_running

    def _refresh_action_list(self) -> None:
        """
        刷新二级翻译页功能按钮状态。
        """
        for btn in self.query(".action-btn"):
            if isinstance(btn, Button):
                btn.disabled = self.task_running or self._glossary_rebuild_checking

        self.query_one("#actions-back-button", Button).disabled = (
            self.task_running or self._glossary_rebuild_checking
        )
        self.query_one("#actions-source-language-select", Select).disabled = (
            self.task_running
            or self.source_language_updating
            or self._glossary_rebuild_checking
        )

    def _refresh_action_panel(self) -> None:
        """
        刷新任务信息区。
        """
        self.query_one("#task-title", Static).update(self.task_title_text)
        self.query_one("#task-phase", Static).update(self.task_phase_text)
        self.query_one("#task-status", Static).update(self.task_status_text)
        self.query_one("#task-detail", Static).update(self.task_detail_text)
        self._sync_actions_source_language_select()

        progress = self.query_one("#task-progress", ProgressBar)
        total = max(self.progress_total, 1)
        current = min(self.progress_current, total)
        progress.update(total=total, progress=current)

    def _refresh_settings_note(self) -> None:
        """
        刷新设置页顶部说明文本。
        """
        note_widget = self.query_one("#settings-note", Static)
        if self.settings_error_text:
            note_widget.update(self.settings_error_text)
            return

        if self.settings_dirty_fields:
            note_widget.update("当前输入尚未通过校验，`setting.toml` 仍保持最后一次合法值。")
            return

        if self.settings_success_text:
            note_widget.update(self.settings_success_text)
            return

        note_widget.update(
            (
                f"共 {len(SETTING_CARDS)} 个配置块。"
                "Tab 切换字段，上下滚动浏览，Esc 返回首页。"
            )
        )

    def _reload_setting_document(self) -> None:
        """
        重新加载原始 `setting.toml` 文档。
        """
        try:
            self._setting_document = load_setting_document()
            self.settings_dirty_fields.clear()
            self.settings_error_text = ""
            self.settings_success_text = "已载入当前 setting.toml 配置。"
        except Exception as error:
            error_summary = self._format_exception_summary(error)
            self._setting_document = None
            self.settings_error_text = f"配置读取失败：{error_summary}"
            self.settings_success_text = ""
            logger.exception(
                f"[tag.exception]设置文件读取失败[/tag.exception]：{error_summary}"
            )

    def _refresh_settings_inputs(self) -> None:
        """
        把当前配置文件中的值同步到设置页控件。
        """
        if self._setting_document is None:
            return

        self._settings_event_suspended = True
        try:
            for field_spec in ALL_SETTING_FIELDS:
                value = self._read_document_value(field_spec.path)
                if field_spec.kind == "enum":
                    widget = self.query_one(f"#{field_spec.widget_id}", Select)
                    widget.value = str(value)
                    continue

                widget = self.query_one(f"#{field_spec.widget_id}", Input)
                widget.value = self._format_setting_value(value)
                widget.password = field_spec.secret
        finally:
            self._settings_event_suspended = False

    def _focus_first_setting_widget(self) -> None:
        """
        聚焦设置页中的第一个字段控件。
        """
        first_widget_id = ALL_SETTING_FIELDS[0].widget_id
        self.query_one(f"#{first_widget_id}").focus()

    def _handle_add_game_result(
        self,
        result: tuple[str, SourceLanguage] | None,
    ) -> None:
        """
        处理添加游戏弹窗返回值。
        """
        if result is None:
            return
        game_path, source_language = result
        self.start_add_game(game_path, source_language)

    def _open_selected_game(self) -> None:
        """
        打开当前选中游戏的功能视图。
        """
        if self.selected_game_title is None or self.task_running:
            return
        self._switch_to_actions_view(self.selected_game_title)

    def _run_selected_action(self) -> None:
        """
        执行当前选中的翻译功能。
        """
        if (
            self.selected_game_title is None
            or self.task_running
            or self._glossary_rebuild_checking
        ):
            return
        self.start_game_task(self.selected_action_id, self.selected_game_title)

    def start_add_game(
        self,
        game_path: str,
        source_language: SourceLanguage,
    ) -> None:
        """
        启动添加游戏任务。
        """
        if self.task_running:
            return
        self.task_running = True
        self.home_status_text = "正在注册游戏..."
        self._refresh_home_view()
        self._refresh_header()
        self.run_worker(
            self._run_add_game_task(game_path, source_language),
            group="translation-workbench",
            exclusive=True,
            exit_on_error=False,
        )

    def start_game_task(self, action_id: str, game_title: str) -> None:
        """
        针对当前游戏启动指定任务。
        """
        self._start_game_task(
            action_id=action_id,
            game_title=game_title,
            force_rebuild=False,
            skip_glossary_confirmation=False,
        )

    def _start_game_task(
        self,
        action_id: str,
        game_title: str,
        force_rebuild: bool,
        skip_glossary_confirmation: bool,
    ) -> None:
        """
        按当前上下文真正启动任务，或在术语构建前先进入重建确认分支。

        Args:
            action_id: 当前动作标识。
            game_title: 目标游戏标题。
            force_rebuild: 是否强制重建术语表。
            skip_glossary_confirmation: 是否跳过“已有术语表”检查。
        """
        if self.task_running or self._glossary_rebuild_checking:
            return

        if action_id == "build_glossary" and not skip_glossary_confirmation:
            self._start_glossary_rebuild_check(game_title)
            return

        coroutine: Awaitable[None] | None = None
        task_label = ""
        if action_id == "build_glossary":
            task_label = "构建术语"
            coroutine = self._run_build_glossary_task(
                game_title,
                force_rebuild=force_rebuild,
            )
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

        self.task_running = True
        self._set_task_context(task_label, game_title)
        self._refresh_action_list()
        self._refresh_action_panel()
        self._refresh_header()
        self.run_worker(
            coroutine,
            group="translation-workbench",
            exclusive=True,
            exit_on_error=False,
        )

    def _start_glossary_rebuild_check(self, game_title: str) -> None:
        """
        在真正启动术语构建前，先检查当前游戏是否已经存在完整术语表。

        这里要用异步 worker 做检查，避免在 UI 线程里直接等待数据库与提取逻辑。

        Args:
            game_title: 目标游戏标题。
        """
        if self._glossary_rebuild_checking or self.task_running:
            return

        self._glossary_rebuild_checking = True
        self.task_title_text = "当前任务：构建术语"
        self.task_phase_text = self._build_task_phase_text(game_title)
        self.task_status_text = "状态：正在检查现有术语表"
        self.task_detail_text = "细节：如果已存在完整术语表，将先询问是否重建。"
        self._refresh_action_list()
        self._refresh_action_panel()
        self._refresh_header()
        self.run_worker(
            self._run_glossary_rebuild_check_task(game_title),
            group="translation-workbench",
            exclusive=True,
            exit_on_error=False,
        )

    def _handle_glossary_rebuild_confirm(
        self,
        result: tuple[str, bool] | None,
    ) -> None:
        """
        处理术语表重建确认弹窗结果。

        Args:
            result: 用户确认结果；为 `None` 表示取消。
        """
        if result is None:
            self.task_status_text = "状态：已取消重建"
            self.task_detail_text = "细节：继续沿用已有术语表。"
            self._refresh_action_list()
            self._refresh_action_panel()
            self._refresh_header()
            self.query_one("#btn-build_glossary", Button).focus()
            return

        game_title, force_rebuild = result
        self._start_game_task(
            action_id="build_glossary",
            game_title=game_title,
            force_rebuild=force_rebuild,
            skip_glossary_confirmation=True,
        )

    async def _run_add_game_task(
        self,
        game_path: str,
        source_language: SourceLanguage,
    ) -> None:
        """
        执行添加游戏任务。
        """
        if self.handler is None:
            self._ui_queue.put(("home_status", "编排器未初始化"))
            self._ui_queue.put(("task_done",))
            return

        try:
            game_title = await self.handler.add_game(game_path, source_language)
            self._ui_queue.put(("reload_games", game_title, source_language))
            self._ui_queue.put(
                (
                    "home_status",
                    "已添加游戏："
                    f"{game_title}（源语言："
                    f"{get_source_language_label(source_language)} ({source_language})）",
                )
            )
        except Exception as error:
            error_summary = self._format_exception_summary(error)
            logger.exception(
                f"[tag.exception]添加游戏任务失败[/tag.exception]：{error_summary}"
            )
            self._ui_queue.put(("home_status", f"添加游戏失败：{error_summary}"))
        finally:
            self._ui_queue.put(("task_done",))

    async def _run_update_source_language_task(
        self,
        game_title: str,
        old_source_language: SourceLanguage,
        new_source_language: SourceLanguage,
    ) -> None:
        """
        执行任务页源语言更新任务。

        Args:
            game_title: 目标游戏标题。
            old_source_language: 变更前的源语言。
            new_source_language: 用户刚选择的新源语言。
        """
        if self.handler is None:
            self._ui_queue.put(
                (
                    "source_language_update_failed",
                    game_title,
                    old_source_language,
                    "编排器未初始化",
                )
            )
            return

        try:
            await self.handler.update_game_source_language(
                game_title=game_title,
                source_language=new_source_language,
            )
            self._ui_queue.put(
                (
                    "source_language_updated",
                    game_title,
                    new_source_language,
                )
            )
        except Exception as error:
            error_summary = self._format_exception_summary(error)
            logger.exception(
                f"[tag.exception]更新源语言失败[/tag.exception]：{error_summary}"
            )
            self._ui_queue.put(
                (
                    "source_language_update_failed",
                    game_title,
                    old_source_language,
                    error_summary,
                )
            )

    async def _run_glossary_rebuild_check_task(self, game_title: str) -> None:
        """
        检查当前游戏是否已经存在完整术语表。

        Args:
            game_title: 目标游戏标题。
        """
        if self.handler is None:
            self._ui_queue.put(
                ("glossary_rebuild_check_failed", "编排器未初始化，无法检查术语表")
            )
            return

        try:
            has_complete_glossary = await self.handler.has_complete_glossary(game_title)
            if has_complete_glossary:
                self._ui_queue.put(("prompt_glossary_rebuild", game_title))
                return
            self._ui_queue.put(("start_glossary_build", game_title, False))
        except Exception as error:
            error_summary = self._format_exception_summary(error)
            logger.exception(
                f"[tag.exception]术语表重建检查失败[/tag.exception]：{error_summary}"
            )
            self._ui_queue.put(("glossary_rebuild_check_failed", error_summary))

    async def _run_build_glossary_task(
        self,
        game_title: str,
        force_rebuild: bool,
    ) -> None:
        """
        执行术语构建任务。

        Args:
            game_title: 目标游戏标题。
            force_rebuild: 是否按用户要求强制重建术语表。
        """
        if self.handler is None:
            self._ui_queue.put(("finished", "术语构建失败：编排器未初始化"))
            return

        try:
            if force_rebuild:
                self._queue_detail("开始重建术语，旧术语表将在成功后再被替换")
            else:
                self._queue_detail("开始构建术语")
            await self.handler.build_glossary(
                game_title=game_title,
                callbacks=(self._queue_set_progress, self._queue_advance_progress),
                force_rebuild=force_rebuild,
            )
            self._ui_queue.put(("finished", "术语构建完成"))
        except Exception as error:
            error_summary = self._format_exception_summary(error)
            logger.exception(
                f"[tag.exception]术语构建任务失败[/tag.exception]：{error_summary}"
            )
            self._ui_queue.put(("finished", f"术语构建失败：{error_summary}"))

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
            error_summary = self._format_exception_summary(error)
            logger.exception(
                f"[tag.exception]正文翻译任务失败[/tag.exception]：{error_summary}"
            )
            self._ui_queue.put(("finished", f"正文翻译失败：{error_summary}"))

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
            error_summary = self._format_exception_summary(error)
            logger.exception(
                f"[tag.exception]错误重翻任务失败[/tag.exception]：{error_summary}"
            )
            self._ui_queue.put(("finished", f"错误重翻失败：{error_summary}"))

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
            error_summary = self._format_exception_summary(error)
            logger.exception(
                f"[tag.exception]回写任务失败[/tag.exception]：{error_summary}"
            )
            self._ui_queue.put(("finished", f"回写失败：{error_summary}"))

    def _try_save_setting(self, field_spec: SettingFieldSpec, raw_value: str) -> None:
        """
        尝试把当前字段修改实时保存到 `setting.toml`。
        """
        widget_id = field_spec.widget_id
        try:
            converted_value = self._convert_setting_value(field_spec, raw_value)
        except ValueError as error:
            self.settings_dirty_fields.add(widget_id)
            self.settings_error_text = f"{self._get_schema_field_title(field_spec.path)} 暂未保存：{error}"
            self.settings_success_text = ""
            self._refresh_settings_note()
            self._refresh_header()
            return

        try:
            save_setting_value(field_path=field_spec.path, value=converted_value)
            self._setting_document = load_setting_document()
            self.settings_dirty_fields.discard(widget_id)
            self.settings_error_text = ""
            self.settings_success_text = (
                f"{self._get_schema_field_title(field_spec.path)} 已保存到 setting.toml。"
            )
        except Exception as error:
            self.settings_dirty_fields.add(widget_id)
            self.settings_error_text = (
                f"{self._get_schema_field_title(field_spec.path)} 保存失败：{error}"
            )
            self.settings_success_text = ""
        self._refresh_settings_note()
        self._refresh_header()

    def _convert_setting_value(
        self,
        field_spec: SettingFieldSpec,
        raw_value: str,
    ) -> str | int | float:
        """
        把界面输入文本转换成可写入配置文件的类型。
        """
        if field_spec.kind == "int":
            return int(raw_value)
        if field_spec.kind == "float":
            return float(raw_value)
        return raw_value

    def _read_document_value(self, field_path: tuple[str, ...]) -> Any:
        """
        从当前原始 TOML 文档中读取字段值。
        """
        if self._setting_document is None:
            return ""

        current: Any = self._setting_document
        for key in field_path:
            current = current[key]

        if hasattr(current, "unwrap"):
            return current.unwrap()
        return current

    def _format_setting_value(self, value: Any) -> str:
        """
        把配置值转成可写入输入框的文本。
        """
        return "" if value is None else str(value)

    def _get_schema_field_title(self, field_path: tuple[str, ...]) -> str:
        """
        从 Pydantic 配置模型中读取字段标题。
        """
        field_info = self._resolve_schema_field(field_path)
        return field_info.title or field_path[-1]

    def _get_schema_field_description(self, field_path: tuple[str, ...]) -> str:
        """
        从 Pydantic 配置模型中读取字段说明。
        """
        field_info = self._resolve_schema_field(field_path)
        return field_info.description or "当前字段未补充说明。"

    def _resolve_schema_field(self, field_path: tuple[str, ...]) -> Any:
        """
        按路径解析配置模型中的目标字段元信息。
        """
        model_type: type[BaseModel] = Setting
        field_info: Any = None

        for index, key in enumerate(field_path):
            field_info = model_type.model_fields[key]
            if index == len(field_path) - 1:
                break

            annotation = self._unwrap_annotation(field_info.annotation)
            model_type = cast(type[BaseModel], annotation)

        return field_info

    def _unwrap_annotation(self, annotation: Any) -> Any:
        """
        从可能的联合类型中提取实际模型类型。
        """
        origin = get_origin(annotation)
        if origin is None:
            return annotation

        for candidate in get_args(annotation):
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                return candidate
        return annotation

    def _enqueue_log_line(self, log_line: LogLine) -> None:
        """
        写入日志队列。
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
                self._refresh_action_panel()
                self._refresh_header()
                continue

            if kind == "advance_progress":
                self.progress_current += int(item[1])
                self._update_progress_status()
                self._refresh_action_panel()
                self._refresh_header()
                continue

            if kind == "detail":
                self.task_detail_text = f"细节：{item[1]}"
                self._refresh_action_panel()
                continue

            if kind == "finished":
                self.task_running = False
                self.task_status_text = f"状态：{item[1]}"
                self.task_detail_text = "细节：任务已结束"
                self._refresh_action_list()
                self._refresh_action_panel()
                self._refresh_header()
                continue

            if kind == "home_status":
                self.home_status_text = str(item[1])
                self._refresh_home_view()
                self._refresh_header()
                continue

            if kind == "reload_games":
                self.selected_game_title = cast(str, item[1])
                self.selected_game_source_language = cast(SourceLanguage, item[2])
                self._refresh_home_view()
                self._refresh_header()
                continue

            if kind == "source_language_updated":
                game_title = cast(str, item[1])
                source_language = cast(SourceLanguage, item[2])
                self.source_language_updating = False
                if self.selected_game_title == game_title:
                    self.selected_game_source_language = source_language
                self.task_detail_text = (
                    "细节：源语言已更新为 "
                    f"{get_source_language_label(source_language)} ({source_language})"
                )
                self._refresh_action_list()
                self._refresh_action_panel()
                self._refresh_header()
                continue

            if kind == "source_language_update_failed":
                game_title = cast(str, item[1])
                source_language = cast(SourceLanguage, item[2])
                error_summary = str(item[3])
                self.source_language_updating = False
                if self.selected_game_title == game_title:
                    self.selected_game_source_language = source_language
                self.task_detail_text = f"细节：源语言保存失败：{error_summary}"
                self._refresh_action_list()
                self._refresh_action_panel()
                self._refresh_header()
                continue

            if kind == "prompt_glossary_rebuild":
                self._glossary_rebuild_checking = False
                game_title = cast(str, item[1])
                self.task_status_text = "状态：等待确认是否重建"
                self.task_detail_text = "细节：当前游戏已有完整术语表，确认后才会开始重建。"
                self.push_screen(
                    GlossaryRebuildConfirmScreen(game_title),
                    self._handle_glossary_rebuild_confirm,
                )
                self._refresh_action_list()
                self._refresh_action_panel()
                self._refresh_header()
                continue

            if kind == "start_glossary_build":
                self._glossary_rebuild_checking = False
                game_title = cast(str, item[1])
                force_rebuild = bool(item[2])
                self._refresh_action_list()
                self._refresh_action_panel()
                self._refresh_header()
                self._start_game_task(
                    action_id="build_glossary",
                    game_title=game_title,
                    force_rebuild=force_rebuild,
                    skip_glossary_confirmation=True,
                )
                continue

            if kind == "glossary_rebuild_check_failed":
                self._glossary_rebuild_checking = False
                error_summary = str(item[1])
                self.task_status_text = "状态：术语表检查失败"
                self.task_detail_text = f"细节：{error_summary}"
                self._refresh_action_list()
                self._refresh_action_panel()
                self._refresh_header()
                continue

            if kind == "task_done":
                self.task_running = False
                self._refresh_home_view()
                self._refresh_action_list()
                self._refresh_action_panel()
                self._refresh_header()

    def _drain_log_queue(self) -> None:
        """
        批量处理日志队列，并写入全局日志窗口。
        """
        while True:
            try:
                log_line = self._log_queue.get_nowait()
            except Empty:
                break

            self.log_history.append(log_line)
            try:
                log_output = self.query_one("#global-log-output", RichLog)
                self._write_log_line(log_output, log_line)
            except Exception:
                pass

    def _reset_task_panel(self, game_title: str) -> None:
        """
        重置二级翻译页中的任务面板。
        """
        self.progress_current = 0
        self.progress_total = 0
        self.task_title_text = "当前任务：未开始"
        self.task_phase_text = self._build_task_phase_text(game_title)
        self.task_status_text = "状态：请选择功能"
        self.task_detail_text = "细节：使用上下键选择功能，Enter 执行，Esc 返回"

    def _set_task_context(self, task_label: str, game_title: str) -> None:
        """
        设置任务上下文并清空进度。
        """
        self.progress_current = 0
        self.progress_total = 0
        self.task_title_text = f"当前任务：{task_label}"
        self.task_phase_text = self._build_task_phase_text(game_title)
        self.task_status_text = "状态：执行中"
        self.task_detail_text = "细节：等待进度更新"

    def _load_game_source_language(self, game_title: str) -> SourceLanguage:
        """
        从编排器读取指定游戏的源语言。

        Args:
            game_title: 目标游戏标题。

        Returns:
            当前游戏已登记的源语言。

        Raises:
            RuntimeError: 编排器未初始化，或当前游戏缺少可用源语言元数据时抛出。
        """
        if self.handler is None:
            raise RuntimeError("编排器未初始化，无法读取游戏源语言")

        source_language = self.handler.get_game_source_language(game_title)
        if source_language not in {"ja", "en"}:
            raise RuntimeError(
                f"游戏源语言元数据非法，无法进入任务页：{game_title} -> {source_language}"
            )
        return source_language

    def _build_task_phase_text(self, game_title: str) -> str:
        """
        生成任务面板中的游戏与源语言摘要文本。

        Args:
            game_title: 当前任务对应的游戏标题。

        Returns:
            面板阶段文本。

        Raises:
            RuntimeError: 当前动作页缺少源语言元数据时抛出。
        """
        if self.selected_game_source_language is None:
            raise RuntimeError(
                f"游戏缺少源语言元数据，无法构建任务面板：{game_title}"
            )

        language_label = get_source_language_label(self.selected_game_source_language)
        return (
            f"游戏：{game_title} | "
            f"源语言：{language_label} ({self.selected_game_source_language})"
        )

    def _sync_actions_source_language_select(self) -> None:
        """
        把当前内存中的源语言状态同步到任务页下拉框。

        Raises:
            RuntimeError: 已进入动作页但当前游戏缺少源语言元数据时抛出。
        """
        if self.current_view != "actions":
            return

        if self.selected_game_source_language is None:
            raise RuntimeError("动作页缺少源语言元数据，无法同步源语言下拉框")

        select = self.query_one("#actions-source-language-select", Select)
        self._settings_event_suspended = True
        try:
            select.value = self.selected_game_source_language
        finally:
            self._settings_event_suspended = False
            self._actions_source_language_ready = True

    def _update_progress_status(self) -> None:
        """
        根据当前进度刷新任务状态文本。
        """
        if self.progress_total <= 0:
            self.task_status_text = "状态：等待任务数据"
            return
        self.task_status_text = (
            f"状态：处理中 {self.progress_current}/{self.progress_total}"
        )

    @staticmethod
    def _format_exception_summary(error: Exception) -> str:
        """
        生成适合界面与日志首行展示的异常摘要。

        为什么不直接使用 `str(error)`：
        某些异常的字符串结果为空，界面上会退化成“任务失败：”这一类无效提示。
        同时要主动展开 `ExceptionGroup`，否则并发任务失败时界面只会看到
        一层外壳类型，真正的底层原因会被埋在长堆栈里。

        Args:
            error: 当前捕获到的异常对象。

        Returns:
            `异常类型: 异常信息` 形式的稳定摘要；若异常消息为空则仅返回类型名。
        """
        current_error: BaseException = error
        while isinstance(current_error, BaseExceptionGroup):
            if not current_error.exceptions:
                break
            current_error = current_error.exceptions[0]

        detail = str(current_error).strip()
        if detail:
            return f"{type(current_error).__name__}: {detail}"
        return type(current_error).__name__

    @staticmethod
    def _write_log_line(log_output: RichLog, log_line: LogLine) -> None:
        """
        按日志级别写入一条日志，使用 Rich markup 还原终端色彩。
        """
        # 尝试将 loguru 的原始带 markup 的 message 重新格式化为终端行
        # 我们使用 Text.from_markup() 来保留 <cyan> 等标签
        try:
            timestamp_text = f"[{log_line.timestamp}] "
            level_text = f"{log_line.level:<8} "
            
            # 对日志级别上色以增强可读性
            level_style = {
                "DEBUG": "dim",
                "INFO": "cyan",
                "SUCCESS": "bold green",
                "WARNING": "bold yellow",
                "ERROR": "bold red",
                "CRITICAL": "bold white on red",
            }.get(log_line.level.upper(), "")

            line = Text()
            line.append(timestamp_text, style="dim")
            line.append(level_text, style=level_style)
            line.append(Text.from_markup(log_line.message))
            log_output.write(line)
        except Exception:
            # 如果解析 markup 失败，回退到纯文本
            log_output.write(Text(log_line.plain_text))


__all__: list[str] = [
    "AddGamePathScreen",
    "GlossaryRebuildConfirmScreen",
    "TranslationWorkbenchApp",
]
