#!/usr/bin/env python3
"""
Build Ignored Advisory Rate Feature
====================================

Computes the rate at which vehicle owners ignore advisories.
If a car had an advisory at t-1 and the same issue persists at t → ignored.

This expands behavioral signal coverage from 0.2% (failure-based) to ~40%+ of fleet.

Output: ignored_advisory_features.parquet with:
- ignored_{component}: Binary flag per component
- ignored_advisory_count: Total ignored advisories across components
- ignored_advisory_rate: ignored_count / total_prev_advisories

Created: 2026-01-08
"""

import pandas as pd
import numpy as np
import duckdb
from pathlib import Path

# Paths
PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set.parquet"
ADVISORIES_SUMMARY = PROJECT_ROOT / "advisories_summary.csv"
TARGETS = PROJECT_ROOT / "filtered_failure_targets.parquet"
OUTPUT_FILE = PROJECT_ROOT / "ignored_advisory_features.parquet"

# Component mapping: prev_adv_* → Advisory_* columns
COMPONENT_MAP = {
    'brakes': 'Advisory_Brakes',
    'tyres': 'Advisory_Tyres',
    'suspension': 'Advisory_Suspension',
    'steering': 'Advisory_Steering',
    'lights': 'Advisory_Lamps, reflectors and electrical equipment',
    'body': 'Advisory_Body, Structure and General Items',
    'emissions': 'Advisory_Exhaust, Fuel and Emissions',
    'wheels': 'Advisory_Wheels',
}


