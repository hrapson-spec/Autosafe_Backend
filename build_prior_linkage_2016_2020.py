"""
Build Prior Test Linkage for 2016-2020 Data
============================================

Computes prev_cycle_outcome and prev_advisory_* features for 2016-2020 years
that are currently hard-coded to NONE in build_stratified_validation.py.

This script:
1. Reads raw test_result CSVs for 2016-2020 (~150M records)
2. Reads test_item CSVs to compute advisory counts per test
3. Applies LAG() window function to link each test to its prior test
4. Outputs: prior_linkage_2016_2020.parquet

Usage:
    python build_prior_linkage_2016_2020.py

Created: 2026-01-13
"""

import duckdb
from pathlib import Path
from datetime import datetime
import os

# =============================================================================
# Configuration
# =============================================================================

PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
WORK_DIR = Path.home() / "autosafe_work"
OUTPUT_FILE = WORK_DIR / "prior_linkage_2016_2020.parquet"

# Years to process (those with missing prior linkage)
YEARS_TO_PROCESS = [2016, 2017, 2018, 2019, 2020]

# RFR codes for advisory component groups (from rfr_subcomponent_groups.py)
BRAKES_RFR_CODES = {30329, 30333, 30335}  # Brake pads/discs
TYRES_RFR_CODES = {31194, 31195, 31196}    # Tyre tread/damage
STEERING_RFR_CODES = {31302, 31292, 31268}  # Steering components
SUSPENSION_RFR_CODES = {31322, 31321, 31323, 30969, 30970, 31226, 31227, 31228}  # Ball joints, shocks, dust covers

# Test result paths per year
TEST_RESULT_PATHS = {
    2016: PROJECT_ROOT / "MOT Test Results/2016/test_result_2016.txt.gz",
    2017: PROJECT_ROOT / "MOT Test Results/2017",  # Multiple CSVs
    2018: PROJECT_ROOT / "MOT Test Results/2018",
    2019: PROJECT_ROOT / "MOT Test Results/2019",
    2020: PROJECT_ROOT / "MOT Test Results/2020",
}

TEST_ITEM_DIR = PROJECT_ROOT / "test_items"


def get_test_result_sql(year: int) -> str:
    """Generate SQL to read test_result data for a given year."""
    if year == 2016:
        # 2016 is pipe-delimited gzipped file
        path = TEST_RESULT_PATHS[2016]
        return f"""
            SELECT
                CAST(test_id AS BIGINT) as test_id,
                CAST(vehicle_id AS BIGINT) as vehicle_id,
                CAST(test_date AS DATE) as test_date,
                test_result,
                CAST(test_mileage AS BIGINT) as test_mileage
            FROM read_csv('{path}',
                         delim='|',
                         header=true,
                         columns={{'test_id': 'BIGINT', 'vehicle_id': 'BIGINT', 'test_date': 'DATE',
                                  'test_class_id': 'VARCHAR', 'test_type': 'VARCHAR', 'test_result': 'VARCHAR',
                                  'test_mileage': 'BIGINT', 'postcode_area': 'VARCHAR', 'make': 'VARCHAR',
                                  'model': 'VARCHAR', 'colour': 'VARCHAR', 'fuel_type': 'VARCHAR',
                                  'cylinder_capacity': 'VARCHAR', 'first_use_date': 'DATE'}})
        """
    else:
        # 2017-2020 are comma-delimited CSVs in subdirectories
        path = TEST_RESULT_PATHS[year]
        pattern = f"{path}/*.csv"
        return f"""
            SELECT
                CAST(test_id AS BIGINT) as test_id,
                CAST(vehicle_id AS BIGINT) as vehicle_id,
                CAST(test_date AS DATE) as test_date,
                test_result,
                CAST(test_mileage AS BIGINT) as test_mileage
            FROM read_csv_auto('{pattern}')
        """


def get_test_item_sql(year: int) -> str:
    """Generate SQL to read test_item data for a given year."""
    year_dir = TEST_ITEM_DIR / str(year)
    if not year_dir.exists():
        return None

    # Find CSV files in the year directory
    csv_files = list(year_dir.glob("*.csv"))
    csv_files = [f for f in csv_files if not f.name.startswith("._")]  # Skip macOS hidden files

    if not csv_files:
        return None

    if len(csv_files) == 1:
        return f"""
            SELECT test_id, rfr_id, rfr_type_code
            FROM read_csv_auto('{csv_files[0]}')
        """
    else:
        # Multiple CSV files - use glob
        pattern = f"{year_dir}/*.csv"
        return f"""
            SELECT test_id, rfr_id, rfr_type_code
            FROM read_csv_auto('{pattern}')
            WHERE rfr_type_code IS NOT NULL
        """


