"""
Bucketed Cycle History Builder (v5 - Ultra Low Disk / Streaming)

Key changes from v4:
- Processes ONE FILE AT A TIME directly from iCloud
- APPENDS to Events Lake instead of overwriting
- Uses file-level resume tracking to avoid re-processing
- Handles iCloud timeouts gracefully with retries
"""

import duckdb
import os
import logging
import shutil
import glob
import time
from pathlib import Path
from datetime import datetime

# --- Configuration ---
WORK_DIR = Path("/Users/henrirapson/autosafe_work")
MOT_DATA_ROOT = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/AutoSafe/MOT Test Results"

# Paths
OUTPUT_DIR = WORK_DIR / "cycle_history_dataset" 
EVENTS_LAKE_PATH = WORK_DIR / "events_lake"
TEMP_DIR = WORK_DIR / "duckdb_tmp"
PROGRESS_FILE = WORK_DIR / "ingest_progress.txt"

# Settings
NUM_BUCKETS = 512
MIN_YEAR = 2005
MAX_RETRIES = 3

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def check_disk_space(min_gb=2):
    try:
        free_gb = shutil.disk_usage(WORK_DIR).free / (1024**3)
        if free_gb < min_gb:
            raise RuntimeError(f"CRITICAL: Only {free_gb:.1f}GB free. Need {min_gb}GB to run safely.")
        return free_gb
    except FileNotFoundError:
        return 0

# --- Source Data Configuration ---
RESULTS_SOURCES = [
    ("2005", "|", "test_result_2005.txt.gz"),
    ("2006", "|", "test_result_2006.txt.gz"),
    ("2007", "|", "test_result_2007.txt.gz"),
    ("2008", "|", "test_result_2008.txt.gz"),
    ("2009", "|", "test_result_2009.txt.gz"),
    ("2010", "|", "test_result_2010.txt.gz"),
    ("2011", "|", "test_result_2011.txt.gz"),
    ("2012", "|", "test_result_2012.txt.gz"),
    ("2013", "|", "test_result_2013.txt.gz"),
    ("2014", "|", "test_result_2014.txt.gz"),
    ("2015", "|", "test_result_2015.txt.gz"),
    ("2016", "|", "test_result_2016.txt.gz"),
    ("2017", "|", "test_result_*.csv"),
    ("2018", ",", "dft_test_result-from-*.csv"),
    ("2019", ",", "dft_test_result-from-*.csv"),
    ("2020", ",", "dft_test_result-from-*.csv"),
    ("2021", ",", "test_result_*.csv"),
    ("2022", "|", "test_result_2022.csv"),
    ("2023", ",", "test_result.csv"),
    ("2024", ",", "test_result_*.csv"),
]

def get_duckdb_connection():
    conn = duckdb.connect()
    conn.execute("SET threads = 4")  # Lower for stability
    conn.execute("SET memory_limit = '4GB'")
    conn.execute(f"SET temp_directory = '{TEMP_DIR}'")
    return conn

def get_processed_files():
    """Load list of already processed files for resume support."""
    if PROGRESS_FILE.exists():
        return set(PROGRESS_FILE.read_text().strip().split('\n'))
    return set()

def mark_file_processed(filepath):
    """Mark file as processed for resume support."""
    with open(PROGRESS_FILE, 'a') as f:
        f.write(f"{filepath}\n")

def trigger_icloud_download(filepath):
    """Touch file to trigger iCloud download, wait for it."""
    logger.info(f"  Triggering iCloud download...")
    try:
        # Just reading the first byte triggers download
        with open(filepath, 'rb') as f:
            f.read(1)
        return True
    except Exception as e:
        logger.warning(f"  iCloud trigger failed: {e}")
        return False

