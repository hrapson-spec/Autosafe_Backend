"""
Bucketed Cycle History Builder (v4 - Low Disk Mode / Streaming)

Architecture:
1. Build Events Lake (Partitioned by Bucket)
2. Streaming Process: Read Bucket -> Sort -> Write DIRECTLY to final dataset
   (Eliminates 2-3GB of intermediate temp files)
"""

import duckdb
import os
import logging
import shutil
import glob
from pathlib import Path
from datetime import datetime

# --- Configuration ---
WORK_DIR = Path("/Users/henrirapson/autosafe_work")
PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")

# Paths - Output is now a DIRECTORY, not a single file
OUTPUT_DIR = WORK_DIR / "cycle_history_dataset" 
EVENTS_LAKE_PATH = WORK_DIR / "events_lake"
TEMP_DIR = WORK_DIR / "duckdb_tmp"

# Settings
NUM_BUCKETS = 512
MIN_YEAR = 2005

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def check_disk_space(min_gb=2):
    """Abort if free space < min_gb. LOWERED threshold for emergency run."""
    free_gb = shutil.disk_usage(WORK_DIR).free / (1024**3)
    if free_gb < min_gb:
        raise RuntimeError(f"CRITICAL: Only {free_gb:.1f}GB free. Need {min_gb}GB to run safely.")
    return free_gb

# Environment-aware Google Drive path
GDRIVE_PATH = "/Users/henrirapson/Library/CloudStorage/GoogleDrive-hrapson@googlemail.com (28-12-2025 19:43)/My Drive/MOT_Data/test_results"

def get_duckdb_connection():
    conn = duckdb.connect()
    conn.execute("SET threads = 4")
    conn.execute("SET memory_limit = '2GB'")
    conn.execute(f"SET temp_directory = '{TEMP_DIR}'")
    return conn

def process_bucket_streaming(bucket_id: int):
    """Read Bucket -> Calc History -> Write DIRECTLY to Final Dataset (No Merge)"""
    conn = get_duckdb_connection()
    bucket_path = EVENTS_LAKE_PATH / f"bucket={bucket_id}"
    
    # OUTPUT: Directly to the final dataset folder
    output_file = OUTPUT_DIR / f"part-{bucket_id:04d}.parquet"
    
    if not bucket_path.exists():
        conn.close()
        return
    if output_file.exists():
        conn.close()
        return  # Resume support

    query = f"""
    WITH bucket_data AS (
        SELECT test_id, vehicle_id, test_date, test_result, test_mileage, postcode_area
        FROM read_parquet('{bucket_path}/**/*.parquet')
    ),
    history AS (
        SELECT
            *,
            LAG(test_date) OVER w AS prev_test_date,
            LAG(test_mileage) OVER w AS prev_mileage,
            LAG(test_result) OVER w AS prev_result
        FROM bucket_data
        WINDOW w AS (PARTITION BY vehicle_id ORDER BY test_date, test_id)
    )
    SELECT
        test_id, vehicle_id, test_date, 
        CASE test_result WHEN 'P' THEN 'PASS' ELSE 'FAIL' END as outcome,
        test_mileage, postcode_area,
        CASE
            WHEN prev_test_date IS NULL THEN 'NO_PRIOR'
            WHEN prev_result = 'P' THEN 'PASS'
            ELSE 'FAIL'
        END as prev_cycle_outcome_band,
        DATE_DIFF('day', prev_test_date, test_date) as days_since_prev_cycle,
        CASE 
            WHEN prev_mileage IS NOT NULL AND test_mileage > prev_mileage THEN test_mileage - prev_mileage
            ELSE NULL
        END as miles_since_prev_cycle,
        CASE
            WHEN prev_test_date IS NULL THEN 'NO_PRIOR'
            WHEN test_date BETWEEN '2020-08-01' AND '2021-03-31' 
                 AND DATE_DIFF('day', prev_test_date, test_date) BETWEEN 366 AND 550 THEN '180d-1y'
            WHEN DATE_DIFF('day', prev_test_date, test_date) < 180 THEN '<180d'
            WHEN DATE_DIFF('day', prev_test_date, test_date) < 365 THEN '180d-1y'
            WHEN DATE_DIFF('day', prev_test_date, test_date) < 730 THEN '1-2y'
            ELSE '2y+'
        END as gap_band
    FROM history
    """
    
    try:
        conn.execute(f"COPY ({query}) TO '{output_file}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    except Exception as e:
        logger.error(f"Bucket {bucket_id} failed: {e}")
    finally:
        conn.close()

def build_cycle_history():
    start = datetime.now()
    
    # Initialize
    TEMP_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
    
    # Check events lake exists
    if not EVENTS_LAKE_PATH.exists():
        raise RuntimeError(f"Events Lake not found at {EVENTS_LAKE_PATH}")
    
    # Count existing buckets
    bucket_dirs = [d for d in EVENTS_LAKE_PATH.iterdir() if d.is_dir() and d.name.startswith("bucket=")]
    logger.info(f"Found {len(bucket_dirs)} buckets in Events Lake")
    
    # Count already-processed
    existing_parts = len(list(OUTPUT_DIR.glob("part-*.parquet")))
    logger.info(f"Resume: {existing_parts} buckets already processed")
    
    # Stream Buckets
    logger.info("Processing buckets (Streaming Mode)...")
    check_disk_space(min_gb=1.5)
    
    for b in range(NUM_BUCKETS):
        if b % 50 == 0:
            free_gb = check_disk_space(min_gb=1.0)
            logger.info(f"Processing {b}/{NUM_BUCKETS}... ({free_gb:.1f}GB free)")
        process_bucket_streaming(b)
    
    # Cleanup temp
    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)
    
    # Statistics
    logger.info("\nComputing statistics...")
    conn = get_duckdb_connection()
    
    output_glob = str(OUTPUT_DIR / "part-*.parquet")
    stats = conn.execute(f"""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN prev_cycle_outcome_band != 'NO_PRIOR' THEN 1 ELSE 0 END) as with_prior,
            COUNT(DISTINCT vehicle_id) as vehicles
        FROM read_parquet('{output_glob}')
    """).fetchone()
    
    total, with_prior, vehicles = stats
    coverage_pct = 100.0 * with_prior / total if total > 0 else 0
    
    logger.info(f"\n{'='*60}")
    logger.info("BUILD COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"Output: {OUTPUT_DIR}")
    logger.info(f"Total records: {total:,}")
    logger.info(f"Distinct vehicles: {vehicles:,}")
    logger.info(f"History coverage: {coverage_pct:.1f}% ({with_prior:,} with prior)")
    
    # Outcome band distribution
    outcome_stats = conn.execute(f"""
        SELECT 
            prev_cycle_outcome_band,
            COUNT(*) as n,
            ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) as pct
        FROM read_parquet('{output_glob}')
        GROUP BY prev_cycle_outcome_band
        ORDER BY n DESC
    """).fetchdf()
    
    logger.info("\nOutcome Band Distribution:")
    logger.info(outcome_stats.to_string(index=False))
    
    conn.close()
    
    logger.info(f"\nTime: {(datetime.now() - start).total_seconds()/60:.1f} min")

if __name__ == "__main__":
    build_cycle_history()
