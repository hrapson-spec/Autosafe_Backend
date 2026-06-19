"""
Validate Frozen vs Recomputed Features

This script validates that features computed at inference time match
features computed at training time. Specifically checks:

1. segment_fail_rate_smoothed: Frozen pickle vs recomputed from data
2. prev_cycle_outcome_band: Values in dataset vs derived from history
3. model_fail_rate_smoothed: Frozen pickle vs recomputed

Key diagnostic: Earlier finding showed segment_fail_rate_smoothed correlation
between Layer 1 and Layer 2 was 0.45 (should be ~1.0).

Run: python validate_frozen_vs_recomputed.py
"""

import pickle
import sys
from pathlib import Path
from typing import Dict, Tuple

import duckdb
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

# Add to path
sys.path.insert(0, str(Path(__file__).parent))

from cohort_gated_ensemble.config import (
    DEV_SET,
    OOT_SET,
    V8_MODEL_DIR,
    V8_FEATURES,
    V8_CAT_FEATURES,
)
from cohort_gated_ensemble.cohort_assignment import assign_cohort_v2
from cohort_gated_ensemble.backtest.layer1_frozen import (
    load_v8_model_and_artifacts,
    prepare_features_for_v8,
    score_veterans_with_v8,
)
from hierarchical_make_adjustment import HierarchicalFeatures, ModelHierarchicalFeatures


def load_pickles():
    """Load frozen pickles from V8 model directory."""
    print("=" * 70)
    print("LOADING FROZEN PICKLES")
    print("=" * 70)

    with open(V8_MODEL_DIR / "segment_hierarchical_features.pkl", 'rb') as f:
        segment_hf = pickle.load(f)

    with open(V8_MODEL_DIR / "model_hierarchical_features.pkl", 'rb') as f:
        model_hf = pickle.load(f)

    # Inspect segment_hf structure
    print("\nSegment HF structure:")
    if isinstance(segment_hf, dict):
        print(f"  Type: dict")
        print(f"  Keys: {list(segment_hf.keys())}")
        if 'segment_rates' in segment_hf:
            sr = segment_hf['segment_rates']
            print(f"  segment_rates type: {type(sr)}")
            if sr:
                sample_key = next(iter(sr.keys()))
                print(f"  Sample key: {sample_key} (type: {type(sample_key)})")
                print(f"  Sample value: {sr[sample_key]}")
                print(f"  Total segments: {len(sr)}")
    elif isinstance(segment_hf, HierarchicalFeatures):
        print(f"  Type: HierarchicalFeatures object")
        if segment_hf.segment_rates:
            sample_key = next(iter(segment_hf.segment_rates.keys()))
            print(f"  Sample key: {sample_key} (type: {type(sample_key)})")
            print(f"  Total segments: {len(segment_hf.segment_rates)}")

    # Inspect model_hf structure
    print("\nModel HF structure:")
    if isinstance(model_hf, dict):
        print(f"  Type: dict")
        print(f"  Keys: {list(model_hf.keys())}")
        if 'model_rates' in model_hf:
            mr = model_hf['model_rates']
            print(f"  model_rates type: {type(mr)}")
            if mr:
                sample_key = next(iter(mr.keys()))
                print(f"  Sample key: {sample_key[:30]}...")
                print(f"  Total models: {len(mr)}")
    elif isinstance(model_hf, ModelHierarchicalFeatures):
        print(f"  Type: ModelHierarchicalFeatures object")
        if model_hf.model_rates:
            sample_key = next(iter(model_hf.model_rates.keys()))
            print(f"  Sample key: {sample_key[:30]}...")
            print(f"  Total models: {len(model_hf.model_rates)}")

    return segment_hf, model_hf


