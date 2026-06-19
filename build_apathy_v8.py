#!/usr/bin/env python3
"""
Build Apathy Features V8 - Polars (Fastest)
============================================
Uses Polars for blazing fast CSV processing.
"""

import polars as pl
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
OUTPUT_PATH = WORK_ROOT / "code_level_apathy_features_v8.parquet"
TEMP_DIR = Path("/tmp/autosafe_apathy_v8")
TEMP_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(AUTOSAFE_ROOT))

TEST_ITEM_FILES = {
    2022: AUTOSAFE_ROOT / "test_items/2022/test_item.csv",
    2023: AUTOSAFE_ROOT / "test_items/2023/test_item.csv",
}

ALPHA = 1
BETA = 5


def main():
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info("BUILD APATHY FEATURES V8 (POLARS)")
    logger.info("=" * 60)

    # Step 1: Load RFR -> anchor mapping
    logger.info("\n[Step 1] Loading anchor mapping...")
    from dvsa_anchor_mapping import AnchorMapper
    mapper = AnchorMapper(AUTOSAFE_ROOT)
    mapping_df = mapper.create_rfr_anchor_mapping()

    # Convert to polars
    mapping_pl = pl.from_pandas(mapping_df[['rfr_id', 'anchor_3']])
    logger.info(f"  Loaded {len(mapping_pl):,} mappings")
    del mapping_df

    # Step 2: Process test items with Polars (streaming/lazy)
    logger.info("\n[Step 2] Processing test items with Polars...")

    advisory_dfs = []
    failure_dfs = []

    for year, csv_path in TEST_ITEM_FILES.items():
        if not csv_path.exists():
            logger.warning(f"  {year}: File not found")
            continue

        logger.info(f"  {year}: Reading CSV...")

        # Lazy scan - much more memory efficient
        lf = pl.scan_csv(
            csv_path,
            separator='|',
            schema={'test_id': pl.Int64, 'rfr_id': pl.Int32, 'rfr_type_code': pl.Utf8,
                    'location_id': pl.Int32, 'dangerous_mark': pl.Utf8}
        )

        # Advisory anchors
        logger.info(f"  {year}: Building advisory anchors...")
        adv_lf = (
            lf.filter(pl.col('rfr_type_code') == 'A')
            .select(['test_id', 'rfr_id'])
            .join(mapping_pl.lazy(), on='rfr_id', how='inner')
            .group_by('test_id')
            .agg(pl.col('anchor_3').unique().alias('advisory_anchor3_list'))
        )
        adv_df = adv_lf.collect()
        logger.info(f"    {len(adv_df):,} tests with advisory anchors")
        advisory_dfs.append(adv_df)

        # Failure anchors
        logger.info(f"  {year}: Building failure anchors...")
        fail_lf = (
            lf.filter(pl.col('rfr_type_code').is_in(['F', 'M']))
            .select(['test_id', 'rfr_id'])
            .join(mapping_pl.lazy(), on='rfr_id', how='inner')
            .group_by('test_id')
            .agg(pl.col('anchor_3').unique().alias('failure_anchor3_list'))
        )
        fail_df = fail_lf.collect()
        logger.info(f"    {len(fail_df):,} tests with failure anchors")
        failure_dfs.append(fail_df)

    # Step 3: Combine and save
    logger.info("\n[Step 3] Combining anchor files...")

    combined_adv = pl.concat(advisory_dfs)
    combined_fail = pl.concat(failure_dfs)

    # If same test_id appears in multiple years, combine their anchor lists
    combined_adv = combined_adv.group_by('test_id').agg(
        pl.col('advisory_anchor3_list').flatten().unique()
    )
    combined_fail = combined_fail.group_by('test_id').agg(
        pl.col('failure_anchor3_list').flatten().unique()
    )

    logger.info(f"  Advisory: {len(combined_adv):,} unique tests")
    logger.info(f"  Failure: {len(combined_fail):,} unique tests")

    # Save to parquet
    adv_path = TEMP_DIR / "advisory_combined.parquet"
    fail_path = TEMP_DIR / "failure_combined.parquet"

    combined_adv.write_parquet(adv_path)
    combined_fail.write_parquet(fail_path)

    # Clean up polars dataframes
    del combined_adv, combined_fail, advisory_dfs, failure_dfs, mapping_pl

    # Step 4: Compute apathy features with DuckDB
    logger.info("\n[Step 4] Computing apathy features with DuckDB...")

    conn = duckdb.connect()
    conn.execute("SET threads = 2")
    conn.execute("SET memory_limit = '3GB'")
    conn.execute(f"SET temp_directory = '{TEMP_DIR}'")

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

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"\nCompleted in {elapsed:.1f}s")
    logger.info(f"Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
