"""
Build PONR (Point of No Return) Mining Dataset
===============================================

Creates a dataset for apriori association rule mining to identify
advisory combinations that predict escalation to major failures.

Uses existing DEV/OOT sets which have prev_advisory_* columns.
This avoids processing the 13GB advisories_summary.csv.

Cross-cycle perspective:
- Antecedents: prev_advisory_* (advisories from previous test)
- Consequent: is_failure (major failure in current test)

Output: Basket format for mlxtend apriori mining

Created: 2026-01-13
"""

import duckdb
import pandas as pd
from pathlib import Path
from datetime import datetime

# Paths
PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
WORK_DIR = Path.home() / "autosafe_work"

# Input files - use DEV/OOT which have prev_advisory columns
DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set_with_advisory.parquet"
OOT_SET = PROJECT_ROOT / "stratified_samples/oot_set_with_advisory.parquet"

# Output
OUTPUT_FILE = WORK_DIR / "ponr_mining_dataset.parquet"
BASKET_FILE = WORK_DIR / "ponr_basket.parquet"

# Advisory columns to use as PONR antecedents
ADVISORY_COLS = [
    'prev_advisory_brakes',
    'prev_advisory_suspension',
    'prev_advisory_steering',
    'prev_advisory_tyres',
]

# Mapping to component groups
COL_TO_GROUP = {
    'prev_advisory_brakes': 'adv_brakes',
    'prev_advisory_suspension': 'adv_suspension',
    'prev_advisory_steering': 'adv_steering',
    'prev_advisory_tyres': 'adv_tyres',
}


def main():
    start_time = datetime.now()
    print("=" * 70)
    print("BUILDING PONR MINING DATASET")
    print("=" * 70)

    con = duckdb.connect()
    con.execute("SET memory_limit='4GB'")

    # ========================================================================
    # Step 1: Load DEV and OOT with advisory features
    # ========================================================================
    print("\n[1] Loading DEV and OOT sets...")

    # Load ALL records (not filtered) to get true baseline
    query = f"""
        SELECT
            test_id,
            vehicle_id,
            test_date,
            is_failure,
            COALESCE(prev_advisory_brakes, 0) as prev_advisory_brakes,
            COALESCE(prev_advisory_suspension, 0) as prev_advisory_suspension,
            COALESCE(prev_advisory_steering, 0) as prev_advisory_steering,
            COALESCE(prev_advisory_tyres, 0) as prev_advisory_tyres,
            COALESCE(prev_advisory_total, 0) as prev_advisory_total
        FROM (
            SELECT test_id, vehicle_id, test_date, is_failure,
                   prev_advisory_brakes, prev_advisory_suspension,
                   prev_advisory_steering, prev_advisory_tyres, prev_advisory_total
            FROM read_parquet('{DEV_SET}')
            UNION ALL
            SELECT test_id, vehicle_id, test_date, is_failure,
                   prev_advisory_brakes, prev_advisory_suspension,
                   prev_advisory_steering, prev_advisory_tyres, prev_advisory_total
            FROM read_parquet('{OOT_SET}')
        )
    """

    df = con.execute(query).fetchdf()
    print(f"  Total records: {len(df):,}")

    # ========================================================================
    # Step 2: Create basket format for apriori
    # ========================================================================
    print("\n[2] Creating basket format...")

    # Convert to binary basket: has_X = 1 if prev_advisory_X > 0
    basket_df = pd.DataFrame()
    basket_df['test_id'] = df['test_id']

    # Advisory presence (antecedents)
    for col in ADVISORY_COLS:
        basket_col = COL_TO_GROUP[col]
        basket_df[basket_col] = (df[col] > 0).astype(int)

    # Failure outcome (consequent)
    basket_df['failure'] = df['is_failure'].astype(int)

    # Stats
    print("\n  Item frequencies:")
    for col in basket_df.columns[1:]:  # Skip test_id
        pct = basket_df[col].mean() * 100
        print(f"    {col}: {pct:.1f}%")

    # ========================================================================
    # Step 3: Create co-occurrence summary
    # ========================================================================
    print("\n[3] Computing co-occurrence patterns...")

    # Get tests with at least one advisory
    has_advisory = basket_df[['adv_brakes', 'adv_suspension', 'adv_steering', 'adv_tyres']].sum(axis=1) > 0
    basket_with_adv = basket_df[has_advisory].copy()

    print(f"  Tests with at least one advisory: {len(basket_with_adv):,}")

    # Failure rate by advisory pattern
    adv_cols = ['adv_brakes', 'adv_suspension', 'adv_steering', 'adv_tyres']
    basket_with_adv['pattern'] = basket_with_adv[adv_cols].apply(
        lambda row: '+'.join([c.replace('adv_', '') for c, v in row.items() if v == 1]),
        axis=1
    )

    pattern_stats = basket_with_adv.groupby('pattern').agg({
        'failure': ['sum', 'count', 'mean']
    }).reset_index()
    pattern_stats.columns = ['pattern', 'failures', 'count', 'failure_rate']
    pattern_stats = pattern_stats.sort_values('failure_rate', ascending=False)

    print("\n  Top 15 PONR patterns (by failure rate):")
    print("  " + "-" * 60)
    print(f"  {'Pattern':<35} {'Count':>8} {'Fail%':>8} {'Lift':>8}")
    print("  " + "-" * 60)

    baseline_rate = basket_df['failure'].mean()
    for _, row in pattern_stats.head(15).iterrows():
        if row['count'] >= 100:  # Min support
            lift = row['failure_rate'] / baseline_rate
            print(f"  {row['pattern']:<35} {row['count']:>8,} {row['failure_rate']*100:>7.1f}% {lift:>7.2f}x")

    # ========================================================================
    # Step 4: Save outputs
    # ========================================================================
    print("\n[4] Saving outputs...")

    # Save full basket for apriori mining
    basket_df.to_parquet(BASKET_FILE, index=False)
    print(f"  Basket file: {BASKET_FILE}")

    # Save pattern stats
    pattern_stats.to_parquet(OUTPUT_FILE, index=False)
    print(f"  Pattern stats: {OUTPUT_FILE}")

    # ========================================================================
    # Summary
    # ========================================================================
    elapsed = (datetime.now() - start_time).total_seconds()
    print("\n" + "=" * 70)
    print("PONR MINING DATASET BUILD COMPLETE")
    print("=" * 70)
    print(f"  Baseline failure rate: {baseline_rate*100:.1f}%")
    print(f"  Tests with advisories: {len(basket_with_adv):,}")
    print(f"  Unique patterns: {len(pattern_stats):,}")
    print(f"  Elapsed: {elapsed:.1f}s")
    print("=" * 70)

    con.close()


if __name__ == "__main__":
    main()
