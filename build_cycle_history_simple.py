#!/usr/bin/env python3
"""
Cycle History Builder - Single-Pass Global LAG

Uses DuckDB's out-of-core processing to compute prior test history
with a single global LAG() window function. No buckets, no state management.

This approach:
- Avoids the "amnesia bug" where vehicles skipping years lose history
- Leverages DuckDB's spill-to-disk for large datasets
- Is 10x simpler than bucket-partitioned approach
"""

import os
import logging
from pathlib import Path
from datetime import datetime
import duckdb

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("build_cycle_history.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
WORK_DIR = Path("/Users/henrirapson/autosafe_work")
EVENTS_LAKE = WORK_DIR / "events_lake"
OUTPUT_PATH = WORK_DIR / "cycle_first_with_history.parquet"
TEMP_DIR = WORK_DIR / "duckdb_tmp"


def check_disk_space(min_gb=10):
    """Fail fast if disk space is too low."""
    import shutil
    free_gb = shutil.disk_usage("/").free / (1024**3)
    if free_gb < min_gb:
        raise RuntimeError(f"ABORT: Only {free_gb:.1f}GB free (need {min_gb}GB)")
    logger.info(f"Disk check: {free_gb:.1f}GB free")
    return free_gb


def build_cycle_history():
    """Build cycle history using single-pass global LAG."""
    start_time = datetime.now()
    
    logger.info("=" * 60)
    logger.info("CYCLE HISTORY BUILDER (Single-Pass Global LAG)")
    logger.info("=" * 60)
    logger.info(f"Events Lake: {EVENTS_LAKE}")
    logger.info(f"Output: {OUTPUT_PATH}")
    
    # Ensure directories exist
    TEMP_DIR.mkdir(exist_ok=True)
    
    check_disk_space(min_gb=5)
    
    # Configure DuckDB for out-of-core processing
    conn = duckdb.connect()
    conn.execute("SET threads = 4")
    conn.execute("SET memory_limit = '2GB'")
    conn.execute(f"SET temp_directory = '{TEMP_DIR}'")
    conn.execute("SET preserve_insertion_order = false")
    
    # Get event counts
    logger.info("Counting events in lake...")
    event_count = conn.execute(f"""
        SELECT COUNT(*) FROM read_parquet('{EVENTS_LAKE}/**/*.parquet')
    """).fetchone()[0]
    logger.info(f"Total events: {event_count:,}")
    
    # Check years
    years = conn.execute(f"""
        SELECT DISTINCT year FROM read_parquet('{EVENTS_LAKE}/**/*.parquet')
        ORDER BY year
    """).fetchall()
    years = [y[0] for y in years]
    logger.info(f"Years: {years}")
    
    # Build cycle history with single global LAG
    logger.info("\nComputing prior test history (single-pass global LAG)...")
    logger.info("This may take a few minutes for large datasets...")
    
    conn.execute(f"""
        COPY (
            WITH events_with_prior AS (
                SELECT 
                    test_id,
                    vehicle_id,
                    test_date,
                    test_result,
                    test_mileage,
                    postcode_area,
                    year,
                    LAG(test_date) OVER w as prev_test_date,
                    LAG(test_result) OVER w as prev_result,
                    LAG(test_mileage) OVER w as prev_mileage
                FROM read_parquet('{EVENTS_LAKE}/**/*.parquet')
                WINDOW w AS (PARTITION BY vehicle_id ORDER BY test_date, test_id)
            )
            SELECT
                test_id,
                vehicle_id,
                test_date,
                CASE test_result WHEN 'P' THEN 'PASS' WHEN 'F' THEN 'FAIL' ELSE 'OTHER' END as outcome,
                test_mileage,
                postcode_area,
                -- Prev outcome band
                CASE
                    WHEN prev_test_date IS NULL THEN 'NONE'
                    WHEN prev_result = 'P' THEN 'PASS'
                    WHEN prev_result = 'F' THEN 'FAIL'
                    ELSE 'NONE'
                END as prev_cycle_outcome_band,
                -- Days since prev
                CASE
                    WHEN prev_test_date IS NOT NULL 
                    THEN CAST(test_date - prev_test_date AS INTEGER)
                    ELSE NULL
                END as days_since_prev,
                -- Prior mileage delta
                CASE
                    WHEN prev_mileage IS NOT NULL AND test_mileage IS NOT NULL
                    THEN test_mileage - prev_mileage
                    ELSE NULL
                END as mileage_delta,
                -- Has prior
                CASE WHEN prev_test_date IS NOT NULL THEN 1 ELSE 0 END as has_prior_test,
                year
            FROM events_with_prior
        ) TO '{OUTPUT_PATH}' (FORMAT PARQUET, PARTITION_BY (year), COMPRESSION ZSTD, OVERWRITE_OR_IGNORE)
    """)
    
    logger.info("Computing statistics...")
    
    # Get output stats
    stats = conn.execute(f"""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN has_prior_test = 1 THEN 1 ELSE 0 END) as with_prior,
            COUNT(DISTINCT vehicle_id) as vehicles
        FROM read_parquet('{OUTPUT_PATH}')
    """).fetchone()
    
    total, with_prior, vehicles = stats
    coverage_pct = 100.0 * with_prior / total if total > 0 else 0
    
    logger.info(f"\n{'='*60}")
    logger.info("BUILD COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"Output: {OUTPUT_PATH}")
    logger.info(f"Total records: {total:,}")
    logger.info(f"Distinct vehicles: {vehicles:,}")
    logger.info(f"History coverage: {coverage_pct:.1f}% ({with_prior:,} with prior)")
    
    # Yearly breakdown
    yearly_stats = conn.execute(f"""
        SELECT 
            year,
            COUNT(*) as n,
            100.0 * SUM(has_prior_test) / COUNT(*) as pct_with_prior
        FROM read_parquet('{OUTPUT_PATH}')
        GROUP BY year
        ORDER BY year
    """).fetchdf()
    
    logger.info("\nYearly Coverage:")
    for _, row in yearly_stats.iterrows():
        logger.info(f"  {int(row['year'])}: {row['n']:,} tests, {row['pct_with_prior']:.1f}% with prior")
    
    # Outcome band distribution
    outcome_stats = conn.execute(f"""
        SELECT 
            prev_cycle_outcome_band,
            COUNT(*) as n,
            ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) as pct
        FROM read_parquet('{OUTPUT_PATH}')
        GROUP BY prev_cycle_outcome_band
        ORDER BY n DESC
    """).fetchdf()
    
    logger.info("\nOutcome Band Distribution:")
    logger.info(outcome_stats.to_string(index=False))
    
    elapsed = datetime.now() - start_time
    logger.info(f"\nTime: {elapsed.total_seconds():.1f}s ({elapsed.total_seconds()/60:.1f} min)")
    
    conn.close()
    
    return total


if __name__ == "__main__":
    count = build_cycle_history()
    print(f"\n✓ Built cycle history with {count:,} records using single-pass global LAG")
