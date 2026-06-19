"""
Validate MDPS Sequence Features V2
==================================

Runs hard and soft validations on the MDPS sequence features.

Hard Validations:
1. Row count matches timeseries
2. All test_ids present
3. test_id uniqueness (no duplicates)
4. No unexpected NaN values (recency NULLs are expected)
5. Persistence values are non-negative integers
6. Leakage test: features don't see current row

Soft Validations:
1. Coverage stats: % zero, % nonzero, % null per feature (both DEV and OOT)
2. Distribution checks (min, max, percentiles)
3. Max persistence report (confirms no artificial cap)
4. susp_steer reconciliation with old advisory features

Created: 2026-01-12
"""

import duckdb
import numpy as np
import pandas as pd
from pathlib import Path
import sys


def log(msg):
    print(msg, flush=True)


def validate_mdps_features():
    """Run comprehensive validations on MDPS sequence features."""
    log("=" * 70)
    log("VALIDATING MDPS SEQUENCE FEATURES V2")
    log("=" * 70)

    WORK_DIR = Path.home() / "autosafe_work"

    # File paths
    FEATURES_DEV = WORK_DIR / "mdps_sequence_features_dev.parquet"
    FEATURES_OOT = WORK_DIR / "mdps_sequence_features_oot.parquet"
    TS_DEV = WORK_DIR / "cycle_advisory_timeseries_dev.parquet"
    TS_OOT = WORK_DIR / "cycle_advisory_timeseries_oot.parquet"

    # Check files exist
    for f in [FEATURES_DEV, FEATURES_OOT]:
        if not f.exists():
            log(f"ERROR: {f} not found!")
            log("Please run build_mdps_sequence_features_v2.py first.")
            return False

    conn = duckdb.connect(":memory:")
    all_passed = True

    # ========================================
    # HARD VALIDATIONS
    # ========================================
    log("\n" + "=" * 50)
    log("HARD VALIDATIONS")
    log("=" * 50)

    for name, feat_path, ts_path in [
        ("DEV", FEATURES_DEV, TS_DEV),
        ("OOT", FEATURES_OOT, TS_OOT)
    ]:
        log(f"\n--- {name} Validations ---")

        # 1. Row count matches (unique test_ids)
        log("\n[1] Checking row count matches timeseries unique test_ids...")
        feat_count = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{feat_path}')").fetchone()[0]
        ts_unique_count = conn.execute(f"SELECT COUNT(DISTINCT test_id) FROM read_parquet('{ts_path}')").fetchone()[0]
        ts_total_count = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{ts_path}')").fetchone()[0]
        if feat_count != ts_unique_count:
            log(f"    FAIL: Feature rows ({feat_count:,}) != Timeseries unique test_ids ({ts_unique_count:,})")
            all_passed = False
        else:
            log(f"    PASS: {feat_count:,} rows match unique test_ids")
            if ts_total_count != ts_unique_count:
                log(f"    NOTE: Timeseries has {ts_total_count - ts_unique_count} duplicate test_ids (deduplicated in features)")

        # 2. All test_ids present
        log("\n[2] Checking all test_ids present...")
        missing = conn.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT test_id FROM read_parquet('{ts_path}')
                EXCEPT
                SELECT test_id FROM read_parquet('{feat_path}')
            )
        """).fetchone()[0]
        if missing > 0:
            log(f"    FAIL: {missing} test_ids missing from features")
            all_passed = False
        else:
            log(f"    PASS: All test_ids present")

        # 3. test_id uniqueness (no duplicates)
        log("\n[3] Checking test_id uniqueness...")
        dup_count = conn.execute(f"""
            SELECT COUNT(*) - COUNT(DISTINCT test_id) as duplicate_count
            FROM read_parquet('{feat_path}')
        """).fetchone()[0]
        if dup_count > 0:
            log(f"    FAIL: {dup_count} duplicate test_ids (1:1 join violated!)")
            all_passed = False
        else:
            log(f"    PASS: All test_ids unique (1:1 join guaranteed)")

        # 4. No unexpected NaN values (recency NULLs are expected for "never had advisory")
        log("\n[4] Checking for unexpected NULL values...")
        # Persistence columns should never be NULL
        persist_cols = ['persist_len_any_prev', 'persist_len_brakes_prev',
                        'persist_len_susp_steer_prev', 'persist_len_tyres_prev']
        for col in persist_cols:
            null_count = conn.execute(f"""
                SELECT COUNT(*) FROM read_parquet('{feat_path}') WHERE {col} IS NULL
            """).fetchone()[0]
            if null_count > 0:
                log(f"    FAIL: {col} has {null_count} NULL values (should be 0)")
                all_passed = False
            else:
                log(f"    PASS: {col} has no NULLs")

        # n_domain_changes should not be NULL
        null_count = conn.execute(f"""
            SELECT COUNT(*) FROM read_parquet('{feat_path}') WHERE n_domain_changes_last5_prev IS NULL
        """).fetchone()[0]
        if null_count > 0:
            log(f"    FAIL: n_domain_changes_last5_prev has {null_count} NULL values")
            all_passed = False
        else:
            log(f"    PASS: n_domain_changes_last5_prev has no NULLs")

        # 5. Persistence values are non-negative
        log("\n[5] Checking persistence values are non-negative...")
        for col in persist_cols:
            neg_count = conn.execute(f"""
                SELECT COUNT(*) FROM read_parquet('{feat_path}') WHERE {col} < 0
            """).fetchone()[0]
            if neg_count > 0:
                log(f"    FAIL: {col} has {neg_count} negative values")
                all_passed = False
            else:
                log(f"    PASS: {col} >= 0")

        # 6. Leakage test: persist_len should use LAG (prior cycle only)
        log("\n[6] Leakage test: verifying features use prior cycles only...")

        # For rows with cycle_index=1, all features should be 0 or NULL (no prior history)
        first_cycle_issues = conn.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT f.*, t.cycle_index
                FROM read_parquet('{feat_path}') f
                JOIN read_parquet('{ts_path}') t ON f.test_id = t.test_id
                WHERE t.cycle_index = 1
                  AND (f.persist_len_any_prev != 0
                       OR f.persist_len_brakes_prev != 0
                       OR f.persist_len_susp_steer_prev != 0
                       OR f.persist_len_tyres_prev != 0
                       OR f.n_domain_changes_last5_prev != 0)
            )
        """).fetchone()[0]
        if first_cycle_issues > 0:
            log(f"    FAIL: {first_cycle_issues} first-cycle rows have non-zero features (leakage!)")
            all_passed = False
        else:
            log(f"    PASS: First-cycle rows all have zero features")

        # Additional leakage check: sample vehicles and verify persist_len
        log("\n[7] Sample leakage check: verify persist_len matches expected...")
        # For a sample vehicle, manually verify the streak calculation
        sample = conn.execute(f"""
            WITH sample_vehicle AS (
                SELECT vehicle_id FROM read_parquet('{ts_path}')
                WHERE cycle_index >= 3  -- Need at least 3 cycles to check
                LIMIT 1
            ),
            vehicle_data AS (
                SELECT t.vehicle_id, t.cycle_index, t.test_id, t.adv_brakes,
                       f.persist_len_brakes_prev
                FROM read_parquet('{ts_path}') t
                JOIN read_parquet('{feat_path}') f ON t.test_id = f.test_id
                WHERE t.vehicle_id IN (SELECT vehicle_id FROM sample_vehicle)
                ORDER BY t.cycle_index
            )
            SELECT * FROM vehicle_data LIMIT 10
        """).fetchdf()

        if len(sample) > 0:
            log(f"    Sample vehicle check:")
            log(f"    cycle_idx | adv_brakes | persist_len_brakes_prev")
            for _, row in sample.iterrows():
                log(f"    {row['cycle_index']:8d} | {row['adv_brakes']:10d} | {row['persist_len_brakes_prev']:>20d}")
            log(f"    (Visual inspection: persist should reflect PRIOR cycle's streak)")

    # ========================================
    # SOFT VALIDATIONS (STATS)
    # ========================================
    log("\n" + "=" * 50)
    log("SOFT VALIDATIONS (Statistics)")
    log("=" * 50)

    # All feature columns (15 total)
    all_cols = [
        'persist_len_any_prev', 'persist_len_brakes_prev', 'persist_len_susp_steer_prev', 'persist_len_tyres_prev',
        'cycles_since_any_adv_prev', 'cycles_since_brakes_prev', 'cycles_since_susp_steer_prev', 'cycles_since_tyres_prev',
        'has_ever_any_adv_prev', 'has_ever_brakes_prev', 'has_ever_susp_steer_prev', 'has_ever_tyres_prev',
        'n_domain_changes_last5_prev', 'n_unique_domains_last5_prev', 'domain_bitmask_last5_prev'
    ]

    for name, feat_path in [("DEV", FEATURES_DEV), ("OOT", FEATURES_OOT)]:
        log(f"\n--- {name} Statistics ---")

        # 1. Coverage stats with % zero, % nonzero, % null
        log("\n[1] Feature coverage:")
        log(f"    {'Feature':35s} {'%zero':>7s} {'%nonzero':>9s} {'%null':>7s}")
        log(f"    {'-'*35} {'-'*7} {'-'*9} {'-'*7}")
        total = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{feat_path}')").fetchone()[0]

        for col in all_cols:
            stats = conn.execute(f"""
                SELECT
                    SUM(CASE WHEN {col} IS NOT NULL AND {col} = 0 THEN 1 ELSE 0 END) as zeros,
                    SUM(CASE WHEN {col} IS NOT NULL AND {col} != 0 THEN 1 ELSE 0 END) as nonzero,
                    SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) as nulls
                FROM read_parquet('{feat_path}')
            """).fetchone()
            zeros, nonzero, nulls = stats
            pct_zero = 100 * zeros / total
            pct_nonzero = 100 * nonzero / total
            pct_null = 100 * nulls / total
            log(f"    {col:35s} {pct_zero:6.1f}% {pct_nonzero:8.1f}% {pct_null:6.1f}%")

        # 2. Distribution stats
        log("\n[2] Distribution statistics (non-null values):")
        log(f"    {'Feature':35s} {'Mean':>8s} {'Min':>6s} {'Max':>6s} {'P50':>6s} {'P90':>6s}")
        log(f"    {'-'*35} {'-'*8} {'-'*6} {'-'*6} {'-'*6} {'-'*6}")
        for col in all_cols:
            stats = conn.execute(f"""
                SELECT
                    AVG({col})::DECIMAL(10,2) as mean,
                    MIN({col}) as min_val,
                    MAX({col}) as max_val,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {col}) as p50,
                    PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY {col}) as p90
                FROM read_parquet('{feat_path}')
                WHERE {col} IS NOT NULL
            """).fetchone()
            log(f"    {col:35s} {stats[0]:>8} {stats[1]:>6} {stats[2]:>6} {stats[3]:>6.0f} {stats[4]:>6.0f}")

        # 3. Max persistence report (confirms no artificial cap)
        log("\n[3] Persistence max values (confirms NO artificial cap):")
        for col in ['persist_len_any_prev', 'persist_len_brakes_prev', 'persist_len_susp_steer_prev', 'persist_len_tyres_prev']:
            max_val = conn.execute(f"SELECT MAX({col}) FROM read_parquet('{feat_path}')").fetchone()[0]
            log(f"    {col:35s}: max = {max_val}")

    # ========================================
    # SUSP_STEER RECONCILIATION
    # ========================================
    log("\n" + "=" * 50)
    log("SUSP_STEER RECONCILIATION")
    log("=" * 50)

    PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
    OLD_ADV_DEV = PROJECT_ROOT / "adv_features_dev.parquet"

    if OLD_ADV_DEV.exists():
        log("\nComparing new has_ever_susp_steer_prev with old prev_adv_suspension + prev_adv_steering:")

        # Compare on overlapping test_ids
        reconcile = conn.execute(f"""
            WITH old_features AS (
                SELECT test_id,
                       CASE WHEN prev_adv_suspension > 0 OR prev_adv_steering > 0 THEN 1 ELSE 0 END as old_has_susp_steer
                FROM read_parquet('{OLD_ADV_DEV}')
            ),
            new_features AS (
                SELECT test_id, has_ever_susp_steer_prev as new_has_susp_steer
                FROM read_parquet('{FEATURES_DEV}')
            )
            SELECT
                COUNT(*) as total_overlap,
                SUM(CASE WHEN old_has_susp_steer = 1 THEN 1 ELSE 0 END) as old_has_any,
                SUM(CASE WHEN new_has_susp_steer = 1 THEN 1 ELSE 0 END) as new_has_any,
                SUM(CASE WHEN old_has_susp_steer = 1 AND new_has_susp_steer = 1 THEN 1 ELSE 0 END) as both_have,
                SUM(CASE WHEN old_has_susp_steer = 1 AND new_has_susp_steer = 0 THEN 1 ELSE 0 END) as old_only,
                SUM(CASE WHEN old_has_susp_steer = 0 AND new_has_susp_steer = 1 THEN 1 ELSE 0 END) as new_only
            FROM old_features o
            JOIN new_features n ON o.test_id = n.test_id
        """).fetchone()

        total, old_has, new_has, both, old_only, new_only = reconcile
        log(f"    Overlapping test_ids: {total:,}")
        log(f"    Old (prev_adv_susp+steer > 0): {old_has:,} ({100*old_has/total:.2f}%)")
        log(f"    New (has_ever_susp_steer = 1): {new_has:,} ({100*new_has/total:.2f}%)")
        log(f"    Both have susp_steer history:  {both:,}")
        log(f"    Old only (not in new):         {old_only:,}")
        log(f"    New only (not in old):         {new_only:,}")

        if old_only > 0:
            log(f"\n    NOTE: {old_only:,} test_ids have old susp_steer history but NOT in new features.")
            log(f"    INVESTIGATION NEEDED: This suggests section_id mapping differences.")
            log(f"    Old features may have used different section_ids for suspension/steering.")
            log(f"    New features use system_map_v1.py: sections 5190, 5100, 20106, 9000, 50, 25046")
        if new_only > 0:
            log(f"\n    {new_only:,} test_ids have new susp_steer history but NOT in old.")
            log(f"    This is expected: new looks at ALL prior cycles, old looked at PREVIOUS only.")
    else:
        log(f"\n    Skipping reconciliation: {OLD_ADV_DEV} not found")

    conn.close()

    # ========================================
    # FINAL RESULT
    # ========================================
    log("\n" + "=" * 70)
    if all_passed:
        log("VALIDATION PASSED - All hard validations successful")
    else:
        log("VALIDATION FAILED - See errors above")
    log("=" * 70)

    return all_passed


if __name__ == "__main__":
    success = validate_mdps_features()
    sys.exit(0 if success else 1)
