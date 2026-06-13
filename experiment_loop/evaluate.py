"""evaluate.py — the runtime scorer (dev-grade). PROTECTED referee file.

Trains base vs base+candidate on the v57.1 frames (mirrors work/bakeoff_2026/
r4_bench.py exactly for the training path), scores via arm0_harness, runs a leakage
ablation, applies referee_config.PROMOTION, writes a ledger row. Keep/revert is the
runner's job; this prints + returns the verdict.

⚠ DEV-GRADE + EXECUTION-GATED:
  * Provisional comparator = the base arm's OWN OOT AUC in this run (a within-run
    base-vs-base+feature ablation), NOT the RCA-settled absolute baseline.
  * Arm-0 slices limited to those the current v57.1 frame supports.
  * The candidate is injected by actually CALLING candidate_feature.compute_candidate_
    features per target over a minimal VehicleHistory shim built from v57_packets
    (so the agent's editable surface is genuinely exercised). The full faithful path
    re-runs the serving FE (r2b stage-3) — heavier, the integration upgrade.
  * NOT YET RUN. Authored at load~30 / 118MB free RAM; a full train would OOM.
    First execution must be a small --sample smoke once the machine has headroom.

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

# contract loader, per r4_bench.py:63-64 (model_bundle lives off-repo)
for _p in config.BUNDLE_IMPORT_PATHS:
    sys.path.insert(0, str(_p))

RUN_DIR = Path("/tmp/experiment_loop_runs")   # outputs OFF the iCloud tree


def load_frames(sample):
    import duckdb
    con = duckdb.connect()
    con.execute("SET memory_limit='3GB'")
    con.execute("SET enable_progress_bar=false")
    samp = f"USING SAMPLE {int(sample)} ROWS" if sample else ""
    dev = con.execute(f"SELECT * FROM read_parquet('{referee_config.DEV_FRAME}') {samp}").df()
    oot = con.execute(f"SELECT * FROM read_parquet('{referee_config.OOT_FRAME}') {samp}").df()
    con.close()
    return dev, oot


def base_feature_cols():
    from model_bundle import load_contract
    return list(load_contract(str(config.CONTRACT)).feature_names)


def inject_candidate(dev, oot):
    """Exercise candidate_feature.compute_candidate_features over a VehicleHistory
    shim built from v57_packets (registration_date := first-use date). Returns the
    frames with new columns + the list of candidate column names."""
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


def train(dev, cols, seed):
    from catboost import CatBoostClassifier, Pool
    from sklearn.model_selection import train_test_split
    cat_idx = [cols.index(c) for c in config.CAT_FEATURES if c in cols]
    tr, va = train_test_split(dev, test_size=config.SPLIT["test_size"],
                              random_state=config.SPLIT["random_state"], stratify=dev["y"])
    m = CatBoostClassifier(**config.PARAMS, random_seed=seed, verbose=0,
                           cat_features=cat_idx, eval_metric="AUC")
    m.fit(Pool(tr[cols], tr["y"], cat_features=cat_idx),
          eval_set=Pool(va[cols], va["y"], cat_features=cat_idx),
          early_stopping_rounds=150)
    return m


def leakage_drop_pp(model, oot, cols, cand_cols, seed):
    from sklearn.metrics import roc_auc_score
    y = oot["y"].values
    base = roc_auc_score(y, model.predict_proba(oot[cols])[:, 1])
    rng = np.random.default_rng(seed)
    shuf = oot.copy()
    for c in cand_cols:
        shuf[c] = rng.permutation(shuf[c].values)
    perm = roc_auc_score(y, model.predict_proba(shuf[cols])[:, 1])
    return (base - perm) * 100.0


def append_ledger(row: dict):
    led = HERE / "ledger.tsv"
    header = led.read_text().splitlines()[0].split("\t")
    n_prior = max(0, len(led.read_text().splitlines()) - 1)
    line = "\t".join(str(row.get(h, "")) for h in header)
    with led.open("a") as f:
        f.write(line + "\n")
    return n_prior + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", default="vehicle_age_years")
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--seeds", default="0,1")
    a = ap.parse_args()
    seeds = [int(s) for s in a.seeds.split(",")]
    RUN_DIR.mkdir(exist_ok=True)
    t0 = time.time()

    dev, oot = load_frames(a.sample)
    base_cols = base_feature_cols()
    dev, oot, cand_cols = inject_candidate(dev, oot)
    full_cols = base_cols + cand_cols
    print(f"loaded dev={len(dev):,} oot={len(oot):,}; candidate cols={cand_cols}", flush=True)

    from sklearn.metrics import roc_auc_score
    base_p, cand_p, deltas, last = [], [], [], None
    for s in seeds:
        mb = train(dev, base_cols, s)
        mc = train(dev, full_cols, s)
        pb = mb.predict_proba(oot[base_cols])[:, 1]
        pc = mc.predict_proba(oot[full_cols])[:, 1]
        base_p.append(pb); cand_p.append(pc); last = (mc, full_cols)
        deltas.append(roc_auc_score(oot["y"], pc) - roc_auc_score(oot["y"], pb))
        print(f"  seed {s}: d_oot_auc={deltas[-1]*100:+.3f}pp", flush=True)

    seed_stable = all(d > 0 for d in deltas) or all(d < 0 for d in deltas)
    report = arm0_harness.segmented_report(
        oot, oot["y"].values, np.mean(cand_p, 0), np.mean(base_p, 0))
    keep, why = arm0_harness.verdict(report, referee_config.PROMOTION)
    drop = leakage_drop_pp(last[0], oot, last[1], cand_cols, seeds[0])
    # A promoting feature (within-segment/pooled win, seed-stable, ECE ok) is KEPT
    # regardless of permutation importance: under feature correlation, permuting one
    # correlated feature understates its value (raw age vs the EB age features). The
    # 'dead' auto-revert therefore fires only when the feature ALSO fails to promote
    # (the advisory_trend case: ~0 drop AND no promotion). Ratified fix, eval_proposals.md.
    promotes = keep and seed_stable
    low_use = drop < referee_config.PROMOTION["leakage_min_auc_drop_pp"]
    final = "keep" if promotes else ("dead" if low_use else "discard")
    worst_ece = max((v.get("d_ece", 0.0) for v in report["slices"].values()
                     if isinstance(v, dict) and "d_ece" in v), default=0.0)
    n = append_ledger({
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "candidate_id": a.candidate, "arm": "1_age", "hypothesis": "raw vehicle age",
        "feature_names": ",".join(cand_cols),
        "oot_auc_mean": round(float(np.mean([roc_auc_score(oot["y"], p) for p in cand_p])), 4),
        "within_seg_slices_passed": len(why["within_segment_wins"]),
        "ece_worst_slice": round(worst_ece, 4), "leakage_drop_pp": round(drop, 3),
        "verdict": final, "comparison_count": "", "notes": why["decision_basis"]})

    out = {"verdict": final, "seed_stable": seed_stable, "leakage_drop_pp": round(drop, 3),
           "why": why, "report": report, "minutes": round((time.time() - t0) / 60, 1),
           "comparison_count": n, "DEV_GRADE": True,
           "baseline_note": "within-run base-vs-base+feature; NOT the RCA-settled baseline"}
    (RUN_DIR / f"{a.candidate}.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str))
    return out


if __name__ == "__main__":
    main()
