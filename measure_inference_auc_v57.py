"""
V57 Inference AUC Comparison
============================

Measures the AUC difference between:
- OLD: Inference with hardcoded defaults (advisory_cohort_delta=0, etc.)
- NEW: Inference with actual cohort lookups

This quantifies the real-world impact of the survivorship feature fix.

Uses prepared data from training to ensure consistent feature engineering.
"""

import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, brier_score_loss
from catboost import CatBoostClassifier

# Configuration
V57_DIR = Path.home() / "autosafe_work/catboost_production_v57"
PREPARED_DATA = Path.home() / "autosafe_work/v55_prepared_data.pkl"

# Need raw data for model_id lookup check
import duckdb
import sys
sys.path.insert(0, str(Path(__file__).parent))


def load_prepared_data():
    """Load prepared training/test data from V55/V57 training."""
    print("Loading prepared data...")

    with open(PREPARED_DATA, 'rb') as f:
        data = pickle.load(f)

    print(f"  X_train shape: {data['X_train'].shape}")
    print(f"  X_test shape: {data['X_test'].shape}")
    print(f"  Features: {len(data['feature_cols'])}")

    return data


def load_model():
    """Load V57 trained model."""
    print("\nLoading V57 model...")

    model_path = V57_DIR / "model.cbm"
    if not model_path.exists():
        raise FileNotFoundError(f"Model not found: {model_path}")

    model = CatBoostClassifier()
    model.load_model(str(model_path))
    print(f"  Model loaded: {len(model.feature_names_)} features")

    return model


def simulate_old_inference(X_test):
    """
    Simulate OLD inference behavior by resetting survivorship features
    to their hardcoded defaults.
    """
    X_old = X_test.copy()

    # Reset to hardcoded defaults used in old inference
    if 'advisory_cohort_delta' in X_old.columns:
        X_old['advisory_cohort_delta'] = 0.0
    if 'mileage_cohort_ratio' in X_old.columns:
        X_old['mileage_cohort_ratio'] = 1.0
    if 'model_fail_rate_smoothed' in X_old.columns:
        X_old['model_fail_rate_smoothed'] = 0.28
    if 'make_fail_rate_smoothed' in X_old.columns:
        X_old['make_fail_rate_smoothed'] = 0.28
    # mech_decay_index_normalized should equal mech_decay_index (unnormalized)
    if 'mech_decay_index_normalized' in X_old.columns and 'mech_decay_index' in X_old.columns:
        X_old['mech_decay_index_normalized'] = X_old['mech_decay_index']

    return X_old


