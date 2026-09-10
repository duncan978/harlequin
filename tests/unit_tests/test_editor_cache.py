"""A named cache keeps its own buffers, and the unnamed one is unchanged.

Ticket 19 (workbench repo): two workspaces sharing one Harlequin cache meant
opening a second workspace always showed the first one's buffers. This
covers the fix's own contract -- the ALE side of naming a cache per workspace
is a separate repo and out of scope here.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest
from textual.widgets.text_area import Selection

from harlequin.editor_cache import (
    CACHE_VERSION,
    BufferState,
    Cache,
    get_cache_file,
    load_cache,
    write_cache,
)


@pytest.fixture
def buffer_states() -> List[BufferState]:
    return [
        BufferState(selection=Selection((0, 0), (0, 0)), text="select 1\n"),
    ]


@pytest.fixture
def cache(buffer_states: List[BufferState]) -> Cache:
    return Cache(focus_index=0, buffers=buffer_states)


@pytest.fixture(autouse=True)
def mock_user_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr("harlequin.editor_cache.user_cache_dir", lambda **_: tmp_path)
    return tmp_path


def test_no_name_is_todays_exact_path(mock_user_cache_dir: Path) -> None:
    """The check that matters most: nobody who never passes the option can
    see their path -- and so their existing buffers -- move."""
    assert get_cache_file() == mock_user_cache_dir / f"cache-{CACHE_VERSION}.pickle"
    assert get_cache_file(None) == get_cache_file()


def test_a_name_is_a_distinct_path(mock_user_cache_dir: Path) -> None:
    named = get_cache_file("workbench")
    assert named != get_cache_file()
    assert named == mock_user_cache_dir / f"cache-workbench-{CACHE_VERSION}.pickle"


def test_two_names_do_not_share_buffers(cache: Cache) -> None:
    other = Cache(focus_index=0, buffers=[BufferState(
        selection=Selection((0, 0), (0, 0)), text="select 2\n"
    )])
    write_cache(cache, cache_name="alpha")
    write_cache(other, cache_name="beta")

    assert load_cache(cache_name="alpha") == cache
    assert load_cache(cache_name="beta") == other
    assert load_cache(cache_name="alpha") != load_cache(cache_name="beta")
    # and neither touched the unnamed cache
    assert load_cache() is None


def test_reopening_the_same_name_reads_its_own_cache_back(cache: Cache) -> None:
    write_cache(cache, cache_name="workbench")
    assert load_cache(cache_name="workbench") == cache
    # opening it again with the same name still finds it
    assert load_cache(cache_name="workbench") == cache


def test_version_discard_still_applies_with_a_name_in_play(
    mock_user_cache_dir: Path, cache: Cache
) -> None:
    """An old-format cache is discarded, not misread, whether or not it is
    named -- corrupt bytes at a named path fail exactly like they do at the
    unnamed one."""
    write_cache(cache, cache_name="workbench")
    cache_file = get_cache_file("workbench")
    with open(cache_file, "wb") as f:
        f.write(b"not a valid pickle at all")
    assert load_cache(cache_name="workbench") is None
    # the unnamed cache is a separate file and is unaffected
    write_cache(cache)
    assert load_cache() == cache
