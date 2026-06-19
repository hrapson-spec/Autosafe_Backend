"""
Build Advisory Flag Features V4
===============================

Splits the conflated "flag" features into distinct advisory (degradation)
and failure (repair) signals.

Rationale:
- Advisory = degradation in progress, part aging
- Failure = component failed, likely repaired afterward
- Combined "flag" conflates opposite risk profiles

Per component (tyres, brakes, suspension):

Advisory Features (Degradation Signal):
- has_prior_advisory_{comp}        : Any advisory in strict prior history
- miles_since_last_advisory_{comp} : Miles since advisory, RESETS after failure
- tests_since_last_advisory_{comp} : Tests since advisory
- advisory_in_last_1_{comp}        : Advisory in t-1
- advisory_in_last_2_{comp}        : Advisory in t-2
- advisory_streak_len_{comp}       : Consecutive prior tests with advisories

Failure Features (Repair Signal):
- has_prior_failure_{comp}         : Most recent prior event was a failure
- has_ever_failed_{comp}           : Any failure in lifetime
- failure_streak_len_{comp}        : Consecutive prior tests with failures

History Controls:
- history_tests_observed
- history_years_observed

Created: 2026-01-14
"""

import duckdb
import shutil
from pathlib import Path
from datetime import datetime

# ============================================================================
# Configuration
# ============================================================================

PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
WORK_DIR = Path.home() / "autosafe_work"

DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set.parquet"
OOT_SET = PROJECT_ROOT / "stratified_samples/oot_test_set.parquet"
CYCLE_FIRST_TESTS = PROJECT_ROOT / "cycle_first_tests.parquet"
MILEAGE_LOOKUP = WORK_DIR / "mileage_lookup_full.parquet"
ADVISORY_TOTALS = WORK_DIR / "advisory_totals_3domain.parquet"
COMPONENT_FAILURES = PROJECT_ROOT / "component_failures_all_years.parquet"

OUTPUT_FILE = WORK_DIR / "advisory_flag_features_v4.parquet"

# Chunked processing configuration (OOM-safe)
NUM_BUCKETS = 20
OUTPUT_DIR = WORK_DIR / "advisory_v4_chunks"