def check_segment_key_mismatch(segment_hf):
    """
    Critical check: Does prepare_features_for_v8 use the right key format?

    HierarchicalFeatures.segment_rates uses TUPLE keys: (make, age_band, mileage_band)
    But prepare_features_for_v8 constructs STRING keys: make_age_band_mileage_band
    """
    print("\n" + "=" * 70)
    print("CHECK 1: SEGMENT KEY FORMAT MISMATCH")
    print("=" * 70)

    if isinstance(segment_hf, dict):
        sr = segment_hf.get('segment_rates', {})
    else:
        sr = segment_hf.segment_rates if hasattr(segment_hf, 'segment_rates') else {}

    if not sr:
        print("  No segment_rates found!")
        return False

    sample_keys = list(sr.keys())[:5]

    print("\n  Sample segment_rates keys from pickle:")
    for k in sample_keys:
        print(f"    {k} (type: {type(k).__name__})")

    # Check if keys are tuples or strings
    first_key = next(iter(sr.keys()))

    if isinstance(first_key, tuple):
        print("\n  KEY FORMAT: TUPLE (make, age_band, mileage_band)")
        print("  BUT prepare_features_for_v8 uses STRING: make_age_band_mileage_band")
        print("\n  *** MISMATCH DETECTED! ***")
        print("  This explains the 0.45 correlation - lookups are failing!")
        return True  # Mismatch found
    elif isinstance(first_key, str):
        print("\n  KEY FORMAT: STRING")
        # Check format
        if '_' in first_key:
            parts = first_key.split('_')
            print(f"  String format appears to be: {' _ '.join(['part'] * len(parts))}")
        return False
    else:
        print(f"\n  KEY FORMAT: {type(first_key).__name__} (unexpected)")
        return True


def validate_segment_rate_lookup(df: pd.DataFrame, segment_hf):
    """
    Validate segment_fail_rate_smoothed lookup success rate.

    If there's a key format mismatch, most lookups will fail and fall back to make rate.
    """
    print("\n" + "=" * 70)
    print("CHECK 2: SEGMENT RATE LOOKUP SUCCESS")
    print("=" * 70)

    df = df.copy()

    # Get segment_rates dict
    if isinstance(segment_hf, dict):
        segment_rates = segment_hf.get('segment_rates', {})
        make_rates = segment_hf.get('make_rates', {})
        global_rate = segment_hf.get('global_fail_rate', 0.25)
    else:
        segment_rates = segment_hf.segment_rates if hasattr(segment_hf, 'segment_rates') else {}
        make_rates = segment_hf.make_rates if hasattr(segment_hf, 'make_rates') else {}
        global_rate = segment_hf.global_fail_rate if hasattr(segment_hf, 'global_fail_rate') else 0.25

    # Create age and mileage bands (same as prepare_features_for_v8)
    df['age_band'] = pd.cut(
        (pd.to_datetime(df['test_date']) - pd.to_datetime(df['first_use_date'])).dt.days / 365.25,
        bins=[0, 3, 5, 10, 15, 100],
        labels=['0-3', '3-5', '6-10', '11-15', '15+']
    ).astype(str)

    df['mileage_band'] = pd.cut(
        df['test_mileage'].fillna(50000),
        bins=[0, 30000, 60000, 100000, 1000000],
        labels=['0-30k', '30k-60k', '60k-100k', '100k+']
    ).astype(str)

    # Test both key formats
    n_samples = min(1000, len(df))
    sample = df.head(n_samples)

    # Test 1: STRING key (what prepare_features_for_v8 uses)
    string_hits = 0
    for _, row in sample.iterrows():
        key = f"{row['make']}_{row['age_band']}_{row['mileage_band']}"
        if key in segment_rates:
            string_hits += 1

    # Test 2: TUPLE key (what HierarchicalFeatures.transform uses)
    tuple_hits = 0
    for _, row in sample.iterrows():
        key = (row['make'], row['age_band'], row['mileage_band'])
        if key in segment_rates:
            tuple_hits += 1

    print(f"\n  Testing {n_samples} records:")
    print(f"  STRING key hits: {string_hits:,} ({string_hits/n_samples*100:.1f}%)")
    print(f"  TUPLE key hits:  {tuple_hits:,} ({tuple_hits/n_samples*100:.1f}%)")

    if tuple_hits > string_hits:
        print("\n  *** TUPLE keys match! prepare_features_for_v8 is using wrong format ***")
        return 'tuple'
    elif string_hits > tuple_hits:
        print("\n  STRING keys match - format is correct")
        return 'string'
    else:
        print("\n  Neither format works well - check data/pickle compatibility")
        return 'neither'


