#!/usr/bin/env python3
"""Extract the 1/100 vehicle-hash panel shard + per-year profile for ONE results year.

Used for local years (2015-2023) directly, and by restore_year ladder for each
restored parked year (2005-2014). Panel rule (pinned): duckdb 1.5.5,
  abs(hash(vehicle_id)) % 100 == 0
NEVER raw modulo (measured severe bias on vehicle_id residues).

Modes:
  --year N --expect-rows R --expect-class4 C [--profile]
      anchors -> panel shard -> per-year distinct-vehicle file (_veh_N.parquet)
      [--profile: parked-year profile block incl. outcome-conditional items join]
  --merge-running N
      fold panel/_veh_N.parquet into panel/all_vehicles_running.parquet, delete it.
      Run AFTER the year is deleted (disk-ledger separation).

Disk ledger (counted): spill cap 1.5 GiB; memory 3 GB; shard ~0.1 GiB/yr;
distinct file ~0.25 GiB transient; running file grows to ~1.5 GiB.

Anchors per year (STOP on mismatch — exact):
  rows == download_record per-year source total
  class-4 rows == recorded year_volumes value
"""
import argparse
import json
import os
import sys
from pathlib import Path

import duckdb

LAKE = "/Users/henrirapson/autosafe/autosafe_lake"
OUT = Path(__file__).resolve().parent.parent / "out"
SCRATCH = Path(
    "/private/tmp/claude-501/-Users-henrirapson/8a63890e-ab45-4b34-aa64-28cfebe748f3/scratchpad"
)
PANEL = SCRATCH / "panel"
EXPECTED_DUCKDB = "1.5.5"


def connect():
    assert duckdb.__version__ == EXPECTED_DUCKDB, (
        f"duckdb {duckdb.__version__} != pinned {EXPECTED_DUCKDB}; panel hash would be unpinned"
    )
    con = duckdb.connect()
    tmp = SCRATCH / f"duckdb_tmp_{os.getpid()}"
    tmp.mkdir(parents=True, exist_ok=True)
    con.execute(f"PRAGMA temp_directory='{tmp}'")
    con.execute("PRAGMA max_temp_directory_size='1.5GiB'")
    con.execute("PRAGMA memory_limit='3GB'")
    return con


