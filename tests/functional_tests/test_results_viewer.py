from __future__ import annotations

from datetime import date, datetime
from typing import Awaitable, Callable
from unittest.mock import MagicMock

import pytest
from textual.message import Message
from textual.pilot import Pilot
from textual.widgets import Tab
from textual_fastdatatable import DataTable

from harlequin import Harlequin
from harlequin.adapter import HarlequinAdapter
from harlequin.components.results_viewer import ResultsViewer
from harlequin.components.text_modal import CellViewModal


@pytest.mark.asyncio
async def test_dupe_column_names(
    app_all_adapters: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    transaction_button_visible: Callable[[Harlequin], bool],
) -> None:
    app = app_all_adapters
    query = "select 1 as a, 1 as a, 2 as a, 2 as a"
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.text = query
        await pilot.press("ctrl+j")
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        if not transaction_button_visible(app):
            assert await app_snapshot(app, "dupe columns")


@pytest.mark.asyncio
async def test_copy_data(
    app_all_adapters: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    mock_pyperclip: MagicMock,
    transaction_button_visible: Callable[[Harlequin], bool],
) -> None:
    app = app_all_adapters
    query = "select 3, 'rosberg', 6, 'ROS', 'Nico', 'Rosberg', '1985-06-27', 'German', 'http://en.wikipedia.org/wiki/Nico_Rosberg'"
    expected = "3	rosberg	6	ROS	Nico	Rosberg	1985-06-27	German	http://en.wikipedia.org/wiki/Nico_Rosberg"
    messages: list[Message] = []
    async with app.run_test(message_hook=messages.append) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.text = query
        await pilot.press("ctrl+j")
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()

        assert app.results_viewer._has_focus_within
        keys = ["shift+right"] * 8
        await pilot.press(*keys)
        await pilot.wait_for_scheduled_animations()
        await pilot.press("ctrl+c")
        await pilot.pause()

        copied_message = list(
            filter(lambda m: isinstance(m, DataTable.SelectionCopied), messages)
        )[0]
        assert isinstance(copied_message, DataTable.SelectionCopied)
        assert isinstance(copied_message.values, list)

        app.editor.text = ""
        app.editor.focus()
        await pilot.press("ctrl+v")  # paste
        assert app.editor.text == expected
        if not transaction_button_visible(app):
            assert await app_snapshot(app, "paste values from table")


@pytest.mark.asyncio
async def test_view_cell_modal(
    app: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    long_value = "the quick brown fox " * 40
    query = f"select '{long_value}' as story"
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.text = query
        await pilot.press("ctrl+j")
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()

        assert app.results_viewer._has_focus_within
        await pilot.press("space")
        await pilot.pause()

        assert isinstance(app.screen, CellViewModal)
        assert app.screen.text == long_value
        assert app.screen.title == "story"
        assert await app_snapshot(app, "view cell modal")

        # clicking the text copies it and leaves the modal up, as does c
        await pilot.click("#modal_info")
        await pilot.pause()
        assert isinstance(app.screen, CellViewModal)
        assert app.clipboard == long_value

        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, CellViewModal)
        assert app.clipboard == long_value

        # scroll keys scroll instead of dismissing
        body = app.screen.body
        await pilot.press("pagedown")
        await pilot.wait_for_scheduled_animations()
        await pilot.pause()
        assert isinstance(app.screen, CellViewModal)
        assert body.scroll_offset.y > 0

        # any other key dismisses it
        await pilot.press("x")
        await pilot.pause()
        assert not isinstance(app.screen, CellViewModal)

        # a click outside the modal also closes it, like the help/error modals
        await pilot.press("space")
        await pilot.pause()
        assert isinstance(app.screen, CellViewModal)
        await pilot.click()
        await pilot.pause()
        assert not isinstance(app.screen, CellViewModal)


@pytest.mark.asyncio
async def test_data_truncated_with_tooltip(
    app_all_adapters: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    transaction_button_visible: Callable[[Harlequin], bool],
) -> None:
    app = app_all_adapters
    query = "select 'supercalifragilisticexpialidocious'"
    async with app.run_test(tooltips=True) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.text = query
        await pilot.press("ctrl+j")
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()

        await pilot.hover(ResultsViewer, (2, 2))
        await pilot.pause(0.5)
        if not transaction_button_visible(app):
            assert await app_snapshot(app, "hover over truncated value")


