#!/usr/bin/env python3
"""
Build Apathy Features V6 - Shell Pre-processing + DuckDB
=========================================================
Uses awk for fast CSV filtering, then DuckDB for aggregation.

Key optimizations:
1. Shell pre-processing (awk) is 10-100x faster than Python CSV reading
2. Write filtered data to temp parquet files first
3. DuckDB aggregates on smaller dataset
"""

import duckdb
import subprocess
import sys
import logging
from pathlib import Path
from datetime import datetime
import os
import gc

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

AUTOSAFE_ROOT = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/AutoSafe"
WORK_ROOT = Path("/Users/henrirapson/autosafe_work")
CYCLE_HISTORY_PATH = AUTOSAFE_ROOT / "cycle_first_with_history.parquet"
OUTPUT_PATH = WORK_ROOT / "code_level_apathy_features_v6.parquet"
TEMP_DIR = Path("/tmp/autosafe_apathy_v6")
TEMP_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(AUTOSAFE_ROOT))

TEST_ITEM_FILES = {
    2022: AUTOSAFE_ROOT / "test_items/2022/test_item.csv",
    2023: AUTOSAFE_ROOT / "test_items/2023/test_item.csv",
}

ALPHA = 1
BETA = 5


def check_available_memory():
    """Log memory status. DuckDB spills to disk so we proceed anyway."""
    result = subprocess.run(['vm_stat'], capture_output=True, text=True)
    free_pages = inactive_pages = 0
    for line in result.stdout.split('\n'):
        if 'Pages free' in line:
            free_pages = int(line.split(':')[1].strip().rstrip('.'))
        elif 'Pages inactive' in line:
            inactive_pages = int(line.split(':')[1].strip().rstrip('.'))
    # macOS uses 16KB pages; free + inactive is effectively available
    available_mb = (free_pages + inactive_pages) * 16384 / 1024 / 1024
    logger.info(f"Available memory (free+inactive): {available_mb:.0f}MB")
    if available_mb < 1000:
        logger.warning("Low memory - DuckDB will spill to disk, expect slower performance")


def preprocess_csv_with_awk(csv_path: Path, output_path: Path, rfr_type: str) -> int:
    """Use awk to extract filtered rows - much faster than Python."""

    # Filter: header + rows where rfr_type_code matches
    # Output: test_id|rfr_id only (columns 1 and 2)
    if rfr_type == 'A':
        filter_condition = '$3=="A"'
    else:  # F or M
        filter_condition = '($3=="F" || $3=="M")'

    awk_cmd = f"""awk -F'|' 'NR==1 {{print "test_id|rfr_id"}} NR>1 && {filter_condition} {{print $1"|"$2}}' '{csv_path}' > '{output_path}'"""

    result = subprocess.run(awk_cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"awk failed: {result.stderr}")
        return 0

    # Count lines
    wc_result = subprocess.run(['wc', '-l', str(output_path)], capture_output=True, text=True)
    return int(wc_result.stdout.split()[0]) - 1  # Minus header


