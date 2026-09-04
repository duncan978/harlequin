"""`catalog_exclude`: glob patterns the Data Catalog hides (fork feature).

A warehouse with a schema per CI run puts hundreds of schemas in a tree nobody
reads, and one of them here holds a view whose metadata the driver cannot read
at all. Hiding them is cheaper than handling them.
"""

from __future__ import annotations

from typing import Awaitable, Callable

import pytest

from harlequin import Harlequin
from harlequin.adapter import HarlequinAdapter
from harlequin.catalog import CatalogItem
from harlequin.components.data_catalog.database_tree import is_excluded, unqualify


def item(qualified_identifier: str, label: str) -> CatalogItem:
    return CatalogItem(
        qualified_identifier=qualified_identifier,
        query_name=qualified_identifier,
        label=label,
        type_label="sch",
    )


def test_unqualify_drops_the_quotes_a_database_uses() -> None:
    assert unqualify('"mydb"."myschema"."mytable"') == "mydb.myschema.mytable"
    assert unqualify("`mydb`.`myschema`") == "mydb.myschema"
    assert unqualify("plain.path") == "plain.path"


@pytest.mark.parametrize(
    "patterns,expected",
    [
        ((), False),
        (("ci_pr_*",), True),  # the label, at any depth
        (("CI_PR_*",), True),  # case-insensitive
        (("mydb.ci_pr_*",), True),  # the dotted path
        (("mydb.*.ci_pr_8768",), False),  # wrong depth: not a match
        (("ci_pr_87??",), True),  # ? is a single character
        (("reporting",), False),
        (("nope", "ci_pr_8768"), True),  # any pattern is enough
    ],
)
def test_is_excluded_matches_label_or_path(
    patterns: tuple[str, ...], expected: bool
) -> None:
    schema = item('"mydb"."ci_pr_8768"', "ci_pr_8768")
    assert is_excluded(schema, patterns) is expected


@pytest.mark.asyncio
async def test_the_tree_hides_excluded_schemas_and_their_children(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = Harlequin(
        duckdb_adapter([":memory:"], no_init=True),
        connection_hash="foo",
        catalog_exclude=["ci_pr_*"],
    )
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        assert app.connection is not None
        for schema in ("reporting", "ci_pr_8768"):
            app.connection.execute(f"create schema {schema}")
            app.connection.execute(f"create table {schema}.t as select 1 as a")
        app.update_schema_data()
        await wait_for_workers(app)
        while app.data_catalog.database_tree.loading:
            await pilot.pause()
        await pilot.pause()

        tree = app.data_catalog.database_tree
        assert tree.exclude == ("ci_pr_*",)
        labels = {str(node.label) for node in tree.root.children}
        # one database node; its schemas are the level that is filtered
        database = tree.root.children[0]
        database.expand()
        await wait_for_workers(app)
        await pilot.pause()
        await pilot.pause()
        schemas = {str(node.label).split()[0] for node in database.children}
        assert "reporting" in schemas, f"kept the schemas that matter: {schemas}"
        assert not any(s.startswith("ci_pr_") for s in schemas), (
            f"excluded schemas are gone: {schemas}"
        )
        assert labels, "the tree still has a database node"


@pytest.mark.asyncio
async def test_no_patterns_hides_nothing(
    duckdb_adapter: type[HarlequinAdapter],
    wait_for_workers: Callable[[Harlequin], Awaitable[None]],
) -> None:
    app = Harlequin(duckdb_adapter([":memory:"], no_init=True), connection_hash="foo")
    async with app.run_test() as pilot:
        await wait_for_workers(app)
        assert app.connection is not None
        app.connection.execute("create schema ci_pr_1")
        app.update_schema_data()
        await wait_for_workers(app)
        while app.data_catalog.database_tree.loading:
            await pilot.pause()
        await pilot.pause()
        tree = app.data_catalog.database_tree
        assert tree.exclude == ()
        database = tree.root.children[0]
        database.expand()
        await wait_for_workers(app)
        await pilot.pause()
        await pilot.pause()
        schemas = {str(node.label).split()[0] for node in database.children}
        assert "ci_pr_1" in schemas, f"default shows everything: {schemas}"


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, ()),
        ("ci_pr_*", ("ci_pr_*",)),  # a config file may write one bare string
        (["a", "b"], ("a", "b")),
        (("a",), ("a",)),
    ],
)
def test_the_app_normalizes_what_config_can_write(
    duckdb_adapter: type[HarlequinAdapter], value: object, expected: tuple[str, ...]
) -> None:
    app = Harlequin(
        duckdb_adapter([":memory:"], no_init=True),
        connection_hash="foo",
        catalog_exclude=value,  # type: ignore[arg-type]
    )
    assert app.catalog_exclude == expected
