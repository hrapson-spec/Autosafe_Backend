"""
Build Advisory Conversion Ratio Features
=========================================

Key insight: Not raw advisory counts, but how often advisories convert to failures.
A vehicle with 3 advisories that all converted is higher risk than one with 10 that didn't.

Features per component (brakes, tyres, suspension):
- adv_count_{comp,k}: Advisory count in last k tests (k=2,3)
- adv_to_fail_{comp,k}: How many of those advisories led to failure in t+1
- adv_conversion_rate_{comp}: Smoothed rate = (adv_to_fail + α) / (adv_count + α + β)
- has_any_adv_{comp}: Binary indicator

Station bias controls:
- station_fail_rate_smoothed: Station baseline failure rate
- station_adv_rate_smoothed: Station advisory verbosity
- These allow model to discount "tester verbosity" from true signal

Created: 2026-01-06
"""

import duckdb
from pathlib import Path
from datetime import datetime
import numpy as np

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

OUTPUT_FILE = WORK_DIR / "advisory_conversion_features.parquet"

# Smoothing priors for conversion rate (Beta prior equivalent)
ALPHA = 1  # Prior successes (conversions)
BETA = 5   # Prior trials (ensures shrinkage toward ~17% base rate)


def main():
    print("=" * 70)
    print("BUILD ADVISORY CONVERSION FEATURES")
    print("=" * 70)
    print(f"Started: {datetime.now().isoformat()}")
    print(f"Smoothing: α={ALPHA}, β={BETA}")
    
    con = duckdb.connect()
    con.execute("SET memory_limit='4GB'")
    con.execute("SET threads=4")
    
    # =========================================================================
    # Step 1: Create target test_ids
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
    
    n_tests = con.execute("SELECT COUNT(*) FROM target_tests").fetchone()[0]
    n_vehicles = con.execute("SELECT COUNT(DISTINCT vehicle_id) FROM target_tests").fetchone()[0]
    print(f"  Target tests: {n_tests:,}")
    print(f"  Target vehicles: {n_vehicles:,}")
    
    # =========================================================================
    # Step 2: Build vehicle history with advisories and failures
    # =========================================================================
    print("\n[Step 2] Building vehicle history...")
    
    # Get all tests for target vehicles with advisory and failure flags
    con.execute(f"""
        CREATE TEMP TABLE vehicle_history AS
        SELECT 
            c.vehicle_id,
            c.test_id,
            c.test_date,
            -- Advisory flags (1 if any advisory for component)
            CASE WHEN COALESCE(a.adv_brakes, 0) > 0 THEN 1 ELSE 0 END AS adv_brakes,
            CASE WHEN COALESCE(a.adv_tyres, 0) > 0 THEN 1 ELSE 0 END AS adv_tyres,
            CASE WHEN COALESCE(a.adv_suspension, 0) > 0 THEN 1 ELSE 0 END AS adv_suspension,
            -- Failure flags
            COALESCE(cf.fail_brakes, 0) AS fail_brakes,
            COALESCE(cf.fail_tyres, 0) AS fail_tyres,
            COALESCE(cf.fail_suspension, 0) AS fail_suspension,
            -- Station for bias control
            SUBSTRING(c.test_id::VARCHAR, 1, 5) AS station_prefix
        FROM read_parquet('{CYCLE_FIRST_TESTS}') c
        SEMI JOIN target_tests tt ON c.vehicle_id = tt.vehicle_id
        LEFT JOIN read_parquet('{ADVISORY_TOTALS}') a ON c.test_id = a.test_id
        LEFT JOIN read_parquet('{COMPONENT_FAILURES}') cf ON c.test_id = cf.test_id
    """)
    
    hist_count = con.execute("SELECT COUNT(*) FROM vehicle_history").fetchone()[0]
    print(f"  Vehicle history rows: {hist_count:,}")
    
    # =========================================================================
    # Step 3: Add test sequence and next-test failure flags
    # =========================================================================
    print("\n[Step 3] Computing test sequence and next-test failures...")
    
    con.execute("""
        CREATE TEMP TABLE with_sequence AS
        SELECT 
            vh.*,
            ROW_NUMBER() OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id) as test_seq,
            -- Next test failures (for conversion tracking)
            LEAD(fail_brakes, 1) OVER w AS next_fail_brakes,
            LEAD(fail_tyres, 1) OVER w AS next_fail_tyres,
            LEAD(fail_suspension, 1) OVER w AS next_fail_suspension
        FROM vehicle_history vh
        WINDOW w AS (PARTITION BY vehicle_id ORDER BY test_date, test_id)
    """)
    
    print("  Added test sequence and next-test failures")
    
    # =========================================================================
    # Step 4: Compute advisory conversion indicators
    # =========================================================================
    print("\n[Step 4] Computing advisory conversion indicators...")
    
    # A conversion occurs when: advisory at test t AND failure at test t+1
    con.execute("""
        CREATE TEMP TABLE with_conversions AS
        SELECT 
            ws.*,
            -- Conversion: advisory at t AND failure at t+1 (same component)
            CASE WHEN adv_brakes = 1 AND next_fail_brakes = 1 THEN 1 ELSE 0 END AS conv_brakes,
            CASE WHEN adv_tyres = 1 AND next_fail_tyres = 1 THEN 1 ELSE 0 END AS conv_tyres,
            CASE WHEN adv_suspension = 1 AND next_fail_suspension = 1 THEN 1 ELSE 0 END AS conv_suspension
        FROM with_sequence ws
    """)
    
    # Check conversion rates
    conv_stats = con.execute("""
        SELECT 
            SUM(adv_brakes) as total_adv_brakes,
            SUM(conv_brakes) as total_conv_brakes,
            SUM(adv_tyres) as total_adv_tyres,
            SUM(conv_tyres) as total_conv_tyres,
            SUM(adv_suspension) as total_adv_suspension,
            SUM(conv_suspension) as total_conv_suspension
        FROM with_conversions
    """).fetchone()
    
    print(f"  Brakes: {conv_stats[1]:,}/{conv_stats[0]:,} conversions ({conv_stats[1]/max(conv_stats[0],1)*100:.1f}%)")
    print(f"  Tyres: {conv_stats[3]:,}/{conv_stats[2]:,} conversions ({conv_stats[3]/max(conv_stats[2],1)*100:.1f}%)")
    print(f"  Suspension: {conv_stats[5]:,}/{conv_stats[4]:,} conversions ({conv_stats[5]/max(conv_stats[4],1)*100:.1f}%)")
    
    # =========================================================================
    # Step 5: Compute rolling window features (strictly prior)
    # =========================================================================
    print("\n[Step 5] Computing rolling window features...")
    
    # Rolling sums over last 2 and 3 prior tests
    con.execute("""
        CREATE TEMP TABLE with_rolling AS
        SELECT 
            wc.*,
            -- Last 2 tests (t-1, t-2) - strictly prior, excluding current
            SUM(adv_brakes) OVER (PARTITION BY vehicle_id ORDER BY test_seq 
                ROWS BETWEEN 2 PRECEDING AND 1 PRECEDING) AS adv_count_brakes_2,
            SUM(conv_brakes) OVER (PARTITION BY vehicle_id ORDER BY test_seq 
                ROWS BETWEEN 2 PRECEDING AND 1 PRECEDING) AS conv_count_brakes_2,
            SUM(adv_tyres) OVER (PARTITION BY vehicle_id ORDER BY test_seq 
                ROWS BETWEEN 2 PRECEDING AND 1 PRECEDING) AS adv_count_tyres_2,
            SUM(conv_tyres) OVER (PARTITION BY vehicle_id ORDER BY test_seq 
                ROWS BETWEEN 2 PRECEDING AND 1 PRECEDING) AS conv_count_tyres_2,
            SUM(adv_suspension) OVER (PARTITION BY vehicle_id ORDER BY test_seq 
                ROWS BETWEEN 2 PRECEDING AND 1 PRECEDING) AS adv_count_suspension_2,
            SUM(conv_suspension) OVER (PARTITION BY vehicle_id ORDER BY test_seq 
                ROWS BETWEEN 2 PRECEDING AND 1 PRECEDING) AS conv_count_suspension_2,
            -- Last 3 tests
            SUM(adv_brakes) OVER (PARTITION BY vehicle_id ORDER BY test_seq 
                ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING) AS adv_count_brakes_3,
            SUM(conv_brakes) OVER (PARTITION BY vehicle_id ORDER BY test_seq 
                ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING) AS conv_count_brakes_3,
            SUM(adv_tyres) OVER (PARTITION BY vehicle_id ORDER BY test_seq 
                ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING) AS adv_count_tyres_3,
            SUM(conv_tyres) OVER (PARTITION BY vehicle_id ORDER BY test_seq 
                ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING) AS conv_count_tyres_3,
            SUM(adv_suspension) OVER (PARTITION BY vehicle_id ORDER BY test_seq 
                ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING) AS adv_count_suspension_3,
            SUM(conv_suspension) OVER (PARTITION BY vehicle_id ORDER BY test_seq 
                ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING) AS conv_count_suspension_3,
            -- Count prior tests
            test_seq - 1 AS prior_test_count
        FROM with_conversions wc
    """)
    
    print("  Computed rolling window sums")
    
    # =========================================================================
    # Step 6: Compute station-level bias controls (using all history)
    # =========================================================================
    print("\n[Step 6] Computing station bias controls...")
    
    # Get station-level stats with shrinkage
    # Use all tests (not just target vehicles) for more stable estimates
    con.execute(f"""
        CREATE TEMP TABLE station_stats AS
        WITH station_raw AS (
            SELECT 
                SUBSTRING(c.test_id::VARCHAR, 1, 5) AS station_prefix,
                COUNT(*) AS n_tests,
                AVG(CASE WHEN cf.fail_brakes = 1 OR cf.fail_tyres = 1 OR cf.fail_suspension = 1 THEN 1.0 ELSE 0.0 END) AS raw_fail_rate,
                AVG(CASE WHEN COALESCE(a.adv_brakes, 0) + COALESCE(a.adv_tyres, 0) + COALESCE(a.adv_suspension, 0) > 0 THEN 1.0 ELSE 0.0 END) AS raw_adv_rate
            FROM read_parquet('{CYCLE_FIRST_TESTS}') c
            LEFT JOIN read_parquet('{ADVISORY_TOTALS}') a ON c.test_id = a.test_id
            LEFT JOIN read_parquet('{COMPONENT_FAILURES}') cf ON c.test_id = cf.test_id
            GROUP BY 1
            HAVING COUNT(*) >= 100
        )
        SELECT 
            station_prefix,
            n_tests,
            -- Shrink toward global mean with K=50
            (raw_fail_rate * n_tests + 0.28 * 50) / (n_tests + 50) AS station_fail_rate,
            (raw_adv_rate * n_tests + 0.35 * 50) / (n_tests + 50) AS station_adv_rate
        FROM station_raw
    """)
    
    n_stations = con.execute("SELECT COUNT(*) FROM station_stats").fetchone()[0]
    print(f"  Computed stats for {n_stations:,} stations")
    
    # =========================================================================
    # Step 7: Build final features
    # =========================================================================
    print("\n[Step 7] Building final features...")
    
    con.execute(f"""
        COPY (
            SELECT 
                r.test_id,
                
                -- Prior test count
                r.prior_test_count,
                
                -- Rolling advisory counts (last 3, most predictive window)
                COALESCE(r.adv_count_brakes_3, 0) AS adv_count_brakes,
                COALESCE(r.adv_count_tyres_3, 0) AS adv_count_tyres,
                COALESCE(r.adv_count_suspension_3, 0) AS adv_count_suspension,
                
                -- Rolling conversion counts
                COALESCE(r.conv_count_brakes_3, 0) AS conv_count_brakes,
                COALESCE(r.conv_count_tyres_3, 0) AS conv_count_tyres,
                COALESCE(r.conv_count_suspension_3, 0) AS conv_count_suspension,
                
                -- Smoothed conversion rates: (conv + α) / (adv + α + β)
                (COALESCE(r.conv_count_brakes_3, 0) + {ALPHA}) * 1.0 / 
                    (COALESCE(r.adv_count_brakes_3, 0) + {ALPHA} + {BETA}) AS conv_rate_brakes,
                (COALESCE(r.conv_count_tyres_3, 0) + {ALPHA}) * 1.0 / 
                    (COALESCE(r.adv_count_tyres_3, 0) + {ALPHA} + {BETA}) AS conv_rate_tyres,
                (COALESCE(r.conv_count_suspension_3, 0) + {ALPHA}) * 1.0 / 
                    (COALESCE(r.adv_count_suspension_3, 0) + {ALPHA} + {BETA}) AS conv_rate_suspension,
                
                -- Binary indicators
                CASE WHEN COALESCE(r.adv_count_brakes_3, 0) > 0 THEN 1 ELSE 0 END AS has_any_adv_brakes,
                CASE WHEN COALESCE(r.adv_count_tyres_3, 0) > 0 THEN 1 ELSE 0 END AS has_any_adv_tyres,
                CASE WHEN COALESCE(r.adv_count_suspension_3, 0) > 0 THEN 1 ELSE 0 END AS has_any_adv_suspension,
                
                -- Station bias controls
                COALESCE(s.station_fail_rate, 0.28) AS station_fail_rate,
                COALESCE(s.station_adv_rate, 0.35) AS station_adv_rate
                
            FROM with_rolling r
            LEFT JOIN station_stats s ON r.station_prefix = s.station_prefix
            SEMI JOIN target_tests tt ON r.test_id = tt.test_id
        ) TO '{OUTPUT_FILE}' (FORMAT PARQUET, COMPRESSION SNAPPY)
    """)
    
    print("  Written to parquet")
    
    # =========================================================================
    # Step 8: Validate
    # =========================================================================
    print("\n[Step 8] Validation...")
    
    stats = con.execute(f"""
        SELECT 
            COUNT(*) as n_rows,
            AVG(conv_rate_brakes) as avg_conv_brakes,
            AVG(conv_rate_tyres) as avg_conv_tyres,
            AVG(conv_rate_suspension) as avg_conv_susp,
            AVG(has_any_adv_brakes) as pct_any_adv_brakes,
            AVG(has_any_adv_tyres) as pct_any_adv_tyres,
            AVG(station_fail_rate) as avg_station_fail,
            AVG(station_adv_rate) as avg_station_adv
        FROM read_parquet('{OUTPUT_FILE}')
    """).fetchone()
    
    print(f"  Rows: {stats[0]:,}")
    print(f"  Avg conversion rates: brakes={stats[1]:.3f}, tyres={stats[2]:.3f}, susp={stats[3]:.3f}")
    print(f"  Has any advisory: brakes={stats[4]*100:.1f}%, tyres={stats[5]*100:.1f}%")
    print(f"  Station rates: fail={stats[6]:.3f}, adv={stats[7]:.3f}")
    
    file_size = OUTPUT_FILE.stat().st_size / (1024 * 1024)
    print(f"  File size: {file_size:.1f} MB")
    
    print("\n" + "=" * 70)
    print("ADVISORY CONVERSION FEATURES BUILD COMPLETE")
    print("=" * 70)
    print(f"  Output: {OUTPUT_FILE}")
    print(f"  Completed: {datetime.now().isoformat()}")
    
    con.close()


if __name__ == "__main__":
    main()
