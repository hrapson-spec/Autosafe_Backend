#!/usr/bin/env python3
"""
Build Ignored Advisory Rate Feature V2
======================================

Computes "ignored advisory" by joining advisory data with vehicle history.
If vehicle had advisories at t-1 AND has advisories at t → owner ignored them.

Uses proper as-of filtering: shift(1) within vehicle groups.

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
OUTPUT_FILE = PROJECT_ROOT / "ignored_advisory_features_v2.parquet"


def main():
    print("=" * 70)
    print("BUILD IGNORED ADVISORY FEATURE V2")
    print("=" * 70)

    # =========================================================================
    # Step 1: Load dev set with vehicle_id and test_date
    # =========================================================================
    print("\n[Step 1] Loading dev set...")

    con = duckdb.connect()
    con.execute("SET memory_limit='4GB'")

    dev_df = con.execute(f"""
        SELECT
            test_id,
            vehicle_id,
            test_date
        FROM read_parquet('{DEV_SET}')
        WHERE vehicle_id IS NOT NULL
    """).fetchdf()

    print(f"  Dev set: {len(dev_df):,} tests")
    print(f"  Unique vehicles: {dev_df['vehicle_id'].nunique():,}")

    dev_test_ids = set(dev_df['test_id'].values)

    # =========================================================================
    # Step 2: Load advisory counts from advisories_summary.csv
    # =========================================================================
    print("\n[Step 2] Loading advisory data...")

    # Columns to aggregate into total advisory count
    adv_cols_to_sum = [
        'Advisory_Brakes',
        'Advisory_Tyres',
        'Advisory_Suspension',
        'Advisory_Steering',
        'Advisory_Lights',
        'Advisory_Lamps, reflectors and electrical equipment',
        'Advisory_Wheels',
        'Advisory_Body, Structure and General Items',
        'Advisory_Exhaust, Fuel and Emissions',
    ]

    chunks = []
    chunk_size = 2_000_000
    total_rows = 0
    matched_rows = 0

    for chunk in pd.read_csv(ADVISORIES_SUMMARY, chunksize=chunk_size):
        total_rows += len(chunk)

        # Filter to dev_set test_ids
        filtered = chunk[chunk['test_id'].isin(dev_test_ids)].copy()

        if len(filtered) > 0:
            # Compute total advisory count (sum of all advisory columns present)
            adv_cols_present = [c for c in adv_cols_to_sum if c in chunk.columns]
            filtered['advisory_count'] = filtered[adv_cols_present].fillna(0).sum(axis=1).astype(int)

            # Also keep component-specific counts
            for col in adv_cols_present:
                filtered[col] = filtered[col].fillna(0).astype(int)

            keep_cols = ['test_id', 'advisory_count'] + adv_cols_present
            chunks.append(filtered[keep_cols].copy())
            matched_rows += len(filtered)

        if total_rows % 20_000_000 == 0:
            print(f"    Processed {total_rows:,} rows, matched {matched_rows:,}")

    print(f"  Total advisory rows: {total_rows:,}")
    print(f"  Matched to dev set: {matched_rows:,}")

    if not chunks:
        print("  ERROR: No advisory data matched!")
        return

    adv_df = pd.concat(chunks, ignore_index=True)
    print(f"  Advisory data: {len(adv_df):,} rows")

    # =========================================================================
    # Step 3: Join advisory counts with dev_set
    # =========================================================================
    print("\n[Step 3] Joining advisory counts with dev_set...")

    merged = dev_df.merge(adv_df, on='test_id', how='left')
    merged['advisory_count'] = merged['advisory_count'].fillna(0).astype(int)

    # Fill component columns
    adv_cols_present = [c for c in adv_cols_to_sum if c in merged.columns]
    for col in adv_cols_present:
        merged[col] = merged[col].fillna(0).astype(int)

    print(f"  Merged: {len(merged):,} rows")
    print(f"  Tests with advisories: {(merged['advisory_count'] > 0).sum():,} ({(merged['advisory_count'] > 0).mean()*100:.1f}%)")

    # =========================================================================
    # Step 4: Compute prior advisory using as-of shift
    # =========================================================================
    print("\n[Step 4] Computing prior advisory counts (as-of safe)...")

    merged['test_date'] = pd.to_datetime(merged['test_date'])
    merged = merged.sort_values(['vehicle_id', 'test_date']).reset_index(drop=True)

    # Prior test advisory count (shift to exclude current)
    merged['prior_advisory_count'] = (
        merged.groupby('vehicle_id')['advisory_count']
        .transform(lambda x: x.shift(1).fillna(0))
    ).astype(int)

    # Binary: had prior advisory
    merged['has_prior_advisory'] = (merged['prior_advisory_count'] > 0).astype(int)

    # Binary: has current advisory
    merged['has_current_advisory'] = (merged['advisory_count'] > 0).astype(int)

    # IGNORED: had prior advisory AND has current advisory
    merged['has_ignored_advisory'] = (
        (merged['has_prior_advisory'] == 1) & (merged['has_current_advisory'] == 1)
    ).astype(int)

    # Coverage stats
    print(f"\n  Coverage:")
    print(f"    has_prior_advisory: {merged['has_prior_advisory'].mean()*100:.1f}%")
    print(f"    has_current_advisory: {merged['has_current_advisory'].mean()*100:.1f}%")
    print(f"    has_ignored_advisory: {merged['has_ignored_advisory'].mean()*100:.1f}%")

    # =========================================================================
    # Step 5: Compute component-level ignored flags
    # =========================================================================
    print("\n[Step 5] Computing component-level ignored flags...")

    # Map to shorter names
    component_map = {
        'brakes': 'Advisory_Brakes',
        'tyres': 'Advisory_Tyres',
        'suspension': 'Advisory_Suspension',
        'steering': 'Advisory_Steering',
        'lights': 'Advisory_Lamps, reflectors and electrical equipment',
        'body': 'Advisory_Body, Structure and General Items',
        'wheels': 'Advisory_Wheels',
        'emissions': 'Advisory_Exhaust, Fuel and Emissions',
    }

    ignored_cols = []
    for comp_name, col in component_map.items():
        if col not in merged.columns:
            continue

        # Prior component advisory
        prior_col = f'prior_adv_{comp_name}'
        merged[prior_col] = (
            merged.groupby('vehicle_id')[col]
            .transform(lambda x: x.shift(1).fillna(0))
        ).astype(int)

        # Ignored: prior > 0 AND current > 0
        ignored_col = f'ignored_{comp_name}'
        merged[ignored_col] = (
            (merged[prior_col] > 0) & (merged[col] > 0)
        ).astype(int)
        ignored_cols.append(ignored_col)

        # Stats
        prior_count = (merged[prior_col] > 0).sum()
        ignored_count = merged[ignored_col].sum()
        rate = ignored_count / prior_count * 100 if prior_count > 0 else 0
        print(f"    {comp_name:12s}: {ignored_count:>7,} ignored / {prior_count:>7,} with prior = {rate:.1f}%")

    # Total ignored component count
    merged['ignored_component_count'] = merged[ignored_cols].sum(axis=1)

    # =========================================================================
    # Step 6: Validate signal against failure outcomes
    # =========================================================================
    print("\n[Step 6] Validating signal against failure outcomes...")

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

    # Failure rates by ignored advisory
    print("\n  BOTH failure rate by has_ignored_advisory:")
    for val in [0, 1]:
        subset = merged[merged['has_ignored_advisory'] == val]
        if len(subset) > 0:
            both_rate = subset['has_both'].mean() * 100
            print(f"    has_ignored_advisory={val}: {both_rate:.2f}% (n={len(subset):,})")

    # By ignored component count
    print("\n  BOTH failure rate by ignored_component_count:")
    for count in [0, 1, 2, 3]:
        if count < 3:
            subset = merged[merged['ignored_component_count'] == count]
            label = f"count={count}"
        else:
            subset = merged[merged['ignored_component_count'] >= count]
            label = f"count>={count}"
        if len(subset) > 0:
            both_rate = subset['has_both'].mean() * 100
            print(f"    {label}: {both_rate:.2f}% (n={len(subset):,})")

    # Compute risk ratio
    if merged['has_ignored_advisory'].sum() > 0:
        base_rate = merged[merged['has_ignored_advisory'] == 0]['has_both'].mean()
        ignored_rate = merged[merged['has_ignored_advisory'] == 1]['has_both'].mean()
        risk_ratio = ignored_rate / base_rate if base_rate > 0 else 0
        print(f"\n  Risk ratio (ignored vs not): {risk_ratio:.2f}x")

    # =========================================================================
    # Step 7: Save output
    # =========================================================================
    print("\n[Step 7] Saving ignored advisory features...")

    output_cols = [
        'test_id', 'vehicle_id', 'test_date',
        'advisory_count', 'prior_advisory_count',
        'has_prior_advisory', 'has_current_advisory', 'has_ignored_advisory',
        'ignored_component_count'
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
    ignored_coverage = merged['has_ignored_advisory'].mean() * 100

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"""
  Ignored Advisory features V2 built successfully!

  Output: {OUTPUT_FILE}
  Tests with features: {len(output_df):,}

  Coverage comparison:
    Neglect Score (failure-based):    0.2% coverage
    Ignored Advisory (advisory-based): {ignored_coverage:.1f}% coverage

  Signal strength: {risk_ratio:.2f}x risk multiplier for ignored advisories

  Next: Run train_ignored_advisory_model.py to test AUC improvement
    """)
    print("=" * 70)


if __name__ == "__main__":
    main()
