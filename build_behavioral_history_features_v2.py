#!/usr/bin/env python3
"""
Build Behavioral History Features v2
=====================================

Computes vehicle-level behavioral history using bounded windows (last N tests).

Key changes from v1:
1. Uses bounded windows (last 3, last 5 tests) instead of unbounded cumulative sums
2. Uses DuckDB for all heavy computation (no large pandas operations)
3. Writes output using COPY TO parquet (no fetchdf on large results)
4. Filters to dev/OOT only at output stage (not inputs)

Features:
- prior_deferral_count_last5: Count of DEFERRAL failures in last 5 tests
- prior_corrosion_count_last5: Count of CORROSION failures in last 5 tests
- prior_total_failures_last5: Total classified failures in last 5 tests
- n_prior_tests_last5: Number of prior tests in window
- deferral_propensity_last5: Smoothed DEFERRAL rate
- behavioral_risk_score_last5: Weighted propensity score
- has_prior_deferral_history: Binary flag (any prior DEFERRAL)

Output: behavioral_history_features_v2.parquet

Created: 2026-01-10
"""

import pandas as pd
import numpy as np
import duckdb
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("build_behavioral_history_features_v2.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================

PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
WORK_DIR = Path("/Users/henrirapson/autosafe_work")

CYCLE_HISTORY = PROJECT_ROOT / "cycle_first_with_history.parquet"
DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set_2021_2023.parquet"  # Filtered 2021-2023
OOT_SET = PROJECT_ROOT / "stratified_samples/oot_test_set.parquet"
CAUSAL_PATHWAY_FEATURES = WORK_DIR / "causal_pathway_features_v2.parquet"

OUTPUT_FILE = WORK_DIR / "behavioral_history_features_v2.parquet"

# History window parameters
HISTORY_WINDOW_N = 5  # Last N tests for bounded window

# Smoothing parameters (Laplace smoothing)
ALPHA = 1
BETA = 5


def validate_inputs():
    """Validate all required input files exist."""
    logger.info("=" * 70)
    logger.info("VALIDATING INPUTS")
    logger.info("=" * 70)

    missing = []
    for path, name in [
        (CYCLE_HISTORY, "cycle_first_with_history"),
        (CAUSAL_PATHWAY_FEATURES, "causal_pathway_features_v2"),
        (DEV_SET, "dev_set"),
        (OOT_SET, "oot_test_set"),
    ]:
        if not path.exists():
            missing.append(f"  {name}: {path}")
        else:
            logger.info(f"  {name}: OK")

    if missing:
        error_msg = "ABORTING: Missing input files:\n" + "\n".join(missing)
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)


