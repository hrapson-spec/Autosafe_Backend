#!/usr/bin/env python3
"""
Build Electronic Advisory Features for V38

Creates a parquet file keyed by test_id with:
- prior_electronic_advisories: Count of lamp/electrical advisories from prior test
- prior_srs_advisories: Count of SRS/airbag advisories from prior test

Uses spine linkage (prev_cycle_test_id) to look up prior test's electronic advisory counts.

Output: ~/autosafe_work/electronic_advisory_features.parquet
"""

import duckdb
from pathlib import Path
import pandas as pd

# Paths
PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
DEFECTS_SUMMARY = PROJECT_ROOT / "defects_summary.parquet"
SPINE = PROJECT_ROOT / "cycle_first_with_history.parquet"
OUTPUT = Path.home() / "autosafe_work/electronic_advisory_features.parquet"

def main():
    print("Building Electronic Advisory Features for V38")
    print("=" * 60)
    
    conn = duckdb.connect()
    
    # Step 1: Build electronic advisory lookup from defects_summary
    print("\n[1] Loading electronic advisory data from defects_summary...")
    
    electronic_lookup = conn.execute(f'''
        SELECT 
            test_id,
            COALESCE("Lamps, reflectors and electrical equipment", 0) as lamp_electrical_count,
            COALESCE("Seat Belts and Supplementary Restraint Systems", 0) as srs_count
        FROM read_parquet('{DEFECTS_SUMMARY}')
    ''').fetchdf()
    
    print(f"    Loaded {len(electronic_lookup):,} tests with electronic data")
    print(f"    lamp_electrical > 0: {(electronic_lookup['lamp_electrical_count'] > 0).mean()*100:.1f}%")
    print(f"    srs > 0: {(electronic_lookup['srs_count'] > 0).mean()*100:.1f}%")
    
    # Step 2: Register for DuckDB join
    conn.register('elec_lookup', electronic_lookup)
    
    # Step 3: Get spine linkage and join with electronic data
    print("\n[2] Joining with spine linkage to get prior test electronic advisories...")
    
    result = conn.execute(f'''
        SELECT 
            s.test_id,
            COALESCE(e.lamp_electrical_count, 0) as prior_electronic_advisories,
            COALESCE(e.srs_count, 0) as prior_srs_advisories
        FROM read_parquet('{SPINE}') s
        LEFT JOIN elec_lookup e ON s.prev_cycle_test_id = e.test_id
        WHERE s.prev_cycle_test_id IS NOT NULL
    ''').fetchdf()
    
    print(f"    Matched {len(result):,} tests with prior test linkage")
    print(f"    prior_electronic_advisories > 0: {(result['prior_electronic_advisories'] > 0).mean()*100:.1f}%")
    print(f"    prior_electronic_advisories mean: {result['prior_electronic_advisories'].mean():.3f}")
    
    # Step 4: Save to parquet
    print(f"\n[3] Saving to {OUTPUT}...")
    result.to_parquet(OUTPUT, index=False)
    
    print(f"    Saved {len(result):,} rows")
    print("\nDone!")

if __name__ == "__main__":
    main()
