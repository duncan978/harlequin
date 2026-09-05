from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import List, Union

from rich.style import Style
from sqlfmt.api import Mode, format_string
from sqlfmt.exception import SqlfmtError
from textual import on, work
from textual.app import ComposeResult
from textual.color import Color
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.geometry import Offset
from textual.message import Message
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import Input, Tab, Tabs, TextArea
from textual.widgets.text_area import EditHistory, Location, Selection
from textual.worker import Worker, WorkerState
from textual_textarea import TextAreaSaved, TextEditor

from harlequin.autocomplete import (
    NO_SYMBOLS,
    BufferSymbols,
    MemberCompleter,
    WordCompleter,
    find_symbols,
)
from harlequin.components.rename_modal import RenameModal
from harlequin.components.sections_modal import SectionsModal
from harlequin.components.text_modal import ErrorModal
from harlequin.editor_cache import BufferState, load_cache
from harlequin.exception import HarlequinExternalError
from harlequin.external import launch_external_editor
from harlequin.messages import WidgetMounted
from harlequin.sections import (
    Section,
    find_sections,
    offset_of,
    point_of,
    section_at,
    section_text,
    splice,
)
from harlequin.statements import find_separators

SYMBOL_SCAN_INTERVAL = 0.3
"""Seconds an edit waits before the buffer is re-read for symbols."""


@dataclass
class EditorState:
    """One buffer's state; the active buffer's lives in the editor, the rest here."""

    text: str = ""
    selection: Selection = field(default_factory=Selection)
    scroll_offset: Offset = field(default_factory=Offset)
    undo_history: Union[EditHistory, None] = None


@dataclass
class SectionView:
    """A tab that is one section of another tab, and where it came from.

    "Focus section" is what Harlequin has instead of code folding (roadmap
    §3.4): the section opens in its own tab, you work on it with the rest of
    the script out of the way, and leaving or closing the tab writes it back
    into the buffer it came from.
    """

    parent_id: str
    """The buffer id this section belongs to."""
    span: tuple[int, int]
    """Character offsets of the section in the parent, as last written."""
    original: str
    """The section's text as last written, so an untouched tab writes nothing
    and a parent that has moved on can be recognised."""
    name: str
    """The section's name, which is how it is found again if the parent moved."""


def _blank_history(template: EditHistory) -> EditHistory:
    """Returns an empty undo history, configured like the template."""
    return EditHistory(
        max_checkpoints=template.max_checkpoints,
        checkpoint_timer=template.checkpoint_timer,
        checkpoint_max_characters=template.checkpoint_max_characters,
    )


