#!/usr/bin/env python3
"""
Build Vehicle-Level Behavioral History Features
=================================================

For each vehicle, compute rolling history of causal pathways from prior tests.
All features are as-of-safe (only use data from tests BEFORE current test).

Features:
- prior_deferral_count: Cumulative DEFERRAL failures in vehicle history
- prior_corrosion_count: Cumulative CORROSION failures in vehicle history
- prior_road_damage_count: Cumulative ROAD_DAMAGE failures in vehicle history
- prior_wear_count: Cumulative WEAR failures in vehicle history
- deferral_propensity: prior_deferral_count / prior_total_failures (smoothed)
- behavioral_risk_score: Weighted combination of deferral propensity

Output: behavioral_history_features.parquet

Created: 2026-01-09
"""

import pandas as pd
import numpy as np
import duckdb
import logging
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("build_behavioral_history_features.log"),
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
DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set.parquet"
OOT_SET = PROJECT_ROOT / "stratified_samples/oot_test_set.parquet"
CAUSAL_PATHWAY_FEATURES = WORK_DIR / "causal_pathway_features.parquet"

OUTPUT_FILE = WORK_DIR / "behavioral_history_features.parquet"

# Smoothing parameters (Laplace smoothing)
ALPHA = 1
BETA = 5


def main():
    start_time = datetime.now()

    logger.info("=" * 70)
    logger.info("BUILD BEHAVIORAL HISTORY FEATURES")
    logger.info("=" * 70)

    conn = duckdb.connect()
    conn.execute("SET memory_limit='8GB'")

    # Step 1: Check causal pathway features exist
    logger.info("\nChecking causal pathway features...")
    if not CAUSAL_PATHWAY_FEATURES.exists():
        logger.error(f"Causal pathway features not found at {CAUSAL_PATHWAY_FEATURES}")
        logger.error("Run build_causal_pathway_features.py first")
        return

    # Step 2: Get target test_ids (dev + OOT sets only) to reduce memory
    logger.info("\nLoading target test IDs (dev + OOT sets)...")
    conn.execute(f"""
        CREATE TEMP TABLE target_tests AS
        SELECT test_id FROM read_parquet('{DEV_SET}')
        UNION ALL
        SELECT test_id FROM read_parquet('{OOT_SET}')
    """)
    target_count = conn.execute("SELECT COUNT(*) FROM target_tests").fetchone()[0]
    logger.info(f"  Target tests: {target_count:,}")

    # Step 3: Get vehicle_ids for target tests
    logger.info("\nGetting vehicle IDs for target tests...")
    conn.execute(f"""
        CREATE TEMP TABLE target_vehicles AS
        SELECT DISTINCT c.vehicle_id
        FROM read_parquet('{CYCLE_HISTORY}') c
        INNER JOIN target_tests t ON c.test_id = t.test_id
        WHERE c.vehicle_id IS NOT NULL
    """)
    vehicle_count = conn.execute("SELECT COUNT(*) FROM target_vehicles").fetchone()[0]
    logger.info(f"  Target vehicles: {vehicle_count:,}")

    # Step 4: Use DuckDB to compute features only for target vehicles
    logger.info("\nComputing behavioral history features using DuckDB window functions...")
    logger.info("  (processing only vehicles in dev/OOT sets)")

    # Create temp table with causal features
    conn.execute(f"""
        CREATE TEMP TABLE causal AS
        SELECT test_id,
               COALESCE(deferral_failure_count, 0) as deferral_failure_count,
               COALESCE(corrosion_failure_count, 0) as corrosion_failure_count,
               COALESCE(road_damage_failure_count, 0) as road_damage_failure_count,
               COALESCE(wear_failure_count, 0) as wear_failure_count,
               COALESCE(total_classified_failures, 0) as total_classified_failures,
               COALESCE(has_deferral_failure, 0) as has_deferral_failure
        FROM read_parquet('{CAUSAL_PATHWAY_FEATURES}')
    """)
    causal_count = conn.execute("SELECT COUNT(*) FROM causal").fetchone()[0]
    logger.info(f"  Loaded {causal_count:,} tests with causal pathway features")

    # Join with cycle history and compute prior cumulative sums using window functions
    # Only process vehicles that have tests in dev/OOT sets
    logger.info("\nJoining with cycle history and computing prior counts...")
    logger.info("  (filtering to target vehicles only)")

    query = f"""
        WITH filtered_cycle AS (
            SELECT
                c.test_id,
                c.vehicle_id,
                c.test_date
            FROM read_parquet('{CYCLE_HISTORY}') c
            INNER JOIN target_vehicles tv ON c.vehicle_id = tv.vehicle_id
            WHERE c.vehicle_id IS NOT NULL
        ),
        joined AS (
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
            FROM filtered_cycle c
            LEFT JOIN causal p ON c.test_id = p.test_id
        ),
        with_priors AS (
            SELECT
                test_id,
                vehicle_id,
                -- Prior cumulative counts (ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING excludes current row)
                COALESCE(SUM(deferral_failure_count) OVER (
                    PARTITION BY vehicle_id ORDER BY test_date
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ), 0) as prior_deferral_count,
                COALESCE(SUM(corrosion_failure_count) OVER (
                    PARTITION BY vehicle_id ORDER BY test_date
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ), 0) as prior_corrosion_count,
                COALESCE(SUM(road_damage_failure_count) OVER (
                    PARTITION BY vehicle_id ORDER BY test_date
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ), 0) as prior_road_damage_count,
                COALESCE(SUM(wear_failure_count) OVER (
                    PARTITION BY vehicle_id ORDER BY test_date
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ), 0) as prior_wear_count,
                COALESCE(SUM(total_classified_failures) OVER (
                    PARTITION BY vehicle_id ORDER BY test_date
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ), 0) as prior_total_failures,
                COALESCE(SUM(has_deferral_failure) OVER (
                    PARTITION BY vehicle_id ORDER BY test_date
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ), 0) as prior_tests_with_deferral,
                -- Count of prior tests
                ROW_NUMBER() OVER (PARTITION BY vehicle_id ORDER BY test_date) - 1 as n_prior_pathway_tests
            FROM joined
        )
        -- Only return rows for target tests (not all tests for these vehicles)
        SELECT p.*
        FROM with_priors p
        INNER JOIN target_tests t ON p.test_id = t.test_id
    """

    logger.info("  Executing window query...")
    merged = conn.execute(query).fetchdf()
    logger.info(f"  Computed prior counts for {len(merged):,} tests")

    # Step 5: Compute propensity scores (smoothed)
    logger.info("\nComputing propensity scores...")

    # Deferral propensity: prior_deferral / prior_total (smoothed)
    merged['deferral_propensity'] = (
        (merged['prior_deferral_count'] + ALPHA) /
        (merged['prior_total_failures'] + ALPHA + BETA)
    )

    # Corrosion propensity
    merged['corrosion_propensity'] = (
        (merged['prior_corrosion_count'] + ALPHA) /
        (merged['prior_total_failures'] + ALPHA + BETA)
    )

    # Road damage propensity
    merged['road_damage_propensity'] = (
        (merged['prior_road_damage_count'] + ALPHA) /
        (merged['prior_total_failures'] + ALPHA + BETA)
    )

    # Wear propensity
    merged['wear_propensity'] = (
        (merged['prior_wear_count'] + ALPHA) /
        (merged['prior_total_failures'] + ALPHA + BETA)
    )

    # Behavioral risk score: weighted combination (deferral is primary signal)
    # Higher weight on deferral because it indicates owner behavior
    merged['behavioral_risk_score'] = (
        0.6 * merged['deferral_propensity'] +
        0.2 * merged['corrosion_propensity'] +
        0.1 * merged['road_damage_propensity'] +
        0.1 * merged['wear_propensity']
    )

    # Has prior behavioral issues (binary)
    merged['has_prior_deferral_history'] = (merged['prior_deferral_count'] > 0).astype(int)
    merged['has_prior_corrosion_history'] = (merged['prior_corrosion_count'] > 0).astype(int)

    # Step 6: Select output columns
    output_cols = [
        'test_id',
        'vehicle_id',
        # Prior counts
        'prior_deferral_count',
        'prior_corrosion_count',
        'prior_road_damage_count',
        'prior_wear_count',
        'prior_total_failures',
        'prior_tests_with_deferral',
        'n_prior_pathway_tests',
        # Propensities (smoothed rates)
        'deferral_propensity',
        'corrosion_propensity',
        'road_damage_propensity',
        'wear_propensity',
        'behavioral_risk_score',
        # Binary flags
        'has_prior_deferral_history',
        'has_prior_corrosion_history',
    ]

    result = merged[output_cols].copy()

    # Step 7: Save output
    result.to_parquet(OUTPUT_FILE, index=False)
    logger.info(f"\nSaved to: {OUTPUT_FILE}")
    logger.info(f"  Total records: {len(result):,}")

    # Step 8: Diagnostics
    logger.info("\n" + "=" * 70)
    logger.info("DIAGNOSTICS")
    logger.info("=" * 70)

    # Coverage stats
    has_prior_history = (result['n_prior_pathway_tests'] > 0).mean() * 100
    has_prior_deferral = (result['has_prior_deferral_history'] == 1).mean() * 100
    has_prior_corrosion = (result['has_prior_corrosion_history'] == 1).mean() * 100

    logger.info("\nCoverage:")
    logger.info(f"  Tests with prior pathway history: {has_prior_history:.1f}%")
    logger.info(f"  Tests with prior DEFERRAL history: {has_prior_deferral:.1f}%")
    logger.info(f"  Tests with prior CORROSION history: {has_prior_corrosion:.1f}%")

    # Distribution of prior counts
    logger.info("\nPrior count distributions (for tests with history):")
    with_history = result[result['n_prior_pathway_tests'] > 0]
    for col in ['prior_deferral_count', 'prior_corrosion_count', 'prior_total_failures']:
        mean_val = with_history[col].mean()
        median_val = with_history[col].median()
        max_val = with_history[col].max()
        logger.info(f"  {col}: mean={mean_val:.2f}, median={median_val:.0f}, max={max_val:.0f}")

    # Propensity score distributions
    logger.info("\nPropensity score distributions:")
    for col in ['deferral_propensity', 'behavioral_risk_score']:
        mean_val = result[col].mean()
        p25 = result[col].quantile(0.25)
        p75 = result[col].quantile(0.75)
        logger.info(f"  {col}: mean={mean_val:.3f}, p25={p25:.3f}, p75={p75:.3f}")

    # Check dev/OOT coverage
    logger.info("\nDev/OOT Set Coverage:")
    try:
        dev_ids = conn.execute(f"SELECT test_id FROM read_parquet('{DEV_SET}')").fetchdf()['test_id']
        oot_ids = conn.execute(f"SELECT test_id FROM read_parquet('{OOT_SET}')").fetchdf()['test_id']

        dev_coverage = result['test_id'].isin(dev_ids).sum()
        oot_coverage = result['test_id'].isin(oot_ids).sum()

        logger.info(f"  Dev set: {dev_coverage:,} / {len(dev_ids):,} ({dev_coverage/len(dev_ids)*100:.1f}%)")
        logger.info(f"  OOT set: {oot_coverage:,} / {len(oot_ids):,} ({oot_coverage/len(oot_ids)*100:.1f}%)")
    except Exception as e:
        logger.warning(f"  Could not check dev/OOT coverage: {e}")

    conn.close()

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"\nCompleted in {elapsed:.1f}s")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
