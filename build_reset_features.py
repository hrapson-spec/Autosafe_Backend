"""
Build Reset Features - Ultra-Efficient Streaming Version
=========================================================

Uses pure DuckDB with streaming to disk, never loading full data into Python.
Processes in batches to avoid memory issues.

Created: 2026-01-06
"""

import duckdb
from pathlib import Path
from datetime import datetime

# ============================================================================
# Configuration
# ============================================================================

PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
WORK_DIR = Path.home() / "autosafe_work"

DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set.parquet"
OOT_SET = PROJECT_ROOT / "stratified_samples/oot_test_set.parquet"
CYCLE_FIRST_TESTS = PROJECT_ROOT / "cycle_first_tests.parquet"
MILEAGE_LOOKUP = WORK_DIR / "mileage_lookup_full.parquet"
COMPONENT_FAILURES = PROJECT_ROOT / "component_failures_all_years.parquet"
OUTPUT_FILE = WORK_DIR / "reset_features.parquet"


def main():
    print("=" * 70)
    print("BUILD RESET FEATURES (STREAMING)")
    print("=" * 70)
    print(f"Started: {datetime.now().isoformat()}")
    
    con = duckdb.connect()
    con.execute("SET memory_limit='3GB'")
    con.execute("SET threads=2")
    con.execute("SET preserve_insertion_order=false")  # Allow reordering for efficiency
    
    # =========================================================================
    # Step 1: Create target test_ids table
    # =========================================================================
    print("\n[Step 1] Creating target test_ids...")
    
    con.execute(f"""
        CREATE TEMP TABLE target_tests AS
        SELECT DISTINCT test_id, vehicle_id 
        FROM (
            SELECT test_id, vehicle_id FROM read_parquet('{DEV_SET}')
            UNION ALL
            SELECT test_id, vehicle_id FROM read_parquet('{OOT_SET}')
        )
    """)
    
    count = con.execute("SELECT COUNT(*) FROM target_tests").fetchone()[0]
    print(f"  Target tests: {count:,}")
    
    vehicles = con.execute("SELECT COUNT(DISTINCT vehicle_id) FROM target_tests").fetchone()[0]
    print(f"  Target vehicles: {vehicles:,}")
    
    # =========================================================================
    # Step 2: Build filtered history with only target vehicles
    # =========================================================================
    print("\n[Step 2] Building filtered history...")
    
    # Use a hash join instead of loading all vehicle_ids into memory
    con.execute(f"""
        CREATE TEMP TABLE filtered_history AS
        SELECT 
            c.vehicle_id,
            c.test_id,
            c.test_date,
            COALESCE(m.test_mileage, 50000) as test_mileage,
            COALESCE(cf.fail_brakes, 0)::TINYINT as fail_brakes,
            COALESCE(cf.fail_tyres, 0)::TINYINT as fail_tyres,
            COALESCE(cf.fail_suspension, 0)::TINYINT as fail_suspension
        FROM read_parquet('{CYCLE_FIRST_TESTS}') c
        SEMI JOIN target_tests tt ON c.vehicle_id = tt.vehicle_id
        LEFT JOIN read_parquet('{MILEAGE_LOOKUP}') m ON c.test_id = m.test_id
        LEFT JOIN read_parquet('{COMPONENT_FAILURES}') cf ON c.test_id = cf.test_id
    """)
    
    hist_count = con.execute("SELECT COUNT(*) FROM filtered_history").fetchone()[0]
    print(f"  Filtered history rows: {hist_count:,}")

    # =========================================================================
    # Step 3: Compute reset features using window functions
    # =========================================================================
    print("\n[Step 3] Computing reset features (streaming to file)...")
    
    # Write directly to parquet file
    con.execute(f"""
        COPY (
            WITH with_last_fail AS (
                SELECT 
                    test_id,
                    vehicle_id,
                    test_mileage,
                    MAX(CASE WHEN fail_brakes = 1 THEN test_mileage END)
                        OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id
                              ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) 
                        as last_brakes_fail_mileage,
                    MAX(CASE WHEN fail_tyres = 1 THEN test_mileage END)
                        OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id
                              ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
                        as last_tyres_fail_mileage,
                    MAX(CASE WHEN fail_suspension = 1 THEN test_mileage END)
                        OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id
                              ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
                        as last_suspension_fail_mileage
                FROM filtered_history
            )
            SELECT 
                w.test_id,
                (w.test_mileage - COALESCE(w.last_brakes_fail_mileage, 0))::INTEGER 
                    as miles_since_last_fail_brakes,
                (w.test_mileage - COALESCE(w.last_tyres_fail_mileage, 0))::INTEGER 
                    as miles_since_last_fail_tyres,
                (w.test_mileage - COALESCE(w.last_suspension_fail_mileage, 0))::INTEGER 
                    as miles_since_last_fail_suspension,
                (CASE WHEN w.last_brakes_fail_mileage IS NOT NULL THEN 1 ELSE 0 END)::TINYINT 
                    as had_prior_brakes_fail,
                (CASE WHEN w.last_tyres_fail_mileage IS NOT NULL THEN 1 ELSE 0 END)::TINYINT 
                    as had_prior_tyres_fail,
                (CASE WHEN w.last_suspension_fail_mileage IS NOT NULL THEN 1 ELSE 0 END)::TINYINT 
                    as had_prior_suspension_fail
            FROM with_last_fail w
            SEMI JOIN target_tests tt ON w.test_id = tt.test_id
        ) TO '{OUTPUT_FILE}' (FORMAT PARQUET, COMPRESSION SNAPPY)
    """)
    
    print("  Written to parquet file")
    
    # =========================================================================
    # Step 4: Validate
    # =========================================================================
    print("\n[Step 4] Validation...")
    
    stats = con.execute(f"""
        SELECT 
            COUNT(*) as n_rows,
            AVG(miles_since_last_fail_brakes) as avg_brakes,
            AVG(miles_since_last_fail_tyres) as avg_tyres,
            AVG(miles_since_last_fail_suspension) as avg_suspension,
            AVG(had_prior_brakes_fail) as pct_prior_brakes,
            AVG(had_prior_tyres_fail) as pct_prior_tyres,
            AVG(had_prior_suspension_fail) as pct_prior_suspension
        FROM read_parquet('{OUTPUT_FILE}')
    """).fetchone()
    
    print(f"  Rows: {stats[0]:,}")
    print(f"  Avg miles_since_last_fail_brakes: {stats[1]:,.0f}")
    print(f"  Avg miles_since_last_fail_tyres: {stats[2]:,.0f}")
    print(f"  Avg miles_since_last_fail_suspension: {stats[3]:,.0f}")
    print(f"  Had prior brakes fail: {stats[4]*100:.1f}%")
    print(f"  Had prior tyres fail: {stats[5]*100:.1f}%")
    print(f"  Had prior suspension fail: {stats[6]*100:.1f}%")
    
    file_size_mb = OUTPUT_FILE.stat().st_size / (1024 * 1024)
    print(f"  File size: {file_size_mb:.1f} MB")
    
    # =========================================================================
    # Summary
    # =========================================================================
    print("\n" + "=" * 70)
    print("RESET FEATURES BUILD COMPLETE")
    print("=" * 70)
    print(f"  Output: {OUTPUT_FILE}")
    print(f"  Completed: {datetime.now().isoformat()}")
    
    con.close()


if __name__ == "__main__":
    main()
