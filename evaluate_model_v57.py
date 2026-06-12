"""v57 bundle evaluation — full OOT metric reporting from the packaged run.

Consumes the trainer-exported ``oot_eval_frame.parquet`` (scores produced by
the exact packaged model.cbm + calibrator.json) instead of re-running the
matrix pipeline, after verifying that the supplied --dataset is byte-for-
byte the OOT input recorded in training_manifest.json.

Outputs (next to the bundle):
- metrics.json            ROC-AUC, PR-AUC, log loss, Brier, calibrated
                          Brier, precision@1/5/10%, lift@1/5/10%
- calibration_bins.csv    10 equal-frequency bins of the calibrated score
- cohort_auc.csv          per-cohort discrimination across 7 axes

Usage:
  python3.13 evaluate_model_v57.py --model-dir models/v57 \
      --dataset stratified_samples/oot_set_with_advisory.parquet
"""

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    log_loss,
    roc_auc_score,
)

REQUIRED_BUNDLE_FILES = ("model.cbm", "calibrator.json", "feature_contract.json",
                         "training_manifest.json", "oot_eval_frame.parquet")

COHORT_AXES = (
    "age_band", "mileage_band", "make", "prev_cycle_outcome_band",
    "gap_band", "has_prior_test_observed", "has_left_truncated_history",
)
TOP_MAKES = 20
MIN_COHORT_N = 200


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def precision_and_lift_at_k(y_true: np.ndarray, y_score: np.ndarray, k: float):
    n = len(y_true)
    cutoff = max(1, int(n * k))
    top = np.argsort(-y_score)[:cutoff]
    precision = float(np.mean(y_true[top]))
    base = float(np.mean(y_true))
    return precision, (precision / base if base > 0 else float("nan"))


def mileage_band(m: float) -> str:
    # serving edges (feature_engineering_v55:716-724)
    if m < 30000:
        return "0-30k"
    if m < 60000:
        return "30k-60k"
    if m < 100000:
        return "60k-100k"
    return "100k+"


def verify_dataset(manifest: dict, dataset: Path) -> None:
    recorded = {Path(e["path"]).name: e for e in manifest.get("input_files", [])}
    entry = recorded.get(dataset.name)
    if entry is None:
        raise SystemExit(f"--dataset {dataset.name} is not among the manifest's inputs")
    actual = sha256_of(dataset)
    if actual != entry["sha256"]:
        raise SystemExit(
            f"--dataset hash mismatch vs training manifest:\n"
            f"  manifest: {entry['sha256']}\n  actual:   {actual}\n"
            f"The evaluation frame was produced from a different file; re-train "
            f"or point --dataset at the recorded input.")
    print(f"  dataset hash verified: {actual[:16]}…")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True, type=Path)
    ap.add_argument("--dataset", required=True, type=Path)
    args = ap.parse_args()

    bundle = args.model_dir
    missing = [n for n in REQUIRED_BUNDLE_FILES if not (bundle / n).exists()]
    if missing:
        raise SystemExit(f"bundle incomplete, missing: {missing}")

    manifest = json.loads((bundle / "training_manifest.json").read_text())
    verify_dataset(manifest, args.dataset)

    frame = pd.read_parquet(bundle / "oot_eval_frame.parquet")
    y = frame["target"].to_numpy()
    raw = frame["raw_score"].to_numpy()
    cal = frame["calibrated_score"].to_numpy()
    print(f"  evaluation frame: {len(frame):,} rows, base rate {y.mean():.4f}")

    metrics = {
        "version": manifest.get("version", "v57"),
        "n_features": manifest.get("feature_count"),
        "oot_rows": int(len(y)),
        "oot_base_rate": float(y.mean()),
        "roc_auc": float(roc_auc_score(y, raw)),
        "pr_auc": float(average_precision_score(y, raw)),
        "log_loss": float(log_loss(y, raw)),
        "brier": float(brier_score_loss(y, raw)),
        "calibrated_brier": float(brier_score_loss(y, cal)),
        "evaluated": datetime.now().isoformat(),
        "evaluation_source": "oot_eval_frame.parquet (scores from the packaged bundle)",
    }
    for k_pct, k in (("1pct", 0.01), ("5pct", 0.05), ("10pct", 0.10)):
        p, lift = precision_and_lift_at_k(y, cal, k)
        metrics[f"precision_at_{k_pct}"] = p
        metrics[f"lift_at_{k_pct}"] = lift

    # consistency check against trainer-reported values, then merge
    prior = json.loads((bundle / "metrics.json").read_text())
    for key in ("roc_auc", "pr_auc", "brier", "calibrated_brier", "log_loss"):
        if key in prior and abs(prior[key] - metrics[key]) > 1e-9:
            raise SystemExit(
                f"metric drift vs trainer-reported {key}: "
                f"{prior[key]} != {metrics[key]} — bundle inconsistent")
    merged = {**prior, **metrics}
    (bundle / "metrics.json").write_text(json.dumps(merged, indent=2))

    # ---- calibration bins (10 equal-frequency) ------------------------------
    order = np.argsort(cal)
    bins = np.array_split(order, 10)
    with open(bundle / "calibration_bins.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["bin", "n_samples", "mean_prediction",
                    "actual_failure_rate", "abs_calibration_error"])
        for i, idx in enumerate(bins, start=1):
            mp = float(cal[idx].mean())
            ar = float(y[idx].mean())
            w.writerow([i, len(idx), f"{mp:.6f}", f"{ar:.6f}", f"{abs(mp - ar):.6f}"])

    # ---- cohort AUC ----------------------------------------------------------
    frame = frame.copy()
    frame["age_band"] = frame["serving_age_band"].astype(str)
    frame["mileage_band"] = [mileage_band(m) for m in
                             pd.to_numeric(frame["test_mileage"], errors="coerce").fillna(0)]
    top = frame["make"].value_counts().head(TOP_MAKES).index
    frame["make"] = np.where(frame["make"].isin(top), frame["make"], "OTHER")

    rows = []
    for axis in COHORT_AXES:
        for value, sub in frame.groupby(frame[axis].astype(str)):
            yv = sub["target"].to_numpy()
            n, nf = len(yv), int(yv.sum())
            if n >= MIN_COHORT_N and 0 < nf < n:
                auc = float(roc_auc_score(yv, sub["raw_score"].to_numpy()))
            else:
                auc = ""
            rows.append([axis, value, n, nf, f"{nf / n:.6f}",
                         f"{auc:.6f}" if auc != "" else ""])
    with open(bundle / "cohort_auc.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cohort_axis", "cohort_value", "n_samples", "n_failures",
                    "event_rate", "roc_auc"])
        w.writerows(rows)

    print("  wrote metrics.json, calibration_bins.csv, cohort_auc.csv")
    for k in ("roc_auc", "pr_auc", "log_loss", "brier", "calibrated_brier",
              "precision_at_1pct", "precision_at_5pct", "precision_at_10pct",
              "lift_at_1pct", "lift_at_5pct", "lift_at_10pct"):
        print(f"  {k}: {metrics[k]:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
