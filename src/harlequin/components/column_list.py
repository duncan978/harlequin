from __future__ import annotations

import pyperclip
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class ColumnList(Vertical, can_focus=True):
    """A filterable list of one result's columns.

    A wide result is worse than unreadable in the grid -- a column 80 to the
    right is not just off-screen, it is unfindable without arrowing past the
    79 in front of it. This is the list the grid cannot be: every column at
    once, filterable by name or type, and picking one puts the table's cursor
    on it.

    Shared by the `c` modal and the Data Catalog's Columns tab, so the two
    never drift apart.
    """

    BINDINGS = [
        Binding("up", "list_key('cursor_up')", "Up", show=False),
        Binding("down", "list_key('cursor_down')", "Down", show=False),
        Binding("pageup", "list_key('page_up')", "Page Up", show=False),
        Binding("pagedown", "list_key('page_down')", "Page Down", show=False),
        Binding("ctrl+y", "copy_names", "Copy Names", show=False),
    ]

    class ColumnSelected(Message):
        """A column was picked; the result grid should jump to it."""

        def __init__(self, column: int) -> None:
            self.column = column
            super().__init__()

    def __init__(
        self,
        columns: list[tuple[str, str]] | None = None,
        current: int | None = None,
        footer: str | None = None,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.columns = columns or []
        self.current = current
        self.footer_text = footer
        self.title_widget: Widget | None = None
        """Whose border title carries the count. Defaults to the OptionList."""
        # what the list is showing, as indexes into `self.columns`; the filter
        # rewrites it, so a highlighted row means nothing without it.
        self.visible_indexes: list[int] = list(range(len(self.columns)))
        self._filter_text = ""
        """The filter the list was last built for.

        The Input posts a `Changed` for its own empty starting value, after
        the list has already put the highlight on the cursor's column;
        rebuilding for that would throw the highlight away.
        """

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Filter columns…", classes="column_filter")
        yield OptionList(classes="column_list")
        if self.footer_text:
            yield Static(self.footer_text, classes="column_list_footer")

    def on_mount(self) -> None:
        self.option_list = self.query_one(OptionList)
        self.option_list.can_focus = False
        self.filter_input = self.query_one(Input)
        if self.title_widget is None:
            self.title_widget = self.option_list
        self._populate(filter_text="", highlight=self.current)

    def on_focus(self) -> None:
        self.filter_input.focus()

    def set_columns(
        self, columns: list[tuple[str, str]], current: int | None = None
    ) -> None:
        """Point the list at a different result, keeping the filter in place."""
        self.columns = columns
        self.current = current
        if not self.is_mounted:
            self.visible_indexes = list(range(len(columns)))
            return
        self._populate(filter_text=self._filter_text, highlight=current)

    @on(Input.Changed)
    def handle_filter(self, message: Input.Changed) -> None:
        message.stop()
        if message.value == self._filter_text:
            return
        self._populate(filter_text=message.value)

    @on(Input.Submitted)
    def handle_submit(self, message: Input.Submitted) -> None:
        message.stop()
        self._select_highlighted()

    @on(OptionList.OptionSelected)
    def handle_option_selected(self, message: OptionList.OptionSelected) -> None:
        message.stop()
        self._select_highlighted()

    def action_list_key(self, action: str) -> None:
        getattr(self.option_list, f"action_{action}")()

    def action_copy_names(self) -> None:
        names = ", ".join(self.columns[i][0] for i in self.visible_indexes)
        if not names:
            return
        # OSC 52 works over ssh and where pyperclip has no backend
        self.app.copy_to_clipboard(names)
        try:
            pyperclip.copy(names)
        except pyperclip.PyperclipException:
            pass
        self.app.notify(f"Copied {len(self.visible_indexes)} column names.")

    def _populate(self, filter_text: str, highlight: int | None = None) -> None:
        """Rebuild the list for `filter_text`, keeping the highlight on the
        same column where the filter still shows it."""
        if highlight is None:
            highlight = self._highlighted_column()
        self._filter_text = filter_text
        needle = filter_text.strip().lower()
        self.visible_indexes = [
            i
            for i, (name, type_label) in enumerate(self.columns)
            if not needle
            or needle in name.lower()
            or needle in str(type_label).lower()
        ]
        self.option_list.clear_options()
        self.option_list.add_options(
            [Option(self._format(i)) for i in self.visible_indexes]
        )
        if self.visible_indexes:
            self.option_list.highlighted = (
                self.visible_indexes.index(highlight)
                if highlight in self.visible_indexes
                else 0
            )
        self._set_title(matched=len(self.visible_indexes))

    def _highlighted_column(self) -> int | None:
        """The column the highlight is on, as an index into `self.columns`."""
        highlighted = self.option_list.highlighted
        if highlighted is None or highlighted >= len(self.visible_indexes):
            return None
        return self.visible_indexes[highlighted]

    def _format(self, index: int) -> Text:
        name, type_label = self.columns[index]
        label = Text(f"{index + 1:>4}  ")
        label.stylize("dim", 0, 6)
        label.append(name)
        if type_label:
            label.append(f"  {type_label}", style="dim")
        return label

    def _set_title(self, matched: int) -> None:
        if self.title_widget is None:
            return
        total = len(self.columns)
        self.title_widget.border_title = (
            f"Columns ({total})"
            if matched == total
            else f"Columns ({matched} of {total})"
        )

    def _select_highlighted(self) -> None:
        highlighted = self.option_list.highlighted
        if highlighted is None or highlighted >= len(self.visible_indexes):
            return
        self.post_message(self.ColumnSelected(self.visible_indexes[highlighted]))
