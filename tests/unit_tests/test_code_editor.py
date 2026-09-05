"""`CodeEditor.action_save`'s contract with `textual_textarea`.

`action_save` (A4) extends upstream's `TextEditor.action_save` rather than
reimplementing its body, so upstream's placeholder wording and `PathInput`
flags arrive automatically. What it still depends on is the id upstream
mounts the footer input under -- `#textarea__save_input` -- because that is
where the prefill is written and where `TextEditor.save_file`'s
`@on(Input.Submitted, ...)` listens. A `textual-textarea` bump that renamed
or dropped that id would silently lose the prefill; this pins it down as a
test instead.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual_textarea import PathInput

from harlequin.components.code_editor import CodeEditor


class _EditorApp(App):
    def compose(self) -> ComposeResult:
        yield CodeEditor()


@pytest.mark.asyncio
async def test_action_save_mounts_the_upstream_save_input() -> None:
    app = _EditorApp()
    async with app.run_test() as pilot:
        editor = app.query_one(CodeEditor)
        await editor.action_save()
        await pilot.pause()
        assert editor.query_one("#textarea__save_input", PathInput) is not None
