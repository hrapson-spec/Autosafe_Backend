#!/usr/bin/env python3
"""F7a — tie-rule materiality on the fulldepth substrate (bounded, one attempt).

Question (audit report §10): do the 104 serving-module features depend on the
UNEXAMINED same-day tie order `p_test_id DESC` in build_packets_fulldepth.py:634?

Design — SELF-DIFFERENTIAL, one module version, two tie orders:
  arm DESC = as-built   ORDER (p_date DESC, p_test_id DESC)  [defect-suspect rule]
  arm ASC  = reversed   ORDER (p_date DESC, p_test_id ASC)
Any per-target feature difference is order-dependence BY CONSTRUCTION. This
deliberately does NOT diff against the banked frame (module drift would confound).
Control group (multi-prior targets with NO within-day tie) must show zero diffs —
harness validation; a nonzero control kills the run's validity.

Faithful to the builder (read 2026-08-12, lines 560-640): same MOTTest fields,
same defects_json/API_TYPE parsing, same PATHOLOGY_BOUND=80 exclusion, same
break-at-null semantics (here: non-null priors only), same
engineer_features_with_stats(hist, pc, tgt_date) call.

F7b (F7a FOUND diffs — owner-scheduled): full 1.05M-target re-stream under the
decided tie rule + rescore both frames with fulldepth_seed0/seed1 models →
ΔAUC vs the 0.002052 materiality floor. F7a alone cannot settle ΔAUC (R67
lesson: a ΔAUC can be a harness artifact — F7b must diff scores per-target and
canonicalise before aggregating). ALSO capture CALIBRATION deltas, not just
AUC: days_late meanΔ 55.7d on flips can straddle band boundaries used by
calibration cohorts (peer caution, 2026-08-12), and miscalibration is where
per-cohort harm hides even when pooled AUC is stable.

Safety: self-gating preflight (refuses if any pipeline.run_lake process is alive
or free disk < 10 GiB); duckdb memory_limit 1.5GB, threads 2, per-PID temp dir
with 4GiB cap; reads only; writes one JSON result next to this script.
"""
import json
import math
import os
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

# ---------------------------------------------------------------- preflight gate
def preflight():
    r = subprocess.run(["pgrep", "-f", "pipeline.run_lake|sharded_cycles|stream_cycles|run_aggregates"],
                       capture_output=True, text=True)
    if r.stdout.strip():
        sys.exit(f"REFUSE: pipeline job alive (pids {r.stdout.split()}) — one compute job at a time")
    free_gib = shutil.disk_usage("/System/Volumes/Data").free / 2**30
    if free_gib < 10:
        sys.exit(f"REFUSE: free disk {free_gib:.1f} GiB < 10 GiB floor")
    print(f"preflight OK: no run_lake, free={free_gib:.1f} GiB")

preflight()

import duckdb          # noqa: E402
import pandas as pd    # noqa: E402

REPO = Path.home() / "autosafe"                      # serving module (same for BOTH arms)
sys.path.insert(0, str(REPO))
import model_v55                                      # noqa: E402
from dvsa_client import MOTTest, VehicleHistory       # noqa: E402

ART = Path.home() / "autosafe/work/goal_0750/feature_repr_review_v1/artifacts"
PACKETS = ART / "fulldepth_packets.parquet"           # h1 frame packets, 159MB
OUT = Path(__file__).with_name("F7A_RESULT.json")

API_TYPE = {"A": "ADVISORY", "F": "MAJOR", "M": "MINOR", "P": "PRS"}  # builder:60
PATHOLOGY_BOUND = 80                                                   # builder:61
N_EXPOSED, N_CONTROL, SEED = 4000, 1000, 42

TMP = Path(__file__).parent / f".duckdb_tmp_f7_{os.getpid()}"
TMP.mkdir(exist_ok=True)
con = duckdb.connect()
con.execute("SET memory_limit='1500MB'")
con.execute("SET threads=2")
con.execute(f"SET temp_directory='{TMP}'")
con.execute("PRAGMA max_temp_directory_size='4GiB'")

