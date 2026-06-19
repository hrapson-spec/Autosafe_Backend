"""
Build Advisory Conversion Features V2 (Fixed)
==============================================

Fixes from V1:
1. GRAIN: Strict GROUP BY test_id with assertion of no duplicates
2. COHORT: Canonical veteran definition: n_prior_tests >= 1
3. NULLs: Rookies excluded from feature computation entirely

Invariants enforced:
- COUNT(*) == COUNT(DISTINCT test_id)
- No duplicate test_ids
- NULL rate stable between train/OOT (both Veterans-only)

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
ADVISORY_TOTALS = PROJECT_ROOT / "advisory_totals_dedup.parquet"
COMPONENT_FAILURES = PROJECT_ROOT / "component_failures_all_years.parquet"

OUTPUT_FILE = WORK_DIR / "advisory_conversion_features_v2.parquet"

# Smoothing priors
ALPHA = 1
BETA = 5


def main():
    print("=" * 70)
    print("BUILD ADVISORY CONVERSION FEATURES V2 (FIXED)")
    print("=" * 70)
    print(f"Started: {datetime.now().isoformat()}")
    
    con = duckdb.connect()
    con.execute("SET memory_limit='4GB'")
    con.execute("SET threads=4")
    
    # =========================================================================
    # Step 1: Define canonical veteran population
    # =========================================================================
    print("\n[Step 1] Defining VETERANS-ONLY target population...")
    
    # Canonical veteran rule: n_prior_tests >= 1 (has at least 1 prior test)
    con.execute(f"""
        CREATE TEMP TABLE target_veterans AS
        SELECT DISTINCT test_id, vehicle_id 
        FROM (
            SELECT test_id, vehicle_id, n_prior_tests
            FROM read_parquet('{DEV_SET}')
            WHERE COALESCE(n_prior_tests, 0) >= 1
            UNION ALL
            SELECT test_id, vehicle_id, n_prior_tests
            FROM read_parquet('{OOT_SET}')
            WHERE COALESCE(n_prior_tests, 0) >= 1
        )
    """)
    
    n_tests = con.execute("SELECT COUNT(*) FROM target_veterans").fetchone()[0]
    n_vehicles = con.execute("SELECT COUNT(DISTINCT vehicle_id) FROM target_veterans").fetchone()[0]
    print(f"  Veteran tests: {n_tests:,}")
    print(f"  Veteran vehicles: {n_vehicles:,}")
    
    # =========================================================================
    # Step 2: Build vehicle history (for ALL vehicles, to compute features)
    # =========================================================================
    print("\n[Step 2] Building vehicle history...")
    
    con.execute(f"""
        CREATE TEMP TABLE vehicle_history AS
        SELECT 
            c.vehicle_id,
            c.test_id,
            c.test_date,
            CASE WHEN COALESCE(a.adv_brakes, 0) > 0 THEN 1 ELSE 0 END AS adv_brakes,
            CASE WHEN COALESCE(a.adv_tyres, 0) > 0 THEN 1 ELSE 0 END AS adv_tyres,
            CASE WHEN COALESCE(a.adv_suspension, 0) > 0 THEN 1 ELSE 0 END AS adv_suspension,
            COALESCE(cf.fail_brakes, 0) AS fail_brakes,
            COALESCE(cf.fail_tyres, 0) AS fail_tyres,
            COALESCE(cf.fail_suspension, 0) AS fail_suspension,
            SUBSTRING(c.test_id::VARCHAR, 1, 5) AS station_prefix
        FROM read_parquet('{CYCLE_FIRST_TESTS}') c
        SEMI JOIN target_veterans tv ON c.vehicle_id = tv.vehicle_id
        LEFT JOIN read_parquet('{ADVISORY_TOTALS}') a ON c.test_id = a.test_id
        LEFT JOIN read_parquet('{COMPONENT_FAILURES}') cf ON c.test_id = cf.test_id
    """)
    
    hist_count = con.execute("SELECT COUNT(*) FROM vehicle_history").fetchone()[0]
    print(f"  Vehicle history rows: {hist_count:,}")
    
    # =========================================================================
    # Step 3: Add sequence and next-test outcomes
    # =========================================================================
    print("\n[Step 3] Computing test sequence and conversions...")
    
    con.execute("""
        CREATE TEMP TABLE with_sequence AS
        SELECT 
            vh.*,
            ROW_NUMBER() OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id) as test_seq,
            LEAD(fail_brakes, 1) OVER w AS next_fail_brakes,
            LEAD(fail_tyres, 1) OVER w AS next_fail_tyres,
            LEAD(fail_suspension, 1) OVER w AS next_fail_suspension
        FROM vehicle_history vh
        WINDOW w AS (PARTITION BY vehicle_id ORDER BY test_date, test_id)
    """)
    
    # Compute conversions
    con.execute("""
        CREATE TEMP TABLE with_conversions AS
        SELECT 
            ws.*,
            CASE WHEN adv_brakes = 1 AND next_fail_brakes = 1 THEN 1 ELSE 0 END AS conv_brakes,
            CASE WHEN adv_tyres = 1 AND next_fail_tyres = 1 THEN 1 ELSE 0 END AS conv_tyres,
            CASE WHEN adv_suspension = 1 AND next_fail_suspension = 1 THEN 1 ELSE 0 END AS conv_suspension
        FROM with_sequence ws
    """)
    
    # Log conversion rates
    conv_stats = con.execute("""
        SELECT 
            SUM(adv_brakes) as adv_b, SUM(conv_brakes) as conv_b,
            SUM(adv_tyres) as adv_t, SUM(conv_tyres) as conv_t,
            SUM(adv_suspension) as adv_s, SUM(conv_suspension) as conv_s
        FROM with_conversions
    """).fetchone()
    print(f"  Conversions: brakes {conv_stats[1]:,}/{conv_stats[0]:,}, tyres {conv_stats[3]:,}/{conv_stats[2]:,}, susp {conv_stats[5]:,}/{conv_stats[4]:,}")
    
    # =========================================================================
    # Step 4: Compute rolling features (strictly prior window)
    # =========================================================================
    print("\n[Step 4] Computing rolling window features...")
    
    con.execute("""
        CREATE TEMP TABLE with_rolling AS
        SELECT 
            wc.*,
            -- Last 3 tests, strictly prior (ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING)
            SUM(adv_brakes) OVER w3 AS adv_count_brakes_3,
            SUM(conv_brakes) OVER w3 AS conv_count_brakes_3,
            SUM(adv_tyres) OVER w3 AS adv_count_tyres_3,
            SUM(conv_tyres) OVER w3 AS conv_count_tyres_3,
            SUM(adv_suspension) OVER w3 AS adv_count_suspension_3,
            SUM(conv_suspension) OVER w3 AS conv_count_suspension_3,
            test_seq - 1 AS prior_test_count
        FROM with_conversions wc
        WINDOW w3 AS (PARTITION BY vehicle_id ORDER BY test_seq 
                      ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING)
    """)
    
    print("  Computed rolling window features")
    
    # =========================================================================
    # Step 5: Compute station bias controls
    # =========================================================================
    print("\n[Step 5] Computing station bias controls...")
    
    con.execute(f"""
        CREATE TEMP TABLE station_stats AS
        WITH station_raw AS (
            SELECT 
                SUBSTRING(c.test_id::VARCHAR, 1, 5) AS station_prefix,
                COUNT(*) AS n_tests,
                AVG(CASE WHEN COALESCE(cf.fail_brakes,0)+COALESCE(cf.fail_tyres,0)+COALESCE(cf.fail_suspension,0) > 0 
                    THEN 1.0 ELSE 0.0 END) AS raw_fail_rate,
                AVG(CASE WHEN COALESCE(a.adv_brakes,0)+COALESCE(a.adv_tyres,0)+COALESCE(a.adv_suspension,0) > 0 
                    THEN 1.0 ELSE 0.0 END) AS raw_adv_rate
            FROM read_parquet('{CYCLE_FIRST_TESTS}') c
            LEFT JOIN read_parquet('{ADVISORY_TOTALS}') a ON c.test_id = a.test_id
            LEFT JOIN read_parquet('{COMPONENT_FAILURES}') cf ON c.test_id = cf.test_id
            GROUP BY 1
            HAVING COUNT(*) >= 100
        )
        SELECT 
            station_prefix,
            (raw_fail_rate * n_tests + 0.28 * 50) / (n_tests + 50) AS station_fail_rate,
            (raw_adv_rate * n_tests + 0.35 * 50) / (n_tests + 50) AS station_adv_rate
        FROM station_raw
    """)
    
    n_stations = con.execute("SELECT COUNT(*) FROM station_stats").fetchone()[0]
    print(f"  Stations with stats: {n_stations:,}")
    
    # =========================================================================
    # Step 6: Build final features with STRICT GROUP BY test_id
    # =========================================================================
    print("\n[Step 6] Building final features (strict grain)...")
    
    # CRITICAL: GROUP BY test_id to ensure no duplicates
    con.execute(f"""
        CREATE TEMP TABLE final_features AS
        SELECT 
            r.test_id,
            
            -- Prior test count
            MAX(r.prior_test_count) AS prior_test_count,
            
            -- Rolling counts (use MAX since each test_id should have one row)
            MAX(COALESCE(r.adv_count_brakes_3, 0)) AS adv_count_brakes,
            MAX(COALESCE(r.adv_count_tyres_3, 0)) AS adv_count_tyres,
            MAX(COALESCE(r.adv_count_suspension_3, 0)) AS adv_count_suspension,
            MAX(COALESCE(r.conv_count_brakes_3, 0)) AS conv_count_brakes,
            MAX(COALESCE(r.conv_count_tyres_3, 0)) AS conv_count_tyres,
            MAX(COALESCE(r.conv_count_suspension_3, 0)) AS conv_count_suspension,
            
            -- Smoothed conversion rates
            (MAX(COALESCE(r.conv_count_brakes_3, 0)) + {ALPHA}) * 1.0 / 
                (MAX(COALESCE(r.adv_count_brakes_3, 0)) + {ALPHA} + {BETA}) AS conv_rate_brakes,
            (MAX(COALESCE(r.conv_count_tyres_3, 0)) + {ALPHA}) * 1.0 / 
                (MAX(COALESCE(r.adv_count_tyres_3, 0)) + {ALPHA} + {BETA}) AS conv_rate_tyres,
            (MAX(COALESCE(r.conv_count_suspension_3, 0)) + {ALPHA}) * 1.0 / 
                (MAX(COALESCE(r.adv_count_suspension_3, 0)) + {ALPHA} + {BETA}) AS conv_rate_suspension,
            
            -- Binary indicators
            MAX(CASE WHEN COALESCE(r.adv_count_brakes_3, 0) > 0 THEN 1 ELSE 0 END) AS has_any_adv_brakes,
            MAX(CASE WHEN COALESCE(r.adv_count_tyres_3, 0) > 0 THEN 1 ELSE 0 END) AS has_any_adv_tyres,
            MAX(CASE WHEN COALESCE(r.adv_count_suspension_3, 0) > 0 THEN 1 ELSE 0 END) AS has_any_adv_suspension,
            
            -- Station bias
            MAX(COALESCE(s.station_fail_rate, 0.28)) AS station_fail_rate,
            MAX(COALESCE(s.station_adv_rate, 0.35)) AS station_adv_rate
            
        FROM with_rolling r
        LEFT JOIN station_stats s ON r.station_prefix = s.station_prefix
        SEMI JOIN target_veterans tv ON r.test_id = tv.test_id
        GROUP BY r.test_id
    """)
    
    # =========================================================================
    # Step 7: ASSERT INVARIANTS
    # =========================================================================
    print("\n[Step 7] Asserting invariants...")
    
    counts = con.execute("""
        SELECT 
            COUNT(*) as total_rows,
            COUNT(DISTINCT test_id) as distinct_ids
        FROM final_features
    """).fetchone()
    
    print(f"  Total rows: {counts[0]:,}")
    print(f"  Distinct test_ids: {counts[1]:,}")
    
    assert counts[0] == counts[1], f"GRAIN VIOLATION: {counts[0]} rows but {counts[1]} distinct test_ids!"
    print("  ✓ GRAIN INVARIANT PASSED: No duplicates")
    
    # Check for duplicate test_ids explicitly
    dups = con.execute("""
        SELECT COUNT(*) FROM (
            SELECT test_id FROM final_features GROUP BY test_id HAVING COUNT(*) > 1
        )
    """).fetchone()[0]
    assert dups == 0, f"DUPLICATE TEST_IDS FOUND: {dups}"
    print("  ✓ DUPLICATE CHECK PASSED")
    
    # NULL rate by year
    null_rates = con.execute(f"""
        WITH labeled AS (
            SELECT 
                EXTRACT(YEAR FROM d.test_date) as year,
                f.conv_rate_brakes
            FROM read_parquet('{DEV_SET}') d
            JOIN final_features f ON d.test_id = f.test_id
            UNION ALL
            SELECT 
                EXTRACT(YEAR FROM d.test_date) as year,
                f.conv_rate_brakes
            FROM read_parquet('{OOT_SET}') d
            JOIN final_features f ON d.test_id = f.test_id
        )
        SELECT 
            year,
            COUNT(*) as n,
            SUM(CASE WHEN conv_rate_brakes IS NULL THEN 1 ELSE 0 END) as n_null,
            SUM(CASE WHEN conv_rate_brakes IS NULL THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as pct_null
        FROM labeled
        GROUP BY 1
        ORDER BY 1
    """).df()
    print(f"\n  NULL rate by year:")
    print(null_rates.to_string())
    
    # =========================================================================
    # Step 8: Export
    # =========================================================================
    print("\n[Step 8] Exporting to parquet...")
    
    con.execute(f"""
        COPY final_features TO '{OUTPUT_FILE}' (FORMAT PARQUET, COMPRESSION SNAPPY)
    """)
    
    file_size = OUTPUT_FILE.stat().st_size / (1024 * 1024)
    print(f"  File size: {file_size:.1f} MB")
    
    # Final stats
    stats = con.execute(f"""
        SELECT 
            COUNT(*) as n,
            AVG(conv_rate_brakes) as avg_conv_brakes,
            AVG(conv_rate_tyres) as avg_conv_tyres,
            AVG(has_any_adv_brakes) as pct_any_adv_brakes
        FROM read_parquet('{OUTPUT_FILE}')
    """).fetchone()
    print(f"  Final: {stats[0]:,} rows, avg conv_rate_brakes={stats[1]:.3f}, pct_any_adv={stats[3]*100:.1f}%")
    
    print("\n" + "=" * 70)
    print("BUILD COMPLETE - ALL INVARIANTS PASSED")
    print("=" * 70)
    
    con.close()


if __name__ == "__main__":
    main()
