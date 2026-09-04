from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from harlequin.components.text_modal import VerticalSuppressClicks
from harlequin.sections import Section

SECTION_ACTIONS = ("jump", "focus", "run")
"""What the navigator can do with the section under the highlight."""


class SectionList(Vertical, can_focus=True):
    """A filterable list of the `-- ## sections` in a buffer.

    Same shape as the ColumnList, for the same reason: a list you filter by
    typing beats arrowing through a long buffer looking for a heading. What is
    different is that picking a section can mean three things, so the pick
    carries which one -- jump to it, open it in its own tab, or run it.
    """

    BINDINGS = [
        Binding("up", "list_key('cursor_up')", "Up", show=False),
        Binding("down", "list_key('cursor_down')", "Down", show=False),
        Binding("pageup", "list_key('page_up')", "Page Up", show=False),
        Binding("pagedown", "list_key('page_down')", "Page Down", show=False),
        Binding("ctrl+o", "pick('focus')", "Focus Section", show=False),
        Binding("ctrl+enter,ctrl+j", "pick('run')", "Run Section", show=False),
    ]

    class SectionPicked(Message):
        """A section was picked, and what to do with it."""

        def __init__(self, section: Section, action: str) -> None:
            self.section = section
            self.action = action
            super().__init__()

    def __init__(
        self,
        sections: list[Section],
        current: int | None = None,
        footer: str | None = None,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.sections = sections
        self.current = current
        self.footer_text = footer
        # what the list is showing, as indexes into `self.sections`; the filter
        # rewrites it, so a highlighted row means nothing without it.
        self.visible_indexes: list[int] = list(range(len(sections)))
        self._filter_text = ""
        """The filter the list was last built for.

        The Input posts a `Changed` for its own empty starting value, after the
        list has already put the highlight on the cursor's section; rebuilding
        for that would throw the highlight away.
        """

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Filter sections…", classes="column_filter")
        yield OptionList(classes="column_list")
        if self.footer_text:
            yield Static(self.footer_text, classes="column_list_footer")

    def on_mount(self) -> None:
        self.option_list = self.query_one(OptionList)
        self.option_list.can_focus = False
        self.filter_input = self.query_one(Input)
        self._populate(filter_text="", highlight=self.current)

    def on_focus(self) -> None:
        self.filter_input.focus()

    @on(Input.Changed)
    def handle_filter(self, message: Input.Changed) -> None:
        message.stop()
        if message.value == self._filter_text:
            return
        self._populate(filter_text=message.value)

    @on(Input.Submitted)
    def handle_submit(self, message: Input.Submitted) -> None:
        message.stop()
        self.action_pick("jump")

    @on(OptionList.OptionSelected)
    def handle_option_selected(self, message: OptionList.OptionSelected) -> None:
        message.stop()
        self.action_pick("jump")

    def action_list_key(self, action: str) -> None:
        getattr(self.option_list, f"action_{action}")()

    def action_pick(self, action: str) -> None:
        highlighted = self.option_list.highlighted
        if highlighted is None or highlighted >= len(self.visible_indexes):
            return
        section = self.sections[self.visible_indexes[highlighted]]
        self.post_message(self.SectionPicked(section=section, action=action))

    def _populate(self, filter_text: str, highlight: int | None = None) -> None:
        if highlight is None:
            highlight = self._highlighted_section()
        self._filter_text = filter_text
        needle = filter_text.strip().lower()
        self.visible_indexes = [
            i
            for i, section in enumerate(self.sections)
            if not needle or needle in section.name.lower()
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

    def _highlighted_section(self) -> int | None:
        highlighted = self.option_list.highlighted
        if highlighted is None or highlighted >= len(self.visible_indexes):
            return None
        return self.visible_indexes[highlighted]

    def _format(self, index: int) -> Text:
        section = self.sections[index]
        # the line number is what you are really looking for once you know
        # which section you want, so it leads -- dimmed, like the column list's.
        label = Text(f"{section.start_row + 1:>5}  ")
        label.stylize("dim", 0, 7)
        # a deeper marker is indented rather than labelled: it reads as an
        # outline, which is what more hashes are for.
        label.append("  " * max(section.level - 2, 0))
        label.append(section.name, style="dim italic" if section.is_preamble else "")
        return label

    def _set_title(self, matched: int) -> None:
        total = len(self.sections)
        self.option_list.border_title = (
            f"Sections ({total})" if matched == total else f"Sections ({matched} of {total})"
        )


class SectionsModal(ModalScreen[tuple[Section, str] | None]):
    """The section navigator, over the whole app, dismissing with the pick.

    Harlequin has no code folding -- `textual-textarea` has none and adding it
    means changing the upstream widget's rendering model -- so this and "focus
    section" are what a long script gets instead (roadmap §3.4).
    """

    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
    ]

    def __init__(
        self,
        sections: list[Section],
        current: int | None = None,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.sections = sections
        self.current = current

    def compose(self) -> ComposeResult:
        with VerticalSuppressClicks(id="modal_outer"):
            yield SectionList(
                sections=self.sections,
                current=self.current,
                footer=(
                    "Type to filter. ↑↓ move, Enter jumps, ctrl+o opens the section "
                    "in its own tab, ctrl+enter runs it, Esc closes."
                ),
            )

    def on_mount(self) -> None:
        self.query_one("#modal_outer").border_title = "Sections"
        self.query_one(SectionList).focus()

    @on(SectionList.SectionPicked)
    def handle_section_picked(self, message: SectionList.SectionPicked) -> None:
        message.stop()
        self.dismiss((message.section, message.action))

    def on_click(self) -> None:
        # the modal's own container suppresses clicks, so this is a click
        # outside it
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