# ------------------------------------------------- population + sample selection
# Exposed: >=2 non-null priors sharing one p_date. Control: >=2 priors, no tie.
pop = con.execute(f"""
WITH pri AS (
    SELECT tgt_id, p_date, count(*) AS n
    FROM read_parquet('{PACKETS}')
    WHERE p_test_id IS NOT NULL
    GROUP BY tgt_id, p_date
), per_tgt AS (
    SELECT tgt_id, max(n) AS max_day, sum(n) AS n_priors
    FROM pri GROUP BY tgt_id
)
SELECT
  (SELECT count(*) FROM per_tgt)                              AS n_targets_with_priors,
  (SELECT count(*) FROM per_tgt WHERE max_day >= 2)           AS n_exposed,
  (SELECT count(*) FROM per_tgt WHERE max_day = 1 AND n_priors >= 2) AS n_control_pool
""").fetchone()
n_with_priors, n_exposed_pop, n_control_pool = pop
print(f"population: {n_with_priors:,} targets with priors; "
      f"exposed (within-day tie) {n_exposed_pop:,} = {n_exposed_pop/max(n_with_priors,1):.2%}")

ids = con.execute(f"""
WITH pri AS (
    SELECT tgt_id, p_date, count(*) AS n
    FROM read_parquet('{PACKETS}') WHERE p_test_id IS NOT NULL
    GROUP BY tgt_id, p_date
), per_tgt AS (
    SELECT tgt_id, max(n) AS max_day, sum(n) AS n_priors FROM pri GROUP BY tgt_id
), exposed AS (
    SELECT tgt_id, 'exposed' AS grp FROM per_tgt WHERE max_day >= 2
    ORDER BY hash(tgt_id * 2654435761 + {SEED}) LIMIT {N_EXPOSED}
), control AS (
    SELECT tgt_id, 'control' AS grp FROM per_tgt WHERE max_day = 1 AND n_priors >= 2
    ORDER BY hash(tgt_id * 2654435761 + {SEED}) LIMIT {N_CONTROL}
)
SELECT * FROM exposed UNION ALL SELECT * FROM control
""").fetch_df()
grp_of = dict(zip(ids.tgt_id, ids.grp))
con.execute("CREATE TEMP TABLE sample_ids AS SELECT tgt_id FROM ids")

rows = con.execute(f"""
SELECT p.* FROM read_parquet('{PACKETS}') p
JOIN sample_ids s ON p.tgt_id = s.tgt_id
ORDER BY p.tgt_id
""").fetch_df()
print(f"sampled {ids.shape[0]:,} targets, {rows.shape[0]:,} packet rows")

# ------------------------------------------------- per-target feature build/diff
def build_feats(sub: pd.DataFrame, tie_asc: bool):
    """Mirror builder:564-605 exactly, with the tie order as the only variable."""
    pri = sub[sub.p_test_id.notna()].copy()
    if len(pri) > PATHOLOGY_BOUND:
        return None  # excluded_pathology — builder skips mots entirely
    pri = pri.sort_values(["p_date", "p_test_id"],
                          ascending=[False, tie_asc], kind="mergesort")
    mots = []
    for r in pri.itertuples():
        defects = []
        dj = r.defects_json
        if isinstance(dj, str) and dj.strip():
            for d in json.loads(dj):
                t = API_TYPE.get(d.get("t"))
                if t is None:
                    continue
                defects.append({"type": t, "text": d.get("x") or "", "dangerous": False})
        tdt = pd.Timestamp(r.p_date).to_pydatetime()
        odo = r.p_miles
        mots.append(MOTTest(
            test_date=tdt, test_result=r.p_result,
            expiry_date=(tdt + timedelta(days=365) if r.p_result == "PASSED" else None),
            odometer_value=None if odo is None or pd.isna(odo) else int(odo),
            odometer_unit="mi", test_number=str(r.p_test_id), defects=defects))
    r0 = sub.iloc[0]
    fud = r0.tgt_fud
    fud_dt = None if fud is None or pd.isna(fud) else pd.Timestamp(fud).to_pydatetime()
    cc = r0.tgt_cc
    hist = VehicleHistory(
        registration="FULLDEPTH", make=str(r0.tgt_make or "UNKNOWN"),
        model=str(r0.tgt_model or "UNKNOWN"), fuel_type=str(r0.tgt_fuel or "PE"),
        colour="UNKNOWN", registration_date=fud_dt, manufacture_date=fud_dt,
        engine_size=None if cc is None or pd.isna(cc) else int(cc), mot_tests=mots)
    return model_v55.engineer_features_with_stats(
        hist, str(r0.tgt_pc or ""), pd.Timestamp(r0.tgt_date).to_pydatetime())

