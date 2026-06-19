"""
Build Advisory Flag Features V3
===============================

New approach using advisory + failure "flags" instead of failure-only.
Advisory data has ~42% coverage vs ~14% for failures.

Per component (tyres, brakes, suspension):
- miles_since_last_flag (advisory OR failure, strict prior)
- tests_since_last_flag
- has_prior_flag

Persistence features:
- flag_in_last_1 (was there a flag in t-1?)
- flag_in_last_2 (was there a flag in t-2?)
- flag_streak_len (consecutive prior tests with flags)

History controls:
- history_tests_observed
- history_years_observed

No imputation - allow NaN for missing values.
Flag = advisory OR failure (kept as separate indicator).

Created: 2026-01-06
Updated: 2026-01-13 - Added chunked processing for OOM safety
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
ADVISORY_TOTALS = WORK_DIR / "advisory_totals_3domain.parquet"  # Updated to use new 3-domain
COMPONENT_FAILURES = PROJECT_ROOT / "component_failures_all_years.parquet"

OUTPUT_FILE = WORK_DIR / "advisory_flag_features_v3.parquet"

# Chunked processing configuration (OOM-safe)
NUM_BUCKETS = 20
OUTPUT_DIR = WORK_DIR / "advisory_v3_chunks"


def main():
    print("=" * 70)
    print("BUILD ADVISORY FLAG FEATURES V3 (Chunked Processing)")
    print("=" * 70)
    print(f"Started: {datetime.now().isoformat()}")

    # Use disk-backed database for OOM safety
    db_path = WORK_DIR / "temp_advisory_v3.duckdb"
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    con.execute("SET memory_limit='4GB'")
    con.execute("SET threads=2")  # Reduced for memory efficiency
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
        # Step 2: Build history with advisories AND failures as "flags"
        # =====================================================================
        print("\n  [Step 2] Building history with advisory + failure flags...")

        # Join cycle_first_tests with advisory and failure data
        # Flag = advisory OR failure for each component
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE filtered_history AS
            SELECT
                c.vehicle_id,
                c.test_id,
                c.test_date,
                m.test_mileage,
                -- Component flags: advisory OR failure
                CASE WHEN COALESCE(a.adv_brakes, 0) > 0 OR COALESCE(cf.fail_brakes, 0) = 1
                     THEN 1 ELSE 0 END AS flag_brakes,
                CASE WHEN COALESCE(a.adv_tyres, 0) > 0 OR COALESCE(cf.fail_tyres, 0) = 1
                     THEN 1 ELSE 0 END AS flag_tyres,
                CASE WHEN COALESCE(a.adv_suspension_steering, 0) > 0 OR COALESCE(cf.fail_suspension, 0) = 1
                     THEN 1 ELSE 0 END AS flag_suspension,
                -- Separate indicators for failure vs advisory
                COALESCE(cf.fail_brakes, 0) AS fail_brakes,
                COALESCE(cf.fail_tyres, 0) AS fail_tyres,
                COALESCE(cf.fail_suspension, 0) AS fail_suspension,
                CASE WHEN COALESCE(a.adv_brakes, 0) > 0 THEN 1 ELSE 0 END AS adv_brakes,
                CASE WHEN COALESCE(a.adv_tyres, 0) > 0 THEN 1 ELSE 0 END AS adv_tyres,
                CASE WHEN COALESCE(a.adv_suspension_steering, 0) > 0 THEN 1 ELSE 0 END AS adv_suspension
            FROM read_parquet('{CYCLE_FIRST_TESTS}') c
            SEMI JOIN target_tests tt ON c.vehicle_id = tt.vehicle_id
            LEFT JOIN read_parquet('{MILEAGE_LOOKUP}') m ON c.test_id = m.test_id
            LEFT JOIN read_parquet('{ADVISORY_TOTALS}') a ON c.test_id = a.test_id
            LEFT JOIN read_parquet('{COMPONENT_FAILURES}') cf ON c.test_id = cf.test_id
        """)

        hist_count = con.execute("SELECT COUNT(*) FROM filtered_history").fetchone()[0]
        print(f"    History rows: {hist_count:,}")

        # =====================================================================
        # Step 3: Add test numbers first (to avoid nested window functions)
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
        # Step 4: Compute window features with proper sequencing
        # =====================================================================
        print("\n  [Step 4] Computing window features...")

        con.execute("""
            CREATE OR REPLACE TEMP TABLE with_lags AS
            SELECT
                t.*,
                -- Previous test flags (t-1)
                LAG(flag_brakes, 1) OVER w AS flag_brakes_t1,
                LAG(flag_tyres, 1) OVER w AS flag_tyres_t1,
                LAG(flag_suspension, 1) OVER w AS flag_suspension_t1,
                -- t-2 flags
                LAG(flag_brakes, 2) OVER w AS flag_brakes_t2,
                LAG(flag_tyres, 2) OVER w AS flag_tyres_t2,
                LAG(flag_suspension, 2) OVER w AS flag_suspension_t2,
                -- t-3 flag for streak
                LAG(flag_brakes, 3) OVER w AS flag_brakes_t3,
                LAG(flag_tyres, 3) OVER w AS flag_tyres_t3,
                LAG(flag_suspension, 3) OVER w AS flag_suspension_t3,
                -- Previous test date for history span
                FIRST_VALUE(test_date) OVER w AS first_test_date,
                -- Last mileage where each component was flagged (strict prior)
                MAX(CASE WHEN flag_brakes = 1 THEN test_mileage END)
                    OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS last_flag_mileage_brakes,
                MAX(CASE WHEN flag_tyres = 1 THEN test_mileage END)
                    OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS last_flag_mileage_tyres,
                MAX(CASE WHEN flag_suspension = 1 THEN test_mileage END)
                    OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS last_flag_mileage_suspension,
                -- Last test_num where each component was flagged (for tests_since)
                MAX(CASE WHEN flag_brakes = 1 THEN test_num END)
                    OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS last_flag_testnum_brakes,
                MAX(CASE WHEN flag_tyres = 1 THEN test_num END)
                    OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS last_flag_testnum_tyres,
                MAX(CASE WHEN flag_suspension = 1 THEN test_num END)
                    OVER (PARTITION BY vehicle_id ORDER BY test_date, test_id
                          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS last_flag_testnum_suspension
            FROM with_testnum t
            WINDOW w AS (PARTITION BY vehicle_id ORDER BY test_date, test_id)
        """)

        print("    Created lag features")

        # =====================================================================
        # Step 5: Compute streak lengths (consecutive flags)
        # =====================================================================
        print("\n  [Step 5] Computing streak features...")

        con.execute("""
            CREATE OR REPLACE TEMP TABLE with_streaks AS
            SELECT
                w.*,
                -- Count flags in last 3 tests (for streak approximation)
                COALESCE(w.flag_brakes_t1, 0) +
                COALESCE(w.flag_brakes_t2, 0) +
                COALESCE(w.flag_brakes_t3, 0) AS recent_flags_brakes,
                COALESCE(w.flag_tyres_t1, 0) +
                COALESCE(w.flag_tyres_t2, 0) +
                COALESCE(w.flag_tyres_t3, 0) AS recent_flags_tyres,
                COALESCE(w.flag_suspension_t1, 0) +
                COALESCE(w.flag_suspension_t2, 0) +
                COALESCE(w.flag_suspension_t3, 0) AS recent_flags_suspension
            FROM with_lags w
        """)

        print("    Created streak features")

        # =====================================================================
        # Step 6: Build final feature set and write to CHUNK file
        # =====================================================================
        print("\n  [Step 6] Building final features and writing to chunk...")

        chunk_file = OUTPUT_DIR / f"chunk_{bucket:02d}.parquet"
        con.execute(f"""
            COPY (
                SELECT
                    s.test_id,

                    -- History controls
                    s.test_num - 1 AS history_tests_observed,
                    EXTRACT(YEAR FROM s.test_date) - EXTRACT(YEAR FROM s.first_test_date)
                        AS history_years_observed,

                    -- BRAKES features
                    CASE WHEN s.last_flag_mileage_brakes IS NOT NULL
                         THEN s.test_mileage - s.last_flag_mileage_brakes END
                        AS miles_since_last_flag_brakes,
                    CASE WHEN s.last_flag_testnum_brakes IS NOT NULL
                         THEN s.test_num - s.last_flag_testnum_brakes END
                        AS tests_since_last_flag_brakes,
                    CASE WHEN s.last_flag_testnum_brakes IS NOT NULL THEN 1 ELSE 0 END
                        AS has_prior_flag_brakes,
                    s.flag_brakes_t1 AS flag_in_last_1_brakes,
                    s.flag_brakes_t2 AS flag_in_last_2_brakes,
                    s.recent_flags_brakes AS flag_streak_len_brakes,

                    -- TYRES features
                    CASE WHEN s.last_flag_mileage_tyres IS NOT NULL
                         THEN s.test_mileage - s.last_flag_mileage_tyres END
                        AS miles_since_last_flag_tyres,
                    CASE WHEN s.last_flag_testnum_tyres IS NOT NULL
                         THEN s.test_num - s.last_flag_testnum_tyres END
                        AS tests_since_last_flag_tyres,
                    CASE WHEN s.last_flag_testnum_tyres IS NOT NULL THEN 1 ELSE 0 END
                        AS has_prior_flag_tyres,
                    s.flag_tyres_t1 AS flag_in_last_1_tyres,
                    s.flag_tyres_t2 AS flag_in_last_2_tyres,
                    s.recent_flags_tyres AS flag_streak_len_tyres,

                    -- SUSPENSION features
                    CASE WHEN s.last_flag_mileage_suspension IS NOT NULL
                         THEN s.test_mileage - s.last_flag_mileage_suspension END
                        AS miles_since_last_flag_suspension,
                    CASE WHEN s.last_flag_testnum_suspension IS NOT NULL
                         THEN s.test_num - s.last_flag_testnum_suspension END
                        AS tests_since_last_flag_suspension,
                    CASE WHEN s.last_flag_testnum_suspension IS NOT NULL THEN 1 ELSE 0 END
                        AS has_prior_flag_suspension,
                    s.flag_suspension_t1 AS flag_in_last_1_suspension,
                    s.flag_suspension_t2 AS flag_in_last_2_suspension,
                    s.recent_flags_suspension AS flag_streak_len_suspension

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
            AVG(has_prior_flag_brakes) as pct_prior_brakes,
            AVG(has_prior_flag_tyres) as pct_prior_tyres,
            AVG(has_prior_flag_suspension) as pct_prior_suspension,
            AVG(miles_since_last_flag_brakes) as avg_miles_brakes,
            AVG(miles_since_last_flag_tyres) as avg_miles_tyres,
            AVG(flag_in_last_1_brakes) as pct_flag_t1_brakes,
            AVG(flag_in_last_1_tyres) as pct_flag_t1_tyres,
            AVG(history_tests_observed) as avg_hist_tests
        FROM read_parquet('{OUTPUT_FILE}')
    """).fetchone()

    print(f"  Rows: {stats[0]:,}")
    print(f"  Has prior flag: brakes={stats[1]*100:.1f}%, tyres={stats[2]*100:.1f}%, suspension={stats[3]*100:.1f}%")
    if stats[4] is not None and stats[5] is not None:
        print(f"  Avg miles_since_last_flag: brakes={stats[4]:,.0f}, tyres={stats[5]:,.0f}")
    print(f"  Flag in t-1: brakes={stats[6]*100:.1f}%, tyres={stats[7]*100:.1f}%")
    print(f"  Avg history_tests_observed: {stats[8]:.1f}")

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
    print("ADVISORY FLAG FEATURES V3 BUILD COMPLETE")
    print("=" * 70)
    print(f"  Output: {OUTPUT_FILE}")
    print(f"  Completed: {datetime.now().isoformat()}")


if __name__ == "__main__":
    main()
