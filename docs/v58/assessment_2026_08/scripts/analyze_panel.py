#!/usr/bin/env python3
"""Phase-4 panel analyses. Runs ONLY after PHASE3 COMPLETE (19 shards + items join).

Outputs (out/):
  panel_validation.json     — per-year x100 counts vs anchors (band ±0.8%) + rate checks
  panel_depth_by_year.json  — event-grain history-depth distributions by calendar year
                              (all events + clean cohort first_use>=2005), D13-safe
  panel_defect_depth.json   — prior defect-history depth (corrected severity) by year
  panel_gaps_mileage.json   — inter-test gaps + mileage-ladder depth/monotonicity
  panel_cohorts.json        — §13 coverage cohorts (min-cell 500 flagged)
  panel_continuity_50k.json — checks.py-exact continuity metrics on ALL panel
                              multi-test vehicles (n >> 50k; no reservoir)
  panel_vehicles.json       — exact unique vehicles (running file) + spans
  panel_deep_history.json   — present-day deep-history population, vehicle-bootstrap CI
  panel_completeness.json   — per-column missing shares by year (panel-based, all 19y)
  panel_identity_check.json — test_id semi-join vs research test_grain_history_v2

Event definition (rule of record): test_type='NT' AND outcome IN (PASS,FAIL,PRS).
Priors: strictly earlier CALENDAR DAYS only (day-grain windows; D13-safe — no
within-day ordering is ever consulted). Same-day co-tests flagged, never ordered.
Corrected severity (F-22): fail-bearing item codes = {F,P} both eras; post-2018 M
= minor (non-fail); dangerous signal = dangerous_mark only.
"""
import json
import os
import sys
from pathlib import Path

import duckdb
import numpy as np

OUT = Path(__file__).resolve().parent.parent / "out"
SCRATCH = Path(
    "/private/tmp/claude-501/-Users-henrirapson/8a63890e-ab45-4b34-aa64-28cfebe748f3/scratchpad"
)
PANEL = SCRATCH / "panel"
RES = f"read_parquet('{PANEL}/results_*.parquet')"
ITM = f"read_parquet('{PANEL}/items_panel.parquet')"
RUNNING = PANEL / "all_vehicles_running.parquet"
TGH2 = "/Users/henrirapson/autosafe/work/goal_0750/feature_repr_review_v1/artifacts/test_grain_history_v2.parquet"
EXPECTED_DUCKDB = "1.5.5"

YEAR_ROWS = {  # exact per-year totals (footers / download_record)
    2005: 7499744, 2006: 32014080, 2007: 33591238, 2008: 34439132, 2009: 35436943,
    2010: 36134920, 2011: 36849154, 2012: 36846342, 2013: 37361925, 2014: 37493825,
    2015: 37490736, 2016: 37693380, 2017: 38056161, 2018: 38681801, 2019: 39310698,
    2020: 38594013, 2021: 40380646, 2022: 41632878, 2023: 42216721,
}
TOL = 0.008  # ±0.8% preregistered band (~±4 SD at 1/100 vehicle-cluster sampling)
DEPTH_BUCKETS = "CASE WHEN {c}=0 THEN '0' WHEN {c}=1 THEN '1' WHEN {c}=2 THEN '2' WHEN {c}<=5 THEN '3-5' WHEN {c}<=10 THEN '6-10' WHEN {c}<=20 THEN '11-20' ELSE '21+' END"


def connect():
    assert duckdb.__version__ == EXPECTED_DUCKDB
    con = duckdb.connect()
    tmp = SCRATCH / f"duckdb_tmp_{os.getpid()}"
    tmp.mkdir(parents=True, exist_ok=True)
    con.execute(f"PRAGMA temp_directory='{tmp}'")
    con.execute("PRAGMA max_temp_directory_size='6GiB'")
    con.execute("PRAGMA memory_limit='3.5GB'")
    return con


