#!/usr/bin/env python3
"""
Build Systemic vs Random Failure Target Labels (V2)
====================================================

Memory-efficient version using DuckDB throughout.

Handles all test_items file structures:
- 2019: quarterly files
- 2020: quarterly files
- 2021: 12 files in subdirectory
- 2022-2023: single test_item.csv
- 2024: monthly files in different location

Output: filtered_failure_targets_v2.parquet (2019-2024)

Created: 2026-01-09
"""

import duckdb
import sys
from pathlib import Path
from datetime import datetime

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)

# Paths
PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
ITEM_DETAIL = PROJECT_ROOT / "item_detail.csv"
TEST_ITEMS_DIR = PROJECT_ROOT / "test_items"
DATA_2024_DIR = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/MOT Testing data failure item (2024)")
OUTPUT_FILE = PROJECT_ROOT / "filtered_failure_targets_v2.parquet"
TEMP_DIR = Path("/Users/henrirapson/autosafe_work/temp_targets")


def get_test_item_files():
    """Get all test_item CSV files organized by year."""
    files = {}

    # 2019: quarterly files
    files[2019] = list(TEST_ITEMS_DIR.glob("2019/dft_test_item-*.csv"))

    # 2020: quarterly files
    files[2020] = list(TEST_ITEMS_DIR.glob("2020/dft_test_item-*.csv"))

    # 2021: files in subdirectory
    files[2021] = list(TEST_ITEMS_DIR.glob("2021/test_item_2021/*.csv"))

    # 2022: single file
    f2022 = TEST_ITEMS_DIR / "2022/test_item.csv"
    files[2022] = [f2022] if f2022.exists() else []

    # 2023: single file
    f2023 = TEST_ITEMS_DIR / "2023/test_item.csv"
    files[2023] = [f2023] if f2023.exists() else []

    # 2024: monthly files in different location
    files[2024] = list(DATA_2024_DIR.glob("test_item_2024*.csv"))

    return files


