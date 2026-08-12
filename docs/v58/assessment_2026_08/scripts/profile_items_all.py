#!/usr/bin/env python3
"""Phase-2 items profiling: ALL 19 local item years (2005-2023), serialized.

Collection + hard instrument assertions:
  - case-sensitive rfr_type_code x era x year census        -> items_census.json
    * era-level sums MUST equal the recorded F-22 decider table exactly
      (#18_F22_DECIDER.md:148-156). Conflict = STOP (never a second verdict).
    * total MUST equal 1,289,329,470.
  - certified vs CORRECTED (F-22) failing-item counts/yr    -> derived in census file
  - dangerous_mark value census x year                      -> items_dangerous_mark.json
  - location_id completeness + cardinality x year           -> items_location.json
  - catalogue guards THEN coverage:
      item_detail uniqueness on rfr_id / (rfr_id, class)    -> catalogue_guards.json
      distinct lake rfr_id x era vs catalogue (class-4 map,
      DISTINCT-map with fan-out accounting)                 -> items_rfr_coverage.json
  - component_category NULL share among certified/corrected
    failing items per year (decomposition input)            -> items_category_coverage.json
  - items-per-test (items-side only, no results join):
      distinct test_ids with items per year + per-test item
      count distribution buckets                            -> items_per_test.json
"""
import json
import os
import sys
from pathlib import Path

import duckdb

LAKE = "/Users/henrirapson/autosafe/autosafe_lake"
GLOB = f"{LAKE}/items/test_year=*/*.parquet"
REL = f"read_parquet('{GLOB}', hive_partitioning=1)"
LOOKUP = "/Users/henrirapson/autosafe_raw/lookup"
OUT = Path(__file__).resolve().parent.parent / "out"
SCRATCH = Path(
    "/private/tmp/claude-501/-Users-henrirapson/8a63890e-ab45-4b34-aa64-28cfebe748f3/scratchpad"
)

ANCHOR_ITEMS = 1_289_329_470
# Recorded F-22 decider census (d7 #18_F22_DECIDER.md:148-156) — verification target.
F22 = {
    ("post_2018", "A"): 285_851_673,
    ("post_2018", "F"): 123_278_768,
    ("post_2018", "M"): 31_748_964,
    ("post_2018", "P"): 18_715_642,
    ("pre_2018", "A"): 409_277_161,
    ("pre_2018", "F"): 362_670_156,
    ("pre_2018", "P"): 57_787_106,
}
# Corrected severity overlay (F-22 verdict): fail-bearing codes by era.
FAIL_CODES = {"pre_2018": ("F", "P"), "post_2018": ("F", "P")}
YEARS = list(range(2005, 2024))


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    tmp = SCRATCH / f"duckdb_tmp_{os.getpid()}"
    tmp.mkdir(parents=True, exist_ok=True)
    con.execute(f"PRAGMA temp_directory='{tmp}'")
    con.execute("PRAGMA max_temp_directory_size='8GiB'")
    con.execute("PRAGMA memory_limit='3.5GB'")
    return con