def main():
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info("BUILD APATHY FEATURES V6 (AWK + DUCKDB) - OOM-SAFE")
    logger.info("=" * 60)

    # Memory check before starting
    check_available_memory()

    # Step 1: Load RFR -> anchor mapping
    logger.info("\n[Step 1] Loading anchor mapping...")
    from dvsa_anchor_mapping import AnchorMapper
    mapper = AnchorMapper(AUTOSAFE_ROOT)
    mapping_df = mapper.create_rfr_anchor_mapping()

    conn = duckdb.connect()
    conn.execute("SET threads = 1")  # Single thread = lower peak memory
    conn.execute("SET memory_limit = '4GB'")
    conn.execute("SET preserve_insertion_order = false")  # Memory optimization
    conn.execute("SET max_temp_directory_size = '50GB'")  # Bound spilling
    conn.execute(f"SET temp_directory = '{TEMP_DIR}'")

    mapping_path = TEMP_DIR / "rfr_anchor_mapping.parquet"
    conn.execute(f"COPY (SELECT * FROM mapping_df) TO '{mapping_path}' (FORMAT PARQUET)")
    logger.info(f"  Loaded {len(mapping_df):,} mappings")
    del mapping_df

    # Step 2: Pre-process CSV files with awk
    logger.info("\n[Step 2] Pre-processing CSV files with awk...")

    for year, csv_path in TEST_ITEM_FILES.items():
        if not csv_path.exists():
            logger.warning(f"  {year}: File not found")
            continue

        logger.info(f"  {year}: Extracting advisories...")
        adv_csv = TEMP_DIR / f"advisory_{year}.csv"
        n_adv = preprocess_csv_with_awk(csv_path, adv_csv, 'A')
        logger.info(f"    {n_adv:,} advisory rows")

        logger.info(f"  {year}: Extracting failures...")
        fail_csv = TEMP_DIR / f"failure_{year}.csv"
        n_fail = preprocess_csv_with_awk(csv_path, fail_csv, 'F')
        logger.info(f"    {n_fail:,} failure rows")

    # Step 3: Convert to parquet with anchor joins
    logger.info("\n[Step 3] Building anchor aggregates...")

    for year in TEST_ITEM_FILES.keys():
        adv_csv = TEMP_DIR / f"advisory_{year}.csv"
        fail_csv = TEMP_DIR / f"failure_{year}.csv"

        if not adv_csv.exists() or not fail_csv.exists():
            continue

        # Advisory anchors
        adv_parquet = TEMP_DIR / f"advisory_anchors_{year}.parquet"
        logger.info(f"  {year}: Building advisory anchors...")
        conn.execute(f"""
            COPY (
                SELECT
                    CAST(t.test_id AS BIGINT) as test_id,
                    LIST(DISTINCT CAST(m.anchor_3 AS VARCHAR)) as advisory_anchor3_list
                FROM read_csv('{adv_csv}', delim='|', header=true) t
                JOIN read_parquet('{mapping_path}') m ON CAST(t.rfr_id AS INTEGER) = m.rfr_id
                GROUP BY t.test_id
            ) TO '{adv_parquet}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        n = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{adv_parquet}')").fetchone()[0]
        logger.info(f"    {n:,} tests with advisory anchors")

        # Failure anchors
        fail_parquet = TEMP_DIR / f"failure_anchors_{year}.parquet"
        logger.info(f"  {year}: Building failure anchors...")
        conn.execute(f"""
            COPY (
                SELECT
                    CAST(t.test_id AS BIGINT) as test_id,
                    LIST(DISTINCT CAST(m.anchor_3 AS VARCHAR)) as failure_anchor3_list
                FROM read_csv('{fail_csv}', delim='|', header=true) t
                JOIN read_parquet('{mapping_path}') m ON CAST(t.rfr_id AS INTEGER) = m.rfr_id
                GROUP BY t.test_id
            ) TO '{fail_parquet}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        n = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{fail_parquet}')").fetchone()[0]
        logger.info(f"    {n:,} tests with failure anchors")

        # Clean up CSV files
        os.remove(adv_csv)
        os.remove(fail_csv)

        # Force memory cleanup after each year
        gc.collect()
        conn.execute("CHECKPOINT")
        logger.info(f"  {year}: Memory cleanup complete")

    # Step 4: Combine anchor files
    logger.info("\n[Step 4] Combining anchor files...")

    adv_files = [str(TEMP_DIR / f"advisory_anchors_{y}.parquet")
                 for y in TEST_ITEM_FILES.keys()
                 if (TEMP_DIR / f"advisory_anchors_{y}.parquet").exists()]
    fail_files = [str(TEMP_DIR / f"failure_anchors_{y}.parquet")
                  for y in TEST_ITEM_FILES.keys()
                  if (TEMP_DIR / f"failure_anchors_{y}.parquet").exists()]

    combined_adv = TEMP_DIR / "advisory_combined.parquet"
    combined_fail = TEMP_DIR / "failure_combined.parquet"

    adv_union = " UNION ALL ".join([f"SELECT * FROM read_parquet('{f}')" for f in adv_files])
    conn.execute(f"COPY ({adv_union}) TO '{combined_adv}' (FORMAT PARQUET, COMPRESSION ZSTD)")

    fail_union = " UNION ALL ".join([f"SELECT * FROM read_parquet('{f}')" for f in fail_files])
    conn.execute(f"COPY ({fail_union}) TO '{combined_fail}' (FORMAT PARQUET, COMPRESSION ZSTD)")

    logger.info(f"  Advisory: {conn.execute(f'SELECT COUNT(*) FROM read_parquet({chr(39)}{combined_adv}{chr(39)})').fetchone()[0]:,} tests")
    logger.info(f"  Failure: {conn.execute(f'SELECT COUNT(*) FROM read_parquet({chr(39)}{combined_fail}{chr(39)})').fetchone()[0]:,} tests")

    # Cleanup year-specific parquets to free disk space
    for year in TEST_ITEM_FILES.keys():
        for pattern in ['advisory_anchors_', 'failure_anchors_']:
            temp_file = TEMP_DIR / f"{pattern}{year}.parquet"
            if temp_file.exists():
                os.remove(temp_file)
                logger.info(f"  Cleaned up {temp_file.name}")
    gc.collect()

    # Step 5: Compute apathy features
    logger.info("\n[Step 5] Computing apathy features...")

    # Use CTE to compute list_intersect once instead of 4 times (memory optimization)
    conn.execute(f"""
        COPY (
            WITH joined_data AS (
                SELECT
                    h.test_id,
                    h.vehicle_id,
                    h.test_date,
                    h.outcome,
                    h.prev_cycle_test_id,
                    COALESCE(a.advisory_anchor3_list, []) as adv_list,
                    COALESCE(f.failure_anchor3_list, []) as fail_list
                FROM read_parquet('{CYCLE_HISTORY_PATH}') h
                LEFT JOIN read_parquet('{combined_adv}') a ON h.prev_cycle_test_id = a.test_id
                LEFT JOIN read_parquet('{combined_fail}') f ON h.test_id = f.test_id
            ),
            with_intersection AS (
                SELECT *,
                    list_intersect(adv_list, fail_list) as intersection_list
                FROM joined_data
            )
            SELECT
                test_id,
                vehicle_id,
                test_date,
                outcome,
                prev_cycle_test_id,
                len(adv_list) as prev_advisory_anchor3_count,
                len(fail_list) as curr_failure_anchor3_count,
                len(intersection_list) as code_level_apathy_hits,
                CASE WHEN len(intersection_list) > 0 THEN 1 ELSE 0 END as has_code_level_apathy,
                CASE
                    WHEN len(adv_list) = 0 THEN 0.0
                    ELSE (len(intersection_list) + {ALPHA}) * 1.0 / (len(adv_list) + {ALPHA} + {BETA})
                END as code_level_apathy_rate
            FROM with_intersection
        ) TO '{OUTPUT_PATH}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
    """)

    # Diagnostics
    logger.info("\n[Step 6] Diagnostics...")

    n_rows = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{OUTPUT_PATH}')").fetchone()[0]
    logger.info(f"Total rows: {n_rows:,}")

    stats = conn.execute(f"""
        SELECT
            SUM(CASE WHEN prev_advisory_anchor3_count > 0 THEN 1 ELSE 0 END) as with_prior_adv,
            SUM(has_code_level_apathy) as with_apathy,
            ROUND(100.0 * SUM(CASE WHEN has_code_level_apathy = 1 AND outcome = 'FAIL' THEN 1 ELSE 0 END) /
                  NULLIF(SUM(has_code_level_apathy), 0), 1) as apathy_fail_rate
        FROM read_parquet('{OUTPUT_PATH}')
    """).fetchone()

    logger.info(f"Tests with prior advisories: {stats[0]:,}")
    logger.info(f"Tests with apathy hits: {stats[1]:,}")
    logger.info(f"Fail rate when apathy=1: {stats[2]}%")

    # Coverage by year
    logger.info("\nCoverage by year:")
    year_stats = conn.execute(f"""
        SELECT
            YEAR(test_date) as year,
            COUNT(*) as n_tests,
            SUM(CASE WHEN prev_advisory_anchor3_count > 0 THEN 1 ELSE 0 END) as with_prior_adv,
            SUM(has_code_level_apathy) as with_apathy
        FROM read_parquet('{OUTPUT_PATH}')
        GROUP BY 1
        ORDER BY 1
    """).fetchall()

    for year, n, adv, apathy in year_stats:
        logger.info(f"  {year}: {n:,} tests, {adv:,} with prior adv, {apathy:,} with apathy")

    conn.close()

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"\nCompleted in {elapsed:.1f}s")
    logger.info(f"Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
