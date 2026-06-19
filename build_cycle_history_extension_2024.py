"""
Build Cycle History Extension for 2024 OOT Tests
================================================

Computes cycle_history features for OOT tests by looking up their
prior tests from existing cycle_history (2023 and earlier).

This raises OOT coverage from 24.9% to 89.3%.

Output: cycle_history_extension_2024.parquet
"""

import duckdb
from pathlib import Path
from datetime import datetime

# Paths
PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
WORK_DIR = Path.home() / "autosafe_work"

CYCLE_HISTORY = PROJECT_ROOT / "cycle_first_with_history.parquet"
OOT_SET = PROJECT_ROOT / "stratified_samples/oot_test_set.parquet"
OUTPUT_FILE = WORK_DIR / "cycle_history_extension_2024.parquet"


def main():
    start_time = datetime.now()
    print("=" * 70)
    print("BUILD CYCLE HISTORY EXTENSION FOR 2024 OOT TESTS")
    print("=" * 70)

    conn = duckdb.connect()
    conn.execute("SET memory_limit='8GB'")

    # Build extension by looking up prior tests for each OOT vehicle
    print("\n[1] Computing prior test features for OOT tests...")

    query = f"""
    WITH oot_tests AS (
        -- All OOT tests with vehicle_id
        SELECT
            o.test_id,
            o.test_date,
            o.vehicle_id
        FROM read_parquet('{OOT_SET}') o
        WHERE o.vehicle_id IS NOT NULL
    ),
    prior_tests AS (
        -- All tests from cycle_history up to 2023
        -- Get the most recent test per vehicle
        SELECT
            h.vehicle_id,
            h.test_date as prev_test_date,
            h.test_id as prev_cycle_test_id,
            h.outcome as prev_outcome,
            h.prev_advisory_brakes,
            h.prev_advisory_suspension,
            h.prev_advisory_steering,
            h.prev_advisory_tyres,
            h.prev_advisory_total,
            h.prev_advisory_known,
            h.advisory_trend,
            h.prev_has_safety_advisory
        FROM read_parquet('{CYCLE_HISTORY}') h
        WHERE EXTRACT(YEAR FROM h.test_date) <= 2023
    ),
    matched AS (
        -- Join OOT tests with their most recent prior test
        SELECT
            o.test_id,
            o.test_date,
            o.vehicle_id,
            p.prev_test_date,
            p.prev_cycle_test_id,
            p.prev_outcome,
            p.prev_advisory_brakes,
            p.prev_advisory_suspension,
            p.prev_advisory_steering,
            p.prev_advisory_tyres,
            p.prev_advisory_total,
            p.prev_advisory_known,
            p.advisory_trend,
            p.prev_has_safety_advisory,
            DATE_DIFF('day', p.prev_test_date, o.test_date) as days_since_prev_cycle,
            -- Derive prev_cycle_outcome_band
            CASE
                WHEN p.prev_outcome = 'P' THEN 'PASS'
                WHEN p.prev_outcome = 'F' THEN 'FAIL'
                WHEN p.prev_outcome IS NULL THEN 'NO_PRIOR_RECORDED'
                ELSE 'NO_PRIOR_RECORDED'
            END as prev_cycle_outcome_band,
            -- Derive gap_band
            CASE
                WHEN p.prev_test_date IS NULL THEN 'NO_PRIOR'
                WHEN DATE_DIFF('day', p.prev_test_date, o.test_date) <= 365 THEN 'ON_TIME'
                WHEN DATE_DIFF('day', p.prev_test_date, o.test_date) <= 395 THEN 'LATE_1MO'
                WHEN DATE_DIFF('day', p.prev_test_date, o.test_date) <= 455 THEN 'LATE_3MO'
                WHEN DATE_DIFF('day', p.prev_test_date, o.test_date) <= 545 THEN 'LATE_6MO'
                ELSE 'LATE_6MO_PLUS'
            END as gap_band
        FROM oot_tests o
        LEFT JOIN prior_tests p ON o.vehicle_id = p.vehicle_id
        -- Keep only the most recent prior test per OOT test
        QUALIFY ROW_NUMBER() OVER (PARTITION BY o.test_id ORDER BY p.prev_test_date DESC) = 1
    )
    SELECT
        test_id,
        test_date,
        vehicle_id,
        prev_cycle_test_id,
        days_since_prev_cycle,
        prev_cycle_outcome_band,
        gap_band,
        prev_advisory_brakes,
        prev_advisory_suspension,
        prev_advisory_steering,
        prev_advisory_tyres,
        prev_advisory_total,
        prev_advisory_known,
        advisory_trend,
        prev_has_safety_advisory
    FROM matched
    """

    # Execute and save
    conn.execute(f"COPY ({query}) TO '{OUTPUT_FILE}' (FORMAT PARQUET)")

    # Verify output
    print("\n[2] Verifying output...")
    stats = conn.execute(f"""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN days_since_prev_cycle IS NOT NULL THEN 1 ELSE 0 END) as has_prior,
            ROUND(100.0 * SUM(CASE WHEN days_since_prev_cycle IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 1) as coverage_pct,
            AVG(days_since_prev_cycle) as avg_days
        FROM read_parquet('{OUTPUT_FILE}')
    """).fetchdf()
    print(stats.to_string())

    # Breakdown by gap_band
    print("\n[3] Gap band distribution:")
    gap_dist = conn.execute(f"""
        SELECT gap_band, COUNT(*) as n, ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) as pct
        FROM read_parquet('{OUTPUT_FILE}')
        GROUP BY gap_band
        ORDER BY n DESC
    """).fetchdf()
    print(gap_dist.to_string())

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\n[4] Saved to: {OUTPUT_FILE}")
    print(f"    Elapsed: {elapsed:.1f}s")
    print("=" * 70)

    conn.close()


if __name__ == "__main__":
    main()
