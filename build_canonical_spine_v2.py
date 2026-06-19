#!/usr/bin/env python3
"""
Canonical Spine Builder v2 (Memory-Efficient, 2016-2024)
=========================================================

Builds the canonical vehicle test spine for 9 years of MOT data on 8GB RAM.

Architecture:
- Persistent DuckDB database (not in-memory)
- Sequential year ingestion (no large UNION ALL)
- 20-pass chunked history computation (safe for 8GB RAM)
- Invariant validation with build-break

Stages:
  0: Preflight & validation (WAL cleanup, os.stat checks only)
  1: Ingest (CSV/gz -> DuckDB tables, per-year)
  2: Build unified index (union, dedupe, is_cycle_first boolean)
  3: Compute history (20-pass by vehicle_id % 20)
  4: Validate invariants (parquet-scan, fail-fast)
  5: Export final parquet (single merged file)

Usage:
    python build_canonical_spine_v2.py              # Run all stages
    python build_canonical_spine_v2.py --stage 3   # Resume from stage 3
    python build_canonical_spine_v2.py --verify    # Verify final output
    python build_canonical_spine_v2.py --clean     # Remove all intermediate files

Created: 2026-01-13
"""

import os
import sys
import json
import shutil
import logging
import argparse
import glob as glob_module
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any

import duckdb

# =============================================================================
# Configuration
# =============================================================================

WORK_DIR = Path.home() / "autosafe_work"
DB_PATH = WORK_DIR / "canonical_spine.duckdb"
STAGING_DIR = WORK_DIR / "spine_staging_v2"
OUTPUT_FILE = WORK_DIR / "canonical_spine.parquet"
MANIFESTS_DIR = WORK_DIR / "manifests"
DUCKDB_TEMP = WORK_DIR / "duckdb_temp"

NUM_PASSES = 20  # 20-pass chunked history computation (safe for 8GB RAM)

# iCloud root (local copy)
ICLOUD_ROOT = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/AutoSafe/MOT Test Results"

# Source configuration: year -> {pattern, delim, compression}
SOURCE_YEARS: Dict[int, Dict[str, Any]] = {
    2016: {
        'pattern': str(ICLOUD_ROOT / "2016/test_result_2016.txt.gz"),
        'delim': '|',
        'compression': 'gzip',
        'description': '2016 (gzipped)'
    },
    2017: {
        'pattern': str(ICLOUD_ROOT / "2017/test_result_318*.csv"),
        'delim': '|',
        'compression': None,
        'description': '2017 (12 CSV files)'
    },
    2018: {
        'pattern': str(ICLOUD_ROOT / "2018/dft_test_result-from-2018-*.csv"),
        'delim': ',',
        'compression': None,
        'description': '2018 (quarterly CSVs)'
    },
    2019: {
        'pattern': str(ICLOUD_ROOT / "2019/dft_test_result-from-2019-*.csv"),
        'delim': ',',
        'compression': None,
        'description': '2019 (quarterly CSVs)'
    },
    2020: {
        'pattern': str(ICLOUD_ROOT / "2020/dft_test_result-from-2020-*.csv"),
        'delim': ',',
        'compression': None,
        'description': '2020 (quarterly CSVs)'
    },
    2021: {
        'pattern': str(ICLOUD_ROOT / "2021/test_result_*.csv"),
        'delim': ',',
        'compression': None,
        'description': '2021 (12 CSV files)'
    },
    2022: {
        'pattern': str(ICLOUD_ROOT / "2022/test_result_2022.csv"),
        'delim': '|',
        'compression': None,
        'description': '2022 (single CSV)'
    },
    2023: {
        'pattern': str(ICLOUD_ROOT / "2023/test_result.csv"),
        'delim': '|',
        'compression': None,
        'description': '2023 (single CSV)'
    },
    2024: {
        'pattern': str(ICLOUD_ROOT / "test_result_2024*.csv"),
        'delim': ',',
        'compression': None,
        'description': '2024 (monthly CSVs)'
    },
}

# Valid test result codes
VALID_RESULT_CODES = {'P', 'PASS', 'F', 'FAIL', 'ABA', 'PRS', 'ABR'}

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# =============================================================================
# Utility Functions
# =============================================================================

