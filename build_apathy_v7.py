#!/usr/bin/env python3
"""
Build Apathy Features V7 - Year-by-Year with Disk Cleanup
==========================================================
Processes one year at a time, cleaning up intermediate files to stay within disk limits.
"""

import duckdb
import subprocess
import sys
import logging
from pathlib import Path
from datetime import datetime
import os

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

AUTOSAFE_ROOT = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/AutoSafe"
WORK_ROOT = Path("/Users/henrirapson/autosafe_work")
CYCLE_HISTORY_PATH = AUTOSAFE_ROOT / "cycle_first_with_history.parquet"
OUTPUT_PATH = WORK_ROOT / "code_level_apathy_features_v7.parquet"
TEMP_DIR = Path("/tmp/autosafe_apathy_v7")

sys.path.insert(0, str(AUTOSAFE_ROOT))

TEST_ITEM_FILES = {
    2022: AUTOSAFE_ROOT / "test_items/2022/test_item.csv",
    2023: AUTOSAFE_ROOT / "test_items/2023/test_item.csv",
}

ALPHA = 1
BETA = 5


def cleanup_temp():
    """Remove all temp files."""
    import shutil
    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)
    TEMP_DIR.mkdir(exist_ok=True)


def preprocess_and_aggregate_year(conn, csv_path: Path, mapping_path: Path, year: int, rfr_type: str) -> Path:
    """Extract, aggregate, and return parquet path for one year/type combo."""

    type_name = "advisory" if rfr_type == 'A' else "failure"
    temp_csv = TEMP_DIR / f"{type_name}_{year}.csv"
    output_parquet = TEMP_DIR / f"{type_name}_anchors_{year}.parquet"

    # Step 1: awk extraction
    if rfr_type == 'A':
        filter_cond = '$3=="A"'
    else:
        filter_cond = '($3=="F" || $3=="M")'

    awk_cmd = f"""awk -F'|' 'NR==1 {{print "test_id|rfr_id"}} NR>1 && {filter_cond} {{print $1"|"$2}}' '{csv_path}' > '{temp_csv}'"""
    subprocess.run(awk_cmd, shell=True, check=True)

    wc = subprocess.run(['wc', '-l', str(temp_csv)], capture_output=True, text=True)
    n_rows = int(wc.stdout.split()[0]) - 1
    logger.info(f"    {type_name}: {n_rows:,} rows extracted")

    # Step 2: DuckDB aggregation
    list_col = "advisory_anchor3_list" if rfr_type == 'A' else "failure_anchor3_list"

    conn.execute(f"""
        COPY (
            SELECT
                CAST(t.test_id AS BIGINT) as test_id,
                LIST(DISTINCT CAST(m.anchor_3 AS VARCHAR)) as {list_col}
            FROM read_csv('{temp_csv}', delim='|', header=true) t
            JOIN read_parquet('{mapping_path}') m ON CAST(t.rfr_id AS INTEGER) = m.rfr_id
            GROUP BY t.test_id
        ) TO '{output_parquet}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)

    n_tests = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{output_parquet}')").fetchone()[0]
    logger.info(f"    {type_name}: {n_tests:,} tests with anchors")

    # Cleanup CSV immediately
    os.remove(temp_csv)

    return output_parquet


def main():
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info("BUILD APATHY FEATURES V7 (YEAR-BY-YEAR + CLEANUP)")
    logger.info("=" * 60)

    cleanup_temp()

    # Step 1: Load RFR -> anchor mapping
    logger.info("\n[Step 1] Loading anchor mapping...")
    from dvsa_anchor_mapping import AnchorMapper
    mapper = AnchorMapper(AUTOSAFE_ROOT)
    mapping_df = mapper.create_rfr_anchor_mapping()

    conn = duckdb.connect()
    conn.execute("SET threads = 1")
    conn.execute("SET memory_limit = '2GB'")
    conn.execute(f"SET temp_directory = '{TEMP_DIR}'")

    mapping_path = TEMP_DIR / "rfr_anchor_mapping.parquet"
    conn.execute(f"COPY (SELECT * FROM mapping_df) TO '{mapping_path}' (FORMAT PARQUET)")
    logger.info(f"  Loaded {len(mapping_df):,} mappings")
    del mapping_df

    # Step 2: Process each year separately
    logger.info("\n[Step 2] Processing years...")

    adv_parquets = []
    fail_parquets = []

    for year, csv_path in TEST_ITEM_FILES.items():
        if not csv_path.exists():
            logger.warning(f"  {year}: File not found")
            continue

        logger.info(f"  {year}:")

        # Advisory
        adv_path = preprocess_and_aggregate_year(conn, csv_path, mapping_path, year, 'A')
        adv_parquets.append(str(adv_path))

        # Failure
        fail_path = preprocess_and_aggregate_year(conn, csv_path, mapping_path, year, 'F')
        fail_parquets.append(str(fail_path))

    # Step 3: Combine anchor files
    logger.info("\n[Step 3] Combining anchor files...")

    combined_adv = TEMP_DIR / "advisory_combined.parquet"
    combined_fail = TEMP_DIR / "failure_combined.parquet"

    adv_union = " UNION ALL ".join([f"SELECT * FROM read_parquet('{f}')" for f in adv_parquets])
    conn.execute(f"COPY ({adv_union}) TO '{combined_adv}' (FORMAT PARQUET, COMPRESSION ZSTD)")

    fail_union = " UNION ALL ".join([f"SELECT * FROM read_parquet('{f}')" for f in fail_parquets])
    conn.execute(f"COPY ({fail_union}) TO '{combined_fail}' (FORMAT PARQUET, COMPRESSION ZSTD)")

    # Clean up individual year files
    for f in adv_parquets + fail_parquets:
        if os.path.exists(f):
            os.remove(f)

    n_adv = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{combined_adv}')").fetchone()[0]
    n_fail = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{combined_fail}')").fetchone()[0]
    logger.info(f"  Advisory: {n_adv:,} tests")
    logger.info(f"  Failure: {n_fail:,} tests")

    # Step 4: Compute apathy features
    logger.info("\n[Step 4] Computing apathy features...")

    conn.execute(f"""
        COPY (
            SELECT
                h.test_id,
                h.vehicle_id,
                h.test_date,
                h.outcome,
                h.prev_cycle_test_id,
                COALESCE(len(a.advisory_anchor3_list), 0) as prev_advisory_anchor3_count,
                COALESCE(len(f.failure_anchor3_list), 0) as curr_failure_anchor3_count,
                COALESCE(len(list_intersect(
                    COALESCE(a.advisory_anchor3_list, []),
                    COALESCE(f.failure_anchor3_list, [])
                )), 0) as code_level_apathy_hits,
                CASE
                    WHEN len(list_intersect(
                        COALESCE(a.advisory_anchor3_list, []),
                        COALESCE(f.failure_anchor3_list, [])
                    )) > 0 THEN 1 ELSE 0
                END as has_code_level_apathy,
                CASE
                    WHEN COALESCE(len(a.advisory_anchor3_list), 0) = 0 THEN 0.0
                    ELSE (
                        len(list_intersect(
                            COALESCE(a.advisory_anchor3_list, []),
                            COALESCE(f.failure_anchor3_list, [])
                        )) + {ALPHA}
                    ) * 1.0 / (
                        len(a.advisory_anchor3_list) + {ALPHA} + {BETA}
                    )
                END as code_level_apathy_rate
            FROM read_parquet('{CYCLE_HISTORY_PATH}') h
            LEFT JOIN read_parquet('{combined_adv}') a ON h.prev_cycle_test_id = a.test_id
            LEFT JOIN read_parquet('{combined_fail}') f ON h.test_id = f.test_id
        ) TO '{OUTPUT_PATH}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
    """)

    # Diagnostics
    logger.info("\n[Step 5] Diagnostics...")

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
    cleanup_temp()

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"\nCompleted in {elapsed:.1f}s")
    logger.info(f"Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
