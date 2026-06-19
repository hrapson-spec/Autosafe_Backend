#!/usr/bin/env python3
"""
Build Lazy Failure Features for V39 Systemic Neglect Index

Creates a parquet file keyed by test_id with prior test's "lazy failure" counts:
- prior_lamps: Lamps, reflectors and electrical equipment defects
- prior_visibility: Visibility (wipers, washers) defects  
- prior_mirrors: Driver's View of the Road (mirrors) defects

These are "avoidable" defects - a responsible owner fixes them before MOT.
High counts indicate systemic neglect.

Uses spine linkage (prev_cycle_test_id) to look up prior test's defect counts.

Output: ~/autosafe_work/lazy_failure_features.parquet
"""

import duckdb
from pathlib import Path
import pandas as pd

# Paths
PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
DEFECTS_SUMMARY = PROJECT_ROOT / "defects_summary.parquet"
SPINE = PROJECT_ROOT / "cycle_first_with_history.parquet"
OUTPUT = Path.home() / "autosafe_work/lazy_failure_features.parquet"

def main():
    print("Building Lazy Failure Features for V39 SNI")
    print("=" * 60)
    
    conn = duckdb.connect()
    
    # Step 1: Build lazy failure lookup from defects_summary
    print("\n[1] Loading lazy failure data from defects_summary...")
    
    # These sections represent "avoidable" defects
    lazy_lookup = conn.execute(f'''
        SELECT 
            test_id,
            COALESCE("Lamps, reflectors and electrical equipment", 0) as lamp_defects,
            COALESCE("Visibility", 0) as visibility_defects,
            COALESCE("Driver's View of the Road", 0) as mirror_defects,
            COALESCE("Lights", 0) as light_defects
        FROM read_parquet('{DEFECTS_SUMMARY}')
    ''').fetchdf()
    
    print(f"    Loaded {len(lazy_lookup):,} tests with defect data")
    print(f"    lamp_defects > 0: {(lazy_lookup['lamp_defects'] > 0).mean()*100:.1f}%")
    print(f"    visibility_defects > 0: {(lazy_lookup['visibility_defects'] > 0).mean()*100:.1f}%")
    print(f"    mirror_defects > 0: {(lazy_lookup['mirror_defects'] > 0).mean()*100:.1f}%")
    print(f"    light_defects > 0: {(lazy_lookup['light_defects'] > 0).mean()*100:.1f}%")
    
    # Step 2: Register for DuckDB join
    conn.register('lazy_lookup', lazy_lookup)
    
    # Step 3: Get spine linkage and join with lazy failure data
    print("\n[2] Joining with spine linkage to get prior test lazy failures...")
    
    result = conn.execute(f'''
        SELECT 
            s.test_id,
            COALESCE(d.lamp_defects, 0) as prior_lamps,
            COALESCE(d.visibility_defects, 0) as prior_visibility,
            COALESCE(d.mirror_defects, 0) as prior_mirrors,
            COALESCE(d.light_defects, 0) as prior_lights
        FROM read_parquet('{SPINE}') s
        LEFT JOIN lazy_lookup d ON s.prev_cycle_test_id = d.test_id
        WHERE s.prev_cycle_test_id IS NOT NULL
    ''').fetchdf()
    
    print(f"    Matched {len(result):,} tests with prior test linkage")
    
    # Compute combined lazy failure count
    result['lazy_failure_count'] = (
        result['prior_lamps'] + 
        result['prior_visibility'] + 
        result['prior_mirrors'] +
        result['prior_lights']
    )
    
    print(f"    prior_lamps > 0: {(result['prior_lamps'] > 0).mean()*100:.1f}%")
    print(f"    prior_visibility > 0: {(result['prior_visibility'] > 0).mean()*100:.1f}%")
    print(f"    lazy_failure_count > 0: {(result['lazy_failure_count'] > 0).mean()*100:.1f}%")
    print(f"    lazy_failure_count mean: {result['lazy_failure_count'].mean():.3f}")
    
    # Step 4: Save to parquet
    print(f"\n[3] Saving to {OUTPUT}...")
    result.to_parquet(OUTPUT, index=False)
    
    print(f"    Saved {len(result):,} rows")
    print("\nDone!")

if __name__ == "__main__":
    main()
