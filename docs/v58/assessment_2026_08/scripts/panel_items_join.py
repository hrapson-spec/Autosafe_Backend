#!/usr/bin/env python3
"""Panel items semi-join: all item rows belonging to panel vehicles' tests.

Runs ONCE at ladder end. Build side = distinct test_ids across all panel result
shards (~6.8M); probe side = 19 item years (all local). Output:
  panel/items_panel.parquet
Sanity: every panel test_id set year must exist; item rows attach only via test_id.
"""
import os
import sys
from pathlib import Path

import duckdb

LAKE = "/Users/henrirapson/autosafe/autosafe_lake"
SCRATCH = Path(
    "/private/tmp/claude-501/-Users-henrirapson/8a63890e-ab45-4b34-aa64-28cfebe748f3/scratchpad"
)
PANEL = SCRATCH / "panel"
EXPECTED_DUCKDB = "1.5.5"


def main() -> int:
    assert duckdb.__version__ == EXPECTED_DUCKDB
    con = duckdb.connect()
    tmp = SCRATCH / f"duckdb_tmp_{os.getpid()}"
    tmp.mkdir(parents=True, exist_ok=True)
    con.execute(f"PRAGMA temp_directory='{tmp}'")
    con.execute("PRAGMA max_temp_directory_size='6GiB'")
    con.execute("PRAGMA memory_limit='3GB'")

    shards = sorted(PANEL.glob("results_*.parquet"))
    years = sorted(int(p.stem.split("_")[1]) for p in shards)
    assert years == list(range(2005, 2024)), f"panel shards incomplete: {years}"

    con.execute(
        f"""
        CREATE TEMP TABLE panel_tests AS
        SELECT DISTINCT test_id FROM read_parquet('{PANEL}/results_*.parquet')
        """
    )
    n_tests = con.execute("SELECT count(*) FROM panel_tests").fetchone()[0]
    print(f"[join] panel tests={n_tests:,}")

    out = PANEL / "items_panel.parquet"
    con.execute(
        f"""
        COPY (
          SELECT i.* FROM read_parquet('{LAKE}/items/test_year=*/*.parquet', hive_partitioning=1) i
          JOIN panel_tests p ON i.test_id = p.test_id
        ) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    n_items = con.execute(f"SELECT count(*) FROM read_parquet('{out}')").fetchone()[0]
    print(f"[join] panel item rows={n_items:,} ({n_items/n_tests:.3f} per test overall)")
    print("PANEL_ITEMS_JOIN COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
