"""Locating `-- ## Section name` blocks in SQL text.

A long working script is a handful of related queries, and the marker a person
already writes to separate them is a comment: `-- ## Daily revenue`. It
survives every SQL formatter, it is a comment to every engine, and it is
Markdown-ish enough to read as a heading. This module is the one place that
knows what a marker looks like, so the navigator, "focus section", "run
section" and anything else added later cannot disagree about where a section
starts.

Markers are found through the same tree-sitter grammar `statements.py` uses, so
`-- ## not a section` inside a string literal, a block comment or a
dollar-quoted body is content rather than a heading -- which a line-by-line
scan of the buffer cannot tell.

Offsets are **character** offsets into the text, for the same reason as in
`statements.py`: tree-sitter counts bytes, and every caller slices `str` or
hands a `(row, column)` to Textual's `Document`. The conversion is owned here.

There is no code folding in any of this. `textual-textarea` has none, and
adding it means changing the upstream widget's rendering model; "focus
section", which opens one section in its own tab and writes it back, is what
gives you the same view (roadmap §3.4).
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass

from harlequin.statements import Point, captures

SECTION_QUERY = "(comment) @comment"
"""Capture line comments.

The grammar makes a `comment` node only where a comment really is one, so a
line that looks like a marker inside a string, a block comment or a
dollar-quoted body is not captured and cannot become a section.
"""

MARKER = re.compile(r"^--[ \t]*(#{1,6})[ \t]+(\S.*?)[ \t]*$")
"""`-- ## Name`: two dashes, one to six hashes, whitespace, then the name.

The whitespace **between the hashes and the name** is what is required -- the
same rule Markdown uses for a heading, and the thing that keeps `-- #4 is
broken` and `-- #TODO` out of the navigator. The space after the dashes is
optional, so `--## Name` is a section too. Trailing whitespace is trimmed off
the name; indentation before the `--` is allowed and is not part of the marker
(the grammar reports where the comment starts, not where its line does).

More hashes make a deeper `level`, which the navigator indents by. The list
stays flat: a level 3 section is its own section, not a child of the level 2
one above it, because "run section" has to mean one unambiguous span of SQL.
"""

PREAMBLE_NAME = "(preamble)"
"""What the text before the first marker is called.

It is a section for every purpose that matters -- the cursor can be in it and
"run section" has to run it -- but it has no marker and so no name of its own.
"""


@dataclass(frozen=True)
class Section:
    """One `-- ## name` block, or the unmarked text before the first one."""

    name: str
    index: int
    """0-based position in the buffer."""
    level: int
    """How many hashes the marker had; 0 for the preamble."""
    start: int
    """Character offset of the start of the marker (the `-` of `--`)."""
    body_start: int
    """Character offset just past the marker's line, where the SQL begins."""
    end: int
    """Character offset just past the section: the next marker, or the end."""
    start_row: int
    """0-indexed row the marker is on; the row the cursor jumps to."""
    body_row: int
    """0-indexed row the SQL begins on."""

    @property
    def is_preamble(self) -> bool:
        return self.level == 0

    def contains(self, offset: int) -> bool:
        """Is `offset` inside this section?

        Half-open, so the offset at a marker belongs to the section the marker
        opens and not to the one it ends.
        """
        return self.start <= offset < self.end


def _line_starts(text: str) -> list[int]:
    """Character offset of the start of each row, plus the end of the text.

    Rows are those of `str.splitlines()`, which is how Textual's `Document`
    splits a buffer, so a row from here can be handed straight to it.
    """
    starts = [0]
    for line in text.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    return starts


def offset_of(text: str, point: Point) -> int:
    """The character offset of a `(row, column)` position, clamped to the text."""
    starts = _line_starts(text)
    row, column = point
    row = max(0, min(row, len(starts) - 1))
    return min(starts[row] + max(column, 0), len(text))


def point_of(text: str, offset: int) -> Point:
    """The `(row, column)` position of a character offset, clamped to the text."""
    starts = _line_starts(text)
    offset = max(0, min(offset, len(text)))
    row = max(bisect_right(starts, offset) - 1, 0)
    # a text ending in a newline gets a final `line_starts` entry with no row
    # of its own; the last real row is where that offset sits.
    row = min(row, max(len(starts) - 2, 0))
    return row, offset - starts[row]