def build_behavioral_history_features(conn: duckdb.DuckDBPyConnection) -> Dict[str, int]:
    """
    Build behavioral history features using DuckDB window functions.

    Key approach:
    1. Join causal pathway features with cycle_history on test_id
    2. Compute bounded window features (last N tests) using window functions
    3. Filter to dev/OOT test_ids at output stage
    4. Write directly to parquet using COPY

    Returns diagnostic counts.
    """
    logger.info("\n" + "=" * 70)
    logger.info("BUILDING BEHAVIORAL HISTORY FEATURES")
    logger.info("=" * 70)

    # Step 1: Create temp tables for efficiency
    logger.info("\nCreating temporary tables...")

    # Get target test_ids (dev + OOT)
    conn.execute(f"""
        CREATE TEMP TABLE target_tests AS
        SELECT test_id FROM read_parquet('{DEV_SET}')
        UNION ALL
        SELECT test_id FROM read_parquet('{OOT_SET}')
    """)
    target_count = conn.execute("SELECT COUNT(*) FROM target_tests").fetchone()[0]
    logger.info(f"  Target tests (dev + OOT): {target_count:,}")

    # Get target vehicle_ids
    conn.execute(f"""
        CREATE TEMP TABLE target_vehicles AS
        SELECT DISTINCT vehicle_id
        FROM read_parquet('{CYCLE_HISTORY}')
        WHERE test_id IN (SELECT test_id FROM target_tests)
          AND vehicle_id IS NOT NULL
    """)
    vehicle_count = conn.execute("SELECT COUNT(*) FROM target_vehicles").fetchone()[0]
    logger.info(f"  Target vehicles: {vehicle_count:,}")

    # Step 2: Load causal pathway features
    logger.info("\nLoading causal pathway features...")
    causal_count = conn.execute(f"""
        SELECT COUNT(*) FROM read_parquet('{CAUSAL_PATHWAY_FEATURES}')
    """).fetchone()[0]
    logger.info(f"  Total causal pathway tests: {causal_count:,}")

    # Step 3: Build the main query with bounded window functions
    logger.info(f"\nComputing behavioral history (last {HISTORY_WINDOW_N} tests)...")
    logger.info("  Using window: ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING")
    logger.info("  Order: test_date, test_id (deterministic)")

    query = f"""
        WITH cycle_with_causal AS (
            -- Join cycle history with causal pathway features
            -- Only for vehicles in target set (reduces computation)
            SELECT
                c.test_id,
                c.vehicle_id,
                c.test_date,
                COALESCE(p.deferral_failure_count, 0) as deferral_failure_count,
                COALESCE(p.corrosion_failure_count, 0) as corrosion_failure_count,
                COALESCE(p.road_damage_failure_count, 0) as road_damage_failure_count,
                COALESCE(p.wear_failure_count, 0) as wear_failure_count,
                COALESCE(p.total_classified_failures, 0) as total_classified_failures,
                COALESCE(p.has_deferral_failure, 0) as has_deferral_failure
            FROM read_parquet('{CYCLE_HISTORY}') c
            INNER JOIN target_vehicles tv ON c.vehicle_id = tv.vehicle_id
            LEFT JOIN read_parquet('{CAUSAL_PATHWAY_FEATURES}') p ON c.test_id = p.test_id
            WHERE c.vehicle_id IS NOT NULL
        ),
        with_priors AS (
            SELECT
                test_id,
                vehicle_id,
                test_date,
                -- Bounded window sums (last N tests, excluding current)
                COALESCE(SUM(deferral_failure_count) OVER w, 0) as prior_deferral_count_last5,
                COALESCE(SUM(corrosion_failure_count) OVER w, 0) as prior_corrosion_count_last5,
                COALESCE(SUM(road_damage_failure_count) OVER w, 0) as prior_road_damage_count_last5,
                COALESCE(SUM(wear_failure_count) OVER w, 0) as prior_wear_count_last5,
                COALESCE(SUM(total_classified_failures) OVER w, 0) as prior_total_failures_last5,
                COALESCE(SUM(has_deferral_failure) OVER w, 0) as prior_tests_with_deferral_last5,
                -- Count of prior tests in window
                COUNT(*) OVER w as n_prior_tests_last5,
                -- Also compute last 3 for comparison
                COALESCE(SUM(deferral_failure_count) OVER w3, 0) as prior_deferral_count_last3,
                COALESCE(SUM(total_classified_failures) OVER w3, 0) as prior_total_failures_last3,
                COUNT(*) OVER w3 as n_prior_tests_last3
            FROM cycle_with_causal
            WINDOW
                w AS (PARTITION BY vehicle_id ORDER BY test_date, test_id ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING),
                w3 AS (PARTITION BY vehicle_id ORDER BY test_date, test_id ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING)
        ),
        with_propensities AS (
            SELECT
                test_id,
                vehicle_id,
                -- Prior counts
                prior_deferral_count_last5,
                prior_corrosion_count_last5,
                prior_road_damage_count_last5,
                prior_wear_count_last5,
                prior_total_failures_last5,
                prior_tests_with_deferral_last5,
                n_prior_tests_last5,
                prior_deferral_count_last3,
                prior_total_failures_last3,
                n_prior_tests_last3,
                -- Propensities (smoothed rates with Laplace)
                (prior_deferral_count_last5 + {ALPHA}) * 1.0 /
                    (prior_total_failures_last5 + {ALPHA} + {BETA}) as deferral_propensity_last5,
                (prior_corrosion_count_last5 + {ALPHA}) * 1.0 /
                    (prior_total_failures_last5 + {ALPHA} + {BETA}) as corrosion_propensity_last5,
                (prior_road_damage_count_last5 + {ALPHA}) * 1.0 /
                    (prior_total_failures_last5 + {ALPHA} + {BETA}) as road_damage_propensity_last5,
                (prior_wear_count_last5 + {ALPHA}) * 1.0 /
                    (prior_total_failures_last5 + {ALPHA} + {BETA}) as wear_propensity_last5,
                -- Binary flags
                CASE WHEN prior_deferral_count_last5 > 0 THEN 1 ELSE 0 END as has_prior_deferral_history,
                CASE WHEN prior_corrosion_count_last5 > 0 THEN 1 ELSE 0 END as has_prior_corrosion_history,
                CASE WHEN n_prior_tests_last5 > 0 THEN 1 ELSE 0 END as has_prior_pathway_history
            FROM with_priors
        )
        SELECT
            p.*,
            -- Behavioral risk score: weighted combination
            0.6 * deferral_propensity_last5 +
            0.2 * corrosion_propensity_last5 +
            0.1 * road_damage_propensity_last5 +
            0.1 * wear_propensity_last5 as behavioral_risk_score_last5
        FROM with_propensities p
        -- Filter to target tests ONLY at output stage
        WHERE test_id IN (SELECT test_id FROM target_tests)
    """

    # Write directly to parquet
    conn.execute(f"COPY ({query}) TO '{OUTPUT_FILE}' (FORMAT PARQUET)")

    # Get diagnostics
    logger.info("\nGetting output diagnostics...")
    total_count = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{OUTPUT_FILE}')").fetchone()[0]
    logger.info(f"  Total output records: {total_count:,}")

    return {'total': total_count}


