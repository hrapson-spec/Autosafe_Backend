#!/usr/bin/env python3
"""
Partitioned Cycle Index Builder (8GB-Safe)

Memory-efficient pipeline that:
- Partitions by vehicle_id % 256 (all records for a vehicle co-located)
- Processes one partition at a time with durable checkpoints
- Never requires global sort/join across full dataset
- Works reliably on 8GB RAM

Stages:
  0: Preflight & validation
  1: CSV → typed Parquet (bucketed)
  2: Same-day collapse per bucket
  3: Cycle boundary computation per bucket
  4: Extract cycle-first tests

Usage:
    python build_cycle_index_partitioned.py              # Run all stages
    python build_cycle_index_partitioned.py --stage 2    # Resume from stage 2
    python build_cycle_index_partitioned.py --verify     # Verify final output
"""

import os
import sys
import json
import shutil
import logging
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Tuple

import duckdb

# Configuration
NUM_BUCKETS = 256
WORK_DIR = Path.home() / "autosafe_work"
STAGE1_DIR = WORK_DIR / "stage1_parquet"
STAGE2_DIR = WORK_DIR / "stage2_collapsed"
STAGE3_DIR = WORK_DIR / "stage3_with_cycles"
DUCKDB_TEMP = WORK_DIR / "duckdb_temp"
MANIFESTS_DIR = WORK_DIR / "manifests"
OUTPUT_FILE = WORK_DIR / "cycle_first_tests.parquet"

# iCloud source data (already synced locally)
ICLOUD_ROOT = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/AutoSafe/MOT Test Results"

# Source file configuration: (path_pattern, delimiter, description)
SOURCE_FILES = [
    # 2024 data (top level)
    (ICLOUD_ROOT / "test_result_202407.csv", ",", "2024-Q3 Jul"),
    (ICLOUD_ROOT / "test_result_202408.csv", ",", "2024-Q3 Aug"),
    (ICLOUD_ROOT / "test_result_202409.csv", ",", "2024-Q3 Sep"),
    # 2023
    (ICLOUD_ROOT / "2023/test_result.csv", "|", "2023"),
    # 2022
    (ICLOUD_ROOT / "2022/test_result_2022.csv", "|", "2022"),
]

# Add 2021 files dynamically
for i in range(12):
    f = ICLOUD_ROOT / f"2021/test_result_20220531131730_32{355 + i}.csv"
    SOURCE_FILES.append((f, ",", f"2021-part{i}"))

# Add 2020 files
for period in ["2020-01-01_00-00-00-to-2020-04-01_00-00-00",
               "2020-04-01_00-00-00-to-2020-07-01_00-00-00",
               "2020-07-01_00-00-00-to-2020-10-01_00-00-00",
               "2020-10-01_00-00-00-to-2021-01-01_00-00-00"]:
    f = ICLOUD_ROOT / f"2020/dft_test_result-from-{period}.csv"
    SOURCE_FILES.append((f, ",", f"2020-{period[:7]}"))

# Add 2019 files
for period in ["2019-01-01_00-00-01-to-2019-04-01_00-00-01",
               "2019-04-01_00-00-01-to-2019-07-01_00-00-01",
               "2019-07-01_00-00-01-to-2019-10-01_00-00-01",
               "2019-10-01_00-00-01-to-2020-01-01_00-00-01"]:
    f = ICLOUD_ROOT / f"2019/dft_test_result-from-{period}.csv"
    SOURCE_FILES.append((f, ",", f"2019-{period[:7]}"))

# Known valid test_result codes
VALID_RESULT_CODES = {'P', 'PASS', 'F', 'FAIL', 'ABA', 'PRS', 'ABR'}

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_duckdb_connection() -> duckdb.DuckDBPyConnection:
    """Get DuckDB connection with 8GB-safe settings."""
    conn = duckdb.connect()
    conn.execute("SET memory_limit='5GB'")
    conn.execute("SET threads=2")
    conn.execute(f"SET temp_directory='{DUCKDB_TEMP}'")
    conn.execute("SET preserve_insertion_order=false")
    return conn


def load_manifest(stage: str) -> Optional[Dict]:
    """Load checkpoint manifest for a stage."""
    manifest_file = MANIFESTS_DIR / f"stage{stage}_manifest.json"
    if manifest_file.exists():
        with open(manifest_file) as f:
            return json.load(f)
    return None


