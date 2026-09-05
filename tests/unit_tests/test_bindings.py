"""The default keymap binds each key once, and nothing else has already taken it.

Reading the keymap is not enough to know a key is free, and getting that wrong
twice in a row is what prompted this file. `f11` is taken by *macOS* (Show
Desktop) and never reaches the terminal at all. `ctrl+p` is taken by *Textual*,
which opens its command palette from `App` whatever a keymap says. Neither
appears in the keymap. And `ctrl+shift+o` is taken by nothing yet still does not
work through Ghostty + tmux, for the reason below.

`$WORKBENCH/bin/ale-keys --probe` is what measures any of this on a given
terminal; these tests are what stop the answers being forgotten.
"""

from __future__ import annotations

import pytest
from textual.app import App

from harlequin_vscode import VSCODE

# Keys an operating system, a terminal or Textual itself takes first, whatever a
# keymap says. Add to this when one bites, with the reason -- the list is the
# institutional memory for "why is this key not that key".
RESERVED = {
    "ctrl+p": "Textual's own command palette (App.COMMAND_PALETTE_BINDING)",
    "f11": "macOS Show Desktop; the key never reaches the terminal",
    "ctrl+shift+p": "many terminals send this as ctrl+p",
}

# `ctrl+shift+X` is not reserved -- it is *unreliable*, which is a different
# problem and needs a different rule.
#
# A modified arrow, Home, End, Page key or f-key has had a legacy encoding since
# xterm (`CSI 1 ; mod D` and friends), so `ctrl+shift+left` arrives everywhere --
# which is why upstream's own selection bindings are fine. `ctrl+shift` with a
# *letter*, or with Enter, has no legacy form at all: it needs CSI-u, which means
# the terminal must encode it and, inside tmux, tmux must forward it. Measured on
# Ghostty + tmux 3.7c with `extended-keys on`, it does not -- the shift is
# dropped, `ctrl+shift+o` arrives as `ctrl+o`, the binding never fires, and Open
# Query runs instead. Re-measured 2026-09-04 with `tmux set -s extended-keys
# always`, which forwards extended keys whether or not the app asked: the
# ctrl+shift chords still do not arrive, so there is no tmux option that buys
# them and this rule is not provisional.
#
# `alt`+letter, measured the same day on the same stack, does arrive -- which is
# the channel a modified chord has here, and what the IDE's configured commands
# are bound on.
#
# So a ctrl+shift+letter chord may be *a* spelling of an action, never its only
# one. Pair it with an ASCII control chord, which no layer can lose.

_NEEDS_CSI_U_BASES = {"enter", "tab", "space", "backspace"}


def _needs_csi_u(key: str) -> bool:
    """True for a ctrl+shift chord with no legacy encoding to fall back on."""
    if not key.startswith("ctrl+shift+"):
        return False
    base = key[len("ctrl+shift+") :]
    return len(base) == 1 or base in _NEEDS_CSI_U_BASES


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


def test_the_csi_u_rule_matches_the_keys_it_is_about() -> None:
    assert _needs_csi_u("ctrl+shift+o")
    assert _needs_csi_u("ctrl+shift+enter")
    # a modified arrow has a legacy encoding, so it is not in this class
    assert not _needs_csi_u("ctrl+shift+left")
    assert not _needs_csi_u("ctrl+shift+home")
    assert not _needs_csi_u("ctrl+o")


def test_no_action_depends_only_on_a_ctrl_shift_letter() -> None:
    by_action: dict[str, list[str]] = {}
    for key, action in _keys():
        by_action.setdefault(action, []).append(key)
    orphans = [
        "%s is only reachable by %s" % (action, keys)
        for action, keys in by_action.items()
        if keys and all(_needs_csi_u(k) for k in keys)
    ]
    assert not orphans, (
        "ctrl+shift with a letter or Enter needs CSI-u, which Ghostty + tmux with "
        "extended-keys on does not deliver, so it must not be an action's only "
        "spelling. Pair it with an ASCII control chord:\n" + "\n".join(orphans)
    )


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


# --- which keys the footer lists ---------------------------------------------
#
# A footer with thirty keys in it is a footer nobody reads, and the keys worth
# listing are not the keys worth binding. `show` on a binding is how a keymap
# adds ten commands and puts one of them on screen.


def test_a_binding_says_whether_the_footer_lists_it() -> None:
    from harlequin.actions import Action
    from harlequin.app import _footer_slot
    from harlequin.keymap import HarlequinKeyBinding

    quiet_action = Action(target=None, action="a", show=False)
    loud_action = Action(target=None, action="a", show=True)

    def binding(**kwargs: object) -> HarlequinKeyBinding:
        return HarlequinKeyBinding(keys="alt+x", action="a", **kwargs)  # type: ignore[arg-type]

    # the action decides when the binding says nothing
    assert _footer_slot(binding(), quiet_action) is False
    assert _footer_slot(binding(), loud_action) is True
    # a key_display still implies "show me", as it did before `show` existed
    assert _footer_slot(binding(key_display="alt+x"), quiet_action) is True
    # and the binding overrules both, in either direction
    assert _footer_slot(binding(show=False, key_display="alt+x"), quiet_action) is False
    assert _footer_slot(binding(show=False), loud_action) is False
    assert _footer_slot(binding(show=True), quiet_action) is True


def test_a_binding_written_back_to_config_keeps_only_what_it_was_given() -> None:
    from harlequin.keymap import HarlequinKeyBinding

    bare = HarlequinKeyBinding(keys="alt+x", action="a").to_dict()
    assert bare == {"keys": "alt+x", "action": "a"}
    full = HarlequinKeyBinding(
        keys="alt+x", action="a", key_display="⌥x", show=False
    ).to_dict()
    assert full == {
        "keys": "alt+x",
        "action": "a",
        "key_display": "⌥x",
        "show": False,
    }, "show = false must survive a round trip, or the footer fills up again"


def test_a_keymap_from_config_may_set_show() -> None:
    from harlequin.keymap import HarlequinKeyMap

    keymap = HarlequinKeyMap.from_config(
        name="workbench",
        bindings=[
            {"keys": "alt+c", "action": "show_command_menu", "key_display": "alt+c"},
            {"keys": "alt+b", "action": "command.x", "show": False},
        ],
    )
    assert keymap.bindings[0].show is None
    assert keymap.bindings[1].show is False


def test_every_alt_chord_bound_to_an_app_action_has_priority() -> None:
    """An `alt` chord that is not `priority` is a key that mostly does nothing.

    A focused `TextArea` inserts any key carrying a printable character, and a
    terminal's alt+i carries an "i". Textual checks the focused widget's bindings
    before a non-priority `App` binding, so the editor -- which is what has focus
    almost all the time -- swallows the chord and types the letter instead.

    `open_watched` shipped without it in +insurify.18 and alt+i typed an "i" into
    the buffer, which is what this test exists to stop happening a third time
    (`launch_external_editor` was the first).
    """
    from harlequin.actions import HARLEQUIN_ACTIONS

    offenders = []
    for binding in VSCODE.bindings:
        if not any(k.strip().startswith("alt+") for k in binding.keys.split(",")):
            continue
        action = HARLEQUIN_ACTIONS.get(binding.action)
        if action is None or action.target is not None:
            continue
        if not action.priority:
            offenders.append(f"{binding.keys} -> {binding.action}")
    assert not offenders, (
        "app-level alt bindings without priority=True; a focused editor will "
        f"swallow these and insert the letter: {offenders}"
    )