def get_db_connection(db_path: Optional[Path] = None, memory_limit: str = '4GB') -> duckdb.DuckDBPyConnection:
    """Get DuckDB connection with 8GB-safe settings.

    CRITICAL: Config is passed to connect() call to prevent WAL replay OOM.
    Setting memory_limit AFTER connect() allows uncapped RAM during WAL recovery.
    """
    config = {
        'memory_limit': memory_limit,
        'threads': 2,
        'temp_directory': str(DUCKDB_TEMP),
        'preserve_insertion_order': 'false'
    }

    if db_path:
        conn = duckdb.connect(str(db_path), config=config)
    else:
        conn = duckdb.connect(config=config)

    return conn


def load_manifest(stage: str) -> Optional[Dict]:
    """Load checkpoint manifest for a stage."""
    manifest_file = MANIFESTS_DIR / f"spine_v2_stage{stage}_manifest.json"
    if manifest_file.exists():
        with open(manifest_file) as f:
            return json.load(f)
    return None


def save_manifest(stage: str, data: Dict):
    """Save checkpoint manifest for a stage."""
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest_file = MANIFESTS_DIR / f"spine_v2_stage{stage}_manifest.json"
    data['timestamp'] = datetime.now().isoformat()
    with open(manifest_file, 'w') as f:
        json.dump(data, f, indent=2)
    logger.info(f"  Saved manifest: {manifest_file.name}")


def check_disk_space(min_gb: float = 30.0) -> float:
    """Check available disk space."""
    free_gb = shutil.disk_usage(WORK_DIR).free / (1024**3)
    if free_gb < min_gb:
        logger.warning(f"Low disk space: {free_gb:.1f}GB free (recommended {min_gb}GB)")
    return free_gb


def find_files(pattern: str) -> List[str]:
    """Find files matching a glob pattern."""
    return glob_module.glob(pattern)


# =============================================================================
# Stage 0: Preflight & Validation
# =============================================================================

