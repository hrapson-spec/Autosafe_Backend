"""
Build PONR (Point of No Return) Features
=========================================

Creates features from PONR rules for V31 training.

For each test, checks if previous advisories match PONR patterns.

Features:
- has_ponr_pattern: Binary (1 if any PONR pattern detected)
- ponr_risk_score: Sum of (confidence * lift) for matched patterns
- ponr_pattern_count: Number of distinct PONR patterns matched
- ponr_max_lift: Maximum lift among matched patterns

Input: ponr_rules.json, DEV/OOT parquet files
Output: ponr_features.parquet

Created: 2026-01-13
"""

import json
import duckdb
import pandas as pd
from pathlib import Path
from datetime import datetime

# Paths
PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
WORK_DIR = Path.home() / "autosafe_work"

RULES_FILE = WORK_DIR / "ponr_rules.json"
DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set_with_advisory.parquet"
OOT_SET = PROJECT_ROOT / "stratified_samples/oot_set_with_advisory.parquet"

OUTPUT_FILE = WORK_DIR / "ponr_features.parquet"


def load_ponr_rules():
    """Load PONR rules from JSON."""
    with open(RULES_FILE) as f:
        data = json.load(f)
    return data['rules']


def check_pattern_match(row, pattern_components):
    """
    Check if a test's previous advisories match a PONR pattern.

    Args:
        row: DataFrame row with prev_advisory_* columns
        pattern_components: List of component names (e.g., ['brakes', 'steering'])

    Returns:
        True if ALL components in the pattern have advisories
    """
    for comp in pattern_components:
        col = f'prev_advisory_{comp}'
        if row.get(col, 0) <= 0:
            return False
    return True


def compute_ponr_features(df, rules):
    """
    Compute PONR features for a dataframe.

    Args:
        df: DataFrame with prev_advisory_* columns
        rules: List of PONR rules from JSON

    Returns:
        DataFrame with PONR features added
    """
    # Initialize feature columns
    df = df.copy()
    df['has_ponr_pattern'] = 0
    df['ponr_risk_score'] = 0.0
    df['ponr_pattern_count'] = 0
    df['ponr_max_lift'] = 0.0

    # Check each rule
    for rule in rules:
        pattern_components = rule['antecedents']
        confidence = rule['confidence']
        lift = rule['lift']
        risk_score = confidence * lift

        # Create mask for matching rows
        mask = pd.Series(True, index=df.index)
        for comp in pattern_components:
            col = f'prev_advisory_{comp}'
            if col in df.columns:
                mask &= (df[col] > 0)
            else:
                mask = pd.Series(False, index=df.index)
                break

        # Update features for matching rows
        df.loc[mask, 'has_ponr_pattern'] = 1
        df.loc[mask, 'ponr_risk_score'] += risk_score
        df.loc[mask, 'ponr_pattern_count'] += 1
        df.loc[mask, 'ponr_max_lift'] = df.loc[mask, 'ponr_max_lift'].clip(lower=lift)

    return df


def main():
    start_time = datetime.now()
    print("=" * 70)
    print("BUILDING PONR FEATURES")
    print("=" * 70)

    # ========================================================================
    # Step 1: Load PONR rules
    # ========================================================================
    print("\n[1] Loading PONR rules...")

    rules = load_ponr_rules()
    print(f"  Loaded {len(rules)} rules")

    for rule in rules[:5]:
        print(f"    {rule['pattern']}: lift={rule['lift']:.2f}x, conf={rule['confidence']*100:.1f}%")

    # ========================================================================
    # Step 2: Load DEV and OOT sets
    # ========================================================================
    print("\n[2] Loading DEV and OOT sets...")

    con = duckdb.connect()

    # Load with only needed columns
    cols = ['test_id', 'prev_advisory_brakes', 'prev_advisory_suspension',
            'prev_advisory_steering', 'prev_advisory_tyres', 'is_failure']

    dev_df = con.execute(f"""
        SELECT {', '.join(cols)}
        FROM read_parquet('{DEV_SET}')
    """).fetchdf()
    print(f"  DEV set: {len(dev_df):,} records")

    oot_df = con.execute(f"""
        SELECT {', '.join(cols)}
        FROM read_parquet('{OOT_SET}')
    """).fetchdf()
    print(f"  OOT set: {len(oot_df):,} records")

    # ========================================================================
    # Step 3: Compute PONR features
    # ========================================================================
    print("\n[3] Computing PONR features...")

    dev_features = compute_ponr_features(dev_df, rules)
    oot_features = compute_ponr_features(oot_df, rules)

    # Combine
    combined = pd.concat([dev_features, oot_features], ignore_index=True)

    # ========================================================================
    # Step 4: Feature statistics
    # ========================================================================
    print("\n[4] Feature statistics...")

    total = len(combined)
    has_ponr = combined['has_ponr_pattern'].sum()
    print(f"  Total records: {total:,}")
    print(f"  Records with PONR pattern: {has_ponr:,} ({has_ponr/total*100:.1f}%)")

    # Distribution of pattern count
    print("\n  PONR pattern count distribution:")
    for cnt, n in combined['ponr_pattern_count'].value_counts().sort_index().items():
        pct = n / total * 100
        print(f"    {cnt} patterns: {n:,} ({pct:.1f}%)")

    # Failure rate by PONR status
    print("\n  Failure rate by PONR status:")
    for has_ponr, group in combined.groupby('has_ponr_pattern'):
        fail_rate = group['is_failure'].mean() * 100
        status = "With PONR" if has_ponr else "Without PONR"
        print(f"    {status}: {fail_rate:.1f}%")

    # ========================================================================
    # Step 5: Save features
    # ========================================================================
    print("\n[5] Saving features...")

    # Keep only test_id and PONR feature columns
    output_df = combined[['test_id', 'has_ponr_pattern', 'ponr_risk_score',
                          'ponr_pattern_count', 'ponr_max_lift']]

    output_df.to_parquet(OUTPUT_FILE, index=False)

    file_size = OUTPUT_FILE.stat().st_size / (1024 * 1024)
    print(f"  Saved to: {OUTPUT_FILE}")
    print(f"  Size: {file_size:.1f} MB")

    # ========================================================================
    # Summary
    # ========================================================================
    elapsed = (datetime.now() - start_time).total_seconds()
    print("\n" + "=" * 70)
    print("PONR FEATURE BUILD COMPLETE")
    print("=" * 70)
    print(f"  Total records: {total:,}")
    print(f"  PONR pattern coverage: {has_ponr/total*100:.1f}%")
    print(f"  Elapsed: {elapsed:.1f}s")
    print("=" * 70)

    con.close()


if __name__ == "__main__":
    main()
