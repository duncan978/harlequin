"""`harlequin.watch`: what a watched directory offers, and what it does with it."""

from __future__ import annotations

import os
from pathlib import Path

import pyarrow as pa
import pytest

from harlequin.watch import (
    UNKNOWN_TYPE,
    claim,
    opened_dir,
    result_set_from_csv,
    scan,
    short_type,
)


def _drop(tmp_path: Path, name: str, text: str, age: float = 10.0) -> Path:
    """Write a file and backdate it, so `scan`'s hold-still rule is satisfied."""
    path = tmp_path / name
    path.write_text(text)
    when = path.stat().st_mtime - age
    os.utime(path, (when, when))
    return path


def test_a_sql_and_a_csv_of_one_name_are_one_item(tmp_path: Path) -> None:
    _drop(tmp_path, "carrier-mix.sql", "select 1")
    _drop(tmp_path, "carrier-mix.csv", "a,b\n1,2\n")
    (items,) = scan(tmp_path)
    assert items.name == "carrier-mix"
    assert items.sql is not None and items.csv is not None
    assert len(items.paths) == 2


def test_one_of_the_two_is_still_an_item(tmp_path: Path) -> None:
    _drop(tmp_path, "just-sql.sql", "select 1")
    _drop(tmp_path, "just-rows.csv", "a\n1\n")
    by_name = {item.name: item for item in scan(tmp_path)}
    assert by_name["just-sql"].csv is None
    assert by_name["just-rows"].sql is None


def test_nothing_else_in_the_directory_is_its_business(tmp_path: Path) -> None:
    _drop(tmp_path, "notes.txt", "hello")
    _drop(tmp_path, "envelope.json", "{}")
    (tmp_path / "opened").mkdir()
    _drop(tmp_path / "opened", "old.sql", "select 1")
    (tmp_path / "sub").mkdir()
    _drop(tmp_path / "sub", "deep.sql", "select 1")
    assert scan(tmp_path) == []


def test_a_file_has_to_hold_still(tmp_path: Path) -> None:
    """The window between a producer's two renames must not show half an item."""
    (tmp_path / "fresh.sql").write_text("select 1")
    assert scan(tmp_path) == []
    assert len(scan(tmp_path, min_age=0.0)) == 1


def test_the_oldest_is_offered_first(tmp_path: Path) -> None:
    _drop(tmp_path, "second.sql", "select 2", age=10.0)
    _drop(tmp_path, "first.sql", "select 1", age=60.0)
    assert [item.name for item in scan(tmp_path)] == ["first", "second"]


def test_a_missing_or_unreadable_directory_is_empty(tmp_path: Path) -> None:
    assert scan(tmp_path / "nope") == []
    assert scan(tmp_path / "nope" / "deeper") == []


def test_claiming_moves_the_file_and_keeps_every_name(tmp_path: Path) -> None:
    first = _drop(tmp_path, "q.sql", "select 1")
    moved = claim(first, tmp_path)
    assert moved == opened_dir(tmp_path) / "q.sql"
    assert not first.exists()
    assert moved.read_text() == "select 1"

    second = _drop(tmp_path, "q.sql", "select 2")
    again = claim(second, tmp_path)
    assert again.name == "q-2.sql"
    assert again.read_text() == "select 2"
    # and a third, so the counter is not a one-off
    assert claim(_drop(tmp_path, "q.sql", "select 3"), tmp_path).name == "q-3.sql"


def test_a_claimed_file_is_not_offered_again(tmp_path: Path) -> None:
    path = _drop(tmp_path, "q.sql", "select 1")
    claim(path, tmp_path)
    assert scan(tmp_path) == []


def test_short_types_speak_the_adapters_vocabulary() -> None:
    assert short_type(pa.int64()) == "#"
    assert short_type(pa.float64()) == "#.#"
    assert short_type(pa.string()) == "s"
    assert short_type(pa.bool_()) == "t/f"
    assert short_type(pa.date32()) == "d"
    assert short_type(pa.timestamp("s")) == "ts"
    assert short_type(pa.decimal128(9, 2)) == "#.#"
    assert short_type(pa.list_(pa.int64())) == "[]"
    assert short_type(pa.binary()) == "0b"


def test_an_unmapped_type_is_a_question_mark() -> None:
    assert short_type(pa.union([pa.field("a", pa.int8())], "sparse")) == UNKNOWN_TYPE


def test_a_csv_becomes_the_result_set_a_query_would_have_made(tmp_path: Path) -> None:
    path = _drop(tmp_path, "rows.csv", "carrier,quotes,rate\nA,10,0.5\nB,20,0.25\n")
    result = result_set_from_csv(path)
    assert result.columns == [("carrier", "s"), ("quotes", "#"), ("rate", "#.#")]
    assert result.fetched_row_count == 2
    assert result.truncated is False
    # no SQL to claim: the statement names the file rather than inventing a query
    assert result.statement.sql == "-- rows.csv"


def test_a_row_cap_applies_the_way_a_query_limit_does(tmp_path: Path) -> None:
    path = _drop(tmp_path, "many.csv", "n\n" + "".join("%d\n" % i for i in range(50)))
    result = result_set_from_csv(path, max_rows=10)
    assert result.row_count == 10
    assert result.truncated is True


def test_an_unreadable_csv_raises_for_the_caller_to_show(tmp_path: Path) -> None:
    path = _drop(tmp_path, "bad.csv", "")
    with pytest.raises(Exception):
        result_set_from_csv(path)


def test_a_fresh_csv_holds_back_the_sql_it_came_with(tmp_path: Path) -> None:
    """The producer renames the `.csv` in first and the `.sql` last, but a slow copy
    can leave the query settled while its rows are still landing. Neither half is
    offered until the pair has held still."""
    _drop(tmp_path, "pair.sql", "select 1", age=60.0)
    (tmp_path / "pair.csv").write_text("a\n1\n")
    assert scan(tmp_path) == []
    os.utime(tmp_path / "pair.csv", (0, 0))
    (item,) = scan(tmp_path)
    assert item.sql is not None and item.csv is not None
