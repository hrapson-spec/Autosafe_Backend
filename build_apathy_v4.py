#!/usr/bin/env python3
"""
Build Apathy Features V4 - Streaming Processing
================================================
Uses streaming/chunking at every stage to handle memory constraints.
"""

import duckdb
import sys
import logging
from pathlib import Path
from datetime import datetime
import gc

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

AUTOSAFE_ROOT = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/AutoSafe"
WORK_ROOT = Path("/Users/henrirapson/autosafe_work")
CYCLE_HISTORY_PATH = AUTOSAFE_ROOT / "cycle_first_with_history.parquet"
OUTPUT_PATH = WORK_ROOT / "code_level_apathy_features_v4.parquet"
TEMP_DIR = Path("/tmp/autosafe_apathy_v4")
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
    logger.info("BUILD APATHY FEATURES V4 (STREAMING)")
    logger.info("=" * 60)

    # Step 1: Load RFR -> anchor mapping
    logger.info("\n[Step 1] Loading anchor mapping...")
    from dvsa_anchor_mapping import AnchorMapper
    mapper = AnchorMapper(AUTOSAFE_ROOT)
    mapping_df = mapper.create_rfr_anchor_mapping()

    conn = duckdb.connect()
    conn.execute("SET threads = 1")
    conn.execute("SET memory_limit = '2GB'")
    conn.execute("SET preserve_insertion_order = false")
    conn.execute(f"SET temp_directory = '{TEMP_DIR}'")

    # Save mapping to parquet
    mapping_path = TEMP_DIR / "rfr_anchor_mapping.parquet"
    conn.execute(f"""
        COPY (SELECT * FROM mapping_df)
        TO '{mapping_path}' (FORMAT PARQUET)
    """)
    logger.info(f"  Loaded {len(mapping_df):,} mappings")
    del mapping_df
    gc.collect()

    # Step 2: Process test items in a simpler way
    logger.info("\n[Step 2] Processing test items...")

    # CSV has 5 columns: test_id|rfr_id|rfr_type_code|location_id|dangerous_mark
    CSV_COLUMNS = "{'test_id': 'BIGINT', 'rfr_id': 'INTEGER', 'rfr_type_code': 'VARCHAR', 'location_id': 'INTEGER', 'dangerous_mark': 'VARCHAR'}"

    for year, csv_path in TEST_ITEM_FILES.items():
        if not csv_path.exists():
            logger.warning(f"  {year}: File not found")
            continue

        logger.info(f"  Processing {year}...")

        adv_path = TEMP_DIR / f"advisory_{year}.parquet"

        try:
            # Advisory anchors
            conn.execute(f"""
                COPY (
                    SELECT
                        test_id,
                        LIST(DISTINCT anchor_3) as advisory_anchor3_list
                    FROM (
                        SELECT
                            ti.test_id as test_id,
                            CAST(m.anchor_3 AS VARCHAR) as anchor_3
                        FROM read_csv('{csv_path}',
                                      delim='|',
                                      header=true,
                                      columns={CSV_COLUMNS},
                                      ignore_errors=true) ti
                        JOIN read_parquet('{mapping_path}') m ON ti.rfr_id = m.rfr_id
                        WHERE ti.rfr_type_code = 'A'
                    )
                    GROUP BY test_id
                ) TO '{adv_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """)
            n_adv = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{adv_path}')").fetchone()[0]
            logger.info(f"    Advisory tests: {n_adv:,}")
        except Exception as e:
            logger.error(f"    Advisory failed: {e}")
            continue

        gc.collect()

        # Failure anchors
        fail_path = TEMP_DIR / f"failure_{year}.parquet"
        try:
            conn.execute(f"""
                COPY (
                    SELECT
                        test_id,
                        LIST(DISTINCT anchor_3) as failure_anchor3_list
                    FROM (
                        SELECT
                            ti.test_id as test_id,
                            CAST(m.anchor_3 AS VARCHAR) as anchor_3
                        FROM read_csv('{csv_path}',
                                      delim='|',
                                      header=true,
                                      columns={CSV_COLUMNS},
                                      ignore_errors=true) ti
                        JOIN read_parquet('{mapping_path}') m ON ti.rfr_id = m.rfr_id
                        WHERE ti.rfr_type_code IN ('F', 'M')
                    )
                    GROUP BY test_id
                ) TO '{fail_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """)
            n_fail = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{fail_path}')").fetchone()[0]
            logger.info(f"    Failure tests: {n_fail:,}")
        except Exception as e:
            logger.error(f"    Failure failed: {e}")
            continue

        gc.collect()

    conn.close()
    gc.collect()

    # Step 3: Combine and compute apathy features
    logger.info("\n[Step 3] Combining anchor files...")

    conn = duckdb.connect()
    conn.execute("SET threads = 1")
    conn.execute("SET memory_limit = '2GB'")
    conn.execute("SET preserve_insertion_order = false")
    conn.execute(f"SET temp_directory = '{TEMP_DIR}'")

    adv_files = [str(TEMP_DIR / f"advisory_{y}.parquet") for y in TEST_ITEM_FILES.keys()
                 if (TEMP_DIR / f"advisory_{y}.parquet").exists()]
    fail_files = [str(TEMP_DIR / f"failure_{y}.parquet") for y in TEST_ITEM_FILES.keys()
                  if (TEMP_DIR / f"failure_{y}.parquet").exists()]

    if not adv_files or not fail_files:
        logger.error("No anchor files created. Exiting.")
        return

    combined_adv = TEMP_DIR / "advisory_combined.parquet"
    combined_fail = TEMP_DIR / "failure_combined.parquet"

    # Union advisory files
    adv_union = " UNION ALL ".join([f"SELECT * FROM read_parquet('{f}')" for f in adv_files])
    conn.execute(f"""
        COPY ({adv_union}) TO '{combined_adv}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n_combined_adv = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{combined_adv}')").fetchone()[0]
    logger.info(f"  Combined advisory tests: {n_combined_adv:,}")

    fail_union = " UNION ALL ".join([f"SELECT * FROM read_parquet('{f}')" for f in fail_files])
    conn.execute(f"""
        COPY ({fail_union}) TO '{combined_fail}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n_combined_fail = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{combined_fail}')").fetchone()[0]
    logger.info(f"  Combined failure tests: {n_combined_fail:,}")

    conn.close()
    gc.collect()

    # Step 4: Compute apathy features by month chunks
    logger.info("\n[Step 4] Computing apathy features...")

    conn = duckdb.connect()
    conn.execute("SET threads = 1")
    conn.execute("SET memory_limit = '2GB'")
    conn.execute("SET preserve_insertion_order = false")
    conn.execute(f"SET temp_directory = '{TEMP_DIR}'")

    # Get distinct year-months
    year_months = conn.execute(f"""
        SELECT DISTINCT YEAR(test_date) as y, MONTH(test_date) as m
        FROM read_parquet('{CYCLE_HISTORY_PATH}')
        ORDER BY y, m
    """).fetchall()

    logger.info(f"  Processing {len(year_months)} year-months...")

    chunk_files = []
    for i, (year, month) in enumerate(year_months):
        chunk_path = TEMP_DIR / f"apathy_{year}_{month:02d}.parquet"

        if i % 12 == 0:
            logger.info(f"  {year}-{month:02d} ({i+1}/{len(year_months)})...")

        try:
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
            chunk_files.append(str(chunk_path))
        except Exception as e:
            logger.error(f"  Failed {year}-{month}: {e}")
            continue

    # Step 5: Combine all chunks
    logger.info("\n[Step 5] Combining all chunks...")

    chunk_union = " UNION ALL ".join([f"SELECT * FROM read_parquet('{f}')" for f in chunk_files if Path(f).exists()])
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
