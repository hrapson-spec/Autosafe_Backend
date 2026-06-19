#!/usr/bin/env python3
"""
Build 9-Category System Failure Features
=========================================

Expands component failure tracking from 3 systems to all 9 MOT system categories.
This is the foundation for system-level features.

Current state: component_failures_all_years.parquet has only:
  - fail_brakes, fail_tyres, fail_suspension

This script creates: system_failures_9cat.parquet with:
  - fail_brakes
  - fail_tyres_wheels
  - fail_suspension_steering
  - fail_structure_corrosion
  - fail_lights_electrical
  - fail_visibility
  - fail_seatbelts_restraints
  - fail_exhaust_emissions
  - fail_other
  - system_failure_count (distinct systems failed per test)

Pipeline:
  test_items(test_id, rfr_id, rfr_type_code)
  -> JOIN item_detail(rfr_id, test_item_set_section_id)
  -> MAP section_id -> system via SECTION_TO_SYSTEM
  -> FILTER to failures only (rfr_type_code in ['M', 'F'])
  -> AGGREGATE by test_id

Created: 2026-01-09
"""

import pandas as pd
import numpy as np
import duckdb
from pathlib import Path

# Import system mapping
from system_map_v1 import SYSTEMS, SECTION_TO_SYSTEM, get_system, get_sql_case_statement

# Paths
PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
ITEM_DETAIL = PROJECT_ROOT / "item_detail.csv"
TEST_ITEMS_DIR = PROJECT_ROOT / "test_items"
OUTPUT_FILE = PROJECT_ROOT / "system_failures_9cat.parquet"


