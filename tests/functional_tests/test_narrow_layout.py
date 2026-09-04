"""Narrow mode: under `catalog_min_width` the Data Catalog is an overlay.

Half of a 13" laptop screen in the workbench's tmux layout is 94 columns. These
tests drive that size; `tests/functional_tests/test_layout.py` covers the column
behaviour every other size keeps. No SVG snapshots here: the committed baseline
can only be regenerated on the pinned Python (see tests/conftest.py); render
`scripts/ui_check.py` to eyeball these states instead.
"""

from __future__ import annotations

from typing import Awaitable, Callable

import pytest
from textual.pilot import Pilot

from harlequin import Harlequin
from harlequin.adapter import HarlequinAdapter

NARROW = (94, 52)
WIDE = (130, 52)


@pytest.fixture(params=["right", "left"])
def narrow_app(
    request: pytest.FixtureRequest, duckdb_adapter: type[HarlequinAdapter]
) -> Harlequin:
    return Harlequin(
        duckdb_adapter([":memory:"], no_init=True),
        connection_hash="foo",
        catalog_side=request.param,
        catalog_min_width=120,
        profile_name="legacy",
    )


async def _ready(
    app: Harlequin,
    pilot: Pilot[None],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    await wait_for_workers(app)
    while app.editor is None or app.data_catalog.database_tree.loading:
        await pilot.pause()
    await pilot.pause()


@pytest.mark.asyncio
async def test_catalog_starts_hidden_and_the_bar_is_compact(
    narrow_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = narrow_app
    async with app.run_test(size=NARROW) as pilot:
        await _ready(app, pilot, wait_for_workers)
        assert app.narrow
        assert app.data_catalog.disabled
        assert app.data_catalog.has_class("overlay")
        assert not app.sidebar_hidden, "the column preference is left alone"
        bar = app.run_query_bar
        assert bar.has_class("narrow")
        assert not bar.catalog_button.has_class("hidden")
        assert bar._profile_text() == "legacy"
        assert str(bar.run_button.label) == "Run"
        # the main panel keeps the whole width; nothing is squeezed
        assert app.query_one("#main_panel").region.width == NARROW[0]


@pytest.mark.asyncio
async def test_f9_opens_an_overlay_over_the_right_of_the_main_panel(
    narrow_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = narrow_app
    async with app.run_test(size=NARROW) as pilot:
        await _ready(app, pilot, wait_for_workers)
        await pilot.press("f9")
        await pilot.pause()
        assert app.catalog_overlay
        assert not app.data_catalog.disabled
        assert app.data_catalog.has_focus_within
        catalog = app.data_catalog.region
        assert catalog.width == 32
        if app.catalog_side == "right":
            assert catalog.x + catalog.width == NARROW[0], "docked to the right edge"
        else:
            assert catalog.x == 0, "docked to the left edge"
        main_panel = app.query_one("#main_panel")
        assert main_panel.region.width == NARROW[0], "floats, no reflow"

        # f9 again closes it and gives the editor focus back
        await pilot.press("f9")
        await pilot.pause()
        assert not app.catalog_overlay
        assert app.data_catalog.disabled
        assert app.editor is not None and app.editor.has_focus_within


@pytest.mark.asyncio
async def test_the_overlay_closes_on_escape_and_when_focus_leaves(
    narrow_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = narrow_app
    async with app.run_test(size=NARROW) as pilot:
        await _ready(app, pilot, wait_for_workers)
        assert app.editor is not None

        await pilot.press("ctrl+b")
        await pilot.pause()
        assert app.catalog_overlay
        await pilot.press("escape")
        await pilot.pause()
        assert not app.catalog_overlay
        assert app.editor.has_focus_within

        # the Catalog button is the mouse's way in; a click in the editor is the way out
        await pilot.click("#catalog_button")
        await pilot.pause()
        assert app.catalog_overlay
        app.editor.focus()
        await pilot.pause()
        await pilot.pause()
        assert not app.catalog_overlay
        assert app.data_catalog.disabled


@pytest.mark.asyncio
async def test_widening_the_terminal_brings_the_column_back(
    narrow_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = narrow_app
    async with app.run_test(size=NARROW) as pilot:
        await _ready(app, pilot, wait_for_workers)
        await pilot.resize_terminal(*WIDE)
        await pilot.pause()
        await pilot.pause()
        assert not app.narrow
        assert not app.data_catalog.disabled
        assert not app.data_catalog.has_class("overlay")
        assert app.query_one("#main_panel").region.width < WIDE[0]
        bar = app.run_query_bar
        assert not bar.has_class("narrow")
        assert bar.catalog_button.has_class("hidden")
        assert str(bar.run_button.label) == "Run Query"
        assert bar._profile_text() == "profile: legacy"

        # hide the column, then go narrow and wide again: the preference survives
        await pilot.press("f9")
        await pilot.pause()
        assert app.sidebar_hidden
        await pilot.resize_terminal(*NARROW)
        await pilot.pause()
        await pilot.pause()
        assert app.narrow and app.data_catalog.disabled
        await pilot.resize_terminal(*WIDE)
        await pilot.pause()
        await pilot.pause()
        assert not app.narrow
        assert app.sidebar_hidden
        assert app.data_catalog.disabled


@pytest.mark.asyncio
async def test_zero_min_width_keeps_the_column_at_any_size(
    app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    async with app.run_test(size=NARROW) as pilot:
        await _ready(app, pilot, wait_for_workers)
        assert app.catalog_min_width == 0
        assert not app.narrow
        assert not app.data_catalog.disabled
        assert app.run_query_bar.catalog_button.has_class("hidden")


@pytest.mark.asyncio
async def test_the_catalog_button_toggles_even_when_the_drawer_has_focus(
    narrow_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = narrow_app
    async with app.run_test(size=NARROW) as pilot:
        await _ready(app, pilot, wait_for_workers)
        await pilot.press("f9")
        await pilot.pause()
        assert app.catalog_overlay
        if app.catalog_side == "left":
            # a left drawer sits over the button; escape and the editor close it
            return
        # a real click focuses the button on mouse-down (blurring the catalog)
        # and presses it on mouse-up, a frame later
        app.run_query_bar.catalog_button.focus()
        await pilot.pause()
        await pilot.pause()
        assert app.catalog_overlay, "the blur alone must not close it"
        await pilot.click("#catalog_button")
        await pilot.pause()
        assert not app.catalog_overlay
        assert app.data_catalog.disabled


@pytest.mark.asyncio
async def test_a_modal_from_the_drawer_leaves_it_open_until_focus_moves_on(
    narrow_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = narrow_app
    async with app.run_test(size=NARROW) as pilot:
        await _ready(app, pilot, wait_for_workers)
        assert app.editor is not None
        await pilot.press("f9")
        await pilot.pause()
        await pilot.press("f1")  # help, a modal screen
        await pilot.pause()
        assert len(app.screen_stack) == 2
        assert app.catalog_overlay, "a modal on top is not 'focus left'"
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert app.catalog_overlay and app.data_catalog.has_focus_within

        # the same, but the modal hands focus to the editor: the drawer closes
        await pilot.press("f1")
        await pilot.pause()
        app.editor.focus()
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()
        assert not app.catalog_overlay


@pytest.mark.asyncio
async def test_full_screen_and_the_drawer(
    narrow_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = narrow_app
    async with app.run_test(size=NARROW) as pilot:
        await _ready(app, pilot, wait_for_workers)
        assert app.editor is not None
        # f10 with the drawer focused has nothing to full-screen: no stuck state
        await pilot.press("f9")
        await pilot.pause()
        await pilot.press("f10")
        await pilot.pause()
        assert not app.full_screen
        await pilot.press("f9")
        await pilot.pause()
        assert not app.catalog_overlay and app.data_catalog.disabled

        # full-screen editor: the drawer still opens over it and closes again
        app.editor.focus()
        await pilot.press("f10")
        await pilot.pause()
        assert app.full_screen and app.data_catalog.disabled
        await pilot.press("f9")
        await pilot.pause()
        assert app.catalog_overlay and not app.data_catalog.disabled
        await pilot.press("escape")
        await pilot.pause()
        assert not app.catalog_overlay and app.data_catalog.disabled
        await pilot.press("f10")
        await pilot.pause()
        assert not app.full_screen
        assert app.data_catalog.disabled, "still narrow, still hidden"


@pytest.mark.asyncio
async def test_losing_terminal_focus_keeps_the_drawer(
    narrow_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = narrow_app
    async with app.run_test(size=NARROW) as pilot:
        await _ready(app, pilot, wait_for_workers)
        await pilot.press("f9")
        await pilot.pause()
        app.app_focus = False  # another tmux pane was clicked
        await pilot.pause()
        await pilot.pause()
        assert app.catalog_overlay and not app.data_catalog.disabled
        app.app_focus = True
        await pilot.pause()
        assert app.catalog_overlay


@pytest.mark.asyncio
async def test_f6_opens_the_drawer_and_f7_moves_it(
    narrow_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = narrow_app
    async with app.run_test(size=NARROW) as pilot:
        await _ready(app, pilot, wait_for_workers)
        await pilot.press("f6")  # focus data catalog
        await pilot.pause()
        assert app.catalog_overlay and app.data_catalog.has_focus_within
        before = app.data_catalog.region.x
        await pilot.press("f7")
        await pilot.pause()
        await pilot.pause()
        assert app.data_catalog.region.x != before
        assert app.data_catalog.has_class("dock-left") == (app.catalog_side == "left")


@pytest.mark.asyncio
async def test_the_drawer_stops_above_the_run_query_bar(
    narrow_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """The gripe this fixed: a full-height drawer covered `Run` and `Limit`."""
    app = narrow_app
    async with app.run_test(size=NARROW) as pilot:
        await _ready(app, pilot, wait_for_workers)
        await pilot.press("f9")
        await pilot.pause()
        catalog = app.data_catalog.region
        bar = app.run_query_bar.region
        editor = app.editor_collection.region
        assert catalog.y == editor.y, "starts at the top of the pane"
        assert catalog.height == editor.height, "as tall as the editor it covers"
        assert catalog.bottom <= bar.y, "and no taller: the run bar is clear"
        # the run buttons are on the drawer's side of the bar under
        # catalog_side = "right", so they are the ones that had to stay visible
        for widget_id in ("#run_query", "#limit_input", "#catalog_button"):
            widget = app.query_one(widget_id)
            assert not widget.region.overlaps(catalog), f"{widget_id} is covered"


@pytest.mark.asyncio
async def test_the_catalog_button_sits_on_the_drawers_edge(
    narrow_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = narrow_app
    async with app.run_test(size=NARROW) as pilot:
        await _ready(app, pilot, wait_for_workers)
        bar = app.run_query_bar
        button = bar.catalog_button.region
        run_buttons = app.query_one("#run_buttons").region
        if app.catalog_side == "right":
            assert bar.catalog_button.has_class("dock-right")
            assert button.right >= run_buttons.right, (
                "on the right, past the run buttons"
            )
        else:
            assert not bar.catalog_button.has_class("dock-right")
            assert button.x < run_buttons.x, "on the left, before the run buttons"

        # f7 moves the drawer, and the button goes with it
        await pilot.press("f7")
        await pilot.pause()
        await pilot.pause()
        assert bar.catalog_button.has_class("dock-right") == (
            app.catalog_side == "right"
        )


@pytest.mark.asyncio
async def test_inserting_a_name_closes_the_drawer(
    narrow_app: Harlequin,
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    """Duncan's UAT answer (roadmap §6): insert is the end of the look-up."""
    app = narrow_app
    async with app.run_test(size=NARROW) as pilot:
        await _ready(app, pilot, wait_for_workers)
        assert app.editor is not None
        await pilot.press("f9")
        await pilot.pause()
        assert app.catalog_overlay
        tree = app.data_catalog.database_tree
        tree.focus()
        await pilot.pause()
        tree.cursor_line = 0
        tree.action_submit()  # what enter on a catalog node does
        await pilot.pause()
        await pilot.pause()
        assert app.editor.text, "a name was inserted"
        assert not app.catalog_overlay
        assert app.data_catalog.disabled
        assert app.editor.has_focus_within