def main():
    start_time = datetime.now()
    print("=" * 70)
    print("BUILD PRIOR LINKAGE FOR 2016-2020")
    print("=" * 70)

    con = duckdb.connect()

    # =========================================================================
    # Step 1: Load all test results for 2016-2020
    # =========================================================================
    print("\n[1] Loading test results for 2016-2020...")

    union_parts = []
    for year in YEARS_TO_PROCESS:
        sql = get_test_result_sql(year)
        union_parts.append(f"({sql})")
        print(f"  Added {year}")

    all_tests_sql = " UNION ALL ".join(union_parts)

    # Create temp table with all tests
    con.execute(f"""
        CREATE TEMP TABLE all_tests AS
        {all_tests_sql}
    """)

    test_count = con.execute("SELECT COUNT(*) FROM all_tests").fetchone()[0]
    print(f"  Total test records: {test_count:,}")

    # =========================================================================
    # Step 2: Load test items and compute advisory counts per test
    # =========================================================================
    print("\n[2] Loading test items and computing advisory counts...")

    brakes_codes = ",".join(str(c) for c in BRAKES_RFR_CODES)
    tyres_codes = ",".join(str(c) for c in TYRES_RFR_CODES)
    steering_codes = ",".join(str(c) for c in STEERING_RFR_CODES)
    suspension_codes = ",".join(str(c) for c in SUSPENSION_RFR_CODES)

    item_union_parts = []
    for year in YEARS_TO_PROCESS:
        sql = get_test_item_sql(year)
        if sql:
            item_union_parts.append(f"({sql})")
            print(f"  Added test_items for {year}")
        else:
            print(f"  WARNING: No test_items found for {year}")

    if item_union_parts:
        all_items_sql = " UNION ALL ".join(item_union_parts)

        # Compute advisory counts per test
        con.execute(f"""
            CREATE TEMP TABLE test_advisory_counts AS
            SELECT
                test_id,
                SUM(CASE WHEN rfr_id IN ({brakes_codes}) AND rfr_type_code = 'A' THEN 1 ELSE 0 END) as advisory_brakes,
                SUM(CASE WHEN rfr_id IN ({tyres_codes}) AND rfr_type_code = 'A' THEN 1 ELSE 0 END) as advisory_tyres,
                SUM(CASE WHEN rfr_id IN ({steering_codes}) AND rfr_type_code = 'A' THEN 1 ELSE 0 END) as advisory_steering,
                SUM(CASE WHEN rfr_id IN ({suspension_codes}) AND rfr_type_code = 'A' THEN 1 ELSE 0 END) as advisory_suspension,
                SUM(CASE WHEN rfr_type_code = 'A' THEN 1 ELSE 0 END) as advisory_total
            FROM ({all_items_sql})
            GROUP BY test_id
        """)

        advisory_count = con.execute("SELECT COUNT(*) FROM test_advisory_counts").fetchone()[0]
        print(f"  Tests with advisory data: {advisory_count:,}")
    else:
        # No test items available - create empty table
        print("  WARNING: No test_item data found. Advisory columns will be 0.")
        con.execute("""
            CREATE TEMP TABLE test_advisory_counts AS
            SELECT
                CAST(0 AS BIGINT) as test_id,
                0 as advisory_brakes,
                0 as advisory_tyres,
                0 as advisory_steering,
                0 as advisory_suspension,
                0 as advisory_total
            WHERE 1=0
        """)

    # =========================================================================
    # Step 3: Join tests with advisory counts
    # =========================================================================
    print("\n[3] Joining tests with advisory counts...")

    con.execute("""
        CREATE TEMP TABLE tests_with_advisory AS
        SELECT
            t.test_id,
            t.vehicle_id,
            t.test_date,
            t.test_result,
            t.test_mileage,
            COALESCE(a.advisory_brakes, 0) as advisory_brakes,
            COALESCE(a.advisory_tyres, 0) as advisory_tyres,
            COALESCE(a.advisory_steering, 0) as advisory_steering,
            COALESCE(a.advisory_suspension, 0) as advisory_suspension,
            COALESCE(a.advisory_total, 0) as advisory_total
        FROM all_tests t
        LEFT JOIN test_advisory_counts a ON t.test_id = a.test_id
    """)

    joined_count = con.execute("SELECT COUNT(*) FROM tests_with_advisory").fetchone()[0]
    print(f"  Joined records: {joined_count:,}")

    # =========================================================================
    # Step 4: Compute prior linkage using LAG() window function
    # =========================================================================
    print("\n[4] Computing prior linkage with LAG() window function...")

    con.execute("""
        CREATE TEMP TABLE prior_linkage AS
        WITH sorted_tests AS (
            SELECT
                test_id,
                vehicle_id,
                test_date,
                test_result,
                test_mileage,
                advisory_brakes,
                advisory_tyres,
                advisory_steering,
                advisory_suspension,
                advisory_total,
                LAG(test_date) OVER w AS prev_test_date,
                LAG(test_result) OVER w AS prev_test_result,
                LAG(test_mileage) OVER w AS prev_test_mileage,
                LAG(advisory_brakes) OVER w AS prev_advisory_brakes,
                LAG(advisory_tyres) OVER w AS prev_advisory_tyres,
                LAG(advisory_steering) OVER w AS prev_advisory_steering,
                LAG(advisory_suspension) OVER w AS prev_advisory_suspension,
                LAG(advisory_total) OVER w AS prev_advisory_total
            FROM tests_with_advisory
            WINDOW w AS (PARTITION BY vehicle_id ORDER BY test_date, test_id)
        )
        SELECT
            test_id,
            vehicle_id,
            test_date,
            -- Prior outcome
            CASE
                WHEN prev_test_date IS NULL THEN 'NO_PRIOR'
                WHEN prev_test_result IN ('P', 'PASS', 'PRS') THEN 'PASS'
                ELSE 'FAIL'
            END as prev_cycle_outcome_band,
            -- Gap band
            CASE
                WHEN prev_test_date IS NULL THEN 'NO_PRIOR'
                WHEN DATE_DIFF('day', prev_test_date, test_date) < 180 THEN '<180d'
                WHEN DATE_DIFF('day', prev_test_date, test_date) < 365 THEN '180d-1y'
                WHEN DATE_DIFF('day', prev_test_date, test_date) < 730 THEN '1-2y'
                ELSE '2y+'
            END as gap_band,
            DATE_DIFF('day', prev_test_date, test_date) as days_since_prev_cycle,
            prev_test_mileage as prev_mileage,
            -- Prior advisory counts
            COALESCE(prev_advisory_brakes, 0) as prev_advisory_brakes,
            COALESCE(prev_advisory_tyres, 0) as prev_advisory_tyres,
            COALESCE(prev_advisory_steering, 0) as prev_advisory_steering,
            COALESCE(prev_advisory_suspension, 0) as prev_advisory_suspension,
            COALESCE(prev_advisory_total, 0) as prev_advisory_total
        FROM sorted_tests
    """)

    linkage_count = con.execute("SELECT COUNT(*) FROM prior_linkage").fetchone()[0]
    print(f"  Prior linkage records: {linkage_count:,}")

    # =========================================================================
    # Step 5: Check coverage statistics
    # =========================================================================
    print("\n[5] Coverage statistics...")

    stats = con.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN prev_cycle_outcome_band = 'NO_PRIOR' THEN 1 ELSE 0 END) as no_prior,
            SUM(CASE WHEN prev_cycle_outcome_band = 'PASS' THEN 1 ELSE 0 END) as has_pass,
            SUM(CASE WHEN prev_cycle_outcome_band = 'FAIL' THEN 1 ELSE 0 END) as has_fail,
            SUM(CASE WHEN prev_advisory_total > 0 THEN 1 ELSE 0 END) as has_advisory
        FROM prior_linkage
    """).fetchdf()

    total = stats['total'].iloc[0]
    no_prior = stats['no_prior'].iloc[0]
    has_pass = stats['has_pass'].iloc[0]
    has_fail = stats['has_fail'].iloc[0]
    has_advisory = stats['has_advisory'].iloc[0]

    print(f"  Total: {total:,}")
    print(f"  NO_PRIOR: {no_prior:,} ({no_prior/total*100:.1f}%)")
    print(f"  PASS: {has_pass:,} ({has_pass/total*100:.1f}%)")
    print(f"  FAIL: {has_fail:,} ({has_fail/total*100:.1f}%)")
    print(f"  Has prior advisory: {has_advisory:,} ({has_advisory/total*100:.1f}%)")

    # =========================================================================
    # Step 6: Save to parquet
    # =========================================================================
    print("\n[6] Saving to parquet...")

    con.execute(f"""
        COPY prior_linkage TO '{OUTPUT_FILE}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)

    file_size = OUTPUT_FILE.stat().st_size / (1024 * 1024)
    print(f"  Saved to: {OUTPUT_FILE}")
    print(f"  Size: {file_size:.1f} MB")

    # =========================================================================
    # Summary
    # =========================================================================
    elapsed = (datetime.now() - start_time).total_seconds()
    print("\n" + "=" * 70)
    print("PRIOR LINKAGE BUILD COMPLETE")
    print("=" * 70)
    print(f"  Total records: {total:,}")
    print(f"  Linked (PASS/FAIL): {has_pass + has_fail:,} ({(has_pass + has_fail)/total*100:.1f}%)")
    print(f"  Has prior advisory: {has_advisory:,} ({has_advisory/total*100:.1f}%)")
    print(f"  Elapsed: {elapsed:.1f}s")
    print("=" * 70)

    con.close()


if __name__ == "__main__":
    main()
