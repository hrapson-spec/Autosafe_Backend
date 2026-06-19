#!/usr/bin/env python3
"""
Build Apathy Features V2 - Memory Optimized
============================================
Uses chunked processing to handle large datasets.
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
OUTPUT_PATH = WORK_ROOT / "code_level_apathy_features_v2.parquet"
TEMP_DIR = Path("/tmp/autosafe_apathy_v2")
TEMP_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(AUTOSAFE_ROOT))

# Use only 2022+2023 which have consistent schema
TEST_ITEM_FILES = [
    AUTOSAFE_ROOT / "test_items/2022/test_item.csv",
    AUTOSAFE_ROOT / "test_items/2023/test_item.csv",
]

ALPHA = 1
BETA = 5


def main():
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info("BUILD APATHY FEATURES V2 (MEMORY OPTIMIZED)")
    logger.info("=" * 60)

    conn = duckdb.connect()
    conn.execute("SET threads = 1")  # Reduce memory
    conn.execute("SET memory_limit = '4GB'")
    conn.execute("SET preserve_insertion_order = false")
    conn.execute(f"SET temp_directory = '{TEMP_DIR}'")

    # Step 1: Load RFR -> anchor mapping
    logger.info("\n[Step 1] Loading anchor mapping...")
    from dvsa_anchor_mapping import AnchorMapper
    mapper = AnchorMapper(AUTOSAFE_ROOT)
    mapping_df = mapper.create_rfr_anchor_mapping()
    conn.register('rfr_anchor_mapping', mapping_df)
    logger.info(f"  Loaded {len(mapping_df):,} mappings")

    # Step 2: Process test items in chunks and build anchor sets
    logger.info("\n[Step 2] Processing test items to parquet...")
    
    test_items_parquet = TEMP_DIR / "test_items_combined.parquet"
    
    parts = []
    for i, csv_path in enumerate(TEST_ITEM_FILES):
        if not csv_path.exists():
            continue
        logger.info(f"  Processing {csv_path.name}...")
        part_path = TEMP_DIR / f"test_items_{i}.parquet"
        
        conn.execute(f"""
            COPY (
                SELECT
                    CAST(test_id AS BIGINT) as test_id,
                    CAST(rfr_id AS INTEGER) as rfr_id,
                    CAST(rfr_type_code AS VARCHAR) as rfr_type_code
                FROM read_csv('{csv_path}', delim='|', header=true, auto_detect=true, ignore_errors=true)
                WHERE rfr_type_code IN ('A', 'F', 'M')
            ) TO '{part_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        cnt = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{part_path}')").fetchone()[0]
        logger.info(f"    {cnt:,} records")
        parts.append(str(part_path))

    # Step 3: Build advisory and failure anchor sets per test
    logger.info("\n[Step 3] Building anchor sets per test (chunked)...")
    
    # Get relevant test_ids from cycle history
    relevant_tests = TEMP_DIR / "relevant_tests.parquet"
    conn.execute(f"""
        COPY (
            SELECT DISTINCT test_id FROM (
                SELECT test_id FROM read_parquet('{CYCLE_HISTORY_PATH}')
                UNION ALL
                SELECT prev_cycle_test_id as test_id FROM read_parquet('{CYCLE_HISTORY_PATH}')
                WHERE prev_cycle_test_id IS NOT NULL
            )
        ) TO '{relevant_tests}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n_relevant = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{relevant_tests}')").fetchone()[0]
    logger.info(f"  Relevant test_ids: {n_relevant:,}")

    # Build advisory anchors
    logger.info("  Building advisory anchors...")
    adv_anchors = TEMP_DIR / "advisory_anchors.parquet"
    
    parts_str = "', '".join(parts)
    conn.execute(f"""
        COPY (
            SELECT
                ti.test_id,
                LIST(DISTINCT CAST(m.anchor_3 AS VARCHAR)) as advisory_anchor3_list
            FROM read_parquet(['{parts_str}']) ti
            JOIN rfr_anchor_mapping m ON ti.rfr_id = m.rfr_id
            JOIN read_parquet('{relevant_tests}') r ON ti.test_id = r.test_id
            WHERE ti.rfr_type_code = 'A'
            GROUP BY ti.test_id
        ) TO '{adv_anchors}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n_adv = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{adv_anchors}')").fetchone()[0]
    logger.info(f"    Tests with advisories: {n_adv:,}")

    # Build failure anchors
    logger.info("  Building failure anchors...")
    fail_anchors = TEMP_DIR / "failure_anchors.parquet"
    conn.execute(f"""
        COPY (
            SELECT
                ti.test_id,
                LIST(DISTINCT CAST(m.anchor_3 AS VARCHAR)) as failure_anchor3_list
            FROM read_parquet(['{parts_str}']) ti
            JOIN rfr_anchor_mapping m ON ti.rfr_id = m.rfr_id
            JOIN read_parquet('{relevant_tests}') r ON ti.test_id = r.test_id
            WHERE ti.rfr_type_code IN ('F', 'M')
            GROUP BY ti.test_id
        ) TO '{fail_anchors}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n_fail = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{fail_anchors}')").fetchone()[0]
    logger.info(f"    Tests with failures: {n_fail:,}")

    # Step 4: Join and compute apathy features
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
            LEFT JOIN read_parquet('{adv_anchors}') a ON h.prev_cycle_test_id = a.test_id
            LEFT JOIN read_parquet('{fail_anchors}') f ON h.test_id = f.test_id
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

    conn.close()
    
    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"\nCompleted in {elapsed:.1f}s")
    logger.info(f"Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
