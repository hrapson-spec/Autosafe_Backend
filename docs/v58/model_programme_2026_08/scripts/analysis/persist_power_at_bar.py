#!/usr/bin/env python3
"""PERSIST power-at-bar table — the D5 freezing precondition.

Inputs are ONLY (a) prior-side clean-room cell counts, which carry no outcome
information, and (b) same-system major/dangerous base rates already published in
out/SEVERITY_RESULT.json. NOTHING here is conditional on persistence state, so
computing it before the freeze contaminates nothing.

Because the clean-room base rate p0 is not knowable without an outcome read, MDE is
reported as a FUNCTION of p0 over a grid. The prereg freezes the bar against the grid,
not against a guessed point value.

Emits out/PERSIST_POWER_AT_BAR.json.
"""
import json
from math import erf, sqrt
from pathlib import Path

import duckdb

PROG = Path("/Users/henrirapson/autosafe-v58/docs/v58/model_programme_2026_08")
TMP = Path(__file__).resolve().parent / "duck_tmp_pow"
TMP.mkdir(exist_ok=True)
Z, ZP = 1.959963985, 0.841621234
Phi = lambda x: 0.5 * (1 + erf(x / sqrt(2)))

# ---------------------------------------------------------------- cell counts
tax = json.loads((PROG / "out/ADVSTRUCT_TAXONOMY.json").read_text())
norm = lambda s: "".join(c.lower() for c in s if c.isalnum())
cwn = {norm(k): v for k, v in tax["crosswalk_normalised"].items()}

con = duckdb.connect()
con.execute("SET memory_limit='3GB'"); con.execute("SET threads=4")
con.execute(f"SET temp_directory='{TMP}'")
con.execute("CREATE TABLE xw(sect_norm VARCHAR, sys VARCHAR)")
con.executemany("INSERT INTO xw VALUES (?,?)", list(tax["crosswalk_normalised"].items()))

P = str(PROG / "out/frames_eval_v2/recipe=eval2024/rung=all/packets/*.parquet")
con.execute(f"""CREATE TABLE ds AS
WITH ex AS (SELECT tgt_id, p_date, j.disp disp,
   lower(regexp_replace(trim(j.sect),'[^a-zA-Z0-9]+',' ','g')) sn
  FROM read_parquet('{P}') t, LATERAL (SELECT unnest(from_json(t.defects_json,
    '[{{"disp":"VARCHAR","sect":"VARCHAR"}}]')) j) WHERE t.defects_json IS NOT NULL)
SELECT ex.tgt_id, ex.p_date, ex.disp, xw.sys FROM ex
LEFT JOIN xw ON lower(regexp_replace(trim(xw.sect_norm),'[^a-zA-Z0-9]+',' ','g'))=ex.sn""")
con.execute("""CREATE TABLE day AS SELECT tgt_id, p_date,
  SUM(CASE WHEN disp='A' THEN 1 ELSE 0 END) n_adv,
  SUM(CASE WHEN disp='M' THEN 1 ELSE 0 END) n_min,
  SUM(CASE WHEN disp='F' THEN 1 ELSE 0 END) n_fail,
  MAX(CASE WHEN disp='A' AND sys IS NOT NULL THEN sys END) adv_sys,
  COUNT(DISTINCT CASE WHEN disp='A' AND sys IS NOT NULL THEN sys END) breadth,
  list(DISTINCT CASE WHEN disp='A' AND sys IS NOT NULL THEN sys END) syslist
  FROM ds GROUP BY 1,2""")
con.execute("CREATE TABLE rk AS SELECT *, row_number() OVER "
            "(PARTITION BY tgt_id ORDER BY p_date DESC) r FROM day")
cells = {r[0]: (r[1], r[2]) for r in con.execute("""
  SELECT a.adv_sys,
    SUM(CASE WHEN list_contains(b.syslist,a.adv_sys) THEN 1 ELSE 0 END) aa,
    SUM(CASE WHEN list_contains(b.syslist,a.adv_sys) THEN 0 ELSE 1 END) ca
  FROM rk a JOIN rk b USING(tgt_id)
  WHERE a.r=1 AND b.r=2 AND a.n_adv=1 AND a.breadth=1 AND a.n_min=0 AND a.n_fail=0
  GROUP BY 1""").fetchall()}

# ------------------------------------------------------- published base rates
idx = json.loads((PROG / "out/PERSIST_SECT_INDEX.json").read_text())
N_EVAL = 330665
base_pop = {}
for s, cols in idx["ontology_bridge"]["systems"].items():
    base_pop[s] = sum(c["positive_n_eval2024"] for c in cols) / N_EVAL

SYS = tax["systems"]
GRID = [0.02, 0.05, 0.10, 0.15]          # candidate clean-room p0 values
RR_BAR = 1.20                             # pre-declared minimum interesting relative risk


