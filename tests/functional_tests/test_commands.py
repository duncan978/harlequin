"""Config-defined commands, through the app.

The process side is `tests/unit_tests/test_commands.py`. What is driven here is
everything a user actually meets: the consent gate, what each `stdin` source gathers,
what each `output` mode does, the empty cases, the menu, and a keymap that names an
action nothing provides.

Every command is a child Python writes for this test, so nothing here depends on a tool
being installed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Awaitable, Callable

import pytest

from textual.widgets.text_area import Selection

from harlequin import Harlequin
from harlequin.adapter import HarlequinAdapter
from harlequin.components import CommandList, CommandMenu
from harlequin.components.confirm_modal import ConfirmModal
from harlequin.components.text_modal import ErrorModal
from harlequin.config import CommandConfig
from harlequin.keymap import HarlequinKeyBinding, HarlequinKeyMap

ECHO_STDIN = "import sys; sys.stdout.write(sys.stdin.read())"
SAY_ENV = (
    "import os,sys; sys.stdout.write('|'.join("
    "os.environ.get(k, '') for k in ("
    "'HARLEQUIN_COMMAND','HARLEQUIN_STDIN','HARLEQUIN_PROFILE',"
    "'HARLEQUIN_ADAPTER','HARLEQUIN_BUFFER_NAME','HARLEQUIN_BUFFER_PATH')))"
)


def command(source: str, **kwargs: object) -> CommandConfig:
    defaults: dict = {
        "command": [sys.executable, "-c", source],
        "description": "Do the thing",
        "stdin": "buffer",
        "output": "notify",
        "timeout": 20.0,
    }
    defaults.update(kwargs)
    return CommandConfig(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def command_app(
    request: pytest.FixtureRequest, duckdb_adapter: type[HarlequinAdapter]
) -> Harlequin:
    """An app with one command called `send`, and `alt+s` bound to it."""
    commands = getattr(request, "param", None) or {"send": command(ECHO_STDIN)}
    keymap = HarlequinKeyMap(
        name="test",
        bindings=[
            HarlequinKeyBinding(keys="alt+s", action="command.send", key_display="⌥s"),
            HarlequinKeyBinding(
                keys="alt+c", action="show_command_menu", key_display="⌥c"
            ),
        ],
    )
    return Harlequin(
        duckdb_adapter([":memory:"], no_init=True),
        connection_hash="foo",
        commands=commands,
        adapter_name="duckdb",
        profile_name="legacy",
        keymap_names=["vscode", "test"],
        user_defined_keymaps=[keymap],
    )


async def _ready(app: Harlequin, pilot, wait_for_workers) -> None:
    await wait_for_workers(app)
    while app.editor is None:
        await pilot.pause()
    await pilot.pause()


async def _run(app: Harlequin, pilot, name: str = "send", approve: bool = True) -> None:
    """Press the key, answer the consent dialog, and wait for the worker."""
    if name == "send":
        await pilot.press("alt+s")
    else:
        app.action_run_command(name)
    await pilot.pause()
    if isinstance(app.screen, ConfirmModal):
        # the dialog is two buttons and no key bindings, so a click is what a user has
        await pilot.click("#yes" if approve else "#no")
        await pilot.pause()
    for worker in list(app.workers):
        if worker.group == "external_commands":
            await app.workers.wait_for_complete([worker])
    await pilot.pause()
    await pilot.pause()


@pytest.mark.asyncio
async def test_the_first_run_asks_and_the_second_does_not(
    command_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = command_app
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        app.editor.text = "select 1;"

        await pilot.press("alt+s")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal), (
            "a config file must not be able to approve its own subprocesses"
        )
        await pilot.click("#yes")
        await pilot.pause()
        assert sys.executable in app._approved_programs

        await pilot.press("alt+s")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmModal), "once per process"


@pytest.mark.asyncio
async def test_answering_no_runs_nothing(
    command_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = command_app
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        app.editor.text = "select 1;"
        await pilot.press("alt+s")
        await pilot.pause()
        await pilot.click("#no")
        await pilot.pause()
        assert app._approved_programs == set()
        assert not [w for w in app.workers if w.group == "external_commands"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command_app",
    [{"send": command(SAY_ENV, stdin="statement", output="replace")}],
    indirect=True,
)
async def test_the_environment_says_where_the_context_came_from(
    command_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = command_app
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        app.editor.text = "select 1;"
        await _run(app, pilot)
        name, stdin_source, profile, adapter, buffer_name, buffer_path = (
            app.editor.text.split("|")
        )
        assert name == "send"
        assert stdin_source == "statement"
        assert profile == "legacy"
        assert adapter == "duckdb"
        assert buffer_name, "a command is told which tab it was run from"
        assert buffer_path == "", "a scratch buffer has no file, and says so"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command_app",
    [{"send": command(ECHO_STDIN, stdin="statement", output="replace")}],
    indirect=True,
)
async def test_statement_sends_what_run_would_run(
    command_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = command_app
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        app.editor.text = "select 1;\nselect 2;\n"
        # inside the first statement: a bare cursor only overlaps a query it sits in
        app.editor.text_input.selection = Selection.cursor((0, 4))
        await _run(app, pilot)
        assert "select 1" in app.editor.text
        assert "select 2" not in app.editor.text, "the statement, not the buffer"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command_app",
    [{"send": command(ECHO_STDIN, stdin="selection", output="none")}],
    indirect=True,
)
async def test_nothing_selected_is_a_notification_and_no_run(
    command_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = command_app
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        app.editor.text = "select 1;"
        await pilot.press("alt+s")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmModal), "nothing to consent to"
        assert not [w for w in app.workers if w.group == "external_commands"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command_app",
    [{"send": command(ECHO_STDIN, stdin="results", output="none")}],
    indirect=True,
)
async def test_results_with_nothing_run_yet_is_a_notification(
    command_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = command_app
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        await pilot.press("alt+s")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmModal)
        assert not [w for w in app.workers if w.group == "external_commands"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command_app",
    [{"send": command("import sys; sys.stdout.write('')", output="replace")}],
    indirect=True,
)
async def test_empty_output_never_blanks_the_buffer(
    command_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = command_app
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        app.editor.text = "select 1;"
        await _run(app, pilot)
        assert app.editor.text == "select 1;", (
            "a tool that returned nothing must not blank the query it was given"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command_app",
    [
        {
            "send": command(
                "import sys; sys.stderr.write('this buffer has no file\\n');"
                " sys.exit(2)",
                output="notify",
            )
        }
    ],
    indirect=True,
)
async def test_a_failure_is_an_error_modal_carrying_stderr(
    command_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = command_app
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        app.editor.text = "select 1;"
        await _run(app, pilot)
        assert isinstance(app.screen, ErrorModal), (
            "the channel a tool says 'open or save it first' through"
        )


@pytest.mark.asyncio
async def test_the_menu_lists_filters_and_runs(
    command_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = command_app
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        app.editor.text = "select 1;"
        await pilot.press("alt+c")
        await pilot.pause()
        assert isinstance(app.screen, CommandMenu)
        listing = app.screen.query_one(CommandList)
        assert listing.names == ["send"]
        assert listing.keys["send"] == "⌥s", "the menu shows the key it is also on"
        await pilot.press("z")  # matches nothing
        await pilot.pause()
        assert listing.visible_indexes == []
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, CommandMenu)


@pytest.mark.asyncio
async def test_a_keymap_that_names_an_unknown_action_does_not_crash(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    # Before commands came from config this was a KeyError during mount, i.e. a
    # traceback on start-up. It is a feature's ordinary failure mode now.
    keymap = HarlequinKeyMap(
        name="typo",
        bindings=[
            HarlequinKeyBinding(keys="alt+s", action="command.not_configured"),
        ],
    )
    app = Harlequin(
        duckdb_adapter([":memory:"], no_init=True),
        connection_hash="foo",
        keymap_names=["vscode", "typo"],
        user_defined_keymaps=[keymap],
    )
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        assert app.is_running, "the app is up, minus that one binding"


@pytest.mark.asyncio
async def test_a_saved_buffer_reports_its_path(
    command_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    tmp_path: Path,
) -> None:
    app = command_app
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        target = tmp_path / "q.sql"
        target.write_text("select 1;\n")
        app.editor_collection.remember_buffer_path(target)
        assert app.editor_collection.active_buffer_path() == target


@pytest.mark.asyncio
async def test_an_opened_path_is_recorded_absolute(
    command_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A file opened as `notes.sql` is relative to Harlequin's working directory, and a
    # bare name means nothing to whatever the path is handed to next -- which may be a
    # program run from anywhere. HARLEQUIN_BUFFER_PATH has to be absolute.
    target = tmp_path / "notes.sql"
    target.write_text("select 1;\n")
    monkeypatch.chdir(tmp_path)
    app = command_app
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        app.editor._remember_path(Path("notes.sql"))
        recorded = app.editor_collection.active_buffer_path()
        assert recorded is not None
        assert recorded.is_absolute(), recorded
        assert recorded.name == "notes.sql"
        assert recorded.is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command_app",
    [
        {
            "send": command(ECHO_STDIN, description="Send buffer"),
            "send_other": command(ECHO_STDIN, description="Send something else"),
        }
    ],
    indirect=True,
)
async def test_approving_one_command_covers_another_running_the_same_program(
    command_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    # The question the dialog asks is "may Harlequin run this program", and several
    # entries handing different flags to one tool are one answer to it. Asking per entry
    # would teach the user to say yes without reading, which is worse than not asking.
    app = command_app
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        app.editor.text = "select 1;"

        await pilot.press("alt+s")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        prompt = app.screen.prompt
        assert "send_other" in prompt, "the dialog says what else it covers"
        await pilot.click("#yes")
        await pilot.pause()

        app.action_run_command("send_other")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmModal), "same program, same decision"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command_app",
    [
        {
            "send": command(ECHO_STDIN),
            "other_program": CommandConfig(
                command=["/bin/echo", "hello"], description="A different tool"
            ),
        }
    ],
    indirect=True,
)
async def test_a_command_running_a_different_program_still_asks(
    command_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = command_app
    async with app.run_test() as pilot:
        await _ready(app, pilot, wait_for_workers)
        app.editor.text = "select 1;"
        await _run(app, pilot)  # approves sys.executable

        app.action_run_command("other_program")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal), (
            "a program you have not approved stops and asks -- that is the whole gate"
        )
