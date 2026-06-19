#!/usr/bin/env python3
"""
Build Component-Specific Escalation Feature
============================================

Tracks advisory → failure progression for specific components.
If prior_adv_{component} > 0 AND current fail_{component} == 1 → escalated

Consumer Output: "Your 2024 Tyre Advisory has a 70% chance of being a Failure this year."

Available mappings (3 components with failure data):
- adv_brakes → fail_brakes
- adv_tyres → fail_tyres
- adv_suspension → fail_suspension

Created: 2026-01-09
"""

import pandas as pd
import numpy as np
import duckdb
from pathlib import Path

# Paths
PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set.parquet"
ADVISORIES = PROJECT_ROOT / "advisory_totals_dedup.parquet"
FAILURES = PROJECT_ROOT / "component_failures_all_years.parquet"
OUTPUT_FILE = PROJECT_ROOT / "component_escalation_features.parquet"

# Component mapping (advisory → failure)
COMPONENT_MAP = {
    'brakes': ('adv_brakes', 'fail_brakes'),
    'tyres': ('adv_tyres', 'fail_tyres'),
    'suspension': ('adv_suspension', 'fail_suspension'),
}


def main():
    print("=" * 70)
    print("BUILD COMPONENT-SPECIFIC ESCALATION FEATURE")
    print("=" * 70)

    # =========================================================================
    # Step 1: Load all data
    # =========================================================================
    print("\n[Step 1] Loading data...")

    con = duckdb.connect()
    con.execute("SET memory_limit='4GB'")

    # Load dev set with vehicle_id
    dev_df = con.execute(f"""
        SELECT
            test_id,
            vehicle_id,
            test_date
        FROM read_parquet('{DEV_SET}')
        WHERE vehicle_id IS NOT NULL
    """).fetchdf()
    print(f"  Dev set: {len(dev_df):,} tests")

    # Load current test advisories
    adv_df = con.execute(f"""
        SELECT
            test_id,
            adv_brakes,
            adv_tyres,
            adv_suspension
        FROM read_parquet('{ADVISORIES}')
    """).fetchdf()
    print(f"  Advisories: {len(adv_df):,} tests")

    # Load current test failures
    fail_df = con.execute(f"""
        SELECT
            test_id,
            fail_brakes,
            fail_tyres,
            fail_suspension
        FROM read_parquet('{FAILURES}')
    """).fetchdf()
    print(f"  Failures: {len(fail_df):,} tests")

    con.close()

    # =========================================================================
    # Step 2: Merge all data
    # =========================================================================
    print("\n[Step 2] Merging data...")

    merged = dev_df.merge(adv_df, on='test_id', how='left')
    merged = merged.merge(fail_df, on='test_id', how='left')

    # Fill NaN
    for col in ['adv_brakes', 'adv_tyres', 'adv_suspension']:
        merged[col] = merged[col].fillna(0).astype(int)
    for col in ['fail_brakes', 'fail_tyres', 'fail_suspension']:
        merged[col] = merged[col].fillna(0).astype(int)

    print(f"  Merged: {len(merged):,} tests")

    # =========================================================================
    # Step 3: Compute prior advisory using as-of shift
    # =========================================================================
    print("\n[Step 3] Computing prior advisories (as-of safe)...")

    merged['test_date'] = pd.to_datetime(merged['test_date'])
    merged = merged.sort_values(['vehicle_id', 'test_date']).reset_index(drop=True)

    # For each component, compute prior advisory
    for comp, (adv_col, fail_col) in COMPONENT_MAP.items():
        prior_col = f'prior_adv_{comp}'
        merged[prior_col] = (
            merged.groupby('vehicle_id')[adv_col]
            .transform(lambda x: x.shift(1).fillna(0))
        ).astype(int)

        # Binary: had prior advisory
        merged[f'has_prior_adv_{comp}'] = (merged[prior_col] > 0).astype(int)

    # =========================================================================
    # Step 4: Compute escalation flags
    # =========================================================================
    print("\n[Step 4] Computing escalation flags...")

    escalation_cols = []
    for comp, (adv_col, fail_col) in COMPONENT_MAP.items():
        prior_col = f'prior_adv_{comp}'
        esc_col = f'escalated_{comp}'

        # Escalation: had prior advisory AND has current failure
        merged[esc_col] = (
            (merged[prior_col] > 0) & (merged[fail_col] == 1)
        ).astype(int)
        escalation_cols.append(esc_col)

        # Stats
        has_prior = (merged[prior_col] > 0).sum()
        escalated = merged[esc_col].sum()
        esc_rate = escalated / has_prior * 100 if has_prior > 0 else 0

        print(f"    {comp:12s}: {escalated:>7,} escalated / {has_prior:>7,} with prior = {esc_rate:.1f}%")

    # Total escalation count
    merged['escalation_count'] = merged[escalation_cols].sum(axis=1)
    merged['has_any_escalation'] = (merged['escalation_count'] > 0).astype(int)

    # =========================================================================
    # Step 5: Compute component-specific escalation rates
    # =========================================================================
    print("\n[Step 5] Component escalation rates (consumer advice)...")

    print("\n  Escalation rates for consumer advice:")
    for comp, (adv_col, fail_col) in COMPONENT_MAP.items():
        prior_col = f'prior_adv_{comp}'

        # P(failure | prior advisory)
        with_prior = merged[merged[prior_col] > 0]
        without_prior = merged[merged[prior_col] == 0]

        if len(with_prior) > 0:
            esc_rate_with = with_prior[fail_col].mean() * 100
            esc_rate_without = without_prior[fail_col].mean() * 100 if len(without_prior) > 0 else 0
            lift = esc_rate_with / esc_rate_without if esc_rate_without > 0 else 0

            print(f"    {comp.upper():12s}: Prior advisory → {esc_rate_with:.1f}% failure rate ({lift:.1f}x lift)")
            print(f"                 No prior advisory → {esc_rate_without:.1f}% failure rate")

    # =========================================================================
    # Step 6: Validate signal against BOTH failures
    # =========================================================================
    print("\n[Step 6] Validating escalation signal...")

    # Load BOTH target from ignored_advisory file
    ignored_df = pd.read_parquet(PROJECT_ROOT / "ignored_advisory_features_v2.parquet", columns=['test_id', 'has_both'])
    merged = merged.merge(ignored_df, on='test_id', how='left')
    merged['has_both'] = merged['has_both'].fillna(0).astype(int)

    # BOTH rate by escalation status
    print("\n  BOTH failure rate by has_any_escalation:")
    for val in [0, 1]:
        subset = merged[merged['has_any_escalation'] == val]
        if len(subset) > 0:
            rate = subset['has_both'].mean() * 100
            print(f"    has_any_escalation={val}: {rate:.2f}% (n={len(subset):,})")

    # Risk ratio
    non_esc_rate = merged[merged['has_any_escalation'] == 0]['has_both'].mean()
    esc_rate = merged[merged['has_any_escalation'] == 1]['has_both'].mean()
    risk_ratio = esc_rate / non_esc_rate if non_esc_rate > 0 else 0
    print(f"\n  Escalation risk ratio (BOTH): {risk_ratio:.2f}x")

    # =========================================================================
    # Step 7: Save output
    # =========================================================================
    print("\n[Step 7] Saving escalation features...")

    output_cols = [
        'test_id', 'vehicle_id', 'test_date',
        'prior_adv_brakes', 'prior_adv_tyres', 'prior_adv_suspension',
        'has_prior_adv_brakes', 'has_prior_adv_tyres', 'has_prior_adv_suspension',
        'fail_brakes', 'fail_tyres', 'fail_suspension',
        'escalated_brakes', 'escalated_tyres', 'escalated_suspension',
        'escalation_count', 'has_any_escalation',
        'has_both'
    ]

    output_df = merged[output_cols].copy()
    output_df.to_parquet(OUTPUT_FILE, index=False)

    print(f"  Saved to: {OUTPUT_FILE}")
    print(f"  Total rows: {len(output_df):,}")

    # =========================================================================
    # Summary with Consumer Advice Examples
    # =========================================================================
    coverage = merged['has_any_escalation'].mean() * 100

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"""
  Component Escalation features built successfully!

  Output: {OUTPUT_FILE}
  Tests with features: {len(output_df):,}
  Coverage (has_any_escalation): {coverage:.1f}%

  Consumer Advice Examples:
""")

    for comp, (adv_col, fail_col) in COMPONENT_MAP.items():
        prior_col = f'prior_adv_{comp}'
        with_prior = merged[merged[prior_col] > 0]
        if len(with_prior) > 0:
            esc_rate = with_prior[fail_col].mean() * 100
            print(f"    if prior_adv_{comp} > 0:")
            print(f"      \"Your {comp.title()} Advisory has a {esc_rate:.0f}% chance of becoming a Failure\"")
            print()

    print("=" * 70)


if __name__ == "__main__":
    main()
