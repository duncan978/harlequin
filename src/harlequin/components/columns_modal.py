from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen

from harlequin.components.column_list import ColumnList
from harlequin.components.text_modal import VerticalSuppressClicks


class ColumnsModal(ModalScreen[int | None]):
    """The column list, over the whole app, dismissing with the pick.

    The same list lives permanently in the Data Catalog's Columns tab; this
    is the version you reach for without leaving the grid.
    """

    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
    ]

    def __init__(
        self,
        columns: list[tuple[str, str]],
        current: int | None = None,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.columns = columns
        self.current = current

    def compose(self) -> ComposeResult:
        with VerticalSuppressClicks(id="modal_outer"):
            yield ColumnList(
                columns=self.columns,
                current=self.current,
                footer=(
                    "Type to filter. ↑↓ move, Enter jumps to the column, "
                    "ctrl+y copies names, Esc closes."
                ),
            )

    def on_mount(self) -> None:
        self.query_one("#modal_outer").border_title = "Columns"
        self.query_one(ColumnList).focus()

    @on(ColumnList.ColumnSelected)
    def handle_column_selected(self, message: ColumnList.ColumnSelected) -> None:
        message.stop()
        self.dismiss(message.column)

    def on_click(self) -> None:
        # the modal's own container suppresses clicks, so this is a click
        # outside it
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