class CodeEditor(TextEditor, inherit_bindings=False):
    class Submitted(Message, bubble=True):
        """Posted when user runs the query.

        Attributes:
            lines: The lines of code being submitted.
            cursor: The position of the cursor
        """

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class SymbolsFound(Message):
        """Posted when the loaded buffer's identifiers have been re-read."""

        def __init__(self, symbols: BufferSymbols) -> None:
            super().__init__()
            self.symbols = symbols

    _symbol_scan_timer: Union[Timer, None] = None
    _symbol_scan_failed: bool = False
    """Whether the last symbol scan failed; one toast per failure streak."""

    @on(TextArea.Changed)
    def schedule_symbol_scan(self, message: TextArea.Changed) -> None:
        """Re-read the buffer's symbols, at most once per scan interval."""
        if self._symbol_scan_timer is None:
            self._symbol_scan_timer = self.set_timer(
                SYMBOL_SCAN_INTERVAL, self._scan_for_symbols
            )

    def _scan_for_symbols(self) -> None:
        self._symbol_scan_timer = None
        self.read_symbols(self.text)

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=False,
        group="symbol_scanners",
    )
    def read_symbols(self, text: str) -> None:
        self.post_message(self.SymbolsFound(symbols=find_symbols(text)))

    @on(Worker.StateChanged)
    def handle_symbol_scan_error(self, message: Worker.StateChanged) -> None:
        if (
            message.state == WorkerState.ERROR
            and message.worker.name == "read_symbols"
            and message.worker.error is not None
            and not self._symbol_scan_failed
        ):
            # a scan failure is a degraded buffer, not a reason to die; typing
            # in the same broken buffer would toast on every debounced scan.
            self._symbol_scan_failed = True
            self.app.notify(
                "Harlequin could not read this buffer's identifiers.",
                severity="warning",
            )

    @on(SymbolsFound)
    def reset_symbol_scan_failure(self, message: SymbolsFound) -> None:
        # not stopped: SymbolsFound must keep bubbling to the app.
        self._symbol_scan_failed = False

    def selected_queries(self) -> list[str]:
        """
        Returns the list of queries that intersect
        with the current selection.
        """
        if self.text_input is None or not self.text.strip():
            return []

        if ";" not in self.text:
            return [self.text]

        separators = find_separators(self.text)
        if not separators:
            # a semicolon could be in a string literal,
            # so there may not be query separators even if
            # there are literal semicolons in the text.
            return [self.text]

        # a selection can be made in either direction, so its end
        # can come before its start.
        selection_start = min(self.selection.start, self.selection.end)
        selection_end = max(self.selection.start, self.selection.end)

        # each query spans from the end of the previous separator
        # (or the start of the buffer) through its own separator.
        queries: list[tuple[Location, Location, str]] = []
        query_start: Location = (0, 0)
        for query_end in [*separators, self.text_input.document.end]:
            q = self.text_input.get_text_range(start=query_start, end=query_end).strip()
            if q:
                queries.append((query_start, query_end, q))
            query_start = query_end

        if not queries:
            return []

        # an empty selection (a bare cursor) spans no range, so it only
        # overlaps a query if it sits strictly inside that query.
        overlapping = [
            q
            for start, end, q in queries
            if start < selection_end and end > selection_start
        ]
        if overlapping:
            return overlapping

        # the cursor sits on a boundary between queries (or in the whitespace
        # between them); run the first query that ends at or after the cursor.
        for _, end, q in queries:
            if end >= selection_start:
                return [q]

        # the cursor is in trailing whitespace after the last query.
        return [queries[-1][2]]

    def capture_state(self) -> Union[EditorState, None]:
        """
        Returns the state of the buffer loaded in the editor, or None if the
        editor has not yet composed its TextArea.
        """
        if self.text_input is None:
            return None
        return EditorState(
            text=self.text_input.text,
            selection=self.text_input.selection,
            scroll_offset=self.text_input.scroll_offset,
            undo_history=self.text_input.history,
        )

    def load_state(self, state: EditorState) -> None:
        """Swaps a buffer's state into the editor, in place of what it holds now."""
        if self.text_input is None:
            return
        # load_text clears the history it finds, so hand it a throwaway one and
        # install the buffer's own history afterwards.
        self.text_input.history = _blank_history(self.text_input.history)
        self.text_input.load_text(state.text)
        self.text_input.history = state.undo_history or _blank_history(
            self.text_input.history
        )
        self.text_input.selection = state.selection
        self.text_input.scroll_to(*state.scroll_offset, animate=False)

    def on_mount(self) -> None:
        self.post_message(EditorCollection.EditorSwitched(active_editor=self))
        self.post_message(WidgetMounted(widget=self))
        self.has_shown_clipboard_error = False

    def on_unmount(self) -> None:
        self.post_message(EditorCollection.EditorSwitched(active_editor=None))

    def on_text_area_saved(self, message: TextAreaSaved) -> None:
        self.app.notify(f"Editor contents saved to {message.path}")
        self._remember_path(Path(message.path))
        if hasattr(self.app, "data_catalog"):
            self.app.data_catalog.update_file_tree()

    @on(Input.Submitted, "#textarea__open_input")
    def remember_opened_path(self, message: Input.Submitted) -> None:
        """Record the file an Open put in this buffer.

        `textual-textarea` posts a message for a Save and none for an Open, so the only
        place the opened path exists is the footer input it was typed into. This handler
        is on the same node and the same selector as the parent's `open_file`, which is
        what makes it run at all: `message.stop()` there stops the message bubbling to
        an *ancestor*, not the other handlers on the widget itself.

        Deliberately does not stop the message, and does not open anything -- the
        parent's handler is what reads the file, and shows its own error modal when it
        cannot. `is_file()` is the guard against recording a path that failed to open;
        a path that opened and was then deleted is a stale entry nothing can prevent,
        and the consumer of this checks the file itself.
        """
        path = Path(message.input.value).expanduser()
        if path.is_file():
            self._remember_path(path)

    def _remember_path(self, path: Path) -> None:
        """Tell the collection which file the active buffer is showing.

        The collection owns the mapping because it owns the buffers: this widget is one
        editor that every tab's state is swapped through, so a path kept here would
        follow the user from one buffer to the next.

        Resolved to an absolute path first. A file opened as `notes.sql` is a file
        opened relative to Harlequin's working directory, and a bare name only means
        anything to a process that happens to share it -- whereas whatever this is
        handed to (a configured command, and whoever reads what that command wrote) may
        be anywhere. `resolve()` where the file exists, absolute-from-cwd where it
        somehow does not, and never an exception: a path that cannot be resolved is
        still better recorded as typed than dropped.
        """
        try:
            path = path.resolve()
        except OSError:
            path = Path(os.path.abspath(str(path)))
        collection = self.parent
        if isinstance(collection, EditorCollection):
            collection.remember_buffer_path(path)

    def on_text_area_clipboard_error(self) -> None:
        if not self.has_shown_clipboard_error:
            self.app.notify(
                "Could not access system clipboard.\n"
                "See https://harlequin.sh/docs/troubleshooting/copying-and-pasting",
                severity="error",
                timeout=10,
            )
            self.has_shown_clipboard_error = True

    async def action_submit(self) -> None:
        self.post_message(self.Submitted(self.text))

    def action_format(self) -> None:
        if self.text_input is None:
            return
        old_selection = self.text_input.selection
        old_text = self.text

        try:
            formatted_text = format_string(old_text, Mode())
        except SqlfmtError as e:
            self.app.push_screen(
                ErrorModal(
                    title="Formatting Error",
                    header="There was an error while formatting your file:",
                    error=e,
                )
            )
        else:
            if formatted_text != old_text:
                self.text = formatted_text
                self.text_input.selection = old_selection
                self.app.notify("Formatted query.")
            else:
                self.app.notify("Query was already formatted; no changes made.")

    def action_launch_external_editor(self) -> None:
        """Round-trips the buffer through the user's editor.

        Synchronous on the main thread, because the app has to be suspended for
        the editor to own the terminal; the result is assigned to `text`, which
        checkpoints undo history, so the whole round trip is one Ctrl+Z away.
        """
        if self.text_input is None:
            return
        old_selection = self.text_input.selection
        try:
            edit = launch_external_editor(self.app, self.text)
        except HarlequinExternalError as e:
            self.app.push_screen(
                ErrorModal(
                    title="External Editor Error",
                    header=e.title,
                    error=e,
                )
            )
            return
        if edit.text is None:
            self.app.notify(
                f"Your editor exited with status {edit.returncode}; "
                "no changes were made to the buffer.",
                severity="warning",
            )
        elif edit.text != self.text:
            self.text = edit.text
            # assigning text moves the cursor to the start of the document, and
            # a shorter buffer may no longer hold the position it was at.
            self.text_input.selection = Selection(
                self.text_input.clamp_visitable(old_selection.start),
                self.text_input.clamp_visitable(old_selection.end),
            )

    def action_focus_results_viewer(self) -> None:
        if hasattr(self.app, "action_focus_results_viewer"):
            self.app.action_focus_results_viewer()

    def action_focus_data_catalog(self) -> None:
        if hasattr(self.app, "action_focus_data_catalog"):
            self.app.action_focus_data_catalog()


    def watch_theme(self, theme: str) -> None:
        """Register the computed syntax theme, then make its two dimmest styles legible.

        `textual_textarea` builds a syntax theme for any app theme it does not know by
        blending foreground into background at 50% for comments, and leaves the gutter
        to whatever Textual derives. Measured on this workbench's palette against its
        own background: comments land at 4.70:1 and the line numbers at 3.42:1, the
        latter under the 4.5:1 that ordinary text is expected to clear.

        The theme already carries a colour for exactly this job -- `$text-muted`, which
        is 5.66:1 here -- and the blend ignores it. So: let the package compute the
        theme, then put that colour on the styles whose whole purpose is to be quiet
        without being unreadable. Nothing else is touched, and a theme that ships its
        own syntax styles (every built-in) never reaches this path.

        It matters more here than it would elsewhere because a section heading is a SQL
        comment (`-- ## Name`): without this, the most structural text in a long script
        is the least readable text on the screen.
        """
        super().watch_theme(theme)
        if not self.text_input.is_mounted:
            return
        muted = self._muted_colour()
        if muted is None:
            return
        # `available_themes` is a set of names; `_themes` is where a registered theme
        # object actually lives. A built-in theme is not in there and is left alone --
        # it shipped with syntax styles of its own and never went through the blend.
        registered = self.text_input._themes.get(theme)
        if registered is None:
            return
        corrected = replace(
            registered,
            gutter_style=Style(color=muted),
            syntax_styles={
                **registered.syntax_styles,
                "comment": muted,
                "string.documentation": muted,
            },
        )
        self.text_input.register_theme(corrected)
        self.text_input.theme = theme

    def _muted_colour(self) -> str | None:
        """A real colour for text that should be quiet but readable.

        `$text-muted` is the right token and is not always a colour: a theme may leave
        it as one of Textual's `auto NN%` tokens, which resolves against whatever it is
        drawn on and which Rich cannot parse. When that happens, blend the theme's own
        foreground into its background far enough to clear 4.5:1 -- the package blends
        half way, which is what put the line numbers at 3.42:1 to begin with.
        """
        variables = self.app.get_css_variables()
        muted = variables.get("text-muted")
        if muted:
            try:
                return Color.parse(muted).hex
            except Exception:
                pass  # an `auto NN%` token, or anything else Rich cannot read
        try:
            background = Color.parse(variables["background"])
            foreground = Color.parse(variables["foreground"])
        except Exception:
            return None
        # 0.65 rather than the package's 0.5: measured on this workbench's palette, half
        # way is 4.70:1 for comments and 3.42:1 for the gutter, and two thirds clears
        # 4.5:1 on both the light and the dark themes shipped here.
        return background.blend(foreground, factor=0.65).hex

