"""
Quick validation of Production V2 with reduced bootstrap samples.
Only validates Production V2 to get slice analysis.
"""

import json
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from catboost import CatBoostClassifier, Pool
from sklearn.metrics import (
    roc_auc_score, brier_score_loss, log_loss,
    precision_score, recall_score, f1_score, average_precision_score
)
import pickle
import sys
sys.path.insert(0, str(Path("/Users/henrirapson/autosafe_work")))
from hierarchical_make_adjustment import HierarchicalFeatures
from sklearn.linear_model import LogisticRegression


class PlattCalibrator:
    def __init__(self):
        self.lr = LogisticRegression(solver='lbfgs', max_iter=1000)
        self.fitted = False

    def fit(self, y_true, y_pred_proba):
        eps = 1e-10
        y_pred_clipped = np.clip(y_pred_proba, eps, 1 - eps)
        log_odds = np.log(y_pred_clipped / (1 - y_pred_clipped))
        self.lr.fit(log_odds.reshape(-1, 1), y_true)
        self.fitted = True
        return self

    def predict_proba(self, y_pred_proba):
        if not self.fitted:
            raise ValueError("Calibrator not fitted")
        eps = 1e-10
        y_pred_clipped = np.clip(y_pred_proba, eps, 1 - eps)
        log_odds = np.log(y_pred_clipped / (1 - y_pred_clipped))
        return self.lr.predict_proba(log_odds.reshape(-1, 1))[:, 1]


def bootstrap_auc(y_true, y_pred, n_samples=100, seed=42):
    """Quick bootstrap with fewer samples."""
    np.random.seed(seed)
    n = len(y_true)
    aucs = []
    for _ in range(n_samples):
        idx = np.random.choice(n, size=n, replace=True)
        if len(np.unique(y_true[idx])) < 2:
            continue
        try:
            auc = roc_auc_score(y_true[idx], y_pred[idx])
            aucs.append(auc)
        except:
            continue
    if len(aucs) < 50:
        return float(roc_auc_score(y_true, y_pred)), np.nan, np.nan
    return np.mean(aucs), np.percentile(aucs, 2.5), np.percentile(aucs, 97.5)


def compute_calibration_error(y_true, y_pred, n_bins=10):
    """Compute ECE."""
    bin_edges = np.linspace(0, 1, n_bins + 1)
    bin_details = []
    total_samples = len(y_true)
    ece = 0.0
    for i in range(n_bins):
        mask = (y_pred >= bin_edges[i]) & (y_pred < bin_edges[i + 1])
        if i == n_bins - 1:
            mask = (y_pred >= bin_edges[i]) & (y_pred <= bin_edges[i + 1])
        n_in_bin = mask.sum()
        if n_in_bin > 0:
            mean_pred = y_pred[mask].mean()
            mean_true = y_true[mask].mean()
            bin_error = abs(mean_pred - mean_true)
            ece += (n_in_bin / total_samples) * bin_error
            bin_details.append({
                "bin": i, "bin_range": f"{bin_edges[i]:.2f}-{bin_edges[i+1]:.2f}",
                "count": int(n_in_bin), "mean_predicted": float(mean_pred),
                "mean_actual": float(mean_true), "calibration_error": float(bin_error)
            })
    return ece, bin_details


def compute_slice_metrics(df, y_pred, slice_col):
    """Compute AUC for each slice."""
    results = {}
    df = df.copy()
    df['y_pred'] = y_pred
    for slice_val in df[slice_col].dropna().unique():
        mask = df[slice_col] == slice_val
        slice_df = df[mask]
        if len(slice_df) < 100 or slice_df['is_failure'].nunique() < 2:
            continue
        try:
            auc = roc_auc_score(slice_df['is_failure'], slice_df['y_pred'])
            results[str(slice_val)] = {
                "auc": float(auc), "n_samples": len(slice_df),
                "failure_rate": float(slice_df['is_failure'].mean())
            }
        except:
            continue
    return results


