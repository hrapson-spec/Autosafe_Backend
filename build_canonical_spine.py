"""
Canonical Vehicle Test Spine Builder (Memory-Efficient)
========================================================

Builds a single, consistent source of truth for all vehicle test history.
Uses bucketed processing to handle large datasets on 8GB RAM machines.

Approach:
1. Use existing cycle_first_tests.parquet as base (already has cycle-first detection)
2. Join with raw CSVs to get test_result and test_mileage
3. Compute history features per-bucket using window functions
4. Merge buckets into final canonical_spine.parquet

Confirmed choices:
- Scope: Cycle-first tests only
- Years: 2019-2024 (what's available in cycle_first_tests.parquet)
- Source: Existing parquet + raw CSV join for missing columns

Output: canonical_spine.parquet (~2GB, ~150M rows)

Usage:
    python build_canonical_spine.py

Created: 2026-01-13
"""

import duckdb
import logging
from pathlib import Path
from datetime import datetime
import json
import shutil

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Paths
PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
WORK_DIR = Path.home() / "autosafe_work"
STAGE_DIR = WORK_DIR / "spine_staging"
OUTPUT_FILE = WORK_DIR / "canonical_spine.parquet"
MANIFEST_FILE = WORK_DIR / "manifests" / "canonical_spine_manifest.json"

# Existing cycle_first_tests file (has test_id, vehicle_id, test_date)
CYCLE_FIRST_TESTS = WORK_DIR / "cycle_first_tests.parquet"

# Raw CSV sources for test_result and test_mileage lookup
RAW_SOURCES = [
    (2019, PROJECT_ROOT / "MOT Test Results/2019/dft_test_result-*.csv", ","),
    (2020, PROJECT_ROOT / "MOT Test Results/2020/dft_test_result-*.csv", ","),
    (2021, PROJECT_ROOT / "MOT Test Results/2021/test_result_*.csv", ","),
    (2022, PROJECT_ROOT / "MOT Test Results/2022/test_result_2022.csv", "|"),
    (2023, PROJECT_ROOT / "MOT Test Results/2023/test_result.csv", "|"),
    (2024, PROJECT_ROOT / "MOT Test Results/test_result_2024*.csv", ","),
]

NUM_BUCKETS = 256  # Process in 256 vehicle buckets for memory efficiency


def check_file_exists(pattern: Path) -> bool:
    """Check if a file pattern matches any files."""
    if '*' in str(pattern):
        import glob
        matches = glob.glob(str(pattern))
        return len(matches) > 0
    return pattern.exists()


def build_test_lookup(conn):
    """Build a lookup table from raw CSVs: test_id -> (test_result, test_mileage)."""
    logger.info("Building test lookup from raw CSVs...")

    union_parts = []
    for year, pattern, delim in RAW_SOURCES:
        pattern_str = str(pattern)
        if not check_file_exists(pattern):
            logger.warning(f"  {year}: No files found")
            continue

        logger.info(f"  {year}: {pattern_str}")
        union_parts.append(f"""
            SELECT
                CAST(test_id AS BIGINT) as test_id,
                UPPER(TRIM(test_result)) as test_result,
                CAST(test_mileage AS INTEGER) as test_mileage
            FROM read_csv(
                '{pattern_str}',
                delim = '{delim}',
                header = true,
                auto_detect = true,
                ignore_errors = true,
                null_padding = true,
                parallel = false
            )
            WHERE test_id IS NOT NULL
        """)

    if not union_parts:
        raise ValueError("No raw CSV files found!")

    # Create the lookup table
    lookup_sql = " UNION ALL ".join(union_parts)
    conn.execute(f"""
        CREATE OR REPLACE TABLE test_lookup AS
        SELECT test_id, test_result, test_mileage
        FROM ({lookup_sql})
    """)

    count = conn.execute("SELECT COUNT(*) FROM test_lookup").fetchone()[0]
    logger.info(f"  Lookup table created: {count:,} rows")


