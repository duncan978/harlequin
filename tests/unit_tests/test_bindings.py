# TODO!
"""The default keymap binds each key once, and nothing Textual has already taken.

Picking a key for a new action by reading the keymap is not enough, and choosing
one badly twice is what prompted this: `f11` is bound by *macOS* (Show Desktop)
before a terminal ever sees it, and `ctrl+p` is bound by *Textual*, which opens
its command palette from `App` regardless of what a keymap says. Neither shows up
in the keymap, so the keymap cannot be the only thing consulted.
"""

from __future__ import annotations

import pytest
from textual.app import App

from harlequin_vscode import VSCODE

# Keys a terminal, an operating system or Textual itself takes first, whatever a
# keymap says. Add to this when one bites, with the reason -- the list is the
# institutional memory for "why is this key not that key".
RESERVED = {
    "ctrl+p": "Textual's own command palette (App.COMMAND_PALETTE_BINDING)",
    "f11": "macOS Show Desktop; the key never reaches the terminal",
    "ctrl+shift+p": "many terminals send this as ctrl+p",
}


def _keys() -> list[tuple[str, str]]:
    """(key, action) for every key the default keymap binds."""
    return [
        (key.strip(), binding.action)
        for binding in VSCODE.bindings
        for key in binding.keys.split(",")
        if key.strip()
    ]


def test_the_command_palette_binding_is_what_we_think_it_is() -> None:
    """If Textual moves it, RESERVED above is wrong and should be corrected."""
    assert App.COMMAND_PALETTE_BINDING in RESERVED


@pytest.mark.parametrize("key,reason", sorted(RESERVED.items()))
def test_the_keymap_avoids_reserved_keys(key: str, reason: str) -> None:
    bound = [action for bound_key, action in _keys() if bound_key == key]
    assert not bound, f"{key} is bound to {bound}, but it is taken by {reason}"


def test_no_key_is_bound_twice_in_the_same_context() -> None:
    """Two actions on one key in one widget means one of them never fires."""
    seen: dict[tuple[str, str], str] = {}
    clashes: list[str] = []
    for key, action in _keys():
        # the context is the action's prefix: app-level actions and, say, the
        # results viewer's own `c` are not in competition with each other.
        context = action.rpartition(".")[0]
        previous = seen.get((context, key))
        if previous is not None and previous != action:
            clashes.append(f"{key} in {context or 'app'}: {previous} and {action}")
        seen[(context, key)] = action
    assert not clashes, "\n".join(clashes)
