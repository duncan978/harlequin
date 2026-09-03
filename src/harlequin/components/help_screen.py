from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from textual import events
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Markdown, Static

from harlequin.components.text_modal import VerticalSuppressClicks

if TYPE_CHECKING:
    from harlequin.keymap import HarlequinKeyMap

SECTIONS: list[tuple[str, str]] = [
    ("", "Anywhere in Harlequin"),
    ("code_editor", "Query Editor"),
    ("data_catalog", "Data Catalog"),
    ("results_viewer", "Results Viewer"),
    ("history_screen", "Query History"),
]
"""Action namespace -> heading, in the order the page reads. A namespace is
also where a binding is live: `results_viewer` keys only do anything while
the results grid has focus, which is the thing this page most needs to say.
"""

NAMED_KEYS: dict[str, str] = {
    "escape": "esc",
    "enter": "⏎",
    "space": "space",
    "tab": "tab",
    "pageup": "pgup",
    "pagedown": "pgdn",
    "up": "↑",
    "down": "↓",
    "left": "←",
    "right": "→",
    "home": "home",
    "end": "end",
    "backspace": "⌫",
    "delete": "del",
    "insert": "ins",
    "full_stop": ".",
    "underscore": "_",
    "minus": "-",
    "plus": "+",
    "equals_sign": "=",
    "comma": ",",
}

MODIFIERS: list[tuple[str, str]] = [("ctrl+", "^"), ("shift+", "⇧"), ("alt+", "⌥")]


def humanize_key(key: str) -> str:
    """One virtual key name as it is printed on a keyboard."""
    prefix = ""
    changed = True
    while changed:
        changed = False
        for token, symbol in MODIFIERS:
            if key.startswith(token):
                prefix += symbol
                key = key[len(token) :]
                changed = True
    return f"{prefix}{NAMED_KEYS.get(key, key)}"


def humanize_action(action: str) -> str:
    """A label for an action, preferring the one the action declares.

    Most actions declare no description -- they are footer-invisible -- so the
    fallback reads the name, which is written to be read: `cursor_word_left`
    is already "Cursor Word Left".
    """
    # imported here: `harlequin.actions` imports this package, so importing it
    # at module scope closes the cycle
    from harlequin.actions import HARLEQUIN_ACTIONS

    declared = HARLEQUIN_ACTIONS.get(action)
    if declared is not None and declared.description:
        return declared.description
    _, _, leaf = action.rpartition(".")
    return leaf.replace("_", " ").title()


def _section(
    heading: str, rows: list[tuple[str, str]], note: str | None = None
) -> list[str]:
    """One heading and its bindings, as a fenced block.

    A Markdown table would draw a rule between every row, which doubles the
    height of a page that is already 130 bindings long. A code fence is
    monospaced, so the keys line up without one.
    """
    width = max((len(keys) for keys, _ in rows), default=0)
    lines = [f"### {heading}", ""]
    if note:
        lines.extend([note, ""])
    lines.append("```")
    lines.extend(f"{keys:<{width}}   {label}" for keys, label in rows)
    lines.extend(["```", ""])
    return lines


def keymap_markdown(keymaps: Iterable["HarlequinKeyMap"]) -> str:
    """Render the bindings actually in force as a Markdown page.

    Generated rather than written down: the page is only worth having if it
    cannot disagree with the keymap, including keys added locally.
    """
    # action -> keys, last keymap winning, which is the order they are bound in
    bindings: dict[str, str] = {}
    order: list[str] = []
    for keymap in keymaps:
        for binding in keymap.bindings:
            if binding.action not in bindings:
                order.append(binding.action)
            bindings[binding.action] = binding.key_display or " / ".join(
                humanize_key(key) for key in binding.keys.split(",")
            )

    lines: list[str] = []
    claimed: set[str] = set()
    for namespace, heading in SECTIONS:
        rows = [
            action
            for action in order
            if action.rpartition(".")[0] == namespace and action not in claimed
        ]
        if not rows:
            continue
        claimed.update(rows)
        lines.extend(_section(heading, [(bindings[a], humanize_action(a)) for a in rows]))

    leftovers = [action for action in order if action not in claimed]
    if leftovers:
        lines.extend(
            _section(
                "Other", [(bindings[a], humanize_action(a)) for a in leftovers]
            )
        )

    if not lines:
        lines = ["No key bindings are loaded."]

    # the column list carries its own bindings rather than keymap actions, so
    # nothing above can find them
    lines.extend(
        _section(
            "Column List",
            [
                ("any text", "Filter by column name or type"),
                ("↑ / ↓ / pgup / pgdn", "Move the highlight"),
                ("⏎", "Jump the grid to the highlighted column"),
                ("^y", "Copy the filtered column names"),
                ("esc", "Close (the c modal only)"),
            ],
            note=(
                "The Data Catalog's Columns tab, and the same list over the "
                "grid with `c`."
            ),
        )
    )

    lines.append("---")
    lines.append("")
    lines.append(
        "Keymaps are set with `--keymap-name` or a `keymap` table in your "
        "config file; see https://harlequin.sh/docs/keymaps. The rest of the "
        "docs are at https://harlequin.sh/docs/getting-started."
    )
    return "\n".join(lines)


class HelpScreen(ModalScreen):
    header_text = """
        Key bindings currently in force. Bindings under a heading only apply
        while that pane has focus.
    """.split()

    def compose(self) -> ComposeResult:
        with VerticalSuppressClicks(id="modal_outer"):
            yield Static(" ".join(self.header_text), id="modal_header")
            with VerticalScroll(id="modal_inner"):
                yield Markdown(markdown=keymap_markdown(self._active_keymaps()))
            yield Static(
                "Scroll with arrows. Press any other key to continue.",
                id="modal_footer",
            )

    def on_mount(self) -> None:
        container = self.query_one("#modal_outer")
        names = getattr(self.app, "keymap_names", None)
        container.border_title = (
            f"Harlequin Keys ({', '.join(names)})" if names else "Harlequin Keys"
        )
        self.body = self.query_one("#modal_inner")

    def _active_keymaps(self) -> list["HarlequinKeyMap"]:
        """The keymaps the app bound, in the order it bound them."""
        all_keymaps = getattr(self.app, "all_keymaps", {})
        names = getattr(self.app, "keymap_names", ())
        return [all_keymaps[name] for name in names if name in all_keymaps]

    def on_key(self, event: events.Key) -> None:
        event.stop()
        if event.key == "up":
            self.body.scroll_up()
        elif event.key == "down":
            self.body.scroll_down()
        elif event.key == "left":
            self.body.scroll_left()
        elif event.key == "right":
            self.body.scroll_right()
        elif event.key == "pageup":
            self.body.scroll_page_up()
        elif event.key == "pagedown":
            self.body.scroll_page_down()
        else:
            self.app.pop_screen()

    def on_click(self) -> None:
        self.app.pop_screen()