def stage0_preflight() -> Dict:
    """
    Preflight checks:
    - Clean up existing DB/WAL files (prevents OOM on WAL replay)
    - Verify source files exist
    - Check disk space
    - Create directories
    """
    logger.info("=" * 70)
    logger.info("STAGE 0: Preflight & Validation")
    logger.info("=" * 70)

    # CRITICAL: Clean up existing DB and WAL files BEFORE any DuckDB connection
    # WAL replay on connect() can spike RAM before memory limits apply
    wal_file = Path(str(DB_PATH) + ".wal")
    for f in [DB_PATH, wal_file]:
        if f.exists():
            f.unlink()
            logger.info(f"  Deleted existing {f.name} (prevents WAL replay OOM)")

    # Check existing manifest
    manifest = load_manifest("0")
    if manifest and manifest.get("status") == "complete":
        logger.info("  Stage 0 already complete, skipping...")
        return manifest

    # Create directories
    for d in [STAGING_DIR, DUCKDB_TEMP, MANIFESTS_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    # Check disk space
    free_gb = check_disk_space(min_gb=30.0)
    logger.info(f"  Disk space: {free_gb:.1f}GB free")

    # Verify source files
    valid_years = []
    missing_years = []
    total_size_mb = 0

    for year, config in SOURCE_YEARS.items():
        pattern = config['pattern']
        files = find_files(pattern)

        if files:
            size_mb = sum(Path(f).stat().st_size for f in files) / (1024**2)
            total_size_mb += size_mb
            valid_years.append({
                'year': year,
                'pattern': pattern,
                'files': len(files),
                'size_mb': round(size_mb, 1),
                'delim': config['delim'],
                'compression': config.get('compression'),
                'description': config['description']
            })
            logger.info(f"  {year}: {len(files)} file(s), {size_mb:.0f}MB - {config['description']}")
        else:
            missing_years.append({'year': year, 'pattern': pattern})
            logger.warning(f"  {year}: MISSING - {pattern}")

    if not valid_years:
        raise RuntimeError("No source files found!")

    result = {
        "status": "complete",
        "valid_years": valid_years,
        "missing_years": missing_years,
        "total_size_mb": round(total_size_mb, 1),
        "disk_free_gb": round(free_gb, 1)
    }
    save_manifest("0", result)

    logger.info(f"\n  Valid years: {len(valid_years)}/9")
    logger.info(f"  Total source size: {total_size_mb:.0f}MB")

    return result


# =============================================================================
# Stage 1: Ingest (Per-Year Sequential)
# =============================================================================

def stage1_ingest() -> Dict:
    """
    Ingest each year's data into persistent DuckDB tables.
    - Sequential processing (no large UNION ALL)
    - Duplicate detection and warning
    - Checkpointing per year
    """
    logger.info("\n" + "=" * 70)
    logger.info("STAGE 1: Ingest (Per-Year Sequential)")
    logger.info("=" * 70)

    # Check preflight complete
    preflight = load_manifest("0")
    if not preflight or preflight.get("status") != "complete":
        raise RuntimeError("Stage 0 not complete. Run preflight first.")

    # Check existing manifest
    manifest = load_manifest("1")
    if manifest and manifest.get("status") == "complete":
        logger.info("  Stage 1 already complete, skipping...")
        return manifest

    # Get already-processed years
    completed_years = set()
    if manifest:
        completed_years = set(manifest.get("completed_years", []))

    conn = get_db_connection(DB_PATH)

    year_stats = []
    total_rows = 0
    total_dupes = 0

    for year_info in preflight["valid_years"]:
        year = year_info['year']

        if year in completed_years:
            logger.info(f"  {year}: Already ingested, skipping...")
            continue

        logger.info(f"  {year}: Ingesting {year_info['description']}...")

        pattern = year_info['pattern']
        delim = year_info['delim']
        compression = year_info.get('compression')

        # Build read_csv options
        read_options = f"delim='{delim}', header=true, auto_detect=true, ignore_errors=true, parallel=false"
        if compression == 'gzip':
            read_options += ", compression='gzip'"

        try:
            # Create year table
            conn.execute(f"""
                CREATE OR REPLACE TABLE raw_{year} AS
                SELECT
                    CAST(test_id AS BIGINT) AS test_id,
                    CAST(vehicle_id AS BIGINT) AS vehicle_id,
                    TRY_CAST(test_date AS DATE) AS test_date,
                    UPPER(TRIM(CAST(test_result AS VARCHAR))) AS test_result,
                    TRY_CAST(test_mileage AS INTEGER) AS test_mileage,
                    {year} AS source_year
                FROM read_csv('{pattern}', {read_options})
                WHERE vehicle_id IS NOT NULL
                  AND test_date IS NOT NULL
                  AND TRY_CAST(vehicle_id AS BIGINT) > 0
            """)

            # Check for duplicates
            stats = conn.execute(f"""
                SELECT
                    COUNT(*) AS total_rows,
                    COUNT(DISTINCT test_id) AS unique_tests
                FROM raw_{year}
            """).fetchone()

            rows, unique = stats
            dupes = rows - unique

            if dupes > 0:
                logger.warning(f"    DUPLICATE ROWS: {dupes:,} duplicates found")
                # Dedupe in place
                conn.execute(f"""
                    CREATE OR REPLACE TABLE raw_{year} AS
                    SELECT DISTINCT ON (test_id) *
                    FROM raw_{year}
                    ORDER BY test_id, test_date
                """)
                rows = unique
                total_dupes += dupes

            year_stats.append({
                'year': year,
                'rows': rows,
                'duplicates_removed': dupes
            })
            total_rows += rows
            completed_years.add(year)

            logger.info(f"    Ingested: {rows:,} rows")

            # Checkpoint
            save_manifest("1", {
                "status": "in_progress",
                "completed_years": list(completed_years),
                "year_stats": year_stats,
                "total_rows": total_rows,
                "total_duplicates_removed": total_dupes
            })

        except Exception as e:
            logger.error(f"    Error ingesting {year}: {e}")
            conn.close()
            raise

    conn.close()

    result = {
        "status": "complete",
        "completed_years": list(completed_years),
        "year_stats": year_stats,
        "total_rows": total_rows,
        "total_duplicates_removed": total_dupes
    }
    save_manifest("1", result)

    logger.info(f"\n  Total rows ingested: {total_rows:,}")
    if total_dupes > 0:
        logger.info(f"  Total duplicates removed: {total_dupes:,}")

    return result


# =============================================================================
# Stage 2: Build Unified Index (Memory-Safe)
# =============================================================================

def stage2_build_index() -> Dict:
    """
    Build unified test index (no window functions - those are in Stage 3).
    - Union all year tables
    - Add canonical outcome column
    - Add pass_bucket for partitioning

    NOTE: is_cycle_first is computed in Stage 3 to avoid OOM on 278M row window.
    """
    logger.info("\n" + "=" * 70)
    logger.info("STAGE 2: Build Unified Index")
    logger.info("=" * 70)

    # Check stage 1 complete
    stage1 = load_manifest("1")
    if not stage1 or stage1.get("status") != "complete":
        raise RuntimeError("Stage 1 not complete. Run it first.")

    # Check existing manifest
    manifest = load_manifest("2")
    if manifest and manifest.get("status") == "complete":
        logger.info("  Stage 2 already complete, skipping...")
        return manifest

    conn = get_db_connection(DB_PATH)

    # Get list of year tables
    years = stage1.get("completed_years", [])

    logger.info("  Creating unified test table (all_tests)...")

    # Build UNION ALL query
    union_parts = [f"SELECT * FROM raw_{year}" for year in years]
    union_sql = " UNION ALL ".join(union_parts)

    conn.execute(f"""
        CREATE OR REPLACE TABLE all_tests AS
        SELECT
            test_id,
            vehicle_id,
            test_date,
            test_result,
            test_mileage,
            source_year,
            CASE
                WHEN test_result IN ('P', 'PASS', 'PRS') THEN 'PASS'
                WHEN test_result IN ('F', 'FAIL') THEN 'FAIL'
                ELSE 'OTHER'
            END AS outcome,
            CAST(vehicle_id % {NUM_PASSES} AS INTEGER) AS pass_bucket
        FROM ({union_sql})
    """)

    total_count = conn.execute("SELECT COUNT(*) FROM all_tests").fetchone()[0]
    logger.info(f"    Unified table: {total_count:,} rows")
    logger.info(f"    Partitioned into {NUM_PASSES} buckets for Stage 3")

    # Drop raw year tables to save space
    logger.info("  Dropping raw year tables...")
    for year in years:
        conn.execute(f"DROP TABLE IF EXISTS raw_{year}")

    conn.close()

    result = {
        "status": "complete",
        "total_tests": total_count
    }
    save_manifest("2", result)

    return result


# =============================================================================
# Stage 3: Compute History (20-Pass Chunked)
# =============================================================================

def stage3_compute_history() -> Dict:
    """
    Compute is_cycle_first and history features using 20-pass chunked processing.
    Each pass handles 5% of vehicles (vehicle_id % 20 = pass_num).
    Safe for 8GB RAM (~14M rows per pass).

    Keeps ALL rows with is_cycle_first as boolean column.
    """
    logger.info("\n" + "=" * 70)
    logger.info("STAGE 3: Compute History (20-Pass Chunked)")
    logger.info("=" * 70)

    # Check stage 2 complete
    stage2 = load_manifest("2")
    if not stage2 or stage2.get("status") != "complete":
        raise RuntimeError("Stage 2 not complete. Run it first.")

    # Check existing manifest
    manifest = load_manifest("3")
    if manifest and manifest.get("status") == "complete":
        logger.info("  Stage 3 already complete, skipping...")
        return manifest

    # Get completed passes
    completed_passes = set()
    if manifest:
        completed_passes = set(manifest.get("completed_passes", []))

    # Clear staging if starting fresh
    if not completed_passes and STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    conn = get_db_connection(DB_PATH)

    total_rows_processed = 0

    for pass_num in range(NUM_PASSES):
        if pass_num in completed_passes:
            logger.info(f"  Pass {pass_num}/{NUM_PASSES}: Already complete, skipping...")
            continue

        logger.info(f"  Pass {pass_num}/{NUM_PASSES}: Processing vehicles where vehicle_id % {NUM_PASSES} = {pass_num}...")

        output_file = STAGING_DIR / f"pass_{pass_num:02d}.parquet"

        try:
            conn.execute(f"""
                COPY (
                    WITH base AS (
                        SELECT * FROM all_tests
                        WHERE pass_bucket = {pass_num}
                    ),
                    with_cycle_flag AS (
                        -- First compute is_cycle_first for ALL rows
                        SELECT
                            b.*,
                            CASE
                                -- First test for vehicle
                                WHEN LAG(b.vehicle_id) OVER w IS NULL THEN TRUE
                                -- Previous test was PASS and gap >= 2 days
                                WHEN LAG(b.outcome) OVER w = 'PASS'
                                     AND DATEDIFF('day', LAG(b.test_date) OVER w, b.test_date) >= 2 THEN TRUE
                                -- COVID adjustment: 180 days for Mar-Aug 2020
                                WHEN b.test_date BETWEEN '2020-03-30' AND '2020-08-31'
                                     AND DATEDIFF('day', LAG(b.test_date) OVER w, b.test_date) > 180 THEN TRUE
                                -- Standard gap threshold: 120 days
                                WHEN DATEDIFF('day', LAG(b.test_date) OVER w, b.test_date) > 120 THEN TRUE
                                ELSE FALSE
                            END AS is_cycle_first,
                            LAG(b.test_date) OVER w AS prev_test_date,
                            LAG(b.test_id) OVER w AS prev_test_id,
                            LAG(b.outcome) OVER w AS prev_outcome,
                            LAG(b.test_mileage) OVER w AS prev_mileage
                        FROM base b
                        WINDOW w AS (PARTITION BY b.vehicle_id ORDER BY b.test_date, b.test_id)
                    ),
                    with_history AS (
                        -- Now compute history features (only meaningful for cycle-first tests)
                        SELECT
                            c.test_id,
                            c.vehicle_id,
                            c.test_date,
                            c.source_year,
                            c.test_result,
                            c.test_mileage,
                            c.outcome,
                            c.is_cycle_first,

                            -- n_prior_tests: count of prior cycle-first tests for this vehicle
                            CAST(COALESCE(SUM(CASE WHEN c.is_cycle_first THEN 1 ELSE 0 END)
                                OVER (PARTITION BY c.vehicle_id ORDER BY c.test_date, c.test_id
                                      ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS INTEGER) AS n_prior_tests,

                            -- Prior test linkage (immediate previous test, regardless of cycle)
                            c.prev_test_id,
                            c.prev_test_date,
                            c.prev_outcome,
                            c.prev_mileage,

                            -- Temporal features
                            DATEDIFF('day', c.prev_test_date, c.test_date) AS days_since_last_test,

                            -- Cumulative fail count (cycle-first fails only)
                            CAST(COALESCE(SUM(CASE WHEN c.is_cycle_first AND c.outcome = 'FAIL' THEN 1 ELSE 0 END)
                                OVER (PARTITION BY c.vehicle_id ORDER BY c.test_date, c.test_id
                                      ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS INTEGER) AS n_prior_fails

                        FROM with_cycle_flag c
                    )
                    SELECT
                        test_id,
                        vehicle_id,
                        test_date,
                        source_year,
                        test_result,
                        test_mileage,
                        outcome,
                        is_cycle_first,
                        n_prior_tests,
                        prev_test_id AS prev_cycle_test_id,
                        prev_test_date AS prev_cycle_date,

                        -- prev_cycle_outcome_band
                        CASE
                            WHEN prev_test_date IS NULL THEN 'NONE'
                            WHEN prev_outcome = 'PASS' THEN 'PASS'
                            WHEN prev_outcome = 'FAIL' THEN 'FAIL'
                            ELSE 'NONE'
                        END AS prev_cycle_outcome_band,

                        days_since_last_test,
                        n_prior_fails,
                        prev_mileage AS prev_cycle_mileage,

                        -- Miles since last test
                        test_mileage - prev_mileage AS miles_since_last_test,

                        -- Annualized mileage (with validation)
                        CASE
                            WHEN prev_mileage IS NULL THEN NULL
                            WHEN test_mileage IS NULL THEN NULL
                            WHEN test_mileage < prev_mileage THEN NULL
                            WHEN days_since_last_test IS NULL OR days_since_last_test <= 0 THEN NULL
                            WHEN (test_mileage - prev_mileage) * 1.0 / days_since_last_test > 500 THEN NULL
                            ELSE (test_mileage - prev_mileage) * 365.25 / days_since_last_test
                        END AS annualized_mileage

                    FROM with_history
                )
                TO '{output_file}'
                (FORMAT PARQUET, COMPRESSION ZSTD)
            """)

            # Count rows processed
            rows = conn.execute(f"""
                SELECT COUNT(*) FROM read_parquet('{output_file}')
            """).fetchone()[0]
            total_rows_processed += rows
            completed_passes.add(pass_num)

            logger.info(f"    Processed: {rows:,} rows")

            # Checkpoint
            save_manifest("3", {
                "status": "in_progress",
                "completed_passes": list(completed_passes),
                "total_rows_processed": total_rows_processed
            })

        except Exception as e:
            logger.error(f"    Error on pass {pass_num}: {e}")
            conn.close()
            raise

    conn.close()

    result = {
        "status": "complete",
        "completed_passes": list(completed_passes),
        "total_rows_processed": total_rows_processed
    }
    save_manifest("3", result)

    logger.info(f"\n  Total rows with history: {total_rows_processed:,}")

    return result


# =============================================================================
# Stage 4: Invariant Validation (Parquet-Scan)
# =============================================================================

def stage4_validate() -> Dict:
    """
    Validate invariants via parquet scan - ABORT if any violations found.

    Uses read_parquet() glob to scan files directly without loading into
    DuckDB tables. Memory-safe for 8GB RAM.

    Invariants:
    1. prev_cycle_outcome_band in (PASS,FAIL) requires n_prior_tests >= 1
    2. n_prior_tests >= 1 requires prev_cycle_test_id IS NOT NULL
    3. Temporal monotonicity (test_date increasing within vehicle)
    """
    logger.info("\n" + "=" * 70)
    logger.info("STAGE 4: Invariant Validation")
    logger.info("=" * 70)

    # Check stage 3 complete
    stage3 = load_manifest("3")
    if not stage3 or stage3.get("status") != "complete":
        raise RuntimeError("Stage 3 not complete. Run it first.")

    # Check existing manifest
    manifest = load_manifest("4")
    if manifest and manifest.get("status") == "complete":
        logger.info("  Stage 4 already complete, skipping...")
        return manifest

    conn = get_db_connection()  # In-memory for validation queries

    staging_pattern = str(STAGING_DIR / "*.parquet")
    violations = []

    # Get counts for reporting
    total_rows = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{staging_pattern}')").fetchone()[0]
    cycle_first_rows = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{staging_pattern}') WHERE is_cycle_first").fetchone()[0]
    logger.info(f"    Total rows: {total_rows:,}")
    logger.info(f"    Cycle-first rows: {cycle_first_rows:,} ({100*cycle_first_rows/total_rows:.1f}%)")

    # Invariant 1: prev_cycle_outcome_band consistency (cycle-first tests only)
    logger.info("  Checking Invariant 1: prev_cycle_outcome_band consistency (cycle-first only)...")
    count1 = conn.execute(f"""
        SELECT COUNT(*)
        FROM read_parquet('{staging_pattern}')
        WHERE is_cycle_first = TRUE
          AND prev_cycle_outcome_band IN ('PASS', 'FAIL')
          AND n_prior_tests = 0
    """).fetchone()[0]

    if count1 > 0:
        violations.append(f"INVARIANT 1 VIOLATION: {count1:,} cycle-first rows have prev_cycle_outcome_band=PASS/FAIL but n_prior_tests=0")
        logger.error(f"    FAILED: {count1:,} violations")
    else:
        logger.info("    PASSED")

    # Invariant 2: n_prior_tests linkage (cycle-first tests only)
    logger.info("  Checking Invariant 2: n_prior_tests linkage (cycle-first only)...")
    count2 = conn.execute(f"""
        SELECT COUNT(*)
        FROM read_parquet('{staging_pattern}')
        WHERE is_cycle_first = TRUE
          AND n_prior_tests >= 1
          AND prev_cycle_test_id IS NULL
    """).fetchone()[0]

    if count2 > 0:
        violations.append(f"INVARIANT 2 VIOLATION: {count2:,} cycle-first rows have n_prior_tests>=1 but prev_cycle_test_id IS NULL")
        logger.error(f"    FAILED: {count2:,} violations")
    else:
        logger.info("    PASSED")

    # Invariant 3: Temporal monotonicity (all rows)
    # Use prev_cycle_date already computed in Stage 3 (no window function needed)
    logger.info("  Checking Invariant 3: Temporal monotonicity (all rows)...")
    count3 = conn.execute(f"""
        SELECT COUNT(*)
        FROM read_parquet('{staging_pattern}')
        WHERE prev_cycle_date IS NOT NULL AND test_date < prev_cycle_date
    """).fetchone()[0]

    if count3 > 0:
        violations.append(f"INVARIANT 3 VIOLATION: {count3:,} temporal monotonicity violations")
        logger.error(f"    FAILED: {count3:,} violations")
    else:
        logger.info("    PASSED")

    conn.close()

    if violations:
        logger.error("\n  BUILD FAILED - Invariant violations detected:")
        for v in violations:
            logger.error(f"    - {v}")

        result = {
            "status": "failed",
            "violations": violations,
            "invariant1_violations": count1,
            "invariant2_violations": count2,
            "invariant3_violations": count3
        }
        save_manifest("4", result)

        raise RuntimeError(f"Invariant validation failed with {len(violations)} violation(s). See log for details.")

    result = {
        "status": "complete",
        "violations": [],
        "invariant1_violations": 0,
        "invariant2_violations": 0,
        "invariant3_violations": 0
    }
    save_manifest("4", result)

    logger.info("\n  All invariants passed!")

    return result


# =============================================================================
# Stage 5: Export Final Parquet (Single Merged File)
# =============================================================================

def stage5_export() -> Dict:
    """
    Merge all 20 pass files into single canonical_spine.parquet.

    Uses read_parquet() glob to stream-merge without loading all data.
    Output is ZSTD compressed with 100K row groups.
    """
    logger.info("\n" + "=" * 70)
    logger.info("STAGE 5: Export Final Parquet")
    logger.info("=" * 70)

    # Check stage 4 complete
    stage4 = load_manifest("4")
    if not stage4 or stage4.get("status") != "complete":
        raise RuntimeError("Stage 4 not complete. Run it first.")

    # Check existing manifest
    manifest = load_manifest("5")
    if manifest and manifest.get("status") == "complete":
        logger.info("  Stage 5 already complete, skipping...")
        return manifest

    conn = get_db_connection()
    staging_pattern = str(STAGING_DIR / "*.parquet")

    logger.info(f"  Merging pass files to {OUTPUT_FILE}...")

    conn.execute(f"""
        COPY (
            SELECT * FROM read_parquet('{staging_pattern}')
        )
        TO '{OUTPUT_FILE}'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
    """)

    # Get final statistics
    logger.info("  Computing final statistics...")
    stats = conn.execute(f"""
        SELECT
            COUNT(*) AS total_rows,
            COUNT(DISTINCT vehicle_id) AS unique_vehicles,
            SUM(CASE WHEN is_cycle_first THEN 1 ELSE 0 END) AS cycle_first_count,
            SUM(CASE WHEN outcome = 'PASS' THEN 1 ELSE 0 END) AS pass_count,
            SUM(CASE WHEN outcome = 'FAIL' THEN 1 ELSE 0 END) AS fail_count,
            SUM(CASE WHEN n_prior_tests > 0 THEN 1 ELSE 0 END) AS has_prior,
            SUM(CASE WHEN prev_cycle_outcome_band IN ('PASS', 'FAIL') THEN 1 ELSE 0 END) AS has_linked_outcome,
            SUM(CASE WHEN annualized_mileage IS NOT NULL THEN 1 ELSE 0 END) AS has_mileage
        FROM read_parquet('{OUTPUT_FILE}')
    """).fetchone()

    total_rows, unique_vehicles, cycle_first_count, pass_count, fail_count, has_prior, has_linked, has_mileage = stats
    retest_count = total_rows - cycle_first_count

    # Year distribution
    year_dist = conn.execute(f"""
        SELECT
            source_year,
            COUNT(*) AS total,
            ROUND(100.0 * SUM(CASE WHEN n_prior_tests > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS prior_pct,
            ROUND(100.0 * SUM(CASE WHEN prev_cycle_outcome_band IN ('PASS','FAIL') THEN 1 ELSE 0 END) / COUNT(*), 1) AS linked_pct,
            ROUND(100.0 * SUM(CASE WHEN annualized_mileage IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 1) AS mileage_pct
        FROM read_parquet('{OUTPUT_FILE}')
        GROUP BY source_year
        ORDER BY source_year
    """).fetchall()

    conn.close()

    file_size_mb = OUTPUT_FILE.stat().st_size / (1024**2)

    logger.info("\n" + "=" * 70)
    logger.info("CANONICAL SPINE BUILD COMPLETE")
    logger.info("=" * 70)
    logger.info(f"  Output: {OUTPUT_FILE}")
    logger.info(f"  File size: {file_size_mb:.1f}MB")
    logger.info(f"  Total rows: {total_rows:,}")
    logger.info(f"  Unique vehicles: {unique_vehicles:,}")
    logger.info(f"  Cycle-first tests: {cycle_first_count:,} ({100*cycle_first_count/total_rows:.1f}%)")
    logger.info(f"  Retests: {retest_count:,} ({100*retest_count/total_rows:.1f}%)")
    logger.info(f"  PASS: {pass_count:,} ({100*pass_count/total_rows:.1f}%)")
    logger.info(f"  FAIL: {fail_count:,} ({100*fail_count/total_rows:.1f}%)")
    logger.info(f"  Has prior tests: {has_prior:,} ({100*has_prior/total_rows:.1f}%)")
    logger.info(f"  Has linked outcome: {has_linked:,} ({100*has_linked/total_rows:.1f}%)")
    logger.info(f"  Has mileage: {has_mileage:,} ({100*has_mileage/total_rows:.1f}%)")

    logger.info("\n  Coverage by year:")
    logger.info(f"  {'Year':<6} {'Total':>12} {'n_prior>0':>10} {'linked':>10} {'mileage':>10}")
    logger.info("  " + "-" * 52)
    for row in year_dist:
        logger.info(f"  {row[0]:<6} {row[1]:>12,} {row[2]:>9.1f}% {row[3]:>9.1f}% {row[4]:>9.1f}%")

    # Cleanup staging
    logger.info("\n  Cleaning up staging files...")
    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)

    # Cleanup database (optional - keep for debugging)
    # if DB_PATH.exists():
    #     DB_PATH.unlink()

    result = {
        "status": "complete",
        "output_file": str(OUTPUT_FILE),
        "file_size_mb": round(file_size_mb, 1),
        "total_rows": total_rows,
        "unique_vehicles": unique_vehicles,
        "cycle_first_count": cycle_first_count,
        "retest_count": retest_count,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "has_prior_count": has_prior,
        "has_linked_count": has_linked,
        "has_mileage_count": has_mileage,
        "year_distribution": [
            {"year": r[0], "total": r[1], "prior_pct": r[2], "linked_pct": r[3], "mileage_pct": r[4]}
            for r in year_dist
        ]
    }
    save_manifest("5", result)

    return result


