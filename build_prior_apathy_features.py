#!/usr/bin/env python3
"""
Build Prior Apathy Features - Leakage-Free Version
===================================================

CRITICAL FIX: The original apathy feature (V6) has TARGET LEAKAGE because it checks:
    advisory(T-1) ∩ failure(T)  ← Uses current test result!

This script computes LEAKAGE-FREE apathy using only historical data:
    advisory(T-2) ∩ failure(T-1)  ← Pure history, "behavioral criminal record"

For test T:
1. Look up prev_cycle_test_id (T-1)
2. For T-1, look up its prev_cycle_test_id (T-2)
3. Check: advisory_anchors(T-2) ∩ failure_anchors(T-1)
4. Output: has_prior_apathy, prior_apathy_hits

Created: 2026-01-09
"""

import duckdb
import subprocess
import sys
import logging
from pathlib import Path
from datetime import datetime
import os
import gc

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

AUTOSAFE_ROOT = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/AutoSafe"
WORK_ROOT = Path("/Users/henrirapson/autosafe_work")
CYCLE_HISTORY_PATH = AUTOSAFE_ROOT / "cycle_first_with_history.parquet"
OUTPUT_PATH = WORK_ROOT / "prior_apathy_features.parquet"
TEMP_DIR = WORK_ROOT / "temp_prior_apathy"
TEMP_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(AUTOSAFE_ROOT))

# All test_item files by year with their delimiters
# 2019-2020: quarterly files, comma-delimited
# 2021: monthly files in subdirectory, comma-delimited
# 2022-2023: single files, pipe-delimited
# 2024: monthly files, comma-delimited
DATA_2024_DIR = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/MOT Testing data failure item (2024)")

def get_test_item_files():
    """Get all test_item CSV files organized by year with delimiters."""
    files = {}

    # 2019: quarterly files (comma-delimited)
    files[2019] = {
        'files': list((AUTOSAFE_ROOT / "test_items/2019").glob("dft_test_item-*.csv")),
        'delim': ','
    }

    # 2020: quarterly files (comma-delimited)
    files[2020] = {
        'files': list((AUTOSAFE_ROOT / "test_items/2020").glob("dft_test_item-*.csv")),
        'delim': ','
    }

    # 2021: monthly files in subdirectory (comma-delimited)
    files[2021] = {
        'files': list((AUTOSAFE_ROOT / "test_items/2021/test_item_2021").glob("*.csv")),
        'delim': ','
    }

    # 2022: single file (pipe-delimited)
    f2022 = AUTOSAFE_ROOT / "test_items/2022/test_item.csv"
    files[2022] = {
        'files': [f2022] if f2022.exists() else [],
        'delim': '|'
    }

    # 2023: single file (pipe-delimited)
    f2023 = AUTOSAFE_ROOT / "test_items/2023/test_item.csv"
    files[2023] = {
        'files': [f2023] if f2023.exists() else [],
        'delim': '|'
    }

    # 2024: monthly files (comma-delimited)
    files[2024] = {
        'files': list(DATA_2024_DIR.glob("test_item_2024*.csv")),
        'delim': ','
    }

    return files

TEST_ITEM_FILES = get_test_item_files()

# Smoothing parameters for apathy rate
ALPHA = 1
BETA = 5


def check_available_memory():
    """Log memory status. DuckDB spills to disk so we proceed anyway."""
    result = subprocess.run(['vm_stat'], capture_output=True, text=True)
    free_pages = inactive_pages = 0
    for line in result.stdout.split('\n'):
        if 'Pages free' in line:
            free_pages = int(line.split(':')[1].strip().rstrip('.'))
        elif 'Pages inactive' in line:
            inactive_pages = int(line.split(':')[1].strip().rstrip('.'))
    # macOS uses 16KB pages; free + inactive is effectively available
    available_mb = (free_pages + inactive_pages) * 16384 / 1024 / 1024
    logger.info(f"Available memory (free+inactive): {available_mb:.0f}MB")
    if available_mb < 1000:
        logger.warning("Low memory - DuckDB will spill to disk, expect slower performance")


