"""`EditorCollection.append_section`: the padding rule, in isolation.

`tests/functional_tests/test_watch_dir.py` exercises this end to end through the
queue panel's `a` key; this pins down the blank-line rule on its own, across the
three cases the functional test only shows one of.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from harlequin.components.code_editor import EditorCollection


class _EditorApp(App):
    def compose(self) -> ComposeResult:
        yield EditorCollection()


@pytest.fixture(autouse=True)
def no_startup_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare `EditorCollection()` restores whatever this machine's real
    Harlequin cache holds -- fine for the app, which always starts from one,
    but not for a test that wants to know its buffer starts empty."""
    monkeypatch.setattr(
        "harlequin.components.code_editor.load_cache", lambda *a, **kw: None
    )


@pytest.mark.asyncio
async def test_an_empty_buffer_gets_no_leading_padding() -> None:
    app = _EditorApp()
    async with app.run_test() as pilot:
        collection = app.query_one(EditorCollection)
        collection.append_section("q", "select 1")
        await pilot.pause()
        assert collection.editor.text == "-- ## q\nselect 1\n"


@pytest.mark.asyncio
async def test_a_buffer_with_no_trailing_newline_gets_a_blank_line() -> None:
    app = _EditorApp()
    async with app.run_test() as pilot:
        collection = app.query_one(EditorCollection)
        collection.editor.text = "select 1 -- already here"
        collection.append_section("q", "select 2")
        await pilot.pause()
        assert collection.editor.text == (
            "select 1 -- already here\n\n-- ## q\nselect 2\n"
        )


@pytest.mark.asyncio
async def test_a_buffer_ending_in_one_newline_gets_one_more() -> None:
    app = _EditorApp()
    async with app.run_test() as pilot:
        collection = app.query_one(EditorCollection)
        collection.editor.text = "select 1;\n"
        collection.append_section("q", "select 2")
        await pilot.pause()
        assert collection.editor.text == "select 1;\n\n-- ## q\nselect 2\n"


@pytest.mark.asyncio
async def test_a_buffer_already_ending_in_a_blank_line_gets_no_more() -> None:
    app = _EditorApp()
    async with app.run_test() as pilot:
        collection = app.query_one(EditorCollection)
        collection.editor.text = "select 1;\n\n"
        collection.append_section("q", "select 2")
        await pilot.pause()
        assert collection.editor.text == "select 1;\n\n-- ## q\nselect 2\n"


@pytest.mark.asyncio
async def test_the_cursor_lands_on_the_new_section() -> None:
    app = _EditorApp()
    async with app.run_test() as pilot:
        collection = app.query_one(EditorCollection)
        collection.editor.text = "select 1;\n"
        collection.append_section("q", "select 2")
        await pilot.pause()
        assert collection.editor.text_input is not None
        row, _ = collection.editor.text_input.selection.end
        assert collection.editor.text.splitlines()[row] == "select 2"
