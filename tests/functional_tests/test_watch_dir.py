"""`--watch-dir`, through the app: what is offered, what opens, and what moves.

`tests/unit_tests/test_watch.py` owns the scanner. These drive the front end:
nothing opens on its own, and when the key is pressed the SQL is a buffer and the
CSV is a named, pinned result tab.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Awaitable, Callable

import pytest

from harlequin import Harlequin
from harlequin.adapter import HarlequinAdapter
from harlequin.watch import opened_dir


def _drop(directory: Path, name: str, text: str) -> Path:
    """Write a file and backdate it past the scanner's hold-still window."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text)
    when = path.stat().st_mtime - 10
    os.utime(path, (when, when))
    return path


def _watching(adapter: type[HarlequinAdapter], watch_dir: Path) -> Harlequin:
    return Harlequin(
        adapter([":memory:"], no_init=True),
        connection_hash="foo",
        watch_dir=watch_dir,
    )


async def _ready(app: Harlequin, pilot, wait_for_workers) -> None:
    await wait_for_workers(app)
    while app.editor is None:
        await pilot.pause()


@pytest.mark.asyncio
async def test_what_is_waiting_is_announced_and_nothing_is_opened(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    tmp_path: Path,
) -> None:
    _drop(tmp_path, "carrier-mix.sql", "select 1 as one")
    app = _watching(duckdb_adapter, tmp_path)
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        await pilot.pause()
        messages = [n.message for n in app._notifications]
        assert any("1 waiting" in m for m in messages)
        # the key the message names is the one that is bound
        assert any("alt+i" in m for m in messages)
        # nothing opened, nothing moved
        assert app.editor_collection.tabs.tab_count == 1
        assert (tmp_path / "carrier-mix.sql").exists()

        # and a second poll does not say it again
        app._poll_watch_dir()
        assert len([m for m in app._notifications if "waiting" in m.message]) == 1


@pytest.mark.asyncio
async def test_the_key_opens_the_sql_as_a_buffer_and_the_csv_as_a_pinned_tab(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    tmp_path: Path,
) -> None:
    _drop(tmp_path, "carrier-mix.sql", "select carrier from quotes")
    _drop(tmp_path, "carrier-mix.csv", "carrier,quotes\nA,10\nB,20\n")
    app = _watching(duckdb_adapter, tmp_path)
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        await pilot.press("alt+i")
        await wait_for_workers(app)
        await pilot.pause()

        # the SQL is a buffer, named after the file, with the file behind it
        assert app.editor.text == "select carrier from quotes"
        assert app.editor_collection.active_buffer_name() == "carrier-mix"
        path = app.editor_collection.active_buffer_path()
        assert path is not None and path == opened_dir(tmp_path) / "carrier-mix.sql"

        # the rows are a result tab that is named and kept
        pane_id = app.results_viewer.last_pushed
        assert pane_id is not None
        assert app.results_viewer.label_for(pane_id) == "carrier-mix"
        assert pane_id in app.results_viewer.pinned_pane_ids()
        table = app.results_viewer.table_for(pane_id)
        assert table is not None
        assert table.plain_column_labels == ["carrier", "quotes"]
        assert table.row_count == 2

        # both files moved, so the directory has nothing left to offer
        assert not (tmp_path / "carrier-mix.sql").exists()
        assert not (tmp_path / "carrier-mix.csv").exists()
        assert sorted(p.name for p in opened_dir(tmp_path).iterdir()) == [
            "carrier-mix.csv",
            "carrier-mix.sql",
        ]


@pytest.mark.asyncio
async def test_a_csv_that_cannot_be_read_costs_only_itself(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    tmp_path: Path,
) -> None:
    _drop(tmp_path, "broken.csv", "")
    _drop(tmp_path, "broken.sql", "select 1")
    app = _watching(duckdb_adapter, tmp_path)
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        await pilot.press("alt+i")
        await wait_for_workers(app)
        await pilot.pause()
        assert any(
            "broken.csv" in n.message and n.severity == "error"
            for n in app._notifications
        )
        # the failure is not offered again: it is in opened/, named, and reported
        assert not (tmp_path / "broken.csv").exists()
        # and the query beside it still opened: a broken file is a poor reason to
        # lose the SQL that came with it
        assert app.editor.text == "select 1"
        assert app.results_viewer.last_pushed is None


@pytest.mark.asyncio
async def test_the_key_with_nothing_waiting_says_nothing_is(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    tmp_path: Path,
) -> None:
    app = _watching(duckdb_adapter, tmp_path)
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        await pilot.press("alt+i")
        await pilot.pause()
        assert "Nothing waiting." in [n.message for n in app._notifications]


@pytest.mark.asyncio
async def test_without_the_option_the_poll_never_runs(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        assert app.watch_dir is None
        await pilot.press("alt+i")
        await pilot.pause()
        assert "No --watch-dir is set." in [n.message for n in app._notifications]