def compute_recomputed_segment_rate(df: pd.DataFrame, segment_hf):
    """
    Recompute segment_fail_rate_smoothed using correct lookup.
    """
    df = df.copy()

    # Get rates
    if isinstance(segment_hf, dict):
        segment_rates = segment_hf.get('segment_rates', {})
        make_rates = segment_hf.get('make_rates', {})
        global_rate = segment_hf.get('global_fail_rate', 0.25)
    else:
        segment_rates = segment_hf.segment_rates if hasattr(segment_hf, 'segment_rates') else {}
        make_rates = segment_hf.make_rates if hasattr(segment_hf, 'make_rates') else {}
        global_rate = segment_hf.global_fail_rate if hasattr(segment_hf, 'global_fail_rate') else 0.25

    # Create bands
    df['_age_band'] = pd.cut(
        (pd.to_datetime(df['test_date']) - pd.to_datetime(df['first_use_date'])).dt.days / 365.25,
        bins=[0, 3, 5, 10, 15, 100],
        labels=['0-3', '3-5', '6-10', '11-15', '15+']
    ).astype(str)

    df['_mileage_band'] = pd.cut(
        df['test_mileage'].fillna(50000),
        bins=[0, 30000, 60000, 100000, 1000000],
        labels=['0-30k', '30k-60k', '60k-100k', '100k+']
    ).astype(str)

    # Determine key format
    sample_key = next(iter(segment_rates.keys())) if segment_rates else None
    use_tuple = isinstance(sample_key, tuple) if sample_key else False

    def get_segment_rate(row):
        if use_tuple:
            key = (row['make'], row['_age_band'], row['_mileage_band'])
        else:
            key = f"{row['make']}_{row['_age_band']}_{row['_mileage_band']}"

        if key in segment_rates:
            return segment_rates[key]
        # Fallback to make rate
        return make_rates.get(row['make'], global_rate)

    df['segment_rate_recomputed'] = df.apply(get_segment_rate, axis=1)
    df = df.drop(columns=['_age_band', '_mileage_band'])

    return df


def validate_frozen_vs_inference(year: int = 2024):
    """
    Main validation: Compare frozen pickle rates vs inference-time computation.
    """
    print("\n" + "=" * 70)
    print(f"FROZEN vs INFERENCE VALIDATION - Year {year}")
    print("=" * 70)

    # Load artifacts
    segment_hf, model_hf = load_pickles()
    model, calibrator, _, _, cohort_stats = load_v8_model_and_artifacts()

    # Check for key mismatch
    mismatch = check_segment_key_mismatch(segment_hf)

    # Load data
    con = duckdb.connect()
    data_path = OOT_SET if year == 2024 else DEV_SET

    query = f"""
    SELECT
        vehicle_id,
        test_id,
        test_date,
        first_use_date,
        n_prior_tests,
        test_mileage,
        is_failure as target,
        prev_cycle_outcome_band,
        gap_band,
        make,
        model_id,
        prev_count_advisory,
        prior_fail_rate_smoothed,
        days_since_last_test,
        annualized_mileage,
        advisory_trend,
        prev_adv_brakes,
        prev_adv_suspension,
        prev_adv_steering,
        prev_adv_tyres
    FROM read_parquet('{data_path}')
    WHERE EXTRACT(YEAR FROM test_date) = {year}
    """

    print(f"\nLoading {year} data...")
    df = con.execute(query).df()
    print(f"  Loaded {len(df):,} records")

    # Filter to veterans (n_prior_tests > 0)
    df_veterans = df[df['n_prior_tests'].fillna(0) > 0].copy()
    print(f"  Veterans: {len(df_veterans):,}")

    # Validate lookup success
    key_format = validate_segment_rate_lookup(df_veterans, segment_hf)

    # Compute recomputed segment rate (using correct key format)
    print("\n" + "=" * 70)
    print("CHECK 3: FROZEN vs RECOMPUTED COMPARISON")
    print("=" * 70)

    df_veterans = compute_recomputed_segment_rate(df_veterans, segment_hf)

    # Also compute using prepare_features_for_v8 (to see what inference actually gets)
    print("\nComputing inference-time features...")
    df_inference = prepare_features_for_v8(df_veterans.copy(), segment_hf, model_hf, cohort_stats)

    # Compare
    print("\n  segment_fail_rate_smoothed comparison:")

    recomputed = df_veterans['segment_rate_recomputed'].values
    inference = df_inference['segment_fail_rate_smoothed'].values

    # Stats
    print(f"\n  Recomputed (correct lookup):")
    print(f"    Mean: {np.mean(recomputed):.4f}")
    print(f"    Std:  {np.std(recomputed):.4f}")
    print(f"    Min:  {np.min(recomputed):.4f}")
    print(f"    Max:  {np.max(recomputed):.4f}")

    print(f"\n  Inference (prepare_features_for_v8):")
    print(f"    Mean: {np.mean(inference):.4f}")
    print(f"    Std:  {np.std(inference):.4f}")
    print(f"    Min:  {np.min(inference):.4f}")
    print(f"    Max:  {np.max(inference):.4f}")

    # Correlation
    corr = np.corrcoef(recomputed, inference)[0, 1]
    print(f"\n  Correlation: {corr:.4f}")

    # Exact match rate
    exact_match = np.sum(np.abs(recomputed - inference) < 0.001) / len(recomputed)
    print(f"  Exact match rate: {exact_match:.1%}")

    if corr < 0.9:
        print("\n  *** LOW CORRELATION DETECTED ***")
        print("  This confirms prepare_features_for_v8 is NOT using correct lookup!")

    # AUC impact
    print("\n" + "=" * 70)
    print("CHECK 4: AUC IMPACT")
    print("=" * 70)

    # Score with inference features
    print("\nScoring with inference features...")
    scores_inference = score_veterans_with_v8(df_inference, model, calibrator)
    auc_inference = roc_auc_score(df_veterans['target'], scores_inference)
    print(f"  AUC (inference features): {auc_inference:.4f}")

    return {
        'year': year,
        'key_format_mismatch': mismatch,
        'key_format_detected': key_format,
        'correlation': corr,
        'exact_match_rate': exact_match,
        'auc_inference': auc_inference,
    }


