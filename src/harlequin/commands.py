"""Running a program the user configured, with the editor or the results as context.

A `[commands.<name>]` table in a config file says what to run and what to hand it:

    [commands.send_results]
    command     = ["my-tool", "--kind", "results"]
    description = "Send results to my tool"
    stdin       = "results"
    output      = "notify"
    timeout     = 15
    max_rows    = 200

The program gets its context on **stdin** -- the selected text, the statement under the
cursor, the whole buffer, or a JSON manifest naming CSV files written to a temp
directory
for this invocation -- and a handful of `HARLEQUIN_*` variables in its environment. What
it writes on stdout is applied according to `output`.

This module owns the process and the serialization; the app owns the widgets the context
is gathered from and the result is applied to, so nothing here imports Textual outside
`TYPE_CHECKING` -- the same division `external.py` keeps.

Harlequin does not know what any of these programs do. A command is run from a key or
the
menu and never automatically, the first run in a process asks the user to confirm it (a
config file must not be able to approve its own subprocesses), and the child inherits
Harlequin's environment -- so it has the credentials the user already gave, and nothing
here ever serializes or logs that environment.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

if TYPE_CHECKING:
    import pyarrow as pa

TERMINATE_GRACE_SECONDS = 2.0
"""How long a timed-out child is given to die politely before it is killed."""

STDIN_SOURCES = (
    "none",
    "selection",
    "statement",
    "section",
    "buffer",
    "results",
    "pinned_results",
)
"""What a command can ask to be given. `results`/`pinned_results` carry a manifest."""

OUTPUT_MODES = ("none", "notify", "replace", "insert", "new-buffer")
"""What is done with stdout. Empty stdout is never applied, in any mode: a tool that
returned nothing must not blank the query it was given."""

RESULT_STDIN_SOURCES = ("results", "pinned_results")


@dataclass
class CommandResult:
    """How a child exited, and what it said."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def first_line(self) -> str:
        """What a `notify` output mode shows: the first line, for a toast."""
        for line in self.stdout.splitlines():
            if line.strip():
                return line.strip()
        return ""


@dataclass
class TableSnapshot:
    """One result table, as plain data the worker can be handed.

    Gathered on the main thread from the widget; an Arrow table is immutable, so this is
    safe to serialize anywhere, but nothing in here is a widget either way.
    """

    pane_id: str
    sql: str
    columns: list[tuple[str, str]]
    data: "pa.Table"
    fetched_row_count: int | None = None
    fetch_truncated: bool = False
    label: str | None = None
    pinned: bool = False
    elapsed: float | None = None


@dataclass
class CommandInvocation:
    """Everything a worker needs to run one command, and nothing else.

    No widgets, no app: the worker thread gets this and returns a `CommandResult`.
    """

    name: str
    argv: list[str]
    env: dict[str, str]
    stdin: bytes = b""
    timeout: float = 120.0
    tmpdir: str | None = None
    """Removed when the child exits; the command must copy what it wants to keep."""
    files: list[str] = field(default_factory=list)


def build_env(
    *,
    name: str,
    stdin_source: str,
    version: str,
    profile: str | None = None,
    adapter: str | None = None,
    buffer_name: str | None = None,
    buffer_path: str | None = None,
) -> dict[str, str]:
    """The `HARLEQUIN_*` variables a command is told about.

    Only these: the child inherits the rest of Harlequin's environment, which holds
    whatever credentials the user's own shell put there, and none of it is described,
    copied or logged here. `HARLEQUIN_CONN_STR` is deliberately absent -- the app does
    not
    hold a conn_str, and a DSN carries a password.

    Every value is a string, and an unknown one is empty rather than missing, so a shell
    script can read `$HARLEQUIN_PROFILE` without testing whether it is set.
    """
    return {
        "HARLEQUIN_COMMAND": name,
        "HARLEQUIN_STDIN": stdin_source,
        "HARLEQUIN_VERSION": version,
        "HARLEQUIN_PROFILE": profile or "",
        "HARLEQUIN_ADAPTER": adapter or "",
        "HARLEQUIN_BUFFER_NAME": buffer_name or "",
        "HARLEQUIN_BUFFER_PATH": buffer_path or "",
    }


