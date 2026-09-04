"""The process side of config-defined commands: run it, serialize it, describe it.

Every child here is `[sys.executable, "-c", ...]` -- hermetic, on the interpreter
running the tests, with no fixture files and nothing to install. The app side (the
consent gate, the keys, the empty cases) is `tests/functional_tests/test_commands.py`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pyarrow as pa
import pytest

from harlequin.commands import (
    CommandResult,
    TableSnapshot,
    build_env,
    results_manifest,
    run_command,
)


def child(source: str) -> list[str]:
    return [sys.executable, "-c", source]


def test_a_command_that_succeeds_reports_its_output() -> None:
    result = run_command(
        child("import sys; sys.stdout.write('Queued 123\\nand more\\n')"),
        env={},
    )
    assert result.ok
    assert result.returncode == 0
    assert result.stdout.startswith("Queued 123")
    assert result.first_line == "Queued 123", "a toast shows the first line only"


def test_stdin_reaches_the_child_verbatim() -> None:
    result = run_command(
        child("import sys; sys.stdout.write(sys.stdin.read().upper())"),
        env={},
        stdin="select 1;\n".encode(),
    )
    assert result.stdout == "SELECT 1;\n"


def test_the_environment_is_added_to_not_replaced() -> None:
    result = run_command(
        child(
            "import os,sys; sys.stdout.write(os.environ['HARLEQUIN_PROFILE'] + ' ' "
            "+ ('PATH' in os.environ and 'inherited' or 'stripped'))"
        ),
        env={"HARLEQUIN_PROFILE": "legacy"},
    )
    assert result.stdout == "legacy inherited"


def test_empty_output_is_not_an_error() -> None:
    result = run_command(child("pass"), env={})
    assert result.ok
    assert result.stdout == ""
    assert result.first_line == ""


def test_a_nonzero_exit_keeps_stderr() -> None:
    result = run_command(
        child("import sys; sys.stderr.write('this buffer has no file\\n'); sys.exit(2)"),
        env={},
    )
    assert not result.ok
    assert result.returncode == 2
    assert "no file" in result.stderr


def test_a_child_that_ignores_terminate_is_killed() -> None:
    # SIGTERM ignored on purpose: the timeout has to end with a dead child either way,
    # which is the whole reason `run_command` does not stop at `terminate()`.
    result = run_command(
        child(
            "import signal,time,sys\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "time.sleep(30)\n"
        ),
        env={},
        timeout=0.5,
    )
    assert result.timed_out
    assert not result.ok


def test_a_program_that_is_not_there_raises_oserror() -> None:
    # left to the caller: the fix is a sentence about PATH, not a returncode
    with pytest.raises(OSError):
        run_command(["harlequin-no-such-program-8f3a"], env={})


def test_build_env_says_what_it_knows_and_nothing_else() -> None:
    env = build_env(
        name="send_results",
        stdin_source="results",
        version="2.13.0+insurify.10",
        profile="legacy",
        adapter="redshift",
        buffer_name="carrier mix",
        buffer_path="/tmp/q.sql",
    )
    assert env == {
        "HARLEQUIN_COMMAND": "send_results",
        "HARLEQUIN_STDIN": "results",
        "HARLEQUIN_VERSION": "2.13.0+insurify.10",
        "HARLEQUIN_PROFILE": "legacy",
        "HARLEQUIN_ADAPTER": "redshift",
        "HARLEQUIN_BUFFER_NAME": "carrier mix",
        "HARLEQUIN_BUFFER_PATH": "/tmp/q.sql",
    }
    assert "HARLEQUIN_CONN_STR" not in env, "a DSN carries a password"


def test_build_env_writes_an_empty_string_for_what_it_does_not_know() -> None:
    env = build_env(name="x", stdin_source="none", version="v")
    assert env["HARLEQUIN_PROFILE"] == ""
    assert env["HARLEQUIN_BUFFER_PATH"] == ""


def snapshot(rows: int, **kwargs: object) -> TableSnapshot:
    data = pa.table(
        {
            "carrier": [f"carrier-{i}" for i in range(rows)],
            "quotes": list(range(rows)),
        }
    )
    defaults: dict = {
        "pane_id": "result-1",
        "sql": "select carrier, count(*) from q group by 1",
        "columns": [("carrier", "VARCHAR"), ("quotes", "BIGINT")],
        "data": data,
        "fetched_row_count": rows,
        "fetch_truncated": False,
    }
    defaults.update(kwargs)
    return TableSnapshot(**defaults)  # type: ignore[arg-type]


def test_manifest_names_a_csv_that_holds_the_rows(tmp_path: Path) -> None:
    text, files = results_manifest(
        [snapshot(3, label="by carrier", pinned=True, elapsed=1.84)],
        stdin_source="results",
        tmpdir=tmp_path,
    )
    document = json.loads(text)
    assert document["stdin"] == "results"
    (entry,) = document["results"]
    assert entry["tab"] == "result-1"
    assert entry["label"] == "by carrier"
    assert entry["pinned"] is True
    assert entry["columns"] == [["carrier", "VARCHAR"], ["quotes", "BIGINT"]]
    assert entry["rows"] == 3
    assert entry["truncated"] is False
    assert entry["elapsed_s"] == 1.84
    assert files == [entry["csv"]]
    written = Path(entry["csv"]).read_text().splitlines()
    assert written[0] == "carrier,quotes"
    assert len(written) == 4, "a header and three rows"


def test_max_rows_caps_the_csv_and_says_it_is_truncated(tmp_path: Path) -> None:
    text, _files = results_manifest(
        [snapshot(500)], stdin_source="results", tmpdir=tmp_path, max_rows=200
    )
    (entry,) = json.loads(text)["results"]
    assert entry["rows"] == 200
    assert entry["fetched_rows"] == 500
    assert entry["truncated"] is True, "the CSV is not the whole answer"
    written = Path(entry["csv"]).read_text().splitlines()
    assert len(written) == 201
    assert written[1].startswith("carrier-0"), "rows 1-200, not a sample"
    assert written[200].startswith("carrier-199")


def test_the_overflow_probe_row_is_not_part_of_the_answer(tmp_path: Path) -> None:
    # Under the Run Query Bar's limit the fetch asks for one row more than the limit,
    # to learn there were more. That row is not what was asked for.
    text, _files = results_manifest(
        [snapshot(6, fetched_row_count=5, fetch_truncated=True)],
        stdin_source="results",
        tmpdir=tmp_path,
    )
    (entry,) = json.loads(text)["results"]
    assert entry["rows"] == 5
    assert entry["fetch_truncated"] is True
    assert entry["truncated"] is True
    assert len(Path(entry["csv"]).read_text().splitlines()) == 6


def test_every_pinned_table_is_in_one_manifest(tmp_path: Path) -> None:
    text, files = results_manifest(
        [
            snapshot(2, pane_id="result-1", label="by carrier", pinned=True),
            snapshot(4, pane_id="result-2", label="by channel", pinned=True),
        ],
        stdin_source="pinned_results",
        tmpdir=tmp_path,
    )
    document = json.loads(text)
    assert [entry["tab"] for entry in document["results"]] == ["result-1", "result-2"]
    assert len(files) == 2
    assert len(set(files)) == 2, "one file per table, not one file twice"


def test_command_result_first_line_skips_blank_lines() -> None:
    assert CommandResult(0, "\n\n  Queued 7  \n", "").first_line == "Queued 7"
