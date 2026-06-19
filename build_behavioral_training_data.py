#!/usr/bin/env python3
"""
Build Behavioral Training Dataset
==================================

Creates training dataset for Segment C (Behavioral) models using full 2023-2024 data.

Data flow:
1. cycle_first_with_history (2023-2024)
2. INNER JOIN prior_apathy_features (has_prior_apathy = 1)
3. INNER JOIN filtered_failure_targets (get systemic/random labels)
4. Split: 2023 H1 (train), 2023 H2 (val), 2024 (OOT)

Output: behavioral_training_data.parquet

Created: 2026-01-09
"""

import duckdb
import logging
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Paths
PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
WORK_ROOT = Path("/Users/henrirapson/autosafe_work")

CYCLE_HISTORY = PROJECT_ROOT / "cycle_first_with_history.parquet"
PRIOR_APATHY = WORK_ROOT / "prior_apathy_features.parquet"
FAILURE_TARGETS = PROJECT_ROOT / "filtered_failure_targets.parquet"
NEGLECT_FEATURES = PROJECT_ROOT / "neglect_features.parquet"

OUTPUT_PATH = WORK_ROOT / "behavioral_training_data.parquet"


def main():
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info("BUILD BEHAVIORAL TRAINING DATASET")
    logger.info("=" * 60)

    conn = duckdb.connect()
    conn.execute("SET memory_limit = '4GB'")
    conn.execute("SET threads = 1")
    conn.execute("SET preserve_insertion_order = false")

    # Step 1: Check data availability
    logger.info("\n[Step 1] Checking data sources...")

    for name, path in [
        ("Cycle History", CYCLE_HISTORY),
        ("Prior Apathy", PRIOR_APATHY),
        ("Failure Targets", FAILURE_TARGETS),
    ]:
        n = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{path}')").fetchone()[0]
        logger.info(f"  {name}: {n:,} rows")

    # Step 2: Count behavioral tests by year
    logger.info("\n[Step 2] Counting behavioral tests by year...")

    year_stats = conn.execute(f"""
        SELECT
            YEAR(p.test_date) as year,
            COUNT(*) as total_tests,
            SUM(CASE WHEN p.has_prior_apathy = 1 THEN 1 ELSE 0 END) as behavioral_tests,
            SUM(CASE WHEN t.has_systemic_failure = 1 THEN 1 ELSE 0 END) as with_systemic,
            SUM(CASE WHEN t.has_random_failure = 1 THEN 1 ELSE 0 END) as with_random
        FROM read_parquet('{PRIOR_APATHY}') p
        LEFT JOIN read_parquet('{FAILURE_TARGETS}') t ON p.test_id = t.test_id
        WHERE YEAR(p.test_date) >= 2023
        GROUP BY 1
        ORDER BY 1
    """).fetchall()

    for year, total, behavioral, systemic, random in year_stats:
        logger.info(f"  {year}: {total:,} total, {behavioral:,} behavioral, {systemic:,} systemic, {random:,} random")

    # Step 3: Build behavioral training dataset
    logger.info("\n[Step 3] Building behavioral training dataset...")
    logger.info("  Filter: has_prior_apathy = 1 AND year >= 2023")

    # Note: filtered_failure_targets only has 2022-2023 data
    # For 2024, we use is_failure as target (can't split by systemic/random)
    # Focus on 2023 for training Mechanical/Neglect models (where we have labels)
    conn.execute(f"""
        COPY (
            SELECT
                p.test_id,
                p.vehicle_id,
                p.test_date,
                p.outcome,
                CASE WHEN p.outcome = 'FAIL' THEN 1 ELSE 0 END as is_failure,
                -- Prior apathy features
                p.has_prior_apathy,
                p.prior_apathy_hits,
                p.prior_apathy_rate,
                p.advisory_count_T2,
                p.failure_count_T1,
                -- From cycle history (available columns)
                h.prev_cycle_outcome_band,
                h.gap_band,
                h.days_since_prev_cycle,
                h.advisory_trend,
                h.prev_advisory_total,
                h.prev_advisory_brakes,
                h.prev_advisory_suspension,
                h.prev_advisory_steering,
                h.prev_advisory_tyres,
                -- Failure targets (only 2023 has these)
                COALESCE(t.has_systemic_failure, 0) as has_systemic_failure,
                COALESCE(t.has_random_failure, 0) as has_random_failure,
                COALESCE(t.has_any_failure, 0) as has_any_failure,
                -- Neglect features
                COALESCE(n.neglect_score, 0) as neglect_score,
                COALESCE(n.has_prior_random, 0) as has_prior_random,
                COALESCE(n.has_prior_systemic, 0) as has_prior_systemic,
                -- Temporal
                YEAR(p.test_date) as test_year,
                MONTH(p.test_date) as test_month
            FROM read_parquet('{PRIOR_APATHY}') p
            INNER JOIN read_parquet('{CYCLE_HISTORY}') h ON p.test_id = h.test_id
            LEFT JOIN read_parquet('{FAILURE_TARGETS}') t ON p.test_id = t.test_id
            LEFT JOIN read_parquet('{NEGLECT_FEATURES}') n ON p.test_id = n.test_id
            WHERE p.has_prior_apathy = 1
              AND YEAR(p.test_date) >= 2023
        ) TO '{OUTPUT_PATH}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
    """)

    # Step 4: Verify output
    logger.info("\n[Step 4] Verifying output...")

    stats = conn.execute(f"""
        SELECT
            COUNT(*) as total,
            SUM(is_failure) as failures,
            SUM(has_systemic_failure) as systemic,
            SUM(has_random_failure) as random,
            SUM(CASE WHEN has_systemic_failure = 1 AND has_random_failure = 1 THEN 1 ELSE 0 END) as both
        FROM read_parquet('{OUTPUT_PATH}')
    """).fetchone()

    logger.info(f"  Total behavioral tests: {stats[0]:,}")
    logger.info(f"  Failures: {stats[1]:,} ({100*stats[1]/stats[0]:.1f}%)")
    logger.info(f"  Systemic: {stats[2]:,} ({100*stats[2]/stats[0]:.1f}%)")
    logger.info(f"  Random: {stats[3]:,} ({100*stats[3]/stats[0]:.1f}%)")
    logger.info(f"  Both: {stats[4]:,} ({100*stats[4]/stats[0]:.1f}%)")

    # Split stats
    logger.info("\n  By temporal split:")
    split_stats = conn.execute(f"""
        SELECT
            CASE
                WHEN test_year = 2023 AND test_month <= 6 THEN 'Train (2023 H1)'
                WHEN test_year = 2023 AND test_month > 6 THEN 'Val (2023 H2)'
                ELSE 'OOT (2024)'
            END as split,
            COUNT(*) as n,
            ROUND(100.0 * SUM(is_failure) / COUNT(*), 1) as fail_rate,
            ROUND(100.0 * SUM(has_systemic_failure) / NULLIF(SUM(is_failure), 0), 1) as systemic_pct,
            ROUND(100.0 * SUM(has_random_failure) / NULLIF(SUM(is_failure), 0), 1) as random_pct
        FROM read_parquet('{OUTPUT_PATH}')
        GROUP BY 1
        ORDER BY 1
    """).fetchall()

    for split, n, fail_rate, systemic_pct, random_pct in split_stats:
        logger.info(f"    {split}: {n:,} tests, {fail_rate}% fail, {systemic_pct}% systemic, {random_pct}% random")

    conn.close()

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"\nCompleted in {elapsed:.1f}s")
    logger.info(f"Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
