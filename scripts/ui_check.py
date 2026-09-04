"""Render Harlequin headlessly at the terminal sizes the workbench actually uses.

    uv run python scripts/ui_check.py [--out DIR] [--sizes 94x52,120x36,...]
        [--catalog-min-width N] [--catalog-side left|right] [--no-png]

Writes one SVG per size and state (catalog as the app starts, catalog toggled with
f9, results after a query) and, on macOS, a PNG next to each via `qlmanage`. The
sizes default to a 13" half-screen tmux pane (94x52), the docs size (120x36), two
thirds of a 32" (160x60) and a 13" full screen (188x53). The sample database is a
few `reporting.*` tables, so the catalog and results have realistic names.

Use it to eyeball a fork change at every size before committing it; the snapshot
tests in tests/functional_tests/test_narrow_layout.py cover the narrow states.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb

from harlequin import Harlequin
from harlequin_duckdb import DuckDbAdapter

DEFAULT_SIZES = "94x52,120x36,160x60,188x53"

SQL = """-- ## Carrier revenue, last 30 days
select carrier_name, count(*) as policies, sum(premium_cents) / 100.0 as premium_usd
from reporting.policy_sales
where sold_at >= current_date - interval 30 day
group by 1 order by 3 desc
"""


def make_sample_db(path: Path) -> None:
    if path.exists():
        return
    con = duckdb.connect(str(path))
    con.execute("create schema reporting")
    con.execute(
        "create table reporting.policy_sales("
        "policy_id int, carrier_name varchar, premium_cents bigint, "
        "sold_at timestamp, state varchar)"
    )
    con.execute(
        "create table reporting.quote_requests("
        "request_id int, session_id varchar, vertical varchar, "
        "created_at timestamp, zip varchar, source varchar)"
    )
    con.execute(
        "create table reporting.carrier_performance("
        "carrier_name varchar, day date, quotes int, binds int, revenue_cents bigint)"
    )
    con.execute(
        "insert into reporting.policy_sales "
        "select i, 'Carrier ' || (i % 7), 10000 + i * 37, "
        "timestamp '2026-08-01' + interval (i) hour, ['MA','CA','TX','NY'][1 + i % 4] "
        "from range(1, 400) t(i)"
    )
    con.close()


async def render(
    db: Path, out: Path, width: int, height: int, min_width: int, side: str
) -> list[Path]:
    written: list[Path] = []

    def shot(app: Harlequin, state: str) -> None:
        name = f"{width}x{height}-{state}.svg"
        app.save_screenshot(filename=name, path=str(out))
        written.append(out / name)

    app = Harlequin(
        adapter=DuckDbAdapter((str(db),), no_init=True),
        catalog_side=side,
        catalog_min_width=min_width,
        profile_name="legacy",
    )
    async with app.run_test(size=(width, height)) as pilot:
        await pilot.pause(1.0)
        while app.editor is None or app.data_catalog.database_tree.loading:
            await pilot.pause()
        app.editor.text = SQL
        # the Redshift adapter shows these; the DuckDB sample does not
        for button_id in ("transaction_button", "commit_button", "rollback_button"):
            app.run_query_bar.query_one(f"#{button_id}").remove_class("hidden")
        await pilot.pause(0.3)
        shot(app, "start")
        await pilot.press("f9")
        await pilot.pause(0.3)
        shot(app, "f9")
        await pilot.press("f9")
        await pilot.press("ctrl+j")
        await pilot.pause(1.5)
        shot(app, "results")
    return written


def to_png(svg: Path) -> Path | None:
    if shutil.which("qlmanage") is None:
        return None
    subprocess.run(
        ["qlmanage", "-t", "-s", "2000", "-o", str(svg.parent), str(svg)],
        check=False,
        capture_output=True,
    )
    png = svg.with_name(svg.name + ".png")
    return png if png.exists() else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("ui_check"))
    parser.add_argument("--sizes", default=DEFAULT_SIZES, help="WxH,WxH,...")
    parser.add_argument("--catalog-min-width", type=int, default=120)
    parser.add_argument("--catalog-side", choices=["left", "right"], default="right")
    parser.add_argument("--no-png", action="store_true")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    db = args.out / "sample.duckdb"
    make_sample_db(db)
    for size in args.sizes.split(","):
        width, height = (int(n) for n in size.lower().split("x"))
        paths = asyncio.run(
            render(
                db, args.out, width, height, args.catalog_min_width, args.catalog_side
            )
        )
        for svg in paths:
            png = None if args.no_png else to_png(svg)
            print(png or svg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
