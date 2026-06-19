"""
Bucketed Cycle History Builder (v4 - Final / Streaming)

Architecture:
1. Build Events Lake (Partitioned by Bucket) - De-duplicates source data
2. Streaming Process: Read Bucket -> Sort -> Write DIRECTLY to final dataset
   (Eliminates 2-3GB of intermediate temp files for low-disk environments)
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

# CORRECTED: iCloud location
MOT_DATA_ROOT = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/AutoSafe/MOT Test Results"

# Paths
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
    """Abort if free space < min_gb. Low threshold for emergency run."""
    try:
        free_gb = shutil.disk_usage(WORK_DIR).free / (1024**3)
        if free_gb < min_gb:
            raise RuntimeError(f"CRITICAL: Only {free_gb:.1f}GB free. Need {min_gb}GB to run safely.")
        return free_gb
    except FileNotFoundError:
        return 0

# --- Source Data Configuration (CORRECTED PATHS - iCloud) ---
# Format: (subfolder under MOT_DATA_ROOT, delimiter, file pattern)
RESULTS_SOURCES = [
    # 2005-2016: pipe-delimited .txt.gz files
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
    
    # 2017: pipe-delimited CSV chunks
    ("2017", "|", "test_result_*.csv"),
    
    # 2018-2020: comma-delimited quarterly CSVs
    ("2018", ",", "dft_test_result-from-*.csv"),
    ("2019", ",", "dft_test_result-from-*.csv"),
    ("2020", ",", "dft_test_result-from-*.csv"),
    
    # 2021: comma-delimited CSV chunks
    ("2021", ",", "test_result_*.csv"),
    
    # 2022: pipe-delimited single file
    ("2022", "|", "test_result_2022.csv"),
    
    # 2023: comma-delimited single file
    ("2023", ",", "test_result.csv"),
    
    # 2024: comma-delimited monthly CSVs
    ("2024", ",", "test_result_*.csv"),
]

def get_duckdb_connection():
    conn = duckdb.connect()
    conn.execute("SET threads = 8")
    conn.execute("SET memory_limit = '6GB'") 
    conn.execute(f"SET temp_directory = '{TEMP_DIR}'")
    return conn

def build_events_lake():
    """Build lake only if missing."""
    if EVENTS_LAKE_PATH.exists():
        logger.info("Events Lake found. Skipping ingest.")
        return

    logger.info("Building Events Lake...")
    # Robust directory creation
    EVENTS_LAKE_PATH.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    source_files = []
    for subfolder, sep, pattern in RESULTS_SOURCES:
        folder_path = MOT_DATA_ROOT / subfolder
        if not folder_path.exists(): 
            logger.warning(f"Folder not found: {folder_path}")
            continue
        if '*' in pattern: 
            files = sorted(glob.glob(str(folder_path / pattern)))
        else: 
            files = [str(folder_path / pattern)] if (folder_path / pattern).exists() else []
        for f in files: 
            source_files.append((f, sep))

    logger.info(f"Found {len(source_files)} source files to ingest")
    
    for i, (filepath, sep) in enumerate(source_files):
        logger.info(f"[{i+1}/{len(source_files)}] Ingesting {os.path.basename(filepath)}...")
        conn = get_duckdb_connection()
        try:
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
                ) TO '{EVENTS_LAKE_PATH}' (FORMAT PARQUET, PARTITION_BY (bucket, year)) 
            """)
        except Exception as e: 
            logger.error(f"Error ingesting {filepath}: {e}")
        finally: 
            conn.close()

def process_bucket_streaming(bucket_id: int):
    """Read Bucket -> Calc History -> Write DIRECTLY to Final Dataset (No Merge)"""
    conn = get_duckdb_connection()
    bucket_path = EVENTS_LAKE_PATH / f"bucket={bucket_id}"
    
    # OUTPUT: Directly to the final dataset folder as part-XXXX.parquet
    output_file = OUTPUT_DIR / f"part-{bucket_id:04d}.parquet"
    
    if not bucket_path.exists(): return
    if output_file.exists(): return # Resume support

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
        -- Corrected COVID Logic
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
    
    # Robust Initialization
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # 1. Build Lake
    build_events_lake()
    
    # 2. Stream Buckets
    logger.info("Processing buckets (Streaming Mode)...")
    check_disk_space(min_gb=2.0)
    
    for b in range(NUM_BUCKETS):
        if b % 20 == 0: 
            logger.info(f"Processing {b}/{NUM_BUCKETS}...")
        process_bucket_streaming(b)
        
    # Robust Cleanup
    shutil.rmtree(TEMP_DIR, ignore_errors=True)
    
    logger.info(f"DONE. Output dataset: {OUTPUT_DIR}")
    logger.info(f"Time: {(datetime.now() - start).total_seconds()/60:.1f} min")

if __name__ == "__main__":
    build_cycle_history()
