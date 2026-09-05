"""The queue of what a watched directory is holding, and what to do with each item.

`alt+i` used to open every waiting item the moment it was pressed (`_open_watched`).
Roadmap §8.3 proposal 28 replaces that with a panel: a press opens *this*, never a
buffer or a tab by itself, because ten drops must not become ten pinned tabs on one
keypress (Archie's A7) and an arrival nobody was looking at is not an arrival nobody
learns about (§8.2b items 13 and 14). The panel is the persistent UI those two items
and A7 all asked for -- one feature, not three.

Nothing here opens itself and nothing pins itself. Every row sits until Enter, `a`,
`p` or `d` is pressed on it, and the four are the whole vocabulary:

* **Enter** opens the item in a new buffer (and a new, pinned result tab for its
  rows) -- what `alt+i` used to do to everything at once.
* **`a`** appends the SQL to the buffer already open, as a `-- ## name` section, so
  a Claude's query lands beside what Duncan is already writing rather than in a tab
  of its own (item 14's co-working shape; `ctrl+d` runs a section in place). Rows
  that came with it still arrive as a pinned tab -- joining a buffer is not a reason
  to lose them.
* **`p`** pins the item's rows as a result tab and leaves its SQL waiting, for an
  item that is a result Duncan wants kept without also wanting the query it came
  from open yet.
* **`d`** discards the item -- moved to `opened/` unread, same as the others, so it
  is not offered again.

The panel polls `scan()` on the same cadence the app's own toast used
(`harlequin.watch.POLL_SECONDS`) and dismisses itself the instant nothing is left:
"stays up while `scan()` is non-empty" is the panel *being* that condition, not a
flag alongside it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from harlequin.components.text_modal import VerticalSuppressClicks
from harlequin.watch import POLL_SECONDS, WatchedItem, scan

if TYPE_CHECKING:
    from harlequin.app import Harlequin

WATCH_ACTIONS = ("open", "append", "pin", "discard")
"""What can be done with the item under the highlight -- the panel's whole
vocabulary, in the order the footer lists them."""

FOOTER_TEXT = (
    "↑↓ move, Enter opens, a appends to the buffer, "
    "p pins the result, d discards, Esc closes."
)


def _kind(item: WatchedItem) -> str:
    """`sql`, `csv`, or `sql+csv`: what this item actually has to offer."""
    if item.sql is not None and item.csv is not None:
        return "sql+csv"
    if item.csv is not None:
        return "csv"
    if item.sql is not None:
        return "sql"
    return "?"  # pragma: no cover -- scan() never returns an item with neither


def _format(item: WatchedItem) -> Text:
    label = Text()
    label.append("%-7s " % _kind(item), style="dim")
    label.append(item.name)
    if item.csv_skipped is not None:
        label.append("  %s" % item.csv_skipped, style="dim italic")
    return label


class WatchList(Vertical):
    """A list of `WatchedItem`s, one row each, with the four picks bound on it.

    Up, down, page up/down and Enter are the `OptionList`'s own -- it is the
    widget that holds focus, so they never reach this container. `a`, `p` and
    `d` are not options the list has, so they bubble up here."""

    BINDINGS = [
        Binding("a", "pick('append')", "Append", show=False),
        Binding("p", "pick('pin')", "Pin", show=False),
        Binding("d", "pick('discard')", "Discard", show=False),
    ]

    class ItemPicked(Message):
        """One item, and what to do with it."""

        def __init__(self, item: WatchedItem, action: str) -> None:
            self.item = item
            self.action = action
            super().__init__()

    def __init__(
        self,
        items: list[WatchedItem],
        jump_to_newest: bool = False,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.items = items
        self.jump_to_newest = jump_to_newest

    def compose(self) -> ComposeResult:
        yield OptionList(classes="watch_list_options")
        yield Static(FOOTER_TEXT, classes="watch_list_footer")

    def on_mount(self) -> None:
        self.option_list = self.query_one(OptionList)
        self._populate(highlight_last=self.jump_to_newest)

    def set_items(self, items: list[WatchedItem]) -> None:
        """Redraw for a fresh `scan()`, keeping the highlight on the same name
        where it is still waiting."""
        kept = self._highlighted_name()
        self.items = items
        self._populate(keep_name=kept)

    def _highlighted_name(self) -> str | None:
        index = self.option_list.highlighted
        if index is None or index >= len(self.items):
            return None
        return self.items[index].name

    def _populate(
        self, keep_name: str | None = None, highlight_last: bool = False
    ) -> None:
        self.option_list.clear_options()
        self.option_list.add_options([Option(_format(item)) for item in self.items])
        if not self.items:
            return
        if highlight_last:
            self.option_list.highlighted = len(self.items) - 1
        elif keep_name is not None:
            names = [item.name for item in self.items]
            self.option_list.highlighted = (
                names.index(keep_name) if keep_name in names else 0
            )
        else:
            self.option_list.highlighted = 0

    @on(OptionList.OptionSelected)
    def handle_option_selected(self, message: OptionList.OptionSelected) -> None:
        message.stop()
        self.action_pick("open")

    def action_pick(self, action: str) -> None:
        highlighted = self.option_list.highlighted
        if highlighted is None or highlighted >= len(self.items):
            return
        self.post_message(self.ItemPicked(item=self.items[highlighted], action=action))


class WatchPanel(ModalScreen[None]):
    """Everything a watched directory is holding, over the whole app.

    Stays open across picks -- one item leaving the queue is not a reason to close
    it on the rest -- and closes itself once `scan()` comes back empty. `Escape` or
    a click outside also close it early; nothing about "stays up while non-empty"
    promises it cannot be dismissed, only that it does not have to be.
    """

    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
    ]

    def __init__(
        self,
        watch_dir: Path,
        jump_to_newest: bool = False,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.watch_dir = watch_dir
        self.jump_to_newest = jump_to_newest

    def compose(self) -> ComposeResult:
        with VerticalSuppressClicks(id="modal_outer"):
            yield WatchList(
                items=scan(self.watch_dir), jump_to_newest=self.jump_to_newest
            )

    def on_mount(self) -> None:
        self._set_title(len(self.query_one(WatchList).items))
        self.query_one(OptionList).focus()
        self.set_interval(POLL_SECONDS, self._refresh)

    def _set_title(self, count: int) -> None:
        self.query_one("#modal_outer").border_title = "Waiting (%d)" % count

    def _refresh(self) -> None:
        items = scan(self.watch_dir)
        if not items:
            self.dismiss(None)
            return
        self.query_one(WatchList).set_items(items)
        self._set_title(len(items))

    @on(WatchList.ItemPicked)
    def handle_item_picked(self, message: WatchList.ItemPicked) -> None:
        message.stop()
        app = cast("Harlequin", self.app)
        self.run_worker(self._act(app, message.item, message.action), exclusive=False)

    async def _act(self, app: "Harlequin", item: WatchedItem, action: str) -> None:
        await app.act_on_watched_item(item, action)
        # the screen may have dismissed itself already, from an empty scan the
        # poll timer found while the action was still running
        if self in app.screen_stack:
            self._refresh()

    def on_click(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
