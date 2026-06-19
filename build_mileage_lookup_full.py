"""
Build Full Mileage Lookup Table
================================

Creates a comprehensive test_id -> test_mileage lookup table from all available sources.

Sources:
1. cycle_history_2005_2023.parquet (covers 2005-2017, 2019-2020, 2023)
2. dft_test_result_2018.zip (covers 2018)
3. Raw CSVs for 2021, 2022 (from iCloud MOT Test Results)

Output: mileage_lookup_full.parquet

Created: 2026-01-13
"""

import duckdb
import zipfile
import tempfile
import os
from pathlib import Path
from datetime import datetime

# Paths
WORK_DIR = Path.home() / "autosafe_work"
CYCLE_HISTORY_DIR = WORK_DIR / "cycle_history_dataset"
PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
MOT_RESULTS_DIR = PROJECT_ROOT / "MOT Test Results"

# Output
OUTPUT_FILE = WORK_DIR / "mileage_lookup_full.parquet"

def main():
    start_time = datetime.now()
    print("=" * 70)
    print("BUILDING FULL MILEAGE LOOKUP TABLE")
    print("=" * 70)

    con = duckdb.connect()
    con.execute("SET memory_limit='8GB'")

    # ========================================================================
    # Source 1: Existing cycle_history_2005_2023.parquet
    # ========================================================================
    print("\n[1] Loading cycle_history_2005_2023.parquet...")

    history_file = CYCLE_HISTORY_DIR / "cycle_history_2005_2023.parquet"

    result = con.execute(f"""
        SELECT
            EXTRACT(YEAR FROM test_date) as year,
            COUNT(*) as total,
            SUM(CASE WHEN test_mileage IS NOT NULL THEN 1 ELSE 0 END) as has_mileage
        FROM read_parquet('{history_file}')
        GROUP BY 1
        ORDER BY 1
    """).fetchall()

    print("  Years in base file:")
    base_years = set()
    for year, total, has_mileage in result:
        pct = 100.0 * has_mileage / total
        print(f"    {int(year)}: {total:>12,} rows ({pct:.1f}% with mileage)")
        base_years.add(int(year))

    # Create base lookup from history
    print("\n  Creating base lookup...")
    con.execute(f"""
        CREATE TABLE mileage_lookup AS
        SELECT test_id, test_mileage
        FROM read_parquet('{history_file}')
        WHERE test_mileage IS NOT NULL
    """)

    base_count = con.execute("SELECT COUNT(*) FROM mileage_lookup").fetchone()[0]
    print(f"  Base lookup: {base_count:,} rows")

    # ========================================================================
    # Source 2: 2018 from zip file
    # ========================================================================
    print("\n[2] Adding 2018 from zip file...")

    zip_path = CYCLE_HISTORY_DIR / "dft_test_result_2018.zip"
    if zip_path.exists():
        with zipfile.ZipFile(zip_path, 'r') as z:
            csv_files = [n for n in z.namelist() if n.endswith('.csv') and not n.startswith('__')]
            print(f"  Found {len(csv_files)} CSV files in zip")

            # Extract to temp dir and read
            with tempfile.TemporaryDirectory() as tmpdir:
                for csv_file in csv_files:
                    z.extract(csv_file, tmpdir)
                    csv_path = os.path.join(tmpdir, csv_file)

                    # Insert into lookup (ignore malformed rows)
                    con.execute(f"""
                        INSERT INTO mileage_lookup
                        SELECT CAST(test_id AS BIGINT), CAST(test_mileage AS INTEGER)
                        FROM read_csv('{csv_path}', header=true, ignore_errors=true)
                        WHERE test_mileage IS NOT NULL
                          AND CAST(test_id AS BIGINT) NOT IN (SELECT test_id FROM mileage_lookup)
                    """)

                    print(f"    Added: {csv_file}")

        count_after_2018 = con.execute("SELECT COUNT(*) FROM mileage_lookup").fetchone()[0]
        print(f"  After 2018: {count_after_2018:,} rows (+{count_after_2018 - base_count:,})")
    else:
        print(f"  WARNING: {zip_path} not found")
        count_after_2018 = base_count

    # ========================================================================
    # Source 3: 2021 from raw CSVs
    # ========================================================================
    print("\n[3] Adding 2021 from raw CSVs...")

    dir_2021 = MOT_RESULTS_DIR / "2021"
    if dir_2021.exists():
        csv_files = list(dir_2021.glob("*.csv"))
        print(f"  Found {len(csv_files)} CSV files")

        added_2021 = 0
        for csv_path in csv_files:
            try:
                con.execute(f"""
                    INSERT INTO mileage_lookup
                    SELECT CAST(test_id AS BIGINT), CAST(test_mileage AS INTEGER)
                    FROM read_csv('{csv_path}', header=true, ignore_errors=true)
                    WHERE test_mileage IS NOT NULL
                      AND CAST(test_id AS BIGINT) NOT IN (SELECT test_id FROM mileage_lookup)
                """)
                print(f"    Added: {csv_path.name}")
            except Exception as e:
                print(f"    Error reading {csv_path.name}: {e}")

        count_after_2021 = con.execute("SELECT COUNT(*) FROM mileage_lookup").fetchone()[0]
        print(f"  After 2021: {count_after_2021:,} rows (+{count_after_2021 - count_after_2018:,})")
    else:
        print(f"  WARNING: {dir_2021} not found")
        count_after_2021 = count_after_2018

    # ========================================================================
    # Source 4: 2022 from raw CSVs
    # ========================================================================
    print("\n[4] Adding 2022 from raw CSVs...")

    dir_2022 = MOT_RESULTS_DIR / "2022"
    if dir_2022.exists():
        csv_files = list(dir_2022.glob("*.csv"))
        print(f"  Found {len(csv_files)} CSV files")

        for csv_path in csv_files:
            try:
                con.execute(f"""
                    INSERT INTO mileage_lookup
                    SELECT CAST(test_id AS BIGINT), CAST(test_mileage AS INTEGER)
                    FROM read_csv('{csv_path}', header=true, ignore_errors=true)
                    WHERE test_mileage IS NOT NULL
                      AND CAST(test_id AS BIGINT) NOT IN (SELECT test_id FROM mileage_lookup)
                """)
                print(f"    Added: {csv_path.name}")
            except Exception as e:
                print(f"    Error reading {csv_path.name}: {e}")

        count_after_2022 = con.execute("SELECT COUNT(*) FROM mileage_lookup").fetchone()[0]
        print(f"  After 2022: {count_after_2022:,} rows (+{count_after_2022 - count_after_2021:,})")
    else:
        print(f"  WARNING: {dir_2022} not found")
        count_after_2022 = count_after_2021

    # ========================================================================
    # Deduplicate and save
    # ========================================================================
    print("\n[5] Deduplicating and saving...")

    # Remove any duplicates (keep first)
    con.execute("""
        CREATE TABLE mileage_lookup_dedup AS
        SELECT test_id, MAX(test_mileage) as test_mileage
        FROM mileage_lookup
        GROUP BY test_id
    """)

    final_count = con.execute("SELECT COUNT(*) FROM mileage_lookup_dedup").fetchone()[0]
    print(f"  Final count: {final_count:,} unique test_ids")

    # Save to parquet
    con.execute(f"""
        COPY mileage_lookup_dedup TO '{OUTPUT_FILE}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)

    file_size = OUTPUT_FILE.stat().st_size / (1024 * 1024 * 1024)
    print(f"  Saved to: {OUTPUT_FILE}")
    print(f"  File size: {file_size:.2f} GB")

    # ========================================================================
    # Summary
    # ========================================================================
    elapsed = (datetime.now() - start_time).total_seconds()
    print("\n" + "=" * 70)
    print("MILEAGE LOOKUP BUILD COMPLETE")
    print("=" * 70)
    print(f"  Total rows: {final_count:,}")
    print(f"  File: {OUTPUT_FILE}")
    print(f"  Elapsed: {elapsed:.1f}s")
    print("=" * 70)

    con.close()


if __name__ == "__main__":
    main()