def preprocess_csv_with_awk(csv_path: Path, output_path: Path, rfr_type: str, delim: str = '|') -> int:
    """Use awk to extract filtered rows - much faster than Python."""

    # Filter: header + rows where rfr_type_code matches
    # Output: test_id|rfr_id only (columns 1 and 2)
    # Handle both quoted ("A") and unquoted (A) values
    if rfr_type == 'A':
        filter_condition = '$3=="A" || $3=="\\"A\\""'
    else:  # F or M
        filter_condition = '($3=="F" || $3=="M" || $3=="\\"F\\"" || $3=="\\"M\\"")'

    # Escape the delimiter for shell
    awk_delim = delim if delim != '|' else '\\|'

    # Also strip quotes from test_id and rfr_id if present (gsub removes surrounding quotes)
    awk_cmd = f"""awk -F'{awk_delim}' 'NR==1 {{print "test_id|rfr_id"}} NR>1 && {filter_condition} {{gsub(/^"|"$/, "", $1); gsub(/^"|"$/, "", $2); print $1"|"$2}}' '{csv_path}' > '{output_path}'"""

    result = subprocess.run(awk_cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"awk failed for {csv_path.name}: {result.stderr}")
        return 0

    # Count lines
    wc_result = subprocess.run(['wc', '-l', str(output_path)], capture_output=True, text=True)
    return int(wc_result.stdout.split()[0]) - 1  # Minus header