def neq(a, b):
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return False
        return abs(a - b) > 1e-9
    return a != b

stats = {"exposed": {"n": 0, "any_diff": 0}, "control": {"n": 0, "any_diff": 0}}
feat_diffs, num_deltas, examples = {}, {}, []
for tgt_id, sub in rows.groupby("tgt_id", sort=False):
    grp = grp_of[tgt_id]
    fd = build_feats(sub, tie_asc=False)
    fa = build_feats(sub, tie_asc=True)
    if fd is None or fa is None:
        continue
    stats[grp]["n"] += 1
    diff_keys = [k for k in fd if neq(fd[k], fa[k])]
    if diff_keys:
        stats[grp]["any_diff"] += 1
        for k in diff_keys:
            feat_diffs[k] = feat_diffs.get(k, 0) + 1
            if isinstance(fd[k], (int, float)) and isinstance(fa[k], (int, float)) \
               and not (isinstance(fd[k], bool) or isinstance(fa[k], bool)):
                try:
                    if not (math.isnan(float(fd[k])) or math.isnan(float(fa[k]))):
                        num_deltas.setdefault(k, []).append(abs(float(fd[k]) - float(fa[k])))
                except (TypeError, ValueError):
                    pass
        if len(examples) < 12:
            examples.append({"tgt_id": int(tgt_id), "grp": grp,
                             "diffs": {k: [fd[k], fa[k]] for k in diff_keys[:8]}})

result = {
    "date": "2026-08-12", "design": "F7a self-differential DESC-vs-ASC tie order",
    "module_repo_head": subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                                       capture_output=True, text=True).stdout.strip(),
    "population": {"targets_with_priors": int(n_with_priors),
                   "exposed_within_day_tie": int(n_exposed_pop),
                   "exposed_share": round(n_exposed_pop / max(n_with_priors, 1), 5)},
    "sample": stats,
    "exposed_any_diff_rate": round(stats["exposed"]["any_diff"] / max(stats["exposed"]["n"], 1), 5),
    "control_any_diff_rate": round(stats["control"]["any_diff"] / max(stats["control"]["n"], 1), 5),
    "harness_valid": stats["control"]["any_diff"] == 0,
    "per_feature_diff_counts": dict(sorted(feat_diffs.items(), key=lambda kv: -kv[1])),
    "numeric_mean_abs_delta": {k: round(sum(v) / len(v), 6) for k, v in num_deltas.items()},
    "examples": examples,
    "next": "if exposed_any_diff_rate > 0: F7b full re-stream + seed0/seed1 rescore vs 0.002052",
}
OUT.write_text(json.dumps(result, indent=1, default=str))
print(json.dumps({k: result[k] for k in
                  ("population", "sample", "exposed_any_diff_rate",
                   "control_any_diff_rate", "harness_valid")}, indent=1))
print(f"full result -> {OUT}")
shutil.rmtree(TMP, ignore_errors=True)
