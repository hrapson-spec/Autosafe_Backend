#!/usr/bin/env python3
"""
Build Regime Lookup Table
=========================

Extracts test_id -> test_class_id mapping from raw MOT data
and saves as parquet for fast joins.

Created: 2026-01-08
"""

import pandas as pd
from pathlib import Path

PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
RAW_TEST_DIR = PROJECT_ROOT / "MOT Test Results"
OUTPUT_FILE = PROJECT_ROOT / "regime_lookup.parquet"


def main():
    print("Building regime lookup table...")

    all_dfs = []

    for year in ['2019', '2020', '2021', '2022', '2023']:
        csv_path = RAW_TEST_DIR / year / "test_result.csv"
        if not csv_path.exists():
            print(f"  {year}: Not found")
            continue

        print(f"  {year}: Loading...")

        # Read only needed columns
        df = pd.read_csv(
            csv_path,
            sep='|',
            usecols=['test_id', 'test_class_id'],
            dtype={'test_id': 'int64', 'test_class_id': 'int8'}
        )

        all_dfs.append(df)
        print(f"  {year}: {len(df):,} records")

    # Combine all years
    combined = pd.concat(all_dfs, ignore_index=True)
    print(f"\n  Total: {len(combined):,} records")

    # Save to parquet
    combined.to_parquet(OUTPUT_FILE, index=False)
    print(f"  Saved to: {OUTPUT_FILE}")

    # Show distribution
    print("\n  Test class distribution:")
    for cls, count in combined['test_class_id'].value_counts().sort_index().items():
        pct = count / len(combined) * 100
        print(f"    Class {cls}: {count:>12,} ({pct:>5.1f}%)")


if __name__ == "__main__":
    main()
