"""evaluate.py — the runtime DEV-GRADE scorer. PROTECTED referee file.

Trains base vs base+candidate on the v57.1 frames via the shared `score_core` path,
scores per-slice via `arm0_harness`, runs a leakage ablation, and applies the
`decision.py` promotion rule. The F3 fix lives in decision.py: seed direction is a
4-way classification (not `all(d>0) or all(d<0)`), promotion requires `stable_positive`
AND within-segment wins (not a pooled-only gain). Keep/revert is the runner's job;
this prints + returns the verdict.

Grade: DEV — within-run base-vs-base+feature comparator (NOT the RCA-settled baseline),
the (reservoir,42)-seeded dev sample, and the `mot_tests=[]` candidate shim. Promotion-
grade (faithful injection, fixed test_id cohort, >=5 seeds, durable manifest, control
battery) is the separate validate_promotion_grade path.

Usage: evaluate.py --candidate vehicle_age_years [--sample N] [--seeds 0,1]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import config            # noqa: E402
import referee_config    # noqa: E402
import arm0_harness      # noqa: E402
import candidate_feature as cf  # noqa: E402
import score_core        # noqa: E402
import decision          # noqa: E402
import manifest          # noqa: E402

# contract loader, per r4_bench.py:63-64 (model_bundle lives off-repo)
for _p in config.BUNDLE_IMPORT_PATHS:
    sys.path.insert(0, str(_p))

RUN_DIR = Path("/tmp/experiment_loop_runs")   # outputs OFF the iCloud tree


def load_frames(sample):
    import duckdb
    con = duckdb.connect()
    con.execute("SET memory_limit='3GB'")
    con.execute("SET enable_progress_bar=false")
    # Seeded reservoir => deterministic dev cohort within a fixed snapshot. (F1: a bare
    # 'USING SAMPLE N ROWS' is nondeterministic and silently flipped keep/dead verdicts.)
    samp = f"USING SAMPLE {int(sample)} ROWS (reservoir, 42)" if sample else ""
    dev = con.execute(f"SELECT * FROM read_parquet('{referee_config.DEV_FRAME}') {samp}").df()
    oot = con.execute(f"SELECT * FROM read_parquet('{referee_config.OOT_FRAME}') {samp}").df()
    con.close()
    return dev, oot


def base_feature_cols():
    from model_bundle import load_contract
    return list(load_contract(str(config.CONTRACT)).feature_names)


def inject_candidate(dev, oot):
    """DEV-GRADE shim: exercise candidate_feature.compute_candidate_features over a
    minimal VehicleHistory (mot_tests=[]) built from v57_packets (registration_date :=
    first-use date). Promotion-grade uses faithful_inject instead. Returns the frames
    with new columns + the list of candidate column names."""
    import duckdb
    ids = pd.concat([dev["test_id"], oot["test_id"]]).unique().tolist()
    con = duckdb.connect()
    con.execute("CREATE TEMP TABLE want(id BIGINT)")
    con.executemany("INSERT INTO want VALUES (?)", [(int(i),) for i in ids])
    tgt = con.execute(f"""
        SELECT DISTINCT p.tgt_id AS test_id, CAST(p.tgt_date AS DATE) AS d,
               CAST(p.tgt_fud AS DATE) AS f
        FROM read_parquet('{config.PACKETS}') p JOIN want w ON p.tgt_id = w.id
    """).df()
    con.close()

    recs = {}
    cand_cols: set[str] = set()
    for r in tgt.itertuples(index=False):
        fud = None if pd.isna(r.f) else pd.Timestamp(r.f).to_pydatetime()
        tdate = None if pd.isna(r.d) else pd.Timestamp(r.d).to_pydatetime()
        hist = SimpleNamespace(registration_date=fud, manufacture_date=fud, mot_tests=[])
        feats = cf.compute_candidate_features(hist, "", tdate)
        recs[int(r.test_id)] = feats
        cand_cols.update(feats)
    cand_cols = sorted(cand_cols)

    cand_df = pd.DataFrame.from_dict(recs, orient="index").reset_index(names="test_id")
    out = []
    for df in (dev, oot):
        merged = df.merge(cand_df, on="test_id", how="left")
        for col in cand_cols:
            fill = cf.MISSING if "missing" not in col else 1
            merged[col] = merged[col].fillna(fill)
        out.append(merged)
    return out[0], out[1], cand_cols


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", default="vehicle_age_years")
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--seeds", default="0,1")
    a = ap.parse_args()
    seeds = [int(s) for s in a.seeds.split(",")]
    RUN_DIR.mkdir(exist_ok=True)
    t0 = time.time()
    P = referee_config.PROMOTION
    LBL = referee_config.LABEL_COL

    dev, oot = load_frames(a.sample)
    base_cols = base_feature_cols()
    dev, oot, cand_cols = inject_candidate(dev, oot)
    print(f"loaded dev={len(dev):,} oot={len(oot):,}; candidate cols={cand_cols}", flush=True)

    sc = score_core.score_candidate(
        dev, oot, base_cols, cand_cols, seeds=seeds, params=config.PARAMS,
        cat_features=config.CAT_FEATURES, split=config.SPLIT, label_col=LBL)
    for s, d in zip(seeds, sc.deltas_pp):
        print(f"  seed {s}: d_oot_auc={d:+.3f}pp", flush=True)

    report = arm0_harness.segmented_report(oot, oot[LBL].values, sc.cand_proba, sc.base_proba)
    summ = decision.summarize_report(report)        # NaN/missing/flat-safe reduction
    within_wins = summ["within_wins"]
    worst_ece = summ["worst_d_ece"]
    pooled_d_auc_pp = summ["pooled_d_auc_pp"]
    median_seed = float(np.median(sc.deltas_pp))
    drop = score_core.leakage_drop_pp(sc.last_model, oot, sc.full_cols, cand_cols,
                                      seeds[0], label_col=LBL)

    seed_direction = decision.classify_seed_direction(sc.deltas_pp, P["seed_dead_zone_pp"])
    dec = decision.decide(
        seed_direction=seed_direction, pooled_d_auc_pp=pooled_d_auc_pp,
        median_seed_d_auc_pp=median_seed, within_segment_wins=len(within_wins),
        ece_breach=worst_ece > P["ece_worsen_max_abs"], leakage_drop_pp=drop, thresholds=P)
    final = dec.verdict

    led_path = HERE / "ledger.tsv"   # gitignored dev ledger (diagnostic-only, schema 1)
    manifest.append_ledger_row(led_path, {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "grade": "dev", "candidate_id": a.candidate, "feature_names": ",".join(cand_cols),
        "seed_direction": seed_direction, "pooled_d_auc_pp": round(pooled_d_auc_pp, 4),
        "median_seed_d_auc_pp": round(median_seed, 4),
        "within_segment_wins": len(within_wins), "worst_d_ece": round(worst_ece, 4),
        "leakage_drop_pp": round(drop, 3), "n_seeds": len(seeds), "verdict": final,
        "promotable": dec.promotable, "diagnostic_only": "true", "worktree_clean": "",
        "ledger_schema_version": manifest.LEDGER_SCHEMA_VERSION,
        "evaluator_version": manifest.EVALUATOR_VERSION,
        "control_battery_version": manifest.CONTROL_BATTERY_VERSION,
        "sample_fingerprint": "", "data_snapshot_id": "", "manifest_id": "",
        "notes": "dev-grade shim; within-run baseline"})
    n = len(manifest.read_ledger(led_path, include_diagnostic=True))

    out = {"verdict": final, "promotable": dec.promotable, "seed_direction": seed_direction,
           "seed_deltas_pp": [round(d, 4) for d in sc.deltas_pp],
           "pooled_d_auc_pp": round(pooled_d_auc_pp, 4),
           "median_seed_d_auc_pp": round(median_seed, 4),
           "within_segment_wins": within_wins, "worst_d_ece": round(worst_ece, 4),
           "leakage_drop_pp": round(drop, 3), "reasons": dec.reasons, "report": report,
           "minutes": round((time.time() - t0) / 60, 1), "comparison_count": n,
           "DEV_GRADE": True,
           "baseline_note": "within-run base-vs-base+feature; NOT the RCA-settled baseline"}
    (RUN_DIR / f"{a.candidate}.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str))
    return out


if __name__ == "__main__":
    main()
