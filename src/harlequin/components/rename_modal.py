from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label


class RenameModal(ModalScreen["str | None"]):
    """Asks what to call a tab.

    Dismisses with the new name, with `""` to mean "go back to the default
    label", or with `None` when the rename was abandoned -- three outcomes the
    caller has to tell apart, since an empty name is a real instruction.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, prompt: str, current: str = "") -> None:
        super().__init__()
        self.prompt = prompt
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="outer"):
            yield Label(self.prompt, id="prompt_label")
            yield Input(value=self.current, id="rename_input")
            yield Label(
                "enter to save · empty name restores the default · esc to cancel",
                id="hint_label",
            )

    def on_mount(self) -> None:
        text_input = self.query_one(Input)
        text_input.focus()
        # land after the existing name rather than on top of it: a rename is
        # more often an edit of the old name than a replacement of it.
        text_input.action_end()

    def on_input_submitted(self, message: Input.Submitted) -> None:
        message.stop()
        self.dismiss(message.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)
