"""
Build Time-Sliced Empirical Bayes Priors
=========================================

Strictly prior-based EB priors to prevent leakage and handle concept drift.
Aggregates cycle history by month and computes cumulative priors for each `asof_month`.

Methodology:
1. Segment: Model + Age Band + Mileage Band
2. Global/Make/Segment Aggregation by Month
3. Cumulative Sums (Window: UNBOUNDED PRECEDING AND 1 PRECEDING)
4. Hierarchical Smoothing
5. Materialization

Created: 2026-01-07
"""

import duckdb
import argparse
from pathlib import Path
from datetime import datetime

# ============================================================================
# Configuration
# ============================================================================

PROJECT_ROOT = Path("/Users/henrirapson/autosafe_work")
CYCLE_HISTORY = PROJECT_ROOT / "cycle_history_dataset/cycle_history_full_combined_with_2023.parquet"
OUTPUT_FILE = PROJECT_ROOT / "time_sliced_eb_priors_dual.parquet"

# Smoothing Parameters (Baseline)
K_GLOBAL = 10
K_MAKE = 5


def main():
    print("=" * 70)
    print("BUILD TIME-SLICED EB PRIORS")
    print("=" * 70)
    print(f"Started: {datetime.now().isoformat()}")
    
    con = duckdb.connect()
    con.execute("SET memory_limit='8GB'")
    con.execute("SET threads=4")
    
    # =========================================================================
    # Step 1: Aggregate by Month (Iterative / Base Layer)
    # =========================================================================
    print("\n[Step 1] Aggregating by Month (Iterative)...")
    
    INTERMEDIATE_DIR = PROJECT_ROOT / "temp_monthly_aggs"
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    
    # Clean up old files
    import shutil
    # shutil.rmtree(INTERMEDIATE_DIR)
    # INTERMEDIATE_DIR.mkdir()

    for year in range(2005, 2025):
        print(f"  Processing {year}...")
        year_file = INTERMEDIATE_DIR / f"monthly_agg_{year}.parquet"
        
        con.execute(f"""
            COPY (
                WITH raw_data AS (
                    SELECT
                        test_date,
                        make,
                        make || ' ' || model as model_id,
                        CASE WHEN outcome = 'FAIL' THEN 1 ELSE 0 END as is_fail,
                        -- Age Band
                        CASE
                            WHEN first_use_date IS NULL OR test_date IS NULL THEN 'Unknown'
                            WHEN (test_date - first_use_date) / 365.25 < 3 THEN '0-2'
                            WHEN (test_date - first_use_date) / 365.25 < 6 THEN '3-5'
                            WHEN (test_date - first_use_date) / 365.25 < 11 THEN '6-10'
                            WHEN (test_date - first_use_date) / 365.25 < 16 THEN '11-15'
                            ELSE '15+'
                        END AS age_band,
                        -- Mileage Band
                        CASE
                            WHEN test_mileage IS NULL OR test_mileage < 0 THEN 'Unknown'
                            WHEN test_mileage < 30000 THEN '0-30k'
                            WHEN test_mileage < 60000 THEN '30k-60k'
                            WHEN test_mileage < 100000 THEN '60k-100k'
                            ELSE '100k+'
                        END AS mileage_band
                    FROM read_parquet('{CYCLE_HISTORY}')
                    WHERE EXTRACT(YEAR FROM test_date) = {year}
                )
                SELECT 
                    DATE_TRUNC('month', test_date) as month,
                    make,
                    model_id,
                    age_band,
                    mileage_band,
                    COUNT(*) as n_tests,
                    SUM(is_fail) as n_fails
                FROM raw_data
                GROUP BY 1, 2, 3, 4, 5
            ) TO '{year_file}' (FORMAT PARQUET)
        """)
        
    print("  Aggregations Complete. Loading intermediate files...")
    con.execute(f"CREATE TEMP TABLE monthly_aggs AS SELECT * FROM read_parquet('{INTERMEDIATE_DIR}/*.parquet')")
            
    n_rows = con.execute("SELECT COUNT(*) FROM monthly_aggs").fetchone()[0]
    print(f"  Monthly segments: {n_rows:,}")

    
    # =========================================================================
    # Step 2: Compute Cumulative Priors (Strictly Prior)
    # =========================================================================
    print("\n[Step 2] Computing Cumulative Priors (The 'As-Of' Step)...")
    
    # We need to fan out for every month.
    # Strategy:
    # 1. Compute Global cumulatives per month
    # 2. Compute Make cumulatives per month
    # 3. Compute Segment cumulatives per month
    # All using ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    
    # 2a. Global Priors (Dual Window)
    con.execute("""
        CREATE TEMP TABLE global_priors AS
        WITH global_monthly AS (
            SELECT month, SUM(n_tests) as tests, SUM(n_fails) as fails
            FROM monthly_aggs
            GROUP BY 1
        )
        SELECT
            month as asof_month,
            SUM(tests) OVER w_long AS glob_tests_long,
            SUM(fails) OVER w_long AS glob_fails_long,
            SUM(tests) OVER w_short AS glob_tests_short,
            SUM(fails) OVER w_short AS glob_fails_short
        FROM global_monthly
        WINDOW 
            w_long AS (ORDER BY month ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
            w_short AS (ORDER BY month ROWS BETWEEN 6 PRECEDING AND 1 PRECEDING)
    """)
    
    # 2b. Make Priors (Dual Window)
    con.execute("""
        CREATE TEMP TABLE make_priors AS
        WITH make_monthly AS (
            SELECT month, make, SUM(n_tests) as tests, SUM(n_fails) as fails
            FROM monthly_aggs
            GROUP BY 1, 2
        )
        SELECT
            month as asof_month,
            make,
            SUM(tests) OVER w_long AS make_tests_long,
            SUM(fails) OVER w_long AS make_fails_long,
            SUM(tests) OVER w_short AS make_tests_short,
            SUM(fails) OVER w_short AS make_fails_short
        FROM make_monthly
        WINDOW 
            w_long AS (PARTITION BY make ORDER BY month ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
            w_short AS (PARTITION BY make ORDER BY month ROWS BETWEEN 6 PRECEDING AND 1 PRECEDING)
    """)
    
    # 2c. Segment Priors (Dual Window)
    con.execute("""
        CREATE TEMP TABLE segment_priors AS
        SELECT
            month as asof_month,
            model_id,
            age_band,
            mileage_band,
            SUM(n_tests) OVER w_long AS seg_tests_long,
            SUM(n_fails) OVER w_long AS seg_fails_long,
            SUM(n_tests) OVER w_short AS seg_tests_short,
            SUM(n_fails) OVER w_short AS seg_fails_short
        FROM monthly_aggs
        WINDOW 
            w_long AS (PARTITION BY model_id, age_band, mileage_band ORDER BY month ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
            w_short AS (PARTITION BY model_id, age_band, mileage_band ORDER BY month ROWS BETWEEN 6 PRECEDING AND 1 PRECEDING)
    """)
    
    # =========================================================================
    # Step 3: Join and Calculate Dual EB Rates
    # =========================================================================
    print("\n[Step 3] Calculating Time-Sliced EB Rates (Dual Prior)...")
    
    con.execute(f"""
        CREATE TEMP TABLE eb_calculated AS
        SELECT
            s.asof_month,
            s.model_id, -- Join Key 1
            s.age_band, -- Join Key 2
            s.mileage_band, -- Join Key 3
            
            -- Debug counts
            COALESCE(s.seg_tests_long, 0) as seg_tests_long,
            COALESCE(s.seg_tests_short, 0) as seg_tests_short,
            
            -- LONG TERM (Stable)
            -- 1. Global Rate Long
            COALESCE(g.glob_fails_long, 0) * 1.0 / NULLIF(g.glob_tests_long, 0) as global_rate_long,
            -- 2. Make Rate Long
            (COALESCE(m.make_fails_long, 0) + {K_GLOBAL} * (COALESCE(g.glob_fails_long, 0) * 1.0 / NULLIF(g.glob_tests_long, 0))) 
            / (COALESCE(m.make_tests_long, 0) + {K_GLOBAL}) as make_rate_long,
            -- 3. Segment Rate Long
            (COALESCE(s.seg_fails_long, 0) + {K_MAKE} * 
                ((COALESCE(m.make_fails_long, 0) + {K_GLOBAL} * (COALESCE(g.glob_fails_long, 0) * 1.0 / NULLIF(g.glob_tests_long, 0))) 
                / (COALESCE(m.make_tests_long, 0) + {K_GLOBAL}))
            ) 
            / (COALESCE(s.seg_tests_long, 0) + {K_MAKE}) as eb_segment_long,
            
            -- SHORT TERM (Responsive)
            -- 1. Global Rate Short
            COALESCE(g.glob_fails_short, 0) * 1.0 / NULLIF(g.glob_tests_short, 0) as global_rate_short,
            -- 2. Make Rate Short (Shrunk to Global Short)
            (COALESCE(m.make_fails_short, 0) + {K_GLOBAL} * (COALESCE(g.glob_fails_short, 0) * 1.0 / NULLIF(g.glob_tests_short, 0))) 
            / (COALESCE(m.make_tests_short, 0) + {K_GLOBAL}) as make_rate_short,
            -- 3. Segment Rate Short (Shrunk to Make Short)
            (COALESCE(s.seg_fails_short, 0) + {K_MAKE} * 
                ((COALESCE(m.make_fails_short, 0) + {K_GLOBAL} * (COALESCE(g.glob_fails_short, 0) * 1.0 / NULLIF(g.glob_tests_short, 0))) 
                / (COALESCE(m.make_tests_short, 0) + {K_GLOBAL}))
            ) 
            / (COALESCE(s.seg_tests_short, 0) + {K_MAKE}) as eb_segment_short
            
        FROM segment_priors s
        JOIN monthly_aggs ma ON s.asof_month = ma.month 
             AND s.model_id = ma.model_id 
             AND s.age_band = ma.age_band 
             AND s.mileage_band = ma.mileage_band
        JOIN make_priors m ON s.asof_month = m.asof_month AND ma.make = m.make
        JOIN global_priors g ON s.asof_month = g.asof_month
        WHERE s.asof_month >= '2016-01-01'
    """)
    
    # =========================================================================
    # Step 4: Materialize
    # =========================================================================
    print("\n[Step 4] Materializing...")
    
    con.execute(f"""
        COPY eb_calculated TO '{OUTPUT_FILE}' (FORMAT PARQUET, COMPRESSION SNAPPY)
    """)
    
    # Stats
    stats = con.execute("SELECT MIN(asof_month), MAX(asof_month), COUNT(*) FROM eb_calculated").fetchone()
    print(f"  Range: {stats[0]} to {stats[1]}")
    print(f"  Rows: {stats[2]:,}")
    
    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)
    con.close()

if __name__ == "__main__":
    main()
