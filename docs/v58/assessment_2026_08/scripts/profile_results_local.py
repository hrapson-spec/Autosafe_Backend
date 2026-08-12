#!/usr/bin/env python3
"""Phase-2 results profiling: local years 2015-2023, serialized single process.

Collection only (no comparisons beyond hard instrument assertions):
  - outcome x test_type x test_class_id x year census      -> results_census.json
  - D7 population + initial/final failure rates per year x class group
    via target_population SQL twins (rule of record)        -> results_rates.json
  - per-column NULL/blank completeness per year             -> results_completeness.json
  - schema_epoch / age_source / taxonomy_era x year         -> results_regime.json
  - mileage + first_use/age sanity per year                 -> results_field_sanity.json
  - make/model_id/postcode_area coverage per year           -> results_entity_coverage.json
  - per-year distinct vehicles + same-day multi-test shares -> results_vehicle_day.json

Instrument assertions (STOP on failure):
  census total == 354,057,034 (local footer anchor)
  year set == {2015..2023}
"""
import json
import os
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, "/Users/henrirapson/autosafe-v58")
from pipeline.lake.target_population import (  # noqa: E402
    final_failure_sql,
    initial_failure_sql,
    initial_test_sql,
)

LAKE = "/Users/henrirapson/autosafe/autosafe_lake"
GLOB = f"{LAKE}/results/test_year=*/*.parquet"
OUT = Path(__file__).resolve().parent.parent / "out"
SCRATCH = Path(
    "/private/tmp/claude-501/-Users-henrirapson/8a63890e-ab45-4b34-aa64-28cfebe748f3/scratchpad"
)
ANCHOR_LOCAL_RESULTS = 354_057_034
YEARS = list(range(2015, 2024))

