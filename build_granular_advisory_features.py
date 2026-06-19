"""
Build Granular Advisory Features

Creates per-test features counting prior advisories and failures
by subcomponent group (ball joints, dust covers, brake pipes, etc.).

Input:
- test_items/{year}/test_item.csv (pipe-delimited)
- cycle_first_with_history.parquet

Output:
- granular_advisory_features.parquet

Usage:
    python build_granular_advisory_features.py

Created: 2026-01-11
"""

import duckdb
from pathlib import Path
from datetime import datetime
import pandas as pd

from rfr_subcomponent_groups import (
    ADVISORY_SUBCOMPONENT_GROUPS,
    FAILURE_SUBCOMPONENT_GROUPS,
)

# =============================================================================
# Configuration
# =============================================================================

PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
WORK_DIR = Path("/Users/henrirapson/autosafe_work")

TEST_ITEMS_DIR = PROJECT_ROOT / "test_items"
CYCLE_HISTORY = PROJECT_ROOT / "cycle_first_with_history.parquet"

OUTPUT_FILE = WORK_DIR / "granular_advisory_features.parquet"

# Years to process
YEARS = ['2019', '2020', '2021', '2022', '2023']


def build_rfr_case_statements() -> tuple[str, str]:
    """Build SQL CASE statements for each subcomponent group."""

    advisory_cases = []
    for group_name, codes in ADVISORY_SUBCOMPONENT_GROUPS.items():
        codes_str = ', '.join(str(c) for c in codes)
        col_name = f"adv_{group_name}"
        advisory_cases.append(
            f"SUM(CASE WHEN rfr_id IN ({codes_str}) AND rfr_type_code = 'A' THEN 1 ELSE 0 END) AS {col_name}"
        )

    failure_cases = []
    for group_name, codes in FAILURE_SUBCOMPONENT_GROUPS.items():
        codes_str = ', '.join(str(c) for c in codes)
        col_name = f"fail_{group_name}"
        failure_cases.append(
            f"SUM(CASE WHEN rfr_id IN ({codes_str}) AND rfr_type_code IN ('F', 'M') THEN 1 ELSE 0 END) AS {col_name}"
        )

    return ',\n            '.join(advisory_cases), ',\n            '.join(failure_cases)


def main():
    start_time = datetime.now()
    print(f"Build Granular Advisory Features")
    print(f"Started: {start_time}")
    print("=" * 60)

    conn = duckdb.connect()

    # Build file list
    test_item_files = []
    for year in YEARS:
        path = TEST_ITEMS_DIR / year / "test_item.csv"
        if path.exists():
            test_item_files.append(str(path))
            print(f"  Found: {path}")

    if not test_item_files:
        print("ERROR: No test_item files found")
        return

    # Build CASE statements for aggregation
    adv_cases, fail_cases = build_rfr_case_statements()

    print(f"\nStep 1: Loading and aggregating test_items...")

    # Create a UNION of all test_item files
    union_query = " UNION ALL ".join([
        f"SELECT * FROM read_csv('{f}', delim='|', header=true, columns={{'test_id': 'BIGINT', 'rfr_id': 'INT', 'rfr_type_code': 'VARCHAR', 'location_id': 'INT', 'dangerous_mark': 'VARCHAR'}})"
        for f in test_item_files
    ])

    # Aggregate RFR codes per test
    agg_query = f"""
        CREATE OR REPLACE TABLE test_rfr_agg AS
        SELECT
            test_id,
            {adv_cases},
            {fail_cases}
        FROM ({union_query})
        GROUP BY test_id
    """

    print("  Executing aggregation query (this may take a few minutes)...")
    conn.execute(agg_query)

    agg_count = conn.execute("SELECT COUNT(*) FROM test_rfr_agg").fetchone()[0]
    print(f"  Aggregated {agg_count:,} unique tests")

    print(f"\nStep 2: Joining to cycle history for vehicle_id and test_date...")

    # Join to cycle_history to get vehicle_id and test_date
    join_query = f"""
        CREATE OR REPLACE TABLE test_with_vehicle AS
        SELECT
            t.*,
            c.vehicle_id,
            c.test_date
        FROM test_rfr_agg t
        INNER JOIN read_parquet('{CYCLE_HISTORY}') c
            ON t.test_id = c.test_id
    """

    conn.execute(join_query)
    joined_count = conn.execute("SELECT COUNT(*) FROM test_with_vehicle").fetchone()[0]
    print(f"  Joined {joined_count:,} tests with vehicle info")

    print(f"\nStep 3: Computing prior test aggregates (as-of features)...")

    # Get column names for aggregation
    adv_cols = [f"adv_{name}" for name in ADVISORY_SUBCOMPONENT_GROUPS.keys()]
    fail_cols = [f"fail_{name}" for name in FAILURE_SUBCOMPONENT_GROUPS.keys()]
    all_cols = adv_cols + fail_cols

    # Build the sum expressions for prior tests
    sum_exprs = ', '.join([f"SUM(prior.{col}) AS prev_{col}" for col in all_cols])

    # Self-join to get prior test aggregates
    # For each test, sum all RFR counts from PRIOR tests of the same vehicle
    prior_query = f"""
        CREATE OR REPLACE TABLE granular_features AS
        SELECT
            curr.test_id,
            {sum_exprs}
        FROM test_with_vehicle curr
        LEFT JOIN test_with_vehicle prior
            ON curr.vehicle_id = prior.vehicle_id
            AND prior.test_date < curr.test_date
        GROUP BY curr.test_id
    """

    conn.execute(prior_query)

    final_count = conn.execute("SELECT COUNT(*) FROM granular_features").fetchone()[0]
    print(f"  Created {final_count:,} test records with prior features")

    print(f"\nStep 4: Filling nulls and saving...")

    # Fill nulls with 0 and export
    fill_exprs = ', '.join([f"COALESCE(prev_{col}, 0) AS prev_{col}" for col in all_cols])

    export_query = f"""
        COPY (
            SELECT test_id, {fill_exprs}
            FROM granular_features
        ) TO '{OUTPUT_FILE}' (FORMAT PARQUET)
    """

    conn.execute(export_query)

    # Show sample statistics
    print(f"\nStep 5: Validating output...")

    df = pd.read_parquet(OUTPUT_FILE)
    print(f"  Output shape: {df.shape}")
    print(f"\n  Feature coverage (% with value > 0):")

    for col in df.columns:
        if col == 'test_id':
            continue
        pct_nonzero = (df[col] > 0).mean() * 100
        mean_val = df[col].mean()
        print(f"    {col}: {pct_nonzero:.1f}% non-zero, mean={mean_val:.2f}")

    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds() / 60

    print(f"\n{'=' * 60}")
    print(f"Complete! Duration: {duration:.1f} minutes")
    print(f"Output: {OUTPUT_FILE}")

    conn.close()


if __name__ == "__main__":
    main()
