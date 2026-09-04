"""The `-- ## Section name` corpus.

`tests/functional_tests/test_sections.py` drives the same parser through the
navigator, "focus section" and "run section", so a divergence between the
parser and the front end fails here or there rather than in a user's buffer.
"""

from __future__ import annotations

import pytest

from harlequin.sections import (
    PREAMBLE_NAME,
    find_sections,
    offset_of,
    point_of,
    replace_section,
    section_at,
    section_text,
    splice,
)

# (name, script, expected [(name, level)]). The name is the pytest id.
CORPUS: list[tuple[str, str, list[tuple[str, int]]]] = [
    ("empty", "", []),
    ("whitespace only", "  \n\t ", []),
    ("no markers", "select 1;\nselect 2;", []),
    ("a hash but no marker", "select '#1' as x; -- #4 is broken", []),
    ("one marker", "-- ## One\nselect 1;", [("One", 2)]),
    (
        "text before the first marker becomes the preamble",
        "select 0;\n-- ## One\nselect 1;",
        [(PREAMBLE_NAME, 0), ("One", 2)],
    ),
    (
        "whitespace before the first marker does not",
        "\n\n-- ## One\nselect 1;",
        [("One", 2)],
    ),
    (
        "two markers",
        "-- ## One\nselect 1;\n-- ## Two\nselect 2;",
        [("One", 2), ("Two", 2)],
    ),
    ("an indented marker still counts", "  -- ## One\nselect 1;", [("One", 2)]),
    ("no space after the dashes", "--## One\nselect 1;", [("One", 2)]),
    ("one hash", "-- # One\nselect 1;", [("One", 1)]),
    ("six hashes", "-- ###### Deep\nselect 1;", [("Deep", 6)]),
    ("seven hashes is not a marker", "-- ####### Nope\nselect 1;", []),
    ("no space between hashes and name", "-- ##One\nselect 1;", []),
    ("a marker needs a name", "-- ##\nselect 1;", []),
    ("trailing whitespace is trimmed", "-- ## One   \nselect 1;", [("One", 2)]),
    (
        "a marker in a string literal is content",
        "select '-- ## Not a section' as x;",
        [],
    ),
    (
        "a marker in a block comment is content",
        "/* -- ## Not a section */\nselect 1;",
        [],
    ),
    (
        "a marker in a dollar-quoted body is content",
        "select $$\n-- ## Not a section\n$$;",
        [],
    ),
    (
        "a marker after a real one, inside a string, is still content",
        "-- ## One\nselect '-- ## Two';",
        [("One", 2)],
    ),
    ("a section with no SQL under it", "-- ## One\n-- ## Two\nselect 2;", [("One", 2), ("Two", 2)]),
    ("non-ascii before a marker", "select '日本語' as x;\n-- ## One\nselect 1;", [(PREAMBLE_NAME, 0), ("One", 2)]),
]


@pytest.mark.parametrize(
    "script,expected", [c[1:] for c in CORPUS], ids=[c[0] for c in CORPUS]
)
def test_find_sections(script: str, expected: list[tuple[str, int]]) -> None:
    sections = find_sections(script)
    assert [(s.name, s.level) for s in sections] == expected
    # indexes are the position in the list, and spans tile the buffer
    for i, section in enumerate(sections):
        assert section.index == i
        assert section.start < section.end
        assert section.body_start >= section.start
        if i:
            assert sections[i - 1].end == section.start
    if sections:
        assert sections[-1].end == len(script)


def test_spans_and_rows_are_characters_not_bytes() -> None:
    """The reason the parser converts: a `Point` is fed to Textual's Document."""
    script = "select '日本語' as x;\n-- ## One\nselect 1;\n"
    preamble, one = find_sections(script)
    assert script[one.start :].startswith("-- ## One")
    assert one.start_row == 1
    assert one.body_row == 2
    assert section_text(script, one, include_marker=False) == "select 1;\n"
    assert preamble.end == one.start


def test_section_at_puts_the_cursor_in_exactly_one_section() -> None:
    script = "select 0;\n-- ## One\nselect 1;\n-- ## Two\nselect 2;\n"
    sections = find_sections(script)
    assert section_at(sections, 0).name == PREAMBLE_NAME  # type: ignore[union-attr]
    assert section_at(sections, script.index("select 1")).name == "One"  # type: ignore[union-attr]
    assert section_at(sections, script.index("select 2")).name == "Two"  # type: ignore[union-attr]
    # a marker belongs to the section it opens, not the one it ends
    assert section_at(sections, script.index("-- ## Two")).name == "Two"  # type: ignore[union-attr]
    # and past the end -- the cursor after the last line -- is the last section
    assert section_at(sections, len(script) + 50).name == "Two"  # type: ignore[union-attr]
    assert section_at([], 0) is None


def test_offset_and_point_round_trip() -> None:
    script = "select '日本語';\n-- ## One\nselect 1;\n"
    for row, column in [(0, 0), (0, 8), (1, 3), (2, 9)]:
        assert point_of(script, offset_of(script, (row, column))) == (row, column)
    # both clamp rather than raise
    assert offset_of(script, (99, 99)) == len(script)
    assert point_of(script, -5) == (0, 0)


def test_replace_section_keeps_the_next_marker_on_its_own_line() -> None:
    script = "-- ## One\nselect 1;\n-- ## Two\nselect 2;\n"
    one, two = find_sections(script)
    updated, span = replace_section(script, one, "-- ## Renamed\nselect 99;")
    assert updated == "-- ## Renamed\nselect 99;\n-- ## Two\nselect 2;\n"
    # the span is where the replacement now is, newline included
    assert updated[span[0] : span[1]] == "-- ## Renamed\nselect 99;\n"
    # the last section is not padded: there is nothing after it to protect
    updated, _ = replace_section(script, two, "-- ## Two\nselect 22;")
    assert updated.endswith("select 22;")


def test_replace_section_renames_in_the_parent() -> None:
    """Editing the marker in a focused section renames it where it came from."""
    script = "-- ## One\nselect 1;\n"
    (one,) = find_sections(script)
    updated, _ = replace_section(script, one, "-- ## Something else\nselect 1;\n")
    assert [s.name for s in find_sections(updated)] == ["Something else"]


def test_splice_pads_only_when_something_follows() -> None:
    # mid-buffer: the newline goes in, so what follows keeps its own line
    assert splice("abc", (1, 1), "X") == ("aX\nbc", (1, 3))
    # at the end of the buffer, and into an empty buffer: no padding
    assert splice("abc", (3, 3), "X") == ("abcX", (3, 4))
    assert splice("", (0, 0), "X") == ("X", (0, 1))
    # a replacement that already ends in a newline is left alone
    assert splice("abc", (1, 1), "X\n") == ("aX\nbc", (1, 3))