@pytest.mark.asyncio
async def test_infinity_timestamp(
    app: Harlequin,
    app_snapshot: Callable[..., Awaitable[bool]],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    query = """
        select
            'infinity'::date,
            'infinity'::timestamp,
            '-infinity'::date,
            '-infinity'::timestamp
        """
    async with app.run_test(size=(120, 36)) as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.text = query
        await pilot.press("ctrl+j")
        await wait_for_workers(app)
        await pilot.pause()

        results_table = app.results_viewer.get_visible_table()
        assert results_table is not None
        assert results_table.get_row_at(0) == [
            date.max,
            datetime.max,
            date.min,
            datetime.min,
        ]

        assert await app_snapshot(app, "hover over truncated value")


@pytest.mark.asyncio
async def test_the_viewer_cap_is_a_soft_one(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """Everything is fetched and the viewer holds the first N.

    Which is why it can report the exact total it is showing a part of --
    unlike the Run Query Bar's limit, where the rest was never fetched.
    """
    app = Harlequin(
        duckdb_adapter([":memory:"], no_init=True),
        connection_hash="capped",
        viewer_max_rows=10,
    )
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.text = "select * from range(100)"
        await pilot.press("ctrl+j")
        await wait_for_workers(app)
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()

        table = app.results_viewer.get_visible_table()
        assert table is not None
        assert table.row_count == 10
        assert table.fetched_row_count == 100
        assert table.fetch_truncated is False
        assert app.results_viewer.border_title == (
            "Query Results (Showing 10 of 100 Records)"
        )


@pytest.mark.asyncio
async def test_pinned_tabs_survive_the_next_run(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """A pinned result is what the next one gets compared against."""

    async def run(pilot: Pilot, sql: str) -> None:
        assert app.editor is not None
        app.editor.focus()
        app.editor.text = sql
        await pilot.press("ctrl+a")
        await pilot.press("ctrl+j")
        for _ in range(3):
            await wait_for_workers(app)
            await pilot.pause()

    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        viewer = app.results_viewer

        await run(pilot, "select 1 as a")
        assert viewer.tab_count == 1
        assert viewer.active == "result-1"
        # nothing to tell apart yet, so no tab bar
        assert "hide-tabs" in viewer.classes

        # an unpinned result is replaced, exactly as it always was
        await run(pilot, "select 2 as b")
        assert viewer.tab_count == 1
        assert viewer.active == "result-1"
        table = viewer.get_visible_table()
        assert table is not None and table.plain_column_labels == ["b"]

        viewer.focus()
        await pilot.press("p")
        await pilot.pause()
        assert "hide-tabs" not in viewer.classes
        assert viewer.get_tab("result-1").label_text.startswith("\N{PUSHPIN}")

        await run(pilot, "select 3 as c")
        assert viewer.tab_count == 2
        # the new result is the one in front of you, not the pinned one
        assert viewer.active == "result-2"
        table = viewer.get_visible_table()
        assert table is not None and table.plain_column_labels == ["c"]

        # cycling walks positions, so the gap a pin can leave does not matter
        await pilot.press("k")
        assert viewer.active == "result-1"
        await pilot.press("k")
        assert viewer.active == "result-2"
        await pilot.press("j")
        assert viewer.active == "result-1"

        # and the pinned tab still holds the query it was pinned on
        pinned_table = viewer.get_visible_table()
        assert pinned_table is not None
        assert pinned_table.plain_column_labels == ["b"]

        # unpinning hands it back to the next run
        await pilot.press("p")
        await pilot.pause()
        assert viewer.get_tab("result-1").label_text == "Result 1"
        await run(pilot, "select 4 as d")
        assert viewer.tab_count == 1


@pytest.mark.asyncio
async def test_close_tab_removes_the_visible_result(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.focus()
        app.editor.text = "select 1; select 2"
        await pilot.press("ctrl+a")
        await pilot.press("ctrl+j")
        for _ in range(3):
            await wait_for_workers(app)
            await pilot.pause()

        viewer = app.results_viewer
        assert viewer.tab_count == 2
        viewer.focus()
        await pilot.press("x")
        await pilot.pause()
        await pilot.pause()
        assert viewer.tab_count == 1
        assert "hide-tabs" in viewer.classes


@pytest.mark.asyncio
async def test_rename_result_tab(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        app.editor.focus()
        app.editor.text = "select 1 as a"
        await pilot.press("ctrl+a")
        await pilot.press("ctrl+j")
        for _ in range(3):
            await wait_for_workers(app)
            await pilot.pause()

        viewer = app.results_viewer
        viewer.focus()
        await pilot.press("ctrl+t")
        await pilot.pause()
        await pilot.press(*"baseline")
        await pilot.press("enter")
        await pilot.pause()

        label = viewer.get_tab("result-1").label_text
        assert "baseline" in label
        # a named result is one you meant to keep, so it pinned itself
        assert "\N{PUSHPIN}" in label
        assert "hide-tabs" not in viewer.classes

        # and it survives the next run, under its name
        app.editor.focus()
        app.editor.text = "select 2 as b"
        await pilot.press("ctrl+a")
        await pilot.press("ctrl+j")
        for _ in range(3):
            await wait_for_workers(app)
            await pilot.pause()
        assert viewer.tab_count == 2
        assert "baseline" in viewer.get_tab("result-1").label_text

        # an empty name puts the default back
        viewer.focus()
        viewer.active = "result-1"
        await pilot.pause()
        await pilot.press("ctrl+t")
        await pilot.pause()
        await pilot.press("ctrl+a")
        await pilot.press("delete")
        await pilot.press("enter")
        await pilot.pause()
        assert "baseline" not in viewer.get_tab("result-1").label_text


@pytest.mark.asyncio
async def test_rename_editor_buffer_tab(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        while app.editor is None:
            await pilot.pause()
        collection = app.editor_collection

        collection.focus()
        await pilot.press("ctrl+t")
        await pilot.pause()
        await pilot.press(*"scratch")
        await pilot.press("enter")
        await pilot.pause()

        assert collection.active is not None
        tab = collection.tabs.query_one(f"#{collection.active}", Tab)
        assert tab.label_text == "scratch"
        # and the name is what gets written to the editor cache
        assert [b.name for b in collection.buffers] == ["scratch"]