REL = f"read_parquet('{GLOB}', hive_partitioning=1)"


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
    out: dict[str, object] = {}

    census = rows(
        con,
        f"""
        SELECT test_year, test_class_id, test_type, outcome, count(*) AS n
        FROM {REL}
        GROUP BY 1,2,3,4 ORDER BY 1,2,3,4
        """,
    )
    total = sum(r["n"] for r in census)
    years = sorted({r["test_year"] for r in census})
    assert total == ANCHOR_LOCAL_RESULTS, f"census total {total} != anchor"
    assert years == YEARS, f"unexpected year set {years}"
    (OUT / "results_census.json").write_text(json.dumps(census, indent=1, default=str))
    print(f"[census] rows={total:,} years={years[0]}..{years[-1]} cells={len(census)}")

    r = "r"
    rates = rows(
        con,
        f"""
        SELECT test_year,
               CASE WHEN r.test_class_id IN ('1','2') THEN 'C1&2'
                    WHEN r.test_class_id IN ('3','4') THEN 'C3&4'
                    WHEN r.test_class_id = '5' THEN 'C5'
                    WHEN r.test_class_id = '7' THEN 'C7'
                    ELSE 'other' END AS class_group,
               count(*) FILTER (WHERE {initial_test_sql(r)}) AS nt_definitive,
               count(*) FILTER (WHERE {initial_test_sql(r)} AND {final_failure_sql(r)}) AS final_fail,
               count(*) FILTER (WHERE {initial_test_sql(r)} AND {initial_failure_sql(r)}) AS initial_fail,
               count(*) AS rows_total
        FROM {REL} r
        GROUP BY 1,2 ORDER BY 1,2
        """,
    )
    (OUT / "results_rates.json").write_text(json.dumps(rates, indent=1, default=str))
    print(f"[rates] cells={len(rates)}")

    cols = [
        "test_id", "vehicle_id", "test_date", "test_class_id", "test_type", "outcome",
        "test_mileage", "postcode_area", "make", "model", "model_id", "colour",
        "fuel_type", "cylinder_capacity", "first_use_date", "age_source", "age_at_test",
        "taxonomy_era", "schema_epoch",
    ]
    null_exprs = ",\n".join(
        f"count(*) FILTER (WHERE {c} IS NULL) AS null_{c}" for c in cols
    )
    blank_exprs = ",\n".join(
        f"count(*) FILTER (WHERE trim({c}) = '') AS blank_{c}"
        for c in ["postcode_area", "make", "model", "colour", "fuel_type", "test_class_id", "test_type"]
    )
    completeness = rows(
        con,
        f"SELECT test_year, count(*) AS n, {null_exprs}, {blank_exprs} FROM {REL} GROUP BY 1 ORDER BY 1",
    )
    (OUT / "results_completeness.json").write_text(json.dumps(completeness, indent=1, default=str))
    print("[completeness] done")

    regime = {
        "schema_epoch": rows(con, f"SELECT test_year, schema_epoch, count(*) n FROM {REL} GROUP BY 1,2 ORDER BY 1,2"),
        "age_source": rows(con, f"SELECT test_year, age_source, count(*) n FROM {REL} GROUP BY 1,2 ORDER BY 1,2"),
        "taxonomy_era": rows(con, f"SELECT test_year, taxonomy_era, count(*) n FROM {REL} GROUP BY 1,2 ORDER BY 1,2"),
        "date_range": rows(con, f"SELECT test_year, min(test_date) dmin, max(test_date) dmax FROM {REL} GROUP BY 1 ORDER BY 1"),
    }
    (OUT / "results_regime.json").write_text(json.dumps(regime, indent=1, default=str))
    print("[regime] done")

    sanity = rows(
        con,
        f"""
        SELECT test_year, count(*) n,
               count(*) FILTER (WHERE test_mileage IS NULL) mileage_null,
               count(*) FILTER (WHERE test_mileage = 0) mileage_zero,
               count(*) FILTER (WHERE test_mileage < 0) mileage_neg,
               count(*) FILTER (WHERE test_mileage > 200000) mileage_gt200k,
               count(*) FILTER (WHERE test_mileage > 1000000) mileage_gt1m,
               approx_quantile(test_mileage, 0.05) m_p05,
               approx_quantile(test_mileage, 0.25) m_p25,
               approx_quantile(test_mileage, 0.50) m_p50,
               approx_quantile(test_mileage, 0.75) m_p75,
               approx_quantile(test_mileage, 0.95) m_p95,
               count(*) FILTER (WHERE first_use_date IS NULL) fud_null,
               approx_quantile(age_at_test, 0.05) age_p05,
               approx_quantile(age_at_test, 0.50) age_p50,
               approx_quantile(age_at_test, 0.95) age_p95,
               count(*) FILTER (WHERE age_at_test < 0) age_neg,
               count(*) FILTER (WHERE age_at_test > 50) age_gt50
        FROM {REL} GROUP BY 1 ORDER BY 1
        """,
    )
    (OUT / "results_field_sanity.json").write_text(json.dumps(sanity, indent=1, default=str))
    print("[sanity] done")

    entity = rows(
        con,
        f"""
        SELECT test_year,
               count(DISTINCT make) n_makes,
               count(*) FILTER (WHERE make = 'UNCLASSIFIED') make_unclassified,
               count(DISTINCT model_id) n_model_ids,
               count(DISTINCT postcode_area) n_postcode_areas,
               count(*) FILTER (WHERE postcode_area IS NULL OR trim(postcode_area)='') pc_missing,
               count(DISTINCT fuel_type) n_fuel_types,
               count(*) FILTER (WHERE cylinder_capacity IS NULL) cc_null
        FROM {REL} GROUP BY 1 ORDER BY 1
        """,
    )
    (OUT / "results_entity_coverage.json").write_text(json.dumps(entity, indent=1, default=str))
    print("[entity] done")

    per_year = []
    for y in YEARS:
        yrel = f"read_parquet('{LAKE}/results/test_year={y}/*.parquet')"
        dv = con.execute(f"SELECT count(DISTINCT vehicle_id) FROM {yrel}").fetchone()[0]
        sd = con.execute(
            f"""
            WITH vd AS (SELECT vehicle_id, test_date, count(*) c FROM {yrel} GROUP BY 1,2)
            SELECT count(*) AS vehicle_days,
                   count(*) FILTER (WHERE c > 1) AS multi_test_vehicle_days,
                   sum(c) FILTER (WHERE c > 1) AS tests_on_multi_days,
                   sum(c) AS tests_total
            FROM vd
            """
        ).fetchone()
        per_year.append(
            {
                "test_year": y,
                "distinct_vehicles": dv,
                "vehicle_days": sd[0],
                "multi_test_vehicle_days": sd[1],
                "tests_on_multi_days": sd[2],
                "tests_total": sd[3],
            }
        )
        print(f"[vehicle-day] {y} distinct={dv:,} multi_share={sd[1]/sd[0]:.5f}")
    (OUT / "results_vehicle_day.json").write_text(json.dumps(per_year, indent=1, default=str))

    print("PROFILE_RESULTS_LOCAL COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