def merge_running(con, year: int) -> int:
    vf = PANEL / f"_veh_{year}.parquet"
    running = PANEL / "all_vehicles_running.parquet"
    tmp_new = PANEL / f"_running_new_{year}.parquet"
    if not vf.exists():
        print(f"MERGE MISSING {vf}")
        return 2
    if running.exists():
        con.execute(
            f"""
            COPY (
              SELECT coalesce(a.vehicle_id, b.vehicle_id) AS vehicle_id,
                     least(coalesce(a.first_year_seen, 9999), coalesce(b.first_year_seen, 9999)) AS first_year_seen,
                     coalesce(a.n_years, 0) + coalesce(b.n_years, 0) AS n_years
              FROM read_parquet('{running}') a
              FULL OUTER JOIN read_parquet('{vf}') b USING (vehicle_id)
            ) TO '{tmp_new}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        os.replace(tmp_new, running)
    else:
        os.replace(vf, running)
        vf = None
    if vf and vf.exists():
        vf.unlink()
    rv = con.execute(f"SELECT count(*) FROM read_parquet('{running}')").fetchone()[0]
    print(f"[merge {year}] running distinct vehicles={rv:,}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int)
    ap.add_argument("--expect-rows", type=int)
    ap.add_argument("--expect-class4", type=int)
    ap.add_argument("--profile", action="store_true")
    ap.add_argument("--merge-running", type=int, default=None)
    args = ap.parse_args()
    PANEL.mkdir(parents=True, exist_ok=True)
    con = connect()

    if args.merge_running is not None:
        return merge_running(con, args.merge_running)

    y = args.year
    yrel = f"read_parquet('{LAKE}/results/test_year={y}/*.parquet')"

    n, c4 = con.execute(
        f"SELECT count(*), count(*) FILTER (WHERE test_class_id='4') FROM {yrel}"
    ).fetchone()
    if n != args.expect_rows or c4 != args.expect_class4:
        print(f"ANCHOR MISMATCH year={y} rows={n:,} (expect {args.expect_rows:,}) "
              f"class4={c4:,} (expect {args.expect_class4:,}) — STOP")
        return 2
    print(f"[{y}] anchors OK rows={n:,} class4={c4:,}")

    shard = PANEL / f"results_{y}.parquet"
    con.execute(
        f"""
        COPY (SELECT *, {y} AS src_year FROM {yrel}
              WHERE abs(hash(vehicle_id)) % 100 = 0)
        TO '{shard}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    pn = con.execute(f"SELECT count(*) FROM read_parquet('{shard}')").fetchone()[0]
    print(f"[{y}] panel shard rows={pn:,} ({100.0*pn/n:.3f}% of year)")

    vf = PANEL / f"_veh_{y}.parquet"
    con.execute(
        f"""
        COPY (SELECT DISTINCT vehicle_id, {y} AS first_year_seen, 1 AS n_years FROM {yrel})
        TO '{vf}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )

    if args.profile:
        block = {}
        block["census"] = [
            dict(zip(["test_class_id", "test_type", "outcome", "n"], r))
            for r in con.execute(
                f"SELECT test_class_id, test_type, outcome, count(*) FROM {yrel} GROUP BY 1,2,3 ORDER BY 1,2,3"
            ).fetchall()
        ]
        block["sanity"] = dict(
            zip(
                ["n", "mileage_null", "mileage_zero", "mileage_gt200k", "m_p50", "m_p95",
                 "fud_null", "age_p50", "age_p95", "schema_epochs", "dmin", "dmax"],
                con.execute(
                    f"""SELECT count(*),
                           count(*) FILTER (WHERE test_mileage IS NULL),
                           count(*) FILTER (WHERE test_mileage = 0),
                           count(*) FILTER (WHERE test_mileage > 200000),
                           approx_quantile(test_mileage, 0.50),
                           approx_quantile(test_mileage, 0.95),
                           count(*) FILTER (WHERE first_use_date IS NULL),
                           approx_quantile(age_at_test, 0.50),
                           approx_quantile(age_at_test, 0.95),
                           list(DISTINCT schema_epoch),
                           min(test_date), max(test_date)
                    FROM {yrel}"""
                ).fetchone(),
            )
        )
        block["vehicle_day"] = dict(
            zip(
                ["distinct_vehicles", "vehicle_days", "multi_test_vehicle_days", "tests_on_multi_days"],
                con.execute(
                    f"""WITH vd AS (SELECT vehicle_id, test_date, count(*) c FROM {yrel} GROUP BY 1,2)
                        SELECT (SELECT count(DISTINCT vehicle_id) FROM {yrel}),
                               count(*), count(*) FILTER (WHERE c>1), sum(c) FILTER (WHERE c>1)
                        FROM vd"""
                ).fetchone(),
            )
        )
        irel = f"read_parquet('{LAKE}/items/test_year={y}/*.parquet')"
        block["items_join"] = [
            dict(zip(["outcome", "tests", "tests_with_items", "item_rows"], r))
            for r in con.execute(
                f"""
                SELECT r.outcome, count(DISTINCT r.test_id) tests,
                       count(DISTINCT i.test_id) tests_with_items, count(i.test_id) item_rows
                FROM {yrel} r LEFT JOIN {irel} i ON r.test_id = i.test_id
                GROUP BY 1 ORDER BY 1
                """
            ).fetchall()
        ]
        prof_path = OUT / "parked_year_profiles.json"
        allp = json.loads(prof_path.read_text()) if prof_path.exists() else {}
        allp[str(y)] = block
        prof_path.write_text(json.dumps(allp, indent=1, default=str))
        print(f"[{y}] profile block written")

    print(f"PANEL_YEAR {y} COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
