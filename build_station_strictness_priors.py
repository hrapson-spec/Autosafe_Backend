"""
Build Station Strictness Priors
================================

Time-sliced station-level priors to detect strictness trends.

Concept: If a station is failing more cars recently (last 12 months) than
historically, it indicates either:
- Management change / crackdown
- Tester calibration shift
- Local enforcement changes

Feature: station_strictness_trend = recent_fail_rate / historical_fail_rate
- Value > 1.0 = station tightening standards
- Value < 1.0 = station loosening standards

Uses strictly prior data (ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
to prevent any temporal leakage.

Created: 2026-01-11
"""

import duckdb
from pathlib import Path
from datetime import datetime

# ============================================================================
# Configuration
# ============================================================================

PROJECT_ROOT = Path("/Users/henrirapson/autosafe_work")
CYCLE_HISTORY = PROJECT_ROOT / "cycle_history_dataset/cycle_history_full_combined_with_2023.parquet"
OUTPUT_FILE = PROJECT_ROOT / "station_strictness_priors.parquet"

# Smoothing parameter (shrink toward global rate)
K_STATION = 50  # Lower than segment (100) because stations have more volume


def main():
    print("=" * 70)
    print("BUILD STATION STRICTNESS PRIORS")
    print("=" * 70)
    print(f"Started: {datetime.now().isoformat()}")

    con = duckdb.connect()
    con.execute("SET memory_limit='8GB'")
    con.execute("SET threads=4")

    # =========================================================================
    # Step 1: Aggregate by Station + Month
    # =========================================================================
    print("\n[Step 1] Aggregating by Station + Month...")

    con.execute(f"""
        CREATE TEMP TABLE station_monthly AS
        SELECT
            DATE_TRUNC('month', test_date) as month,
            postcode_area,
            COUNT(*) as n_tests,
            SUM(CASE WHEN outcome = 'FAIL' THEN 1 ELSE 0 END) as n_fails
        FROM read_parquet('{CYCLE_HISTORY}')
        WHERE postcode_area IS NOT NULL
          AND test_date >= '2010-01-01'
        GROUP BY 1, 2
    """)

    n_rows = con.execute("SELECT COUNT(*) FROM station_monthly").fetchone()[0]
    n_stations = con.execute("SELECT COUNT(DISTINCT postcode_area) FROM station_monthly").fetchone()[0]
    print(f"  Monthly station records: {n_rows:,}")
    print(f"  Unique stations: {n_stations:,}")

    # =========================================================================
    # Step 2: Compute Global Monthly Rates
    # =========================================================================
    print("\n[Step 2] Computing global monthly rates...")

    con.execute("""
        CREATE TEMP TABLE global_monthly AS
        SELECT
            month,
            SUM(n_tests) as glob_tests,
            SUM(n_fails) as glob_fails
        FROM station_monthly
        GROUP BY 1
    """)

    # Compute cumulative global rates
    con.execute("""
        CREATE TEMP TABLE global_priors AS
        SELECT
            month as asof_month,
            SUM(glob_tests) OVER w_long AS glob_tests_long,
            SUM(glob_fails) OVER w_long AS glob_fails_long,
            SUM(glob_tests) OVER w_short AS glob_tests_short,
            SUM(glob_fails) OVER w_short AS glob_fails_short
        FROM global_monthly
        WINDOW
            w_long AS (ORDER BY month ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
            w_short AS (ORDER BY month ROWS BETWEEN 12 PRECEDING AND 1 PRECEDING)
    """)

    # =========================================================================
    # Step 3: Compute Station Cumulative Priors (Dual Window)
    # =========================================================================
    print("\n[Step 3] Computing station cumulative priors...")

    con.execute("""
        CREATE TEMP TABLE station_priors AS
        SELECT
            month as asof_month,
            postcode_area,
            SUM(n_tests) OVER w_long AS station_tests_long,
            SUM(n_fails) OVER w_long AS station_fails_long,
            SUM(n_tests) OVER w_short AS station_tests_short,
            SUM(n_fails) OVER w_short AS station_fails_short
        FROM station_monthly
        WINDOW
            w_long AS (PARTITION BY postcode_area ORDER BY month ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
            w_short AS (PARTITION BY postcode_area ORDER BY month ROWS BETWEEN 12 PRECEDING AND 1 PRECEDING)
    """)

    # =========================================================================
    # Step 4: Calculate EB-Smoothed Rates and Strictness Trend
    # =========================================================================
    print("\n[Step 4] Calculating strictness trend...")

    con.execute(f"""
        CREATE TEMP TABLE strictness_calculated AS
        SELECT
            s.asof_month,
            s.postcode_area,

            -- Debug counts
            COALESCE(s.station_tests_long, 0) as station_tests_long,
            COALESCE(s.station_tests_short, 0) as station_tests_short,

            -- Global rates
            COALESCE(g.glob_fails_long, 0) * 1.0 / NULLIF(g.glob_tests_long, 0) as global_rate_long,
            COALESCE(g.glob_fails_short, 0) * 1.0 / NULLIF(g.glob_tests_short, 0) as global_rate_short,

            -- Station rate LONG (EB-smoothed toward global)
            (COALESCE(s.station_fails_long, 0) + {K_STATION} * (COALESCE(g.glob_fails_long, 0) * 1.0 / NULLIF(g.glob_tests_long, 0)))
            / (COALESCE(s.station_tests_long, 0) + {K_STATION}) as station_rate_long,

            -- Station rate SHORT (EB-smoothed toward global short)
            (COALESCE(s.station_fails_short, 0) + {K_STATION} * (COALESCE(g.glob_fails_short, 0) * 1.0 / NULLIF(g.glob_tests_short, 0)))
            / (COALESCE(s.station_tests_short, 0) + {K_STATION}) as station_rate_short

        FROM station_priors s
        JOIN global_priors g ON s.asof_month = g.asof_month
        WHERE s.asof_month >= '2016-01-01'
          AND s.station_tests_long > 0  -- Must have some history
    """)

    # Add strictness trend
    con.execute("""
        CREATE TEMP TABLE final_output AS
        SELECT
            asof_month,
            postcode_area,
            station_tests_long,
            station_tests_short,
            station_rate_long,
            station_rate_short,

            -- Strictness trend: recent / historical
            -- > 1.0 = stricter, < 1.0 = looser
            CASE
                WHEN station_rate_long > 0 THEN station_rate_short / station_rate_long
                ELSE 1.0
            END as station_strictness_trend

        FROM strictness_calculated
    """)

    # =========================================================================
    # Step 5: Materialize
    # =========================================================================
    print("\n[Step 5] Materializing...")

    con.execute(f"""
        COPY final_output TO '{OUTPUT_FILE}' (FORMAT PARQUET, COMPRESSION SNAPPY)
    """)

    # Stats
    stats = con.execute("""
        SELECT
            MIN(asof_month),
            MAX(asof_month),
            COUNT(*),
            COUNT(DISTINCT postcode_area),
            AVG(station_strictness_trend),
            MIN(station_strictness_trend),
            MAX(station_strictness_trend)
        FROM final_output
    """).fetchone()

    print(f"\n  Date range: {stats[0]} to {stats[1]}")
    print(f"  Total rows: {stats[2]:,}")
    print(f"  Unique stations: {stats[3]:,}")
    print(f"  Avg strictness trend: {stats[4]:.4f}")
    print(f"  Min strictness trend: {stats[5]:.4f}")
    print(f"  Max strictness trend: {stats[6]:.4f}")

    # Sample output
    print("\n  Sample output (2023-06):")
    sample = con.execute("""
        SELECT postcode_area, station_tests_long, station_rate_long, station_rate_short, station_strictness_trend
        FROM final_output
        WHERE asof_month = '2023-06-01'
        ORDER BY station_tests_long DESC
        LIMIT 10
    """).fetchdf()
    print(sample.to_string(index=False))

    print("\n" + "=" * 70)
    print(f"OUTPUT: {OUTPUT_FILE}")
    print("=" * 70)
    con.close()


if __name__ == "__main__":
    main()
