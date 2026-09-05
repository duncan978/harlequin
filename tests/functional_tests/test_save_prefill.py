"""`ctrl+s` comes up holding the file the buffer came from (fork feature).

Upstream's `action_save` mounts an empty path input every time, so saving a file
you opened a minute ago means typing its path again from memory. Harlequin knows
the path -- the editor records it on open, and the watched directory records the
`opened/` copy it just wrote -- so the box is prefilled and Enter saves over the
file.

Found in the Phase 5 UAT (2026-09-05): a query a Claude sent arrived in a buffer
backed by a real file, and `ctrl+s` still asked where to put it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Awaitable, Callable

import pytest
from textual_textarea import PathInput

from harlequin import Harlequin
from harlequin.adapter import HarlequinAdapter


def _drop(watch_dir: Path, name: str, text: str) -> Path:
    """A watched file, backdated so `scan`'s hold-still rule is satisfied."""
    watch_dir.mkdir(parents=True, exist_ok=True)
    path = watch_dir / name
    path.write_text(text)
    when = path.stat().st_mtime - 10.0
    os.utime(path, (when, when))
    return path


async def _ready(app: Harlequin, pilot, wait_for_workers) -> None:
    """The editor is mounted and the workers are quiet. Same wait test_commands uses."""
    await wait_for_workers(app)
    while app.editor is None:
        await pilot.pause()
    await pilot.pause()


def _save_input(app: Harlequin) -> PathInput | None:
    inputs = app.query(PathInput)
    return inputs[0] if inputs else None


@pytest.mark.asyncio
async def test_saving_a_watched_query_offers_the_file_it_came_from(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    tmp_path: Path,
) -> None:
    watch_dir = tmp_path / "inbox"
    _drop(watch_dir, "carrier-mix.sql", "select 1 as one\n")
    app = Harlequin(
        duckdb_adapter([":memory:"], no_init=True),
        connection_hash="foo",
        watch_dir=watch_dir,
    )
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        app.action_open_watched()
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()

        opened = watch_dir / "opened" / "carrier-mix.sql"
        assert opened.is_file(), "the file was claimed into opened/"
        assert app.editor_collection.active_buffer_path() == opened.resolve()

        await app.editor_collection.current_editor.action_save()
        await pilot.pause()
        box = _save_input(app)
        assert box is not None, "ctrl+s still asks where the file goes"
        assert box.value == str(opened.resolve()), (
            "the box holds the file the query arrived in, so Enter saves over it"
        )


@pytest.mark.asyncio
async def test_a_buffer_with_no_file_still_gets_an_empty_box(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """A scratch buffer has no path to offer, and must not borrow another's."""
    app = Harlequin(duckdb_adapter([":memory:"], no_init=True), connection_hash="foo")
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        assert app.editor_collection.active_buffer_path() is None
        await app.editor_collection.current_editor.action_save()
        await pilot.pause()
        box = _save_input(app)
        assert box is not None
        assert box.value == "", "nothing known, nothing prefilled"
