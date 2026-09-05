from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import threading
import time
from functools import partial
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Callable,
    Dict,
    Optional,
    Sequence,
    Type,
    Union,
)

from textual import events, on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import DOMQuery
from textual.dom import DOMNode
from textual.driver import Driver
from textual.lazy import Lazy
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen, ScreenResultCallbackType, ScreenResultType
from textual.timer import Timer
from textual.types import CSSPathType
from textual.widget import AwaitMount, Widget
from textual.widgets import Button, Footer, Input, TextArea
from textual.worker import Worker, WorkerState
from textual_fastdatatable import DataTable
from textual_fastdatatable.backend import ArrowBackend

from harlequin import HarlequinConnection
from harlequin.actions import Action, build_actions
from harlequin.adapter import HarlequinAdapter
from harlequin.app_base import AppBase
from harlequin.autocomplete import completer_factory
from harlequin.autocomplete.completers import MemberCompleter, WordCompleter
from harlequin.bindings import bind
from harlequin.catalog import (
    Catalog,
    CatalogItem,
    Interaction,
    TCatalogItem_contra,
)
from harlequin.catalog_cache import (
    CatalogCache,
    get_catalog_cache,
    update_catalog_cache,
)
from harlequin.commands import (
    CommandInvocation,
    CommandResult,
    TableSnapshot,
    build_env,
    results_manifest,
    run_command,
)
from harlequin.components import (
    CodeEditor,
    DataCatalog,
    DebugInfoScreen,
    EditorCollection,
    ErrorModal,
    ExportScreen,
    HelpScreen,
    HistoryScreen,
    ResultsViewer,
    RunQueryBar,
    export_callback,
)
from harlequin.components.column_list import ColumnList
from harlequin.components.command_menu import CommandMenu
from harlequin.components.confirm_modal import ConfirmModal
from harlequin.components.data_catalog import ContextMenu
from harlequin.components.data_catalog.tree import HarlequinTree
from harlequin.components.debug_info import AdapterDebugInfo, HarlequinDebugInfo
from harlequin.config import (
    CommandConfig,
    get_highest_priority_existing_config_file,
    load_config,
    load_profile_and_keymaps,
)
from harlequin.copy_formats import HARLEQUIN_COPY_FORMATS, WINDOWS_COPY_FORMATS
from harlequin.driver import HarlequinDriver
from harlequin.editor_cache import Cache
from harlequin.editor_cache import write_cache as write_editor_cache
from harlequin.exception import (
    HarlequinBindingError,
    HarlequinConfigError,
    HarlequinConnectionError,
    HarlequinError,
    HarlequinExternalError,
    pretty_error_message,
    pretty_print_error,
)
from harlequin.history import History
from harlequin.messages import NewCatalog, NewCatalogItems, WidgetMounted
from harlequin.plugins import load_keymap_plugins
from harlequin.query import ExecutedStatement, ResultSet, RowLimit, execute, fetch
from harlequin.statements import Statement
from harlequin.transaction_mode import HarlequinTransactionMode

if TYPE_CHECKING:
    from textual.await_complete import AwaitComplete

    from harlequin.keymap import HarlequinKeyBinding, HarlequinKeyMap
    from harlequin.ssh import SshTunnel


class CatalogCacheLoaded(Message):
    def __init__(self, cache: CatalogCache) -> None:
        super().__init__()
        self.cache = cache


class DatabaseConnected(Message):
    def __init__(self, connection: HarlequinConnection) -> None:
        super().__init__()
        self.connection = connection


class QueryError(Message):
    def __init__(self, query_text: str, error: BaseException) -> None:
        super().__init__()
        self.query_text = query_text
        self.error = error


class QuerySubmitted(Message):
    def __init__(self, queries: list[str], limit: int | None) -> None:
        super().__init__()
        self.queries = queries
        self.limit = limit
        self.submitted_at = time.monotonic()


class QueriesExecuted(Message):
    def __init__(
        self,
        query_count: int,
        cursors: Dict[str, ExecutedStatement],
        submitted_at: float,
        ddl_queries: list[str],
        limit: RowLimit,
    ) -> None:
        super().__init__()
        self.query_count = query_count
        self.cursors = cursors
        self.submitted_at = submitted_at
        self.ddl_queries = ddl_queries
        self.limit = limit
        """The limit these cursors were executed under; the fetch needs it too."""


class QueriesCanceled(Message):
    pass


class CatalogRefreshAborted(Message):
    """The catalog worker stopped without a connection to build a tree on."""


class ResultsFetched(Message):
    def __init__(
        self,
        cursors: Dict[str, ExecutedStatement],
        results: Dict[str, ResultSet],
        errors: list[tuple[BaseException, str]],
        elapsed: float,
    ) -> None:
        super().__init__()
        self.cursors = cursors
        self.results = results
        self.errors = errors
        self.elapsed = elapsed


class TunnelClosed(Message):
    """The SSH tunnel's child exited on its own, and took the forward with it."""

    def __init__(self, notice: str) -> None:
        super().__init__()
        self.notice = notice


class TunnelReconnected(Message):
    """The dropped tunnel is back, and what runs through it is a new session."""

    def __init__(self, connection: HarlequinConnection, rebuild_catalog: bool) -> None:
        super().__init__()
        self.connection = connection
        self.rebuild_catalog = rebuild_catalog
        """Whether this handler is the one that has to rebuild the tree."""


class TunnelUnrecoverable(Message):
    """The tunnel dropped and would not come back, in ssh's own words."""

    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self.error = error


class TransactionModeChanged(Message):
    def __init__(self, new_mode: HarlequinTransactionMode | None) -> None:
        super().__init__()
        self.new_mode = new_mode


class ExternalCommandFinished(Message):
    """A configured command exited; what it said, and how."""

    def __init__(self, name: str, result: CommandResult, tmpdir: str | None) -> None:
        super().__init__()
        self.name = name
        self.result = result
        self.tmpdir = tmpdir


class CompletersReady(Message):
    def __init__(
        self, word_completer: WordCompleter, member_completer: MemberCompleter
    ) -> None:
        super().__init__()
        self.word_completer = word_completer
        self.member_completer = member_completer


_PARTIAL_FAILURE_WORKER_NOTIFICATIONS: dict[str, str] = {
    "_load_catalog_cache": (
        "Harlequin could not load its cache; your query history may be missing."
    ),
    "_extend_and_merge_completers": "Harlequin could not update completions.",
    "_build_completers": "Harlequin could not build completions.",
}
"""Toast text for the workers whose failure is partial: the app stays usable,
so their errors surface as a notification rather than an error modal.
"""


