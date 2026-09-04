"""`--theme` taking a path, not only a built-in name (roadmap §5.19).

Harlequin could only be handed a theme by name, so a terminal set up from a
generated palette could only be matched by whichever built-in was nearest --
close, never equal. A theme file closes that gap.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harlequin.colors import (
    VALID_THEMES,
    load_theme_file,
    looks_like_a_theme_file,
)
from harlequin.exception import HarlequinThemeError


@pytest.mark.parametrize(
    "value,expected",
    [
        ("harlequin", False),
        ("nord", False),
        ("monokai", False),
        ("bark.toml", True),
        ("bark.json", True),
        ("~/.config/ale/theme.toml", True),
        ("themes/bark", True),  # a path with no suffix is still a path
        ("./bark", True),
        ("bark", False),  # a bare word is a theme name, and an unknown one
        ("bark.txt", False),
    ],
)
def test_looks_like_a_theme_file(value: str, expected: bool) -> None:
    assert looks_like_a_theme_file(value) is expected


def test_a_built_in_name_is_never_read_as_a_path(tmp_path: Path) -> None:
    """A file called `nord.toml` next to you must not shadow the built-in."""
    for name in VALID_THEMES:
        assert looks_like_a_theme_file(name) is False


def test_load_a_toml_theme(tmp_path: Path) -> None:
    path = tmp_path / "bark.toml"
    path.write_text(
        "\n".join(
            [
                'name = "bark"',
                'primary = "#c26b4f"',
                'background = "#191110"',
                'foreground = "#f2e9e1"',
                "dark = true",
                "luminosity_spread = 0.2",
                "[variables]",
                '"block-cursor-foreground" = "#191110"',
            ]
        )
    )
    theme = load_theme_file(path)
    assert theme.name == "bark"
    assert theme.primary == "#c26b4f"
    assert theme.background == "#191110"
    assert theme.dark is True
    assert theme.luminosity_spread == 0.2
    assert theme.variables == {"block-cursor-foreground": "#191110"}


def test_load_a_json_theme(tmp_path: Path) -> None:
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"primary": "#123456", "dark": False}))
    theme = load_theme_file(path)
    assert theme.primary == "#123456"
    assert theme.dark is False


def test_the_file_name_names_the_theme(tmp_path: Path) -> None:
    """A generated palette should need no boilerplate beyond its colours."""
    path = tmp_path / "daltonized-dark.toml"
    path.write_text('primary = "#3d95d1"\n')
    assert load_theme_file(path).name == "daltonized-dark"


def test_an_int_is_accepted_where_a_float_is_wanted(tmp_path: Path) -> None:
    path = tmp_path / "t.toml"
    path.write_text('primary = "#fff"\ntext_alpha = 1\n')
    assert load_theme_file(path).text_alpha == 1.0


@pytest.mark.parametrize(
    "body,message",
    [
        ('name = "x"\n', "at least set `primary`"),
        ('primary = "#fff"\nflavour = "salt"\n', "unknown theme field"),
        ('primary = "#fff"\ndark = "yes"\n', "dark must be bool"),
        ('primary = "#fff"\n[variables]\nx = 3\n', "must be a table of strings"),
        ("primary = [\n", "could not be read"),
    ],
)
def test_a_bad_theme_file_says_what_is_wrong(
    tmp_path: Path, body: str, message: str
) -> None:
    path = tmp_path / "t.toml"
    path.write_text(body)
    with pytest.raises(HarlequinThemeError) as excinfo:
        load_theme_file(path)
    assert message in str(excinfo.value)


def test_a_missing_theme_file_is_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(HarlequinThemeError) as excinfo:
        load_theme_file(tmp_path / "nope.toml")
    assert "No theme file at" in str(excinfo.value)
