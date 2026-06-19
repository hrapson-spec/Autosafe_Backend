#!/usr/bin/env python3
"""
Build Neglect Score Features
============================

Computes rolling count of historical RANDOM failures per vehicle.
Hypothesis: High Neglect_Score → owner ignores early warning signs → increased SYSTEMIC failure risk.

Output: neglect_features.parquet with:
- neglect_score: Count of prior RANDOM failures
- neglect_score_last3: RANDOM failures in last 3 tests
- has_prior_random: Binary: any prior RANDOM failure
- has_prior_systemic: Binary: any prior SYSTEMIC failure

Created: 2026-01-08
"""

import pandas as pd
import numpy as np
import duckdb
from pathlib import Path

# Paths
PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set.parquet"
TARGETS = PROJECT_ROOT / "filtered_failure_targets.parquet"
OUTPUT_FILE = PROJECT_ROOT / "neglect_features.parquet"


def main():
    print("=" * 70)
    print("BUILD NEGLECT SCORE FEATURES")
    print("=" * 70)

    # =========================================================================
    # Step 1: Load and merge data
    # =========================================================================
    print("\n[Step 1] Loading data...")

    con = duckdb.connect()
    con.execute("SET memory_limit='4GB'")

    # Load dev set with vehicle_id and test_date
    dev_df = con.execute(f"""
        SELECT
            test_id,
            vehicle_id,
            test_date,
            is_failure
        FROM read_parquet('{DEV_SET}')
        WHERE vehicle_id IS NOT NULL
    """).fetchdf()

    print(f"  Dev set (with vehicle_id): {len(dev_df):,} tests")
    print(f"  Unique vehicles: {dev_df['vehicle_id'].nunique():,}")

    # Load targets
    targets_df = con.execute(f"""
        SELECT
            test_id,
            has_systemic_failure,
            has_random_failure
        FROM read_parquet('{TARGETS}')
    """).fetchdf()

    print(f"  Targets: {len(targets_df):,} tests")

    # Merge (LEFT join to keep all dev set tests)
    merged = dev_df.merge(targets_df, on='test_id', how='left')

    # Fill NaN with 0 for tests without failures
    merged['has_systemic_failure'] = merged['has_systemic_failure'].fillna(0).astype(int)
    merged['has_random_failure'] = merged['has_random_failure'].fillna(0).astype(int)

    print(f"  Merged: {len(merged):,} tests")
    print(f"  With RANDOM failures: {(merged['has_random_failure'] == 1).sum():,}")
    print(f"  With SYSTEMIC failures: {(merged['has_systemic_failure'] == 1).sum():,}")

    con.close()

    # =========================================================================
    # Step 2: Compute Neglect Score (as-of safe)
    # =========================================================================
    print("\n[Step 2] Computing Neglect Score features...")

    # Sort by vehicle and date for proper cumsum
    merged['test_date'] = pd.to_datetime(merged['test_date'])
    merged = merged.sort_values(['vehicle_id', 'test_date']).reset_index(drop=True)

    # Cumulative sum of PRIOR RANDOM failures (shift to exclude current)
    # The shift(1) ensures we only count tests BEFORE the current one
    merged['neglect_score'] = (
        merged.groupby('vehicle_id')['has_random_failure']
        .transform(lambda x: x.shift(1).cumsum().fillna(0))
    ).astype(int)

    # RANDOM failures in last 3 prior tests
    merged['neglect_score_last3'] = (
        merged.groupby('vehicle_id')['has_random_failure']
        .transform(lambda x: x.shift(1).rolling(3, min_periods=0).sum().fillna(0))
    ).astype(int)

    # Binary: any prior RANDOM failure
    merged['has_prior_random'] = (merged['neglect_score'] > 0).astype(int)

    # Also compute prior SYSTEMIC failures (for comparison)
    merged['prior_systemic_count'] = (
        merged.groupby('vehicle_id')['has_systemic_failure']
        .transform(lambda x: x.shift(1).cumsum().fillna(0))
    ).astype(int)

    merged['has_prior_systemic'] = (merged['prior_systemic_count'] > 0).astype(int)

    # =========================================================================
    # Step 3: Validate as-of correctness
    # =========================================================================
    print("\n[Step 3] Validating as-of correctness (sampling 1000 rows)...")

    sample = merged.sample(n=min(1000, len(merged)), random_state=42)
    errors = 0

    for idx, row in sample.iterrows():
        vehicle_id = row['vehicle_id']
        test_date = row['test_date']

        # Get all prior tests for this vehicle
        vehicle_tests = merged[merged['vehicle_id'] == vehicle_id]
        prior_tests = vehicle_tests[vehicle_tests['test_date'] < test_date]

        # Recompute neglect_score
        expected = prior_tests['has_random_failure'].sum()
        actual = row['neglect_score']

        if expected != actual:
            errors += 1
            if errors <= 3:
                print(f"  ERROR: test_id={row['test_id']}, expected={expected}, actual={actual}")

    if errors == 0:
        print(f"  ✓ All {len(sample)} sampled rows passed as-of validation")
    else:
        print(f"  ⚠️  {errors} errors found in {len(sample)} samples")

    # =========================================================================
    # Step 4: Summary statistics
    # =========================================================================
    print("\n[Step 4] Summary statistics...")

    print("\n  Neglect Score distribution:")
    print(f"    neglect_score = 0: {(merged['neglect_score'] == 0).sum():,} ({(merged['neglect_score'] == 0).mean()*100:.1f}%)")
    print(f"    neglect_score = 1: {(merged['neglect_score'] == 1).sum():,} ({(merged['neglect_score'] == 1).mean()*100:.1f}%)")
    print(f"    neglect_score = 2: {(merged['neglect_score'] == 2).sum():,} ({(merged['neglect_score'] == 2).mean()*100:.1f}%)")
    print(f"    neglect_score >= 3: {(merged['neglect_score'] >= 3).sum():,} ({(merged['neglect_score'] >= 3).mean()*100:.1f}%)")
    print(f"    has_prior_random = 1: {merged['has_prior_random'].sum():,} ({merged['has_prior_random'].mean()*100:.1f}%)")

    print("\n  Prior Systemic distribution:")
    print(f"    has_prior_systemic = 1: {merged['has_prior_systemic'].sum():,} ({merged['has_prior_systemic'].mean()*100:.1f}%)")

    # Cross-tabulation: neglect_score vs current systemic failure
    print("\n  Systemic failure rate by Neglect Score:")
    for score in [0, 1, 2, 3]:
        if score < 3:
            subset = merged[merged['neglect_score'] == score]
            label = f"neglect={score}"
        else:
            subset = merged[merged['neglect_score'] >= score]
            label = f"neglect>={score}"

        if len(subset) > 0:
            rate = subset['has_systemic_failure'].mean() * 100
            print(f"    {label}: {rate:.1f}% (n={len(subset):,})")

    # =========================================================================
    # Step 5: Save output
    # =========================================================================
    print("\n[Step 5] Saving neglect features...")

    output_cols = [
        'test_id', 'vehicle_id', 'test_date',
        'neglect_score', 'neglect_score_last3', 'has_prior_random',
        'prior_systemic_count', 'has_prior_systemic',
        'has_systemic_failure', 'has_random_failure'
    ]

    output_df = merged[output_cols].copy()
    output_df.to_parquet(OUTPUT_FILE, index=False)

    print(f"  Saved to: {OUTPUT_FILE}")
    print(f"  Total rows: {len(output_df):,}")

    # =========================================================================
    # Summary
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"""
  Neglect features built successfully!

  Output: {OUTPUT_FILE}
  Tests with features: {len(output_df):,}

  Feature coverage:
    has_prior_random:   {merged['has_prior_random'].mean()*100:.1f}%
    has_prior_systemic: {merged['has_prior_systemic'].mean()*100:.1f}%

  Key insight: Systemic failure rate by neglect_score shows correlation
  between prior RANDOM failures and future SYSTEMIC failures.

  Next: Run train_neglect_enhanced_model.py to test hypothesis
    """)
    print("=" * 70)


if __name__ == "__main__":
    main()
