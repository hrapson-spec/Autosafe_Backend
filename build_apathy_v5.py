#!/usr/bin/env python3
"""
Build Apathy Features V5 - Pandas Chunked Processing
=====================================================
Uses pandas chunked CSV reading to stay within memory limits.
"""

import pandas as pd
import duckdb
import sys
import logging
from pathlib import Path
from datetime import datetime
import gc
from collections import defaultdict

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

AUTOSAFE_ROOT = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/AutoSafe"
WORK_ROOT = Path("/Users/henrirapson/autosafe_work")
CYCLE_HISTORY_PATH = AUTOSAFE_ROOT / "cycle_first_with_history.parquet"
OUTPUT_PATH = WORK_ROOT / "code_level_apathy_features_v5.parquet"
TEMP_DIR = Path("/tmp/autosafe_apathy_v5")
TEMP_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(AUTOSAFE_ROOT))

# Use 2022+2023 which have consistent schema
TEST_ITEM_FILES = {
    2022: AUTOSAFE_ROOT / "test_items/2022/test_item.csv",
    2023: AUTOSAFE_ROOT / "test_items/2023/test_item.csv",
}

ALPHA = 1
BETA = 5
CHUNK_SIZE = 1_000_000  # Process 1M rows at a time


def main():
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info("BUILD APATHY FEATURES V5 (PANDAS CHUNKED)")
    logger.info("=" * 60)

    # Step 1: Load RFR -> anchor mapping
    logger.info("\n[Step 1] Loading anchor mapping...")
    from dvsa_anchor_mapping import AnchorMapper
    mapper = AnchorMapper(AUTOSAFE_ROOT)
    mapping_df = mapper.create_rfr_anchor_mapping()

    # Create rfr_id -> anchor_3 lookup
    rfr_to_anchor = dict(zip(mapping_df['rfr_id'], mapping_df['anchor_3']))
    logger.info(f"  Loaded {len(rfr_to_anchor):,} RFR->anchor mappings")
    del mapping_df
    gc.collect()

    # Step 2: Process test items in chunks and build dictionaries
    logger.info("\n[Step 2] Processing test items (chunked)...")

    # Dictionary: test_id -> set of anchor_3 values
    advisory_anchors = defaultdict(set)  # test_id -> set of anchor3
    failure_anchors = defaultdict(set)   # test_id -> set of anchor3

    for year, csv_path in TEST_ITEM_FILES.items():
        if not csv_path.exists():
            logger.warning(f"  {year}: File not found")
            continue

        logger.info(f"  Processing {year}...")
        chunk_num = 0
        total_rows = 0

        for chunk in pd.read_csv(csv_path, sep='|', chunksize=CHUNK_SIZE,
                                 usecols=['test_id', 'rfr_id', 'rfr_type_code'],
                                 dtype={'test_id': 'int64', 'rfr_id': 'int32', 'rfr_type_code': 'str'}):
            total_rows += len(chunk)
            chunk_num += 1

            if chunk_num % 10 == 1:
                logger.info(f"    Chunk {chunk_num} ({total_rows:,} rows)...")

            # Filter to advisories and failures
            advisories = chunk[chunk['rfr_type_code'] == 'A']
            failures = chunk[chunk['rfr_type_code'].isin(['F', 'M'])]

            # Map rfr_id -> anchor_3 and aggregate by test_id
            for test_id, rfr_id in zip(advisories['test_id'], advisories['rfr_id']):
                if rfr_id in rfr_to_anchor:
                    advisory_anchors[test_id].add(rfr_to_anchor[rfr_id])

            for test_id, rfr_id in zip(failures['test_id'], failures['rfr_id']):
                if rfr_id in rfr_to_anchor:
                    failure_anchors[test_id].add(rfr_to_anchor[rfr_id])

            del chunk, advisories, failures
            gc.collect()

        logger.info(f"    Total: {total_rows:,} rows")

    logger.info(f"  Tests with advisories: {len(advisory_anchors):,}")
    logger.info(f"  Tests with failures: {len(failure_anchors):,}")

    # Clean up
    del rfr_to_anchor
    gc.collect()

    # Step 3: Save anchor dictionaries to parquet for joining
    logger.info("\n[Step 3] Saving anchor lookups...")

    # Convert to dataframes
    adv_df = pd.DataFrame([
        {'test_id': tid, 'advisory_anchor3_list': list(anchors)}
        for tid, anchors in advisory_anchors.items()
    ])
    fail_df = pd.DataFrame([
        {'test_id': tid, 'failure_anchor3_list': list(anchors)}
        for tid, anchors in failure_anchors.items()
    ])

    adv_path = TEMP_DIR / "advisory_anchors.parquet"
    fail_path = TEMP_DIR / "failure_anchors.parquet"

    adv_df.to_parquet(adv_path, engine='pyarrow', index=False)
    fail_df.to_parquet(fail_path, engine='pyarrow', index=False)

    logger.info(f"  Advisory anchors: {len(adv_df):,} tests")
    logger.info(f"  Failure anchors: {len(fail_df):,} tests")

    # Clean up
    del advisory_anchors, failure_anchors, adv_df, fail_df
    gc.collect()

    # Step 4: Compute apathy features using DuckDB
    logger.info("\n[Step 4] Computing apathy features...")

    conn = duckdb.connect()
    conn.execute("SET threads = 1")
    conn.execute("SET memory_limit = '3GB'")
    conn.execute("SET preserve_insertion_order = false")
    conn.execute(f"SET temp_directory = '{TEMP_DIR}'")

    # Get year-months
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
                    LEFT JOIN read_parquet('{adv_path}') a ON h.prev_cycle_test_id = a.test_id
                    LEFT JOIN read_parquet('{fail_path}') f ON h.test_id = f.test_id
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
