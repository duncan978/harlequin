from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from typing_extensions import NotRequired

from harlequin.exception import HarlequinConfigError

# TODO: ADD VALIDATION when creating bindings from config


class RawKeyBinding(TypedDict):
    keys: str
    action: str
    key_display: NotRequired[str]
    show: NotRequired[bool]


class RawKeyMap(TypedDict):
    name: str
    bindings: list[RawKeyBinding]


@dataclass
class HarlequinKeyBinding:
    keys: str
    """Comma-separated list of virtual key names."""
    action: str
    """The name of an action. Must be a key of harlequin.actions.HARLEQUIN_ACTIONS"""
    key_display: str | None = None
    """If specified, overrides the key display in Harlequin footer for this binding."""
    show: bool | None = None
    """Whether the footer lists this key. `None` leaves it to the action, which is what
    every binding did before this field existed.

    A footer with thirty keys in it is a footer nobody reads, and the keys worth listing
    are not the same as the keys worth binding: a keymap that adds ten commands wants
    one or two of them on screen and the rest reachable. `show = false` is how a binding
    exists without spending a footer slot -- including a binding that sets
    `key_display`, which used to force itself into the footer whatever the action said.
    """

    def to_dict(self) -> RawKeyBinding:
        """
        Returns a dictionary that can be written to a TOML config file.
        """
        all_keys: RawKeyBinding = dict(self.__dict__)  # type: ignore[assignment]
        for optional in ("key_display", "show"):
            if all_keys.get(optional) is None:
                all_keys.pop(optional, None)  # type: ignore[misc]
        return all_keys


@dataclass
class HarlequinKeyMap:
    name: str
    bindings: list[HarlequinKeyBinding]

    @classmethod
    def from_config(cls, name: str, bindings: list[RawKeyBinding]) -> "HarlequinKeyMap":
        try:
            keymap = cls(
                name=name,
                bindings=[HarlequinKeyBinding(**binding) for binding in bindings],
            )
        except TypeError as e:
            bad_key = str(e).split("argument ")[-1].strip("'")
            raise HarlequinConfigError(
                title="Harlequin could not load your keymap.",
                msg=(
                    "Key bindings must be defined in config files with "
                    "only these properties: `keys`, `action`, `key_display`, "
                    "and `show`. "
                    f"Got a binding in the map named {name} that tried to define "
                    f"a property: {bad_key!r}"
                ),
            ) from e
        return keymap

    def to_dict(self) -> RawKeyMap:
        return {"name": self.name, "bindings": [b.to_dict() for b in self.bindings]}