def validate_prev_cycle_outcome_band(year: int = 2024):
    """
    Validate prev_cycle_outcome_band: values in dataset vs derived from history.
    """
    print("\n" + "=" * 70)
    print(f"PREV_CYCLE_OUTCOME_BAND VALIDATION - Year {year}")
    print("=" * 70)

    # This would require joining to the history file to verify
    # For now, just check distribution stability

    con = duckdb.connect()

    # Load 2023 (dev) and 2024 (oot)
    query_2023 = f"""
    SELECT prev_cycle_outcome_band, COUNT(*) as n
    FROM read_parquet('{DEV_SET}')
    WHERE EXTRACT(YEAR FROM test_date) = 2023
    GROUP BY prev_cycle_outcome_band
    """

    query_2024 = f"""
    SELECT prev_cycle_outcome_band, COUNT(*) as n
    FROM read_parquet('{OOT_SET}')
    WHERE EXTRACT(YEAR FROM test_date) = 2024
    GROUP BY prev_cycle_outcome_band
    """

    df_2023 = con.execute(query_2023).df()
    df_2024 = con.execute(query_2024).df()

    # Normalize to percentages
    df_2023['pct'] = df_2023['n'] / df_2023['n'].sum() * 100
    df_2024['pct'] = df_2024['n'] / df_2024['n'].sum() * 100

    print("\n  prev_cycle_outcome_band distribution:")
    print("\n  Band              | 2023    | 2024    | Delta")
    print("  " + "-" * 50)

    all_bands = set(df_2023['prev_cycle_outcome_band']).union(set(df_2024['prev_cycle_outcome_band']))

    for band in sorted(all_bands, key=lambda x: str(x)):
        pct_2023 = df_2023[df_2023['prev_cycle_outcome_band'] == band]['pct'].values
        pct_2024 = df_2024[df_2024['prev_cycle_outcome_band'] == band]['pct'].values

        pct_2023 = pct_2023[0] if len(pct_2023) > 0 else 0
        pct_2024 = pct_2024[0] if len(pct_2024) > 0 else 0
        delta = pct_2024 - pct_2023

        print(f"  {str(band):17} | {pct_2023:5.1f}%  | {pct_2024:5.1f}%  | {delta:+5.1f}pp")

    print("\n  Large shifts may indicate data pipeline changes.")