def rows(con, sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def main() -> int:
    con = connect()

    census = rows(
        con,
        f"""
        SELECT test_year, taxonomy_era, rfr_type_code, count(*) AS n,
               count(*) FILTER (WHERE is_fail_item) AS n_certified_fail
        FROM {REL} GROUP BY 1,2,3 ORDER BY 1,2,3
        """,
    )
    total = sum(r["n"] for r in census)
    assert total == ANCHOR_ITEMS, f"items census total {total:,} != anchor {ANCHOR_ITEMS:,}"

    era_code: dict[tuple[str, str], int] = {}
    for r in census:
        k = (r["taxonomy_era"], r["rfr_type_code"])
        era_code[k] = era_code.get(k, 0) + r["n"]
    mismatches = {
        k: (era_code.get(k), v) for k, v in F22.items() if era_code.get(k) != v
    }
    extras = {k: v for k, v in era_code.items() if k not in F22}
    if mismatches or extras:
        print("F-22 VERIFICATION CONFLICT — STOP", mismatches, extras)
        (OUT / "items_census.json").write_text(json.dumps(census, indent=1, default=str))
        return 2
    print("[census] total OK; F-22 era-level table reproduced EXACTLY (7/7 cells, no extras)")

    per_year_sev = {}
    for r in census:
        y = int(r["test_year"])
        d = per_year_sev.setdefault(
            y, {"items": 0, "certified_fail": 0, "corrected_fail": 0, "minor_post2018": 0, "advisory": 0}
        )
        d["items"] += r["n"]
        d["certified_fail"] += r["n_certified_fail"]
        if r["rfr_type_code"] in FAIL_CODES[r["taxonomy_era"]]:
            d["corrected_fail"] += r["n"]
        if r["taxonomy_era"] == "post_2018" and r["rfr_type_code"] == "M":
            d["minor_post2018"] += r["n"]
        if r["rfr_type_code"] == "A":
            d["advisory"] += r["n"]
    (OUT / "items_census.json").write_text(
        json.dumps({"census": census, "per_year_severity": per_year_sev}, indent=1, default=str)
    )

    dmark = rows(
        con,
        f"""
        SELECT test_year, taxonomy_era,
               coalesce(nullif(trim(dangerous_mark), ''), '<null-or-blank>') AS dangerous_mark,
               count(*) AS n
        FROM {REL} GROUP BY 1,2,3 ORDER BY 1,2,3
        """,
    )
    (OUT / "items_dangerous_mark.json").write_text(json.dumps(dmark, indent=1, default=str))
    print("[dangerous_mark] done")

    loc = rows(
        con,
        f"""
        SELECT test_year,
               count(*) n,
               count(*) FILTER (WHERE location_id IS NULL OR trim(location_id)='') loc_missing,
               count(DISTINCT location_id) loc_distinct
        FROM {REL} GROUP BY 1 ORDER BY 1
        """,
    )
    (OUT / "items_location.json").write_text(json.dumps(loc, indent=1, default=str))
    print("[location] done")

    # --- catalogue guards BEFORE any coverage join (B2) ---
    con.execute(
        f"""
        CREATE TEMP TABLE detail AS
        SELECT trim(rfr_id) AS rfr_id, trim(test_class_id) AS test_class_id,
               trim(rfr_deficiency_category) AS deficiency_category,
               trim(test_item_set_section_id) AS section_id
        FROM read_csv('{LOOKUP}/item_detail.csv', delim='|', header=true,
                      all_varchar=true)
        """
    )
    guards = {
        "detail_rows": con.execute("SELECT count(*) FROM detail").fetchone()[0],
        "distinct_rfr_id": con.execute("SELECT count(DISTINCT rfr_id) FROM detail").fetchone()[0],
        "distinct_rfr_id_class": con.execute(
            "SELECT count(*) FROM (SELECT DISTINCT rfr_id, test_class_id FROM detail)"
        ).fetchone()[0],
        "rfr_id_multi_class": con.execute(
            """SELECT count(*) FROM (
                 SELECT rfr_id FROM (SELECT DISTINCT rfr_id, test_class_id FROM detail)
                 GROUP BY rfr_id HAVING count(*) > 1)"""
        ).fetchone()[0],
        "rfr_id_multi_deficiency_within_class4": con.execute(
            """SELECT count(*) FROM (
                 SELECT rfr_id FROM (SELECT DISTINCT rfr_id, deficiency_category
                                     FROM detail WHERE test_class_id='4')
                 GROUP BY rfr_id HAVING count(*) > 1)"""
        ).fetchone()[0],
        "class4_rfr_ids": con.execute(
            "SELECT count(DISTINCT rfr_id) FROM detail WHERE test_class_id='4'"
        ).fetchone()[0],
        "class4_by_deficiency": rows(
            con,
            """SELECT deficiency_category, count(DISTINCT rfr_id) n
               FROM detail WHERE test_class_id='4' GROUP BY 1 ORDER BY 1""",
        ),
    }
    (OUT / "catalogue_guards.json").write_text(json.dumps(guards, indent=1, default=str))
    print(f"[catalogue guards] {json.dumps({k: v for k, v in guards.items() if not isinstance(v, list)})}")

    lake_codes = rows(
        con,
        f"""
        WITH lk AS (SELECT DISTINCT taxonomy_era, trim(rfr_id) AS rfr_id FROM {REL})
        SELECT lk.taxonomy_era,
               count(*) AS distinct_rfr_ids,
               count(*) FILTER (WHERE d4.rfr_id IS NOT NULL) AS in_class4_catalogue,
               count(*) FILTER (WHERE dall.rfr_id IS NOT NULL) AS in_any_class_catalogue
        FROM lk
        LEFT JOIN (SELECT DISTINCT rfr_id FROM detail WHERE test_class_id='4') d4 USING (rfr_id)
        LEFT JOIN (SELECT DISTINCT rfr_id FROM detail) dall USING (rfr_id)
        GROUP BY 1 ORDER BY 1
        """,
    )
    lake_rows_cov = rows(
        con,
        f"""
        SELECT i.taxonomy_era,
               count(*) AS item_rows,
               count(*) FILTER (WHERE d4.rfr_id IS NOT NULL) AS rows_rfr_in_class4_catalogue,
               count(*) FILTER (WHERE dall.rfr_id IS NOT NULL) AS rows_rfr_in_any_catalogue
        FROM {REL} i
        LEFT JOIN (SELECT DISTINCT rfr_id FROM detail WHERE test_class_id='4') d4
               ON trim(i.rfr_id) = d4.rfr_id
        LEFT JOIN (SELECT DISTINCT rfr_id FROM detail) dall
               ON trim(i.rfr_id) = dall.rfr_id
        GROUP BY 1 ORDER BY 1
        """,
    )
    (OUT / "items_rfr_coverage.json").write_text(
        json.dumps({"distinct_codes": lake_codes, "row_coverage": lake_rows_cov}, indent=1, default=str)
    )
    print("[rfr coverage] done")

    cat = rows(
        con,
        f"""
        SELECT test_year, taxonomy_era,
               count(*) FILTER (WHERE is_fail_item) AS certified_fail,
               count(*) FILTER (WHERE is_fail_item AND component_category IS NULL) AS certified_fail_uncat,
               count(*) FILTER (WHERE rfr_type_code IN ('F','P')) AS corrected_fail,
               count(*) FILTER (WHERE rfr_type_code IN ('F','P') AND component_category IS NULL) AS corrected_fail_uncat,
               count(*) FILTER (WHERE component_category IS NULL) AS all_uncat,
               count(*) AS n
        FROM {REL} GROUP BY 1,2 ORDER BY 1,2
        """,
    )
    (OUT / "items_category_coverage.json").write_text(json.dumps(cat, indent=1, default=str))
    print("[category coverage] done")

    ipt = []
    for y in YEARS:
        yrel = f"read_parquet('{LAKE}/items/test_year={y}/*.parquet')"
        r = con.execute(
            f"""
            WITH pt AS (SELECT test_id, count(*) c FROM {yrel} GROUP BY 1)
            SELECT count(*) tests_with_items,
                   sum(c) item_rows,
                   approx_quantile(c, 0.50) c_p50,
                   approx_quantile(c, 0.90) c_p90,
                   approx_quantile(c, 0.99) c_p99,
                   max(c) c_max,
                   count(*) FILTER (WHERE c = 1) tests_1,
                   count(*) FILTER (WHERE c BETWEEN 2 AND 3) tests_2_3,
                   count(*) FILTER (WHERE c BETWEEN 4 AND 6) tests_4_6,
                   count(*) FILTER (WHERE c >= 7) tests_7p
            FROM pt
            """
        ).fetchone()
        ipt.append(
            dict(
                zip(
                    [
                        "tests_with_items", "item_rows", "c_p50", "c_p90", "c_p99",
                        "c_max", "tests_1", "tests_2_3", "tests_4_6", "tests_7p",
                    ],
                    r,
                )
            )
            | {"test_year": y}
        )
        print(f"[items-per-test] {y} tests_with_items={r[0]:,} rows={r[1]:,}")
    (OUT / "items_per_test.json").write_text(json.dumps(ipt, indent=1, default=str))

    print("PROFILE_ITEMS_ALL COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