def main():
    start_time = datetime.now()

    print("=" * 70, flush=True)
    print("BUILD SYSTEMIC VS RANDOM FAILURE TARGETS (V2)", flush=True)
    print("Using DuckDB for memory-efficient processing", flush=True)
    print("=" * 70, flush=True)

    # Create temp directory
    TEMP_DIR.mkdir(exist_ok=True)

    # Get all test_item files
    all_files = get_test_item_files()
    print("\n[Step 0] Discovered test_item files:", flush=True)
    for year, files in sorted(all_files.items()):
        print(f"  {year}: {len(files)} files", flush=True)

    # Initialize DuckDB with memory limits
    con = duckdb.connect()
    con.execute("SET memory_limit='4GB'")
    con.execute("SET threads=1")
    con.execute("SET preserve_insertion_order=false")

    # Step 1: Build RFR lookup table using DuckDB regex
    print("\n[Step 1] Building RFR classification lookup using DuckDB...", flush=True)

    # Create RFR classification with regex in SQL
    # DuckDB uses regexp_matches(text, pattern, flags) where 'i' = case-insensitive
    con.execute(f"""
        CREATE OR REPLACE TABLE rfr_lookup AS
        SELECT DISTINCT
            CAST(rfr_id AS BIGINT) as rfr_id,
            rfr_desc,
            CASE
                -- RANDOM patterns (check first - more specific)
                WHEN regexp_matches(LOWER(rfr_desc), 'fractur|broken|snapped|crack|detach|shatter|sever|split|bent|deform|misalign')
                    OR regexp_matches(LOWER(rfr_desc), 'impact|sudden|collapsed|buckled|twisted|torn|ripped|puncture')
                    OR regexp_matches(LOWER(rfr_desc), 'missing|insecure|disconnected|not attached|separated')
                    OR regexp_matches(LOWER(rfr_desc), 'kinked|knotted|bulging|chaffing|fouled|fouling')
                    OR regexp_matches(LOWER(rfr_desc), 'inappropriately.*(repaired|modified)|repaired by welding|modification unsafe')
                    OR regexp_matches(LOWER(rfr_desc), 'inadequately.*(repaired|mounted)|defective|obviously.*(modified|repaired)')
                    OR regexp_matches(LOWER(rfr_desc), 'does not face|too far to the|too (low|high)|incorrect.*(colour|position)')
                    OR regexp_matches(LOWER(rfr_desc), 'stitching.*not|stitching.*(incomplete|frayed)|locking mechanism')
                    OR regexp_matches(LOWER(rfr_desc), 'webbing.*(cut|reworked)|unsuitable|cannot be secured')
                    OR regexp_matches(LOWER(rfr_desc), 'tampering|evidence of')
                THEN 'RANDOM'
                -- SYSTEMIC patterns
                WHEN regexp_matches(LOWER(rfr_desc), 'bush|play|worn|ball.?joint|movement|mounting|rubber|deteriorat|perish|clearance')
                    OR regexp_matches(LOWER(rfr_desc), 'wear|corrod|rust|age|oxidat|weaken|perforat|holed')
                    OR regexp_matches(LOWER(rfr_desc), 'stiff|notchy|rough|leak|seep|porous|eroded|degraded|thin|excessive|insufficient')
                    OR regexp_matches(LOWER(rfr_desc), 'faded|discolour|misted|cloudy|opaque|pit|erosion|stretch|slack|loose|binding')
                    OR regexp_matches(LOWER(rfr_desc), 'not working|inoperative|faulty|malfunctioning|not functioning|not illuminat')
                    OR regexp_matches(LOWER(rfr_desc), 'intensity.*(reduced|significantly)|reduced.*(intensity|efficiency)|efficiency.*(below|less)')
                    OR regexp_matches(LOWER(rfr_desc), 'below.*(minimum|requirements)|contaminated|fluid level|less than.*mm')
                    OR regexp_matches(LOWER(rfr_desc), 'flickers|flickering|not releasing|not holding')
                    OR regexp_matches(LOWER(rfr_desc), 'displaced|deflated|incorrectly positioned')
                    OR regexp_matches(LOWER(rfr_desc), 'warning lamp|does not illuminate|light source.*not')
                    OR regexp_matches(LOWER(rfr_desc), 'adversely affected|obscured|not able to be operated|does not operate')
                    OR regexp_matches(LOWER(rfr_desc), 'flashing.*(less than|more than)|not in good working order')
                    OR regexp_matches(LOWER(rfr_desc), 'emits.*(smoke|visible)|emissions.*exceed|idle speed.*too')
                    OR regexp_matches(LOWER(rfr_desc), 'does not clear|not operating|incapable of being adjusted|seriously obscured')
                THEN 'SYSTEMIC'
                ELSE 'UNCATEGORIZED'
            END as failure_mode
        FROM read_csv('{ITEM_DETAIL}',
                     delim='|',
                     header=true,
                     auto_detect=true,
                     ignore_errors=true)
    """)

    # Get classification stats
    stats = con.execute("""
        SELECT failure_mode, COUNT(*) as cnt
        FROM rfr_lookup
        GROUP BY 1
        ORDER BY 2 DESC
    """).fetchall()

    print("\n  RFR Classification distribution:", flush=True)
    total_rfr = sum(s[1] for s in stats)
    for mode, count in stats:
        print(f"    {mode:<15} {count:>8,} ({count/total_rfr*100:>5.1f}%)", flush=True)

    categorized_count = con.execute("""
        SELECT COUNT(*) FROM rfr_lookup WHERE failure_mode != 'UNCATEGORIZED'
    """).fetchone()[0]
    print(f"\n  Categorized RFR IDs: {categorized_count:,}", flush=True)

    # Step 2: Process test_item files year by year
    print("\n[Step 2] Processing test_items files by year...", flush=True)

    year_results = []

    for year in sorted(all_files.keys()):
        files = all_files[year]
        if not files:
            print(f"\n  {year}: No files found", flush=True)
            continue

        print(f"\n  {year}: Processing {len(files)} files...", flush=True)

        # Determine delimiter based on year (known formats)
        # 2022-2023: pipe-delimited
        # Others: comma-delimited
        if year in [2022, 2023]:
            delim = '|'
        else:
            delim = ','

        print(f"    Using delimiter: '{delim}'", flush=True)

        # Build list of file paths as SQL array
        file_list = [f"'{str(f)}'" for f in files]
        file_array = f"[{', '.join(file_list)}]"

        # Process all files for this year
        year_temp = TEMP_DIR / f"year_{year}.parquet"

        try:
            con.execute(f"""
                COPY (
                    SELECT
                        CAST(ti.test_id AS BIGINT) as test_id,
                        MAX(CASE WHEN r.failure_mode = 'SYSTEMIC' THEN 1 ELSE 0 END) as has_systemic_failure,
                        MAX(CASE WHEN r.failure_mode = 'RANDOM' THEN 1 ELSE 0 END) as has_random_failure,
                        SUM(CASE WHEN r.failure_mode = 'SYSTEMIC' THEN 1 ELSE 0 END) as systemic_count,
                        SUM(CASE WHEN r.failure_mode = 'RANDOM' THEN 1 ELSE 0 END) as random_count,
                        1 as has_any_failure
                    FROM read_csv({file_array},
                                 delim='{delim}',
                                 header=true,
                                 auto_detect=true,
                                 ignore_errors=true) ti
                    INNER JOIN rfr_lookup r ON CAST(ti.rfr_id AS BIGINT) = r.rfr_id
                    WHERE r.failure_mode != 'UNCATEGORIZED'
                    GROUP BY CAST(ti.test_id AS BIGINT)
                ) TO '{year_temp}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """)

            # Get stats for this year
            year_stats = con.execute(f"""
                SELECT
                    COUNT(*) as tests,
                    SUM(has_systemic_failure) as systemic,
                    SUM(has_random_failure) as random
                FROM read_parquet('{year_temp}')
            """).fetchone()

            print(f"    Year {year}: {year_stats[0]:,} tests with failures", flush=True)
            print(f"      Systemic: {year_stats[1]:,}, Random: {year_stats[2]:,}", flush=True)
            year_results.append((year, str(year_temp)))

        except Exception as e:
            print(f"    Error processing {year}: {e}", flush=True)
            continue

    # Step 3: Combine all years incrementally to avoid OOM
    print("\n[Step 3] Combining all years into final output...", flush=True)

    if not year_results:
        print("  ERROR: No data processed!", flush=True)
        return

    # Combine incrementally - just concatenate all parquet files
    # Since test_ids are unique per test, no need to aggregate across years
    file_list = [f"'{p}'" for _, p in year_results]
    file_array = f"[{', '.join(file_list)}]"

    con.execute(f"""
        COPY (
            SELECT * FROM read_parquet({file_array})
        ) TO '{OUTPUT_FILE}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
    """)

    # Step 4: Final stats
    print("\n[Step 4] Final statistics...", flush=True)

    final_stats = con.execute(f"""
        SELECT
            COUNT(*) as total,
            SUM(has_systemic_failure) as systemic,
            SUM(has_random_failure) as random,
            SUM(CASE WHEN has_systemic_failure = 1 AND has_random_failure = 1 THEN 1 ELSE 0 END) as both,
            SUM(CASE WHEN has_systemic_failure = 1 AND has_random_failure = 0 THEN 1 ELSE 0 END) as systemic_only,
            SUM(CASE WHEN has_systemic_failure = 0 AND has_random_failure = 1 THEN 1 ELSE 0 END) as random_only
        FROM read_parquet('{OUTPUT_FILE}')
    """).fetchone()

    print(f"\n  Total tests with failures: {final_stats[0]:,}", flush=True)
    print(f"  has_systemic_failure:  {final_stats[1]:,} ({final_stats[1]/final_stats[0]*100:.1f}%)", flush=True)
    print(f"  has_random_failure:    {final_stats[2]:,} ({final_stats[2]/final_stats[0]*100:.1f}%)", flush=True)
    print(f"\n  Overlap analysis:", flush=True)
    print(f"    SYSTEMIC only:  {final_stats[4]:,} ({final_stats[4]/final_stats[0]*100:.1f}%)", flush=True)
    print(f"    RANDOM only:    {final_stats[5]:,} ({final_stats[5]/final_stats[0]*100:.1f}%)", flush=True)
    print(f"    Both:           {final_stats[3]:,} ({final_stats[3]/final_stats[0]*100:.1f}%)", flush=True)

    # Step 5: Verify coverage by year
    print("\n[Step 5] Checking coverage by year...", flush=True)

    CYCLE_HISTORY = PROJECT_ROOT / "cycle_first_with_history.parquet"

    year_coverage = con.execute(f"""
        SELECT
            YEAR(h.test_date) as year,
            COUNT(*) as total_tests,
            COUNT(t.test_id) as with_targets
        FROM read_parquet('{CYCLE_HISTORY}') h
        LEFT JOIN read_parquet('{OUTPUT_FILE}') t ON h.test_id = t.test_id
        WHERE YEAR(h.test_date) >= 2019
        GROUP BY 1
        ORDER BY 1
    """).fetchall()

    print(f"\n  {'Year':<6} {'Total':>12} {'With Targets':>14} {'Coverage':>10}", flush=True)
    print(f"  {'-'*6} {'-'*12} {'-'*14} {'-'*10}", flush=True)
    for year, total, with_targets in year_coverage:
        pct = with_targets / total * 100 if total > 0 else 0
        print(f"  {year:<6} {total:>12,} {with_targets:>14,} {pct:>9.1f}%", flush=True)

    # Cleanup temp files
    print("\n[Step 6] Cleaning up temp files...", flush=True)
    for year, temp_path in year_results:
        try:
            Path(temp_path).unlink()
        except:
            pass

    con.close()

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\n" + "=" * 70, flush=True)
    print(f"COMPLETE in {elapsed:.1f}s", flush=True)
    print(f"Output: {OUTPUT_FILE}", flush=True)
    print("=" * 70, flush=True)


if __name__ == "__main__":
    main()