def mde_rr(n1, n0, p0):
    """Smallest relative risk detectable at 80% power, two-sided alpha .05."""
    if n1 < 2 or n0 < 2:
        return None
    lo, hi = 1.0, 6.0
    for _ in range(200):
        rr = (lo + hi) / 2
        p1 = min(p0 * rr, 0.999)
        se = sqrt(p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
        if (p1 - p0) / se >= Z + ZP:
            hi = rr
        else:
            lo = rr
    return round((lo + hi) / 2, 4)


def power_at(n1, n0, p0, rr):
    if n1 < 2 or n0 < 2:
        return 0.0
    p1 = min(p0 * rr, 0.999)
    se = sqrt(p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
    return Phi((p1 - p0) / se - Z)


rows = []
for s in SYS:
    aa, ca = cells.get(s, (0, 0))
    rows.append({
        "system": s, "n_AA": aa, "n_CA": ca,
        "population_same_system_md_rate_eval2024": round(base_pop[s], 5),
        "mde_rr_by_p0": {str(p): mde_rr(aa, ca, p) for p in GRID},
        "power_at_RR_bar_by_p0": {str(p): round(power_at(aa, ca, p, RR_BAR), 4) for p in GRID},
    })

# Standardised average across systems, population-weighted. tau=0 (homogeneity) is
# the OPTIMISTIC bound; a no-pooling weighted average is the conservative bound.
tot = sum(r["n_AA"] + r["n_CA"] for r in rows)
pooled = {}
for p0 in GRID:
    var_inv = 0.0
    for r in rows:
        n1, n0 = r["n_AA"], r["n_CA"]
        if n1 < 2 or n0 < 2:
            continue
        w = (n1 + n0) / tot
        p1 = p0 * RR_BAR
        se_s = sqrt(p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
        var_inv += (w ** 2) * (se_s ** 2)
    se_std = sqrt(var_inv)
    delta = p0 * (RR_BAR - 1)
    pooled[str(p0)] = {
        "se_standardised_tau0": round(se_std, 6),
        "mde80_abs_pp_tau0": round((Z + ZP) * se_std * 100, 4),
        "power_at_RR_bar_tau0": round(Phi(delta / se_std - Z), 4),
    }

out = {
    "artifact": "PERSIST power-at-bar — D5 freezing precondition",
    "surface": "eval2024 clean-room cohort A (one advisory, one system, no minors, no fail at t, t-1 observable)",
    "n_cleanroom_total": sum(r["n_AA"] + r["n_CA"] for r in rows),
    "inputs_are_contamination_free": (
        "Cell counts are prior-side only. Base rates are population-wide same-system M/D "
        "prevalences already published in SEVERITY_RESULT.json. Nothing is conditional on "
        "persistence state."),
    "pre_declared_minimum_interesting_effect": {"relative_risk": RR_BAR},
    "p0_grid": GRID,
    "per_system": rows,
    "standardised_average": {
        "note": ("tau=0 assumes homogeneous system effects and is the OPTIMISTIC bound. "
                 "Partial pooling lands between this and the no-pooling case; a heterogeneous "
                 "truth inflates SE by sqrt(1 + tau^2/se_s^2)."),
        "by_p0": pooled,
    },
}
(PROG / "out/PERSIST_POWER_AT_BAR.json").write_text(json.dumps(out, indent=1))

print(f"PERSIST clean-room cohort A, eval2024 — n = {out['n_cleanroom_total']:,}")
print(f"Pre-declared minimum interesting effect: RR = {RR_BAR}\n")
print(f"{'system':<17}{'n_AA':>7}{'n_CA':>7}   " + "".join(f"MDE_RR@p0={p:<6}" for p in GRID))
for r in rows:
    m = r["mde_rr_by_p0"]
    cellstr = "".join(f"{(str(m[str(p)]) if m[str(p)] else 'n/a'):<14}" for p in GRID)
    print(f"{r['system']:<17}{r['n_AA']:>7,}{r['n_CA']:>7,}   {cellstr}")
print(f"\n{'':<17}{'':>14}   power to detect RR={RR_BAR}:")
for r in rows:
    pw = r["power_at_RR_bar_by_p0"]
    print(f"{r['system']:<17}{'':>14}   " + "".join(f"{pw[str(p)]:<14.0%}" for p in GRID))
print(f"\nSTANDARDISED AVERAGE (population-weighted, tau=0 optimistic bound):")
for p in GRID:
    d = pooled[str(p)]
    print(f"  p0={p:<6} MDE80 = {d['mde80_abs_pp_tau0']:.3f} pp   "
          f"power at RR={RR_BAR}: {d['power_at_RR_bar_tau0']:.1%}")
print(f"\nwrote {PROG/'out/PERSIST_POWER_AT_BAR.json'}")