def process_bucket(conn, bucket: int, stage_dir: Path):
    """Process a single vehicle bucket."""
    output_file = stage_dir / f"bucket_{bucket:03d}.parquet"

    conn.execute(f"""
        COPY (
            WITH base AS (
                SELECT
                    c.test_id,
                    c.vehicle_id,
                    c.test_date,
                    YEAR(c.test_date) as source_year,
                    l.test_result,
                    l.test_mileage,
                    CASE
                        WHEN l.test_result IN ('P', 'PASS', 'PRS') THEN 'PASS'
                        WHEN l.test_result IN ('F', 'FAIL') THEN 'FAIL'
                        ELSE 'OTHER'
                    END as outcome
                FROM read_parquet('{CYCLE_FIRST_TESTS}') c
                LEFT JOIN test_lookup l ON c.test_id = l.test_id
                WHERE CAST(c.vehicle_id % {NUM_BUCKETS} AS INTEGER) = {bucket}
            ),
            with_history AS (
                SELECT
                    b.test_id,
                    b.vehicle_id,
                    b.test_date,
                    b.source_year,
                    b.test_result,
                    b.test_mileage,
                    b.outcome,

                    -- History counts
                    ROW_NUMBER() OVER w - 1 as n_prior_tests,

                    -- Prior cycle linkage
                    LAG(b.test_id) OVER w as prev_cycle_test_id,
                    LAG(b.test_date) OVER w as prev_cycle_date,
                    LAG(b.outcome) OVER w as prev_cycle_outcome,
                    LAG(b.test_mileage) OVER w as prev_cycle_mileage,

                    -- Time since last test
                    DATEDIFF('day', LAG(b.test_date) OVER w, b.test_date) as days_since_last_test,

                    -- Cumulative fail count
                    SUM(CASE WHEN b.outcome = 'FAIL' THEN 1 ELSE 0 END)
                        OVER (PARTITION BY b.vehicle_id ORDER BY b.test_date, b.test_id
                              ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) as n_prior_fails

                FROM base b
                WINDOW w AS (PARTITION BY b.vehicle_id ORDER BY b.test_date, b.test_id)
            )
            SELECT
                test_id,
                vehicle_id,
                test_date,
                source_year,
                test_result,
                test_mileage,
                outcome,

                n_prior_tests,
                prev_cycle_test_id,
                prev_cycle_date,

                CASE
                    WHEN prev_cycle_date IS NULL THEN 'NONE'
                    WHEN prev_cycle_outcome = 'PASS' THEN 'PASS'
                    WHEN prev_cycle_outcome = 'FAIL' THEN 'FAIL'
                    ELSE 'NONE'
                END as prev_cycle_outcome_band,

                days_since_last_test,
                COALESCE(n_prior_fails, 0) as n_prior_fails,

                test_mileage - prev_cycle_mileage as miles_since_last_test_raw,

                CASE
                    WHEN prev_cycle_mileage IS NULL THEN NULL
                    WHEN test_mileage IS NULL THEN 'missing'
                    WHEN test_mileage < prev_cycle_mileage
                         AND prev_cycle_mileage - test_mileage < 10000 THEN 'rollover'
                    WHEN test_mileage < prev_cycle_mileage THEN 'implausible_negative'
                    WHEN days_since_last_test IS NOT NULL
                         AND days_since_last_test > 0
                         AND (test_mileage - prev_cycle_mileage) / days_since_last_test > 500
                        THEN 'implausible_high'
                    ELSE 'valid'
                END as mileage_quality_flag,

                CASE
                    WHEN prev_cycle_mileage IS NULL THEN NULL
                    WHEN test_mileage IS NULL THEN NULL
                    WHEN test_mileage < prev_cycle_mileage THEN NULL
                    WHEN days_since_last_test IS NULL OR days_since_last_test <= 0 THEN NULL
                    WHEN (test_mileage - prev_cycle_mileage) / days_since_last_test > 500 THEN NULL
                    ELSE (test_mileage - prev_cycle_mileage) * 365.25 / days_since_last_test
                END as annualized_mileage

            FROM with_history
        )
        TO '{output_file}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
    """)