def rows(con, sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def jdump(name, obj):
    (OUT / name).write_text(json.dumps(obj, indent=1, default=str))
    print(f"[out] {name}")


def main() -> int:
    con = connect()

    # ---------- 0. build day-grain + event tables (temp) ----------
    con.execute(
        f"""
        CREATE TEMP TABLE item_per_test AS
        SELECT test_id,
               count(*) AS n_items,
               count(*) FILTER (WHERE rfr_type_code IN ('F','P')) AS n_fail_items_corr,
               count(*) FILTER (WHERE rfr_type_code = 'A') AS n_adv_items,
               count(*) FILTER (WHERE taxonomy_era='post_2018' AND rfr_type_code='M') AS n_minor_items,
               count(*) FILTER (WHERE trim(coalesce(dangerous_mark,'')) <> '') AS n_dangerous_marked
        FROM {ITM} GROUP BY 1
        """
    )
    con.execute(
        f"""
        CREATE TEMP TABLE day_grain AS
        SELECT r.vehicle_id, r.test_date,
               count(*) AS n_tests_day,
               count(*) FILTER (WHERE r.test_type='NT' AND r.outcome IN ('PASS','FAIL','PRS')) AS n_initial_day,
               count(*) FILTER (WHERE r.test_type='NT' AND r.outcome='FAIL') AS n_final_fail_day,
               max(CASE WHEN i.n_items IS NOT NULL THEN 1 ELSE 0 END) AS any_items_day,
               sum(coalesce(i.n_items,0)) AS n_items_day,
               sum(coalesce(i.n_fail_items_corr,0)) AS n_fail_items_day,
               sum(coalesce(i.n_adv_items,0)) AS n_adv_items_day,
               sum(coalesce(i.n_minor_items,0)) AS n_minor_items_day,
               max(CASE WHEN r.test_mileage > 0 THEN r.test_mileage END) AS max_valid_mileage_day,
               count(*) FILTER (WHERE r.test_mileage > 0) AS n_valid_mileage_day
        FROM {RES} r LEFT JOIN item_per_test i USING (test_id)
        GROUP BY 1,2
        """
    )
    con.execute(
        """
        CREATE TEMP TABLE day_hist AS
        SELECT *,
          count(*)              OVER w AS prior_days,
          sum(n_tests_day)      OVER w AS prior_tests,
          sum(n_initial_day)    OVER w AS prior_initials,
          sum(n_final_fail_day) OVER w AS prior_final_fails,
          sum(any_items_day)    OVER w AS prior_defect_bearing_days,
          sum(n_items_day)      OVER w AS prior_items,
          sum(n_fail_items_day) OVER w AS prior_fail_items,
          sum(n_adv_items_day)  OVER w AS prior_adv_items,
          sum(n_minor_items_day) OVER w AS prior_minor_items,
          sum(n_valid_mileage_day) OVER w AS prior_valid_mileages,
          max(max_valid_mileage_day) OVER w AS prior_max_mileage,
          min(test_date)        OVER w AS first_prior_date,
          max(test_date)        OVER w AS last_prior_date
        FROM day_grain
        WINDOW w AS (PARTITION BY vehicle_id ORDER BY test_date
                     ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
        """
    )
    con.execute(
        f"""
        CREATE TEMP TABLE events AS
        SELECT r.test_id, r.vehicle_id, r.test_date, r.src_year AS event_year,
               r.test_class_id, r.outcome, r.make, r.fuel_type, r.test_mileage,
               r.first_use_date, r.age_at_test,
               d.n_tests_day > 1 AS same_day_multi,
               coalesce(d.prior_tests, 0) AS prior_tests,
               coalesce(d.prior_initials, 0) AS prior_initials,
               coalesce(d.prior_final_fails, 0) AS prior_final_fails,
               coalesce(d.prior_defect_bearing_days, 0) AS prior_defect_bearing_days,
               coalesce(d.prior_items, 0) AS prior_items,
               coalesce(d.prior_fail_items, 0) AS prior_fail_items,
               coalesce(d.prior_adv_items, 0) AS prior_adv_items,
               coalesce(d.prior_minor_items, 0) AS prior_minor_items,
               coalesce(d.prior_valid_mileages, 0) AS prior_valid_mileages,
               d.prior_max_mileage,
               CASE WHEN d.max_valid_mileage_day IS NOT NULL AND d.prior_max_mileage IS NOT NULL
                    AND d.max_valid_mileage_day < d.prior_max_mileage THEN 1 ELSE 0 END AS mileage_regression_flag,
               date_diff('day', d.last_prior_date, r.test_date) AS gap_days,
               date_diff('day', d.first_prior_date, r.test_date) / 365.25 AS history_years,
               date_diff('day', greatest(r.first_use_date, DATE '2005-01-01'), r.test_date) / 365.25
                   AS observable_years,
               r.first_use_date >= DATE '2005-01-01' AS clean_cohort
        FROM {RES} r
        JOIN day_hist d ON r.vehicle_id = d.vehicle_id AND r.test_date = d.test_date
        WHERE r.test_type='NT' AND r.outcome IN ('PASS','FAIL','PRS')
        """
    )
    n_events = con.execute("SELECT count(*) FROM events").fetchone()[0]
    print(f"[events] {n_events:,} NT+definitive panel events")

    # ---------- 1. validation ----------
    val = rows(
        con,
        f"SELECT src_year AS y, count(*) n FROM {RES} GROUP BY 1 ORDER BY 1",
    )
    checks = []
    ok = True
    for r in val:
        exp = YEAR_ROWS[int(r["y"])]
        rel = abs(r["n"] * 100 - exp) / exp
        passed = rel <= TOL
        ok &= passed
        checks.append({"year": int(r["y"]), "panel_n": r["n"], "x100": r["n"] * 100,
                       "exact": exp, "rel_dev": round(rel, 5), "pass": passed})
    # rate check: C3&4 initial/final rates vs exact (Phase-2 + parked profiles read by memo)
    rates = rows(
        con,
        """
        SELECT event_year,
               count(*) AS nt,
               avg(CASE WHEN outcome='FAIL' THEN 1.0 ELSE 0 END) AS final_rate,
               avg(CASE WHEN outcome IN ('FAIL','PRS') THEN 1.0 ELSE 0 END) AS initial_rate
        FROM events WHERE test_class_id IN ('3','4') GROUP BY 1 ORDER BY 1
        """,
    )
    jdump("panel_validation.json", {"counts": checks, "c34_rates_panel": rates, "all_pass": ok})
    if not ok:
        print("PANEL VALIDATION FAILED — STOP")
        return 2

    # ---------- 2. depth distributions by calendar year ----------
    def depth_block(where: str) -> list:
        b = DEPTH_BUCKETS.format(c="prior_tests")
        return rows(
            con,
            f"""
            SELECT event_year, {b} AS bucket, count(*) n,
                   avg(prior_tests) mean_priors,
                   avg(CASE WHEN outcome='FAIL' THEN 1.0 ELSE 0 END) final_fail_rate
            FROM events {where}
            GROUP BY 1,2 ORDER BY 1,2
            """,
        )

    quant = rows(
        con,
        """
        SELECT event_year,
               count(*) n,
               avg(prior_tests) mean_prior_tests,
               approx_quantile(prior_tests, 0.50) p50,
               approx_quantile(prior_tests, 0.90) p90,
               approx_quantile(prior_tests, 0.99) p99,
               max(prior_tests) mx,
               avg(history_years) mean_hist_years,
               approx_quantile(history_years, 0.50) hy_p50,
               approx_quantile(history_years, 0.90) hy_p90,
               avg(observable_years) mean_observable_years,
               avg(CASE WHEN clean_cohort THEN 1.0 ELSE 0 END) clean_share,
               avg(CASE WHEN same_day_multi THEN 1.0 ELSE 0 END) same_day_share
        FROM events GROUP BY 1 ORDER BY 1
        """,
    )
    jdump(
        "panel_depth_by_year.json",
        {
            "quantiles_all": quant,
            "buckets_all": depth_block(""),
            "buckets_clean_cohort": depth_block("WHERE clean_cohort"),
        },
    )

    # ---------- 3. defect-history depth ----------
    dfb = DEPTH_BUCKETS.format(c="prior_defect_bearing_days")
    defect = {
        "by_year": rows(
            con,
            """
            SELECT event_year, count(*) n,
                   avg(prior_items) mean_prior_items,
                   approx_quantile(prior_items, 0.50) items_p50,
                   approx_quantile(prior_items, 0.90) items_p90,
                   approx_quantile(prior_items, 0.99) items_p99,
                   avg(prior_fail_items) mean_prior_fail_items,
                   avg(prior_adv_items) mean_prior_adv_items,
                   avg(prior_minor_items) mean_prior_minor_items,
                   avg(CASE WHEN prior_defect_bearing_days > 0 THEN 1.0 ELSE 0 END) share_any_defect_history,
                   avg(CASE WHEN prior_defect_bearing_days >= 3 THEN 1.0 ELSE 0 END) share_3plus_defect_tests
            FROM events GROUP BY 1 ORDER BY 1
            """,
        ),
        "defect_bearing_buckets": rows(
            con,
            f"""
            SELECT event_year, {dfb} AS bucket, count(*) n,
                   avg(CASE WHEN outcome='FAIL' THEN 1.0 ELSE 0 END) final_fail_rate
            FROM events GROUP BY 1,2 ORDER BY 1,2
            """,
        ),
    }
    jdump("panel_defect_depth.json", defect)

    # ---------- 4. gaps + mileage ladder ----------
    jdump(
        "panel_gaps_mileage.json",
        {
            "gaps": rows(
                con,
                """
                SELECT event_year, count(*) n_with_prior,
                       approx_quantile(gap_days, 0.05) g_p05,
                       approx_quantile(gap_days, 0.50) g_p50,
                       approx_quantile(gap_days, 0.95) g_p95,
                       avg(CASE WHEN gap_days <= 60 THEN 1.0 ELSE 0 END) share_le60d,
                       avg(CASE WHEN gap_days BETWEEN 300 AND 430 THEN 1.0 ELSE 0 END) share_annual_band
                FROM events WHERE prior_tests > 0 GROUP BY 1 ORDER BY 1
                """,
            ),
            "mileage_ladder": rows(
                con,
                """
                SELECT event_year, count(*) n,
                       avg(prior_valid_mileages) mean_prior_mileages,
                       approx_quantile(prior_valid_mileages, 0.50) pm_p50,
                       approx_quantile(prior_valid_mileages, 0.90) pm_p90,
                       avg(mileage_regression_flag) day_grain_regression_share
                FROM events GROUP BY 1 ORDER BY 1
                """,
            ),
            "caveat": "test_mileage has no unit column; DVSA documents pre-2022 km contamination (corrected upstream only from the 2022 dataset). Regression share conflates unit flips, corrections and genuine clocking.",
        },
    )

    # ---------- 5. §13 coverage cohorts ----------
    cohorts = {}
    for name, expr in {
        "age_band": "CASE WHEN age_at_test IS NULL THEN 'unknown' WHEN age_at_test < 3 THEN '0-2' WHEN age_at_test < 6 THEN '3-5' WHEN age_at_test < 10 THEN '6-9' WHEN age_at_test < 15 THEN '10-14' ELSE '15+' END",
        "fuel": "coalesce(nullif(trim(fuel_type),''),'<missing>')",
        "mileage_band": "CASE WHEN test_mileage IS NULL OR test_mileage<=0 THEN 'missing/0' WHEN test_mileage<30000 THEN '<30k' WHEN test_mileage<60000 THEN '30-60k' WHEN test_mileage<100000 THEN '60-100k' WHEN test_mileage<150000 THEN '100-150k' ELSE '150k+' END",
        "depth_bucket": DEPTH_BUCKETS.format(c="prior_tests"),
    }.items():
        cohorts[name] = rows(
            con,
            f"""
            SELECT {expr} AS cohort,
                   count(*) n,
                   avg(CASE WHEN outcome='FAIL' THEN 1.0 ELSE 0 END) final_fail_rate,
                   avg(prior_tests) mean_priors,
                   avg(prior_items) mean_prior_items,
                   count(*) < 500 AS min_cell_flag
            FROM events WHERE event_year >= 2019
            GROUP BY 1 ORDER BY n DESC
            """,
        )
    cohorts["make_top20"] = rows(
        con,
        """
        WITH top AS (SELECT make FROM events WHERE event_year>=2019 GROUP BY 1 ORDER BY count(*) DESC LIMIT 20)
        SELECT CASE WHEN make IN (SELECT make FROM top) THEN make ELSE '<other>' END AS cohort,
               count(*) n,
               avg(CASE WHEN outcome='FAIL' THEN 1.0 ELSE 0 END) final_fail_rate,
               avg(prior_tests) mean_priors,
               count(*) < 500 AS min_cell_flag
        FROM events WHERE event_year >= 2019 GROUP BY 1 ORDER BY n DESC
        """,
    )
    jdump("panel_cohorts.json", cohorts)

    # ---------- 6. continuity closure (checks.py-exact, ALL panel multi-test vehicles) ----------
    cont = con.execute(
        f"""
        WITH multi AS (
            SELECT vehicle_id FROM {RES} GROUP BY vehicle_id HAVING count(*) >= 3
        ),
        per_vehicle AS (
            SELECT r.vehicle_id,
                   count(DISTINCT year(r.test_date)) AS n_years,
                   count(DISTINCT r.first_use_date) FILTER (WHERE r.first_use_date IS NOT NULL) AS n_first_use,
                   median(gap) AS median_gap
            FROM (
                SELECT vehicle_id, test_date, first_use_date,
                       date_diff('day', lag(test_date) OVER (PARTITION BY vehicle_id
                                                              ORDER BY test_date, test_id),
                                 test_date) AS gap
                FROM {RES}
                WHERE vehicle_id IN (SELECT vehicle_id FROM multi)
            ) r GROUP BY r.vehicle_id
        )
        SELECT count(*), avg(CASE WHEN n_years>=2 THEN 1.0 ELSE 0 END),
               avg(CASE WHEN n_first_use>1 THEN 1.0 ELSE 0 END), median(median_gap)
        FROM per_vehicle
        """
    ).fetchone()
    jdump(
        "panel_continuity_50k.json",
        {
            "n_vehicles": cont[0], "multiyear_share": cont[1],
            "first_use_conflict_share": cont[2], "median_gap_days": cont[3],
            "definition": "checks.py:29-106 replicated exactly; population = ALL panel multi-test (>=3) vehicles, no reservoir; panel = abs(hash(vehicle_id))%100==0 across all 19 years",
            "bars": {"multiyear_min": 0.20, "conflict_max": 0.01, "gap_range": [200, 800]},
        },
    )
    print(f"[continuity] n={cont[0]:,} multiyear={cont[1]:.4f} conflict={cont[2]:.5f} gap={cont[3]}")

    # ---------- 7. unique vehicles ----------
    uv = con.execute(f"SELECT count(*), sum(CASE WHEN n_years>=2 THEN 1 ELSE 0 END) FROM read_parquet('{RUNNING}')").fetchone()
    jdump(
        "panel_vehicles.json",
        {
            "unique_vehicles_exact": uv[0],
            "vehicles_in_2plus_years": uv[1],
            "first_year_seen": rows(con, f"SELECT first_year_seen, count(*) n FROM read_parquet('{RUNNING}') GROUP BY 1 ORDER BY 1"),
            "n_years_dist": rows(con, f"SELECT n_years, count(*) n FROM read_parquet('{RUNNING}') GROUP BY 1 ORDER BY 1"),
        },
    )
    print(f"[vehicles] exact unique = {uv[0]:,}")

    # ---------- 8. present-day deep-history population + vehicle bootstrap ----------
    per_veh = con.execute(
        """
        SELECT vehicle_id,
               count(*) FILTER (WHERE event_year=2023 AND prior_initials>=6) AS deep6,
               count(*) FILTER (WHERE event_year=2023 AND prior_initials>=10) AS deep10,
               count(*) FILTER (WHERE event_year=2023 AND prior_defect_bearing_days>=3) AS defect3,
               count(*) FILTER (WHERE event_year=2023) AS ev2023
        FROM events GROUP BY 1
        """
    ).fetchnumpy()
    rng = np.random.default_rng(42)
    nveh = len(per_veh["vehicle_id"])
    stats = {}
    for key in ("deep6", "deep10", "defect3", "ev2023"):
        v = per_veh[key].astype(np.int64)
        point = int(v.sum()) * 100
        reps = np.empty(1000)
        for i in range(1000):
            w = rng.poisson(1.0, nveh)
            reps[i] = (v * w).sum() * 100
        stats[key] = {"point": point, "ci_lo": float(np.percentile(reps, 2.5)),
                      "ci_hi": float(np.percentile(reps, 97.5))}
    jdump("panel_deep_history.json", {
        "definitions": {
            "deep6": "2023 NT+definitive events with >=6 prior initial tests",
            "deep10": ">=10 prior initial tests",
            "defect3": ">=3 prior defect-bearing test days",
            "ev2023": "all 2023 NT+definitive events",
        },
        "x100_with_vehicle_bootstrap_ci": stats,
        "panel_vehicles_contributing": int(nveh),
    })

    # ---------- 9. panel-based per-column completeness by year ----------
    cols = ["test_mileage", "postcode_area", "make", "model", "colour", "fuel_type",
            "cylinder_capacity", "first_use_date", "test_class_id", "test_type"]
    parts = ",\n".join(
        f"avg(CASE WHEN {c} IS NULL THEN 1.0 ELSE 0 END) AS null_{c}" for c in cols
    )
    jdump("panel_completeness.json", rows(con, f"SELECT src_year, count(*) n, {parts} FROM {RES} GROUP BY 1 ORDER BY 1"))

    # ---------- 10. identity check vs research lake (bounded read-only) ----------
    idc = con.execute(
        f"""
        WITH tg AS (SELECT test_id, test_date FROM read_parquet('{TGH2}')
                    WHERE test_date <= DATE '2023-12-31'),
             p AS (SELECT DISTINCT test_id FROM {RES})
        SELECT count(*) AS tgh2_rows_le2023,
               count(*) FILTER (WHERE test_id IN (SELECT test_id FROM p)) AS matched_in_panel
        FROM tg
        """
    ).fetchone()
    expected_rate = 0.01
    obs_rate = idc[1] / idc[0] if idc[0] else None
    jdump("panel_identity_check.json", {
        "tgh2_rows_le2023": idc[0], "matched_in_panel": idc[1],
        "observed_match_rate": obs_rate, "expected_if_same_id_space": expected_rate,
        "ratio_obs_over_expected": (obs_rate / expected_rate) if obs_rate else None,
        "note": "vehicle_id spaces are NOT comparable across vintages; this is a test_id-space compatibility probe. Ratio ~1.0 => same test_id space => 875.4M new-items claim upgraded to identity-verified basis.",
    })
    print(f"[identity] tgh2<=2023 rows={idc[0]:,} matched={idc[1]:,} ratio={obs_rate/expected_rate:.3f}")

    print("ANALYZE_PANEL COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
