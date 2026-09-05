"""A directory another program drops queries and results into.

`harlequin --watch-dir DIR` polls DIR and offers to open the `*.sql` and
`*.csv` files it finds: the SQL as an editor buffer, the CSV as a result tab.
Nothing runs, nothing steals focus, and nothing is opened until the user asks
for it -- a directory some other process writes to is not allowed to change
what is on screen.

The contract is deliberately as small as a contract can be, because the
program on the other end of it is nobody's business here: **files directly in
DIR, named `<name>.sql` and `<name>.csv`, paired by name.** No manifest, no
envelope, no subdirectory to walk. A producer that wants a result to arrive
with its query renames the `.csv` into place first and the `.sql` last; a
producer that has only one of the two drops only that one. Anything else in
the directory -- another suffix, a subdirectory -- is ignored, so a producer
is free to keep its own bookkeeping alongside the files.

Two rules keep a half-written file off the screen:

* **A file has to hold still.** Nothing is offered until it has gone
  `MIN_AGE` seconds without changing, which covers both a slow copy and the
  moment between a producer's two renames.
* **Opening a file moves it** to `DIR/opened/`, so the same query is not
  offered twice and the buffer that shows it still has a real path behind it.
  A name already taken there gains `-2`, `-3`: the point of the directory is
  that nothing is lost in it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.csv
from textual_fastdatatable.backend import create_backend

from harlequin.query import ResultSet
from harlequin.statements import Statement

SUFFIXES = (".sql", ".csv")
"""What the watcher opens. Everything else in the directory is not its business."""

MIN_AGE = 0.5
"""Seconds an item has to go unchanged before it is offered.

Long enough to cover a copy or the gap between two renames, short enough that
nobody notices it. The alternative -- offering a file the instant it appears --
shows half a CSV and, worse, a query without the rows that came with it.

The rule is per *item*, not per file: a `.sql` that settled while its `.csv`
was still being copied is not ready, because opening it would hand over a query
without the rows a producer was in the middle of sending with it.
"""

OPENED = "opened"
"""Subdirectory an opened file is moved to. Also why the scan ignores directories."""


@dataclass
class WatchedItem:
    """One thing to open: a query, its rows, or both under one name."""

    name: str
    """The shared filename stem, which is what the buffer and the tab are called."""

    sql: Path | None
    csv: Path | None
    changed_at: float
    """The newer of the two mtimes, for ordering: oldest first, as a queue."""

    @property
    def paths(self) -> list[Path]:
        return [p for p in (self.sql, self.csv) if p is not None]


def opened_dir(watch_dir: Path) -> Path:
    return Path(watch_dir) / OPENED


def scan(
    watch_dir: Path, min_age: float = MIN_AGE, now: float | None = None
) -> list[WatchedItem]:
    """Everything in `watch_dir` that is ready to open, oldest first.

    Never raises: a watch directory that does not exist yet, or that the user
    cannot read, is empty as far as the app is concerned. The option is a
    standing instruction to look, not a promise that anything is there.
    """
    watch_dir = Path(watch_dir)
    now = time.time() if now is None else now
    found: dict[str, dict[str, Path]] = {}
    ages: dict[str, float] = {}
    try:
        entries = sorted(watch_dir.iterdir())
    except OSError:
        return []
    for entry in entries:
        if entry.suffix.lower() not in SUFFIXES:
            continue
        try:
            if not entry.is_file():
                continue
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        found.setdefault(entry.stem, {})[entry.suffix.lower()] = entry
        ages[entry.stem] = max(ages.get(entry.stem, 0.0), mtime)
    items = [
        WatchedItem(
            name=stem,
            sql=paths.get(".sql"),
            csv=paths.get(".csv"),
            changed_at=ages[stem],
        )
        for stem, paths in found.items()
        if now - ages[stem] >= min_age
    ]
    items.sort(key=lambda item: (item.changed_at, item.name))
    return items


def claim(path: Path, watch_dir: Path) -> Path:
    """Move one opened file into `opened/` and say where it went.

    The move is what makes the offer single-shot, and it happens *before* the
    file is read into a buffer so that a read that fails still does not leave
    the file to be offered again on the next poll. Returns the original path
    when the move is not possible, which keeps a read-only directory usable
    (at the cost of being offered its files again next time Harlequin starts).
    """
    path = Path(path)
    dest_dir = opened_dir(watch_dir)
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return path
    dest = dest_dir / path.name
    n = 2
    while dest.exists():
        dest = dest_dir / ("%s-%d%s" % (path.stem, n, path.suffix))
        n += 1
    try:
        path.replace(dest)
    except OSError:
        return path
    return dest


# Arrow's own type names, in the vocabulary the adapters' column labels use, so
# a CSV's columns are labelled the way a query's are. A type nobody mapped is
# `?`, which is what an adapter does with one too.
_ARROW_TYPE_LABELS = (
    (pa.types.is_boolean, "t/f"),
    (pa.types.is_decimal, "#.#"),
    (pa.types.is_floating, "#.#"),
    (pa.types.is_integer, "#"),
    (pa.types.is_date, "d"),
    (pa.types.is_time, "t"),
    (pa.types.is_timestamp, "ts"),
    (pa.types.is_duration, "|-|"),
    (pa.types.is_string, "s"),
    (pa.types.is_large_string, "s"),
    (pa.types.is_binary, "0b"),
    (pa.types.is_null, "\\n"),
    (pa.types.is_struct, "{}"),
    (pa.types.is_map, "{m}"),
    (pa.types.is_list, "[]"),
)

UNKNOWN_TYPE = "?"


def short_type(field_type: pa.DataType) -> str:
    for predicate, label in _ARROW_TYPE_LABELS:
        try:
            if predicate(field_type):
                return label
        except (TypeError, pa.ArrowNotImplementedError):  # pragma: no cover
            continue
    return UNKNOWN_TYPE


def result_set_from_csv(path: Path, max_rows: int | None = None) -> ResultSet:
    """A CSV as the same ResultSet a query would have produced.

    Arrow reads the file and infers the types, `create_backend` normalizes it
    exactly as `harlequin.query.fetch()` does, and the statement carries the
    file's name rather than SQL: there is no query behind a CSV, and inventing
    a `select * from 'x.csv'` would be a statement that does not run under most
    adapters. Raises whatever pyarrow raises -- an unreadable CSV is a real
    error for the caller to show.
    """
    path = Path(path)
    table = pyarrow.csv.read_csv(path)
    backend = create_backend(table, max_rows=max_rows)
    columns = [(field.name, short_type(field.type)) for field in table.schema]
    return ResultSet(
        statement=Statement(sql="-- %s" % path.name, index=0),
        columns=columns,
        backend=backend,
        truncated=(max_rows is not None and backend.source_row_count > max_rows),
        fetched_row_count=table.num_rows,
        elapsed=0.0,
    )
