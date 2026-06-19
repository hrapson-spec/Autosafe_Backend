#!/usr/bin/env python3
"""
Build Cross-System Features (Phase 3)
======================================

Computes features that capture multi-system failure patterns:
- n_systems_failed_last_test: Systems failed in previous test
- multi_system_history_flag: Ever failed >1 system in single test
- system_failure_diversity: Distinct systems ever failed (from history features)
- max_systems_single_test: Maximum systems failed in any prior single test

All features use as-of filtering (shift) to prevent leakage.

Input:
  - system_failures_9cat.parquet (from Phase 1)
  - dev_set.parquet (for vehicle_id, test_date)
  - filtered_failure_targets.parquet (for has_both)

Output:
  - cross_system_features.parquet

Created: 2026-01-09
"""

import pandas as pd
import numpy as np
import duckdb
from pathlib import Path

from system_map_v1 import SYSTEMS

# Paths
PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
SYSTEM_FAILURES = PROJECT_ROOT / "system_failures_9cat.parquet"
DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set.parquet"
TARGETS = PROJECT_ROOT / "filtered_failure_targets.parquet"
OUTPUT_FILE = PROJECT_ROOT / "cross_system_features.parquet"


def main():
    print("=" * 70)
    print("BUILD CROSS-SYSTEM FEATURES (PHASE 3)")
    print("=" * 70)

    con = duckdb.connect()
    con.execute("SET memory_limit='6GB'")

    # =========================================================================
    # Step 1: Load and join data
    # =========================================================================
    print("\n[Step 1] Loading data...")

    # Load dev set with vehicle_id and test_date
    dev_df = con.execute(f"""
        SELECT test_id, vehicle_id, test_date
        FROM read_parquet('{DEV_SET}')
        WHERE vehicle_id IS NOT NULL
    """).df()
    print(f"  Dev set: {len(dev_df):,} tests")

    # Load system failures
    sys_fail_df = con.execute(f"""
        SELECT *
        FROM read_parquet('{SYSTEM_FAILURES}')
    """).df()
    print(f"  System failures: {len(sys_fail_df):,} tests")

    # Load BOTH targets
    targets_df = con.execute(f"""
        SELECT test_id, has_systemic_failure, has_random_failure
        FROM read_parquet('{TARGETS}')
    """).df()
    targets_df['has_both'] = (
        (targets_df['has_systemic_failure'] == 1) &
        (targets_df['has_random_failure'] == 1)
    ).astype(int)
    print(f"  Targets: {len(targets_df):,} tests")

    con.close()

    # =========================================================================
    # Step 2: Merge all data
    # =========================================================================
    print("\n[Step 2] Merging data...")

    merged = dev_df.merge(sys_fail_df, on='test_id', how='left')
    merged = merged.merge(targets_df[['test_id', 'has_both']], on='test_id', how='left')

    # Fill NaN for system failures (tests that didn't fail get 0)
    fail_cols = [f'fail_{s}' for s in SYSTEMS]
    for col in fail_cols:
        if col in merged.columns:
            merged[col] = merged[col].fillna(0).astype(int)

    merged['system_failure_count'] = merged['system_failure_count'].fillna(0).astype(int)
    merged['has_both'] = merged['has_both'].fillna(0).astype(int)

    print(f"  Merged: {len(merged):,} tests")

    # =========================================================================
    # Step 3: Sort by vehicle_id, test_date
    # =========================================================================
    print("\n[Step 3] Sorting by vehicle_id, test_date...")

    merged['test_date'] = pd.to_datetime(merged['test_date'])
    merged = merged.sort_values(['vehicle_id', 'test_date']).reset_index(drop=True)

    # =========================================================================
    # Step 4: Compute cross-system features
    # =========================================================================
    print("\n[Step 4] Computing cross-system features...")

    # n_systems_failed_last_test: systems failed in previous test (shifted)
    print("  Computing n_systems_failed_last_test...")
    merged['n_systems_failed_last_test'] = (
        merged.groupby('vehicle_id')['system_failure_count']
        .transform(lambda x: x.shift(1).fillna(0))
    ).astype(int)

    # max_systems_single_test: maximum systems failed in any prior single test
    print("  Computing max_systems_single_test...")
    merged['max_systems_single_test'] = (
        merged.groupby('vehicle_id')['system_failure_count']
        .transform(lambda x: x.shift(1).expanding().max().fillna(0))
    ).astype(int)

    # multi_system_history_flag: ever failed >1 system in single test (historically)
    print("  Computing multi_system_history_flag...")
    merged['multi_system_history_flag'] = (merged['max_systems_single_test'] > 1).astype(int)

    # has_multi_system_last_test: >1 system failed in previous test
    print("  Computing has_multi_system_last_test...")
    merged['has_multi_system_last_test'] = (merged['n_systems_failed_last_test'] > 1).astype(int)

    # system_failure_diversity: count of unique systems ever failed
    # This is computed by summing "has ever failed system X" across all systems
    print("  Computing system_failure_diversity...")

    # For each system, compute whether it has ever been failed historically
    diversity_components = []
    for system in SYSTEMS:
        col = f'fail_{system}'
        if col in merged.columns:
            ever_failed_col = f'_ever_failed_{system}'
            merged[ever_failed_col] = (
                merged.groupby('vehicle_id')[col]
                .transform(lambda x: x.shift(1).cummax().fillna(0))
            ).astype(int)
            diversity_components.append(ever_failed_col)

    # Sum to get total diversity
    merged['system_failure_diversity'] = merged[diversity_components].sum(axis=1).astype(int)

    # Clean up temporary columns
    for col in diversity_components:
        del merged[col]

    # Print stats
    print(f"\n  Feature statistics:")
    print(f"    n_systems_failed_last_test mean: {merged['n_systems_failed_last_test'].mean():.3f}")
    print(f"    max_systems_single_test mean: {merged['max_systems_single_test'].mean():.3f}")
    print(f"    multi_system_history_flag: {merged['multi_system_history_flag'].sum():,} ({merged['multi_system_history_flag'].mean()*100:.2f}%)")
    print(f"    has_multi_system_last_test: {merged['has_multi_system_last_test'].sum():,} ({merged['has_multi_system_last_test'].mean()*100:.2f}%)")
    print(f"    system_failure_diversity mean: {merged['system_failure_diversity'].mean():.3f}")

    # Distribution of n_systems_failed_last_test
    print(f"\n  n_systems_failed_last_test distribution:")
    for n in range(5):
        count = (merged['n_systems_failed_last_test'] == n).sum()
        pct = count / len(merged) * 100
        print(f"    {n}: {count:>10,} ({pct:>5.2f}%)")

    # =========================================================================
    # Step 5: Validate signal against has_both
    # =========================================================================
    print("\n[Step 5] Validating signal against has_both target...")

    feature_cols = [
        'n_systems_failed_last_test',
        'max_systems_single_test',
        'multi_system_history_flag',
        'has_multi_system_last_test',
        'system_failure_diversity'
    ]

    for col in feature_cols:
        corr = merged[col].corr(merged['has_both'])
        print(f"  Correlation {col} vs has_both: {corr:.4f}")

    # Risk ratio for multi_system_history_flag
    print("\n  Risk analysis for multi_system_history_flag:")
    if merged['multi_system_history_flag'].sum() > 0:
        no_multi = merged[merged['multi_system_history_flag'] == 0]['has_both'].mean()
        with_multi = merged[merged['multi_system_history_flag'] == 1]['has_both'].mean()
        risk_ratio = with_multi / no_multi if no_multi > 0 else 0
        print(f"    Without multi-system history: {no_multi*100:.2f}% BOTH rate")
        print(f"    With multi-system history: {with_multi*100:.2f}% BOTH rate")
        print(f"    Risk ratio: {risk_ratio:.2f}x")

    # Risk by n_systems_failed_last_test
    print("\n  BOTH rate by n_systems_failed_last_test:")
    for n in range(4):
        subset = merged[merged['n_systems_failed_last_test'] == n]
        if len(subset) > 0:
            rate = subset['has_both'].mean() * 100
            print(f"    {n} systems: {rate:.2f}% (n={len(subset):,})")

    # =========================================================================
    # Step 6: Save output
    # =========================================================================
    print("\n[Step 6] Saving cross-system features...")

    output_cols = [
        'test_id', 'vehicle_id', 'test_date',
        'n_systems_failed_last_test',
        'max_systems_single_test',
        'multi_system_history_flag',
        'has_multi_system_last_test',
        'system_failure_diversity',
        'has_both'
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
  Cross-System features built successfully!

  Output: {OUTPUT_FILE}
  Tests: {len(output_df):,}

  Key features:
    n_systems_failed_last_test mean: {output_df['n_systems_failed_last_test'].mean():.3f}
    multi_system_history_flag: {output_df['multi_system_history_flag'].sum():,} ({output_df['multi_system_history_flag'].mean()*100:.2f}%)
    system_failure_diversity mean: {output_df['system_failure_diversity'].mean():.3f}

  Next: Run build_system_persistence_features.py
    """)
    print("=" * 70)


if __name__ == "__main__":
    main()
