"""
Validate Cycle-Level Advisory Time Series (MDPS Step 1.3)
=========================================================

Runs hard and soft validations on the cycle advisory time series outputs.

Hard Validations (fail if violated):
1. No duplicate (vehicle_id, cycle_index) pairs
2. cycle_index is consecutive (1, 2, 3, ...) per vehicle
3. test_date is monotonically increasing within vehicle
4. All DEV test_ids present in output
5. All OOT test_ids present in output
6. DEV and OOT logic is identical (same code path)

Soft Validations (report stats):
1. Advisory coverage: % of cycles with at least one advisory
2. Domain distribution: mean/median per domain
3. Cycle count distribution: histogram of n_cycles per vehicle
4. Zero-advisory rate per domain

Created: 2026-01-12
"""

import duckdb
from pathlib import Path
import sys


def log(msg):
    print(msg, flush=True)


def validate_cycle_timeseries():
    """Run comprehensive validations on cycle time series outputs."""
    log("=" * 70)
    log("VALIDATING CYCLE-LEVEL ADVISORY TIME SERIES")
    log("=" * 70)

    PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
    WORK_DIR = Path.home() / "autosafe_work"

    # File paths
    DEV_SET = PROJECT_ROOT / "stratified_samples" / "dev_set.parquet"
    OOT_SET = PROJECT_ROOT / "stratified_samples" / "oot_test_set.parquet"
    TS_DEV = WORK_DIR / "cycle_advisory_timeseries_dev.parquet"
    TS_OOT = WORK_DIR / "cycle_advisory_timeseries_oot.parquet"
    ADV_FEATURES = PROJECT_ROOT / "adv_features_dev.parquet"

    # Check files exist
    for f in [TS_DEV, TS_OOT]:
        if not f.exists():
            log(f"ERROR: {f} not found!")
            log("Please run build_cycle_advisory_timeseries.py first.")
            return False

    conn = duckdb.connect(":memory:")
    conn.execute("SET memory_limit='4GB'")

    all_passed = True

    # ========================================
    # HARD VALIDATIONS
    # ========================================
    log("\n" + "=" * 50)
    log("HARD VALIDATIONS")
    log("=" * 50)

    for name, ts_path in [("DEV", TS_DEV), ("OOT", TS_OOT)]:
        log(f"\n--- {name} Validations ---")

        # 1. No duplicate (vehicle_id, cycle_index) pairs
        log("\n[1] Checking for duplicate (vehicle_id, cycle_index) pairs...")
        dup_count = conn.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT vehicle_id, cycle_index, COUNT(*) as cnt
                FROM read_parquet('{ts_path}')
                GROUP BY vehicle_id, cycle_index
                HAVING COUNT(*) > 1
            )
        """).fetchone()[0]
        if dup_count > 0:
            log(f"    FAIL: {dup_count} duplicate (vehicle_id, cycle_index) pairs found")
            all_passed = False
        else:
            log(f"    PASS: No duplicates found")

        # 2. cycle_index is consecutive (1, 2, 3, ...) per vehicle
        log("\n[2] Checking cycle_index is consecutive per vehicle...")
        gap_count = conn.execute(f"""
            WITH ordered AS (
                SELECT
                    vehicle_id,
                    cycle_index,
                    LAG(cycle_index) OVER (PARTITION BY vehicle_id ORDER BY cycle_index) as prev_idx
                FROM read_parquet('{ts_path}')
            )
            SELECT COUNT(*) FROM ordered
            WHERE prev_idx IS NOT NULL AND cycle_index != prev_idx + 1
        """).fetchone()[0]
        if gap_count > 0:
            log(f"    FAIL: {gap_count} non-consecutive cycle_index gaps found")
            all_passed = False
        else:
            log(f"    PASS: All cycle_index values are consecutive")

        # Check cycle_index starts at 1
        min_idx = conn.execute(f"""
            SELECT MIN(min_idx) FROM (
                SELECT vehicle_id, MIN(cycle_index) as min_idx
                FROM read_parquet('{ts_path}')
                GROUP BY vehicle_id
            )
        """).fetchone()[0]
        if min_idx != 1:
            log(f"    WARN: Minimum cycle_index is {min_idx}, expected 1")

        # 3. test_date is monotonically increasing within vehicle
        log("\n[3] Checking test_date is monotonically increasing per vehicle...")
        date_violations = conn.execute(f"""
            WITH ordered AS (
                SELECT
                    vehicle_id,
                    cycle_index,
                    test_date,
                    LAG(test_date) OVER (PARTITION BY vehicle_id ORDER BY cycle_index) as prev_date
                FROM read_parquet('{ts_path}')
            )
            SELECT COUNT(*) FROM ordered
            WHERE prev_date IS NOT NULL AND test_date < prev_date
        """).fetchone()[0]
        if date_violations > 0:
            log(f"    FAIL: {date_violations} test_date ordering violations found")
            all_passed = False
        else:
            log(f"    PASS: All test_dates are monotonically increasing per vehicle")

    # 4 & 5. All DEV/OOT test_ids present in output
    log("\n[4-5] Checking all target test_ids are present in outputs...")

    for name, target_path, ts_path in [
        ("DEV", DEV_SET, TS_DEV),
        ("OOT", OOT_SET, TS_OOT)
    ]:
        target_ids = conn.execute(f"SELECT COUNT(DISTINCT test_id) FROM read_parquet('{target_path}')").fetchone()[0]
        output_ids = conn.execute(f"SELECT COUNT(DISTINCT test_id) FROM read_parquet('{ts_path}')").fetchone()[0]

        # Check if target test_ids are in output
        missing = conn.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT test_id FROM read_parquet('{target_path}')
                EXCEPT
                SELECT test_id FROM read_parquet('{ts_path}')
            )
        """).fetchone()[0]

        if missing > 0:
            log(f"    FAIL: {missing} {name} test_ids not found in output")
            all_passed = False
        else:
            log(f"    PASS: All {target_ids:,} {name} test_ids found in output ({output_ids:,} total cycles)")

    # 6. DEV and OOT logic is identical - check schema match
    log("\n[6] Checking DEV and OOT schema match...")
    dev_schema = conn.execute(f"DESCRIBE SELECT * FROM read_parquet('{TS_DEV}')").fetchall()
    oot_schema = conn.execute(f"DESCRIBE SELECT * FROM read_parquet('{TS_OOT}')").fetchall()

    dev_cols = [(r[0], r[1]) for r in dev_schema]
    oot_cols = [(r[0], r[1]) for r in oot_schema]

    if dev_cols != oot_cols:
        log(f"    FAIL: Schema mismatch between DEV and OOT")
        log(f"    DEV: {dev_cols}")
        log(f"    OOT: {oot_cols}")
        all_passed = False
    else:
        log(f"    PASS: DEV and OOT have identical schema")
        log(f"    Columns: {[c[0] for c in dev_cols]}")

    # ========================================
    # SOFT VALIDATIONS (STATS)
    # ========================================
    log("\n" + "=" * 50)
    log("SOFT VALIDATIONS (Statistics)")
    log("=" * 50)

    for name, ts_path in [("DEV", TS_DEV), ("OOT", TS_OOT)]:
        log(f"\n--- {name} Statistics ---")

        # 1. Advisory coverage
        log("\n[1] Advisory coverage (% of cycles with at least one advisory)...")
        coverage = conn.execute(f"""
            SELECT
                SUM(CASE WHEN adv_brakes > 0 OR adv_suspension_steering > 0 OR adv_tyres > 0 THEN 1 ELSE 0 END) as has_any,
                COUNT(*) as total
            FROM read_parquet('{ts_path}')
        """).fetchone()
        log(f"    Has any advisory: {coverage[0]:,} / {coverage[1]:,} ({100*coverage[0]/coverage[1]:.2f}%)")

        # 2. Domain distribution
        log("\n[2] Domain distribution (mean/median per domain)...")
        domain_stats = conn.execute(f"""
            SELECT
                AVG(adv_brakes)::DECIMAL(10,4) as mean_brakes,
                MEDIAN(adv_brakes)::DECIMAL(10,4) as median_brakes,
                AVG(adv_suspension_steering)::DECIMAL(10,4) as mean_susp,
                MEDIAN(adv_suspension_steering)::DECIMAL(10,4) as median_susp,
                AVG(adv_tyres)::DECIMAL(10,4) as mean_tyres,
                MEDIAN(adv_tyres)::DECIMAL(10,4) as median_tyres
            FROM read_parquet('{ts_path}')
        """).fetchone()
        log(f"    brakes:             mean={domain_stats[0]}, median={domain_stats[1]}")
        log(f"    suspension_steering: mean={domain_stats[2]}, median={domain_stats[3]}")
        log(f"    tyres:              mean={domain_stats[4]}, median={domain_stats[5]}")

        # 3. Cycle count distribution
        log("\n[3] Cycle count distribution (n_cycles per vehicle)...")
        cycle_hist = conn.execute(f"""
            WITH vehicle_cycles AS (
                SELECT vehicle_id, MAX(cycle_index) as n_cycles
                FROM read_parquet('{ts_path}')
                GROUP BY vehicle_id
            )
            SELECT
                CASE
                    WHEN n_cycles = 1 THEN '1'
                    WHEN n_cycles = 2 THEN '2'
                    WHEN n_cycles = 3 THEN '3'
                    WHEN n_cycles BETWEEN 4 AND 5 THEN '4-5'
                    WHEN n_cycles BETWEEN 6 AND 10 THEN '6-10'
                    ELSE '11+'
                END as bucket,
                COUNT(*) as count
            FROM vehicle_cycles
            GROUP BY bucket
            ORDER BY MIN(n_cycles)
        """).fetchall()
        for bucket, count in cycle_hist:
            log(f"    {bucket:6s} cycles: {count:,} vehicles")

        # 4. Zero-advisory rate per domain
        log("\n[4] Zero-advisory rate per domain...")
        zero_rates = conn.execute(f"""
            SELECT
                SUM(CASE WHEN adv_brakes = 0 THEN 1 ELSE 0 END)::FLOAT / COUNT(*) as zero_brakes,
                SUM(CASE WHEN adv_suspension_steering = 0 THEN 1 ELSE 0 END)::FLOAT / COUNT(*) as zero_susp,
                SUM(CASE WHEN adv_tyres = 0 THEN 1 ELSE 0 END)::FLOAT / COUNT(*) as zero_tyres,
                COUNT(*) as total
            FROM read_parquet('{ts_path}')
        """).fetchone()
        log(f"    brakes:             {100*zero_rates[0]:.1f}% zero")
        log(f"    suspension_steering: {100*zero_rates[1]:.1f}% zero")
        log(f"    tyres:              {100*zero_rates[2]:.1f}% zero")

    # ========================================
    # CROSS-VALIDATION WITH EXISTING FEATURES
    # ========================================
    log("\n" + "=" * 50)
    log("CROSS-VALIDATION WITH EXISTING FEATURES")
    log("=" * 50)

    if ADV_FEATURES.exists():
        log("\n[X1] Cross-validating with adv_features_dev.parquet...")

        # Join timeseries with existing features on test_id
        # Check if prev_adv_* values match what we computed for the prior cycle
        sample_check = conn.execute(f"""
            WITH ts AS (
                SELECT
                    vehicle_id,
                    cycle_index,
                    test_id,
                    adv_brakes,
                    adv_suspension_steering,
                    adv_tyres,
                    LAG(adv_brakes) OVER (PARTITION BY vehicle_id ORDER BY cycle_index) as ts_prev_brakes,
                    LAG(adv_suspension_steering) OVER (PARTITION BY vehicle_id ORDER BY cycle_index) as ts_prev_susp
                FROM read_parquet('{TS_DEV}')
            )
            SELECT
                COUNT(*) as total_with_prev,
                SUM(CASE WHEN ts.ts_prev_brakes = af.prev_adv_brakes THEN 1 ELSE 0 END) as brakes_match,
                SUM(CASE WHEN ts.ts_prev_susp = (af.prev_adv_suspension + af.prev_adv_steering) THEN 1 ELSE 0 END) as susp_match
            FROM ts
            JOIN read_parquet('{ADV_FEATURES}') af ON ts.test_id = af.test_id
            WHERE ts.ts_prev_brakes IS NOT NULL
        """).fetchone()

        if sample_check[0] > 0:
            log(f"    Compared {sample_check[0]:,} cycles with prior cycle data")
            log(f"    Brakes match: {sample_check[1]:,} ({100*sample_check[1]/sample_check[0]:.1f}%)")
            log(f"    Susp+Steer match: {sample_check[2]:,} ({100*sample_check[2]/sample_check[0]:.1f}%)")
        else:
            log(f"    No overlapping cycles found for comparison")
    else:
        log(f"    Skipping: adv_features_dev.parquet not found")

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
    success = validate_cycle_timeseries()
    sys.exit(0 if success else 1)
