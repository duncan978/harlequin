"""`--watch-dir`, through the app: what is offered, what opens, and what moves.

`tests/unit_tests/test_watch.py` owns the scanner. These drive the front end: since
proposal 28 (roadmap §8.3), `alt+i` opens the queue panel and *never* opens a file by
itself -- Enter, `a`, `p` and `d` inside the panel are what do, and only to the one
item under the highlight.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Awaitable, Callable

import pytest
from textual.widgets import OptionList

from harlequin import Harlequin
from harlequin.adapter import HarlequinAdapter
from harlequin.components.watch_panel import WatchList, WatchPanel
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


def _panel(app: Harlequin) -> WatchPanel | None:
    for screen in app.screen_stack:
        if isinstance(screen, WatchPanel):
            return screen
    return None


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
async def test_the_key_opens_the_panel_not_the_file(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    tmp_path: Path,
) -> None:
    _drop(tmp_path, "carrier-mix.sql", "select 1 as one")
    app = _watching(duckdb_adapter, tmp_path)
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        await pilot.press("alt+i")
        await pilot.pause()

        panel = _panel(app)
        assert panel is not None
        assert [item.name for item in panel.query_one(WatchList).items] == [
            "carrier-mix"
        ]
        # the panel shows it; nothing has opened or moved on its own
        assert app.editor_collection.tabs.tab_count == 1
        assert (tmp_path / "carrier-mix.sql").exists()


@pytest.mark.asyncio
async def test_enter_opens_the_sql_as_a_buffer_and_the_csv_as_a_pinned_tab(
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
        await pilot.pause()
        await pilot.press("enter")
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

        # both files moved, so the directory has nothing left to offer, and the
        # panel closed itself once scan() came back empty
        assert not (tmp_path / "carrier-mix.sql").exists()
        assert not (tmp_path / "carrier-mix.csv").exists()
        assert sorted(p.name for p in opened_dir(tmp_path).iterdir()) == [
            "carrier-mix.csv",
            "carrier-mix.sql",
        ]
        assert _panel(app) is None


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
        await pilot.pause()
        await pilot.press("enter")
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
async def test_a_appends_the_sql_as_a_section_and_still_pins_the_rows(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    tmp_path: Path,
) -> None:
    """Item 14's co-working shape: the query joins the buffer already open."""
    _drop(tmp_path, "carrier-mix.sql", "select carrier from quotes")
    _drop(tmp_path, "carrier-mix.csv", "carrier,quotes\nA,10\nB,20\n")
    app = _watching(duckdb_adapter, tmp_path)
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        app.editor.text = "select 1 -- what I was already doing"
        tab_count_before = app.editor_collection.tabs.tab_count

        await pilot.press("alt+i")
        await pilot.pause()
        await pilot.press("a")
        await wait_for_workers(app)
        await pilot.pause()

        # no new tab -- it joined the one already open
        assert app.editor_collection.tabs.tab_count == tab_count_before
        assert "select 1 -- what I was already doing" in app.editor.text
        assert "-- ## carrier-mix" in app.editor.text
        assert "select carrier from quotes" in app.editor.text

        # the rows still arrived, pinned
        pane_id = app.results_viewer.last_pushed
        assert pane_id is not None
        assert pane_id in app.results_viewer.pinned_pane_ids()

        assert not (tmp_path / "carrier-mix.sql").exists()
        assert not (tmp_path / "carrier-mix.csv").exists()