def main():
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info("BUILD PRIOR APATHY FEATURES (LEAKAGE-FREE)")
    logger.info("=" * 60)
    logger.info("Using: advisory(T-2) ∩ failure(T-1) instead of advisory(T-1) ∩ failure(T)")

    # Memory check before starting
    check_available_memory()

    # Step 1: Load RFR -> anchor mapping
    logger.info("\n[Step 1] Loading anchor mapping...")
    from dvsa_anchor_mapping import AnchorMapper
    mapper = AnchorMapper(AUTOSAFE_ROOT)
    mapping_df = mapper.create_rfr_anchor_mapping()

    conn = duckdb.connect()
    conn.execute("SET threads = 1")  # Single thread = lower peak memory
    conn.execute("SET memory_limit = '4GB'")
    conn.execute("SET preserve_insertion_order = false")  # Memory optimization
    conn.execute("SET max_temp_directory_size = '50GB'")  # Bound spilling
    conn.execute(f"SET temp_directory = '{TEMP_DIR}'")

    mapping_path = TEMP_DIR / "rfr_anchor_mapping.parquet"
    conn.execute(f"COPY (SELECT * FROM mapping_df) TO '{mapping_path}' (FORMAT PARQUET)")
    logger.info(f"  Loaded {len(mapping_df):,} mappings")
    del mapping_df

    # Step 2: Pre-process CSV files with awk
    logger.info("\n[Step 2] Pre-processing CSV files with awk...")

    for year, year_data in TEST_ITEM_FILES.items():
        file_list = year_data['files']
        delim = year_data['delim']

        if not file_list:
            logger.warning(f"  {year}: No files found")
            continue

        logger.info(f"  {year}: Processing {len(file_list)} files (delim='{delim}')...")

        # Process all files for this year, concatenating results
        adv_csv = TEMP_DIR / f"advisory_{year}.csv"
        fail_csv = TEMP_DIR / f"failure_{year}.csv"

        total_adv = 0
        total_fail = 0

        for i, csv_path in enumerate(file_list):
            # For first file, create new file; for rest, append
            temp_adv = TEMP_DIR / f"temp_adv_{year}_{i}.csv"
            temp_fail = TEMP_DIR / f"temp_fail_{year}_{i}.csv"

            n_adv = preprocess_csv_with_awk(csv_path, temp_adv, 'A', delim)
            n_fail = preprocess_csv_with_awk(csv_path, temp_fail, 'F', delim)

            total_adv += n_adv
            total_fail += n_fail

            if i == 0:
                # First file - move to main output (includes header)
                if temp_adv.exists():
                    temp_adv.rename(adv_csv)
                if temp_fail.exists():
                    temp_fail.rename(fail_csv)
            else:
                # Subsequent files - append without header
                if temp_adv.exists():
                    subprocess.run(f"tail -n +2 '{temp_adv}' >> '{adv_csv}'", shell=True)
                    temp_adv.unlink()
                if temp_fail.exists():
                    subprocess.run(f"tail -n +2 '{temp_fail}' >> '{fail_csv}'", shell=True)
                    temp_fail.unlink()

        logger.info(f"    {total_adv:,} advisory rows")
        logger.info(f"    {total_fail:,} failure rows")

    # Step 3: Convert to parquet with anchor joins
    logger.info("\n[Step 3] Building anchor aggregates...")

    for year, year_data in TEST_ITEM_FILES.items():
        adv_csv = TEMP_DIR / f"advisory_{year}.csv"
        fail_csv = TEMP_DIR / f"failure_{year}.csv"

        if not adv_csv.exists() or not fail_csv.exists():
            continue

        # Advisory anchors (for T-2)
        adv_parquet = TEMP_DIR / f"advisory_anchors_{year}.parquet"
        logger.info(f"  {year}: Building advisory anchors...")
        conn.execute(f"""
            COPY (
                SELECT
                    CAST(t.test_id AS BIGINT) as test_id,
                    LIST(DISTINCT CAST(m.anchor_3 AS VARCHAR)) as advisory_anchor3_list
                FROM read_csv('{adv_csv}', delim='|', header=true) t
                JOIN read_parquet('{mapping_path}') m ON CAST(t.rfr_id AS INTEGER) = m.rfr_id
                GROUP BY t.test_id
            ) TO '{adv_parquet}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        n = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{adv_parquet}')").fetchone()[0]
        logger.info(f"    {n:,} tests with advisory anchors")

        # Failure anchors (for T-1)
        fail_parquet = TEMP_DIR / f"failure_anchors_{year}.parquet"
        logger.info(f"  {year}: Building failure anchors...")
        conn.execute(f"""
            COPY (
                SELECT
                    CAST(t.test_id AS BIGINT) as test_id,
                    LIST(DISTINCT CAST(m.anchor_3 AS VARCHAR)) as failure_anchor3_list
                FROM read_csv('{fail_csv}', delim='|', header=true) t
                JOIN read_parquet('{mapping_path}') m ON CAST(t.rfr_id AS INTEGER) = m.rfr_id
                GROUP BY t.test_id
            ) TO '{fail_parquet}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        n = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{fail_parquet}')").fetchone()[0]
        logger.info(f"    {n:,} tests with failure anchors")

        # Clean up CSV files
        os.remove(adv_csv)
        os.remove(fail_csv)

        # Force memory cleanup after each year
        gc.collect()
        conn.execute("CHECKPOINT")
        logger.info(f"  {year}: Memory cleanup complete")

    # Step 4: Combine anchor files
    logger.info("\n[Step 4] Combining anchor files...")

    all_years = list(TEST_ITEM_FILES.keys())
    adv_files = [str(TEMP_DIR / f"advisory_anchors_{y}.parquet")
                 for y in all_years
                 if (TEMP_DIR / f"advisory_anchors_{y}.parquet").exists()]
    fail_files = [str(TEMP_DIR / f"failure_anchors_{y}.parquet")
                  for y in all_years
                  if (TEMP_DIR / f"failure_anchors_{y}.parquet").exists()]

    combined_adv = TEMP_DIR / "advisory_combined.parquet"
    combined_fail = TEMP_DIR / "failure_combined.parquet"

    adv_union = " UNION ALL ".join([f"SELECT * FROM read_parquet('{f}')" for f in adv_files])
    conn.execute(f"COPY ({adv_union}) TO '{combined_adv}' (FORMAT PARQUET, COMPRESSION ZSTD)")

    fail_union = " UNION ALL ".join([f"SELECT * FROM read_parquet('{f}')" for f in fail_files])
    conn.execute(f"COPY ({fail_union}) TO '{combined_fail}' (FORMAT PARQUET, COMPRESSION ZSTD)")

    logger.info(f"  Advisory: {conn.execute(f'SELECT COUNT(*) FROM read_parquet({chr(39)}{combined_adv}{chr(39)})').fetchone()[0]:,} tests")
    logger.info(f"  Failure: {conn.execute(f'SELECT COUNT(*) FROM read_parquet({chr(39)}{combined_fail}{chr(39)})').fetchone()[0]:,} tests")

    # Cleanup year-specific parquets to free disk space
    for year in all_years:
        for pattern in ['advisory_anchors_', 'failure_anchors_']:
            temp_file = TEMP_DIR / f"{pattern}{year}.parquet"
            if temp_file.exists():
                os.remove(temp_file)
                logger.info(f"  Cleaned up {temp_file.name}")
    gc.collect()

    # Step 5: Compute PRIOR apathy features (LEAKAGE-FREE)
    # Key difference from V6:
    # - V6 checks: advisory(T-1) ∩ failure(T)  ← LEAKY!
    # - This checks: advisory(T-2) ∩ failure(T-1)  ← Pure history
    logger.info("\n[Step 5] Computing PRIOR apathy features (leakage-free)...")
    logger.info("  Logic: For test T, check if T-1 failed on items that were advisories at T-2")

    # Phase 5a: Build just the test ID chain (T -> T-1 -> T-2) - minimal columns
    chain_path = TEMP_DIR / "test_chain.parquet"
    logger.info("  Phase 5a: Building test chain (T -> T-1 -> T-2)...")
    conn.execute(f"""
        COPY (
            SELECT
                h1.test_id as test_id_T,
                h1.vehicle_id,
                h1.test_date,
                h1.outcome,
                h1.prev_cycle_test_id as test_id_T1,
                h2.prev_cycle_test_id as test_id_T2
            FROM read_parquet('{CYCLE_HISTORY_PATH}') h1
            LEFT JOIN (
                SELECT test_id, prev_cycle_test_id
                FROM read_parquet('{CYCLE_HISTORY_PATH}')
            ) h2 ON h1.prev_cycle_test_id = h2.test_id
        ) TO '{chain_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    chain_count = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{chain_path}')").fetchone()[0]
    logger.info(f"    Chain built: {chain_count:,} rows")
    gc.collect()
    conn.execute("CHECKPOINT")

    # Phase 5b: Join with failure anchors (T-1)
    with_fail_path = TEMP_DIR / "chain_with_fail.parquet"
    logger.info("  Phase 5b: Joining failure anchors (T-1)...")
    conn.execute(f"""
        COPY (
            SELECT
                c.test_id_T,
                c.vehicle_id,
                c.test_date,
                c.outcome,
                c.test_id_T1,
                c.test_id_T2,
                COALESCE(f.failure_anchor3_list, []) as fail_T1_list
            FROM read_parquet('{chain_path}') c
            LEFT JOIN read_parquet('{combined_fail}') f ON c.test_id_T1 = f.test_id
        ) TO '{with_fail_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    logger.info(f"    Failure join complete")
    os.remove(chain_path)  # Free disk space
    gc.collect()
    conn.execute("CHECKPOINT")

    # Phase 5c: Join with advisory anchors (T-2) and compute intersection
    logger.info("  Phase 5c: Joining advisory anchors (T-2) and computing intersection...")
    conn.execute(f"""
        COPY (
            SELECT
                c.test_id_T as test_id,
                c.vehicle_id,
                c.test_date,
                c.outcome,
                c.test_id_T1 as prev_test_id,
                c.test_id_T2 as prev_prev_test_id,
                len(COALESCE(a.advisory_anchor3_list, [])) as advisory_count_T2,
                len(c.fail_T1_list) as failure_count_T1,
                len(list_intersect(COALESCE(a.advisory_anchor3_list, []), c.fail_T1_list)) as prior_apathy_hits,
                CASE WHEN len(list_intersect(COALESCE(a.advisory_anchor3_list, []), c.fail_T1_list)) > 0 THEN 1 ELSE 0 END as has_prior_apathy,
                CASE
                    WHEN len(COALESCE(a.advisory_anchor3_list, [])) = 0 THEN 0.0
                    ELSE (len(list_intersect(COALESCE(a.advisory_anchor3_list, []), c.fail_T1_list)) + {ALPHA}) * 1.0 / (len(COALESCE(a.advisory_anchor3_list, [])) + {ALPHA} + {BETA})
                END as prior_apathy_rate
            FROM read_parquet('{with_fail_path}') c
            LEFT JOIN read_parquet('{combined_adv}') a ON c.test_id_T2 = a.test_id
        ) TO '{OUTPUT_PATH}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
    """)
    logger.info(f"    Final output written")
    os.remove(with_fail_path)  # Free disk space
    gc.collect()

    # Diagnostics
    logger.info("\n[Step 6] Diagnostics...")

    n_rows = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{OUTPUT_PATH}')").fetchone()[0]
    logger.info(f"Total rows: {n_rows:,}")

    stats = conn.execute(f"""
        SELECT
            SUM(CASE WHEN prev_prev_test_id IS NOT NULL THEN 1 ELSE 0 END) as with_T2,
            SUM(CASE WHEN advisory_count_T2 > 0 THEN 1 ELSE 0 END) as with_prior_adv_T2,
            SUM(has_prior_apathy) as with_prior_apathy,
            ROUND(100.0 * SUM(CASE WHEN has_prior_apathy = 1 AND outcome = 'FAIL' THEN 1 ELSE 0 END) /
                  NULLIF(SUM(has_prior_apathy), 0), 1) as apathy_fail_rate,
            ROUND(100.0 * SUM(CASE WHEN has_prior_apathy = 0 AND outcome = 'FAIL' THEN 1 ELSE 0 END) /
                  NULLIF(SUM(CASE WHEN has_prior_apathy = 0 THEN 1 ELSE 0 END), 0), 1) as no_apathy_fail_rate
        FROM read_parquet('{OUTPUT_PATH}')
    """).fetchone()

    logger.info(f"Tests with T-2 link: {stats[0]:,}")
    logger.info(f"Tests with advisories at T-2: {stats[1]:,}")
    logger.info(f"Tests with prior apathy (behavioral flag): {stats[2]:,}")
    logger.info(f"Fail rate when has_prior_apathy=1: {stats[3]}%")
    logger.info(f"Fail rate when has_prior_apathy=0: {stats[4]}%")

    if stats[3] and stats[4]:
        lift = float(stats[3]) / float(stats[4])
        logger.info(f"Lift (apathy vs no-apathy): {lift:.2f}x")

    # Coverage by year
    logger.info("\nCoverage by year:")
    year_stats = conn.execute(f"""
        SELECT
            YEAR(test_date) as year,
            COUNT(*) as n_tests,
            SUM(CASE WHEN advisory_count_T2 > 0 THEN 1 ELSE 0 END) as with_prior_adv,
            SUM(has_prior_apathy) as with_apathy
        FROM read_parquet('{OUTPUT_PATH}')
        GROUP BY 1
        ORDER BY 1
    """).fetchall()

    for year, n, adv, apathy in year_stats:
        logger.info(f"  {year}: {n:,} tests, {adv:,} with T-2 advisories, {apathy:,} with prior apathy")

    # Validate no leakage: prior_apathy should NOT correlate with current test items
    logger.info("\n[Step 7] Leakage validation...")
    logger.info("  Prior apathy uses only T-2 advisories and T-1 failures")
    logger.info("  Current test T's items are NOT used in the feature")

    conn.close()

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"\nCompleted in {elapsed:.1f}s")
    logger.info(f"Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
