from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Literal

from rich.style import Style
from rich.text import Text
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import (
    ContentSwitcher,
    TabbedContent,
    TabPane,
    Tabs,
)
from textual_fastdatatable import DataTable

from harlequin.components.columns_modal import ColumnsModal
from harlequin.components.rename_modal import RenameModal
from harlequin.components.text_modal import CellViewModal
from harlequin.messages import WidgetMounted

if TYPE_CHECKING:
    from textual_fastdatatable.backend import DataTableBackend
    from textual_fastdatatable.data_table import CursorType

    from harlequin.query import ResultSet


class ResultsTable(DataTable, inherit_bindings=False):
    DEFAULT_CSS = """
        ResultsTable {
            height: 100%;
            width: 100%;
        }
    """

    def on_mount(self) -> None:
        self.post_message(WidgetMounted(widget=self))

    def __init__(
        self,
        *,
        backend: "DataTableBackend" | None = None,
        data: Any | None = None,
        column_labels: list[str | Text] | None = None,
        plain_column_labels: list[str | Text] | None = None,
        column_type_labels: list[str] | None = None,
        column_widths: list[int | None] | None = None,
        max_column_content_width: int | None = None,
        show_header: bool = True,
        show_row_labels: bool = True,
        max_rows: int | None = None,
        fixed_rows: int = 0,
        fixed_columns: int = 0,
        zebra_stripes: bool = False,
        header_height: int = 1,
        show_cursor: bool = True,
        cursor_foreground_priority: Literal["renderable", "css"] = "css",
        cursor_background_priority: Literal["renderable", "css"] = "renderable",
        cursor_type: "CursorType" = "cell",
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
        disabled: bool = False,
        null_rep: str = "",
        render_markup: bool = True,
        fetched_row_count: int | None = None,
        fetch_truncated: bool = False,
    ):
        self.plain_column_labels: list[str] = (
            [str(label) for label in plain_column_labels]
            if plain_column_labels is not None
            else []
        )
        # the type the database reported for each column, parallel to the
        # labels above; the Columns list shows it next to the name.
        self.column_type_labels: list[str] = (
            [str(label) for label in column_type_labels]
            if column_type_labels is not None
            else []
        )
        # what the database returned, which `source_row_count` cannot say on its
        # own: under a hard fetch limit it counts the overflow probe row, and
        # there were more rows behind it that nobody fetched.
        self.fetched_row_count = fetched_row_count
        self.fetch_truncated = fetch_truncated
        super().__init__(
            backend=backend,
            data=data,
            column_labels=column_labels,
            column_widths=column_widths,
            max_column_content_width=max_column_content_width,
            show_header=show_header,
            show_row_labels=show_row_labels,
            max_rows=max_rows,
            fixed_rows=fixed_rows,
            fixed_columns=fixed_columns,
            zebra_stripes=zebra_stripes,
            header_height=header_height,
            show_cursor=show_cursor,
            cursor_foreground_priority=cursor_foreground_priority,
            cursor_background_priority=cursor_background_priority,
            cursor_type=cursor_type,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            null_rep=null_rep,
            render_markup=render_markup,
        )

    def action_view_cell(self) -> None:
        """Open a modal showing the full value of the cell under the cursor."""
        if self.backend is None or self.row_count == 0:
            return
        coord = self.cursor_coordinate
        if not self.is_valid_coordinate(coord):
            return
        value = self.get_cell_at(coord)
        try:
            column_label = self.plain_column_labels[coord.column]
        except IndexError:
            column_label = ""
        self.app.push_screen(CellViewModal(value=value, column_label=column_label))

    def column_pairs(self) -> list[tuple[str, str]]:
        """This result's columns as (name, type) pairs, in grid order."""
        if not self.plain_column_labels:
            return []
        types = self.column_type_labels
        return [
            (name, types[i] if i < len(types) else "")
            for i, name in enumerate(self.plain_column_labels)
        ]

    def action_show_columns(self) -> None:
        """List every column of this result; picking one jumps the cursor to it.

        A column 80 to the right is not merely off-screen in the grid, it is
        unfindable without arrowing past the 79 in front of it.
        """
        columns = self.column_pairs()
        if not columns:
            return

        def jump_to(column: int | None) -> None:
            if column is None:
                return
            self.move_cursor(column=column)
            self.focus()

        self.app.push_screen(
            ColumnsModal(columns=columns, current=self.cursor_column), jump_to
        )