def main():
    print("=" * 70)
    print("BUILD 9-CATEGORY SYSTEM FAILURE FEATURES")
    print("=" * 70)

    con = duckdb.connect()
    con.execute("SET memory_limit='6GB'")

    # =========================================================================
    # Step 1: Load item_detail and show system distribution
    # =========================================================================
    print("\n[Step 1] Loading item_detail for RFR to section mapping...")

    item_detail = con.execute(f"""
        SELECT rfr_id, test_item_set_section_id
        FROM read_csv('{ITEM_DETAIL}', delim='|', header=true, auto_detect=true)
    """).df()
    print(f"  Total RFR codes: {len(item_detail):,}")

    # Dedupe to get unique rfr_id -> section_id
    item_detail_dedup = item_detail.drop_duplicates('rfr_id')
    print(f"  Unique RFR IDs: {len(item_detail_dedup):,}")

    # Show system distribution
    print("\n  RFR codes by system:")
    item_detail_dedup['system'] = item_detail_dedup['test_item_set_section_id'].apply(get_system)
    for system in SYSTEMS:
        count = (item_detail_dedup['system'] == system).sum()
        print(f"    {system:25s}: {count:>5,} RFR codes")

    # =========================================================================
    # Step 2: Process test_items using DuckDB (much faster)
    # =========================================================================
    print("\n[Step 2] Processing test_items files with DuckDB...")

    # Get the SQL CASE statement for system mapping
    system_case = get_sql_case_statement('section_id')

    all_years_dfs = []

    for year in ['2019', '2020', '2021', '2022', '2023', '2024']:
        csv_file = TEST_ITEMS_DIR / year / "test_item.csv"
        if not csv_file.exists():
            print(f"  Warning: {csv_file} not found")
            continue

        print(f"\n  Processing {year}...")

        # Use DuckDB to efficiently join and aggregate
        year_df = con.execute(f"""
            WITH test_items AS (
                SELECT test_id, rfr_id, rfr_type_code
                FROM read_csv('{csv_file}', delim='|', header=true, auto_detect=true)
                WHERE rfr_type_code IN ('M', 'F')
            ),
            item_detail AS (
                SELECT DISTINCT rfr_id, test_item_set_section_id as section_id
                FROM read_csv('{ITEM_DETAIL}', delim='|', header=true, auto_detect=true)
            ),
            joined AS (
                SELECT
                    t.test_id,
                    {system_case} as system
                FROM test_items t
                LEFT JOIN item_detail i ON t.rfr_id = i.rfr_id
            )
            SELECT
                test_id,
                MAX(CASE WHEN system = 'brakes' THEN 1 ELSE 0 END) as fail_brakes,
                MAX(CASE WHEN system = 'tyres_wheels' THEN 1 ELSE 0 END) as fail_tyres_wheels,
                MAX(CASE WHEN system = 'suspension_steering' THEN 1 ELSE 0 END) as fail_suspension_steering,
                MAX(CASE WHEN system = 'structure_corrosion' THEN 1 ELSE 0 END) as fail_structure_corrosion,
                MAX(CASE WHEN system = 'lights_electrical' THEN 1 ELSE 0 END) as fail_lights_electrical,
                MAX(CASE WHEN system = 'visibility' THEN 1 ELSE 0 END) as fail_visibility,
                MAX(CASE WHEN system = 'seatbelts_restraints' THEN 1 ELSE 0 END) as fail_seatbelts_restraints,
                MAX(CASE WHEN system = 'exhaust_emissions' THEN 1 ELSE 0 END) as fail_exhaust_emissions,
                MAX(CASE WHEN system = 'other' THEN 1 ELSE 0 END) as fail_other
            FROM joined
            GROUP BY test_id
        """).df()

        print(f"    Tests with failures: {len(year_df):,}")
        all_years_dfs.append(year_df)

    # Combine all years
    print("\n  Combining all years...")
    systems_df = pd.concat(all_years_dfs, ignore_index=True)

    # Some test_ids might appear in multiple years (retests) - take max
    print(f"  Total rows before dedup: {len(systems_df):,}")
    systems_df = systems_df.groupby('test_id').max().reset_index()
    print(f"  Total rows after dedup: {len(systems_df):,}")

    # =========================================================================
    # Step 3: Add system_failure_count
    # =========================================================================
    print("\n[Step 3] Computing system_failure_count...")

    fail_cols = [f'fail_{s}' for s in SYSTEMS]
    systems_df['system_failure_count'] = systems_df[fail_cols].sum(axis=1).astype(int)

    # Show distribution
    print("\n  System failure distribution:")
    for system in SYSTEMS:
        col = f'fail_{system}'
        count = systems_df[col].sum()
        pct = count / len(systems_df) * 100
        print(f"    {col:30s}: {count:>8,} ({pct:>5.1f}%)")

    print(f"\n  system_failure_count distribution:")
    for cnt in range(1, 6):
        n = (systems_df['system_failure_count'] == cnt).sum()
        pct = n / len(systems_df) * 100
        print(f"    count={cnt}: {n:>8,} ({pct:>5.1f}%)")
    n_5plus = (systems_df['system_failure_count'] >= 5).sum()
    print(f"    count>=5: {n_5plus:>8,} ({n_5plus/len(systems_df)*100:.1f}%)")

    # =========================================================================
    # Step 4: Save to parquet
    # =========================================================================
    print("\n[Step 4] Saving to parquet...")

    systems_df.to_parquet(OUTPUT_FILE, index=False)
    print(f"  Saved to: {OUTPUT_FILE}")
    print(f"  Total rows: {len(systems_df):,}")

    # =========================================================================
    # Step 5: Verify coverage with dev/OOT sets
    # =========================================================================
    print("\n[Step 5] Checking coverage with training data...")

    DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set.parquet"
    OOT_SET = PROJECT_ROOT / "stratified_samples/oot_test_set.parquet"

    system_ids = set(systems_df['test_id'])

    # Check dev set
    if DEV_SET.exists():
        dev_df = con.execute(f"SELECT test_id, is_failure FROM read_parquet('{DEV_SET}')").df()
        dev_ids = set(dev_df['test_id'])
        dev_failures = set(dev_df[dev_df['is_failure'] == 1]['test_id'])
        dev_overlap = len(dev_ids.intersection(system_ids))
        dev_fail_overlap = len(dev_failures.intersection(system_ids))
        print(f"  Dev set:  {len(dev_ids):,} tests, {dev_overlap:,} with system data ({dev_overlap/len(dev_ids)*100:.1f}%)")
        print(f"            {len(dev_failures):,} failures, {dev_fail_overlap:,} with system data ({dev_fail_overlap/len(dev_failures)*100:.1f}%)")

    # Check OOT set
    if OOT_SET.exists():
        oot_df = con.execute(f"SELECT test_id, is_failure FROM read_parquet('{OOT_SET}')").df()
        oot_ids = set(oot_df['test_id'])
        oot_failures = set(oot_df[oot_df['is_failure'] == 1]['test_id'])
        oot_overlap = len(oot_ids.intersection(system_ids))
        oot_fail_overlap = len(oot_failures.intersection(system_ids))
        print(f"  OOT set:  {len(oot_ids):,} tests, {oot_overlap:,} with system data ({oot_overlap/len(oot_ids)*100:.1f}%)")
        print(f"            {len(oot_failures):,} failures, {oot_fail_overlap:,} with system data ({oot_fail_overlap/len(oot_failures)*100:.1f}%)")

    con.close()

    # =========================================================================
    # Summary
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"""
  9-Category System Failures built successfully!

  Output: {OUTPUT_FILE}
  Tests with system failures: {len(systems_df):,}

  System coverage:
    brakes:              {systems_df['fail_brakes'].sum():>8,} tests
    tyres_wheels:        {systems_df['fail_tyres_wheels'].sum():>8,} tests
    suspension_steering: {systems_df['fail_suspension_steering'].sum():>8,} tests
    structure_corrosion: {systems_df['fail_structure_corrosion'].sum():>8,} tests
    lights_electrical:   {systems_df['fail_lights_electrical'].sum():>8,} tests
    visibility:          {systems_df['fail_visibility'].sum():>8,} tests
    seatbelts_restraints:{systems_df['fail_seatbelts_restraints'].sum():>8,} tests
    exhaust_emissions:   {systems_df['fail_exhaust_emissions'].sum():>8,} tests
    other:               {systems_df['fail_other'].sum():>8,} tests

  Multi-system failures:
    Avg systems per failed test: {systems_df['system_failure_count'].mean():.2f}
    Max systems in single test:  {systems_df['system_failure_count'].max()}

  Next: Run build_system_history_features.py
    """)
    print("=" * 70)


if __name__ == "__main__":
    main()