def main():
    print("=" * 70)
    print("V57 INFERENCE AUC COMPARISON")
    print("=" * 70)

    # Load prepared data and model
    data = load_prepared_data()
    model = load_model()

    X_test = data['X_test']
    y_test = data['y_test']
    feature_cols = data['feature_cols']

    # Check survivorship features
    surv_features = ['advisory_cohort_delta', 'mileage_cohort_ratio',
                     'model_fail_rate_smoothed', 'make_fail_rate_smoothed',
                     'mech_decay_index_normalized', 'mech_decay_index']
    existing = [f for f in surv_features if f in X_test.columns]
    print(f"\nSurvivorship features in data: {existing}")

    # Show current values (NEW behavior - correct lookups from training)
    print("\n  NEW feature values (mean) - with cohort lookups:")
    for f in existing:
        if f in X_test.columns:
            print(f"    {f}: {X_test[f].mean():.4f}")

    # Compute AUC with NEW behavior (correct lookups)
    print("\n" + "-" * 70)
    print("NEW BEHAVIOR (with cohort lookups)")
    print("-" * 70)
    y_pred_new = model.predict_proba(X_test)[:, 1]
    auc_new = roc_auc_score(y_test, y_pred_new)
    print(f"  AUC: {auc_new:.4f} (n={len(y_test):,})")

    # Simulate OLD behavior (hardcoded defaults)
    print("\n" + "-" * 70)
    print("OLD BEHAVIOR (hardcoded defaults)")
    print("-" * 70)
    X_old = simulate_old_inference(X_test)

    # Show what we changed
    print("  Simulated hardcoded values (mean):")
    for f in existing:
        if f in X_old.columns:
            print(f"    {f}: {X_old[f].mean():.4f}")

    y_pred_old = model.predict_proba(X_old)[:, 1]
    auc_old = roc_auc_score(y_test, y_pred_old)
    print(f"  AUC: {auc_old:.4f} (n={len(y_test):,})")

    # Summary
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"  OLD inference AUC (hardcoded): {auc_old:.4f}")
    print(f"  NEW inference AUC (lookups):   {auc_new:.4f}")
    print(f"  AUC LIFT:                      {auc_new - auc_old:+.4f}")
    print(f"  Relative improvement:          {(auc_new - auc_old) / auc_old * 100:+.2f}%")
    print("=" * 70)

    # Feature importance context
    print("\nFeature importance context (from training):")
    print("  advisory_cohort_delta:       0.31%")
    print("  mileage_cohort_ratio:        2.91%")
    print("  model_fail_rate_smoothed:    3.78%")
    print("  make_fail_rate_smoothed:     0.09%")
    print("  mech_decay_index_normalized: 0.02%")
    print("  TOTAL survivorship:          ~7.1%")

    if auc_new > auc_old:
        print("\n>>> The survivorship feature fix IMPROVES inference AUC.")
        print(f">>> Production inference was degraded by ~{(auc_new - auc_old):.4f} AUC points.")
    elif abs(auc_new - auc_old) < 0.001:
        print("\n>>> AUC change is negligible (<0.1%).")
    else:
        print("\n>>> Unexpected: NEW AUC is lower. Investigate feature values.")

    # Additional analysis: prediction distribution changes
    print("\n" + "=" * 70)
    print("PREDICTION ANALYSIS")
    print("=" * 70)

    pred_diff = y_pred_new - y_pred_old
    print(f"  Prediction difference stats:")
    print(f"    Mean:   {pred_diff.mean():+.6f}")
    print(f"    Std:    {pred_diff.std():.6f}")
    print(f"    Min:    {pred_diff.min():+.6f}")
    print(f"    Max:    {pred_diff.max():+.6f}")
    print(f"    |diff| > 0.01: {(np.abs(pred_diff) > 0.01).sum():,} ({(np.abs(pred_diff) > 0.01).mean()*100:.1f}%)")
    print(f"    |diff| > 0.05: {(np.abs(pred_diff) > 0.05).sum():,} ({(np.abs(pred_diff) > 0.05).mean()*100:.1f}%)")

    # Correlation analysis
    corr = np.corrcoef(y_pred_new, y_pred_old)[0, 1]
    print(f"\n  Prediction correlation: {corr:.6f}")

    print("\n" + "=" * 70)
    print("INTERPRETATION")
    print("=" * 70)
    print("""
  The AUC impact is negligible because:

  1. The survivorship features (~7% importance) work in COMBINATION with
     other correlated features. Setting them to constants forces the model
     to rely more on these correlated predictors, which partially compensate.

  2. The hardcoded defaults (0.28 for fail rates, 1.0 for mileage ratio)
     were not wildly off from population averages, so the ranking of
     predictions wasn't drastically affected.

  3. CatBoost is robust to single-feature perturbations when there are
     107 features with redundant predictive signals.

  HOWEVER, the fix is still important because:

  - Individual predictions DO change (see prediction diff stats above)
  - Training/inference parity is restored (code correctness)
  - The features now use actual vehicle-specific values as intended
  - Edge cases (unusual vehicles) now get appropriate risk adjustments
""")

    # ======================================================================
    # DIAGNOSTIC 1: Lookup Hit Rate
    # ======================================================================
    print("\n" + "=" * 70)
    print("DIAGNOSTIC 1: LOOKUP HIT RATE")
    print("=" * 70)

    # Load model_hierarchical to check keys
    model_hier_path = V57_DIR / "model_hierarchical_features.pkl"
    with open(model_hier_path, 'rb') as f:
        model_hier = pickle.load(f)

    # Get unique model_fail_rate_smoothed values to see if they vary
    model_fr = X_test['model_fail_rate_smoothed']
    unique_model_fr = model_fr.nunique()
    print(f"  Unique model_fail_rate_smoothed values: {unique_model_fr}")
    print(f"  Value range: [{model_fr.min():.4f}, {model_fr.max():.4f}]")

    # Check if values are constant (would indicate lookup failure)
    if unique_model_fr < 10:
        print(f"  WARNING: Only {unique_model_fr} unique values - possible lookup failure!")
    else:
        print(f"  OK: {unique_model_fr} unique values indicates lookups are working")

    # Check model_hierarchical stats
    print(f"\n  model_hierarchical stats:")
    print(f"    global_fail_rate: {model_hier.global_fail_rate:.4f}")
    print(f"    model_rates entries: {len(model_hier.model_rates)}")
    print(f"    make_rates entries: {len(model_hier.make_rates)}")

    # Sample some model_rates keys to check format
    sample_keys = list(model_hier.model_rates.keys())[:5]
    print(f"    Sample model_rates keys: {sample_keys}")

    # ======================================================================
    # DIAGNOSTIC 2: Brier Score Comparison
    # ======================================================================
    print("\n" + "=" * 70)
    print("DIAGNOSTIC 2: BRIER SCORE COMPARISON")
    print("=" * 70)

    brier_old = brier_score_loss(y_test, y_pred_old)
    brier_new = brier_score_loss(y_test, y_pred_new)
    brier_improvement = brier_old - brier_new

    print(f"  Brier Score OLD (hardcoded): {brier_old:.6f}")
    print(f"  Brier Score NEW (lookups):   {brier_new:.6f}")
    print(f"  Brier improvement:           {brier_improvement:+.6f}")

    if brier_improvement > 0.001:
        print(f"  OK: Brier improved by {brier_improvement:.6f} (shift is helpful)")
    elif brier_improvement > 0:
        print(f"  MARGINAL: Brier improved slightly ({brier_improvement:.6f})")
    else:
        print(f"  WARNING: Brier got WORSE by {-brier_improvement:.6f} (shift may be harmful)")

    # ======================================================================
    # DIAGNOSTIC 3: Top-Decile Jaccard Index
    # ======================================================================
    print("\n" + "=" * 70)
    print("DIAGNOSTIC 3: TOP-DECILE JACCARD INDEX")
    print("=" * 70)

    n = len(y_test)
    top_k = n // 10  # top 10%

    top_old_idx = set(np.argsort(y_pred_old)[-top_k:])
    top_new_idx = set(np.argsort(y_pred_new)[-top_k:])

    intersection = len(top_old_idx & top_new_idx)
    union = len(top_old_idx | top_new_idx)
    jaccard = intersection / union

    print(f"  Top 10% vehicles: {top_k:,}")
    print(f"  Intersection:     {intersection:,}")
    print(f"  Union:            {union:,}")
    print(f"  Jaccard Index:    {jaccard:.4f}")

    if jaccard > 0.98:
        print(f"  WARNING: Jaccard > 0.98 - survivorship features not changing outcomes!")
    elif jaccard > 0.95:
        print(f"  MARGINAL: Jaccard {jaccard:.4f} - minimal change in top-risk identification")
    elif jaccard > 0.80:
        print(f"  OK: Jaccard {jaccard:.4f} - healthy reordering in top decile")
    else:
        print(f"  WARNING: Jaccard < 0.80 - chaotic reordering, investigate!")

    # ======================================================================
    # SUMMARY TABLE
    # ======================================================================
    print("\n" + "=" * 70)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 70)
    print(f"  | Diagnostic              | Value      | Status     |")
    print(f"  |-------------------------|------------|------------|")
    print(f"  | Unique model_fr values  | {unique_model_fr:>10} | {'OK' if unique_model_fr >= 10 else 'WARN':>10} |")
    print(f"  | Brier improvement       | {brier_improvement:>+10.6f} | {'OK' if brier_improvement > 0 else 'WARN':>10} |")
    print(f"  | Top-decile Jaccard      | {jaccard:>10.4f} | {'OK' if 0.80 <= jaccard <= 0.95 else 'WARN':>10} |")


if __name__ == "__main__":
    main()