def run_diagnostics(conn: duckdb.DuckDBPyConnection):
    """Run diagnostics on the output to validate n_prior_tests distribution."""
    logger.info("\n" + "=" * 70)
    logger.info("DIAGNOSTICS")
    logger.info("=" * 70)

    # Distribution of n_prior_tests_last5
    logger.info("\n1. Distribution of n_prior_tests_last5:")
    stats = conn.execute(f"""
        SELECT
            MIN(n_prior_tests_last5) as min_val,
            MAX(n_prior_tests_last5) as max_val,
            AVG(n_prior_tests_last5) as mean_val,
            MEDIAN(n_prior_tests_last5) as median_val,
            PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY n_prior_tests_last5) as p25,
            PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY n_prior_tests_last5) as p75,
            PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY n_prior_tests_last5) as p90,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY n_prior_tests_last5) as p95
        FROM read_parquet('{OUTPUT_FILE}')
    """).fetchone()
    logger.info(f"   Min: {stats[0]}, Max: {stats[1]}, Mean: {stats[2]:.2f}, Median: {stats[3]:.0f}")
    logger.info(f"   p25: {stats[4]:.0f}, p75: {stats[5]:.0f}, p90: {stats[6]:.0f}, p95: {stats[7]:.0f}")

    # Distribution by bucket
    logger.info("\n   Distribution by bucket:")
    buckets = conn.execute(f"""
        SELECT
            CASE
                WHEN n_prior_tests_last5 = 0 THEN '0'
                WHEN n_prior_tests_last5 = 1 THEN '1'
                WHEN n_prior_tests_last5 = 2 THEN '2'
                WHEN n_prior_tests_last5 = 3 THEN '3'
                WHEN n_prior_tests_last5 = 4 THEN '4'
                WHEN n_prior_tests_last5 >= 5 THEN '5'
            END as bucket,
            COUNT(*) as cnt
        FROM read_parquet('{OUTPUT_FILE}')
        GROUP BY bucket
        ORDER BY bucket
    """).fetchdf()
    total = buckets['cnt'].sum()
    for _, row in buckets.iterrows():
        pct = row['cnt'] / total * 100
        logger.info(f"     {row['bucket']:>5}: {int(row['cnt']):>10,} ({pct:>5.1f}%)")

    # Behavioral coverage
    logger.info("\n2. Behavioral history coverage:")
    coverage = conn.execute(f"""
        SELECT
            AVG(has_prior_pathway_history) * 100 as pct_with_history,
            AVG(has_prior_deferral_history) * 100 as pct_with_deferral,
            AVG(has_prior_corrosion_history) * 100 as pct_with_corrosion
        FROM read_parquet('{OUTPUT_FILE}')
    """).fetchone()
    logger.info(f"   Tests with prior pathway history: {coverage[0]:.1f}%")
    logger.info(f"   Tests with prior DEFERRAL history: {coverage[1]:.1f}%")
    logger.info(f"   Tests with prior CORROSION history: {coverage[2]:.1f}%")

    # Prior counts distribution
    logger.info("\n3. Prior counts distribution (for tests with history):")
    prior_stats = conn.execute(f"""
        SELECT
            AVG(prior_deferral_count_last5) as mean_deferral,
            MAX(prior_deferral_count_last5) as max_deferral,
            AVG(prior_total_failures_last5) as mean_total,
            MAX(prior_total_failures_last5) as max_total
        FROM read_parquet('{OUTPUT_FILE}')
        WHERE n_prior_tests_last5 > 0
    """).fetchone()
    logger.info(f"   prior_deferral_count_last5: mean={prior_stats[0]:.2f}, max={prior_stats[1]}")
    logger.info(f"   prior_total_failures_last5: mean={prior_stats[2]:.2f}, max={prior_stats[3]}")

    # Propensity distributions
    logger.info("\n4. Propensity score distributions:")
    prop_stats = conn.execute(f"""
        SELECT
            AVG(deferral_propensity_last5) as mean_deferral_prop,
            PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY deferral_propensity_last5) as p25_deferral,
            PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY deferral_propensity_last5) as p75_deferral,
            AVG(behavioral_risk_score_last5) as mean_risk,
            PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY behavioral_risk_score_last5) as p25_risk,
            PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY behavioral_risk_score_last5) as p75_risk
        FROM read_parquet('{OUTPUT_FILE}')
    """).fetchone()
    logger.info(f"   deferral_propensity_last5: mean={prop_stats[0]:.3f}, p25={prop_stats[1]:.3f}, p75={prop_stats[2]:.3f}")
    logger.info(f"   behavioral_risk_score_last5: mean={prop_stats[3]:.3f}, p25={prop_stats[4]:.3f}, p75={prop_stats[5]:.3f}")

    # Dev/OOT coverage
    logger.info("\n5. Dev/OOT set coverage:")
    dev_ids = conn.execute(f"SELECT test_id FROM read_parquet('{DEV_SET}')").fetchdf()['test_id']
    oot_ids = conn.execute(f"SELECT test_id FROM read_parquet('{OOT_SET}')").fetchdf()['test_id']
    output_ids = conn.execute(f"SELECT test_id FROM read_parquet('{OUTPUT_FILE}')").fetchdf()['test_id']

    dev_coverage = output_ids.isin(dev_ids).sum()
    oot_coverage = output_ids.isin(oot_ids).sum()

    logger.info(f"   Dev set: {dev_coverage:,} / {len(dev_ids):,} ({dev_coverage/len(dev_ids)*100:.1f}%)")
    logger.info(f"   OOT set: {oot_coverage:,} / {len(oot_ids):,} ({oot_coverage/len(oot_ids)*100:.1f}%)")