def _char_offsets(text: str, byte_offsets: list[int]) -> list[int]:
    """Convert byte offsets to character offsets, in order.

    The gaps between offsets are decoded rather than the prefix of each, so a
    buffer with many markers stays linear in its length -- the same trick, and
    the same reason, as `statements._separator_offsets`.
    """
    if text.isascii():
        return byte_offsets
    encoded = text.encode("utf-8")
    offsets: list[int] = []
    byte_cursor = char_cursor = 0
    for byte_offset in byte_offsets:
        char_cursor += len(encoded[byte_cursor:byte_offset].decode("utf-8"))
        byte_cursor = byte_offset
        offsets.append(char_cursor)
    return offsets


def find_sections(text: str) -> list[Section]:
    """Every section in `text`, in buffer order.

    The text before the first marker is a section too, named `(preamble)`, so
    that the cursor is always in exactly one section -- but only when it holds
    something other than whitespace, so an ordinary script that starts with a
    marker has no phantom first entry.

    A buffer with no markers at all has no sections: there is nothing to
    navigate between, and the callers say so rather than offering one entry
    covering everything.
    """
    if "#" not in text:
        return []  # cheap, and the common case

    matched = captures(text, SECTION_QUERY)
    markers: list[tuple[int, str, int]] = []  # (byte offset, name, level)
    for node in sorted(matched.get("comment", []), key=lambda n: n.start_byte):
        match = MARKER.match(node.text.decode("utf-8", errors="replace"))
        if match is None:
            continue
        markers.append((node.start_byte, match.group(2), len(match.group(1))))
    if not markers:
        return []

    starts = _char_offsets(text, [byte_offset for byte_offset, _, _ in markers])
    line_starts = _line_starts(text)

    def row_of(offset: int) -> int:
        return min(max(bisect_right(line_starts, offset) - 1, 0), max(len(line_starts) - 2, 0))

    def next_row_start(offset: int) -> tuple[int, int]:
        """The offset and row where the line after `offset` begins."""
        row = row_of(offset)
        if row + 1 < len(line_starts):
            return line_starts[row + 1], min(row + 1, max(len(line_starts) - 2, 0))
        return len(text), row

    sections: list[Section] = []
    if text[: starts[0]].strip():
        body_offset, body_row = 0, 0
        sections.append(
            Section(
                name=PREAMBLE_NAME,
                index=0,
                level=0,
                start=0,
                body_start=body_offset,
                end=starts[0],
                start_row=0,
                body_row=body_row,
            )
        )
    for i, (start, (_, name, level)) in enumerate(zip(starts, markers)):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        body_start, body_row = next_row_start(start)
        sections.append(
            Section(
                name=name,
                index=len(sections),
                level=level,
                start=start,
                body_start=min(body_start, end),
                end=end,
                start_row=row_of(start),
                body_row=body_row,
            )
        )
    return sections


def section_at(sections: list[Section], offset: int) -> Section | None:
    """The section an offset is in, or None.

    An offset past the last section's end -- trailing whitespace after the last
    statement -- belongs to that last section, because that is where the cursor
    sits after typing the section's final line.
    """
    for section in sections:
        if section.contains(offset):
            return section
    if sections and offset >= sections[-1].end:
        return sections[-1]
    return None


def section_text(text: str, section: Section, include_marker: bool = True) -> str:
    """The section's text: its marker and body, or just its body."""
    start = section.start if include_marker else section.body_start
    return text[start : section.end]


def splice(
    text: str, span: tuple[int, int], replacement: str
) -> tuple[str, tuple[int, int]]:
    """`text` with `span` replaced, and the span the replacement now occupies.

    A newline is added when the replacement does not end in one and the span
    was not the last thing in the buffer, so whatever followed keeps its own
    line -- without that, writing an edited section back over the one above it
    would join its last statement to the next `-- ##` marker.

    The new span comes back because it is not `(start, start +
    len(replacement))` -- that added newline moves the end -- and because a
    caller writing into the same place again has to know where it is now.
    """
    start, end = span
    if end < len(text) and replacement and not replacement.endswith("\n"):
        replacement += "\n"
    return text[:start] + replacement + text[end:], (start, start + len(replacement))


def replace_section(
    text: str, section: Section, replacement: str
) -> tuple[str, tuple[int, int]]:
    """`text` with this section replaced, and the span the replacement occupies.

    The replacement covers the marker as well as the body, so a section edited
    in its own tab can be renamed there and the parent buffer follows.
    """
    return splice(text, (section.start, section.end), replacement)