# =============================================================================
# Verify Output
# =============================================================================

def verify_output():
    """Verify the final output file."""
    logger.info("=" * 70)
    logger.info("VERIFYING OUTPUT")
    logger.info("=" * 70)

    if not OUTPUT_FILE.exists():
        logger.error(f"Output file not found: {OUTPUT_FILE}")
        return False

    conn = get_db_connection()

    stats = conn.execute(f"""
        SELECT
            COUNT(*) AS total,
            COUNT(DISTINCT vehicle_id) AS vehicles,
            MIN(source_year) AS min_year,
            MAX(source_year) AS max_year,
            SUM(CASE WHEN n_prior_tests > 0 THEN 1 ELSE 0 END) AS has_prior,
            SUM(CASE WHEN prev_cycle_outcome_band IN ('PASS','FAIL') AND n_prior_tests = 0 THEN 1 ELSE 0 END) AS inv1_violations,
            SUM(CASE WHEN n_prior_tests >= 1 AND prev_cycle_test_id IS NULL THEN 1 ELSE 0 END) AS inv2_violations
        FROM read_parquet('{OUTPUT_FILE}')
    """).fetchone()

    conn.close()

    total, vehicles, min_year, max_year, has_prior, inv1, inv2 = stats

    logger.info(f"  Total rows: {total:,}")
    logger.info(f"  Unique vehicles: {vehicles:,}")
    logger.info(f"  Year range: {min_year}-{max_year}")
    logger.info(f"  Has prior history: {has_prior:,} ({100*has_prior/total:.1f}%)")

    if inv1 > 0 or inv2 > 0:
        logger.error(f"  Invariant 1 violations: {inv1:,}")
        logger.error(f"  Invariant 2 violations: {inv2:,}")
        return False
    else:
        logger.info("  Invariant checks: PASSED")
        return True


