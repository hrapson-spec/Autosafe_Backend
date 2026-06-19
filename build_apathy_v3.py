#!/usr/bin/env python3
"""
Build Apathy Features V3 - Year-by-Year Processing
===================================================
Processes each year separately to avoid memory issues.
"""

import duckdb
import sys
import logging
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

AUTOSAFE_ROOT = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/AutoSafe"
WORK_ROOT = Path("/Users/henrirapson/autosafe_work")
CYCLE_HISTORY_PATH = AUTOSAFE_ROOT / "cycle_first_with_history.parquet"
OUTPUT_PATH = WORK_ROOT / "code_level_apathy_features_v3.parquet"
TEMP_DIR = Path("/tmp/autosafe_apathy_v3")
TEMP_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(AUTOSAFE_ROOT))

# Use 2022+2023 which have consistent schema
TEST_ITEM_FILES = {
    2022: AUTOSAFE_ROOT / "test_items/2022/test_item.csv",
    2023: AUTOSAFE_ROOT / "test_items/2023/test_item.csv",
}

ALPHA = 1
BETA = 5


def main():
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info("BUILD APATHY FEATURES V3 (YEAR-BY-YEAR)")
    logger.info("=" * 60)

    conn = duckdb.connect()
    conn.execute("SET threads = 1")
    conn.execute("SET memory_limit = '3GB'")  # Lower limit
    conn.execute("SET preserve_insertion_order = false")
    conn.execute(f"SET temp_directory = '{TEMP_DIR}'")

    # Step 1: Load RFR -> anchor mapping
    logger.info("\n[Step 1] Loading anchor mapping...")
    from dvsa_anchor_mapping import AnchorMapper
    mapper = AnchorMapper(AUTOSAFE_ROOT)
    mapping_df = mapper.create_rfr_anchor_mapping()

    # Save to parquet for later use
    mapping_path = TEMP_DIR / "rfr_anchor_mapping.parquet"
    conn.execute(f"""
        COPY (SELECT * FROM mapping_df)
        TO '{mapping_path}' (FORMAT PARQUET)
    """)
    logger.info(f"  Loaded {len(mapping_df):,} mappings")
    del mapping_df  # Free memory

    # Step 2: Process each year's test items separately
    logger.info("\n[Step 2] Processing test items by year...")

    for year, csv_path in TEST_ITEM_FILES.items():
        if not csv_path.exists():
            logger.warning(f"  {year}: File not found")
            continue

        logger.info(f"  Processing {year}...")

        # Build advisory anchors for this year
        adv_path = TEMP_DIR / f"advisory_anchors_{year}.parquet"
        conn.execute(f"""
            COPY (
                SELECT
                    CAST(ti.test_id AS BIGINT) as test_id,
                    LIST(DISTINCT CAST(m.anchor_3 AS VARCHAR)) as advisory_anchor3_list
                FROM read_csv('{csv_path}', delim='|', header=true, auto_detect=true, ignore_errors=true) ti
                JOIN read_parquet('{mapping_path}') m ON CAST(ti.rfr_id AS INTEGER) = m.rfr_id
                WHERE ti.rfr_type_code = 'A'
                GROUP BY ti.test_id
            ) TO '{adv_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        n_adv = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{adv_path}')").fetchone()[0]
        logger.info(f"    Advisory tests: {n_adv:,}")

        # Build failure anchors for this year
        fail_path = TEMP_DIR / f"failure_anchors_{year}.parquet"
        conn.execute(f"""
            COPY (
                SELECT
                    CAST(ti.test_id AS BIGINT) as test_id,
                    LIST(DISTINCT CAST(m.anchor_3 AS VARCHAR)) as failure_anchor3_list
                FROM read_csv('{csv_path}', delim='|', header=true, auto_detect=true, ignore_errors=true) ti
                JOIN read_parquet('{mapping_path}') m ON CAST(ti.rfr_id AS INTEGER) = m.rfr_id
                WHERE ti.rfr_type_code IN ('F', 'M')
                GROUP BY ti.test_id
            ) TO '{fail_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        n_fail = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{fail_path}')").fetchone()[0]
        logger.info(f"    Failure tests: {n_fail:,}")

    # Step 3: Combine all anchor files
    logger.info("\n[Step 3] Combining anchor files...")

    adv_files = [str(TEMP_DIR / f"advisory_anchors_{y}.parquet") for y in TEST_ITEM_FILES.keys()
                 if (TEMP_DIR / f"advisory_anchors_{y}.parquet").exists()]
    fail_files = [str(TEMP_DIR / f"failure_anchors_{y}.parquet") for y in TEST_ITEM_FILES.keys()
                  if (TEMP_DIR / f"failure_anchors_{y}.parquet").exists()]

    # Union advisory files
    combined_adv = TEMP_DIR / "advisory_anchors_combined.parquet"
    adv_union = " UNION ALL ".join([f"SELECT * FROM read_parquet('{f}')" for f in adv_files])
    conn.execute(f"""
        COPY (
            SELECT test_id, list_sort(list_distinct(flatten(list(advisory_anchor3_list)))) as advisory_anchor3_list
            FROM ({adv_union})
            GROUP BY test_id
        ) TO '{combined_adv}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n_combined_adv = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{combined_adv}')").fetchone()[0]
    logger.info(f"  Combined advisory tests: {n_combined_adv:,}")

    # Union failure files
    combined_fail = TEMP_DIR / "failure_anchors_combined.parquet"
    fail_union = " UNION ALL ".join([f"SELECT * FROM read_parquet('{f}')" for f in fail_files])
    conn.execute(f"""
        COPY (
            SELECT test_id, list_sort(list_distinct(flatten(list(failure_anchor3_list)))) as failure_anchor3_list
            FROM ({fail_union})
            GROUP BY test_id
        ) TO '{combined_fail}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n_combined_fail = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{combined_fail}')").fetchone()[0]
    logger.info(f"  Combined failure tests: {n_combined_fail:,}")

    # Step 4: Compute apathy features in chunks
    logger.info("\n[Step 4] Computing apathy features (chunked by test_date)...")

    # Get date range from cycle history
    date_range = conn.execute(f"""
        SELECT MIN(test_date), MAX(test_date)
        FROM read_parquet('{CYCLE_HISTORY_PATH}')
    """).fetchone()
    logger.info(f"  Date range: {date_range[0]} to {date_range[1]}")

    # Process in monthly chunks
    chunk_files = []

    # Get distinct year-months
    year_months = conn.execute(f"""
        SELECT DISTINCT YEAR(test_date) as y, MONTH(test_date) as m
        FROM read_parquet('{CYCLE_HISTORY_PATH}')
        ORDER BY y, m
    """).fetchall()

    for i, (year, month) in enumerate(year_months):
        chunk_path = TEMP_DIR / f"apathy_chunk_{year}_{month:02d}.parquet"
        logger.info(f"  Processing {year}-{month:02d} ({i+1}/{len(year_months)})...")

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
                WHERE YEAR(h.test_date) = {year} AND MONTH(h.test_date) = {month}
            ) TO '{chunk_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)

        n_chunk = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{chunk_path}')").fetchone()[0]
        chunk_files.append(str(chunk_path))

    # Step 5: Combine all chunks
    logger.info("\n[Step 5] Combining all chunks...")

    chunk_union = " UNION ALL ".join([f"SELECT * FROM read_parquet('{f}')" for f in chunk_files])
    conn.execute(f"""
        COPY ({chunk_union}) TO '{OUTPUT_PATH}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
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
