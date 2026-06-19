#!/usr/bin/env python3
"""
Cycle History Builder - Bucket-by-Bucket Global LAG

Hybrid approach that:
1. Processes one bucket at a time (bounds memory)
2. Uses global LAG across ALL years within each bucket (no amnesia bug)
3. Uses explicit paths (no glob ambiguity)
"""

import os
import glob as glob_module
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
OUTPUT_DIR = WORK_DIR / "cycle_history_output"
FINAL_OUTPUT = WORK_DIR / "cycle_first_with_history.parquet"
TEMP_DIR = WORK_DIR / "duckdb_tmp"

NUM_BUCKETS = 512


def check_disk_space(min_gb=3):
    """Fail fast if disk space is too low."""
    import shutil
    free_gb = shutil.disk_usage("/").free / (1024**3)
    if free_gb < min_gb:
        raise RuntimeError(f"ABORT: Only {free_gb:.1f}GB free (need {min_gb}GB)")
    return free_gb


def get_duckdb_connection():
    """Create DuckDB connection with memory-safe settings."""
    conn = duckdb.connect()
    conn.execute("SET threads = 4")
    conn.execute("SET memory_limit = '2GB'")
    conn.execute(f"SET temp_directory = '{TEMP_DIR}'")
    conn.execute("SET preserve_insertion_order = false")
    return conn


def process_bucket_optimized(bucket_id: int) -> str:
    """
    Process a single bucket with global LAG across ALL years.
    
    This avoids the amnesia bug by computing LAG on the entire
    vehicle history within the bucket, not year-by-year.
    """
    conn = get_duckdb_connection()
    
    # Target the specific bucket folder directly
    bucket_path = EVENTS_LAKE / f"bucket={bucket_id}"
    
    # Fail fast if bucket doesn't exist
    if not bucket_path.exists():
        conn.close()
        return None
    
    # Check if bucket has any data
    parquet_files = list(bucket_path.rglob("*.parquet"))
    if not parquet_files:
        conn.close()
        return None
    
    output_file = OUTPUT_DIR / f"bucket_{bucket_id:04d}.parquet"
    
    # Single global LAG across all years for this bucket
    query = f"""
    WITH bucket_data AS (
        SELECT 
            test_id, vehicle_id, test_date, test_result, test_mileage, postcode_area, year
        FROM read_parquet('{bucket_path}/**/*.parquet') 
    )
    SELECT
        test_id,
        vehicle_id,
        test_date,
        CASE test_result WHEN 'P' THEN 'PASS' WHEN 'F' THEN 'FAIL' ELSE 'OTHER' END as outcome,
        test_mileage,
        postcode_area,
        year,
        -- Compute LAG across ALL years (no amnesia)
        LAG(test_date) OVER w AS prev_test_date,
        LAG(test_mileage) OVER w AS prev_mileage,
        LAG(test_result) OVER w AS prev_result
    FROM bucket_data
    WINDOW w AS (PARTITION BY vehicle_id ORDER BY test_date, test_id)
    """
    
    try:
        conn.execute(f"""
            COPY ({query}) TO '{output_file}' 
            (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """)
        conn.close()
        return str(output_file)
    except Exception as e:
        logger.error(f"Bucket {bucket_id} failed: {e}")
        conn.close()
        return None


def build_cycle_history():
    """Build cycle history using bucket-by-bucket global LAG."""
    start_time = datetime.now()
    
    logger.info("=" * 60)
    logger.info("CYCLE HISTORY BUILDER (Bucket-by-Bucket Global LAG)")
    logger.info("=" * 60)
    logger.info(f"Events Lake: {EVENTS_LAKE}")
    logger.info(f"Output Dir: {OUTPUT_DIR}")
    
    # Ensure directories exist
    TEMP_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
    
    free_gb = check_disk_space(min_gb=3)
    logger.info(f"Disk: {free_gb:.1f}GB free")
    
    # Discover buckets
    bucket_dirs = sorted([d for d in EVENTS_LAKE.iterdir() if d.is_dir() and d.name.startswith("bucket=")])
    logger.info(f"Found {len(bucket_dirs)} buckets")
    
    # Process each bucket
    successful = 0
    failed = 0
    output_files = []
    
    for i, bucket_dir in enumerate(bucket_dirs):
        bucket_id = int(bucket_dir.name.split("=")[1])
        
        if i % 50 == 0:
            free_gb = check_disk_space(min_gb=2)
            logger.info(f"Processing bucket {i}/{len(bucket_dirs)} (disk: {free_gb:.1f}GB)...")
        
        result = process_bucket_optimized(bucket_id)
        if result:
            output_files.append(result)
            successful += 1
        else:
            failed += 1
    
    logger.info(f"\nProcessed: {successful} successful, {failed} empty/failed")
    
    # Merge all bucket outputs
    logger.info("\nMerging bucket outputs...")
    conn = get_duckdb_connection()
    
    output_glob = str(OUTPUT_DIR / "bucket_*.parquet")
    
    conn.execute(f"""
        COPY (
            SELECT
                test_id,
                vehicle_id,
                test_date,
                outcome,
                test_mileage,
                postcode_area,
                year,
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
                CASE WHEN prev_test_date IS NOT NULL THEN 1 ELSE 0 END as has_prior_test
            FROM read_parquet('{output_glob}')
        ) TO '{FINAL_OUTPUT}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    
    # Statistics
    logger.info("Computing statistics...")
    stats = conn.execute(f"""
        SELECT 
            COUNT(*) as total,
            SUM(has_prior_test) as with_prior,
            COUNT(DISTINCT vehicle_id) as vehicles
        FROM read_parquet('{FINAL_OUTPUT}')
    """).fetchone()
    
    total, with_prior, vehicles = stats
    coverage_pct = 100.0 * with_prior / total if total > 0 else 0
    
    logger.info(f"\n{'='*60}")
    logger.info("BUILD COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"Output: {FINAL_OUTPUT}")
    logger.info(f"Total records: {total:,}")
    logger.info(f"Distinct vehicles: {vehicles:,}")
    logger.info(f"History coverage: {coverage_pct:.1f}% ({with_prior:,} with prior)")
    
    # Yearly breakdown
    yearly_stats = conn.execute(f"""
        SELECT 
            year,
            COUNT(*) as n,
            100.0 * SUM(has_prior_test) / COUNT(*) as pct_with_prior
        FROM read_parquet('{FINAL_OUTPUT}')
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
        FROM read_parquet('{FINAL_OUTPUT}')
        GROUP BY prev_cycle_outcome_band
        ORDER BY n DESC
    """).fetchdf()
    
    logger.info("\nOutcome Band Distribution:")
    logger.info(outcome_stats.to_string(index=False))
    
    elapsed = datetime.now() - start_time
    logger.info(f"\nTime: {elapsed.total_seconds():.1f}s ({elapsed.total_seconds()/60:.1f} min)")
    
    # Cleanup intermediate files
    logger.info("\nCleaning up intermediate files...")
    for f in OUTPUT_DIR.glob("bucket_*.parquet"):
        f.unlink()
    
    conn.close()
    
    return total


if __name__ == "__main__":
    count = build_cycle_history()
    print(f"\n✓ Built cycle history with {count:,} records")
