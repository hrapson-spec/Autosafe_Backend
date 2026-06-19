#!/usr/bin/env python3
"""
Build Ghost Vehicle Detection Feature
======================================

Identifies vehicles that skip MOT cycles (>14 months between tests).
Hypothesis: Ghost vehicles = truly neglected → higher Systemic/BOTH failure risk.

Thresholds:
- is_ghost: >420 days (skipped annual cycle)
- is_long_dormant: >1460 days (4+ year gap)

Created: 2026-01-09
"""

import pandas as pd
import numpy as np
import duckdb
from pathlib import Path

# Paths
PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set.parquet"
TARGETS = PROJECT_ROOT / "filtered_failure_targets.parquet"
OUTPUT_FILE = PROJECT_ROOT / "ghost_vehicle_features.parquet"

# Thresholds
GHOST_THRESHOLD = 420  # 14 months (skipped annual cycle)
LONG_DORMANT_THRESHOLD = 1460  # 4 years (true ghost)


def main():
    print("=" * 70)
    print("BUILD GHOST VEHICLE DETECTION FEATURE")
    print("=" * 70)

    # =========================================================================
    # Step 1: Load dev set with gap features
    # =========================================================================
    print("\n[Step 1] Loading dev set...")

    con = duckdb.connect()
    con.execute("SET memory_limit='4GB'")

    dev_df = con.execute(f"""
        SELECT
            test_id,
            vehicle_id,
            test_date,
            days_since_last_test,
            gap_band,
            is_overdue
        FROM read_parquet('{DEV_SET}')
    """).fetchdf()

    print(f"  Dev set: {len(dev_df):,} tests")

    # =========================================================================
    # Step 2: Compute ghost vehicle features
    # =========================================================================
    print("\n[Step 2] Computing ghost vehicle features...")

    # Handle missing days_since_last_test (first-time tests)
    dev_df['days_since_last_test'] = dev_df['days_since_last_test'].fillna(365)

    # Ghost detection
    dev_df['is_ghost'] = (dev_df['days_since_last_test'] > GHOST_THRESHOLD).astype(int)
    dev_df['is_long_dormant'] = (dev_df['days_since_last_test'] > LONG_DORMANT_THRESHOLD).astype(int)
    dev_df['days_late'] = dev_df['days_since_last_test'] - 365

    # Coverage stats
    ghost_pct = dev_df['is_ghost'].mean() * 100
    dormant_pct = dev_df['is_long_dormant'].mean() * 100

    print(f"\n  Coverage:")
    print(f"    is_ghost (>420 days): {dev_df['is_ghost'].sum():,} ({ghost_pct:.1f}%)")
    print(f"    is_long_dormant (>1460 days): {dev_df['is_long_dormant'].sum():,} ({dormant_pct:.1f}%)")

    # Distribution of days_since_last_test
    print(f"\n  days_since_last_test distribution:")
    for band in ['0-30', '30-60', '60-90', 'NONE']:
        count = (dev_df['gap_band'] == band).sum()
        print(f"    gap_band={band}: {count:,} ({count/len(dev_df)*100:.1f}%)")

    print(f"\n  days_late percentiles:")
    for p in [25, 50, 75, 90, 95, 99]:
        val = dev_df['days_late'].quantile(p/100)
        print(f"    {p}th percentile: {val:.0f} days")

    # =========================================================================
    # Step 3: Join with failure targets
    # =========================================================================
    print("\n[Step 3] Validating signal against failure outcomes...")

    targets_df = con.execute(f"""
        SELECT
            test_id,
            has_systemic_failure,
            has_random_failure
        FROM read_parquet('{TARGETS}')
    """).fetchdf()

    dev_df = dev_df.merge(targets_df, on='test_id', how='left')
    dev_df['has_systemic_failure'] = dev_df['has_systemic_failure'].fillna(0).astype(int)
    dev_df['has_random_failure'] = dev_df['has_random_failure'].fillna(0).astype(int)
    dev_df['has_both'] = ((dev_df['has_systemic_failure'] == 1) & (dev_df['has_random_failure'] == 1)).astype(int)

    # Also get is_failure from dev_set
    failure_df = con.execute(f"""
        SELECT test_id, is_failure
        FROM read_parquet('{DEV_SET}')
    """).fetchdf()
    dev_df = dev_df.merge(failure_df[['test_id', 'is_failure']], on='test_id', how='left')

    con.close()

    # =========================================================================
    # Step 4: Signal analysis
    # =========================================================================
    print("\n[Step 4] Signal analysis...")

    # BOTH failure rate by ghost status
    print("\n  BOTH failure rate by is_ghost:")
    for val in [0, 1]:
        subset = dev_df[dev_df['is_ghost'] == val]
        if len(subset) > 0:
            rate = subset['has_both'].mean() * 100
            print(f"    is_ghost={val}: {rate:.2f}% (n={len(subset):,})")

    # Risk ratio for ghost
    non_ghost_rate = dev_df[dev_df['is_ghost'] == 0]['has_both'].mean()
    ghost_rate = dev_df[dev_df['is_ghost'] == 1]['has_both'].mean()
    ghost_risk_ratio = ghost_rate / non_ghost_rate if non_ghost_rate > 0 else 0
    print(f"\n  Ghost risk ratio (BOTH): {ghost_risk_ratio:.2f}x")

    # SYSTEMIC failure rate by ghost status
    print("\n  SYSTEMIC failure rate by is_ghost:")
    for val in [0, 1]:
        subset = dev_df[dev_df['is_ghost'] == val]
        if len(subset) > 0:
            rate = subset['has_systemic_failure'].mean() * 100
            print(f"    is_ghost={val}: {rate:.2f}% (n={len(subset):,})")

    systemic_non_ghost = dev_df[dev_df['is_ghost'] == 0]['has_systemic_failure'].mean()
    systemic_ghost = dev_df[dev_df['is_ghost'] == 1]['has_systemic_failure'].mean()
    systemic_risk_ratio = systemic_ghost / systemic_non_ghost if systemic_non_ghost > 0 else 0
    print(f"\n  Ghost risk ratio (SYSTEMIC): {systemic_risk_ratio:.2f}x")

    # is_failure rate by ghost status
    print("\n  is_failure rate by is_ghost:")
    for val in [0, 1]:
        subset = dev_df[dev_df['is_ghost'] == val]
        if len(subset) > 0:
            rate = subset['is_failure'].mean() * 100
            print(f"    is_ghost={val}: {rate:.2f}% (n={len(subset):,})")

    failure_non_ghost = dev_df[dev_df['is_ghost'] == 0]['is_failure'].mean()
    failure_ghost = dev_df[dev_df['is_ghost'] == 1]['is_failure'].mean()
    failure_risk_ratio = failure_ghost / failure_non_ghost if failure_non_ghost > 0 else 0
    print(f"\n  Ghost risk ratio (is_failure): {failure_risk_ratio:.2f}x")

    # Long dormant analysis
    print("\n  BOTH failure rate by is_long_dormant:")
    for val in [0, 1]:
        subset = dev_df[dev_df['is_long_dormant'] == val]
        if len(subset) > 0:
            rate = subset['has_both'].mean() * 100
            print(f"    is_long_dormant={val}: {rate:.2f}% (n={len(subset):,})")

    # =========================================================================
    # Step 5: Save output
    # =========================================================================
    print("\n[Step 5] Saving ghost vehicle features...")

    output_cols = [
        'test_id', 'vehicle_id', 'test_date',
        'days_since_last_test', 'days_late', 'gap_band',
        'is_ghost', 'is_long_dormant', 'is_overdue',
        'has_systemic_failure', 'has_random_failure', 'has_both', 'is_failure'
    ]

    output_df = dev_df[output_cols].copy()
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
  Ghost Vehicle features built successfully!

  Output: {OUTPUT_FILE}
  Tests with features: {len(output_df):,}

  Coverage:
    is_ghost (>420 days): {ghost_pct:.1f}%
    is_long_dormant (>1460 days): {dormant_pct:.1f}%

  Signal Strength:
    Ghost risk ratio (BOTH): {ghost_risk_ratio:.2f}x
    Ghost risk ratio (SYSTEMIC): {systemic_risk_ratio:.2f}x
    Ghost risk ratio (is_failure): {failure_risk_ratio:.2f}x

  Interpretation: {"POSITIVE signal - ghosts have HIGHER risk" if ghost_risk_ratio > 1 else "INVERTED signal - ghosts have LOWER risk"}

  Next: Integrate is_ghost into Both-Segment model training
    """)
    print("=" * 70)


if __name__ == "__main__":
    main()