class ResultsViewer(TabbedContent, can_focus=True):
    BORDER_TITLE = "Query Results"

    class ColumnsChanged(Message):
        """The visible result changed, and with it the list of columns.

        The Data Catalog's Columns tab is a second view of the grid's header,
        so it has to hear about every result that replaces the one it shows.
        """

        def __init__(
            self, columns: list[tuple[str, str]], current: int | None
        ) -> None:
            self.columns = columns
            self.current = current
            super().__init__()

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "results-viewer--type-label",
    }

    def __init__(self) -> None:
        # Pinned panes outlive the run that made them, so tab numbers cannot be
        # `tab_count + 1`: a pin holds `result-2` while `result-2` would be the
        # name of the next thing pushed. The counter only rewinds to zero when
        # the viewer is empty, which keeps the numbering of an unpinned session
        # exactly what it always was.
        self._next_tab_number = 0
        self._pinned: set[str] = set()
        self._sql_by_pane: dict[str, str] = {}
        self._elapsed_by_pane: dict[str, float] = {}
        """How long each result took to fetch, as its run reported it.

        Recorded here rather than asked of the table, because it is a fact about the
        run and not about the grid, and a configured command that hands a result to
        another program is expected to say how long it took.
        """
        self._names: dict[str, str] = {}
        self._activate_next_push = False
        super().__init__()

    def on_mount(self) -> None:
        self.query_one(Tabs).can_focus = False
        self.add_class("hide-tabs")
        self.max_col_width = self._get_max_col_width()
        self.post_message(WidgetMounted(widget=self))

    def clear_all_tables(self) -> None:
        self.clear_panes()
        self._pinned.clear()
        self._sql_by_pane.clear()
        self._elapsed_by_pane.clear()
        self._names.clear()
        self._next_tab_number = 0
        self.add_class("hide-tabs")

    def clear_unpinned_tables(self) -> None:
        """Drop last run's results, but keep the ones asked to stay.

        A pin is the whole difference between a results pane and a scratch
        pane: it says this table is worth comparing the next one against, so
        the next run appends beside it instead of replacing it.
        """
        for pane in list(self.query(TabPane)):
            if pane.id is None or pane.id in self._pinned:
                continue
            self._sql_by_pane.pop(pane.id, None)
            self._elapsed_by_pane.pop(pane.id, None)
            self._names.pop(pane.id, None)
            self.remove_pane(pane.id)
        if not self._pinned:
            self._next_tab_number = 0
            self.add_class("hide-tabs")
        # whatever this run pushes first is what the user asked to see, even
        # with older pinned tabs sitting to the left of it.
        self._activate_next_push = True

    def get_visible_table(self) -> ResultsTable | None:
        content = self.query_one(ContentSwitcher)
        active_tab_id = self.active
        if active_tab_id:
            try:
                tab_pane = content.query_one(f"#{active_tab_id}", TabPane)
                return tab_pane.query_one(ResultsTable)
            except NoMatches:
                return None
        else:
            tables = content.query(ResultsTable)
            try:
                return tables.first(ResultsTable)
            except NoMatches:
                return None

    # --- what is here, for something outside the viewer to hand on ----------
    # A configured command (`[commands.x]` with `stdin = "results"`) serializes a
    # result and gives it to another program, which means something outside this
    # widget has to be able to ask what is in it: which tab is visible, which are
    # pinned, and for any of them the grid, the SQL that made it and how long that
    # took. Accessors rather than public attributes, so the bookkeeping above stays
    # this widget's own.

    def visible_pane_id(self) -> str | None:
        """The tab the user is looking at, or None when the viewer is empty."""
        return self.active or None

    def pinned_pane_ids(self) -> list[str]:
        """Every pinned tab, in tab order -- the order they are on screen, which is
        the order a user reading them would expect them handed over in."""
        order = [pane.id for pane in self.query(TabPane) if pane.id is not None]
        return [pane_id for pane_id in order if pane_id in self._pinned]

    def table_for(self, pane_id: str) -> ResultsTable | None:
        try:
            return self.query_one(f"#{pane_id}", TabPane).query_one(ResultsTable)
        except NoMatches:
            return None

    def sql_for(self, pane_id: str) -> str:
        return self._sql_by_pane.get(pane_id, "")

    def label_for(self, pane_id: str) -> str | None:
        """The name the user gave the tab, or None for a `Result n` nobody renamed."""
        return self._names.get(pane_id)

    def elapsed_for(self, pane_id: str) -> float | None:
        return self._elapsed_by_pane.get(pane_id)

    async def push_table(
        self, table_id: str, result: ResultSet, elapsed: float | None = None
    ) -> ResultsTable:
        formatted_labels = [
            self._format_column_label(col_name, col_type)
            for col_name, col_type in result.columns
        ]
        table = ResultsTable(
            id=table_id,
            column_labels=formatted_labels,  # type: ignore
            plain_column_labels=[col_name for (col_name, _) in result.columns],
            column_type_labels=[col_type for (_, col_type) in result.columns],
            # the backend was built by `harlequin.query.fetch()`, which already
            # applied `viewer_max_rows` as its row cap.
            backend=result.backend,
            fetched_row_count=result.fetched_row_count,
            fetch_truncated=result.truncated,
            cursor_type="range",
            max_column_content_width=self.max_col_width,
            null_rep="[dim]∅ null[/]",
            render_markup=False,
        )
        self._next_tab_number += 1
        n = self._next_tab_number
        pane_id = f"result-{n}"
        self._sql_by_pane[pane_id] = result.statement.sql
        if elapsed is not None:
            self._elapsed_by_pane[pane_id] = elapsed
        pane = TabPane(f"Result {n}", table, id=pane_id)
        await self.add_pane(pane)
        self._relabel_tab(pane_id)
        self._sync_tab_visibility()
        if self._activate_next_push:
            self._activate_next_push = False
            self.active = pane_id
        # need to manually refresh the table, since activating the tab
        # doesn't consistently cause a new layout calc.
        table.refresh(repaint=True, layout=True)
        self.announce_columns()
        return table

    def action_toggle_pin(self) -> None:
        """Keep the visible result across the next run, or stop keeping it."""
        pane_id = self.active
        if not pane_id:
            return
        if pane_id in self._pinned:
            self._pinned.discard(pane_id)
            self.notify(f"Unpinned {pane_id.replace('-', ' ').title()}.")
        else:
            self._pinned.add(pane_id)
            self.notify(f"Pinned {pane_id.replace('-', ' ').title()}.")
        self._relabel_tab(pane_id)
        self._sync_tab_visibility()

    def action_rename_tab(self) -> None:
        """Give the visible result a name of your own."""
        pane_id = self.active
        if not pane_id:
            return

        def apply(name: str | None) -> None:
            if name is None:
                return
            if name:
                self._names[pane_id] = name
                # naming a result is a statement that it is worth keeping, so
                # it pins too -- an unpinned tab would not survive to wear the
                # name past the next run. `p` still unpins it.
                self._pinned.add(pane_id)
            else:
                self._names.pop(pane_id, None)
            self._relabel_tab(pane_id)
            self._sync_tab_visibility()

        self.app.push_screen(
            RenameModal(
                prompt="Name this result:", current=self._names.get(pane_id, "")
            ),
            apply,
        )

    def action_close_tab(self) -> None:
        """Remove the visible result, pinned or not."""
        pane_id = self.active
        if not pane_id:
            return
        self._pinned.discard(pane_id)
        self._sql_by_pane.pop(pane_id, None)
        self._elapsed_by_pane.pop(pane_id, None)
        self._names.pop(pane_id, None)
        self.remove_pane(pane_id)
        # `remove_pane` is queued, so `tab_count` is still the old one here;
        # everything that depends on what is left has to wait for the removal.
        self.call_after_refresh(self._after_close)

    def _after_close(self) -> None:
        self._sync_tab_visibility()
        if self.tab_count == 0:
            self._next_tab_number = 0
            self.border_title = "Query Results"
        self.announce_columns()

    def _relabel_tab(self, pane_id: str) -> None:
        """A tab says which result it is; its tooltip says which query it was.

        Every tab keeps the number it was given, pinned or not, because the
        number is the one thing about a result that is short, stable and
        unambiguous. A tab wore its SQL for a while and that was worse: ad hoc
        queries start with the same twenty characters (`select * from
        insurify.…`), so a row of them was a row of identical labels. The SQL
        is still one hover away, and `ctrl+t` names the tabs worth a name.
        """
        try:
            tab = self.get_tab(pane_id)
        except (NoMatches, ValueError):
            return
        n = pane_id.rpartition("-")[2]
        name = self._names.get(pane_id)
        body = name if name is not None else f"Result {n}"
        tab.label = f"\N{PUSHPIN} {body}" if pane_id in self._pinned else body
        tab.tooltip = self._sql_by_pane.get(pane_id, "").strip() or None

    def _sync_tab_visibility(self) -> None:
        """Hide the tab bar only when there is nothing a tab could tell you."""
        if self.tab_count > 1 or self._pinned or self._names:
            self.remove_class("hide-tabs")
        else:
            self.add_class("hide-tabs")

    def announce_columns(self) -> None:
        """Tell the app which columns the visible result has."""
        table = self.get_visible_table()
        if table is None:
            self.post_message(self.ColumnsChanged(columns=[], current=None))
            return
        self.post_message(
            self.ColumnsChanged(
                columns=table.column_pairs(), current=table.cursor_column
            )
        )

    def show_loading(self) -> None:
        self.border_title = "Running Query"
        self.add_class("non-responsive")
        self.loading = True
        self.clear_unpinned_tables()

    def show_table(self, did_run: bool = True) -> None:
        self.loading = False
        self.remove_class("non-responsive")
        if not did_run:
            self.border_title = "Query Results"
        else:
            table = self.get_visible_table()
            if table is not None:
                if table.source_row_count > 0:
                    self.border_title = f"Query Results {self._human_row_count(table)}"
                else:
                    self.border_title = "Query Returned No Records"
            else:
                self.border_title = "Query Results"
        self.announce_columns()

    def on_focus(self) -> None:
        self._focus_on_visible_table()

    def on_resize(self) -> None:
        # only impacts new tables pushed after the resize
        self.max_col_width = self._get_max_col_width()

    def on_tabbed_content_tab_activated(
        self, message: TabbedContent.TabActivated
    ) -> None:
        message.stop()
        maybe_table = self.get_visible_table()
        if maybe_table is not None:
            self.border_title = f"Query Results {self._human_row_count(maybe_table)}"
            maybe_table.focus()
        self.announce_columns()

    def action_switch_tab(self, offset: int) -> None:
        """Cycle by position in the tab bar.

        Pinning leaves gaps in the numbering -- `result-1` can sit next to
        `result-4` -- so the old arithmetic on the id would land on a tab that
        is not there. Position is what the user sees, and what they mean.
        """
        if not self.active:
            return
        pane_ids = [pane.id for pane in self.query(TabPane) if pane.id is not None]
        if not pane_ids:
            return
        try:
            current = pane_ids.index(self.active)
        except ValueError:
            current = 0
        self.active = pane_ids[(current + offset) % len(pane_ids)]
        self._focus_on_visible_table()

    def action_focus_data_catalog(self) -> None:
        if hasattr(self.app, "action_focus_data_catalog"):
            self.app.action_focus_data_catalog()

    def action_focus_query_editor(self) -> None:
        if hasattr(self.app, "action_focus_query_editor"):
            self.app.action_focus_query_editor()

    def _focus_on_visible_table(self) -> None:
        maybe_table = self.get_visible_table()
        if maybe_table is not None:
            maybe_table.focus()

    def _human_row_count(self, table: ResultsTable) -> str:
        """What the table holds, and what it is holding it out of.

        A hard fetch limit stops the total from being knowable -- not fetching
        the rest is the point of it -- so a truncated fetch reads `>500` rather
        than claiming the 500 rows that arrived were all there were.
        """
        shown = table.row_count
        total = (
            table.fetched_row_count
            if table.fetched_row_count is not None
            else table.source_row_count
        )
        if table.fetch_truncated:
            return f"(Showing {shown:,} of >{total:,} Records)"
        if shown < total:
            return f"(Showing {shown:,} of {total:,} Records)"
        return f"({total:,} Records)"

    def _format_column_label(self, col_name: str, col_type: str) -> Text:
        type_label_style = self.get_component_rich_style("results-viewer--type-label")
        type_label_fg_style = Style(color=type_label_style.color)
        label = Text.assemble(col_name, " ", (col_type, type_label_fg_style))
        return label

    def _get_max_col_width(self) -> int:
        SMALLEST_MAX_WIDTH = 20
        CELL_X_PADDING = 2
        parent_size = getattr(self.parent, "container_size", self.screen.container_size)
        return max(SMALLEST_MAX_WIDTH, parent_size.width // 2 - CELL_X_PADDING)
