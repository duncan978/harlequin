from __future__ import annotations

from typing import Mapping

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
from harlequin.config import CommandConfig


class CommandList(Vertical, can_focus=True):
    """A filterable list of the commands a config file defined.

    The same shape as the section navigator and the column list, for the same reason: a
    list you filter by typing is faster than remembering which key a command is on. It
    is also the answer to a keyspace that has run out -- a command needs no key at all
    to be reachable, and one key reaches all of them.
    """

    BINDINGS = [
        Binding("up", "list_key('cursor_up')", "Up", show=False),
        Binding("down", "list_key('cursor_down')", "Down", show=False),
        Binding("pageup", "list_key('page_up')", "Page Up", show=False),
        Binding("pagedown", "list_key('page_down')", "Page Down", show=False),
    ]

    class CommandPicked(Message):
        def __init__(self, name: str) -> None:
            self.name = name
            super().__init__()

    def __init__(
        self,
        commands: Mapping[str, CommandConfig],
        keys: Mapping[str, str] | None = None,
        footer: str | None = None,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.names = sorted(commands)
        self.commands = commands
        self.keys = dict(keys or {})
        self.footer_text = footer
        # what the list is showing, as indexes into `self.names`
        self.visible_indexes: list[int] = list(range(len(self.names)))
        self._filter_text = ""

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Filter commands…", classes="column_filter")
        yield OptionList(classes="column_list")
        if self.footer_text:
            yield Static(self.footer_text, classes="column_list_footer")

    def on_mount(self) -> None:
        self.option_list = self.query_one(OptionList)
        self.option_list.can_focus = False
        self.filter_input = self.query_one(Input)
        self._populate(filter_text="")

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
        self.action_pick()

    @on(OptionList.OptionSelected)
    def handle_option_selected(self, message: OptionList.OptionSelected) -> None:
        message.stop()
        self.action_pick()

    def action_list_key(self, action: str) -> None:
        getattr(self.option_list, f"action_{action}")()

    def action_pick(self) -> None:
        highlighted = self.option_list.highlighted
        if highlighted is None or highlighted >= len(self.visible_indexes):
            return
        self.post_message(
            self.CommandPicked(self.names[self.visible_indexes[highlighted]])
        )

    def _populate(self, filter_text: str) -> None:
        self._filter_text = filter_text
        needle = filter_text.strip().lower()
        self.visible_indexes = [
            i
            for i, name in enumerate(self.names)
            if not needle
            or needle in name.lower()
            or needle in (self.commands[name].description or "").lower()
        ]
        self.option_list.clear_options()
        self.option_list.add_options(
            [Option(self._format(i)) for i in self.visible_indexes]
        )
        if self.visible_indexes:
            self.option_list.highlighted = 0
        self._set_title(matched=len(self.visible_indexes))

    def _format(self, index: int) -> Text:
        name = self.names[index]
        command = self.commands[name]
        label = Text(command.description or name)
        key = self.keys.get(name)
        if key:
            # the key leads the same way the section list's line number does: once you
            # know which command you want, the key is how you stop needing this list.
            label.append(f"  {key}", style="dim")
        return label

    def _set_title(self, matched: int) -> None:
        total = len(self.names)
        self.option_list.border_title = (
            f"Commands ({total})"
            if matched == total
            else f"Commands ({matched} of {total})"
        )


class CommandMenu(ModalScreen[str | None]):
    """Every configured command, over the whole app, dismissing with the one picked.

    A command is reachable three ways -- its own key, this menu, and the mouse -- so a
    config file that binds no keys at all is still usable, and a keyspace as full as
    Harlequin's does not decide how many commands a user may have.
    """

    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
    ]

    def __init__(
        self,
        commands: Mapping[str, CommandConfig],
        keys: Mapping[str, str] | None = None,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.commands = commands
        self.keys = keys or {}

    def compose(self) -> ComposeResult:
        with VerticalSuppressClicks(id="modal_outer"):
            yield CommandList(
                commands=self.commands,
                keys=self.keys,
                footer="Type to filter. ↑↓ move, Enter runs, Esc closes.",
            )

    def on_mount(self) -> None:
        self.query_one("#modal_outer").border_title = "Commands"
        self.query_one(CommandList).focus()

    @on(CommandList.CommandPicked)
    def handle_command_picked(self, message: CommandList.CommandPicked) -> None:
        message.stop()
        self.dismiss(message.name)

    def on_click(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