# =============================================================================
# Cleanup
# =============================================================================

def cleanup_all():
    """Remove all intermediate files and database."""
    logger.info("=" * 70)
    logger.info("CLEANUP: Removing intermediate files")
    logger.info("=" * 70)

    items_removed = []

    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)
        items_removed.append(f"Staging directory: {STAGING_DIR}")

    if DB_PATH.exists():
        DB_PATH.unlink()
        items_removed.append(f"Database: {DB_PATH}")

    # Remove manifests
    for manifest in MANIFESTS_DIR.glob("spine_v2_*.json"):
        manifest.unlink()
        items_removed.append(f"Manifest: {manifest.name}")

    for item in items_removed:
        logger.info(f"  Removed: {item}")

    if not items_removed:
        logger.info("  Nothing to clean up")


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Canonical Spine Builder v2 (Memory-Efficient)")
    parser.add_argument("--stage", type=int, help="Resume from specific stage (0-5)")
    parser.add_argument("--verify", action="store_true", help="Verify final output only")
    parser.add_argument("--clean", action="store_true", help="Remove all intermediate files")
    args = parser.parse_args()

    if args.clean:
        cleanup_all()
        return

    if args.verify:
        success = verify_output()
        sys.exit(0 if success else 1)

    start_time = datetime.now()
    start_stage = args.stage if args.stage is not None else 0

    try:
        if start_stage <= 0:
            stage0_preflight()

        if start_stage <= 1:
            stage1_ingest()

        if start_stage <= 2:
            stage2_build_index()

        if start_stage <= 3:
            stage3_compute_history()

        if start_stage <= 4:
            stage4_validate()

        if start_stage <= 5:
            stage5_export()

        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"\n{'=' * 70}")
        logger.info(f"PIPELINE COMPLETE in {elapsed/60:.1f} minutes")
        logger.info(f"Output: {OUTPUT_FILE}")
        logger.info(f"{'=' * 70}")

    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        raise


if __name__ == "__main__":
    main()
