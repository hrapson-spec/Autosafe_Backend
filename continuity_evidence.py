#!/usr/bin/env python3
"""Non-gating continuity evidence: per-first-year cohort span table (F3 sentinel).

A per-file vehicle_id remap drives EVERY cohort's multi-year share toward
zero; a PARTIAL remap (the spurious-pass nightmare) shows as a step change
between adjacent cohorts. Pure evidence for the remote session — this does
not replace or alter check_vehicle_continuity.
"""
import sys
from pathlib import Path

import duckdb

lake = Path(sys.argv[1])
con = duckdb.connect()
con.execute("SET memory_limit='2GB'")
con.execute("SET threads=2")
# preserve_insertion_order NOT set: =false proven to corrupt window pipelines (08-12)
con.execute("SET temp_directory='/Users/henrirapson/autosafe-v58/.tmp'")
rel = f"read_parquet('{(lake / 'results' / '**' / '*.parquet').as_posix()}', hive_partitioning=true)"
rows = con.execute(f"""
    WITH per_vehicle AS (
        SELECT vehicle_id,
               min(test_year)                AS first_year,
               count(DISTINCT test_year)     AS n_years,
               count(*)                      AS n_tests
        FROM {rel}
        GROUP BY vehicle_id
        HAVING count(*) >= 2
    )
    SELECT first_year,
           count(*)                                         AS vehicles,
           round(avg(CASE WHEN n_years >= 2 THEN 1.0 ELSE 0 END), 4) AS multiyear_share,
           round(avg(n_tests), 2)                           AS avg_tests
    FROM per_vehicle GROUP BY 1 ORDER BY 1
""").fetchall()
print("CONTINUITY-EVIDENCE cohort table (first_year, vehicles>=2tests, multiyear_share, avg_tests):")
for r in rows:
    print(f"  {r[0]}  {r[1]:>12,}  {r[2]:.4f}  {r[3]}")
shares = [r[2] for r in rows[:-1]]  # last cohort structurally can't span
if shares and (max(shares) - min(shares)) > 0.35:
    print(f"CONTINUITY-EVIDENCE WARNING: cohort multiyear_share step of "
          f"{max(shares)-min(shares):.2f} — possible partial ID remap; inspect before trusting gates")
else:
    print("CONTINUITY-EVIDENCE: cohort shares consistent (no partial-remap signature)")