def save_manifest(stage: str, data: Dict):
    """Save checkpoint manifest for a stage."""
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest_file = MANIFESTS_DIR / f"stage{stage}_manifest.json"
    data['timestamp'] = datetime.now().isoformat()
    with open(manifest_file, 'w') as f:
        json.dump(data, f, indent=2)
    logger.info(f"  Saved manifest: {manifest_file.name}")


def check_disk_space(min_gb: float = 20.0) -> float:
    """Check available disk space."""
    free_gb = shutil.disk_usage(WORK_DIR).free / (1024**3)
    if free_gb < min_gb:
        raise RuntimeError(f"Insufficient disk space: {free_gb:.1f}GB free (need {min_gb}GB)")
    return free_gb


# =============================================================================
# STAGE 0: Preflight & Validation
# =============================================================================

def stage0_preflight() -> Dict:
    """
    Preflight checks:
    - Verify source files exist
    - Check disk space
    - Audit test_result codes for unknown values
    - Verify no duplicate " 2.csv" files
    """
    logger.info("="*70)
    logger.info("STAGE 0: Preflight & Validation")
    logger.info("="*70)

    # Check existing manifest
    manifest = load_manifest("0")
    if manifest and manifest.get("status") == "complete":
        logger.info("  Stage 0 already complete, skipping...")
        return manifest

    # Create directories
    for d in [STAGE1_DIR, STAGE2_DIR, STAGE3_DIR, DUCKDB_TEMP, MANIFESTS_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    # Check disk space
    free_gb = check_disk_space(min_gb=30.0)
    logger.info(f"  Disk space: {free_gb:.1f}GB free")

    # Verify source files exist
    valid_sources = []
    missing_sources = []

    for filepath, delimiter, desc in SOURCE_FILES:
        if filepath.exists():
            size_mb = filepath.stat().st_size / (1024**2)
            valid_sources.append({
                "path": str(filepath),
                "delimiter": delimiter,
                "description": desc,
                "size_mb": round(size_mb, 1)
            })
            logger.info(f"  Found: {desc} ({size_mb:.0f}MB)")
        else:
            # Check for " 2" duplicate
            if " 2.csv" not in str(filepath):
                missing_sources.append(str(filepath))
                logger.warning(f"  Missing: {desc} - {filepath}")

    if not valid_sources:
        raise RuntimeError("No source files found!")

    logger.info(f"\n  Valid sources: {len(valid_sources)}")
    logger.info(f"  Missing sources: {len(missing_sources)}")

    # Audit test_result codes
    logger.info("\n  Auditing test_result codes...")
    conn = get_duckdb_connection()
    unknown_codes = set()

    for source in valid_sources[:5]:  # Sample first 5 files
        try:
            codes = conn.execute(f"""
                SELECT DISTINCT UPPER(CAST(test_result AS VARCHAR)) as code
                FROM read_csv('{source["path"]}',
                              delim='{source["delimiter"]}',
                              header=true,
                              ignore_errors=true,
                              sample_size=100000)
            """).fetchall()

            for (code,) in codes:
                if code and code not in VALID_RESULT_CODES:
                    unknown_codes.add(code)
        except Exception as e:
            logger.warning(f"    Could not audit {source['description']}: {e}")

    conn.close()

    if unknown_codes:
        logger.warning(f"  Unknown test_result codes found: {unknown_codes}")
        logger.warning("  These will be mapped to 0 (unknown)")
    else:
        logger.info("  All test_result codes valid")

    # Save manifest
    result = {
        "status": "complete",
        "valid_sources": valid_sources,
        "missing_sources": missing_sources,
        "unknown_codes": list(unknown_codes),
        "disk_free_gb": round(free_gb, 1)
    }
    save_manifest("0", result)

    return result


# =============================================================================
# STAGE 1: CSV → Typed Parquet (Bucketed)
# =============================================================================

def stage1_ingest() -> Dict:
    """
    Convert CSV files to typed Parquet, partitioned by vehicle_id % 256.
    Only columns needed for cycle computation are kept.

    Each source file is written to its own subdirectory under stage1, then
    all are unioned in Stage 2 to avoid overwrite issues.
    """
    logger.info("\n" + "="*70)
    logger.info("STAGE 1: CSV → Typed Parquet (Bucketed)")
    logger.info("="*70)

    # Load preflight manifest
    preflight = load_manifest("0")
    if not preflight or preflight.get("status") != "complete":
        raise RuntimeError("Stage 0 not complete. Run preflight first.")

    # Check existing stage1 manifest
    manifest = load_manifest("1")
    if manifest and manifest.get("status") == "complete":
        logger.info("  Stage 1 already complete, skipping...")
        return manifest

    # Get list of already-processed files
    processed_files = set()
    if manifest:
        processed_files = set(manifest.get("files_processed", []))

    # Clear stage1 directory if starting fresh
    if not processed_files and STAGE1_DIR.exists():
        shutil.rmtree(STAGE1_DIR)
        STAGE1_DIR.mkdir()

    conn = get_duckdb_connection()
    total_rows = 0
    files_processed = list(processed_files)
    errors = []

    for idx, source in enumerate(preflight["valid_sources"]):
        filepath = source["path"]

        if filepath in processed_files:
            logger.info(f"  Skipping (already done): {source['description']}")
            continue

        logger.info(f"  Processing: {source['description']}...")

        # Each source file goes to its own subdirectory to avoid overwrites
        source_subdir = STAGE1_DIR / f"source_{idx:03d}"
        source_subdir.mkdir(exist_ok=True)

        try:
            # Read CSV and convert to bucketed parquet in source-specific directory
            result = conn.execute(f"""
                COPY (
                    SELECT
                        CAST(vehicle_id AS BIGINT) AS vehicle_id,
                        CAST(test_id AS BIGINT) AS test_id,
                        TRY_CAST(test_date AS DATE) AS test_date,
                        CASE UPPER(CAST(test_result AS VARCHAR))
                            WHEN 'P' THEN 1 WHEN 'PASS' THEN 1
                            WHEN 'ABA' THEN 2 WHEN 'ABR' THEN 2
                            WHEN 'PRS' THEN 3
                            WHEN 'F' THEN 4 WHEN 'FAIL' THEN 4
                            ELSE 0
                        END AS test_result,
                        CAST(vehicle_id % {NUM_BUCKETS} AS INTEGER) AS bucket
                    FROM read_csv('{filepath}',
                                  delim='{source["delimiter"]}',
                                  header=true,
                                  ignore_errors=true,
                                  auto_detect=true)
                    WHERE vehicle_id IS NOT NULL
                      AND test_date IS NOT NULL
                      AND TRY_CAST(vehicle_id AS BIGINT) > 0
                ) TO '{source_subdir}'
                (FORMAT PARQUET, PARTITION_BY (bucket), COMPRESSION ZSTD)
            """)

            # Count rows added (estimate from file size)
            rows_estimate = int(source["size_mb"] * 40000)  # ~40K rows per MB typical
            total_rows += rows_estimate
            files_processed.append(filepath)

            logger.info(f"    Done: ~{rows_estimate:,} rows (estimated)")

            # Checkpoint after each file
            save_manifest("1", {
                "status": "in_progress",
                "files_processed": files_processed,
                "total_rows_estimate": total_rows,
                "errors": errors
            })

        except Exception as e:
            errors.append({"file": filepath, "error": str(e)})
            logger.error(f"    Error: {e}")

    conn.close()

    # Get actual row count (now reads from source_*/bucket=*/files)
    logger.info("\n  Getting actual row count...")
    conn = get_duckdb_connection()
    actual_rows = conn.execute(f"""
        SELECT COUNT(*) FROM read_parquet('{STAGE1_DIR}/*/*/*.parquet')
    """).fetchone()[0]
    conn.close()

    logger.info(f"  Total rows in Stage 1: {actual_rows:,}")

    # Verify test_id uniqueness
    logger.info("  Verifying test_id uniqueness...")
    conn = get_duckdb_connection()
    dups = conn.execute(f"""
        SELECT test_id, COUNT(*) as cnt
        FROM read_parquet('{STAGE1_DIR}/*/*/*.parquet')
        GROUP BY test_id
        HAVING COUNT(*) > 1
        LIMIT 10
    """).fetchall()
    conn.close()

    if dups:
        logger.warning(f"  Found {len(dups)} duplicate test_ids (showing first 10)")
        for test_id, cnt in dups[:5]:
            logger.warning(f"    test_id={test_id}: {cnt} occurrences")
    else:
        logger.info("  No duplicate test_ids found")

    result = {
        "status": "complete",
        "files_processed": files_processed,
        "total_rows": actual_rows,
        "duplicate_test_ids": len(dups),
        "errors": errors
    }
    save_manifest("1", result)

    return result


# =============================================================================
# STAGE 2: Same-Day Collapse (Per Bucket)
# =============================================================================

def stage2_collapse() -> Dict:
    """
    Collapse same-day tests per (vehicle_id, test_date).
    Keep the FINAL test of the day (highest test_id) - reflects actual outcome.
    """
    logger.info("\n" + "="*70)
    logger.info("STAGE 2: Same-Day Collapse (Per Bucket)")
    logger.info("="*70)

    # Check stage 1 complete
    stage1 = load_manifest("1")
    if not stage1 or stage1.get("status") != "complete":
        raise RuntimeError("Stage 1 not complete. Run it first.")

    # Check existing manifest
    manifest = load_manifest("2")
    if manifest and manifest.get("status") == "complete":
        logger.info("  Stage 2 already complete, skipping...")
        return manifest

    # Get list of completed buckets
    completed_buckets = set()
    if manifest:
        completed_buckets = set(manifest.get("buckets_completed", []))

    # Clear stage2 if starting fresh
    if not completed_buckets and STAGE2_DIR.exists():
        shutil.rmtree(STAGE2_DIR)
    STAGE2_DIR.mkdir(exist_ok=True)

    total_before = 0
    total_after = 0

    for bucket in range(NUM_BUCKETS):
        if bucket in completed_buckets:
            continue

        if bucket % 16 == 0:
            logger.info(f"  Processing bucket {bucket}/{NUM_BUCKETS}...")

        # Read from all source directories for this bucket
        # Pattern: stage1_parquet/source_*/bucket={bucket}/*.parquet
        bucket_pattern = f"{STAGE1_DIR}/source_*/bucket={bucket}/*.parquet"

        conn = get_duckdb_connection()

        try:
            # Check if any files exist for this bucket
            import glob as glob_module
            bucket_files = glob_module.glob(str(bucket_pattern))
            if not bucket_files:
                completed_buckets.add(bucket)
                conn.close()
                continue

            # Count before
            before = conn.execute(f"""
                SELECT COUNT(*) FROM read_parquet('{bucket_pattern}')
            """).fetchone()[0]
            total_before += before

            # Collapse: keep final test of each day (highest test_id)
            conn.execute(f"""
                COPY (
                    SELECT
                        LAST(test_id ORDER BY test_id) AS test_id,
                        vehicle_id,
                        test_date,
                        LAST(test_result ORDER BY test_id) AS test_result
                    FROM read_parquet('{bucket_pattern}')
                    GROUP BY vehicle_id, test_date
                ) TO '{STAGE2_DIR}/bucket={bucket:03d}.parquet'
                (FORMAT PARQUET, COMPRESSION ZSTD)
            """)

            # Count after
            after = conn.execute(f"""
                SELECT COUNT(*) FROM read_parquet('{STAGE2_DIR}/bucket={bucket:03d}.parquet')
            """).fetchone()[0]
            total_after += after

            completed_buckets.add(bucket)

        except Exception as e:
            logger.error(f"  Error on bucket {bucket}: {e}")
            conn.close()
            raise

        conn.close()

        # Checkpoint every 16 buckets
        if bucket % 16 == 15:
            save_manifest("2", {
                "status": "in_progress",
                "buckets_completed": list(completed_buckets),
                "rows_before": total_before,
                "rows_after": total_after
            })

    duplicates_removed = total_before - total_after
    pct_removed = 100 * duplicates_removed / total_before if total_before > 0 else 0

    logger.info(f"\n  Collapse complete:")
    logger.info(f"    Before: {total_before:,}")
    logger.info(f"    After:  {total_after:,}")
    logger.info(f"    Removed: {duplicates_removed:,} ({pct_removed:.1f}%)")

    result = {
        "status": "complete",
        "buckets_completed": list(completed_buckets),
        "rows_before": total_before,
        "rows_after": total_after,
        "duplicates_removed": duplicates_removed
    }
    save_manifest("2", result)

    return result


# =============================================================================
# STAGE 3: Cycle Boundary Computation (Per Bucket)
# =============================================================================

def stage3_cycles() -> Dict:
    """
    Compute cycle boundaries using window functions.
    A test is cycle-first if:
    - First test for vehicle, OR
    - Previous test was PASS and gap >= 2 days, OR
    - Gap > 120 days (or 180 for COVID period)
    """
    logger.info("\n" + "="*70)
    logger.info("STAGE 3: Cycle Boundary Computation")
    logger.info("="*70)

    # Check stage 2 complete
    stage2 = load_manifest("2")
    if not stage2 or stage2.get("status") != "complete":
        raise RuntimeError("Stage 2 not complete. Run it first.")

    # Check existing manifest
    manifest = load_manifest("3")
    if manifest and manifest.get("status") == "complete":
        logger.info("  Stage 3 already complete, skipping...")
        return manifest

    # Get list of completed buckets
    completed_buckets = set()
    if manifest:
        completed_buckets = set(manifest.get("buckets_completed", []))

    # Clear stage3 if starting fresh
    if not completed_buckets and STAGE3_DIR.exists():
        shutil.rmtree(STAGE3_DIR)
    STAGE3_DIR.mkdir(exist_ok=True)

    total_tests = 0
    cycle_first_count = 0

    for bucket in range(NUM_BUCKETS):
        if bucket in completed_buckets:
            continue

        if bucket % 16 == 0:
            logger.info(f"  Processing bucket {bucket}/{NUM_BUCKETS}...")

        bucket_file = STAGE2_DIR / f"bucket={bucket:03d}.parquet"
        if not bucket_file.exists():
            completed_buckets.add(bucket)
            continue

        conn = get_duckdb_connection()

        try:
            # Compute cycle boundaries with window function
            conn.execute(f"""
                COPY (
                    SELECT
                        test_id,
                        vehicle_id,
                        test_date,
                        test_result,
                        CASE
                            -- First test for vehicle
                            WHEN LAG(vehicle_id) OVER w IS NULL THEN TRUE
                            WHEN LAG(vehicle_id) OVER w != vehicle_id THEN TRUE
                            -- Previous test was PASS and gap >= 2 days
                            WHEN LAG(test_result) OVER w = 1
                                 AND DATEDIFF('day', LAG(test_date) OVER w, test_date) >= 2 THEN TRUE
                            -- COVID adjustment: 180 days for Mar-Aug 2020
                            WHEN test_date BETWEEN '2020-03-30' AND '2020-08-31'
                                 AND DATEDIFF('day', LAG(test_date) OVER w, test_date) > 180 THEN TRUE
                            -- Standard gap threshold: 120 days
                            WHEN DATEDIFF('day', LAG(test_date) OVER w, test_date) > 120 THEN TRUE
                            ELSE FALSE
                        END AS is_cycle_first
                    FROM read_parquet('{bucket_file}')
                    WINDOW w AS (PARTITION BY vehicle_id ORDER BY test_date, test_id)
                ) TO '{STAGE3_DIR}/bucket={bucket:03d}.parquet'
                (FORMAT PARQUET, COMPRESSION ZSTD)
            """)

            # Count cycle-first tests
            stats = conn.execute(f"""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN is_cycle_first THEN 1 ELSE 0 END) as cycle_first
                FROM read_parquet('{STAGE3_DIR}/bucket={bucket:03d}.parquet')
            """).fetchone()

            total_tests += stats[0]
            cycle_first_count += stats[1]

            completed_buckets.add(bucket)

        except Exception as e:
            logger.error(f"  Error on bucket {bucket}: {e}")
            conn.close()
            raise

        conn.close()

        # Checkpoint every 16 buckets
        if bucket % 16 == 15:
            save_manifest("3", {
                "status": "in_progress",
                "buckets_completed": list(completed_buckets),
                "total_tests": total_tests,
                "cycle_first_count": cycle_first_count
            })

    retest_count = total_tests - cycle_first_count
    retest_pct = 100 * retest_count / total_tests if total_tests > 0 else 0

    logger.info(f"\n  Cycle computation complete:")
    logger.info(f"    Total tests: {total_tests:,}")
    logger.info(f"    Cycle-first: {cycle_first_count:,} ({100-retest_pct:.1f}%)")
    logger.info(f"    Retests: {retest_count:,} ({retest_pct:.1f}%)")

    result = {
        "status": "complete",
        "buckets_completed": list(completed_buckets),
        "total_tests": total_tests,
        "cycle_first_count": cycle_first_count,
        "retest_count": retest_count
    }
    save_manifest("3", result)

    return result


# =============================================================================
# STAGE 4: Extract Cycle-First Tests
# =============================================================================

def stage4_extract() -> Dict:
    """
    Combine all buckets and extract cycle-first tests to final output.
    """
    logger.info("\n" + "="*70)
    logger.info("STAGE 4: Extract Cycle-First Tests")
    logger.info("="*70)

    # Check stage 3 complete
    stage3 = load_manifest("3")
    if not stage3 or stage3.get("status") != "complete":
        raise RuntimeError("Stage 3 not complete. Run it first.")

    # Check existing manifest
    manifest = load_manifest("4")
    if manifest and manifest.get("status") == "complete":
        logger.info("  Stage 4 already complete, skipping...")
        return manifest

    logger.info(f"  Writing to {OUTPUT_FILE}...")

    conn = get_duckdb_connection()

    # Extract cycle-first tests
    conn.execute(f"""
        COPY (
            SELECT test_id, test_date, vehicle_id
            FROM read_parquet('{STAGE3_DIR}/*.parquet')
            WHERE is_cycle_first = TRUE
        ) TO '{OUTPUT_FILE}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
    """)

    # Get stats
    stats = conn.execute(f"""
        SELECT
            COUNT(*) as total,
            COUNT(DISTINCT vehicle_id) as vehicles,
            MIN(test_date) as min_date,
            MAX(test_date) as max_date
        FROM read_parquet('{OUTPUT_FILE}')
    """).fetchone()

    # Year distribution
    year_dist = conn.execute(f"""
        SELECT
            YEAR(test_date) as year,
            COUNT(*) as cnt
        FROM read_parquet('{OUTPUT_FILE}')
        GROUP BY YEAR(test_date)
        ORDER BY year
    """).fetchall()

    conn.close()

    file_size_mb = OUTPUT_FILE.stat().st_size / (1024**2)

    logger.info(f"\n  Output stats:")
    logger.info(f"    Total cycle-first tests: {stats[0]:,}")
    logger.info(f"    Unique vehicles: {stats[1]:,}")
    logger.info(f"    Date range: {stats[2]} to {stats[3]}")
    logger.info(f"    File size: {file_size_mb:.1f}MB")

    logger.info(f"\n  Year distribution:")
    for year, cnt in year_dist:
        logger.info(f"    {year}: {cnt:,}")

    result = {
        "status": "complete",
        "output_file": str(OUTPUT_FILE),
        "total_tests": stats[0],
        "unique_vehicles": stats[1],
        "min_date": str(stats[2]),
        "max_date": str(stats[3]),
        "file_size_mb": round(file_size_mb, 1),
        "year_distribution": {str(y): c for y, c in year_dist}
    }
    save_manifest("4", result)

    return result


# =============================================================================
# Cleanup
# =============================================================================

def cleanup_intermediate():
    """Clean up intermediate stage files (keep stage1)."""
    logger.info("\n" + "="*70)
    logger.info("Cleanup: Removing intermediate files")
    logger.info("="*70)

    for stage_dir in [STAGE2_DIR, STAGE3_DIR]:
        if stage_dir.exists():
            size_mb = sum(f.stat().st_size for f in stage_dir.glob("*.parquet")) / (1024**2)
            shutil.rmtree(stage_dir)
            logger.info(f"  Removed {stage_dir.name} ({size_mb:.0f}MB)")

    logger.info("  Stage1 parquet kept for reuse")


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Partitioned Cycle Index Builder")
    parser.add_argument("--stage", type=int, help="Resume from specific stage (0-4)")
    parser.add_argument("--verify", action="store_true", help="Verify final output only")
    parser.add_argument("--no-cleanup", action="store_true", help="Keep intermediate files")
    args = parser.parse_args()

    start_time = datetime.now()

    if args.verify:
        # Just verify the output
        if not OUTPUT_FILE.exists():
            logger.error(f"Output file not found: {OUTPUT_FILE}")
            sys.exit(1)

        conn = get_duckdb_connection()
        stats = conn.execute(f"""
            SELECT
                COUNT(*) as total,
                COUNT(DISTINCT vehicle_id) as vehicles,
                SUM(CASE WHEN YEAR(test_date) = 2024 THEN 1 ELSE 0 END) as tests_2024
            FROM read_parquet('{OUTPUT_FILE}')
        """).fetchone()
        conn.close()

        logger.info(f"Output verification:")
        logger.info(f"  Total tests: {stats[0]:,}")
        logger.info(f"  Unique vehicles: {stats[1]:,}")
        logger.info(f"  2024 tests: {stats[2]:,}")
        return

    start_stage = args.stage if args.stage is not None else 0

    try:
        if start_stage <= 0:
            stage0_preflight()

        if start_stage <= 1:
            stage1_ingest()

        if start_stage <= 2:
            stage2_collapse()

        if start_stage <= 3:
            stage3_cycles()

        if start_stage <= 4:
            stage4_extract()

        if not args.no_cleanup:
            cleanup_intermediate()

        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"\n{'='*70}")
        logger.info(f"COMPLETE in {elapsed/60:.1f} minutes")
        logger.info(f"Output: {OUTPUT_FILE}")
        logger.info(f"{'='*70}")

    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        raise


if __name__ == "__main__":
    main()
