from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import List, Union

from platformdirs import user_cache_dir
from textual.widgets.text_area import Selection

CACHE_VERSION = 1


@dataclass
class BufferState:
    selection: Selection
    text: str
    name: str | None = None
    """What the user called this tab, or None for the default `Tab n`.

    Defaulted so a cache written before names existed still unpickles: the
    dataclass default is a class attribute, which a restored instance without
    the key falls back to.
    """


@dataclass
class Cache:
    focus_index: int
    buffers: List[BufferState]


def get_cache_file(cache_name: str | None = None) -> Path:
    """
    Returns the path to the cache file on disk.

    `cache_name` is None for one Harlequin shared by everything that runs it --
    today's exact path, untouched. A name gets its own file, so two named
    caches (and the unnamed one) never read or clobber each other's buffers.
    The version number still sits at the end of every filename, named or not,
    so an old format is discarded per-name exactly as it always was for the
    unnamed cache.
    """
    cache_dir = Path(user_cache_dir(appname="harlequin"))
    stem = f"cache-{cache_name}" if cache_name else "cache"
    cache_file = cache_dir / f"{stem}-{CACHE_VERSION}.pickle"
    return cache_file


def load_cache(cache_name: str | None = None) -> Union[Cache, None]:
    """
    Returns a Cache (a list of strings) by loading
    from a pickle saved to disk
    """
    cache_file = get_cache_file(cache_name)
    try:
        with cache_file.open("rb") as f:
            cache: Cache = pickle.load(f)
            assert isinstance(cache, Cache)
    except (
        pickle.UnpicklingError,
        ValueError,
        IndexError,
        FileNotFoundError,
        AssertionError,
    ):
        return None
    else:
        return cache


def write_cache(cache: Cache, cache_name: str | None = None) -> None:
    """
    Updates dumps buffer contents to to disk
    """
    cache_file = get_cache_file(cache_name)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "wb") as f:
        pickle.dump(cache, f)
