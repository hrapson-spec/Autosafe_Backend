#!/usr/bin/env python3
"""§4b window discovery via one-pass pre-aggregation (efficiency E4).

Two single scans build a month × basis count table; every candidate window
(36–84 months) is then evaluated in-memory against the old artifact's ground
truth. Three failure semantics are scored because whether the original
counted all tests, cycle-first rows, or cycle outcomes is itself unrecorded:
  all_tests/f_row, cycle_first/f_row (handover's probe), cycle_first/f_cycle.
Writes docs/v58/evidence/window_discovery.md and prints WINDOW-PROBE verdict.
"""
import sys
from pathlib import Path

import duckdb

LAKE = Path(sys.argv[1])
OUT = Path(sys.argv[2])
T_TRUE, F_TRUE, R_TRUE = 148_509_908, 39_969_903, 0.269139638817903
TOL_N, TOL_R = 0.02, 0.02

con = duckdb.connect()
con.execute("SET memory_limit='2GB'")
R = f"read_parquet('{(LAKE/'results'/'**'/'*.parquet').as_posix()}', hive_partitioning=true)"
C = f"read_parquet('{(LAKE/'cycles'/'**'/'*.parquet').as_posix()}', hive_partitioning=true)"

months = {}
for m, n, f in con.execute(f"""
        SELECT date_trunc('month', test_date), count(*), count(*) FILTER (WHERE outcome='FAIL')
        FROM {R} WHERE test_class_id='4' GROUP BY 1""").fetchall():
    months.setdefault(m, [0]*5)
    months[m][0], months[m][1] = n, f
for m, n, fr, fc in con.execute(f"""
        SELECT date_trunc('month', r.test_date), count(*),
               count(*) FILTER (WHERE r.outcome='FAIL'),
               count(*) FILTER (WHERE c.cycle_outcome='FAIL')
        FROM {R} r JOIN {C} c ON c.test_id = r.test_id AND c.is_cycle_first
        WHERE r.test_class_id='4' GROUP BY 1""").fetchall():
    months.setdefault(m, [0]*5)
    months[m][2], months[m][3], months[m][4] = n, fr, fc

keys = sorted(months)
BASES = {"all_tests/f_row": (0, 1), "cycle_first/f_row": (2, 3), "cycle_first/f_cycle": (2, 4)}
rows, matches = [], []
for i in range(len(keys)):
    for j in range(i + 35, min(i + 84, len(keys))):
        span = keys[i:j + 1]
        for basis, (ni, fi) in BASES.items():
            t = sum(months[k][ni] for k in span); f = sum(months[k][fi] for k in span)
            if not t: continue
            r = f / t
            en, ef, er = abs(t-T_TRUE)/T_TRUE, abs(f-F_TRUE)/F_TRUE, abs(r-R_TRUE)
            rec = (en+ef+er*10, basis, keys[i].date(), keys[j].date(), t, f, r, en, ef, er)
            rows.append(rec)
            if en <= TOL_N and ef <= TOL_N and er <= TOL_R:
                matches.append(rec)
rows.sort()

with OUT.open("w") as fh:
    fh.write("# §4b window discovery — probe table (one-pass pre-agg, 3 bases)\n\n")
    fh.write(f"Ground truth: {T_TRUE:,} tests / {F_TRUE:,} failures / rate {R_TRUE}\n")
    fh.write(f"Windows scored: {len(rows):,}; within tolerance (±2% counts, ±0.02 rate): {len(matches)}\n\n")
    fh.write("| rank | basis | start | end(mo) | tests | failures | rate | err_t | err_f | err_r |\n|--|--|--|--|--|--|--|--|--|--|\n")
    for k, rec in enumerate(rows[:10]):
        _, b, s, e, t, f, r, en, ef, er = rec
        fh.write(f"| {k+1} | {b} | {s} | {e} | {t:,} | {f:,} | {r:.6f} | {en:.3%} | {ef:.3%} | {er:.4f} |\n")
    fh.write("\n## Verdict\n")
    if matches:
        _, b, s, e, t, f, r, *_ = sorted(matches)[0]
        fh.write(f"RECONCILING CELL: basis={b} window={s}..{e} tests={t:,} failures={f:,} rate={r:.9f}\n")
    else:
        fh.write("NO CELL WITHIN TOLERANCE — escalate per handover §4b.\n")

if matches:
    _, b, s, e, *_ = sorted(matches)[0]
    print(f"WINDOW-PROBE MATCH: basis={b} start={s} end_month={e}")
    sys.exit(0)
if not rows:
    print("WINDOW-PROBE NO-MATCH: no windows scored (fewer than 36 months of data) — escalate")
    sys.exit(2)
best = rows[0]
print(f"WINDOW-PROBE NO-MATCH: best was basis={best[1]} {best[2]}..{best[3]} "
      f"err_t={best[7]:.3%} err_f={best[8]:.3%} err_r={best[9]:.4f} — escalate")
sys.exit(2)