def build_canonical_spine():
    """Build the canonical spine using bucketed processing."""
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info("BUILDING CANONICAL VEHICLE TEST SPINE")
    logger.info("=" * 60)

    # Check prerequisites
    if not CYCLE_FIRST_TESTS.exists():
        raise FileNotFoundError(f"cycle_first_tests.parquet not found: {CYCLE_FIRST_TESTS}")

    # Ensure manifests and staging directories exist
    (WORK_DIR / "manifests").mkdir(parents=True, exist_ok=True)
    if STAGE_DIR.exists():
        shutil.rmtree(STAGE_DIR)
    STAGE_DIR.mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect()

    # Memory settings
    conn.execute("SET memory_limit='5GB'")
    conn.execute("SET threads=2")
    conn.execute(f"SET temp_directory='{WORK_DIR / 'duckdb_temp'}'")
    conn.execute("SET preserve_insertion_order=false")

    # Step 1: Build test lookup
    build_test_lookup(conn)

    # Step 2: Process each bucket
    logger.info(f"Processing {NUM_BUCKETS} vehicle buckets...")
    for bucket in range(NUM_BUCKETS):
        if bucket % 16 == 0:
            logger.info(f"  Bucket {bucket}/{NUM_BUCKETS}...")
        process_bucket(conn, bucket, STAGE_DIR)

    logger.info("  All buckets complete")

    # Step 3: Merge buckets
    logger.info("Merging buckets into final spine...")
    conn.execute(f"""
        COPY (
            SELECT * FROM read_parquet('{STAGE_DIR}/*.parquet')
        )
        TO '{OUTPUT_FILE}'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
    """)

    # Cleanup staging
    shutil.rmtree(STAGE_DIR)

    # Get stats
    logger.info("Computing statistics...")
    stats = conn.execute(f"""
        SELECT
            COUNT(*) as total_rows,
            COUNT(DISTINCT vehicle_id) as unique_vehicles,
            SUM(CASE WHEN outcome = 'PASS' THEN 1 ELSE 0 END) as pass_count,
            SUM(CASE WHEN outcome = 'FAIL' THEN 1 ELSE 0 END) as fail_count,
            SUM(CASE WHEN n_prior_tests > 0 THEN 1 ELSE 0 END) as has_prior,
            SUM(CASE WHEN prev_cycle_outcome_band IN ('PASS', 'FAIL') THEN 1 ELSE 0 END) as has_linked_outcome,
            SUM(CASE WHEN annualized_mileage IS NOT NULL THEN 1 ELSE 0 END) as has_mileage
        FROM read_parquet('{OUTPUT_FILE}')
    """).fetchone()

    total_rows, unique_vehicles, pass_count, fail_count, has_prior, has_linked, has_mileage = stats

    # Coverage by year
    year_stats = conn.execute(f"""
        SELECT
            source_year,
            COUNT(*) as total,
            ROUND(100.0 * SUM(CASE WHEN n_prior_tests > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) as prior_pct,
            ROUND(100.0 * SUM(CASE WHEN prev_cycle_outcome_band IN ('PASS','FAIL') THEN 1 ELSE 0 END) / COUNT(*), 1) as linked_pct,
            ROUND(100.0 * SUM(CASE WHEN annualized_mileage IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 1) as mileage_pct
        FROM read_parquet('{OUTPUT_FILE}')
        GROUP BY source_year
        ORDER BY source_year
    """).fetchall()

    elapsed = (datetime.now() - start_time).total_seconds()

    logger.info("=" * 60)
    logger.info("CANONICAL SPINE BUILD COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Output: {OUTPUT_FILE}")
    logger.info(f"Total rows: {total_rows:,}")
    logger.info(f"Unique vehicles: {unique_vehicles:,}")
    logger.info(f"PASS: {pass_count:,} ({100*pass_count/total_rows:.1f}%)")
    logger.info(f"FAIL: {fail_count:,} ({100*fail_count/total_rows:.1f}%)")
    logger.info(f"Has prior tests: {has_prior:,} ({100*has_prior/total_rows:.1f}%)")
    logger.info(f"Has linked outcome: {has_linked:,} ({100*has_linked/total_rows:.1f}%)")
    logger.info(f"Has mileage: {has_mileage:,} ({100*has_mileage/total_rows:.1f}%)")
    logger.info(f"Elapsed time: {elapsed:.1f}s")

    logger.info("")
    logger.info("Coverage by year:")
    logger.info(f"{'Year':<6} {'Total':>12} {'n_prior>0':>10} {'linked':>10} {'mileage':>10}")
    logger.info("-" * 52)
    for row in year_stats:
        logger.info(f"{row[0]:<6} {row[1]:>12,} {row[2]:>9.1f}% {row[3]:>9.1f}% {row[4]:>9.1f}%")

    # Write manifest
    manifest = {
        "timestamp": datetime.now().isoformat(),
        "output_file": str(OUTPUT_FILE),
        "total_rows": total_rows,
        "unique_vehicles": unique_vehicles,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "has_prior_count": has_prior,
        "has_linked_count": has_linked,
        "has_mileage_count": has_mileage,
        "elapsed_seconds": elapsed,
        "year_coverage": [
            {"year": r[0], "total": r[1], "prior_pct": r[2], "linked_pct": r[3], "mileage_pct": r[4]}
            for r in year_stats
        ]
    }

    with open(MANIFEST_FILE, 'w') as f:
        json.dump(manifest, f, indent=2)

    logger.info(f"Manifest written to: {MANIFEST_FILE}")

    conn.close()
    return True


if __name__ == "__main__":
    build_canonical_spine()