def main():
    print("=" * 70)
    print("PRODUCTION V2 QUICK VALIDATION")
    print("=" * 70)

    # Paths
    AUTOSAFE_DIR = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
    WORK_DIR = Path("/Users/henrirapson/autosafe_work")
    MODEL_PATH = WORK_DIR / "catboost_production" / "model.cbm"
    HIERARCHICAL_PATH = WORK_DIR / "catboost_production" / "hierarchical_features.pkl"
    PLATT_PATH = WORK_DIR / "catboost_production" / "platt_calibrator.pkl"
    OOT_SET = AUTOSAFE_DIR / "stratified_samples" / "oot_test_set.parquet"

    # Load test data
    print("\n[1] Loading OOT test data...")
    test_df = pd.read_parquet(OOT_SET)
    print(f"  Shape: {test_df.shape}")
    print(f"  Failure rate: {test_df['is_failure'].mean():.4f}")

    # Load model
    print("\n[2] Loading Production V2 model...")
    model = CatBoostClassifier()
    model.load_model(str(MODEL_PATH))
    feature_names = model.feature_names_
    print(f"  Features: {feature_names}")

    # Load and apply hierarchical features
    print("\n[3] Applying hierarchical features...")
    hierarchical = HierarchicalFeatures.load(HIERARCHICAL_PATH)
    test_df = hierarchical.transform(test_df, make_col='make', prev_outcome_col='prev_cycle_outcome_band')

    # Load Platt calibrator
    print("\n[4] Loading Platt calibrator...")
    with open(PLATT_PATH, 'rb') as f:
        platt = pickle.load(f)
    print(f"  Intercept: {platt.lr.intercept_[0]:.4f}, Coef: {platt.lr.coef_[0][0]:.4f}")

    # Prepare features - all categorical features from the model
    cat_features = ['prev_cycle_outcome_band', 'gap_band', 'make', 'advisory_trend',
                    'make_x_age_band', 'make_x_prev_outcome']
    cat_indices = [i for i, f in enumerate(feature_names) if f in cat_features]

    # Handle missing values for categorical features
    for col in cat_features:
        if col in test_df.columns:
            test_df[col] = test_df[col].fillna('UNKNOWN').astype(str)

    numeric_fill = {
        'n_prior_tests': 0, 'n_prior_fails': 0, 'prev_count_advisory': 0,
        'test_mileage': 50000, 'days_since_last_test': 365,
        'prior_fail_rate_smoothed': 0.33, 'make_fail_rate_smoothed': 0.28,
        'segment_fail_rate_smoothed': 0.28
    }
    for col, val in numeric_fill.items():
        if col in test_df.columns:
            test_df[col] = test_df[col].fillna(val)

    X_test = test_df[feature_names].copy()
    y_test = test_df['is_failure'].values

    # Make predictions
    print("\n[5] Generating predictions...")
    test_pool = Pool(X_test, cat_features=cat_indices)
    y_pred_raw = model.predict_proba(test_pool)[:, 1]
    y_pred_cal = platt.predict_proba(y_pred_raw)
    print(f"  Raw pred mean: {y_pred_raw.mean():.4f}")
    print(f"  Calibrated pred mean: {y_pred_cal.mean():.4f}")

    # Compute metrics
    print("\n[6] Computing metrics (100 bootstrap samples)...")
    auc_mean, auc_lower, auc_upper = bootstrap_auc(y_test, y_pred_cal, n_samples=100)
    brier = brier_score_loss(y_test, y_pred_cal)
    ll = log_loss(y_test, np.clip(y_pred_cal, 1e-7, 1-1e-7))
    avg_precision = average_precision_score(y_test, y_pred_cal)
    ece, cal_bins = compute_calibration_error(y_test, y_pred_cal)

    print(f"\n  AUC: {auc_mean:.4f} (95% CI: {auc_lower:.4f} - {auc_upper:.4f})")
    print(f"  Brier Score: {brier:.4f}")
    print(f"  Log Loss: {ll:.4f}")
    print(f"  Avg Precision: {avg_precision:.4f}")
    print(f"  ECE: {ece:.4f}")

    # Raw metrics
    brier_raw = brier_score_loss(y_test, y_pred_raw)
    ll_raw = log_loss(y_test, np.clip(y_pred_raw, 1e-7, 1-1e-7))
    print(f"\n  Raw Brier: {brier_raw:.4f} (vs calibrated: {brier:.4f})")
    print(f"  Raw LogLoss: {ll_raw:.4f} (vs calibrated: {ll:.4f})")

    # Slice analysis
    print("\n[7] Computing slice metrics...")
    slices = {}

    if 'age_band' in test_df.columns:
        slices['by_age_band'] = compute_slice_metrics(test_df, y_pred_cal, 'age_band')
        print("\n  Slice AUC by Age Band:")
        for k, v in sorted(slices['by_age_band'].items()):
            print(f"    {k}: AUC={v['auc']:.4f} (n={v['n_samples']:,})")

    if 'mileage_band' in test_df.columns:
        slices['by_mileage_band'] = compute_slice_metrics(test_df, y_pred_cal, 'mileage_band')
        print("\n  Slice AUC by Mileage Band:")
        for k, v in sorted(slices['by_mileage_band'].items()):
            print(f"    {k}: AUC={v['auc']:.4f} (n={v['n_samples']:,})")

    if 'make' in test_df.columns:
        top_makes = test_df['make'].value_counts().head(10).index.tolist()
        test_df['make_filtered'] = test_df['make'].where(test_df['make'].isin(top_makes), 'OTHER')
        slices['by_make_top10'] = compute_slice_metrics(test_df, y_pred_cal, 'make_filtered')
        print("\n  Slice AUC by Make (Top 10):")
        for k, v in sorted(slices['by_make_top10'].items(), key=lambda x: -x[1]['n_samples']):
            print(f"    {k}: AUC={v['auc']:.4f} (n={v['n_samples']:,})")

    if 'prev_cycle_outcome_band' in test_df.columns:
        slices['by_history'] = compute_slice_metrics(test_df, y_pred_cal, 'prev_cycle_outcome_band')
        print("\n  Slice AUC by History:")
        for k, v in sorted(slices['by_history'].items()):
            print(f"    {k}: AUC={v['auc']:.4f} (n={v['n_samples']:,})")

    # Save results
    print("\n[8] Saving results...")
    results = {
        "model_name": "PRODUCTION_V2",
        "timestamp": datetime.now().isoformat(),
        "performance": {
            "auc": {"value": auc_mean, "ci_lower": auc_lower, "ci_upper": auc_upper},
            "brier_score": brier,
            "log_loss": ll,
            "average_precision": avg_precision,
            "calibration": {"ece": ece, "bins": cal_bins},
            "raw_metrics": {"brier_score": brier_raw, "log_loss": ll_raw},
            "calibration_applied": True
        },
        "slices": slices,
        "data_stats": {
            "n_test_samples": len(y_test),
            "actual_failure_rate": float(y_test.mean()),
            "predicted_failure_rate": float(y_pred_cal.mean()),
            "raw_predicted_failure_rate": float(y_pred_raw.mean()),
        }
    }

    output_path = WORK_DIR / "validation_production_v2.json"
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"  Saved to: {output_path}")

    print("\n" + "=" * 70)
    return results


if __name__ == "__main__":
    main()