def _harlequin_version() -> str:
    """What `HARLEQUIN_VERSION` tells a command it is talking to.

    From the installed metadata, the way `--version` reads it, so a command sees the
    same string the user would. Unknown rather than fatal where the package metadata is
    missing (an editable checkout someone has not installed).
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("harlequin")
    except PackageNotFoundError:  # pragma: no cover -- an uninstalled checkout
        return "unknown"


def _footer_slot(binding: "HarlequinKeyBinding", action: Action) -> bool:
    """Whether the footer lists this binding.

    The binding decides when it says so; otherwise the action does, with the rule
    that a `key_display` implies the binding wanted to be seen -- which was true of
    every binding that set one before `show` existed.
    """
    if binding.show is not None:
        return binding.show
    return action.show or bool(binding.key_display)


class Harlequin(AppBase):
    """
    The SQL IDE for your Terminal.
    """

    _selection_made_at: float | None = None
    """When the user last highlighted something, on the monotonic clock."""

    CSS_PATH = ["global.tcss", "app.tcss"]

    full_screen: reactive[bool] = reactive(False)
    sidebar_hidden: reactive[bool] = reactive(False)
    narrow: reactive[bool] = reactive(False)
    """The terminal is under `catalog_min_width` columns wide.

    In narrow mode the Data Catalog is an overlay over the main panel rather than
    a column beside it, and the Run Query Bar shortens its labels.
    """
    catalog_overlay: reactive[bool] = reactive(False)
    """Whether the Data Catalog overlay is showing (narrow mode only)."""

    def __init__(
        self,
        adapter: HarlequinAdapter,
        profile_name: str | None = None,
        *,
        keymap_names: Sequence[str] | None = None,
        user_defined_keymaps: Sequence[HarlequinKeyMap] | None = None,
        connection_hash: str | None = None,
        theme: str = "harlequin",
        show_files: Path | None = None,
        show_s3: str | None = None,
        catalog_side: str = "left",
        catalog_min_width: int | str | None = None,
        catalog_exclude: Sequence[str] | str | None = None,
        export_path: Path | str | None = None,
        viewer_max_rows: int | str | None = 100_000,
        query_limit: int | str | None = None,
        ssh_tunnel: SshTunnel | None = None,
        commands: dict[str, CommandConfig] | None = None,
        adapter_name: str | None = None,
        driver_class: Union[Type[Driver], None] = None,
        css_path: Union[CSSPathType, None] = None,
        watch_css: bool = False,
    ):
        super().__init__(
            theme=theme,
            driver_class=driver_class,
            css_path=css_path,
            watch_css=watch_css,
        )
        self.adapter = adapter
        self.profile_name = profile_name
        self.active_profile_name = self._resolve_active_profile_name(profile_name)
        """The profile actually in force, which the Run Query Bar shows."""
        if self.active_profile_name:
            # also the terminal's window/tab title, for telling two sessions
            # apart from outside Harlequin
            self.title = f"Harlequin ({self.active_profile_name})"
        self.connection_hash = connection_hash
        self.history: History | None = None
        self.show_files = show_files
        self.show_s3 = show_s3 or None
        # which side of the main panel the Data Catalog sits on. Anything but
        # "right" is left, so a typo in the config file costs the default
        # layout rather than the app.
        self.catalog_side = "right" if str(catalog_side).lower() == "right" else "left"
        # Glob patterns the Data Catalog hides. A config file can spell one pattern as a
        # bare string, so a string is one pattern rather than a list of its characters.
        if catalog_exclude is None:
            self.catalog_exclude: tuple[str, ...] = ()
        elif isinstance(catalog_exclude, str):
            self.catalog_exclude = (catalog_exclude,)
        else:
            self.catalog_exclude = tuple(str(pattern) for pattern in catalog_exclude)
        # under this many columns the catalog is an overlay (see `narrow`). 0 or
        # None keeps the column at every width, which is also what tests and other
        # library callers get by default; the CLI supplies its own default.
        try:
            self.catalog_min_width = int(catalog_min_width or 0)
        except (TypeError, ValueError):
            self.catalog_min_width = 0
            self.exit(
                return_code=2,
                message=pretty_error_message(
                    HarlequinConfigError(
                        f"catalog_min_width={catalog_min_width!r} was set by config "
                        "file but is not a valid integer."
                    )
                ),
            )
        # already started, by the command that built this app: `ssh` prompts for
        # a passphrase on the terminal Textual is about to take.
        self.ssh_tunnel = ssh_tunnel
        # kept as text: it is what the Data Exporter's path input starts with
        self.export_path = str(export_path) if export_path is not None else None
        # None is no cap: the viewer holds every row that was fetched. So are 0
        # and -1, which the CLI has already normalized -- a Results Viewer that
        # holds no rows serves nobody, so neither spelling can mean that here.
        try:
            rows = None if viewer_max_rows is None else int(viewer_max_rows)
            self.viewer_max_rows = rows if rows is None or rows > 0 else None
        except ValueError:
            # assigned anyway: `self.exit()` schedules the exit rather than
            # taking it, and the rest of __init__ still runs.
            self.viewer_max_rows = None
            self.exit(
                return_code=2,
                message=pretty_error_message(
                    HarlequinConfigError(
                        f"viewer_max_rows={viewer_max_rows!r} was set by config file "
                        "but is not a valid integer."
                    )
                ),
            )
        # the hard limit, which the Run Query Bar holds and every query runs
        # under. None leaves the box unchecked, which is a full fetch; 0 is a
        # header and no rows, so only a negative number can mean "no limit".
        try:
            rows = None if query_limit is None else int(query_limit)
            self.query_limit = rows if rows is None or rows >= 0 else None
        except ValueError:
            self.query_limit = None
            self.exit(
                return_code=2,
                message=pretty_error_message(
                    HarlequinConfigError(
                        f"limit={query_limit!r} was set by config file "
                        "but is not a valid integer."
                    )
                ),
            )
        self.query_timer: Union[float, None] = None
        self.connection: HarlequinConnection | None = None
        self._recovery_lock = threading.Lock()
        """Held across reopening the tunnel and the connection through it.

        Two workers reaching recovery at once would otherwise each open a
        connection, and one of them would be closed out from under the worker
        still running on it.
        """
        self._recovered_connection: HarlequinConnection | None = None
        """The connection a recovery opened, for a worker that waited on it."""
        self.harlequin_driver = HarlequinDriver(app=self)
        self._completer_merge_timer: Timer | None = None
        self._pending_completer_items: list[tuple[CatalogItem, list[CatalogItem]]] = []

        if keymap_names is None:
            keymap_names = ("vscode",)
        if user_defined_keymaps is None:
            user_defined_keymaps = []

        self.keymap_names = keymap_names
        # Commands from config, and the action registry that includes them. A keymap
        # binds `command.<name>`, so the registry has to exist before any key is bound.
        self.commands: dict[str, CommandConfig] = dict(commands or {})
        self.adapter_name = adapter_name
        self.actions = build_actions(self.commands)
        self._approved_programs: set[str] = set()
        """Programs the user has let a configured command run, in this process.

        Keyed by the program rather than by the command name, because that is what the
        question is about: several `[commands]` entries handing different flags to one
        tool are one decision, and asking it seven times would teach the user to say yes
        without reading. Session-scoped on purpose -- a config file must not be able to
        approve its own subprocesses, and a trust store that outlives the process is
        upstream's to design (M4 §3.8), not something to invent here and have to unpick
        on the next rebase.
        """
        try:
            self.all_keymaps = load_keymap_plugins(
                user_defined_keymaps=user_defined_keymaps
            )
        except HarlequinConfigError as e:
            self.exit(return_code=2, message=pretty_error_message(e))

    @staticmethod
    def _resolve_active_profile_name(profile_name: str | None) -> str | None:
        """The profile this session is running under.

        `--profile` alone cannot say it: with no `--profile`, what got loaded
        is the config file's `default_profile`, and that is the name worth
        showing. The special name `None` asks for Harlequin's own defaults, so
        there is no profile to name.
        """
        if profile_name is not None:
            return None if profile_name == "None" else profile_name
        try:
            config = load_config(get_highest_priority_existing_config_file())
        except Exception:
            # a broken config is the CLI's error to report, not a reason for
            # the app to fail on its way up
            return None
        return config.default_profile

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        self.data_catalog = DataCatalog(
            show_files=self.show_files,
            show_s3=self.show_s3,
            catalog_exclude=self.catalog_exclude,
        )
        self.editor_collection = EditorCollection(
            language="sql", classes="hide-tabs"
        ).data_bind(Harlequin.theme)
        self.editor_collection.add_class("premount")
        self.editor: CodeEditor | None = None
        editor_placeholder = Lazy(widget=self.editor_collection)
        editor_placeholder.border_title = self.editor_collection.border_title
        editor_placeholder.loading = True
        self.results_viewer = ResultsViewer()
        self.run_query_bar = RunQueryBar(
            query_limit=self.query_limit,
            classes="non-responsive",
            show_cancel_button=self.adapter.IMPLEMENTS_CANCEL,
            profile_name=self.active_profile_name,
            profile_is_default=self.profile_name is None,
        )
        self.footer = Footer(show_command_palette=False)

        # lay out the widgets. The Data Catalog is a sibling of the main
        # panel, so which side it lands on is only the order they are yielded
        # in -- and `action_toggle_catalog_side` moves it after the fact.
        with Horizontal(id="panes"):
            if self.catalog_side == "left":
                yield self.data_catalog
            with Vertical(id="main_panel"):
                yield editor_placeholder
                yield self.run_query_bar
                yield self.results_viewer
            if self.catalog_side == "right":
                yield self.data_catalog
        yield self.footer

    # this is some kind of mypy bug; the types are literally copied from the
    # parent impl
    def push_screen(  # type: ignore[override]
        self,
        screen: Screen[ScreenResultType] | str,
        callback: ScreenResultCallbackType[ScreenResultType] | None = None,
        wait_for_dismiss: bool = False,
    ) -> AwaitMount | asyncio.Future[ScreenResultType]:
        # the editor keeps focus while a modal is up, so its cursor has to be
        # frozen explicitly.
        if self.editor is not None and self.editor._has_focus_within:
            self.editor.pause_blink(visible=True)

        ## TODO: PREVENT DUPLICATE SCREENS HERE.
        return super().push_screen(  # type: ignore[no-any-return,call-overload]
            screen,
            callback=callback,
            wait_for_dismiss=wait_for_dismiss,
        )

    def pop_screen(self) -> "AwaitComplete":
        new_screen = super().pop_screen()
        if (
            len(self.screen_stack) == 1
            and self.editor is not None
            and self.editor._has_focus_within
        ):
            self.editor.restart_blink()
        # a modal opened from the catalog drawer kept it open; if focus came back
        # somewhere else, the drawer has no blur left to close it
        self.call_after_refresh(self._close_overlay_if_unfocused)
        return new_screen

    def append_to_history(
        self, query_text: str, result_row_count: int, elapsed: float
    ) -> None:
        if self.history is None:
            self.history = History.blank()
        self.history.append(
            query_text=query_text, result_row_count=result_row_count, elapsed=elapsed
        )

    async def on_mount(self) -> None:
        self.run_query_bar.apply_configured_limit()
        self._check_narrow(self.size.width)
        self.watch_narrow(self.narrow)  # a resize before compose was skipped

        if self.ssh_tunnel is not None:
            # which database this session is actually looking at
            warnings = self.ssh_tunnel.warnings()
            self.notify(
                "\n\n".join((self.ssh_tunnel.notice(), *warnings)),
                title="SSH tunnel",
                severity=(
                    "warning" if self.ssh_tunnel.reused or warnings else "information"
                ),
                markup=False,
            )
            self.ssh_tunnel.watch(self._post_tunnel_closed)

        self._connect()
        self._load_catalog_cache()
        self.action_bind_keymaps(*self.keymap_names)

    @on(Button.Pressed, "#run_query")
    def submit_query_from_run_query_bar(self, message: Button.Pressed) -> None:
        message.stop()
        queries = self._get_selected_queries()
        if queries:
            self.post_message(
                QuerySubmitted(
                    queries=queries,
                    limit=self.run_query_bar.limit_value,
                )
            )

    @on(Button.Pressed, "#cancel_query")
    def cancel_query(self, message: Button.Pressed) -> None:
        message.stop()
        self.action_cancel_query()

    @on(Button.Pressed, "#transaction_button")
    def handle_transaction_button_press(self, message: Button.Pressed) -> None:
        message.stop()
        self.toggle_transaction_mode()
        if self.editor is not None:
            self.editor.focus()

    @on(Button.Pressed, "#commit_button")
    def handle_commit_button_press(self, message: Button.Pressed) -> None:
        message.stop()
        self.commit()
        if self.editor is not None:
            self.editor.focus()

    @on(Button.Pressed, "#rollback_button")
    def handle_rollback_button_press(self, message: Button.Pressed) -> None:
        message.stop()
        self.rollback()
        if self.editor is not None:
            self.editor.focus()

    @on(CatalogCacheLoaded)
    def build_trees(self, message: CatalogCacheLoaded) -> None:
        if self.connection_hash and (
            cached_db := message.cache.get_db(self.connection_hash)
        ):
            self.post_message(NewCatalog(catalog=cached_db))
        if self.show_s3 is not None:
            self.data_catalog.load_s3_tree_from_cache(message.cache)
        if self.connection_hash:
            history = message.cache.get_history(self.connection_hash)
            self.history = history if history is not None else History.blank()

    @on(CodeEditor.Submitted)
    def submit_query_from_editor(self, message: CodeEditor.Submitted) -> None:
        message.stop()
        queries = self._get_selected_queries()
        if queries:
            self.post_message(
                QuerySubmitted(
                    queries=queries,
                    limit=self.run_query_bar.limit_value,
                )
            )

    @on(DatabaseConnected)
    def initialize_app(self, message: DatabaseConnected) -> None:
        self.connection = message.connection
        self.post_message(
            TransactionModeChanged(new_mode=message.connection.transaction_mode)
        )
        self.run_query_bar.set_responsive()
        self.results_viewer.show_table(did_run=False)
        if message.connection.init_message:
            self.notify(message.connection.init_message, title="Database Connected.")
        else:
            self.notify("Database Connected.")
        self.update_schema_data()

    @on(HarlequinTree.NodeSubmitted)
    def insert_node_into_editor(self, message: HarlequinTree.NodeSubmitted) -> None:
        message.stop()
        if self.editor is None:
            # recycle message while editor loads
            callback = partial(self.post_message, message)
            self.set_timer(delay=0.1, callback=callback)
            return
        self.editor.insert_text_at_selection(text=message.insert_name)
        self.editor.focus()
        # narrow mode: the name is in, so the drawer's job is done. Focus moving
        # to the editor closes it anyway, a refresh later; say so outright.
        if self.narrow:
            self.catalog_overlay = False

    def _recycle_message(self, message: Message) -> None:
        """Re-post a message we can't handle yet, while we wait for the editor."""
        callback = partial(self.post_message, message)
        self.set_timer(delay=0.1, callback=callback)

    def _copy_to_clipboard(self, text: str, success_message: str) -> None:
        """Copy text to the editor's clipboard, and to the system's if enabled.

        The editor must be loaded; callers handle that by recycling their message.
        textual-textarea also emits OSC 52, so this works over ssh, and reports
        failures as TextAreaClipboardError, which CodeEditor turns into a notification.
        """
        assert self.editor is not None
        self.editor.copy_to_clipboard(text)
        self.notify(success_message)

    @on(HarlequinTree.NodeCopied)
    def copy_node_name(self, message: HarlequinTree.NodeCopied) -> None:
        message.stop()
        if self.editor is None or self.editor.text_input is None:
            self._recycle_message(message)
            return
        self._copy_to_clipboard(
            message.copy_name, "Selected label copied to clipboard."
        )

    @on(HarlequinDriver.InsertTextAtSelection)
    def driver_insert_text_into_editor(
        self, message: HarlequinDriver.InsertTextAtSelection
    ) -> None:
        message.stop()
        if self.editor is None:
            # recycle message while editor loads
            callback = partial(self.post_message, message)
            self.set_timer(delay=0.1, callback=callback)
            return
        self.editor.insert_text_at_selection(text=message.text)
        self.editor.focus()

    @on(HarlequinDriver.InsertTextInNewBuffer)
    async def driver_insert_text_in_new_buffer(
        self, message: HarlequinDriver.InsertTextInNewBuffer
    ) -> None:
        message.stop()
        if self.editor is None:
            # recycle message while editor loads
            callback = partial(self.post_message, message)
            self.set_timer(delay=0.1, callback=callback)
            return
        await self.editor_collection.insert_buffer_with_text(query_text=message.text)

    @on(HarlequinDriver.ConfirmAndExecute)
    def driver_confirm_and_execute(
        self, message: HarlequinDriver.ConfirmAndExecute
    ) -> None:
        message.stop()

        def screen_callback(dismiss_value: bool | None) -> None:
            if dismiss_value:
                self._execute_callback(callback=message.callback)

        self.push_screen(
            ConfirmModal(prompt=message.instructions), callback=screen_callback
        )

    @on(HarlequinDriver.Notify)
    def driver_notify(self, message: HarlequinDriver.Notify) -> None:
        message.stop()
        self.notify(message=message.notify_message, severity=message.severity)

    @on(HarlequinDriver.Refreshcatalog)
    def driver_refresh_catalog(self, message: HarlequinDriver.Refreshcatalog) -> None:
        message.stop()
        self.update_schema_data()

    @on(EditorCollection.EditorSwitched)
    def update_internal_editor_state(
        self, message: EditorCollection.EditorSwitched
    ) -> None:
        self.editor = message.active_editor or self.editor_collection.current_editor
        self.editor.focus()
        self._sync_run_button_disabled()
        self._sync_run_button_text()

    def on_text_area_changed(self) -> None:
        self._sync_run_button_disabled()

    def on_text_area_selection_changed(self) -> None:
        self._sync_run_button_text()

    @on(Input.Changed, "#limit_input")
    def update_limit_tooltip(self, message: Input.Changed) -> None:
        message.stop()
        if (
            message.input.value
            and message.validation_result
            and message.validation_result.is_valid
        ):
            message.input.tooltip = None
        elif message.validation_result:
            failures = "\n".join(message.validation_result.failure_descriptions)
            message.input.tooltip = f"Validation Error:\n{failures}"

    @on(Input.Submitted, "#limit_input")
    def submit_query_if_limit_valid(self, message: Input.Submitted) -> None:
        message.stop()
        if (
            message.input.value
            and message.validation_result
            and message.validation_result.is_valid
        ):
            queries = self._get_selected_queries()
            if queries:
                self.post_message(
                    QuerySubmitted(
                        queries=queries,
                        limit=self.run_query_bar.limit_value,
                    )
                )

    @on(DataTable.SelectionCopied)
    def copy_data_to_clipboard(self, message: DataTable.SelectionCopied) -> None:
        message.stop()
        if self.editor is None or self.editor.text_input is None:
            self._recycle_message(message)
            return
        # Excel, sheets, and Snowsight all use a TSV format for copying tabular data
        text = os.linesep.join("\t".join(map(str, row)) for row in message.values)
        self._copy_to_clipboard(text, "Selected data copied to clipboard.")

    @on(Worker.StateChanged)
    async def handle_worker_error(self, message: Worker.StateChanged) -> None:
        if message.state == WorkerState.ERROR:
            await self._handle_worker_error(message)

    async def _handle_worker_error(self, message: Worker.StateChanged) -> None:
        worker_name = message.worker.name
        worker_error = message.worker.error
        if self._exit or worker_error is None:
            # an error that lands while the app is exiting is noise, not news.
            return
        if worker_name == "update_schema_data":
            self._push_error_modal(
                title="Catalog Error",
                header="Could not update data catalog",
                error=worker_error,
            )
            self.data_catalog.database_tree.loading = False
        elif worker_name == "_connect":
            title = getattr(
                worker_error,
                "title",
                "Harlequin could not connect to your database.",
            )
            error = (
                worker_error
                if isinstance(worker_error, HarlequinError)
                else HarlequinConnectionError(msg=str(worker_error), title=title)
            )
            self.exit(return_code=2, message=pretty_error_message(error))
        elif worker_name in ("_execute_query", "_fetch_data"):
            # the worker died before posting QueriesExecuted or ResultsFetched,
            # which is what would have restored these.
            self.run_query_bar.set_responsive()
            self.results_viewer.show_table()
            header = getattr(worker_error, "title", worker_error.__class__.__name__)
            self._push_error_modal(
                title="Query Error",
                header=header,
                error=worker_error,
            )
        elif worker_name == "toggle_transaction_mode":
            self._push_error_modal(
                title="Transaction Error",
                header="Harlequin could not change the transaction mode.",
                error=worker_error,
            )
        elif worker_name in _PARTIAL_FAILURE_WORKER_NOTIFICATIONS:
            self.notify(
                _PARTIAL_FAILURE_WORKER_NOTIFICATIONS[worker_name],
                severity="warning",
            )
        else:
            # loud by default: a worker added later is an error modal until
            # someone decides its failures are benign.
            self._push_error_modal(
                title="Unexpected Error",
                header="A background task failed. Harlequin is still running.",
                error=worker_error,
            )

    @on(HarlequinTree.CatalogError)
    def handle_catalog_error(self, message: HarlequinTree.CatalogError) -> None:
        self._push_error_modal(
            title=f"Catalog Error: {message.catalog_type}",
            header=f"Could not populate the {message.catalog_type} data catalog",
            error=message.error,
        )

    @on(QueryError)
    def handle_query_error(self, message: QueryError) -> None:
        self.append_to_history(
            query_text=message.query_text, result_row_count=-1, elapsed=0.0
        )
        self.run_query_bar.set_responsive()
        self.results_viewer.show_table()
        header = getattr(message.error, "title", message.error.__class__.__name__)
        self._push_error_modal(
            title="Query Error",
            header=header,
            error=message.error,
        )

    @on(DataTable.DataLoadError)
    def handle_data_load_error(self, message: DataTable.DataLoadError) -> None:
        header = getattr(message.error, "title", message.error.__class__.__name__)
        self._push_error_modal(
            title="Query Error",
            header=header,
            error=message.error,
        )

    @on(ContextMenu.ExecuteInteraction)
    def execute_interaction_in_thread(
        self, message: ContextMenu.ExecuteInteraction
    ) -> None:
        self._execute_interaction(
            interaction=message.interaction,
            item=message.item,
            driver=self.harlequin_driver,
        )

    @on(NewCatalog)
    def handle_new_catalog(self, message: NewCatalog) -> None:
        self.data_catalog.update_database_tree(message.catalog)
        self.update_completers(message.catalog)

    @on(CatalogRefreshAborted)
    def stop_catalog_loading(self) -> None:
        self.data_catalog.database_tree.loading = False

    @on(NewCatalogItems)
    def handle_new_catalog_item(self, message: NewCatalogItems) -> None:
        if (
            self.editor_collection.word_completer is not None
            and self.editor_collection.member_completer is not None
        ):
            self.extend_completers(parent=message.parent, items=message.items)
        else:
            # recycle message while completers are built
            callback = partial(self.post_message, message)
            self.set_timer(delay=0.5, callback=callback)

    @on(CodeEditor.SymbolsFound)
    def load_catalog_items_named_by_buffer(
        self, message: CodeEditor.SymbolsFound
    ) -> None:
        self.data_catalog.database_tree.load_items_named(message.symbols.names)

    @on(QueriesExecuted)
    def fetch_data_or_reset_table(self, message: QueriesExecuted) -> None:
        if message.cursors:  # select query
            self._fetch_data(message.cursors, message.submitted_at, message.limit)
        else:
            self.run_query_bar.set_responsive()
            self.results_viewer.show_table(did_run=message.query_count > 0)
        if message.ddl_queries:
            n = len(message.ddl_queries)
            # at least one DDL statement
            elapsed = time.monotonic() - message.submitted_at
            for query_text in message.ddl_queries:
                self.append_to_history(
                    query_text=query_text, result_row_count=0, elapsed=elapsed
                )
            self.notify(
                f"{n} DDL/DML {'query' if n == 1 else 'queries'} "
                f"executed successfully in {elapsed:.2f} seconds."
            )
            self.update_schema_data()

    @on(QueriesCanceled)
    def reset_after_cancel(self) -> None:
        self.run_query_bar.set_responsive()
        self.results_viewer.show_table(did_run=False)
        self.notify("Queries canceled.", severity="error")

    @on(ResultsFetched)
    async def load_tables(self, message: ResultsFetched) -> None:
        for id_, result in message.results.items():
            await self.results_viewer.push_table(
                table_id=id_, result=result, elapsed=message.elapsed
            )
            self.append_to_history(
                query_text=result.statement.sql,
                # the rows the database returned, not the rows the viewer kept
                result_row_count=result.fetched_row_count,
                elapsed=message.elapsed,
            )
        if message.errors:
            for _, query_text in message.errors:
                self.append_to_history(
                    query_text=query_text, result_row_count=-1, elapsed=0.0
                )
            header = getattr(
                message.errors[0][0],
                "title",
                "The database raised an error when running your query:",
            )
            self._push_error_modal(
                title="Query Error",
                header=header,
                error=message.errors[0][0],
            )
        else:
            self.notify(
                f"{len(message.cursors)} "
                f"{'query' if len(message.cursors) == 1 else 'queries'} "
                f"executed successfully in {message.elapsed:.2f} seconds."
            )
        self.run_query_bar.set_responsive()
        if len(message.errors) == len(message.cursors):
            self.results_viewer.show_table(did_run=False)
        else:
            self.results_viewer.show_table(did_run=True)
            if message.results:
                self.results_viewer.focus()

    @on(WidgetMounted)
    def bind_keys(self, message: WidgetMounted) -> None:
        """
        When widgets are first mounted, they will have their default bindings.
        Here we add the bindings from the keymap.
        """
        for keymap_name in self.keymap_names:
            keymap = self._get_keymap(keymap_name=keymap_name)
            if keymap is None:
                continue
            for binding in keymap.bindings:
                action = self._action_for(binding.action, keymap_name)
                if action is None:
                    continue
                if action.target is not None and isinstance(
                    message.widget, action.target
                ):
                    try:
                        bind(
                            target=message.widget,
                            keys=binding.keys,
                            action=action.action,
                            description=action.description,
                            show=_footer_slot(binding, action),
                            key_display=binding.key_display,
                            priority=action.priority,
                        )
                    except HarlequinBindingError as e:
                        pretty_print_error(e)
                        self.exit(return_code=2)

    def watch_full_screen(self, full_screen: bool) -> None:
        full_screen_widgets = [self.editor_collection, self.results_viewer]
        other_widgets = [self.run_query_bar, self.footer]
        all_widgets = [*full_screen_widgets, *other_widgets]
        if full_screen:
            target: Optional[DOMNode] = self.focused
            while target not in full_screen_widgets:
                if (
                    target is None
                    or target in other_widgets
                    or not isinstance(target, Widget)
                ):
                    # nothing to full-screen (focus is in the catalog, say)
                    self.full_screen = False
                    return
                else:
                    target = target.parent
            for w in all_widgets:
                w.disabled = w != target
            if target == self.editor_collection:
                self.run_query_bar.disabled = False
            self.catalog_overlay = False
            self.data_catalog.disabled = True
        else:
            for w in all_widgets:
                w.disabled = False
            self._apply_catalog_visibility()

    @on(QuerySubmitted)
    def execute_query(self, message: QuerySubmitted) -> None:
        if self.connection is None:
            return
        if message.queries:
            self.full_screen = False
            self.run_query_bar.set_not_responsive()
            self.results_viewer.show_loading()
            self._execute_query(message)

    def watch_sidebar_hidden(self, sidebar_hidden: bool) -> None:
        if sidebar_hidden:
            if self.data_catalog.has_focus and self.editor is not None:
                self.editor.focus()
        if not self.narrow:
            self.data_catalog.disabled = sidebar_hidden

    # -- narrow mode -------------------------------------------------------
    # A half-screen tmux pane is ~94 columns; a catalog column there leaves the
    # editor too narrow for real SQL. Under `catalog_min_width` the catalog
    # starts hidden and f9 / ctrl+b / the Run Query Bar's Catalog button open it
    # as a drawer over the right of the main panel, dismissed by escape, f9
    # again, or focus leaving it (a click in the editor, or inserting a name).
    # `sidebar_hidden` is left alone in narrow mode, so widening the terminal
    # again restores whatever column state it had.

    def on_resize(self, event: events.Resize) -> None:
        self._check_narrow(event.size.width)
        if self.narrow and self.catalog_overlay:
            # the editor's height has changed under an open drawer
            self.call_after_refresh(self._size_drawer)

    def _check_narrow(self, width: int) -> None:
        self.narrow = 0 < self.catalog_min_width and width < self.catalog_min_width

    def watch_narrow(self, narrow: bool) -> None:
        if not hasattr(self, "run_query_bar"):
            return  # before compose; on_mount re-applies
        self.catalog_overlay = False
        self.data_catalog.set_class(narrow, "overlay")
        self.data_catalog.set_class(self.catalog_side == "left", "dock-left")
        self.run_query_bar.set_narrow(narrow)
        self.run_query_bar.set_catalog_side(self.catalog_side)
        self._size_drawer()
        self._apply_catalog_visibility()

    def _size_drawer(self) -> None:
        """Stop the drawer above the Run Query Bar.

        The drawer is a child of `#panes`, so a full-height overlay covers the
        bar and the results as well as the editor, and `Run` and `Limit` go with
        them. The editor starts at the top of the pane like the drawer does, so
        the editor's height is exactly the room above the bar. It changes when
        the results split moves, so this is called on every open, not once.
        """
        if not hasattr(self, "data_catalog"):
            return
        # region, not size: the editor's outer box, borders included, is the
        # box the drawer has to match
        height = self.editor_collection.region.height if self.narrow else 0
        if height > 0:
            self.data_catalog.styles.height = height
        else:
            # not narrow, or the editor has not been laid out yet: let the CSS
            # say what the height is (a full-height column, or a full drawer)
            self.data_catalog.styles.clear_rule("height")
        self.data_catalog.refresh(layout=True)

    def watch_catalog_overlay(self, showing: bool) -> None:
        if not hasattr(self, "run_query_bar") or not self.narrow:
            return
        # disabling the catalog blurs it, so note where focus was first
        had_focus = self.data_catalog.has_focus or self.data_catalog.has_focus_within
        self.data_catalog.disabled = not showing
        if showing:
            self._size_drawer()
            self.data_catalog.focus()
        elif had_focus and self.editor is not None:
            self.editor.focus()

    def _apply_catalog_visibility(self) -> None:
        """Set the catalog's `disabled` from the mode and the state that mode keeps."""
        if self.full_screen:
            return
        if self.narrow:
            self.data_catalog.disabled = not self.catalog_overlay
        else:
            self.data_catalog.disabled = self.sidebar_hidden

    @on(events.DescendantBlur)
    def _close_overlay_when_left(self, event: events.DescendantBlur) -> None:
        if self.narrow and self.catalog_overlay:
            # settle first: focus may be moving within the catalog (tree to filter)
            self.call_after_refresh(self._close_overlay_if_unfocused)

    def _close_overlay_if_unfocused(self) -> None:
        if (
            self.narrow
            and self.catalog_overlay
            and self.app_focus  # another tmux pane took focus: keep the drawer
            and len(self.screen_stack) == 1
            and not self.data_catalog.has_focus_within
            and not self.data_catalog.has_focus
            # the Catalog button is about to toggle; it is the only writer then
            and self.focused is not self.run_query_bar.catalog_button
        ):
            self.catalog_overlay = False

    @on(DataCatalog.Dismiss)
    def _close_overlay_on_escape(self, message: DataCatalog.Dismiss) -> None:
        message.stop()
        if self.narrow and self.catalog_overlay:
            self.catalog_overlay = False

    @on(Button.Pressed, "#catalog_button")
    def _toggle_catalog_from_button(self, message: Button.Pressed) -> None:
        message.stop()
        self.action_toggle_sidebar()

    def _post_tunnel_closed(self, notice: str) -> None:
        """Called on the tunnel's watcher thread, so it only posts a message.

        A child that dies during shutdown reaches a loop that is already
        closing, which raises rather than delivering.
        """
        with contextlib.suppress(RuntimeError):
            self.post_message(TunnelClosed(notice))

    @on(TunnelClosed)
    def notify_tunnel_closed(self, message: TunnelClosed) -> None:
        message.stop()
        # ssh quotes a server's own disconnect message and a helper's output,
        # so the notice is text rather than markup
        self.notify(message.notice, title="SSH tunnel", severity="error", markup=False)

    @on(TunnelReconnected)
    def report_tunnel_reconnected(self, message: TunnelReconnected) -> None:
        message.stop()
        replaced, self.connection = self.connection, message.connection
        if replaced is not None and replaced is not message.connection:
            # its socket died with the forward, but an adapter does real work
            # here -- thread pools, temp files -- and may raise on the way out
            with contextlib.suppress(Exception):
                replaced.close()
        self.post_message(
            TransactionModeChanged(new_mode=message.connection.transaction_mode)
        )
        if message.rebuild_catalog:
            # the tree's items load their children on the connection the adapter
            # captured when it built them, so expanding an unloaded node would
            # reach the socket that died with the old forward
            self.data_catalog.database_tree.loading = True
            self.update_schema_data()
        self.notify(
            "The tunnel dropped and has been reopened, so this is a new "
            "session: an open transaction, a temp table, and anything set with "
            "SET went with the old one.",
            title="SSH tunnel",
            severity="warning",
        )

    @on(TunnelUnrecoverable)
    def report_tunnel_unrecoverable(self, message: TunnelUnrecoverable) -> None:
        message.stop()
        self._push_error_modal(
            title="SSH Tunnel Error",
            header="Harlequin could not reopen the SSH tunnel.",
            error=message.error,
        )

    @on(TransactionModeChanged)
    def update_transaction_button_label(self, message: TransactionModeChanged) -> None:
        message.stop()
        if message.new_mode is not None:
            self.run_query_bar.transaction_button.remove_class("hidden")
            self.run_query_bar.transaction_button.label = (
                f"Tx: {message.new_mode.label}"
            )
            if message.new_mode.commit is not None:
                self.run_query_bar.commit_button.remove_class("hidden")
            else:
                self.run_query_bar.commit_button.add_class("hidden")
            if message.new_mode.rollback is not None:
                self.run_query_bar.rollback_button.remove_class("hidden")
            else:
                self.run_query_bar.rollback_button.add_class("hidden")
        else:
            self.run_query_bar.transaction_button.add_class("hidden")
            self.run_query_bar.commit_button.add_class("hidden")
            self.run_query_bar.rollback_button.add_class("hidden")

    @on(CompletersReady)
    def update_editor_completers(self, message: CompletersReady) -> None:
        self.editor_collection.word_completer = message.word_completer
        self.editor_collection.member_completer = message.member_completer

    def action_noop(self) -> None:
        """
        A no-op action to unmap keys.
        """
        return

    def action_bind_keymaps(self, *keymap_names: str) -> None:
        """
        Binds the action/key pairs in the keymaps to the currently-mounted
        widgets in Harlequin.
        """
        required_bindings = {"quit": "ctrl+q"}
        self.keymap_names = keymap_names
        for keymap_name in keymap_names:
            keymap = self._get_keymap(keymap_name=keymap_name)
            if keymap is None:
                continue
            for binding in keymap.bindings:
                required_bindings.pop(binding.action, None)
                action = self._action_for(binding.action, keymap_name)
                if action is None:
                    continue
                if action.target is not None:
                    targets: DOMQuery[Widget] | list[App] = self.query(action.target)
                    if not targets:
                        # some widgets are not yet mounted... we'll get them
                        # by listening for their mount event
                        continue
                else:
                    targets = [self]
                for target in targets:
                    try:
                        bind(
                            target=target,
                            keys=binding.keys,
                            action=action.action,
                            description=action.description,
                            show=_footer_slot(binding, action),
                            key_display=binding.key_display,
                            priority=action.priority,
                        )
                    except HarlequinBindingError as e:
                        pretty_print_error(e)
                        self.exit(return_code=2)
        for action_name, key in required_bindings.items():
            action = self.actions[action_name]
            try:
                bind(
                    target=self,
                    keys=key,
                    action=action.action,
                    description=action.description,
                    show=action.show,
                    key_display=None,
                    priority=action.priority,
                )
            except HarlequinBindingError as e:
                pretty_print_error(e)
                self.exit(return_code=2)

    async def action_run_query(self) -> None:
        if self.editor is None:
            return
        await self.editor.action_submit()

    def action_cancel_query(self) -> None:
        self._cancel_query()

    def action_export(self) -> None:
        show_export_error = partial(
            self._push_error_modal,
            "Export Data Error",
            "Could not export data.",
        )
        table = self.results_viewer.get_visible_table()
        if table is None:
            show_export_error(error=ValueError("You must execute a query first."))
            return

        def on_export_success() -> None:
            self.notify("Data exported successfully.")
            self.data_catalog.update_file_tree()

        callback = partial(
            export_callback,
            table=table,
            success_callback=on_export_success,
            error_callback=show_export_error,
        )
        self.app.push_screen(
            ExportScreen(
                formats=(
                    WINDOWS_COPY_FORMATS
                    if sys.platform == "win32"
                    else HARLEQUIN_COPY_FORMATS
                ),
                default_path=self.export_path,
                id="export_screen",
            ),
            callback,
        )

    def action_show_query_history(self) -> None:
        async def history_callback(screen_data: str | None) -> None:
            """
            Insert the selected query into a new buffer.
            """
            if screen_data is None:
                return
            await self.editor_collection.insert_buffer_with_text(query_text=screen_data)

        if self.history is None:
            # This should only happen immediately after start-up, before the cache is
            # loaded from disk.
            self._push_error_modal(
                title="History Not Yet Loaded",
                header="Harlequin could not load the Query History.",
                error=ValueError(
                    "Your Query History has not yet been loaded. "
                    "Please wait a moment and try again."
                ),
            )
        elif self.screen.id != "history_screen":
            self.push_screen(
                HistoryScreen(
                    history=self.history,
                    theme=self.theme,
                    id="history_screen",
                ),
                history_callback,
            )

    def action_focus_data_catalog(self) -> None:
        if self.sidebar_hidden or self.data_catalog.disabled:
            self.action_toggle_sidebar()
        self.data_catalog.focus()

    def action_focus_query_editor(self) -> None:
        if self.editor is not None:
            self.editor.focus()

    def action_focus_results_viewer(self) -> None:
        self.results_viewer.focus()

    async def action_quit(self) -> None:
        write_editor_cache(
            Cache(
                focus_index=self.editor_collection.active_buffer_index,
                buffers=self.editor_collection.buffers,
            )
        )
        update_catalog_cache(
            connection_hash=self.connection_hash,
            catalog=None,  # TODO: cache completions instead.
            s3_tree=self.data_catalog.s3_tree,
            history=self.history,
        )
        if self.connection:
            self.connection.close()
        await super().action_quit()

    def action_show_help_screen(self) -> None:
        self.push_screen(HelpScreen(id="help_screen"))

    def action_show_debug_info(self) -> None:
        SCREEN_ID = "debug_info_screen"
        if self.screen.id == SCREEN_ID:
            # already showing this screen.
            return

        config_path = get_highest_priority_existing_config_file()
        config = load_config(config_path)
        profile_name = self.profile_name
        active_profile_config, _ = load_profile_and_keymaps(config_path, profile_name)
        active_profile_name = profile_name or config.default_profile
        adapter_options = getattr(self.adapter, "ADAPTER_OPTIONS", None)
        adapter_type = type(self.adapter).__name__

        harlequin_info = HarlequinDebugInfo(
            active_profile_config=active_profile_config,
            active_profile_name=active_profile_name,
            adapter_options=adapter_options,
            all_keymaps=list(self.all_keymaps.keys()),
            config=config,
            config_path=config_path,
            keymap_names=self.keymap_names,
            theme=self.theme,
            ssh_tunnel=(
                self.ssh_tunnel.describe() if self.ssh_tunnel is not None else None
            ),
        )
        adapter_info = AdapterDebugInfo(
            adapter_options=adapter_options,
            adapter_type=adapter_type,
            adapter_details=self.adapter.ADAPTER_DETAILS
            if self.adapter.provides_details
            else "No details were provided by adapter.",
            adapter_driver_details=self.adapter.ADAPTER_DRIVER_DETAILS
            if self.adapter.provides_driver_details
            else "No details were provided by the database driver.",
        )
        self.push_screen(
            DebugInfoScreen(
                harlequin_details=harlequin_info.parse_info(),
                adapter_details=adapter_info.parse_info(),
                id=SCREEN_ID,
            )
        )

    @on(ResultsViewer.ColumnsChanged)
    def update_catalog_columns(self, message: ResultsViewer.ColumnsChanged) -> None:
        """Keep the Data Catalog's Columns tab on the visible result."""
        message.stop()
        self.data_catalog.update_columns(
            columns=message.columns, current=message.current
        )

    @on(ColumnList.ColumnSelected)
    def jump_to_column(self, message: ColumnList.ColumnSelected) -> None:
        """A pick in the Columns tab puts the grid's cursor on that column."""
        message.stop()
        table = self.results_viewer.get_visible_table()
        if table is None:
            return
        table.move_cursor(column=message.column)
        table.focus()

    def action_toggle_full_screen(self) -> None:
        self.full_screen = not self.full_screen

    def action_toggle_sidebar(self) -> None:
        """
        sidebar_hidden and self.sidebar.disabled both hold important state.
        The sidebar can be hidden with either ctrl+b or f10, and we need
        to persist the state depending on how that happens
        """
        if self.narrow:
            self.catalog_overlay = not self.catalog_overlay
        elif self.sidebar_hidden is False and self.data_catalog.disabled is True:
            # sidebar was hidden by f10; toggle should show it
            self.data_catalog.disabled = False
        else:
            self.sidebar_hidden = not self.sidebar_hidden

    def action_toggle_catalog_side(self) -> None:
        """Move the Data Catalog to the other side of the main panel."""
        panes = self.query_one("#panes", Horizontal)
        main_panel = self.query_one("#main_panel", Vertical)
        if self.catalog_side == "left":
            panes.move_child(self.data_catalog, after=main_panel)
            self.catalog_side = "right"
        else:
            panes.move_child(self.data_catalog, before=main_panel)
            self.catalog_side = "left"
        # the narrow-mode drawer docks by class, not by DOM order, and its
        # button on the run bar sits on the edge the drawer comes out of
        self.data_catalog.set_class(self.catalog_side == "left", "dock-left")
        self.run_query_bar.set_catalog_side(self.catalog_side)
        self.notify(
            f"Data Catalog moved to the {self.catalog_side}. "
            f'Set `catalog_side = "{self.catalog_side}"` in your config to keep it.'
        )

    def action_refresh_catalog(self) -> None:
        self.data_catalog.database_tree.loading = True
        self.update_schema_data()
        self.data_catalog.update_file_tree()
        self.data_catalog.update_s3_tree()

    def _sync_run_button_text(self) -> None:
        self.run_query_bar.set_runs_selection(bool(self._validate_selection()))

    def _sync_run_button_disabled(self) -> None:
        if self.editor is None or self.editor.text_input is None:
            return

        if self.editor.text.strip():
            self.run_query_bar.run_button.disabled = False
        else:
            self.run_query_bar.run_button.disabled = True

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=False,
        group="connect",
        description="Connecting to DB",
    )
    def _connect(self) -> None:
        connection = self.adapter.connect()
        self.post_message(DatabaseConnected(connection=connection))

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=False,
        group="cache_loaders",
        description="Loading cached catalog",
    )
    def _load_catalog_cache(self) -> None:
        cache = get_catalog_cache()
        if cache is not None:
            self.post_message(CatalogCacheLoaded(cache=cache))

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=False,
        group="query_runners",
        description="Executing queries.",
    )
    def _execute_query(self, message: QuerySubmitted) -> None:
        # ahead of reading the connection: a tunnel that dropped took it too
        connection = self._connection_for_worker()
        if connection is None:
            return
        cursors: Dict[str, ExecutedStatement] = {}
        ddl_queries: list[str] = []
        statements = [Statement(sql=q, index=i) for i, q in enumerate(message.queries)]
        # the Run Query Bar's limit is a hard fetch limit, so the true total
        # stops being knowable and one extra row is what tells the Results
        # Viewer to say `500 of >500` instead of claiming the 500 was all of it.
        # `viewer_max_rows` is a soft cap over that fetch and is applied in
        # _fetch_data, which is why it needs no overflow detection of its own.
        limit = RowLimit(
            max_rows=message.limit, detect_overflow=message.limit is not None
        )
        for executed in execute(
            connection=connection,
            statements=statements,
            limit=limit,
        ):
            if executed.error is not None:
                self.post_message(
                    QueryError(query_text=executed.statement.sql, error=executed.error)
                )
            elif executed.cursor is not None:
                cursors[f"t{hash(executed.cursor)}"] = executed
            else:
                ddl_queries.append(executed.statement.sql)
        self.post_message(
            QueriesExecuted(
                query_count=len(cursors) + len(ddl_queries),
                cursors=cursors,
                submitted_at=message.submitted_at,
                ddl_queries=ddl_queries,
                limit=limit,
            )
        )

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=False,
        group="query_cancellers",
        description="Cancelling queries.",
    )
    def _cancel_query(self) -> None:
        if self.connection is None or not self.adapter.IMPLEMENTS_CANCEL:
            return
        try:
            self.connection.cancel()
        except Exception as e:
            self.call_from_thread(
                self._push_error_modal,
                title="Cancel Error",
                header="Harlequin could not cancel your queries.",
                error=e,
            )
        self.post_message(QueriesCanceled())

    def _connection_for_worker(
        self, rebuilds_catalog: bool = False
    ) -> HarlequinConnection | None:
        """The connection to run on, reopening a dropped tunnel first.

        Called on a worker's thread, ahead of the database work it was about to
        do: restarting `ssh` is not enough on its own, because the adapter's TCP
        connection ran *through* the old forward. One attempt, and none at all
        once one has failed -- the user retries by running their query again.

        None is a worker that must not run: a recovery that failed leaves a
        connection whose socket died with the forward, and running on it would
        raise a second modal behind the one that said why.

        `rebuilds_catalog` is the caller about to build a tree on the connection
        it gets back, so a recovery here does not ask for a second one -- two
        `get_catalog()` calls at once are two threads inside one adapter. It
        reports the caller's own intent, which the worker that loses the lock
        below cannot: a query and a refresh that hit the same drop rebuild twice.
        """
        tunnel = self.ssh_tunnel
        if tunnel is None or self.connection is None:
            return self.connection
        with self._recovery_lock:
            # `_execute_query` and `update_schema_data` are exclusive within
            # their own worker groups and not against each other, so both can
            # arrive here at once. The second finds the tunnel already back.
            if not tunnel.needs_restart:
                if tunnel.dropped:
                    # a restart already failed and is not tried again, so what
                    # is left is a connection whose socket went with the forward
                    return None
                return self._recovered_connection or self.connection
            try:
                tunnel.restart()
                connection = self.adapter.connect()
            except BaseException as e:  # adapters are third-party code
                self.post_message(TunnelUnrecoverable(e))
                return None
            # what a worker still inside this method runs on; the handler is
            # what makes it the app's, once the message lands
            self._recovered_connection = connection
        self.post_message(
            TunnelReconnected(
                connection=connection, rebuild_catalog=not rebuilds_catalog
            )
        )
        return connection

    def _get_selected_queries(self) -> list[str]:
        if self.editor is None:
            return []
        return self.editor.selected_queries()

    # ------------------------------------------------------------------------
    # Commands from config: running someone else's program with the editor or
    # the results as context. See harlequin/commands.py for the process side.
    # ------------------------------------------------------------------------

    def _action_for(self, action_name: str, keymap_name: str) -> Action | None:
        """The action a binding names, or None with one notification saying so.

        A keymap can name an action that does not exist -- a typo, a plug-in that is not
        installed, or a `command.x` whose `[commands.x]` table is in a config file this
        run ignored. Before commands came from config that was a `KeyError` during
        mount,
        which is to say a crash on start-up with a traceback; it is a feature's ordinary
        failure mode now, so it costs that binding and nothing else.
        """
        action = self.actions.get(action_name)
        if action is None:
            self.notify(
                f"Keymap {keymap_name!r} binds unknown action {action_name!r}. That "
                "binding is off.",
                severity="warning",
                timeout=10,
            )
        return action

    def _command_keys(self) -> dict[str, str]:
        """Which key each configured command is on, for the menu to show.

        Read off the keymaps in force rather than remembered when they are bound: the
        menu is built when it is opened, and this is the only description of the keys
        that cannot fall out of step with them.
        """
        keys: dict[str, str] = {}
        for keymap_name in self.keymap_names:
            keymap = self._get_keymap(keymap_name=keymap_name)
            if keymap is None:
                continue
            for binding in keymap.bindings:
                if not binding.action.startswith("command."):
                    continue
                name = binding.action[len("command.") :]
                keys.setdefault(name, binding.key_display or binding.keys)
        return keys

    def action_show_command_menu(self) -> None:
        """The command menu: every `[commands.x]` a config file defined.

        The answer to a keyspace with nothing left in it -- a command needs no key of
        its own to be reachable, and this one key reaches all of them.
        """
        if not self.commands:
            self.notify(
                "No commands configured. Add a [commands.<name>] table to your config "
                "file.",
                severity="warning",
                timeout=10,
            )
            return

        def picked(name: str | None) -> None:
            # the action, not `run_action`: the menu already knows which command was
            # picked, and going back through the action string would only give the name
            # a chance to be quoted wrongly
            if name is not None:
                self.action_run_command(name)

        self.push_screen(
            CommandMenu(commands=self.commands, keys=self._command_keys()), picked
        )

    def action_run_command(self, name: str) -> None:
        """Run the configured command called `name`.

        Everything the command is given is gathered here, on the main thread, before
        the worker starts: an Arrow table is immutable and would be safe to read from a
        thread, but the rule that nothing off the main thread touches a widget is worth
        keeping literally, so the worker is handed argv, an environment, bytes and a
        directory path and knows nothing else.

        The empty cases end here too, with a notification rather than a run: a command
        that was asked to send the selection when nothing is selected has nothing to do,
        and running it with empty stdin would make that the tool's problem to report.
        """
        command = self.commands.get(name)
        if command is None:
            self.notify(f"No command named {name!r} is configured.", severity="error")
            return
        try:
            invocation = self._gather_invocation(name, command)
        except HarlequinError as e:
            self.notify(e.msg, severity="warning", timeout=8)
            return
        if invocation is None:
            return
        program = invocation.argv[0]
        if program in self._approved_programs:
            self._run_external_command(invocation)
            return

        def confirmed(approved: bool | None) -> None:
            if not approved:
                return
            self._approved_programs.add(program)
            self._run_external_command(invocation)

        # The consent gate. A config file is data, and this is the one place where
        # data becomes a program Harlequin executes, so a human says yes before it
        # runs. There is deliberately no config key to skip it: a file cannot be
        # allowed to approve itself.
        #
        # Keyed by the **program**, not by the command's name. What is being consented
        # to is "Harlequin may execute this program", and a second table that runs the
        # same program with a different flag is not a second decision -- it is the same
        # one, asked again in a way that teaches the user to say yes without reading.
        # Seven entries calling one tool are one Yes; a config that names a program you
        # have not approved still stops and asks, which is the property that matters.
        others = sorted(
            other
            for other, config in self.commands.items()
            if other != name and config.argv()[:1] == [program]
        )
        also = (
            f"\n\nThis also covers {len(others)} other command"
            f"{'' if len(others) == 1 else 's'} that run {program} "
            f"({', '.join(others)}) for the rest of this Harlequin session."
            if others
            else ""
        )
        self.push_screen(
            ConfirmModal(
                prompt=(
                    f"Run [b]{' '.join(invocation.argv)}[/b]?\n\n"
                    f"{command.description or name} is configured in a config file. "
                    f"Harlequin will run it with your environment.{also}"
                )
            ),
            confirmed,
        )

    @on(TextArea.SelectionChanged)
    def note_selection_time(self, message: TextArea.SelectionChanged) -> None:
        """Remember when the user last highlighted something.

        Only a real selection counts. Moving the cursor changes the selection too, and
        an empty one is not the user saying "this bit".
        """
        selection = message.selection
        if selection.start != selection.end:
            self._selection_made_at = time.monotonic()

    def _freshest_first(self, sources: list[str]) -> list[str]:
        """Put the result ahead of the selection when the result is the newer of the
        two.

        A chain is a statement of preference, and preference is not the whole story: a
        selection that predates the result on screen is stale, and a stale source
        should yield to a fresher one further down the chain.

        This exists because of `run_section`, which runs a section *by selecting it*.
        Without it, running a section and then sending would hand over the SQL that had
        just run rather than the rows it produced -- the one thing the user cannot have
        meant, since the rows are what they are looking at. Selecting something after a
        query has run still wins, which is the case the ordering is there for.
        """
        if "selection" not in sources:
            return sources
        results = next((s for s in sources if s in ("results", "pinned_results")), None)
        if results is None or sources.index(results) < sources.index("selection"):
            return sources
        viewer = getattr(self, "results_viewer", None)
        arrived = viewer.newest_arrival() if viewer is not None else None
        if arrived is None:
            return sources
        selected = self._selection_made_at
        if selected is not None and selected >= arrived:
            return sources
        reordered = list(sources)
        reordered.remove(results)
        reordered.insert(reordered.index("selection"), results)
        return reordered

    def _gather_invocation(
        self, name: str, command: CommandConfig
    ) -> CommandInvocation | None:
        """Everything the worker needs, or None when there is nothing to send."""
        import tempfile

        editor = self.editor_collection
        stdin_bytes = b""
        tmpdir: str | None = None
        files: list[str] = []

        # A command may name further sources for when the first has nothing, tried in
        # order: one key can mean "a selection if there is one, else the result on
        # screen, else the statement under the cursor". The earlier attempts stay
        # quiet, so a command that recovers does not warn about what it recovered from;
        # only the last one tried says why it failed.
        fallbacks = command.fallback_stdin or []
        if isinstance(fallbacks, str):
            fallbacks = [fallbacks]
        sources = [command.stdin]
        for extra in fallbacks:
            if extra not in sources:
                sources.append(extra)
        sources = self._freshest_first(sources)
        source = sources[-1]
        for attempt, candidate in enumerate(sources):
            last = attempt == len(sources) - 1
            if candidate == "none":
                source = candidate
                break
            if candidate in ("results", "pinned_results"):
                snapshots = self._result_snapshots(candidate)
                if not snapshots:
                    if last:
                        self.notify(
                            "Run a query first."
                            if candidate == "results"
                            else "No results are pinned.",
                            severity="warning",
                        )
                        return None
                    continue
                tmpdir = tempfile.mkdtemp(prefix="harlequin-cmd-")
                stdin_text, files = results_manifest(
                    snapshots,
                    stdin_source=candidate,
                    tmpdir=Path(tmpdir),
                    max_rows=command.max_rows,
                )
                stdin_bytes = stdin_text.encode("utf-8")
                source = candidate
                break
            text = self._editor_context(candidate, quiet=not last)
            if text is None:
                if last:
                    return None
                continue
            stdin_bytes = text.encode("utf-8")
            source = candidate
            break

        argv = command.argv()
        if not argv:
            self.notify(
                f"Command {name!r} names no program to run.", severity="error"
            )
            return None
        return CommandInvocation(
            name=name,
            argv=argv,
            env=build_env(
                name=name,
                stdin_source=source,
                version=_harlequin_version(),
                profile=self.active_profile_name,
                adapter=self.adapter_name,
                buffer_name=editor.active_buffer_name(),
                buffer_path=(
                    str(path) if (path := editor.active_buffer_path()) else None
                ),
            ),
            stdin=stdin_bytes,
            timeout=command.timeout,
            tmpdir=tmpdir,
            files=files,
        )

    def _editor_context(self, source: str, quiet: bool = False) -> str | None:
        """The text a `stdin` source asks for, or None with a notification.

        `statement` is what Run would run, `section` is what Run Section would run, and
        both come from the same code those actions use -- a second definition of "the
        statement under the cursor" would sooner or later disagree with the one that
        executes.

        `quiet` is for a source tried before a fallback: nothing is wrong yet, so
        nothing is said yet.
        """

        def warn(message: str) -> None:
            if not quiet:
                self.notify(message, severity="warning")

        editor = self.editor_collection.current_editor
        if source == "selection":
            text = editor.selected_text
            if not text.strip():
                warn("Nothing is selected.")
                return None
            return text
        if source == "buffer":
            if not editor.text.strip():
                warn("The buffer is empty.")
                return None
            return editor.text
        if source == "statement":
            queries = editor.selected_queries()
            if not queries:
                warn("The buffer is empty.")
                return None
            return ";\n".join(query.rstrip().rstrip(";") for query in queries) + ";\n"
        if source == "section":
            section = self.editor_collection.section_under_cursor()
            if section is None:
                warn(
                    "The cursor is not in a section. Start a line with `-- ## Name`."
                )
                return None
            text, _name = section
            if not text.strip():
                warn("That section has no SQL under it.")
                return None
            return text
        return None

    def _result_snapshots(self, source: str) -> list[TableSnapshot]:
        """The result tables a command asked for, as plain data.

        `results` is the one on screen; `pinned_results` is every pinned tab in tab
        order. A table whose grid has gone (a tab closed between the key and here) is
        skipped rather than sent as an empty one.
        """
        viewer = self.results_viewer
        if source == "pinned_results":
            pane_ids = viewer.pinned_pane_ids()
        else:
            visible = viewer.visible_pane_id()
            pane_ids = [visible] if visible else []
        snapshots: list[TableSnapshot] = []
        for pane_id in pane_ids:
            table = viewer.table_for(pane_id)
            if table is None or not isinstance(table.backend, ArrowBackend):
                continue
            snapshots.append(
                TableSnapshot(
                    pane_id=pane_id,
                    sql=viewer.sql_for(pane_id),
                    columns=list(
                        zip(
                            table.plain_column_labels,
                            table.column_type_labels,
                            strict=False,
                        )
                    ),
                    data=table.backend.source_data,
                    fetched_row_count=table.fetched_row_count,
                    fetch_truncated=bool(table.fetch_truncated),
                    label=viewer.label_for(pane_id),
                    pinned=pane_id in viewer.pinned_pane_ids(),
                    elapsed=viewer.elapsed_for(pane_id),
                )
            )
        return snapshots

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=False,
        group="external_commands",
        description="Running a configured command",
    )
    def _run_external_command(self, invocation: CommandInvocation) -> None:
        """Run one command on a worker thread and post the result back.

        Exclusive within its group: two commands at once would race over stdout and,
        worse, over the same temp directory. A query already running is untouched --
        this worker is not in that group.
        """
        try:
            result = run_command(
                invocation.argv,
                env=invocation.env,
                stdin=invocation.stdin,
                timeout=invocation.timeout,
            )
        except OSError as e:
            result = CommandResult(
                returncode=-1,
                stdout="",
                stderr=(
                    f"Harlequin could not run {invocation.argv[0]!r}: {e}\n\n"
                    "A configured command has to be on the PATH Harlequin was started "
                    f"with:\n{os.environ.get('PATH', '')}"
                ),
            )
        self.post_message(
            ExternalCommandFinished(
                name=invocation.name, result=result, tmpdir=invocation.tmpdir
            )
        )

    @on(ExternalCommandFinished)
    async def apply_command_output(self, message: ExternalCommandFinished) -> None:
        """What the command said, applied on the main thread.

        The temp directory goes as soon as the child is gone: the command was told to
        copy what it wants to keep, and a directory of query results is not something to
        leave in `/tmp` because a tool might come back for it.
        """
        import shutil

        if message.tmpdir:
            shutil.rmtree(message.tmpdir, ignore_errors=True)
        command = self.commands.get(message.name)
        title = (command.description if command else None) or message.name
        result = message.result

        if result.timed_out:
            self._push_error_modal(
                title=title,
                header=(
                    f"{title} did not finish in "
                    f"{command.timeout if command else 0:g} s."
                ),
                error=HarlequinExternalError(
                    title=title, msg=result.stderr or result.stdout or "No output."
                ),
            )
            return
        if result.returncode != 0:
            self._push_error_modal(
                title=title,
                header=f"The command exited with status {result.returncode}.",
                error=HarlequinExternalError(
                    title=title,
                    msg=(result.stderr or result.stdout or "").strip()
                    or "The command wrote nothing to stderr.",
                ),
            )
            return
        if result.stderr.strip():
            # Tools log to stderr and still succeed; that is a note, not a failure.
            self.notify(result.stderr.strip().splitlines()[0], severity="warning")

        mode = command.output if command else "none"
        if mode == "none" or not result.stdout.strip():
            # Empty stdout is never applied, in any mode: a tool that returned nothing
            # must not blank the query it was given.
            if mode == "notify" and not result.stdout.strip():
                self.notify(f"{title} finished.")
            return
        if mode == "notify":
            self.notify(result.first_line)
        elif mode == "replace":
            self.editor_collection.current_editor.text = result.stdout
        elif mode == "insert":
            editor = self.editor_collection.current_editor
            if editor.text_input is not None:
                editor.text_input.insert(result.stdout)
        elif mode == "new-buffer":
            await self.editor_collection.insert_buffer_with_text(
                query_text=result.stdout
            )

    def _push_error_modal(self, title: str, header: str, error: BaseException) -> None:
        self.push_screen(
            ErrorModal(
                title=title,
                header=header,
                error=error,
            ),
        )

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=False,
        group="query_runners",
        description="fetching data from adapter.",
    )
    def _fetch_data(
        self,
        cursors: Dict[str, ExecutedStatement],
        submitted_at: float,
        limit: RowLimit,
    ) -> None:
        errors: list[tuple[BaseException, str]] = []
        results: Dict[str, ResultSet] = {}
        # `limit` is the hard limit the queries were executed under, passed back
        # so the overflow probe row is dropped rather than displayed;
        # `viewer_max_rows` is the soft cap on top of it, which leaves the
        # number of rows fetched known exactly.
        for id_, executed in cursors.items():
            try:
                results[id_] = fetch(
                    executed, limit=limit, display_limit=self.viewer_max_rows
                )
            except BaseException as e:
                errors.append((e, executed.statement.sql))
        # each ResultSet times its own fetch; the app reports the batch,
        # measured from the moment the query was submitted.
        elapsed = time.monotonic() - submitted_at
        self.post_message(
            ResultsFetched(
                cursors=cursors, results=results, errors=errors, elapsed=elapsed
            )
        )

    def extend_completers(self, parent: CatalogItem, items: list[CatalogItem]) -> None:
        # Building completions for a node, and then merging the full completion
        # list (O(n log n) over the whole catalog), are both too expensive for the
        # event loop: a schema can hold thousands of relations, and the Data
        # Catalog's lazy loader delivers one message per node. Batch the arrivals
        # and do the work in a thread, at most once per second.
        self._pending_completer_items.append((parent, items))
        if self._completer_merge_timer is None:
            self._completer_merge_timer = self.set_timer(1.0, self._merge_completers)

    def _merge_completers(self) -> None:
        self._completer_merge_timer = None
        if self._pending_completer_items:
            batch = self._pending_completer_items
            self._pending_completer_items = []
            self._extend_and_merge_completers(batch)

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=False,
        group="completer_mergers",
        description="merging catalog completions",
    )
    def _extend_and_merge_completers(
        self, batch: list[tuple[CatalogItem, list[CatalogItem]]]
    ) -> None:
        word_completer = self.editor_collection.word_completer
        member_completer = self.editor_collection.member_completer
        if word_completer is None or member_completer is None:
            return
        for parent, items in batch:
            word_completer.extend_catalog(parent=parent, items=items, defer_merge=True)
            member_completer.extend_catalog(
                parent=parent, items=items, defer_merge=True
            )
        # merge() swaps in a freshly built list, so a completer call on the main
        # thread reads either the old list or the new one, never a partial one.
        word_completer.merge()
        member_completer.merge()

    def update_completers(self, catalog: Catalog) -> None:
        if self.connection is None:
            return
        if (
            self.editor_collection.word_completer is not None
            and self.editor_collection.member_completer is not None
        ):
            self.editor_collection.word_completer.update_catalog(catalog=catalog)
            self.editor_collection.member_completer.update_catalog(catalog=catalog)
        else:
            self._build_completers(catalog)

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=False,
        group="completer_builders",
        description="building completers",
    )
    def _build_completers(self, catalog: Catalog) -> None:
        assert self.connection is not None
        try:
            extra_completions = self.connection.get_completions()
        except Exception:
            # completions are a nice-to-have; build the rest without them.
            extra_completions = []
            self.notify(
                "Harlequin could not load completions from your adapter.",
                severity="warning",
            )
        word_completer, member_completer = completer_factory(
            catalog=catalog,
            extra_completions=extra_completions,
        )
        self.post_message(
            CompletersReady(
                word_completer=word_completer, member_completer=member_completer
            )
        )

    @work(thread=True, exclusive=True, exit_on_error=False, group="schema_updaters")
    def update_schema_data(self) -> None:
        connection = self._connection_for_worker(rebuilds_catalog=True)
        if connection is None:
            # no NewCatalog is coming, and a plain return is not a worker error,
            # so nothing else stops the spinner a refresh started
            self.post_message(CatalogRefreshAborted())
            return
        catalog = connection.get_catalog()
        self.post_message(NewCatalog(catalog=catalog))

    def _validate_selection(self) -> str:
        """
        If the selection is valid query, return it. Otherwise
        return the empty string.
        """
        if self.editor is None:
            return ""
        selection = self.editor.selected_text.strip()
        if self.connection is None:
            return selection
        if selection:
            try:
                return self.connection.validate_sql(selection)
            except NotImplementedError:
                return selection
        else:
            return ""

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=False,
        group="transaction_togglers",
    )
    def toggle_transaction_mode(self) -> None:
        if self.connection is not None:
            new_mode = self.connection.toggle_transaction_mode()
            self.post_message(TransactionModeChanged(new_mode=new_mode))

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=False,
        group="commit-rollback",
    )
    def commit(self) -> None:
        if (
            self.connection is not None
            and self.connection.transaction_mode is not None
            and self.connection.transaction_mode.commit is not None
        ):
            started_at = time.monotonic()
            try:
                self.connection.transaction_mode.commit()
            except Exception as e:
                self._push_error_modal(
                    title="Transaction Error",
                    header="Harlequin could not commit the transaction.",
                    error=e,
                )
            else:
                elapsed = time.monotonic() - started_at
                self.notify(f"Transaction committed in {elapsed:.2f} seconds.")
                self.update_schema_data()

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=False,
        group="commit-rollback",
    )
    def rollback(self) -> None:
        if (
            self.connection is not None
            and self.connection.transaction_mode is not None
            and self.connection.transaction_mode.rollback is not None
        ):
            started_at = time.monotonic()
            try:
                self.connection.transaction_mode.rollback()
            except Exception as e:
                self._push_error_modal(
                    title="Transaction Error",
                    header="Harlequin could not roll back the transaction.",
                    error=e,
                )
            else:
                elapsed = time.monotonic() - started_at
                self.notify(f"Transaction rolled back in {elapsed:.2f} seconds.")
                self.update_schema_data()

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=False,
        group="interactions",
        description="_execute_interaction",
    )
    def _execute_interaction(
        self,
        interaction: Interaction,
        item: TCatalogItem_contra,
        driver: HarlequinDriver,
    ) -> None:
        try:
            interaction(item=item, driver=driver)
        except Exception as e:
            self.call_from_thread(
                self._push_error_modal,
                title="Data Catalog Interaction Error",
                header=(
                    "Harlequin could not execute an interaction from your data catalog."
                ),
                error=e,
            )

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=False,
        group="interactions",
    )
    def _execute_callback(
        self,
        callback: Callable[[], None],
    ) -> None:
        try:
            callback()
        except Exception as e:
            self.call_from_thread(
                self._push_error_modal,
                title="Data Catalog Interaction Error",
                header=(
                    "Harlequin could not execute an interaction from your data catalog."
                ),
                error=e,
            )

    def _get_keymap(self, keymap_name: str) -> "HarlequinKeyMap" | None:
        try:
            keymap = self.all_keymaps[keymap_name]
        except KeyError as e:
            self.exit(
                return_code=2,
                message=pretty_error_message(
                    HarlequinConfigError(
                        title="Could not bind keymap",
                        msg=(
                            f"Harlequin could not find a keymap named {e}, "
                            f"either as a plug-in or user-defined keymap. You may "
                            "need to install it before specifying it as an option."
                        ),
                    )
                ),
            )
            # for some reason this doesn't exit right away...
            keymap = None
        return keymap