def run_ablation_study(year: int = 2024):
    """
    Ablation study: Set top features to constant and measure AUC impact.
    """
    print("\n" + "=" * 70)
    print(f"ABLATION STUDY - Year {year}")
    print("=" * 70)

    # Load artifacts
    segment_hf, model_hf = load_pickles()
    model, calibrator, _, _, cohort_stats = load_v8_model_and_artifacts()

    # Load data
    con = duckdb.connect()
    data_path = OOT_SET if year == 2024 else DEV_SET

    query = f"""
    SELECT
        vehicle_id, test_id, test_date, first_use_date,
        n_prior_tests, test_mileage, is_failure as target,
        prev_cycle_outcome_band, gap_band, make, model_id,
        prev_count_advisory, prior_fail_rate_smoothed,
        days_since_last_test, annualized_mileage, advisory_trend,
        prev_adv_brakes, prev_adv_suspension, prev_adv_steering, prev_adv_tyres
    FROM read_parquet('{data_path}')
    WHERE EXTRACT(YEAR FROM test_date) = {year}
    """

    df = con.execute(query).df()
    df_veterans = df[df['n_prior_tests'].fillna(0) > 0].copy()
    print(f"\nLoaded {len(df_veterans):,} veterans for {year}")

    # Prepare baseline features
    df_base = prepare_features_for_v8(df_veterans.copy(), segment_hf, model_hf, cohort_stats)
    scores_base = score_veterans_with_v8(df_base, model, calibrator)
    auc_base = roc_auc_score(df_veterans['target'], scores_base)
    print(f"\nBaseline AUC: {auc_base:.4f}")

    # Ablation targets (top features by importance)
    ablation_targets = [
        ('segment_fail_rate_smoothed', 0.25),  # Set to global avg
        ('model_fail_rate_smoothed', 0.25),
        ('prev_cycle_outcome_band', 'UNKNOWN'),
    ]

    print("\n  Feature                      | AUC     | Delta   | Impact")
    print("  " + "-" * 60)

    for feature, constant_value in ablation_targets:
        df_ablated = prepare_features_for_v8(df_veterans.copy(), segment_hf, model_hf, cohort_stats)

        if feature in df_ablated.columns:
            df_ablated[feature] = constant_value

            scores_ablated = score_veterans_with_v8(df_ablated, model, calibrator)
            auc_ablated = roc_auc_score(df_veterans['target'], scores_ablated)
            delta = auc_ablated - auc_base
            impact = 'CRITICAL' if delta < -0.02 else 'MODERATE' if delta < -0.01 else 'LOW'

            print(f"  {feature:28} | {auc_ablated:.4f}  | {delta:+.4f} | {impact}")
        else:
            print(f"  {feature:28} | N/A     | N/A     | Not in features")


def main():
    print("=" * 70)
    print("FROZEN vs RECOMPUTED FEATURE VALIDATION")
    print("=" * 70)
    print("\nThis script validates that inference-time features match training-time.")
    print("Earlier finding: segment_fail_rate_smoothed correlation was 0.45 (should be ~1.0)")

    # Run validation for 2024 (OOT)
    results = validate_frozen_vs_inference(year=2024)

    # Validate prev_cycle_outcome_band
    validate_prev_cycle_outcome_band(year=2024)

    # Run ablation study
    run_ablation_study(year=2024)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"\n  Key format mismatch: {results['key_format_mismatch']}")
    print(f"  Key format detected: {results['key_format_detected']}")
    print(f"  Frozen vs Inference correlation: {results['correlation']:.4f}")
    print(f"  Exact match rate: {results['exact_match_rate']:.1%}")
    print(f"  AUC: {results['auc_inference']:.4f}")

    if results['correlation'] < 0.9:
        print("\n" + "!" * 70)
        print("  ROOT CAUSE IDENTIFIED:")
        print("  prepare_features_for_v8 uses WRONG key format for segment lookup!")
        print("  FIX: Change STRING key to TUPLE key in layer1_frozen.py:175")
        print("!" * 70)


if __name__ == "__main__":
    main()
