#!/usr/bin/env python3
"""
Build Undercarriage Stress Features for V40

Creates a parquet file keyed by test_id with prior test's counts for coupled mechanical systems:
- Suspension
- Steering
- Brakes
- Body, chassis, structure (Proxy for Drivetrain/Structure)

These systems are mechanically linked. Isolated issues might be minor, but
multiple issues across these systems suggest "structural fatigue" or "undercarriage stress".

Uses spine linkage (prev_cycle_test_id) to look up prior test's defect counts.

Output: ~/autosafe_work/undercarriage_features.parquet
"""

import duckdb
from pathlib import Path
import pandas as pd

# Paths
PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
DEFECTS_SUMMARY = PROJECT_ROOT / "defects_summary.parquet"
SPINE = PROJECT_ROOT / "cycle_first_with_history.parquet"
OUTPUT = Path.home() / "autosafe_work/undercarriage_features.parquet"

def main():
    print("Building Undercarriage Stress Features for V40")
    print("=" * 60)
    
    conn = duckdb.connect()
    
    # Step 1: Build lookup from defects_summary
    print("\n[1] Loading undercarriage defect data from defects_summary...")
    
    # Check column names in defects_summary just to be safe (though we checked via CLI)
    # Using specific columns identified in planning
    lookup = conn.execute(f'''
        SELECT 
            test_id,
            COALESCE("Suspension", 0) as count_suspension,
            COALESCE("Steering", 0) as count_steering,
            COALESCE("Brakes", 0) as count_brakes,
            COALESCE("Body, chassis, structure", 0) as count_structure
        FROM read_parquet('{DEFECTS_SUMMARY}')
    ''').fetchdf()
    
    print(f"    Loaded {len(lookup):,} tests with defect data")
    print(f"    Suspsension > 0: {(lookup['count_suspension'] > 0).mean()*100:.1f}%")
    print(f"    Steering > 0:    {(lookup['count_steering'] > 0).mean()*100:.1f}%")
    print(f"    Brakes > 0:      {(lookup['count_brakes'] > 0).mean()*100:.1f}%")
    print(f"    Structure > 0:   {(lookup['count_structure'] > 0).mean()*100:.1f}%")
    
    # Step 2: Register for DuckDB join
    conn.register('undercarriage_lookup', lookup)
    
    # Step 3: Get spine linkage and join
    print("\n[2] Joining with spine linkage to get prior test counts...")
    
    result = conn.execute(f'''
        SELECT 
            s.test_id,
            COALESCE(d.count_suspension, 0) as prior_suspension,
            COALESCE(d.count_steering, 0) as prior_steering,
            COALESCE(d.count_brakes, 0) as prior_brakes,
            COALESCE(d.count_structure, 0) as prior_structure
        FROM read_parquet('{SPINE}') s
        LEFT JOIN undercarriage_lookup d ON s.prev_cycle_test_id = d.test_id
        WHERE s.prev_cycle_test_id IS NOT NULL
    ''').fetchdf()
    
    print(f"    Matched {len(result):,} tests with prior test linkage")
    
    # Compute simple sum score for stats (weighted version happens in training script)
    result['raw_sum'] = (
        result['prior_suspension'] + 
        result['prior_steering'] + 
        result['prior_brakes'] + 
        result['prior_structure']
    )
    
    print(f"    Any undercarriage defect (raw sum > 0): {(result['raw_sum'] > 0).mean()*100:.1f}%")
    print(f"    Multiple defects (raw sum > 1):         {(result['raw_sum'] > 1).mean()*100:.1f}%")
    print(f"    Mean raw sum: {result['raw_sum'].mean():.3f}")
    
    # Step 4: Save to parquet
    print(f"\n[3] Saving to {OUTPUT}...")
    result.to_parquet(OUTPUT, index=False)
    
    print(f"    Saved {len(result):,} rows")
    print("\nDone!")

if __name__ == "__main__":
    main()