def results_manifest(
    tables: Sequence[TableSnapshot],
    *,
    stdin_source: str,
    tmpdir: Path,
    max_rows: int | None = None,
) -> tuple[str, list[str]]:
    """Write each table to CSV under `tmpdir` and return the manifest that names them.

    One JSON document, whether there is one table or seven, so the receiving side has
    one
    parser and one shape. The payload goes on disk rather than through the pipe, and the
    metadata a CSV cannot carry -- the types the database reported, how many rows were
    fetched, whether the answer is complete, how long it took -- travels with it.

    `truncated` is the one flag a consumer has to check: it means the CSV is not the
    whole
    answer, either because the Run Query Bar's limit stopped the fetch or because
    `max_rows` capped what was written. Nothing is sampled or summarized: 200 rows of
    500
    are rows 1-200.
    """
    import json

    from harlequin.export import write_file

    entries: list[dict[str, Any]] = []
    written: list[str] = []
    for snapshot in tables:
        data = snapshot.data
        # The overflow probe row is not part of the answer: under the Run Query Bar's
        # limit the fetch asks for one row more than the limit, to learn there were
        # more.
        # `export_callback` drops it the same way.
        if snapshot.fetch_truncated and snapshot.fetched_row_count is not None:
            data = data.slice(0, snapshot.fetched_row_count)
        fetched = (
            snapshot.fetched_row_count
            if snapshot.fetched_row_count is not None
            else data.num_rows
        )
        capped = max_rows is not None and data.num_rows > max_rows
        if capped:
            data = data.slice(0, max_rows)
        path = tmpdir / f"{snapshot.pane_id}.csv"
        write_file(data=data, path=path, format_name="csv")
        written.append(str(path))
        entries.append(
            {
                "tab": snapshot.pane_id,
                "label": snapshot.label,
                "pinned": snapshot.pinned,
                "sql": snapshot.sql,
                "columns": [list(column) for column in snapshot.columns],
                "rows": data.num_rows,
                "fetched_rows": fetched,
                "fetch_truncated": snapshot.fetch_truncated,
                "truncated": bool(capped or snapshot.fetch_truncated)
                or data.num_rows < fetched,
                "elapsed_s": snapshot.elapsed,
                "csv": str(path),
            }
        )
    document = {"stdin": stdin_source, "results": entries}
    return json.dumps(document), written


def run_command(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    stdin: bytes = b"",
    timeout: float = 120.0,
) -> CommandResult:
    """Run one command to completion and report how it went.

    Blocking, and meant for a worker thread. `env` is *added* to the environment the
    child would inherit anyway -- a command finds its own tools on the user's `PATH`,
    and
    a Harlequin that stripped the environment would be a Harlequin whose commands could
    not reach the database the user is already connected to.

    A child that outlives its timeout is terminated, given two seconds, and then killed;
    the result says `timed_out` so the caller can say so rather than reporting exit 143
    as
    a failure of the tool. `OSError` (a program that is not on `PATH`, a directory that
    is
    not executable) is left to the caller: the fix for it is a sentence about `PATH`,
    not
    a returncode.
    """
    import os

    child_env = dict(os.environ)
    child_env.update({key: str(value) for key, value in env.items()})
    process = subprocess.Popen(  # noqa: S603 -- argv from the user's own config
        list(argv),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_env,
    )
    try:
        out, err = process.communicate(input=stdin, timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
        process.terminate()
        try:
            out, err = process.communicate(timeout=TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            out, err = process.communicate()
    return CommandResult(
        returncode=process.returncode if process.returncode is not None else -1,
        stdout=_text(out),
        stderr=_text(err),
        timed_out=timed_out,
    )


def _text(raw: bytes | None) -> str:
    """A child's output as text. A tool that writes bytes we cannot decode is a tool
    whose message we show imperfectly rather than one that crashes the app."""
    if not raw:
        return ""
    return raw.decode("utf-8", errors="replace")
