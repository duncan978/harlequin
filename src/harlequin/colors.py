from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from questionary import Style as QuestionaryStyle
from textual.theme import BUILTIN_THEMES
from textual.theme import Theme as TextualTheme

from harlequin.exception import HarlequinThemeError

GREEN = "#45FFCA"
YELLOW = "#FEFFAC"
PINK = "#FFB6D9"
PURPLE = "#D67BFF"
GRAY = "#777777"
DARK_GRAY = "#555555"
BLACK = "#0C0C0C"
WHITE = "#DDDDDD"

HARLEQUIN_TEXTUAL_THEME = TextualTheme(
    name="harlequin",
    primary=YELLOW,
    secondary=GREEN,
    warning=YELLOW,
    error=PINK,
    success=GREEN,
    accent=PINK,
    foreground=WHITE,
    background=BLACK,
    surface=BLACK,
    panel=DARK_GRAY,
    dark=True,
)

VALID_THEMES = BUILTIN_THEMES
# Harlequin doesn't support ANSI-passthrough themes.
VALID_THEMES.pop("ansi-dark", None)
VALID_THEMES.pop("ansi-light", None)
VALID_THEMES.update({"harlequin": HARLEQUIN_TEXTUAL_THEME})

# -- themes from a file ----------------------------------------------------
# `--theme` (and the `theme` config key) takes the name of a built-in theme or the PATH of a
# theme file. A path is how you match Harlequin to a palette that is generated rather than
# chosen from a list: the terminal, the multiplexer and the editor can all be given exact hex
# values, and until this existed Harlequin could only be given the nearest built-in name.
#
# The file is TOML or JSON, one table of the fields of a Textual theme:
#
#     name = "bark"              # optional; the file's stem is used when it is absent
#     primary = "#c26b4f"
#     background = "#191110"
#     foreground = "#f2e9e1"
#     dark = true
#     [variables]                # optional; any Textual design token, verbatim
#     "block-cursor-foreground" = "#191110"
#
# `primary` is the only required colour, as in Textual. Everything else Textual derives.

THEME_FILE_SUFFIXES = (".toml", ".json")

_THEME_FIELDS: dict[str, type] = {
    "name": str,
    "primary": str,
    "secondary": str,
    "warning": str,
    "error": str,
    "success": str,
    "accent": str,
    "foreground": str,
    "background": str,
    "surface": str,
    "panel": str,
    "boost": str,
    "dark": bool,
    "luminosity_spread": float,
    "text_alpha": float,
}


def looks_like_a_theme_file(value: str) -> bool:
    """Is this `--theme` value a path rather than the name of a built-in theme?

    A name is a bare word, so anything carrying a directory separator or a theme-file
    suffix is a path -- including one that does not exist, so that a typo'd path is
    reported as a missing file instead of as an unknown theme name.
    """
    if value in VALID_THEMES:
        return False
    if os.sep in value or (os.altsep and os.altsep in value):
        return True
    return Path(value).suffix.lower() in THEME_FILE_SUFFIXES


def _read_theme_data(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        import json

        data = json.loads(text)
    else:
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli as tomllib  # type: ignore[no-redef]
        data = tomllib.loads(text)
    if not isinstance(data, dict):
        raise HarlequinThemeError(
            f"{path} does not hold a table of theme fields.",
            title="Harlequin couldn't load your theme.",
        )
    return data


def load_theme_file(path: Path | str) -> TextualTheme:
    """Build a Textual theme from a theme file. Raises HarlequinThemeError."""
    path = Path(path).expanduser()
    try:
        data = _read_theme_data(path)
    except HarlequinThemeError:
        raise
    except FileNotFoundError:
        raise HarlequinThemeError(
            f"No theme file at {path}.",
            title="Harlequin couldn't load your theme.",
        ) from None
    except (OSError, ValueError) as e:
        raise HarlequinThemeError(
            f"{path} could not be read as a theme file: {e}",
            title="Harlequin couldn't load your theme.",
        ) from None

    variables = data.pop("variables", {})
    if not isinstance(variables, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in variables.items()
    ):
        raise HarlequinThemeError(
            f"{path}: [variables] must be a table of strings.",
            title="Harlequin couldn't load your theme.",
        )

    unknown = sorted(set(data) - set(_THEME_FIELDS))
    if unknown:
        raise HarlequinThemeError(
            f"{path}: unknown theme field(s) {', '.join(unknown)}.\n"
            f"A theme file may set: {', '.join(_THEME_FIELDS)}, and [variables].",
            title="Harlequin couldn't load your theme.",
        )

    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        expected = _THEME_FIELDS[key]
        if expected is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if not isinstance(value, expected) or (
            expected is not bool and isinstance(value, bool)
        ):
            raise HarlequinThemeError(
                f"{path}: {key} must be {expected.__name__}, not "
                f"{type(value).__name__}.",
                title="Harlequin couldn't load your theme.",
            )
        kwargs[key] = value

    # The file's stem names the theme when the file does not, so a palette dropped in a
    # directory needs no boilerplate. A theme with no name at all cannot be registered.
    kwargs.setdefault("name", path.stem)
    if not kwargs["name"]:
        raise HarlequinThemeError(
            f"{path}: the theme needs a name (or a file name to take one from).",
            title="Harlequin couldn't load your theme.",
        )
    if "primary" not in kwargs:
        raise HarlequinThemeError(
            f"{path}: a theme must at least set `primary`.",
            title="Harlequin couldn't load your theme.",
        )
    return TextualTheme(variables=variables, **kwargs)


HARLEQUIN_QUESTIONARY_STYLE = (
    QuestionaryStyle(
        [
            ("qmark", "fg:ansidefault bold"),
            ("question", "fg:ansidefault nobold"),
            ("answer", "fg:ansidefault bold"),
            ("pointer", "fg:ansidefault bold"),
            ("highlighted", "fg:ansidefault bold"),
            ("selected", "fg:ansidefault noreverse bold"),
            ("separator", "fg:ansidefault"),
            ("instruction", "fg:ansidefault italic"),
            ("text", ""),
            ("disabled", "fg:ansidefault italic"),
        ]
    )
    if os.getenv("NO_COLOR")
    else QuestionaryStyle(
        [
            ("qmark", f"fg:{GREEN} bold"),
            ("question", "bold"),
            ("answer", f"fg:{YELLOW} bold"),
            ("pointer", f"fg:{YELLOW} bold"),
            ("highlighted", f"fg:{YELLOW} bold"),
            ("selected", f"fg:{YELLOW} noreverse bold"),
            ("separator", f"fg:{PURPLE}"),
            ("instruction", "fg:#858585 italic"),
            ("text", ""),
            ("disabled", "fg:#858585 italic"),
        ]
    )
)