def main():
    print("=" * 70)
    print("BUILD IGNORED ADVISORY RATE FEATURE")
    print("=" * 70)

    # =========================================================================
    # Step 1: Load dev set with prev_adv_* features
    # =========================================================================
    print("\n[Step 1] Loading dev set...")

    con = duckdb.connect()
    con.execute("SET memory_limit='4GB'")

    prev_adv_cols = [f'prev_adv_{c}' for c in COMPONENT_MAP.keys()]
    prev_adv_select = ', '.join(prev_adv_cols)

    dev_df = con.execute(f"""
        SELECT
            test_id,
            vehicle_id,
            test_date,
            prev_advisory_count_numeric,
            {prev_adv_select}
        FROM read_parquet('{DEV_SET}')
    """).fetchdf()

    print(f"  Dev set: {len(dev_df):,} tests")
    print(f"  Columns: {list(dev_df.columns)}")

    # Check coverage of prev_adv features
    has_any_prev = (dev_df[prev_adv_cols].sum(axis=1) > 0).mean() * 100
    print(f"  Tests with any prev_adv: {has_any_prev:.1f}%")

    # =========================================================================
    # Step 2: Load current advisory counts
    # =========================================================================
    print("\n[Step 2] Loading current advisory counts...")

    # Read advisories_summary in chunks (it's 197M+ rows)
    # Only keep test_ids that are in dev_set
    dev_test_ids = set(dev_df['test_id'].values)

    curr_adv_cols = list(COMPONENT_MAP.values())
    chunks = []
    chunk_size = 1_000_000

    total_rows = 0
    matched_rows = 0

    for chunk in pd.read_csv(ADVISORIES_SUMMARY, chunksize=chunk_size):
        total_rows += len(chunk)
        # Filter to dev_set test_ids
        filtered = chunk[chunk['test_id'].isin(dev_test_ids)]
        if len(filtered) > 0:
            # Keep only needed columns
            keep_cols = ['test_id'] + [c for c in curr_adv_cols if c in chunk.columns]
            chunks.append(filtered[keep_cols].copy())
            matched_rows += len(filtered)

        if total_rows % 10_000_000 == 0:
            print(f"    Processed {total_rows:,} rows, matched {matched_rows:,}")

    print(f"  Total advisory rows: {total_rows:,}")
    print(f"  Matched to dev set: {matched_rows:,}")

    if not chunks:
        print("  ERROR: No advisory data matched!")
        return

    adv_df = pd.concat(chunks, ignore_index=True)
    print(f"  Advisory data loaded: {len(adv_df):,} rows")

    # =========================================================================
    # Step 3: Merge and compute ignored flags
    # =========================================================================
    print("\n[Step 3] Computing ignored advisory flags...")

    merged = dev_df.merge(adv_df, on='test_id', how='left')
    print(f"  Merged: {len(merged):,} rows")

    # Fill NaN advisory counts with 0
    for col in curr_adv_cols:
        if col in merged.columns:
            merged[col] = merged[col].fillna(0).astype(int)

    # Compute per-component ignored flags
    # ignored_{component} = 1 if prev_adv_{component} > 0 AND curr_adv_{component} > 0
    ignored_cols = []
    for component, curr_col in COMPONENT_MAP.items():
        prev_col = f'prev_adv_{component}'
        ignored_col = f'ignored_{component}'

        if curr_col in merged.columns:
            merged[ignored_col] = (
                (merged[prev_col] > 0) & (merged[curr_col] > 0)
            ).astype(int)
            ignored_cols.append(ignored_col)

            # Stats
            ignored_count = merged[ignored_col].sum()
            prev_count = (merged[prev_col] > 0).sum()
            ignore_rate = ignored_count / prev_count * 100 if prev_count > 0 else 0
            print(f"    {component:12s}: {ignored_count:>8,} ignored / {prev_count:>8,} with prior = {ignore_rate:.1f}%")

    # =========================================================================
    # Step 4: Aggregate to ignored_advisory_count and rate
    # =========================================================================
    print("\n[Step 4] Aggregating ignored advisory features...")

    # Total ignored across components
    merged['ignored_advisory_count'] = merged[ignored_cols].sum(axis=1)

    # Total prev advisories (sum of all prev_adv_* columns)
    merged['total_prev_advisories'] = merged[prev_adv_cols].sum(axis=1)

    # Rate: ignored / total_prev (handle division by zero)
    merged['ignored_advisory_rate'] = np.where(
        merged['total_prev_advisories'] > 0,
        merged['ignored_advisory_count'] / merged['total_prev_advisories'],
        0
    )

    # Binary: any ignored advisory
    merged['has_ignored_advisory'] = (merged['ignored_advisory_count'] > 0).astype(int)

    # Coverage stats
    has_any_prev = (merged['total_prev_advisories'] > 0).mean() * 100
    has_ignored = merged['has_ignored_advisory'].mean() * 100

    print(f"\n  Coverage:")
    print(f"    Tests with any prev advisory: {has_any_prev:.1f}%")
    print(f"    Tests with ignored advisory: {has_ignored:.1f}%")

    print(f"\n  Ignored advisory count distribution:")
    for count in [0, 1, 2, 3]:
        if count < 3:
            subset = merged[merged['ignored_advisory_count'] == count]
            label = f"count={count}"
        else:
            subset = merged[merged['ignored_advisory_count'] >= count]
            label = f"count>={count}"
        print(f"    {label}: {len(subset):,} ({len(subset)/len(merged)*100:.1f}%)")

    print(f"\n  Ignored advisory rate distribution:")
    print(f"    Mean: {merged['ignored_advisory_rate'].mean():.3f}")
    print(f"    Median: {merged['ignored_advisory_rate'].median():.3f}")
    print(f"    Max: {merged['ignored_advisory_rate'].max():.3f}")

    # =========================================================================
    # Step 5: Join failure targets to validate signal
    # =========================================================================
    print("\n[Step 5] Validating signal against failure outcomes...")

    targets_df = con.execute(f"""
        SELECT
            test_id,
            has_systemic_failure,
            has_random_failure
        FROM read_parquet('{TARGETS}')
    """).fetchdf()

    merged = merged.merge(targets_df, on='test_id', how='left')
    merged['has_systemic_failure'] = merged['has_systemic_failure'].fillna(0).astype(int)
    merged['has_random_failure'] = merged['has_random_failure'].fillna(0).astype(int)
    merged['has_both'] = ((merged['has_systemic_failure'] == 1) & (merged['has_random_failure'] == 1)).astype(int)

    con.close()

    # Failure rates by ignored advisory status
    print("\n  BOTH failure rate by has_ignored_advisory:")
    for val in [0, 1]:
        subset = merged[merged['has_ignored_advisory'] == val]
        if len(subset) > 0:
            both_rate = subset['has_both'].mean() * 100
            print(f"    has_ignored_advisory={val}: {both_rate:.2f}% (n={len(subset):,})")

    # By ignored count
    print("\n  BOTH failure rate by ignored_advisory_count:")
    for count in [0, 1, 2, 3]:
        if count < 3:
            subset = merged[merged['ignored_advisory_count'] == count]
            label = f"count={count}"
        else:
            subset = merged[merged['ignored_advisory_count'] >= count]
            label = f"count>={count}"
        if len(subset) > 0:
            both_rate = subset['has_both'].mean() * 100
            print(f"    {label}: {both_rate:.2f}% (n={len(subset):,})")

    # =========================================================================
    # Step 6: Save output
    # =========================================================================
    print("\n[Step 6] Saving ignored advisory features...")

    output_cols = [
        'test_id', 'vehicle_id', 'test_date',
        'total_prev_advisories',
        'ignored_advisory_count', 'ignored_advisory_rate', 'has_ignored_advisory',
    ] + ignored_cols + [
        'has_systemic_failure', 'has_random_failure', 'has_both'
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
  Ignored Advisory features built successfully!

  Output: {OUTPUT_FILE}
  Tests with features: {len(output_df):,}

  Coverage comparison:
    Neglect Score (failure-based):  0.2% coverage
    Ignored Advisory (advisory-based): {has_ignored:.1f}% coverage

  Key insight: This behavioral signal now covers {has_ignored:.1f}% of tests,
  enabling contribution to global model predictions.

  Next: Run train_ignored_advisory_model.py to test AUC improvement
    """)
    print("=" * 70)


if __name__ == "__main__":
    main()