def main():
    print("=" * 70)
    print("BUILD ADVISORY FLAG FEATURES V4 (Split Advisory/Failure)")
    print("=" * 70)
    print(f"Started: {datetime.now().isoformat()}")

    # Use disk-backed database for OOM safety
    db_path = WORK_DIR / "temp_advisory_v4.duckdb"
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    con.execute("SET memory_limit='4GB'")
    con.execute("SET threads=2")
    con.execute("SET preserve_insertion_order=false")

    # Create output directory for chunks
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Process in buckets
    for bucket in range(NUM_BUCKETS):
        print(f"\n{'='*70}")
        print(f"[Bucket {bucket+1}/{NUM_BUCKETS}]")
        print(f"{'='*70}")

        # =====================================================================
        # Step 1: Create target test_ids and vehicles FOR THIS BUCKET
        # =====================================================================
        print("\n  [Step 1] Creating target test_ids...")

        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE target_tests AS
            SELECT DISTINCT test_id, vehicle_id
            FROM (
                SELECT test_id, vehicle_id FROM read_parquet('{DEV_SET}')
                WHERE vehicle_id % {NUM_BUCKETS} = {bucket}
                UNION ALL
                SELECT test_id, vehicle_id FROM read_parquet('{OOT_SET}')
                WHERE vehicle_id % {NUM_BUCKETS} = {bucket}
            )
        """)

        n_tests = con.execute("SELECT COUNT(*) FROM target_tests").fetchone()[0]
        if n_tests == 0:
            print(f"    No tests in bucket {bucket}, skipping")
            continue
        n_vehicles = con.execute("SELECT COUNT(DISTINCT vehicle_id) FROM target_tests").fetchone()[0]
        print(f"    Target tests: {n_tests:,}")
        print(f"    Target vehicles: {n_vehicles:,}")

        # =====================================================================
        # Step 2: Build history with SEPARATE advisory and failure indicators
        # =====================================================================
        print("\n  [Step 2] Building history with separate advisory/failure indicators...")

        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE filtered_history AS
            SELECT
                c.vehicle_id,
                c.test_id,
                c.test_date,
                m.test_mileage,
                -- Separate advisory indicators
                CASE WHEN COALESCE(a.adv_brakes, 0) > 0 THEN 1 ELSE 0 END AS adv_brakes,
                CASE WHEN COALESCE(a.adv_tyres, 0) > 0 THEN 1 ELSE 0 END AS adv_tyres,
                CASE WHEN COALESCE(a.adv_suspension_steering, 0) > 0 THEN 1 ELSE 0 END AS adv_suspension,
                -- Separate failure indicators
                COALESCE(cf.fail_brakes, 0) AS fail_brakes,
                COALESCE(cf.fail_tyres, 0) AS fail_tyres,
                COALESCE(cf.fail_suspension, 0) AS fail_suspension
            FROM read_parquet('{CYCLE_FIRST_TESTS}') c
            SEMI JOIN target_tests tt ON c.vehicle_id = tt.vehicle_id
            LEFT JOIN read_parquet('{MILEAGE_LOOKUP}') m ON c.test_id = m.test_id
            LEFT JOIN read_parquet('{ADVISORY_TOTALS}') a ON c.test_id = a.test_id
            LEFT JOIN read_parquet('{COMPONENT_FAILURES}') cf ON c.test_id = cf.test_id
        """)

        hist_count = con.execute("SELECT COUNT(*) FROM filtered_history").fetchone()[0]
        print(f"    History rows: {hist_count:,}")

        # =====================================================================
        # Step 3: Add test numbers
        # =====================================================================
        print("\n  [Step 3] Adding test numbers...")

        con.execute("""
            CREATE OR REPLACE TEMP TABLE with_testnum AS
            SELECT
                *,
                ROW_NUMBER() OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id) as test_num
            FROM filtered_history
        """)
        print("    Added test_num")

        # =====================================================================
        # Step 4: Compute window features - SEPARATE for advisory and failure
        # =====================================================================
        print("\n  [Step 4] Computing window features...")

        con.execute("""
            CREATE OR REPLACE TEMP TABLE with_lags AS
            SELECT
                t.*,
                -- Previous test date for history span
                FIRST_VALUE(test_date) OVER w AS first_test_date,

                -- ============ ADVISORY LAGS ============
                LAG(adv_brakes, 1) OVER w AS adv_brakes_t1,
                LAG(adv_brakes, 2) OVER w AS adv_brakes_t2,
                LAG(adv_brakes, 3) OVER w AS adv_brakes_t3,
                LAG(adv_tyres, 1) OVER w AS adv_tyres_t1,
                LAG(adv_tyres, 2) OVER w AS adv_tyres_t2,
                LAG(adv_tyres, 3) OVER w AS adv_tyres_t3,
                LAG(adv_suspension, 1) OVER w AS adv_suspension_t1,
                LAG(adv_suspension, 2) OVER w AS adv_suspension_t2,
                LAG(adv_suspension, 3) OVER w AS adv_suspension_t3,

                -- ============ FAILURE LAGS ============
                LAG(fail_brakes, 1) OVER w AS fail_brakes_t1,
                LAG(fail_brakes, 2) OVER w AS fail_brakes_t2,
                LAG(fail_brakes, 3) OVER w AS fail_brakes_t3,
                LAG(fail_tyres, 1) OVER w AS fail_tyres_t1,
                LAG(fail_tyres, 2) OVER w AS fail_tyres_t2,
                LAG(fail_tyres, 3) OVER w AS fail_tyres_t3,
                LAG(fail_suspension, 1) OVER w AS fail_suspension_t1,
                LAG(fail_suspension, 2) OVER w AS fail_suspension_t2,
                LAG(fail_suspension, 3) OVER w AS fail_suspension_t3,

                -- ============ FAILURE TRACKING (for reset logic) ============
                -- Last failure mileage (strict prior)
                MAX(CASE WHEN fail_brakes = 1 THEN test_mileage END)
                    OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS last_fail_mileage_brakes,
                MAX(CASE WHEN fail_tyres = 1 THEN test_mileage END)
                    OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS last_fail_mileage_tyres,
                MAX(CASE WHEN fail_suspension = 1 THEN test_mileage END)
                    OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS last_fail_mileage_suspension,

                -- Last failure test_num (for most-recent detection)
                MAX(CASE WHEN fail_brakes = 1 THEN test_num END)
                    OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS last_fail_testnum_brakes,
                MAX(CASE WHEN fail_tyres = 1 THEN test_num END)
                    OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS last_fail_testnum_tyres,
                MAX(CASE WHEN fail_suspension = 1 THEN test_num END)
                    OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS last_fail_testnum_suspension,

                -- ============ ADVISORY TRACKING ============
                -- Last advisory mileage (strict prior) - raw, before reset logic
                MAX(CASE WHEN adv_brakes = 1 THEN test_mileage END)
                    OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS last_adv_mileage_raw_brakes,
                MAX(CASE WHEN adv_tyres = 1 THEN test_mileage END)
                    OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS last_adv_mileage_raw_tyres,
                MAX(CASE WHEN adv_suspension = 1 THEN test_mileage END)
                    OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS last_adv_mileage_raw_suspension,

                -- Last advisory test_num (strict prior)
                MAX(CASE WHEN adv_brakes = 1 THEN test_num END)
                    OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS last_adv_testnum_brakes,
                MAX(CASE WHEN adv_tyres = 1 THEN test_num END)
                    OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS last_adv_testnum_tyres,
                MAX(CASE WHEN adv_suspension = 1 THEN test_num END)
                    OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS last_adv_testnum_suspension

            FROM with_testnum t
            WINDOW w AS (PARTITION BY vehicle_id ORDER BY test_date, test_id)
        """)

        print("    Created lag features")

        # =====================================================================
        # Step 5: Compute streak features (separate for advisory and failure)
        # =====================================================================
        print("\n  [Step 5] Computing streak features...")

        con.execute("""
            CREATE OR REPLACE TEMP TABLE with_streaks AS
            SELECT
                w.*,
                -- Advisory streaks (consecutive advisories in last 3 tests)
                COALESCE(w.adv_brakes_t1, 0) +
                COALESCE(w.adv_brakes_t2, 0) +
                COALESCE(w.adv_brakes_t3, 0) AS advisory_streak_brakes,
                COALESCE(w.adv_tyres_t1, 0) +
                COALESCE(w.adv_tyres_t2, 0) +
                COALESCE(w.adv_tyres_t3, 0) AS advisory_streak_tyres,
                COALESCE(w.adv_suspension_t1, 0) +
                COALESCE(w.adv_suspension_t2, 0) +
                COALESCE(w.adv_suspension_t3, 0) AS advisory_streak_suspension,

                -- Failure streaks (consecutive failures in last 3 tests)
                COALESCE(w.fail_brakes_t1, 0) +
                COALESCE(w.fail_brakes_t2, 0) +
                COALESCE(w.fail_brakes_t3, 0) AS failure_streak_brakes,
                COALESCE(w.fail_tyres_t1, 0) +
                COALESCE(w.fail_tyres_t2, 0) +
                COALESCE(w.fail_tyres_t3, 0) AS failure_streak_tyres,
                COALESCE(w.fail_suspension_t1, 0) +
                COALESCE(w.fail_suspension_t2, 0) +
                COALESCE(w.fail_suspension_t3, 0) AS failure_streak_suspension
            FROM with_lags w
        """)

        print("    Created streak features")

        # =====================================================================
        # Step 6: Build final feature set with RESET LOGIC for advisory mileage
        # =====================================================================
        print("\n  [Step 6] Building final features with reset logic...")

        chunk_file = OUTPUT_DIR / f"chunk_{bucket:02d}.parquet"
        con.execute(f"""
            COPY (
                SELECT
                    s.test_id,

                    -- History controls
                    s.test_num - 1 AS history_tests_observed,
                    EXTRACT(YEAR FROM s.test_date) - EXTRACT(YEAR FROM s.first_test_date)
                        AS history_years_observed,

                    -- ============ BRAKES ADVISORY FEATURES ============
                    CASE WHEN s.last_adv_testnum_brakes IS NOT NULL THEN 1 ELSE 0 END
                        AS has_prior_advisory_brakes,
                    -- Miles since advisory - RESET after failure
                    CASE
                        WHEN s.last_adv_mileage_raw_brakes IS NULL THEN NULL
                        WHEN s.last_fail_mileage_brakes IS NOT NULL
                             AND s.last_fail_mileage_brakes > s.last_adv_mileage_raw_brakes THEN NULL
                        ELSE s.test_mileage - s.last_adv_mileage_raw_brakes
                    END AS miles_since_last_advisory_brakes,
                    CASE WHEN s.last_adv_testnum_brakes IS NOT NULL
                         THEN s.test_num - s.last_adv_testnum_brakes END
                        AS tests_since_last_advisory_brakes,
                    s.adv_brakes_t1 AS advisory_in_last_1_brakes,
                    s.adv_brakes_t2 AS advisory_in_last_2_brakes,
                    s.advisory_streak_brakes AS advisory_streak_len_brakes,

                    -- ============ BRAKES FAILURE FEATURES ============
                    -- has_prior_failure: most recent event was a failure
                    CASE WHEN s.last_fail_testnum_brakes IS NOT NULL
                              AND (s.last_adv_testnum_brakes IS NULL
                                   OR s.last_fail_testnum_brakes > s.last_adv_testnum_brakes)
                         THEN 1 ELSE 0 END AS has_prior_failure_brakes,
                    CASE WHEN s.last_fail_testnum_brakes IS NOT NULL THEN 1 ELSE 0 END
                        AS has_ever_failed_brakes,
                    s.failure_streak_brakes AS failure_streak_len_brakes,
                    -- Tests since last failure (repair recency)
                    CASE WHEN s.last_fail_testnum_brakes IS NOT NULL
                         THEN s.test_num - s.last_fail_testnum_brakes END
                        AS tests_since_last_failure_brakes,

                    -- ============ TYRES ADVISORY FEATURES ============
                    CASE WHEN s.last_adv_testnum_tyres IS NOT NULL THEN 1 ELSE 0 END
                        AS has_prior_advisory_tyres,
                    CASE
                        WHEN s.last_adv_mileage_raw_tyres IS NULL THEN NULL
                        WHEN s.last_fail_mileage_tyres IS NOT NULL
                             AND s.last_fail_mileage_tyres > s.last_adv_mileage_raw_tyres THEN NULL
                        ELSE s.test_mileage - s.last_adv_mileage_raw_tyres
                    END AS miles_since_last_advisory_tyres,
                    CASE WHEN s.last_adv_testnum_tyres IS NOT NULL
                         THEN s.test_num - s.last_adv_testnum_tyres END
                        AS tests_since_last_advisory_tyres,
                    s.adv_tyres_t1 AS advisory_in_last_1_tyres,
                    s.adv_tyres_t2 AS advisory_in_last_2_tyres,
                    s.advisory_streak_tyres AS advisory_streak_len_tyres,

                    -- ============ TYRES FAILURE FEATURES ============
                    CASE WHEN s.last_fail_testnum_tyres IS NOT NULL
                              AND (s.last_adv_testnum_tyres IS NULL
                                   OR s.last_fail_testnum_tyres > s.last_adv_testnum_tyres)
                         THEN 1 ELSE 0 END AS has_prior_failure_tyres,
                    CASE WHEN s.last_fail_testnum_tyres IS NOT NULL THEN 1 ELSE 0 END
                        AS has_ever_failed_tyres,
                    s.failure_streak_tyres AS failure_streak_len_tyres,
                    -- Tests since last failure (repair recency)
                    CASE WHEN s.last_fail_testnum_tyres IS NOT NULL
                         THEN s.test_num - s.last_fail_testnum_tyres END
                        AS tests_since_last_failure_tyres,

                    -- ============ SUSPENSION ADVISORY FEATURES ============
                    CASE WHEN s.last_adv_testnum_suspension IS NOT NULL THEN 1 ELSE 0 END
                        AS has_prior_advisory_suspension,
                    CASE
                        WHEN s.last_adv_mileage_raw_suspension IS NULL THEN NULL
                        WHEN s.last_fail_mileage_suspension IS NOT NULL
                             AND s.last_fail_mileage_suspension > s.last_adv_mileage_raw_suspension THEN NULL
                        ELSE s.test_mileage - s.last_adv_mileage_raw_suspension
                    END AS miles_since_last_advisory_suspension,
                    CASE WHEN s.last_adv_testnum_suspension IS NOT NULL
                         THEN s.test_num - s.last_adv_testnum_suspension END
                        AS tests_since_last_advisory_suspension,
                    s.adv_suspension_t1 AS advisory_in_last_1_suspension,
                    s.adv_suspension_t2 AS advisory_in_last_2_suspension,
                    s.advisory_streak_suspension AS advisory_streak_len_suspension,

                    -- ============ SUSPENSION FAILURE FEATURES ============
                    CASE WHEN s.last_fail_testnum_suspension IS NOT NULL
                              AND (s.last_adv_testnum_suspension IS NULL
                                   OR s.last_fail_testnum_suspension > s.last_adv_testnum_suspension)
                         THEN 1 ELSE 0 END AS has_prior_failure_suspension,
                    CASE WHEN s.last_fail_testnum_suspension IS NOT NULL THEN 1 ELSE 0 END
                        AS has_ever_failed_suspension,
                    s.failure_streak_suspension AS failure_streak_len_suspension,
                    -- Tests since last failure (repair recency)
                    CASE WHEN s.last_fail_testnum_suspension IS NOT NULL
                         THEN s.test_num - s.last_fail_testnum_suspension END
                        AS tests_since_last_failure_suspension

                FROM with_streaks s
                SEMI JOIN target_tests tt ON s.test_id = tt.test_id
            ) TO '{chunk_file}' (FORMAT PARQUET, COMPRESSION SNAPPY)
        """)

        chunk_rows = con.execute(f"SELECT COUNT(*) FROM read_parquet('{chunk_file}')").fetchone()[0]
        print(f"    Written {chunk_rows:,} rows to {chunk_file.name}")

        # Clean up intermediate tables to free memory
        for table in ['filtered_history', 'with_testnum', 'with_lags', 'with_streaks']:
            con.execute(f"DROP TABLE IF EXISTS {table}")

    # =========================================================================
    # Final: Concatenate all chunks
    # =========================================================================
    print(f"\n{'='*70}")
    print("[Final] Concatenating chunks...")
    print(f"{'='*70}")

    chunk_pattern = str(OUTPUT_DIR / "*.parquet")
    con.execute(f"""
        COPY (
            SELECT * FROM read_parquet('{chunk_pattern}')
        ) TO '{OUTPUT_FILE}' (FORMAT PARQUET, COMPRESSION SNAPPY)
    """)

    # =========================================================================
    # Validation
    # =========================================================================
    print("\n[Validation]")

    stats = con.execute(f"""
        SELECT
            COUNT(*) as n_rows,
            -- Advisory coverage
            AVG(has_prior_advisory_brakes) as pct_adv_brakes,
            AVG(has_prior_advisory_tyres) as pct_adv_tyres,
            AVG(has_prior_advisory_suspension) as pct_adv_suspension,
            -- Failure coverage
            AVG(has_prior_failure_brakes) as pct_fail_brakes,
            AVG(has_prior_failure_tyres) as pct_fail_tyres,
            AVG(has_prior_failure_suspension) as pct_fail_suspension,
            -- Ever failed
            AVG(has_ever_failed_brakes) as pct_ever_fail_brakes,
            AVG(has_ever_failed_tyres) as pct_ever_fail_tyres,
            AVG(has_ever_failed_suspension) as pct_ever_fail_suspension,
            -- Mileage features
            AVG(miles_since_last_advisory_brakes) as avg_miles_adv_brakes,
            AVG(miles_since_last_advisory_tyres) as avg_miles_adv_tyres,
            COUNT(miles_since_last_advisory_brakes) as n_with_adv_miles_brakes,
            -- History
            AVG(history_tests_observed) as avg_hist_tests
        FROM read_parquet('{OUTPUT_FILE}')
    """).fetchone()

    print(f"  Rows: {stats[0]:,}")
    print(f"\n  Advisory coverage:")
    print(f"    brakes={stats[1]*100:.1f}%, tyres={stats[2]*100:.1f}%, suspension={stats[3]*100:.1f}%")
    print(f"\n  Failure coverage (most recent = failure):")
    print(f"    brakes={stats[4]*100:.1f}%, tyres={stats[5]*100:.1f}%, suspension={stats[6]*100:.1f}%")
    print(f"\n  Ever failed:")
    print(f"    brakes={stats[7]*100:.1f}%, tyres={stats[8]*100:.1f}%, suspension={stats[9]*100:.1f}%")
    if stats[10] is not None:
        print(f"\n  Avg miles_since_last_advisory (after reset): brakes={stats[10]:,.0f}, tyres={stats[11]:,.0f}")
        print(f"  Rows with advisory mileage (brakes): {stats[12]:,}")
    print(f"\n  Avg history_tests_observed: {stats[13]:.1f}")

    file_size_mb = OUTPUT_FILE.stat().st_size / (1024 * 1024)
    print(f"  File size: {file_size_mb:.1f} MB")

    # =========================================================================
    # Cleanup
    # =========================================================================
    print("\n[Cleanup]")
    shutil.rmtree(OUTPUT_DIR)
    print(f"  Removed temp chunks: {OUTPUT_DIR}")
    con.close()
    if db_path.exists():
        db_path.unlink()
        print(f"  Removed temp database: {db_path}")

    # =========================================================================
    # Summary
    # =========================================================================
    print("\n" + "=" * 70)
    print("ADVISORY FLAG FEATURES V4 BUILD COMPLETE")
    print("=" * 70)
    print(f"  Output: {OUTPUT_FILE}")
    print(f"  Completed: {datetime.now().isoformat()}")


if __name__ == "__main__":
    main()