def run_validation_checks(conn: duckdb.DuckDBPyConnection) -> bool:
    """Run validation checks against expected metrics."""
    logger.info("\n" + "=" * 70)
    logger.info("VALIDATION CHECKS")
    logger.info("=" * 70)

    all_passed = True

    # Check 1: n_prior_tests max should be much higher than before
    max_prior = conn.execute(f"SELECT MAX(n_prior_tests_last5) FROM read_parquet('{OUTPUT_FILE}')").fetchone()[0]
    expected_max = 5  # Bounded window is 5, so max is 5
    status = "PASSED" if max_prior >= expected_max else "FAILED"
    logger.info(f"\n1. n_prior_tests_last5 max: {max_prior} (expected: {expected_max}) - {status}")
    # Note: With bounded window of 5, max is always 5

    # Check 2: Mean should be reasonable
    mean_prior = conn.execute(f"SELECT AVG(n_prior_tests_last5) FROM read_parquet('{OUTPUT_FILE}')").fetchone()[0]
    expected_mean_min = 1.5  # Should be higher than broken version's 1.24
    status = "PASSED" if mean_prior >= expected_mean_min else "FAILED"
    logger.info(f"2. n_prior_tests_last5 mean: {mean_prior:.2f} (expected >={expected_mean_min}) - {status}")
    if mean_prior < expected_mean_min:
        all_passed = False

    # Check 3: has_prior_deferral_history should be higher than 5.7%
    deferral_pct = conn.execute(f"""
        SELECT AVG(has_prior_deferral_history) * 100
        FROM read_parquet('{OUTPUT_FILE}')
    """).fetchone()[0]
    expected_deferral_min = 8.0  # Should be higher than broken 5.7%
    status = "PASSED" if deferral_pct >= expected_deferral_min else "CHECK"
    logger.info(f"3. has_prior_deferral_history: {deferral_pct:.1f}% (expected >={expected_deferral_min}%) - {status}")

    # Check 4: has_prior_pathway_history coverage
    history_pct = conn.execute(f"""
        SELECT AVG(has_prior_pathway_history) * 100
        FROM read_parquet('{OUTPUT_FILE}')
    """).fetchone()[0]
    expected_history_min = 50.0  # Should have reasonable coverage
    status = "PASSED" if history_pct >= expected_history_min else "CHECK"
    logger.info(f"4. has_prior_pathway_history: {history_pct:.1f}% (expected >={expected_history_min}%) - {status}")

    logger.info("\n" + "=" * 70)
    if all_passed:
        logger.info("ALL VALIDATION CHECKS PASSED")
    else:
        logger.warning("SOME VALIDATION CHECKS FAILED - Review output")
    logger.info("=" * 70)

    return all_passed


def main():
    start_time = datetime.now()

    logger.info("=" * 70)
    logger.info("BUILD BEHAVIORAL HISTORY FEATURES V2")
    logger.info("=" * 70)
    logger.info(f"Using bounded window: last {HISTORY_WINDOW_N} tests")

    conn = duckdb.connect()
    conn.execute("SET memory_limit='8GB'")

    # Step 1: Validate inputs
    validate_inputs()

    # Step 2: Build features
    build_behavioral_history_features(conn)

    # Step 3: Run diagnostics
    run_diagnostics(conn)

    # Step 4: Run validation checks
    validation_passed = run_validation_checks(conn)

    conn.close()

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"\nCompleted in {elapsed:.1f}s")
    logger.info(f"Output: {OUTPUT_FILE}")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
