"""Sections, through the app: the navigator, focus and run (roadmap §3.4).

`tests/unit_tests/test_sections.py` owns the parser's corpus. These drive the
three things the parser exists for, so a change that keeps the parser right and
the front end wrong fails here.
"""

from __future__ import annotations

from typing import Awaitable, Callable

import pytest
from textual.widgets.text_area import Selection

from harlequin import Harlequin
from harlequin.components import SectionList, SectionsModal
from harlequin.components.code_editor import EditorCollection

SCRIPT = """select 0;
-- ## First
select 1;
-- ## Second
select 2;
select 22;
-- ## Empty
-- ## Third
select 3;
"""


async def _ready(app: Harlequin, pilot, wait_for_workers) -> None:
    await wait_for_workers(app)
    while app.editor is None:
        await pilot.pause()


@pytest.mark.asyncio
async def test_the_navigator_lists_the_sections_and_jumps(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        app.editor.text = SCRIPT

        await pilot.press("f11")
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, SectionsModal)
        listing = modal.query_one(SectionList)
        assert [s.name for s in listing.sections] == [
            "(preamble)",
            "First",
            "Second",
            "Empty",
            "Third",
        ]
        # the highlight starts on the section the cursor is in
        assert listing.option_list.highlighted == 0

        # typing filters, and Enter jumps to the first line of SQL
        await pilot.press("S", "e", "c")
        await pilot.pause()
        assert len(listing.visible_indexes) == 1
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, SectionsModal)
        assert app.editor.selection.end[0] == SCRIPT.splitlines().index("select 2;")


@pytest.mark.asyncio
async def test_the_navigator_says_so_when_there_are_no_sections(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        app.editor.text = "select 1;\nselect 2;\n"
        await pilot.press("f11")
        await pilot.pause()
        assert not isinstance(app.screen, SectionsModal)
        assert "-- ## Name" in list(app._notifications)[-1].message


@pytest.mark.asyncio
async def test_run_section_runs_only_that_section(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """It selects the section and submits, so the app's own splitter runs it."""
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        app.editor.text = SCRIPT
        collection = app.query_one(EditorCollection)
        # cursor inside "Second", which holds two statements
        row = SCRIPT.splitlines().index("select 22;")
        app.editor.text_input.selection = Selection.cursor((row, 0))

        collection.action_run_section()
        await pilot.pause()
        queries = app.editor.selected_queries()
        # both of the section's statements, and nothing from the next section.
        # The first carries its own heading comment, which is what a statement
        # that starts after the previous semicolon actually is.
        assert len(queries) == 2
        assert queries[0].endswith("select 2;")
        assert queries[1] == "select 22;"
        assert not any("select 3" in q for q in queries)

        # a section with no SQL under it is refused rather than run
        row = SCRIPT.splitlines().index("-- ## Empty")
        app.editor.text_input.selection = Selection.cursor((row, 0))
        collection.action_run_section()
        await pilot.pause()
        assert "no SQL" in list(app._notifications)[-1].message


@pytest.mark.asyncio
async def test_focus_section_opens_a_tab_and_writes_it_back(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        app.editor.text = SCRIPT
        collection = app.query_one(EditorCollection)
        parent_id = collection.active
        row = SCRIPT.splitlines().index("select 1;")
        app.editor.text_input.selection = Selection.cursor((row, 0))

        collection.action_focus_section()
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        section_id = collection.active
        assert section_id != parent_id
        assert section_id in collection.section_views
        assert collection.buffer_names[section_id] == "First"
        assert app.editor.text == "-- ## First\nselect 1;\n"

        # edit the section, including its heading, then go back to the parent
        app.editor.text = "-- ## First, renamed\nselect 11;\n"
        collection.tabs.active = parent_id
        await pilot.pause()
        assert app.editor.text == (
            "select 0;\n"
            "-- ## First, renamed\n"
            "select 11;\n"
            "-- ## Second\n"
            "select 2;\n"
            "select 22;\n"
            "-- ## Empty\n"
            "-- ## Third\n"
            "select 3;\n"
        )


@pytest.mark.asyncio
async def test_focus_section_will_not_nest(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        app.editor.text = SCRIPT
        collection = app.query_one(EditorCollection)
        app.editor.text_input.selection = Selection.cursor(
            (SCRIPT.splitlines().index("select 1;"), 0)
        )
        collection.action_focus_section()
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()
        tabs_before = collection.tab_count

        collection.action_focus_section()
        await pilot.pause()
        assert collection.tab_count == tabs_before
        assert "already one section" in list(app._notifications)[-1].message


@pytest.mark.asyncio
async def test_a_parent_that_moved_on_is_found_by_heading(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """The parent is an ordinary tab and can be edited while a section is open."""
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        app.editor.text = SCRIPT
        collection = app.query_one(EditorCollection)
        parent_id = collection.active
        app.editor.text_input.selection = Selection.cursor(
            (SCRIPT.splitlines().index("select 3;"), 0)
        )
        collection.action_focus_section()
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()

        # something was inserted above the section, so its offsets are stale
        parent = collection.buffer_states[parent_id]
        collection.buffer_states[parent_id] = type(parent)(
            text="-- ## Brand new\nselect 999;\n" + parent.text,
            selection=parent.selection,
        )
        app.editor.text = "-- ## Third\nselect 33;\n"
        collection.tabs.active = parent_id
        await pilot.pause()
        assert app.editor.text.endswith("-- ## Third\nselect 33;\n")
        assert "select 3;" not in app.editor.text
        assert app.editor.text.startswith("-- ## Brand new\nselect 999;\n")


@pytest.mark.asyncio
async def test_a_section_whose_heading_is_gone_is_not_guessed_at(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        app.editor.text = SCRIPT
        collection = app.query_one(EditorCollection)
        parent_id = collection.active
        app.editor.text_input.selection = Selection.cursor(
            (SCRIPT.splitlines().index("select 1;"), 0)
        )
        collection.action_focus_section()
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()

        parent = collection.buffer_states[parent_id]
        collection.buffer_states[parent_id] = type(parent)(
            text="-- ## Nothing like it\nselect 1;\n", selection=parent.selection
        )
        app.editor.text = "-- ## First\nselect 111;\n"
        collection.tabs.active = parent_id
        await pilot.pause()
        # untouched, and said so
        assert app.editor.text == "-- ## Nothing like it\nselect 1;\n"
        assert "no longer in the tab it came from" in list(app._notifications)[-1].message


@pytest.mark.asyncio
async def test_closing_a_section_tab_writes_it_back(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        app.editor.text = SCRIPT
        collection = app.query_one(EditorCollection)
        parent_id = collection.active
        app.editor.text_input.selection = Selection.cursor(
            (SCRIPT.splitlines().index("select 1;"), 0)
        )
        collection.action_focus_section()
        await pilot.pause()
        await wait_for_workers(app)
        await pilot.pause()

        app.editor.text = "-- ## First\nselect 1111;\n"
        collection.action_close_buffer()
        await pilot.pause()
        assert collection.active == parent_id
        assert collection.section_views == {}
        assert "select 1111;" in collection.buffer_states[parent_id].text