@pytest.mark.asyncio
async def test_p_pins_only_the_rows_and_leaves_the_sql_waiting(
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
        await pilot.pause()
        await pilot.press("p")
        await wait_for_workers(app)
        await pilot.pause()

        pane_id = app.results_viewer.last_pushed
        assert pane_id is not None
        assert pane_id in app.results_viewer.pinned_pane_ids()

        # the rows are gone; the SQL is still there, waiting for its own pick
        assert not (tmp_path / "carrier-mix.csv").exists()
        assert (tmp_path / "carrier-mix.sql").exists()
        assert app.editor_collection.tabs.tab_count == 1
        panel = _panel(app)
        assert panel is not None
        assert [item.name for item in panel.query_one(WatchList).items] == [
            "carrier-mix"
        ]


@pytest.mark.asyncio
async def test_p_with_nothing_to_pin_warns_and_does_not_close_the_item(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    tmp_path: Path,
) -> None:
    _drop(tmp_path, "sql-only.sql", "select 1")
    app = _watching(duckdb_adapter, tmp_path)
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        await pilot.press("alt+i")
        await pilot.pause()
        await pilot.press("p")
        await wait_for_workers(app)
        await pilot.pause()
        assert any(
            "no result to pin" in n.message and n.severity == "warning"
            for n in app._notifications
        )
        assert (tmp_path / "sql-only.sql").exists()


@pytest.mark.asyncio
async def test_d_discards_without_opening_anything(
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
        await pilot.pause()
        await pilot.press("d")
        await wait_for_workers(app)
        await pilot.pause()

        assert app.editor.text == ""
        assert app.editor_collection.tabs.tab_count == 1
        assert app.results_viewer.last_pushed is None
        assert not (tmp_path / "carrier-mix.sql").exists()
        assert not (tmp_path / "carrier-mix.csv").exists()
        assert sorted(p.name for p in opened_dir(tmp_path).iterdir()) == [
            "carrier-mix.csv",
            "carrier-mix.sql",
        ]
        # nothing left to decide about, so the panel closed itself
        assert _panel(app) is None


@pytest.mark.asyncio
async def test_alt_j_opens_the_panel_on_the_newest_item(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    tmp_path: Path,
) -> None:
    _drop(tmp_path, "second.sql", "select 2")
    os.utime(tmp_path / "second.sql", (0, 60))
    _drop(tmp_path, "first.sql", "select 1")
    os.utime(tmp_path / "first.sql", (0, 10))
    app = _watching(duckdb_adapter, tmp_path)
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        await pilot.press("alt+j")
        await pilot.pause()

        panel = _panel(app)
        assert panel is not None
        watch_list = panel.query_one(WatchList)
        assert [item.name for item in watch_list.items] == ["first", "second"]
        option_list = watch_list.query_one(OptionList)
        assert option_list.highlighted == 1  # "second" is the newest


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
        assert _panel(app) is None


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


@pytest.mark.asyncio
async def test_a_symlinked_item_remembers_the_origin_not_the_moved_link(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    tmp_path: Path,
) -> None:
    """Roadmap §8.3 proposal 27: `ctrl+s` has to write through to the file Duncan
    picked, not over the symlink `claim()` moved into `opened/`. Unresolved, the
    remembered path is that moved symlink, and a save there replaces the link with
    a plain file -- breaking it silently rather than writing the origin."""
    origin_dir = tmp_path / "origin"
    origin_dir.mkdir()
    origin = origin_dir / "picked.sql"
    origin.write_text("select 1")
    when = origin.stat().st_mtime - 10
    os.utime(origin, (when, when))
    watch_dir = tmp_path / "watch"
    watch_dir.mkdir()
    (watch_dir / "picked.sql").symlink_to(origin)
    os.utime(watch_dir / "picked.sql", (when, when), follow_symlinks=False)

    app = _watching(duckdb_adapter, watch_dir)
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        await pilot.press("alt+i")
        await pilot.pause()
        await pilot.press("enter")
        await wait_for_workers(app)
        await pilot.pause()

        path = app.editor_collection.active_buffer_path()
        assert path == origin
        assert not path.is_symlink()


@pytest.mark.asyncio
async def test_the_panel_stays_open_between_two_picks(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    tmp_path: Path,
) -> None:
    """One item leaving the queue is not a reason to close the panel on the rest.

    The panel's whole claim over the old open-everything key is that a press
    decides nothing by itself; that only holds if the second decision is still
    reachable after the first one is made."""
    _drop(tmp_path, "first.sql", "select 1")
    _drop(tmp_path, "second.sql", "select 2")
    app = _watching(duckdb_adapter, tmp_path)
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        await pilot.press("alt+i")
        await pilot.pause()
        await pilot.press("d")
        await wait_for_workers(app)
        await pilot.pause()

        panel = _panel(app)
        assert panel is not None, "the other item still has to be decided about"
        assert [item.name for item in panel.query_one(WatchList).items] == ["second"]

        await pilot.press("enter")
        await wait_for_workers(app)
        await pilot.pause()
        assert app.editor.text == "select 2"
        assert _panel(app) is None


@pytest.mark.asyncio
async def test_a_second_press_on_an_item_already_running_is_ignored(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    tmp_path: Path,
) -> None:
    """The row cannot disappear until the action that empties it finishes, so a
    double-tap arrives at an item whose file has already moved. Without the
    guard the second press claims nothing, `claim()` hands back the original
    path, and the read reports "no such file" about a file that is fine and
    already open."""
    _drop(tmp_path, "carrier-mix.sql", "select 1 as one")
    app = _watching(duckdb_adapter, tmp_path)
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        await pilot.press("alt+i")
        await pilot.pause()

        # both picks are posted before either action can run: the handler is
        # synchronous and the work is a worker, which is exactly the interleaving
        # a fast double-tap produces.
        watch_list = _panel(app).query_one(WatchList)  # type: ignore[union-attr]
        watch_list.action_pick("open")
        watch_list.action_pick("open")
        await wait_for_workers(app)
        await pilot.pause()

        assert not [n.message for n in app._notifications if n.severity == "error"], (
            "the second press should do nothing, not fail loudly"
        )
        assert app.editor.text == "select 1 as one"
        assert app.editor_collection.tabs.tab_count == 2


@pytest.mark.asyncio
async def test_an_action_finishes_even_if_the_panel_closes_under_it(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    tmp_path: Path,
) -> None:
    """The file is claimed before it is read, so an action cancelled halfway
    leaves the item out of the queue and nothing on the screen. The panel's own
    poll can dismiss it the instant `scan()` empties, which is while the read is
    still going -- so the work belongs to the app, not to the screen."""
    _drop(tmp_path, "carrier-mix.sql", "select 1 as one")
    app = _watching(duckdb_adapter, tmp_path)
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        await pilot.press("alt+i")
        await pilot.pause()

        panel = _panel(app)
        assert panel is not None
        panel.query_one(WatchList).action_pick("open")
        panel.dismiss(None)
        await wait_for_workers(app)
        await pilot.pause()

        assert app.editor.text == "select 1 as one"
        assert not (tmp_path / "carrier-mix.sql").exists()


@pytest.mark.asyncio
async def test_the_toast_does_not_talk_over_the_open_panel(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    tmp_path: Path,
) -> None:
    """The panel is the livelier notice of the same thing; a toast under it would
    only repeat what it already lists."""
    _drop(tmp_path, "first.sql", "select 1")
    app = _watching(duckdb_adapter, tmp_path)
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        await pilot.press("alt+i")
        await pilot.pause()
        before = len([n for n in app._notifications if "waiting" in n.message])

        _drop(tmp_path, "second.sql", "select 2")
        app._poll_watch_dir()
        await pilot.pause()

        assert len([n for n in app._notifications if "waiting" in n.message]) == before
        # and the panel itself picks the arrival up on its own poll
        panel = _panel(app)
        assert panel is not None
        panel._refresh()
        await pilot.pause()
        assert [item.name for item in panel.query_one(WatchList).items] == [
            "first",
            "second",
        ]