def ingest_single_file(filepath, sep, retries=MAX_RETRIES):
    """Ingest a single file to the Events Lake with retry logic."""
    for attempt in range(retries):
        try:
            # Trigger iCloud download if needed
            if not os.path.exists(filepath):
                if attempt == 0:
                    trigger_icloud_download(filepath)
                    time.sleep(2)  # Wait for download to start
            
            conn = get_duckdb_connection()
            
            # Use OVERWRITE_OR_IGNORE for appending to partitioned dataset
            conn.execute(f"""
                COPY (
                    SELECT
                        CAST(test_id AS BIGINT) as test_id,
                        CAST(vehicle_id AS BIGINT) as vehicle_id,
                        CAST(test_date AS DATE) as test_date,
                        CASE WHEN UPPER(CAST(test_result AS VARCHAR)) IN ('P','PASS') THEN 'P' ELSE 'F' END as test_result,
                        CAST(test_mileage AS INTEGER) as test_mileage,
                        CAST(postcode_area AS VARCHAR) as postcode_area,
                        CAST(vehicle_id % {NUM_BUCKETS} AS INTEGER) as bucket,
                        YEAR(CAST(test_date AS DATE)) as year
                    FROM read_csv('{filepath}', delim='{sep}', header=true, auto_detect=true, ignore_errors=true)
                    WHERE vehicle_id IS NOT NULL AND test_date IS NOT NULL AND YEAR(CAST(test_date AS DATE)) >= {MIN_YEAR}
                ) TO '{EVENTS_LAKE_PATH}' (FORMAT PARQUET, PARTITION_BY (bucket, year), OVERWRITE_OR_IGNORE true)
            """)
            conn.close()
            return True
            
        except Exception as e:
            error_msg = str(e)
            if "timed out" in error_msg.lower() or "operation canceled" in error_msg.lower():
                logger.warning(f"  Attempt {attempt+1}/{retries}: Timeout/cancel, retrying after wait...")
                time.sleep(5 * (attempt + 1))  # Exponential backoff
            else:
                logger.error(f"  Error: {e}")
                return False
            
    return False

def build_events_lake():
    """Build the events lake, processing one file at a time with resume."""
    EVENTS_LAKE_PATH.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    
    processed = get_processed_files()
    
    # Build file list
    source_files = []
    for subfolder, sep, pattern in RESULTS_SOURCES:
        folder_path = MOT_DATA_ROOT / subfolder
        if not folder_path.exists():
            logger.warning(f"Folder not found: {folder_path}")
            continue
        if '*' in pattern:
            files = sorted(glob.glob(str(folder_path / pattern)))
        else:
            file_path = folder_path / pattern
            files = [str(file_path)] if file_path.exists() else []
        for f in files:
            source_files.append((f, sep))
    
    logger.info(f"Found {len(source_files)} source files ({len(processed)} already processed)")
    
    for i, (filepath, sep) in enumerate(source_files):
        if filepath in processed:
            logger.info(f"[{i+1}/{len(source_files)}] SKIP (done): {os.path.basename(filepath)}")
            continue
            
        logger.info(f"[{i+1}/{len(source_files)}] Ingesting {os.path.basename(filepath)}...")
        
        if ingest_single_file(filepath, sep):
            mark_file_processed(filepath)
            logger.info(f"  ✓ Success")
        else:
            logger.error(f"  ✗ Failed after retries")

def process_bucket_streaming(bucket_id: int):
    """Read Bucket -> Calc History -> Write to Final Dataset."""
    bucket_path = EVENTS_LAKE_PATH / f"bucket={bucket_id}"
    output_file = OUTPUT_DIR / f"part-{bucket_id:04d}.parquet"
    
    if not bucket_path.exists():
        return
    if output_file.exists():
        return  # Resume support
    
    conn = get_duckdb_connection()
    
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
    
    # Check disk space
    free_gb = check_disk_space(min_gb=2.0)
    logger.info(f"Free disk space: {free_gb:.1f}GB")
    
    # Setup directories
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # 1. Build Events Lake with resume
    build_events_lake()
    
    # 2. Process buckets
    logger.info("Processing buckets...")
    for b in range(NUM_BUCKETS):
        if b % 50 == 0:
            logger.info(f"Processing bucket {b}/{NUM_BUCKETS}...")
        process_bucket_streaming(b)
    
    # 3. Cleanup temp
    shutil.rmtree(TEMP_DIR, ignore_errors=True)
    
    logger.info(f"DONE. Output: {OUTPUT_DIR}")
    logger.info(f"Time: {(datetime.now() - start).total_seconds()/60:.1f} min")

if __name__ == "__main__":
    build_cycle_history()
