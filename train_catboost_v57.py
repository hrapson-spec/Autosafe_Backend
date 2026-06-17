"""v57 trainer scaffold — realises decision #4 ("one model via coverage features").

This is the SCAFFOLD half of the v57 build. The correctness-critical pieces are
real and shared with serving (the feature transform, the OOT-safe early-stopping
split, the contract emission); the data-loading + the v55 feature pipeline are
reused from ``train_catboost_production_v55`` and marked as the integration
points to fill against the off-repo data.

v57 deltas vs the v55 trainer (all encoded here or in v57_features):
  1. veterans_only = FALSE — first-test / no-prior vehicles are kept in BOTH
     training and OOT evaluation (v55 excluded them from both, yet served them
     anyway). The coverage features added below tell the model which rows are
     genuinely first-test vs window-truncated.
  2. The v57 matrix = v55 features  →  drop RC-3, rename ``*_observed``, add the
     5 coverage features, via the SAME ``v57_features.add_v57_columns`` the
     serving adapter (``model_v57``) uses — so train/serve cannot drift.
  3. Early stopping uses a time-based fold carved from DEV
     (``training_utils.time_based_eval_split``); OOT is used only for the final
     metric (the audit-#1 leakage fix).
  4. The bundle is EMITTED from the run (contract + manifest + metrics) so the
     107-vs-104 hand-maintained-list drift class cannot recur.

Heavy deps (numpy/pandas/catboost) and the data live in the training env; this
module imports them lazily so it can be inspected/contract-checked anywhere.

Run (in the training env, after mapping the spine date columns):
    python train_catboost_v57.py --out models/v57
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from model_bundle import emit_contract
from training_utils import time_based_eval_split
from v57_features import (
    V57_CATEGORICAL,
    V57_FEATURE_NAMES,
    add_v57_columns,
    v57_feature_rows,
)

# CatBoost params: inherit the v55 production settings unless a v57 sweep changes
# them. Kept literal here so the scaffold has no import-time dependency on the
# v55 trainer module body.
PARAMS = dict(
    iterations=2000,
    learning_rate=0.03,
    depth=6,
    l2_leaf_reg=3.0,
    border_count=254,
    random_strength=1.0,
    bagging_temperature=1.0,
    eval_metric="AUC",
)
N_SEEDS = 10
EARLY_STOPPING_ROUNDS = 150
VAL_FRACTION = 0.1

# Date columns the coverage features need. Map these to the real spine/lake
# columns when wiring the data step (see build_v55_frames).
TARGET_DATE_COL = "test_date"
FIRST_USE_DATE_COL = "first_use_date"
FIRST_OBSERVED_TEST_DATE_COL = "first_observed_test_date"


# ---------------------------------------------------------------------------
# Step 1 — load + v55-feature-engineer DEV/OOT  (INTEGRATION POINT)
# ---------------------------------------------------------------------------
def build_v55_frames() -> Tuple[Any, Any]:
    """Return (dev_df, oot_df) carrying the v55 FEATURE_COLS + the date columns.

    Reuses the v55 trainer's query builders and feature pipeline, with the one
    behavioural change that makes v57 a single model: ``veterans_only=False``.

    This is the one part that depends on the off-repo data and the long v55
    add_* feature sequence; keep it in lockstep with the v55 trainer. The
    queries must additionally select the coverage date columns
    (``first_use_date``, ``first_observed_test_date``) from the spine/results
    lake — v55's queries do not, because v55 has no coverage features.
    """
    raise NotImplementedError(
        "Wire to train_catboost_production_v55's data load + feature pipeline "
        "with veterans_only=False, selecting first_use_date and "
        "first_observed_test_date. Then return (dev_df, oot_df)."
    )


# ---------------------------------------------------------------------------
# Step 2 — build the v57 matrix (REAL; shared with serving)
# ---------------------------------------------------------------------------
def build_v57_matrix(dev_df, oot_df):
    """Apply the shared v57 transform and return ((X_dev, y_dev, dev_dates),
    (X_oot, y_oot)) with columns == V57_FEATURE_NAMES in order."""
    dev_v57 = add_v57_columns(
        dev_df,
        target_date_col=TARGET_DATE_COL,
        first_use_date_col=FIRST_USE_DATE_COL,
        first_observed_test_date_col=FIRST_OBSERVED_TEST_DATE_COL,
    )
    oot_v57 = add_v57_columns(
        oot_df,
        target_date_col=TARGET_DATE_COL,
        first_use_date_col=FIRST_USE_DATE_COL,
        first_observed_test_date_col=FIRST_OBSERVED_TEST_DATE_COL,
    )
    missing_dev = [c for c in V57_FEATURE_NAMES if c not in dev_v57.columns]
    missing_oot = [c for c in V57_FEATURE_NAMES if c not in oot_v57.columns]
    if missing_dev or missing_oot:
        raise ValueError(f"v57 matrix missing columns: dev={missing_dev} oot={missing_oot}")

    X_dev = dev_v57[V57_FEATURE_NAMES]
    X_oot = oot_v57[V57_FEATURE_NAMES]
    return (
        (X_dev, dev_v57["target"], dev_v57[TARGET_DATE_COL]),
        (X_oot, oot_v57["target"]),
    )


# ---------------------------------------------------------------------------
# Step 3 — train the ensemble (REAL; inherits the #1 OOT-safe split)
# ---------------------------------------------------------------------------
def train_v57(X_dev, y_dev, dev_dates, *, n_seeds: int = N_SEEDS):
    """Train a CatBoost seed-ensemble with time-based early stopping from DEV."""
    import numpy as np
    from catboost import CatBoostClassifier, Pool

    cat_idx = [V57_FEATURE_NAMES.index(c) for c in V57_CATEGORICAL]
    tr_idx, val_idx = time_based_eval_split(
        np.asarray(dev_dates), val_fraction=VAL_FRACTION
    )
    train_pool = Pool(X_dev.iloc[tr_idx], y_dev.iloc[tr_idx], cat_features=cat_idx)
    val_pool = Pool(X_dev.iloc[val_idx], y_dev.iloc[val_idx], cat_features=cat_idx)
    print(f"  early-stopping split: {len(tr_idx):,} fit / {len(val_idx):,} val "
          f"(OOT untouched)")

    models = []
    for seed in range(n_seeds):
        m = CatBoostClassifier(random_seed=seed, verbose=0, cat_features=cat_idx, **PARAMS)
        m.fit(train_pool, eval_set=val_pool, early_stopping_rounds=EARLY_STOPPING_ROUNDS)
        models.append(m)
        print(f"  seed {seed}: best_iter={m.get_best_iteration()}")
    return models


def evaluate_oot(models, X_oot, y_oot) -> Dict[str, float]:
    """Final OOT metric — the single, untouched evaluation."""
    import numpy as np
    from sklearn.metrics import roc_auc_score

    preds = np.mean([m.predict_proba(X_oot)[:, 1] for m in models], axis=0)
    return {"oot_auc_ensemble": float(roc_auc_score(y_oot, preds)),
            "oot_rows": int(len(y_oot)), "oot_fail_rate": float(np.mean(y_oot))}


# ---------------------------------------------------------------------------
# Step 4 — emit the bundle (REAL; closes the hand-maintained-list drift class)
# ---------------------------------------------------------------------------
def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()
    except Exception:
        return "unknown"


def emit_bundle(models, metrics: Dict[str, Any], out_dir: Path,
                *, inputs: Optional[List[Dict[str, Any]]] = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    # Contract emitted from the shared feature definition (single source).
    emit_contract(
        version="v57.0",
        features=v57_feature_rows(),
        artifact_dependencies=[
            "cohort_stats.pkl", "model_hierarchical_features.pkl",
            "segment_hierarchical_features.pkl", "model_age_hierarchical.pkl",
            "calibrator.json",
        ],
        out_path=out_dir / "feature_contract.json",
    )
    for i, m in enumerate(models):
        m.save_model(str(out_dir / f"model_seed_{i}.cbm"))
    # Representative single binary for the bundle layout; serving averages the
    # seed probabilities (matching v55's ensemble contract).
    models[0].save_model(str(out_dir / "model.cbm"))

    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    (out_dir / "training_manifest.json").write_text(json.dumps({
        "version": "v57.0",
        "git_sha": _git_sha(),
        "created": datetime.utcnow().isoformat() + "Z",
        "n_seeds": len(models),
        "params": PARAMS,
        "val_fraction": VAL_FRACTION,
        "feature_count": len(V57_FEATURE_NAMES),
        "veterans_only": False,
        "inputs": inputs or [],
        "note": "first-test rows included in train+eval; coverage features active",
    }, indent=2))
    print(f"  bundle written to {out_dir}")


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="v57 trainer (scaffold).")
    p.add_argument("--out", type=Path, default=Path("models/v57"))
    p.add_argument("--seeds", type=int, default=N_SEEDS)
    args = p.parse_args(argv)

    print("[1] load + v55 feature pipeline (veterans_only=False)...")
    dev_df, oot_df = build_v55_frames()

    print("[2] build v57 matrix (drop RC-3, rename observed, add coverage)...")
    (X_dev, y_dev, dev_dates), (X_oot, y_oot) = build_v57_matrix(dev_df, oot_df)

    print(f"[3] train {args.seeds}-seed ensemble (OOT-safe early stopping)...")
    models = train_v57(X_dev, y_dev, dev_dates, n_seeds=args.seeds)

    print("[4] evaluate on OOT (single, untouched)...")
    metrics = evaluate_oot(models, X_oot, y_oot)
    print(f"    OOT AUC = {metrics['oot_auc_ensemble']:.4f}")

    print("[5] emit bundle...")
    emit_bundle(models, metrics, args.out)
    print("done. Promotion gates: run gf17_train_serve_parity.py --expect-fixed "
          "--contract models/v57/feature_contract.json --cbm models/v57/model.cbm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