class EditorCollection(Vertical):
    """
    A row of tabs over a single editor. Switching tabs swaps the loaded buffer's
    state out of the editor and the newly-active buffer's state in.
    """

    BORDER_TITLE = "Query Editor"
    theme: reactive[str] = reactive("harlequin")

    class EditorSwitched(Message):
        def __init__(self, active_editor: Union[CodeEditor, None]) -> None:
            self.active_editor = active_editor
            super().__init__()

    def __init__(
        self,
        name: Union[str, None] = None,
        id: Union[str, None] = None,  # noqa: A002
        classes: Union[str, None] = None,
        disabled: bool = False,
        language: str = "sql",
        theme: str = "harlequin",
    ):
        super().__init__(
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
        )
        self.language = language
        self.counter = 0
        self._word_completer: WordCompleter | None = None
        self._member_completer: MemberCompleter | None = None
        self._buffer_symbols: BufferSymbols = NO_SYMBOLS
        self.startup_cache = load_cache()
        self.buffer_states: dict[str, EditorState] = {}
        # kept beside the states rather than inside them: `_save_loaded_buffer`
        # replaces a state wholesale on every tab switch, which would take the
        # name with it.
        self.buffer_names: dict[str, str] = {}
        # Which file each buffer was last opened from or saved to. Beside the states
        # for the same reason the names are, and deliberately NOT in the editor cache:
        # putting it there would change the pickle's format, and a path is cheap to
        # lose -- a buffer whose path is forgotten is a buffer you save once.
        self.buffer_paths: dict[str, Path] = {}
        # tabs that are one section of another tab; see SectionView.
        self.section_views: dict[str, SectionView] = {}
        self.loaded_buffer_id: str | None = None
        self.tabs = Tabs()
        self.tabs.can_focus = False
        self.editor = CodeEditor(id="buffer", language=language, theme=theme)
        self.theme = theme

    def compose(self) -> ComposeResult:
        yield self.tabs
        yield self.editor

    def remember_buffer_path(self, path: Path) -> None:
        """Record the file the active buffer is showing."""
        buffer_id = self.active
        if buffer_id is not None:
            self.buffer_paths[buffer_id] = path

    def active_buffer_path(self) -> Path | None:
        """The file the active buffer was last opened from or saved to.

        None where there is none, which is the ordinary case for a scratch buffer and
        also the case after a restart: paths are not cached, so a buffer restored from
        the cache has no path until it is opened or saved again. Something that needs
        the path is expected to say so rather than guess.
        """
        buffer_id = self.active
        return self.buffer_paths.get(buffer_id) if buffer_id is not None else None

    def active_buffer_name(self) -> str:
        """What the active tab is called: the user's name for it, else `Tab n`."""
        buffer_id = self.active
        if buffer_id is None:
            return ""
        named = self.buffer_names.get(buffer_id)
        if named:
            return named
        try:
            tab = self.tabs.get_tab(buffer_id)
        except Exception:  # noqa: BLE001 -- a tab that is gone has no label
            return buffer_id
        return str(tab.label) if tab is not None else buffer_id

    @property
    def current_editor(self) -> CodeEditor:
        return self.editor

    @property
    def active(self) -> Union[str, None]:
        """The ID of the active buffer's tab."""
        return self.tabs.active or None

    @property
    def tab_count(self) -> int:
        return len(self.buffer_states)

    @property
    def buffers(self) -> List[BufferState]:
        """The state of every buffer, in tab order, for the editor cache.

        This is what the cache reads at quit, so it is also the last chance a
        focused section gets to reach its parent: `apply_section_views` runs
        here so quitting with a section tab open does not lose the edit. The
        cache does not carry the section-to-parent link itself, so on the next
        start that tab is an ordinary tab holding the section's text.
        """
        self._save_loaded_buffer()
        self.apply_section_views()
        return [
            BufferState(
                selection=state.selection,
                text=state.text,
                name=self.buffer_names.get(buffer_id),
            )
            for buffer_id, state in self.buffer_states.items()
        ]

    @property
    def active_buffer_index(self) -> int:
        buffer_ids = list(self.buffer_states)
        if self.loaded_buffer_id is None or self.loaded_buffer_id not in buffer_ids:
            return 0
        return buffer_ids.index(self.loaded_buffer_id)

    @property
    def member_completer(self) -> MemberCompleter | None:
        return self._member_completer

    @member_completer.setter
    def member_completer(self, new_completer: MemberCompleter) -> None:
        self._member_completer = new_completer
        new_completer.update_buffer_symbols(self._buffer_symbols)
        self.editor.member_completer = new_completer

    @property
    def word_completer(self) -> WordCompleter | None:
        return self._word_completer

    @word_completer.setter
    def word_completer(self, new_completer: WordCompleter) -> None:
        self._word_completer = new_completer
        new_completer.update_buffer_symbols(self._buffer_symbols)
        self.editor.word_completer = new_completer

    @on(CodeEditor.SymbolsFound)
    def update_completer_symbols(self, message: CodeEditor.SymbolsFound) -> None:
        """Hand the loaded buffer's symbols to the completers.

        They are kept here as well, since the app swaps in whole new completers
        every time it rebuilds them from the catalog. The message goes on to the
        app, which asks the Data Catalog to load the items the buffer names.
        """
        self._buffer_symbols = message.symbols
        for completer in (self._word_completer, self._member_completer):
            if completer is not None:
                completer.update_buffer_symbols(message.symbols)

    async def on_mount(self) -> None:
        if self.startup_cache is not None and self.startup_cache.buffers:
            for buffer in self.startup_cache.buffers:
                await self.action_new_buffer(state=buffer, activate=False)
            self._activate_cached_buffer(self.startup_cache.focus_index)
        else:
            await self.action_new_buffer()
        self.editor.theme = self.theme
        self.editor.word_completer = self.word_completer
        self.editor.member_completer = self.member_completer
        self.remove_class("premount")
        self.post_message(WidgetMounted(widget=self))

    def on_focus(self) -> None:
        self.editor.focus()

    def on_tabs_tab_activated(self, message: Tabs.TabActivated) -> None:
        message.stop()
        new_buffer_id = message.tab.id
        if new_buffer_id is None or new_buffer_id == self.loaded_buffer_id:
            return
        leaving = self.loaded_buffer_id
        self._save_loaded_buffer()
        # Before the new tab's state is loaded, not after: the tab being opened
        # is often the parent, and it has to load the text the section just
        # wrote into it rather than the text from before.
        if leaving is not None:
            self.apply_section_view(leaving)
        # And the mirror of it: a section tab being opened takes up whatever the
        # script has said about that section since, so the two tabs agree in both
        # directions rather than only one.
        self.refresh_section_view(new_buffer_id)
        self.loaded_buffer_id = new_buffer_id
        state = self.buffer_states.get(new_buffer_id)
        if state is not None:
            self.editor.load_state(state)
        self.post_message(self.EditorSwitched(active_editor=self.editor))
        self.editor.focus()

    def watch_theme(self, theme: str) -> None:
        if self.editor.is_mounted:
            self.editor.theme = theme

    async def insert_buffer_with_text(self, query_text: str) -> None:
        state = BufferState(selection=Selection(), text=query_text)
        await self.action_new_buffer(state=state)

    async def action_new_buffer(
        self, state: Union[BufferState, None] = None, activate: bool = True
    ) -> CodeEditor:
        self.counter += 1
        new_buffer_id = f"tab-{self.counter}"
        self.buffer_states[new_buffer_id] = (
            EditorState(text=state.text, selection=state.selection)
            if state is not None
            else EditorState()
        )
        name = getattr(state, "name", None) if state is not None else None
        if name:
            self.buffer_names[new_buffer_id] = name
        await self.tabs.add_tab(Tab(name or f"Tab {self.counter}", id=new_buffer_id))
        if activate:
            # adding the first tab activates it; any later tab has to be
            # activated here to swap its state into the editor.
            self.tabs.active = new_buffer_id
            self.editor.focus()
        if self.counter > 1:
            self.remove_class("hide-tabs")
        return self.editor

    def action_close_buffer(self) -> None:
        closing = self.active
        # A section tab is closed by writing it back, which is what closing one
        # means: the section goes home and the tab goes away.
        if closing is not None and closing in self.section_views:
            self._save_loaded_buffer()
            self.apply_section_view(closing)
        if self.tab_count > 1:
            if self.tab_count == 2:
                self.add_class("hide-tabs")
            closed_buffer_id = closing
            # the editor's contents belong to the buffer being closed, so they
            # are dropped rather than saved when the next tab is activated.
            self.loaded_buffer_id = None
            if closed_buffer_id is not None:
                self.buffer_states.pop(closed_buffer_id, None)
                self.buffer_names.pop(closed_buffer_id, None)
                self.buffer_paths.pop(closed_buffer_id, None)
                self.section_views.pop(closed_buffer_id, None)
                self.tabs.remove_tab(closed_buffer_id)
        else:
            if closing is not None:
                self.section_views.pop(closing, None)
            self.editor.load_state(EditorState())
        self.editor.focus()

    def action_rename_buffer(self) -> None:
        """Give the active buffer a name of your own.

        The name rides along in the editor cache, so the tabs you left open are
        the tabs you come back to, still labelled.
        """
        buffer_id = self.active
        if buffer_id is None:
            return

        def apply(name: str | None) -> None:
            if name is None:
                return
            if name:
                self.buffer_names[buffer_id] = name
            else:
                self.buffer_names.pop(buffer_id, None)
            tab = self.tabs.query_one(f"#{buffer_id}", Tab)
            number = buffer_id.rpartition("-")[2]
            tab.label = name or f"Tab {number}"
            if self.tab_count > 1 or self.buffer_names:
                self.remove_class("hide-tabs")
            self.editor.focus()

        self.app.push_screen(
            RenameModal(
                prompt="Name this query tab:",
                current=self.buffer_names.get(buffer_id, ""),
            ),
            apply,
        )

    def action_next_buffer(self) -> None:
        if self.tab_count < 2:
            return
        self.tabs.action_next_tab()

    # --- sections (roadmap §3.4) --------------------------------------------
    # A long working script is a handful of related queries under `-- ##`
    # headings. There is no code folding to collapse the ones you are not
    # looking at -- `textual-textarea` has none -- so these three are what a
    # long buffer gets instead: a navigator to jump between the headings, a way
    # to open one section on its own, and a way to run just that section.
    # All three read the same parser, `harlequin.sections`.

    def _sections(self) -> list[Section]:
        return find_sections(self.editor.text)

    def _cursor_section(
        self, sections: list[Section] | None = None
    ) -> Section | None:
        """The section the cursor is in."""
        sections = self._sections() if sections is None else sections
        if not sections or self.editor.text_input is None:
            return None
        # the cursor is the moving end of the selection, which is where a
        # person thinks they are even when they have selected backwards.
        offset = offset_of(self.editor.text, self.editor.selection.end)
        return section_at(sections, offset)

    def _no_sections_notice(self) -> None:
        self.app.notify(
            "No sections here. Start a line with `-- ## Name`.",
            severity="warning",
        )

    def action_show_sections(self) -> None:
        """The section navigator: a filterable list of the buffer's headings."""
        sections = self._sections()
        if not sections:
            self._no_sections_notice()
            return
        current = self._cursor_section(sections)

        def picked(result: tuple[Section, str] | None) -> None:
            if result is None:
                self.editor.focus()
                return
            section, action = result
            if action == "focus":
                self.focus_section(section)
            elif action == "run":
                self.run_section(section)
            else:
                self.jump_to_section(section)

        self.app.push_screen(
            SectionsModal(
                sections=sections,
                current=current.index if current is not None else None,
            ),
            picked,
        )

    def section_under_cursor(self) -> tuple[str, str] | None:
        """The text and the name of the section the cursor is in, or None.

        The heading is kept: a section handed to another program without its `-- ##`
        line loses the only thing that says what it is for. Same parser as the section
        actions, so "the section the cursor is in" has one definition.
        """
        sections = self._sections()
        if not sections:
            return None
        section = self._cursor_section(sections)
        if section is None:
            return None
        return section_text(self.editor.text, section), section.name

    def action_run_section(self) -> None:
        """Run every statement under the heading the cursor is in."""
        sections = self._sections()
        if not sections:
            self._no_sections_notice()
            return
        section = self._cursor_section(sections)
        if section is None:
            return
        self.run_section(section)

    def action_focus_section(self) -> None:
        """Open the section the cursor is in in its own tab."""
        sections = self._sections()
        if not sections:
            self._no_sections_notice()
            return
        section = self._cursor_section(sections)
        if section is None:
            return
        self.focus_section(section)

    def jump_to_section(self, section: Section) -> None:
        """Put the cursor on the section's first line of SQL."""
        if self.editor.text_input is None:
            return
        row = section.body_row if not section.is_preamble else section.start_row
        self.editor.text_input.selection = Selection.cursor((row, 0))
        self.editor.text_input.scroll_cursor_visible(center=True, animate=False)
        self.editor.focus()

    def run_section(self, section: Section) -> None:
        """Select the section's SQL and submit it.

        Selecting rather than submitting the text directly is deliberate: the
        app already runs "the selection" and splits it into statements, the run
        bar's row limit still applies, and Duncan can see exactly what ran.
        """
        if self.editor.text_input is None:
            return
        text = self.editor.text
        if not section_text(text, section, include_marker=False).strip():
            self.app.notify(
                f"Section '{section.name}' has no SQL under it.", severity="warning"
            )
            return
        # Trim the span to the section's first and last non-whitespace character.
        # The blank line before the next `-- ##` marker belongs, as far as the
        # statement splitter is concerned, to the statement that marker heads:
        # a selection that reaches into it runs the next section too.
        start, end = section.body_start, section.end
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        self.editor.text_input.selection = Selection(
            start=point_of(text, start), end=point_of(text, end)
        )
        self.editor.focus()
        self.editor.post_message(CodeEditor.Submitted(text))

    def focus_section(self, section: Section) -> None:
        """Open one section in its own tab, to be written back when you leave."""
        parent_id = self.active
        if parent_id is None:
            return
        if parent_id in self.section_views:
            self.app.notify(
                "Already one section. Close it for the whole script.",
                severity="warning",
            )
            return
        body = section_text(self.editor.text, section)
        self.run_worker(
            self._open_section_tab(parent_id, section, body), exclusive=False
        )

    async def _open_section_tab(
        self, parent_id: str, section: Section, body: str
    ) -> None:
        # the marker rides along in the tab, so the section can be renamed
        # there and the parent buffer follows when it is written back.
        await self.action_new_buffer(
            state=BufferState(selection=Selection(), text=body, name=section.name)
        )
        new_id = self.active
        if new_id is None:
            return
        self.section_views[new_id] = SectionView(
            parent_id=parent_id,
            span=(section.start, section.end),
            original=body,
            name=section.name,
        )
        self._mark_section_tab(new_id, parent_id, section.name)

    def _mark_section_tab(self, buffer_id: str, parent_id: str, name: str) -> None:
        """Say on the tab that it is part of another tab, and which.

        A section tab was built exactly like a scratch buffer, so nothing on screen
        told them apart -- and leaving one writes into a script it never named, which
        is a bad thing to be unable to see. `> 2 Carrier revenue` reads as "part of
        tab 2": an arrow, the parent's number, then the section.

        A prefix rather than an indent, and a number rather than the parent's name,
        because a tab strip in a 94-column pane has no room for either -- three or four
        characters is what this costs. The class is what lets `app.tcss` style it
        differently without a second colour: the palette has to stay legible for
        someone who cannot rely on hue (roadmap §6.22).
        """
        try:
            tab = self.tabs.query_one(f"#{buffer_id}", Tab)
        except NoMatches:
            return
        number = parent_id.rpartition("-")[2]
        tab.label = f"\N{RIGHTWARDS ARROW WITH HOOK} {number} {name}"
        tab.add_class("section-tab")
        tab.tooltip = f"A section of {self.buffer_names.get(parent_id, parent_id)}"

    def refresh_section_view(self, buffer_id: str) -> None:
        """Take a section tab up to date with the script it came from.

        The write-back was one-way for a while, and one-way was worse than either
        direction alone: edit the section in the script, open its tab, and the tab
        showed stale text that overwrote the newer text the moment you left it.

        Three cases, and only the first changes anything. The tab is untouched since
        it was opened and the parent has moved on -- reload it, which is what the user
        would have done by hand. Both have been edited -- keep what is in the tab and
        say so, because the tab is where the user is looking and nothing here may throw
        away typing. The parent no longer has the section at all -- say nothing now;
        `apply_section_view` is where that is a problem, and it explains itself there.
        """
        view = self.section_views.get(buffer_id)
        if view is None:
            return
        state = self.buffer_states.get(buffer_id)
        parent = self.buffer_states.get(view.parent_id)
        if state is None or parent is None:
            return

        if parent.text[view.span[0] : view.span[1]] == view.original:
            return  # the parent still holds exactly what this tab was opened with
        span: tuple[int, int] | None = None
        for candidate in find_sections(parent.text):
            if candidate.name == view.name:
                span = (candidate.start, candidate.end)
                break
        if span is None:
            return
        current = parent.text[span[0] : span[1]]
        if current == view.original:
            # the section itself is unchanged; something above it moved it along
            view.span = span
            return
        if state.text != view.original:
            self.app.notify(
                f"'{view.name}' changed in both tabs; this one was kept.",
                severity="warning",
                timeout=10,
            )
            view.span = span
            return
        self.buffer_states[buffer_id] = EditorState(
            text=current,
            selection=Selection(),
            scroll_offset=state.scroll_offset,
            undo_history=state.undo_history,
        )
        view.span = span
        view.original = current

    def apply_section_views(self) -> None:
        """Write every section tab back into its parent."""
        for buffer_id in list(self.section_views):
            self.apply_section_view(buffer_id)

    def apply_section_view(self, buffer_id: str) -> None:
        """Write one section tab back into the buffer it came from.

        The section is found in the parent by offset when the parent still has
        the text this tab was opened with, and by heading name when it does
        not -- the parent is an ordinary tab and can have been edited. When it
        is neither, nothing is written and the notification says so, because
        guessing at a span would overwrite somebody's work.
        """
        view = self.section_views.get(buffer_id)
        if view is None:
            return
        state = self.buffer_states.get(buffer_id)
        parent = self.buffer_states.get(view.parent_id)
        if state is None:
            return
        if parent is None:
            self.app.notify(
                f"'{view.name}' was not written back: its tab is closed.",
                severity="warning",
            )
            self.section_views.pop(buffer_id, None)
            return
        if state.text == view.original:
            return  # nothing was changed in the section tab

        span: tuple[int, int] | None = None
        if parent.text[view.span[0] : view.span[1]] == view.original:
            span = view.span
        else:
            for candidate in find_sections(parent.text):
                if candidate.name == view.name:
                    span = (candidate.start, candidate.end)
                    break
        if span is None:
            self.app.notify(
                f"'{view.name}' is gone from its tab. Changes kept in this one.",
                severity="warning",
                timeout=10,
            )
            return

        updated, new_span = splice(parent.text, span, state.text)
        self.buffer_states[view.parent_id] = EditorState(
            text=updated,
            selection=parent.selection,
            scroll_offset=parent.scroll_offset,
            undo_history=parent.undo_history,
        )
        view.span = new_span
        view.original = state.text

    def _activate_cached_buffer(self, focus_index: int) -> None:
        """Reopens the buffer that was active when the cache was written."""
        buffer_ids = list(self.buffer_states)
        if not 0 <= focus_index < len(buffer_ids):
            focus_index = 0
        self.tabs.active = buffer_ids[focus_index]

    def _save_loaded_buffer(self) -> None:
        """Copies the editor's contents back into the buffer they were loaded from."""
        if self.loaded_buffer_id is None:
            return
        state = self.editor.capture_state()
        if state is not None and self.loaded_buffer_id in self.buffer_states:
            self.buffer_states[self.loaded_buffer_id] = state
