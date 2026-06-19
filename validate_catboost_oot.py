#!/usr/bin/env python3
"""
AutoSafe CatBoost Model Validation - OOT 2024 Protocol

This script validates the AutoSafe CatBoost model following strict temporal validation:
- Training data: 2016-2023
- OOT Test data: 2024

Metrics computed:
- ROC-AUC with bootstrap confidence intervals
- Brier Score
- Log Loss
- Calibration Error (ECE)
- Precision/Recall at various thresholds
- Slice analysis by age, mileage, make

Author: ML Validation Engineer
Date: 2026-01-03
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, Pool
from sklearn.metrics import (
    roc_auc_score,
    brier_score_loss,
    log_loss,
    precision_recall_curve,
    average_precision_score,
    roc_curve,
    precision_score,
    recall_score,
    f1_score
)
import pickle
import sys
sys.path.insert(0, str(Path("/Users/henrirapson/autosafe_work")))
from hierarchical_make_adjustment import HierarchicalFeatures
from sklearn.linear_model import LogisticRegression


class PlattCalibrator:
    """Platt scaling for probability calibration.

    Fits a logistic regression on log-odds to map raw probabilities
    to calibrated probabilities that match the target distribution.
    """

    def __init__(self):
        self.lr = LogisticRegression(solver='lbfgs', max_iter=1000)
        self.fitted = False

    def fit(self, y_true, y_pred_proba):
        """Fit calibrator on validation data."""
        eps = 1e-10
        y_pred_clipped = np.clip(y_pred_proba, eps, 1 - eps)
        log_odds = np.log(y_pred_clipped / (1 - y_pred_clipped))
        self.lr.fit(log_odds.reshape(-1, 1), y_true)
        self.fitted = True
        return self

    def predict_proba(self, y_pred_proba):
        """Apply calibration to raw probabilities."""
        if not self.fitted:
            raise ValueError("Calibrator not fitted. Call fit() first.")
        eps = 1e-10
        y_pred_clipped = np.clip(y_pred_proba, eps, 1 - eps)
        log_odds = np.log(y_pred_clipped / (1 - y_pred_clipped))
        return self.lr.predict_proba(log_odds.reshape(-1, 1))[:, 1]

    def get_params(self):
        """Get calibration parameters."""
        if not self.fitted:
            return None
        return {
            'intercept': float(self.lr.intercept_[0]),
            'coef': float(self.lr.coef_[0][0])
        }

# =============================================================================
# Configuration
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("/Users/henrirapson/autosafe_work/validate_catboost_oot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Paths
AUTOSAFE_DIR = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
WORK_DIR = Path("/Users/henrirapson/autosafe_work")

# Model paths - we'll validate both release candidate and latest
MODEL_PATHS = {
    "RELEASE_CANDIDATE_V3_12": WORK_DIR / "RELEASE_CANDIDATE_V3_12_AUC_0.7256" / "model.cbm",
    "V3_13": WORK_DIR / "catboost_v3_13_work" / "model.cbm",
    "PRODUCTION_V2": WORK_DIR / "catboost_production" / "model.cbm",
}

# Production model auxiliary files
PRODUCTION_HIERARCHICAL = WORK_DIR / "catboost_production" / "hierarchical_features.pkl"
PRODUCTION_PLATT = WORK_DIR / "catboost_production" / "platt_calibrator.pkl"

# Data paths
DEV_SET = AUTOSAFE_DIR / "stratified_samples" / "dev_set.parquet"
OOT_SET = AUTOSAFE_DIR / "stratified_samples" / "oot_test_set.parquet"
STRATIFICATION_REPORT = AUTOSAFE_DIR / "stratified_samples" / "stratification_report.json"

# Validation configuration
BOOTSTRAP_SAMPLES = 1000
RANDOM_SEED = 42
CALIBRATION_BINS = 10


# =============================================================================
# Integrity Check Functions
# =============================================================================

def check_data_integrity(df: pd.DataFrame, name: str) -> Dict:
    """Run integrity checks on a dataset."""
    report = {
        "name": name,
        "n_rows": len(df),
        "checks": {}
    }

    # Check 1: Key uniqueness (test_id should be unique)
    if 'test_id' in df.columns:
        n_unique = df['test_id'].nunique()
        n_total = len(df)
        is_unique = n_unique == n_total
        report["checks"]["key_uniqueness"] = {
            "status": "PASS" if is_unique else "FAIL",
            "unique_keys": n_unique,
            "total_rows": n_total,
            "duplicates": n_total - n_unique
        }

    # Check 2: Label distribution sanity
    if 'is_failure' in df.columns:
        fail_rate = df['is_failure'].mean()
        # Expected MOT failure rate is 15-30%
        is_sane = 0.10 <= fail_rate <= 0.40
        report["checks"]["label_sanity"] = {
            "status": "PASS" if is_sane else "WARN",
            "failure_rate": float(fail_rate),
            "expected_range": "10%-40%"
        }

    # Check 3: Missing values per feature
    missing_report = {}
    for col in df.columns:
        missing_pct = df[col].isna().mean()
        if missing_pct > 0:
            missing_report[col] = float(missing_pct)
    report["checks"]["missingness"] = {
        "status": "PASS" if all(v < 0.50 for v in missing_report.values()) else "WARN",
        "features_with_missing": missing_report
    }

    # Check 4: Feature range sanity
    if 'test_mileage' in df.columns:
        valid_mileage = df['test_mileage'].between(0, 500000, inclusive='both') | df['test_mileage'].isna()
        pct_valid = valid_mileage.mean()
        report["checks"]["mileage_range"] = {
            "status": "PASS" if pct_valid > 0.99 else "WARN",
            "pct_valid": float(pct_valid),
            "min": float(df['test_mileage'].min()) if not df['test_mileage'].isna().all() else None,
            "max": float(df['test_mileage'].max()) if not df['test_mileage'].isna().all() else None
        }

    return report


def check_temporal_leakage(train_df: pd.DataFrame, test_df: pd.DataFrame) -> Dict:
    """Check for temporal leakage between train and test sets."""
    report = {"checks": {}}

    # Check 1: No overlapping test_ids
    if 'test_id' in train_df.columns and 'test_id' in test_df.columns:
        train_ids = set(train_df['test_id'].dropna())
        test_ids = set(test_df['test_id'].dropna())
        overlap = train_ids & test_ids
        report["checks"]["no_id_overlap"] = {
            "status": "PASS" if len(overlap) == 0 else "FAIL",
            "overlap_count": len(overlap)
        }

    # Check 2: Temporal separation (test dates should be after train dates)
    if 'test_date' in train_df.columns and 'test_date' in test_df.columns:
        train_max = pd.to_datetime(train_df['test_date']).max()
        test_min = pd.to_datetime(test_df['test_date']).min()
        is_separated = test_min > train_max if pd.notna(train_max) and pd.notna(test_min) else None
        report["checks"]["temporal_separation"] = {
            "status": "PASS" if is_separated else ("FAIL" if is_separated is False else "UNKNOWN"),
            "train_max_date": str(train_max) if pd.notna(train_max) else None,
            "test_min_date": str(test_min) if pd.notna(test_min) else None
        }

    return report


# =============================================================================
# Metric Functions
# =============================================================================

def bootstrap_auc(y_true: np.ndarray, y_pred: np.ndarray,
                  n_samples: int = 1000, seed: int = 42) -> Tuple[float, float, float]:
    """Compute AUC with bootstrap confidence interval."""
    np.random.seed(seed)
    n = len(y_true)
    aucs = []

    for _ in range(n_samples):
        idx = np.random.choice(n, size=n, replace=True)
        # Ensure both classes present in bootstrap sample
        if len(np.unique(y_true[idx])) < 2:
            continue
        try:
            auc = roc_auc_score(y_true[idx], y_pred[idx])
            aucs.append(auc)
        except:
            continue

    if len(aucs) < 100:
        return float(roc_auc_score(y_true, y_pred)), np.nan, np.nan

    mean_auc = np.mean(aucs)
    ci_lower = np.percentile(aucs, 2.5)
    ci_upper = np.percentile(aucs, 97.5)

    return mean_auc, ci_lower, ci_upper


def compute_calibration_error(y_true: np.ndarray, y_pred: np.ndarray,
                               n_bins: int = 10) -> Tuple[float, List[Dict]]:
    """Compute Expected Calibration Error (ECE) and return bin details."""
    bin_edges = np.linspace(0, 1, n_bins + 1)
    bin_details = []

    total_samples = len(y_true)
    ece = 0.0

    for i in range(n_bins):
        mask = (y_pred >= bin_edges[i]) & (y_pred < bin_edges[i + 1])
        if i == n_bins - 1:  # Include right edge for last bin
            mask = (y_pred >= bin_edges[i]) & (y_pred <= bin_edges[i + 1])

        n_in_bin = mask.sum()
        if n_in_bin > 0:
            mean_pred = y_pred[mask].mean()
            mean_true = y_true[mask].mean()
            bin_error = abs(mean_pred - mean_true)
            ece += (n_in_bin / total_samples) * bin_error

            bin_details.append({
                "bin": i,
                "bin_range": f"{bin_edges[i]:.2f}-{bin_edges[i+1]:.2f}",
                "count": int(n_in_bin),
                "mean_predicted": float(mean_pred),
                "mean_actual": float(mean_true),
                "calibration_error": float(bin_error)
            })

    return ece, bin_details


def compute_precision_recall_at_k(y_true: np.ndarray, y_pred: np.ndarray,
                                   k_values: List[float] = [0.1, 0.2, 0.3]) -> Dict:
    """Compute precision and recall at top k% of predictions."""
    results = {}
    n = len(y_true)

    for k in k_values:
        n_top = int(n * k)
        top_indices = np.argsort(y_pred)[-n_top:]

        precision = y_true[top_indices].mean()
        recall = y_true[top_indices].sum() / y_true.sum() if y_true.sum() > 0 else 0

        results[f"top_{int(k*100)}pct"] = {
            "precision": float(precision),
            "recall": float(recall),
            "n_samples": n_top
        }

    return results


def compute_slice_metrics(df: pd.DataFrame, y_pred: np.ndarray,
                          slice_col: str) -> Dict:
    """Compute AUC for each slice of the data."""
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
                "auc": float(auc),
                "n_samples": len(slice_df),
                "failure_rate": float(slice_df['is_failure'].mean())
            }
        except:
            continue

    return results


# =============================================================================
# Main Validation Function
# =============================================================================

def validate_model(model_name: str, model_path: Path,
                   train_df: pd.DataFrame, test_df: pd.DataFrame) -> Dict:
    """Run full validation on a single model."""

    logger.info(f"\n{'='*70}")
    logger.info(f"VALIDATING MODEL: {model_name}")
    logger.info(f"{'='*70}")

    results = {
        "model_name": model_name,
        "model_path": str(model_path),
        "timestamp": datetime.now().isoformat(),
        "integrity": {},
        "performance": {},
        "slices": {},
        "decision": {}
    }

    # Load model
    if not model_path.exists():
        logger.error(f"Model not found: {model_path}")
        results["decision"]["status"] = "REJECT"
        results["decision"]["reason"] = "Model file not found"
        return results

    model = CatBoostClassifier()
    model.load_model(str(model_path))
    feature_names = model.feature_names_
    logger.info(f"Model features: {feature_names}")

    # Check if this is Production V2 - needs hierarchical features
    hierarchical_features = None
    platt_calibrator = None
    is_production_v2 = "PRODUCTION" in model_name.upper()

    if is_production_v2:
        logger.info("Loading hierarchical features for Production model...")
        if PRODUCTION_HIERARCHICAL.exists():
            hierarchical_features = HierarchicalFeatures.load(PRODUCTION_HIERARCHICAL)
        else:
            logger.error(f"Hierarchical features not found: {PRODUCTION_HIERARCHICAL}")

        if PRODUCTION_PLATT.exists():
            with open(PRODUCTION_PLATT, 'rb') as f:
                platt_calibrator = pickle.load(f)
            params = platt_calibrator.get_params()
            logger.info(f"Loaded Platt calibrator: intercept={params['intercept']:.4f}, coef={params['coef']:.4f}")

        # Apply hierarchical features BEFORE checking for missing features (Production V2)
        if hierarchical_features is not None:
            logger.info("Applying hierarchical features to test data...")
            test_df = hierarchical_features.transform(
                test_df,
                make_col='make',
                prev_outcome_col='prev_cycle_outcome_band'
            )
            logger.info(f"  Added: make_fail_rate_smoothed, segment_fail_rate_smoothed, make_x_age_band, make_x_prev_outcome")

    # Check if all required features are available
    missing_features = [f for f in feature_names if f not in test_df.columns]
    if missing_features:
        logger.warning(f"Missing features in test data: {missing_features}")
        # Try to add missing features with default values
        for feat in missing_features:
            if feat == 'mileage_reliability_score':
                # Load from lookup if available
                lookup_path = AUTOSAFE_DIR / "make_mileage_reliability_lookup.parquet"
                if lookup_path.exists():
                    logger.info(f"Loading mileage reliability lookup to fill missing feature...")
                    lookup_df = pd.read_parquet(lookup_path)
                    test_df = test_df.merge(
                        lookup_df[['make', 'mileage_reliability_score']],
                        left_on=test_df['make'].str.upper().str.strip(),
                        right_on='make',
                        how='left',
                        suffixes=('', '_lookup')
                    )
                    # Fill with median for unmatched makes
                    median_val = lookup_df['mileage_reliability_score'].median()
                    test_df['mileage_reliability_score'] = test_df['mileage_reliability_score'].fillna(median_val)
                else:
                    # Use default median value
                    test_df[feat] = 0.41  # Default median
            else:
                logger.error(f"Cannot fill missing feature: {feat}")
                results["decision"]["status"] = "REJECT"
                results["decision"]["reason"] = f"Missing required feature: {feat}"
                return results

    # Prepare features
    cat_features = ['prev_cycle_outcome_band', 'gap_band', 'make', 'make_x_age_band', 'make_x_prev_outcome', 'advisory_trend']
    cat_indices = [i for i, f in enumerate(feature_names) if f in cat_features]

    # Prepare test data
    logger.info("Preparing OOT test data...")
    X_test = test_df[feature_names].copy()
    y_test = test_df['is_failure'].values

    # Handle missing values for categorical features
    for col in cat_features:
        if col in X_test.columns:
            X_test[col] = X_test[col].fillna('UNKNOWN').astype(str)

    # Handle missing values for numeric features
    # FIXED: Use training set statistics to avoid training-serving skew
    # These values are pre-computed from the training set (2016-2023)

    # Features that should be filled with 0 (count-based features for NO_PRIOR population)
    zero_fill_features = {
        'n_prior_tests': 0,
        'n_prior_fails': 0,
        'tests_last_365d': 0,
        'fails_last_365d': 0,
        'prev_count_advisory': 0,  # Always 0 for NO_PRIOR population
    }

    # Boolean features that should be filled with False
    false_fill_features = {
        'is_overdue': False,
        'is_clocked': False,
    }

    # Pre-computed TRAINING set medians for other numeric features
    # Computed from dev_set.parquet (2016-2023 training data)
    training_medians = {
        'vehicle_age_at_test': 8.0,
        'test_mileage': 62000.0,
        'annual_mileage': 8500.0,
        'prev_mileage_delta': 8000.0,
        'mileage_reliability_score': 0.41,
        'days_since_last_test': 365.0,
        'cylinder_capacity': 1598.0,
        'prev_cycle_defect_count': 0.0,
    }

    for col in feature_names:
        if col not in cat_features and col in X_test.columns:
            if col in zero_fill_features:
                X_test[col] = X_test[col].fillna(zero_fill_features[col])
            elif col in false_fill_features:
                X_test[col] = X_test[col].fillna(false_fill_features[col])
            elif col in training_medians:
                X_test[col] = X_test[col].fillna(training_medians[col])
            else:
                # Fallback: log warning and use 0 as default
                logger.warning(f"No pre-computed training median for feature '{col}', using 0")
                X_test[col] = X_test[col].fillna(0)

    # Make predictions
    logger.info("Generating predictions...")
    test_pool = Pool(X_test, cat_features=cat_indices)
    y_pred_raw = model.predict_proba(test_pool)[:, 1]

    # Apply Platt calibration if available
    y_pred = y_pred_raw
    y_pred_calibrated = None
    if platt_calibrator is not None:
        logger.info("Applying Platt calibration...")
        y_pred_calibrated = platt_calibrator.predict_proba(y_pred_raw)
        y_pred = y_pred_calibrated  # Use calibrated for main metrics
        logger.info(f"  Raw pred mean: {y_pred_raw.mean():.4f}, Calibrated pred mean: {y_pred_calibrated.mean():.4f}")

    # ==========================================================================
    # Performance Metrics
    # ==========================================================================
    logger.info("Computing performance metrics...")

    # Core metrics
    auc_mean, auc_ci_lower, auc_ci_upper = bootstrap_auc(y_test, y_pred,
                                                          n_samples=BOOTSTRAP_SAMPLES,
                                                          seed=RANDOM_SEED)
    brier = brier_score_loss(y_test, y_pred)
    ll = log_loss(y_test, np.clip(y_pred, 1e-7, 1-1e-7))
    avg_precision = average_precision_score(y_test, y_pred)

    results["performance"]["auc"] = {
        "value": float(auc_mean),
        "ci_lower": float(auc_ci_lower) if not np.isnan(auc_ci_lower) else None,
        "ci_upper": float(auc_ci_upper) if not np.isnan(auc_ci_upper) else None,
        "ci_width": float(auc_ci_upper - auc_ci_lower) if not np.isnan(auc_ci_lower) else None
    }
    results["performance"]["brier_score"] = float(brier)
    results["performance"]["log_loss"] = float(ll)
    results["performance"]["average_precision"] = float(avg_precision)

    # Add raw metrics if Platt calibration was applied
    if y_pred_calibrated is not None:
        raw_brier = brier_score_loss(y_test, y_pred_raw)
        raw_ll = log_loss(y_test, np.clip(y_pred_raw, 1e-7, 1-1e-7))
        results["performance"]["raw_metrics"] = {
            "brier_score": float(raw_brier),
            "log_loss": float(raw_ll),
            "note": "Metrics above use Platt-calibrated predictions"
        }
        results["performance"]["calibration_applied"] = True
    else:
        results["performance"]["calibration_applied"] = False

    # Calibration
    ece, calibration_bins = compute_calibration_error(y_test, y_pred, n_bins=CALIBRATION_BINS)
    results["performance"]["calibration"] = {
        "ece": float(ece),
        "bins": calibration_bins
    }

    # Precision/Recall at k
    pr_at_k = compute_precision_recall_at_k(y_test, y_pred)
    results["performance"]["precision_recall_at_k"] = pr_at_k

    # Threshold-based metrics at 0.25 (typical MOT failure rate)
    threshold = 0.25
    y_pred_binary = (y_pred >= threshold).astype(int)
    results["performance"]["at_threshold_0.25"] = {
        "precision": float(precision_score(y_test, y_pred_binary, zero_division=0)),
        "recall": float(recall_score(y_test, y_pred_binary, zero_division=0)),
        "f1": float(f1_score(y_test, y_pred_binary, zero_division=0))
    }

    # ==========================================================================
    # Slice Analysis
    # ==========================================================================
    logger.info("Computing slice metrics...")

    # By age band
    if 'age_band' in test_df.columns:
        results["slices"]["by_age_band"] = compute_slice_metrics(test_df, y_pred, 'age_band')

    # By mileage band
    if 'mileage_band' in test_df.columns:
        results["slices"]["by_mileage_band"] = compute_slice_metrics(test_df, y_pred, 'mileage_band')

    # By make (top 10)
    if 'make' in test_df.columns:
        top_makes = test_df['make'].value_counts().head(10).index.tolist()
        test_df_copy = test_df.copy()
        test_df_copy['make_filtered'] = test_df_copy['make'].where(
            test_df_copy['make'].isin(top_makes), 'OTHER'
        )
        results["slices"]["by_make_top10"] = compute_slice_metrics(test_df_copy, y_pred, 'make_filtered')

    # By history status
    if 'prev_cycle_outcome_band' in test_df.columns:
        results["slices"]["by_history"] = compute_slice_metrics(test_df, y_pred, 'prev_cycle_outcome_band')

    # ==========================================================================
    # Distribution stats
    # ==========================================================================
    results["data_stats"] = {
        "n_test_samples": len(y_test),
        "actual_failure_rate": float(y_test.mean()),
        "predicted_failure_rate": float(y_pred.mean()),
        "prediction_min": float(y_pred.min()),
        "prediction_max": float(y_pred.max()),
        "prediction_std": float(y_pred.std())
    }

    return results


def run_full_validation():
    """Run complete validation pipeline."""

    logger.info("="*70)
    logger.info("AUTOSAFE CATBOOST MODEL VALIDATION")
    logger.info("="*70)
    logger.info(f"Timestamp: {datetime.now().isoformat()}")
    logger.info(f"OOT Protocol: Train 2016-2023, Test 2024")

    # Load stratification report
    if STRATIFICATION_REPORT.exists():
        with open(STRATIFICATION_REPORT) as f:
            strat_report = json.load(f)
        logger.info(f"\nStratification report loaded:")
        logger.info(f"  Dev set: {strat_report['dev_set']['n_tests']:,} tests")
        logger.info(f"  OOT set: {strat_report['oot_test_set']['n_tests']:,} tests")

    # Load datasets
    logger.info("\n" + "-"*70)
    logger.info("LOADING DATASETS")
    logger.info("-"*70)

    logger.info(f"Loading dev set from: {DEV_SET}")
    train_df = pd.read_parquet(DEV_SET)
    logger.info(f"  Shape: {train_df.shape}")

    logger.info(f"Loading OOT set from: {OOT_SET}")
    test_df = pd.read_parquet(OOT_SET)
    logger.info(f"  Shape: {test_df.shape}")

    # ==========================================================================
    # Integrity Checks
    # ==========================================================================
    logger.info("\n" + "-"*70)
    logger.info("INTEGRITY CHECKS")
    logger.info("-"*70)

    train_integrity = check_data_integrity(train_df, "Dev Set (Train)")
    test_integrity = check_data_integrity(test_df, "OOT Set (Test 2024)")
    leakage_check = check_temporal_leakage(train_df, test_df)

    # Log integrity results
    for check_name, check_result in train_integrity["checks"].items():
        status = check_result["status"]
        logger.info(f"  Train - {check_name}: {status}")

    for check_name, check_result in test_integrity["checks"].items():
        status = check_result["status"]
        logger.info(f"  Test - {check_name}: {status}")

    for check_name, check_result in leakage_check["checks"].items():
        status = check_result["status"]
        logger.info(f"  Leakage - {check_name}: {status}")

    # Check for critical failures
    critical_failures = []
    for check_name, check_result in leakage_check["checks"].items():
        if check_result["status"] == "FAIL":
            critical_failures.append(f"Leakage check failed: {check_name}")

    if critical_failures:
        logger.error("CRITICAL INTEGRITY FAILURES DETECTED:")
        for failure in critical_failures:
            logger.error(f"  - {failure}")
        # Continue with validation but note the failures

    # ==========================================================================
    # Validate Each Model
    # ==========================================================================
    all_results = {
        "validation_timestamp": datetime.now().isoformat(),
        "protocol": "OOT 2024 (Train 2016-2023, Test 2024)",
        "integrity": {
            "train": train_integrity,
            "test": test_integrity,
            "leakage": leakage_check
        },
        "models": {}
    }

    for model_name, model_path in MODEL_PATHS.items():
        if model_path.exists():
            results = validate_model(model_name, model_path, train_df, test_df)
            all_results["models"][model_name] = results
        else:
            logger.warning(f"Model not found: {model_name} at {model_path}")

    # ==========================================================================
    # Summary Report
    # ==========================================================================
    logger.info("\n" + "="*70)
    logger.info("VALIDATION SUMMARY")
    logger.info("="*70)

    print("\n")
    print("+" + "-"*68 + "+")
    print("| INTEGRITY CHECKS                                                   |")
    print("+" + "-"*20 + "+" + "-"*10 + "+" + "-"*36 + "+")
    print("| {:<18} | {:<8} | {:<34} |".format("Check", "Status", "Details"))
    print("+" + "-"*20 + "+" + "-"*10 + "+" + "-"*36 + "+")

    # Key uniqueness
    key_check = test_integrity["checks"].get("key_uniqueness", {})
    print("| {:<18} | {:<8} | {:<34} |".format(
        "Key uniqueness",
        key_check.get("status", "N/A"),
        f"Duplicates: {key_check.get('duplicates', 'N/A')}"
    ))

    # Label sanity
    label_check = test_integrity["checks"].get("label_sanity", {})
    print("| {:<18} | {:<8} | {:<34} |".format(
        "Label sanity",
        label_check.get("status", "N/A"),
        f"Fail rate: {label_check.get('failure_rate', 0):.4f}"
    ))

    # Temporal separation
    temporal_check = leakage_check["checks"].get("temporal_separation", {})
    print("| {:<18} | {:<8} | {:<34} |".format(
        "Temporal separation",
        temporal_check.get("status", "N/A"),
        f"Test min: {temporal_check.get('test_min_date', 'N/A')[:10] if temporal_check.get('test_min_date') else 'N/A'}"
    ))

    # ID overlap
    id_check = leakage_check["checks"].get("no_id_overlap", {})
    print("| {:<18} | {:<8} | {:<34} |".format(
        "No ID overlap",
        id_check.get("status", "N/A"),
        f"Overlap: {id_check.get('overlap_count', 'N/A')}"
    ))

    print("+" + "-"*20 + "+" + "-"*10 + "+" + "-"*36 + "+")

    # Performance summary
    print("\n")
    print("+" + "-"*68 + "+")
    print("| OOT PERFORMANCE (2024 Holdout)                                     |")
    print("+" + "-"*25 + "+" + "-"*12 + "+" + "-"*12 + "+" + "-"*15 + "+")
    print("| {:<23} | {:<10} | {:<10} | {:<13} |".format("Model", "AUC", "Brier", "Log Loss"))
    print("+" + "-"*25 + "+" + "-"*12 + "+" + "-"*12 + "+" + "-"*15 + "+")

    for model_name, model_results in all_results["models"].items():
        perf = model_results.get("performance", {})
        auc_info = perf.get("auc", {})
        auc_val = auc_info.get("value", 0)
        brier_val = perf.get("brier_score", 0)
        ll_val = perf.get("log_loss", 0)

        print("| {:<23} | {:<10.4f} | {:<10.4f} | {:<13.4f} |".format(
            model_name[:23], auc_val, brier_val, ll_val
        ))

    print("+" + "-"*25 + "+" + "-"*12 + "+" + "-"*12 + "+" + "-"*15 + "+")

    # Detailed metrics for each model
    for model_name, model_results in all_results["models"].items():
        perf = model_results.get("performance", {})
        auc_info = perf.get("auc", {})
        cal_info = perf.get("calibration", {})
        stats = model_results.get("data_stats", {})

        print(f"\n--- {model_name} Details ---")
        print(f"  AUC: {auc_info.get('value', 0):.4f} (95% CI: {auc_info.get('ci_lower', 0):.4f} - {auc_info.get('ci_upper', 0):.4f})")
        print(f"  Brier Score: {perf.get('brier_score', 0):.4f}")
        print(f"  Log Loss: {perf.get('log_loss', 0):.4f}")
        print(f"  Avg Precision: {perf.get('average_precision', 0):.4f}")
        print(f"  Calibration Error (ECE): {cal_info.get('ece', 0):.4f}")
        print(f"  Samples: {stats.get('n_test_samples', 0):,}")
        print(f"  Actual Fail Rate: {stats.get('actual_failure_rate', 0):.4f}")
        print(f"  Predicted Fail Rate: {stats.get('predicted_failure_rate', 0):.4f}")

        # Slice performance
        slices = model_results.get("slices", {})
        if "by_age_band" in slices:
            print(f"\n  Slice AUC by Age Band:")
            for age_band, metrics in sorted(slices["by_age_band"].items()):
                print(f"    {age_band}: AUC={metrics['auc']:.4f} (n={metrics['n_samples']:,})")

        if "by_history" in slices:
            print(f"\n  Slice AUC by History:")
            for hist, metrics in sorted(slices["by_history"].items()):
                print(f"    {hist}: AUC={metrics['auc']:.4f} (n={metrics['n_samples']:,})")

    # ==========================================================================
    # Decision
    # ==========================================================================
    print("\n")
    print("+" + "-"*68 + "+")
    print("| DECISION                                                           |")
    print("+" + "-"*68 + "+")

    # Determine overall decision
    best_model = None
    best_auc = 0
    for model_name, model_results in all_results["models"].items():
        auc = model_results.get("performance", {}).get("auc", {}).get("value", 0)
        if auc > best_auc:
            best_auc = auc
            best_model = model_name

    if best_auc >= 0.70:
        decision = "APPROVE"
        rationale = f"{best_model} achieves AUC {best_auc:.4f} on OOT 2024, meeting production threshold."
    elif best_auc >= 0.65:
        decision = "PARK"
        rationale = f"{best_model} achieves AUC {best_auc:.4f}. Marginal performance - requires stakeholder review."
    else:
        decision = "REJECT"
        rationale = f"Best AUC {best_auc:.4f} is below acceptable threshold of 0.65."

    # Check for critical failures
    if critical_failures:
        decision = "REJECT"
        rationale = "Critical integrity failures detected. " + "; ".join(critical_failures)

    print(f"| Status: {decision:<58} |")
    print("+" + "-"*68 + "+")
    print(f"| Rationale: {rationale[:56]:<56} |")
    if len(rationale) > 56:
        print(f"| {rationale[56:112]:<66} |")
    print("+" + "-"*68 + "+")

    all_results["decision"] = {
        "status": decision,
        "rationale": rationale,
        "best_model": best_model,
        "best_auc": float(best_auc) if best_auc else None
    }

    # Save results
    output_path = WORK_DIR / "validation_results_oot_2024.json"
    with open(output_path, 'w') as f:
        json.dump(all_results, f, indent=2, default=str)

    logger.info(f"\nResults saved to: {output_path}")

    return all_results


if __name__ == "__main__":
    results = run_full_validation()
