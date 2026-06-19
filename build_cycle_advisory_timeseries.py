"""
Build Cycle-Level Advisory Time Series (MDPS Step 1.2)
======================================================

Creates per-vehicle, per-cycle ordered time series with domain advisory counts.
Enables sequence feature engineering (persistence, recency, switching).

Input:
  - advisory_totals_3domain.parquet (from Step 1.1)
  - cycle_first_with_history.parquet (main cycle history)
  - cycle_history_extension_2024.parquet (2024 extension)
  - dev_set.parquet, oot_test_set.parquet (target vehicles)

Output:
  - cycle_advisory_timeseries_dev.parquet
  - cycle_advisory_timeseries_oot.parquet

Schema:
  - vehicle_id: int64
  - cycle_index: int32 (1-based, ordered by test_date)
  - test_id: int64
  - test_date: date
  - test_mileage: int64
  - adv_brakes: int32
  - adv_suspension_steering: int32
  - adv_tyres: int32

Created: 2026-01-12
"""

import duckdb
from pathlib import Path


def log(msg):
    print(msg, flush=True)


def build_cycle_advisory_timeseries():
    """Build ordered cycle-level advisory time series for DEV and OOT vehicles."""
    log("=" * 70)
    log("BUILDING CYCLE-LEVEL ADVISORY TIME SERIES")
    log("=" * 70)

    PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
    WORK_DIR = Path.home() / "autosafe_work"

    # Input paths
    ADVISORY_TOTALS = WORK_DIR / "advisory_totals_3domain.parquet"
    CYCLE_HISTORY = PROJECT_ROOT / "cycle_first_with_history.parquet"
    CYCLE_EXT_2024 = WORK_DIR / "cycle_history_extension_2024.parquet"
    DEV_SET = PROJECT_ROOT / "stratified_samples" / "dev_set.parquet"
    OOT_SET = PROJECT_ROOT / "stratified_samples" / "oot_test_set.parquet"

    # Output paths
    OUTPUT_DEV = WORK_DIR / "cycle_advisory_timeseries_dev.parquet"
    OUTPUT_OOT = WORK_DIR / "cycle_advisory_timeseries_oot.parquet"

    # Check advisory totals exists
    if not ADVISORY_TOTALS.exists():
        log(f"ERROR: {ADVISORY_TOTALS} not found!")
        log("Please run build_advisory_totals_3domain.py first.")
        return

    # Use disk-based database for large data processing
    db_file = WORK_DIR / "temp_timeseries.duckdb"
    if db_file.exists():
        db_file.unlink()

    conn = duckdb.connect(str(db_file))
    conn.execute("SET memory_limit='6GB'")
    conn.execute("SET threads=4")
    conn.execute("SET preserve_insertion_order=false")
    conn.execute("PRAGMA max_temp_directory_size='50GB'")

    # Step 1: Load DEV and OOT vehicle lists
    log("\n[STEP 1] Loading DEV and OOT vehicle lists...")

    conn.execute(f"""
        CREATE TABLE dev_vehicles AS
        SELECT DISTINCT vehicle_id
        FROM read_parquet('{DEV_SET}')
    """)
    dev_count = conn.execute("SELECT COUNT(*) FROM dev_vehicles").fetchone()[0]
    log(f"  DEV vehicles: {dev_count:,}")

    conn.execute(f"""
        CREATE TABLE oot_vehicles AS
        SELECT DISTINCT vehicle_id
        FROM read_parquet('{OOT_SET}')
    """)
    oot_count = conn.execute("SELECT COUNT(*) FROM oot_vehicles").fetchone()[0]
    log(f"  OOT vehicles: {oot_count:,}")

    # Combined vehicle list
    conn.execute("""
        CREATE TABLE target_vehicles AS
        SELECT vehicle_id FROM dev_vehicles
        UNION
        SELECT vehicle_id FROM oot_vehicles
    """)
    total_vehicles = conn.execute("SELECT COUNT(*) FROM target_vehicles").fetchone()[0]
    log(f"  Total unique target vehicles: {total_vehicles:,}")

    # Step 2: Load cycle history (main + 2024 extension) + target tests
    log("\n[STEP 2] Loading cycle history...")

    # Load main cycle history (only target vehicles)
    log("  Loading main cycle_first_with_history.parquet...")
    conn.execute(f"""
        CREATE TABLE cycles_main AS
        SELECT
            c.test_id,
            c.test_date::DATE as test_date,
            c.vehicle_id,
            NULL::BIGINT as test_mileage  -- Not available in this file
        FROM read_parquet('{CYCLE_HISTORY}') c
        WHERE c.vehicle_id IN (SELECT vehicle_id FROM target_vehicles)
    """)
    main_count = conn.execute("SELECT COUNT(*) FROM cycles_main").fetchone()[0]
    log(f"    Loaded {main_count:,} cycles from main history")

    # Load 2024 extension if exists
    if CYCLE_EXT_2024.exists():
        log("  Loading cycle_history_extension_2024.parquet...")
        conn.execute(f"""
            CREATE TABLE cycles_2024 AS
            SELECT
                c.test_id,
                c.test_date::DATE as test_date,
                c.vehicle_id,
                NULL::BIGINT as test_mileage
            FROM read_parquet('{CYCLE_EXT_2024}') c
            WHERE c.vehicle_id IN (SELECT vehicle_id FROM target_vehicles)
        """)
        ext_count = conn.execute("SELECT COUNT(*) FROM cycles_2024").fetchone()[0]
        log(f"    Loaded {ext_count:,} cycles from 2024 extension")

        # Combine (avoiding duplicates)
        conn.execute("""
            CREATE TABLE history_cycles AS
            SELECT * FROM cycles_main
            UNION
            SELECT * FROM cycles_2024
        """)
    else:
        log("  No 2024 extension found, using main history only")
        conn.execute("CREATE TABLE history_cycles AS SELECT * FROM cycles_main")

    history_count = conn.execute("SELECT COUNT(*) FROM history_cycles").fetchone()[0]
    log(f"  History cycles: {history_count:,}")

    # Also add target tests themselves to ensure no silent drops
    # Some target tests may not be in cycle_first_with_history (first tests, no prior recorded)
    log("  Adding target tests from dev/oot sets (ensuring no silent drops)...")
    conn.execute(f"""
        CREATE TABLE target_tests AS
        SELECT test_id, test_date::DATE as test_date, vehicle_id, test_mileage
        FROM read_parquet('{DEV_SET}')
        UNION
        SELECT test_id, test_date::DATE as test_date, vehicle_id, test_mileage
        FROM read_parquet('{OOT_SET}')
    """)
    target_count = conn.execute("SELECT COUNT(*) FROM target_tests").fetchone()[0]
    log(f"    Target tests: {target_count:,}")

    # Combine history cycles with target tests (deduplicate by test_id)
    # Use UNION ALL then deduplicate, preferring rows with mileage
    conn.execute("""
        CREATE TABLE all_cycles_raw AS
        SELECT test_id, test_date, vehicle_id, NULL::BIGINT as test_mileage
        FROM history_cycles
        UNION ALL
        SELECT test_id, test_date, vehicle_id, test_mileage
        FROM target_tests
    """)

    # Deduplicate by test_id, keeping the row with mileage if available
    conn.execute("""
        CREATE TABLE all_cycles AS
        SELECT
            test_id,
            test_date,
            vehicle_id,
            MAX(test_mileage) as test_mileage  -- Prefer non-null mileage
        FROM all_cycles_raw
        GROUP BY test_id, test_date, vehicle_id
    """)

    cycle_count = conn.execute("SELECT COUNT(*) FROM all_cycles").fetchone()[0]
    log(f"  Total cycles (after dedup): {cycle_count:,}")

    # Step 3 is now combined above
    log("\n[STEP 3] Mileage already added...")
    conn.execute("""
        CREATE TABLE cycles_with_mileage AS
        SELECT * FROM all_cycles
    """)

    # Step 4: Load advisory totals
    log("\n[STEP 4] Loading advisory totals...")
    conn.execute(f"""
        CREATE TABLE advisory_totals AS
        SELECT * FROM read_parquet('{ADVISORY_TOTALS}')
    """)
    adv_count = conn.execute("SELECT COUNT(*) FROM advisory_totals").fetchone()[0]
    log(f"  Advisory totals rows: {adv_count:,}")

    # Step 5: Join cycles with advisory counts
    log("\n[STEP 5] Joining cycles with advisory counts...")
    conn.execute("""
        CREATE TABLE cycles_with_advisories AS
        SELECT
            c.vehicle_id,
            c.test_id,
            c.test_date,
            c.test_mileage,
            COALESCE(a.adv_brakes, 0)::INTEGER as adv_brakes,
            COALESCE(a.adv_suspension_steering, 0)::INTEGER as adv_suspension_steering,
            COALESCE(a.adv_tyres, 0)::INTEGER as adv_tyres
        FROM cycles_with_mileage c
        LEFT JOIN advisory_totals a ON c.test_id = a.test_id
    """)

    joined_count = conn.execute("SELECT COUNT(*) FROM cycles_with_advisories").fetchone()[0]
    log(f"  Cycles with advisories joined: {joined_count:,}")

    # Check advisory coverage
    has_any = conn.execute("""
        SELECT COUNT(*) FROM cycles_with_advisories
        WHERE adv_brakes > 0 OR adv_suspension_steering > 0 OR adv_tyres > 0
    """).fetchone()[0]
    log(f"  Cycles with at least one advisory: {has_any:,} ({100*has_any/joined_count:.1f}%)")

    # Step 6: Compute cycle_index (1-based, ordered by test_date, test_id)
    log("\n[STEP 6] Computing cycle_index...")
    conn.execute("""
        CREATE TABLE timeseries AS
        SELECT
            vehicle_id,
            ROW_NUMBER() OVER (
                PARTITION BY vehicle_id
                ORDER BY test_date, test_id
            )::INTEGER as cycle_index,
            test_id,
            test_date,
            test_mileage,
            adv_brakes,
            adv_suspension_steering,
            adv_tyres
        FROM cycles_with_advisories
    """)

    ts_count = conn.execute("SELECT COUNT(*) FROM timeseries").fetchone()[0]
    log(f"  Time series rows: {ts_count:,}")

    # Stats on cycle counts per vehicle
    cycle_stats = conn.execute("""
        SELECT
            MIN(max_cycle) as min_cycles,
            MAX(max_cycle) as max_cycles,
            AVG(max_cycle)::DECIMAL(10,2) as avg_cycles,
            MEDIAN(max_cycle)::DECIMAL(10,2) as median_cycles
        FROM (
            SELECT vehicle_id, MAX(cycle_index) as max_cycle
            FROM timeseries
            GROUP BY vehicle_id
        )
    """).fetchone()
    log(f"  Cycles per vehicle: min={cycle_stats[0]}, max={cycle_stats[1]}, avg={cycle_stats[2]}, median={cycle_stats[3]}")

    # Step 7: Split into DEV and OOT
    log("\n[STEP 7] Splitting into DEV and OOT...")

    conn.execute(f"""
        COPY (
            SELECT t.*
            FROM timeseries t
            WHERE t.vehicle_id IN (SELECT vehicle_id FROM dev_vehicles)
            ORDER BY t.vehicle_id, t.cycle_index
        ) TO '{OUTPUT_DEV}' (FORMAT PARQUET)
    """)
    dev_rows = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{OUTPUT_DEV}')").fetchone()[0]
    log(f"  DEV output: {dev_rows:,} rows saved to {OUTPUT_DEV.name}")

    conn.execute(f"""
        COPY (
            SELECT t.*
            FROM timeseries t
            WHERE t.vehicle_id IN (SELECT vehicle_id FROM oot_vehicles)
            ORDER BY t.vehicle_id, t.cycle_index
        ) TO '{OUTPUT_OOT}' (FORMAT PARQUET)
    """)
    oot_rows = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{OUTPUT_OOT}')").fetchone()[0]
    log(f"  OOT output: {oot_rows:,} rows saved to {OUTPUT_OOT.name}")

    # Step 8: Final validation summary
    log("\n[STEP 8] Validation summary...")

    # Sample output
    log("\n  Sample DEV output (first vehicle):")
    sample = conn.execute(f"""
        SELECT * FROM read_parquet('{OUTPUT_DEV}')
        WHERE vehicle_id = (
            SELECT vehicle_id FROM read_parquet('{OUTPUT_DEV}') LIMIT 1
        )
        ORDER BY cycle_index
        LIMIT 5
    """).fetchdf()
    print(sample.to_string(index=False))

    # Advisory distribution in output
    for name, path in [("DEV", OUTPUT_DEV), ("OOT", OUTPUT_OOT)]:
        stats = conn.execute(f"""
            SELECT
                AVG(adv_brakes)::DECIMAL(10,3) as avg_brakes,
                AVG(adv_suspension_steering)::DECIMAL(10,3) as avg_susp,
                AVG(adv_tyres)::DECIMAL(10,3) as avg_tyres,
                SUM(CASE WHEN adv_brakes > 0 OR adv_suspension_steering > 0 OR adv_tyres > 0 THEN 1 ELSE 0 END) as has_any,
                COUNT(*) as total
            FROM read_parquet('{path}')
        """).fetchone()
        log(f"\n  {name} advisory stats:")
        log(f"    avg brakes: {stats[0]}, avg susp_steer: {stats[1]}, avg tyres: {stats[2]}")
        log(f"    has any: {stats[3]:,} / {stats[4]:,} ({100*stats[3]/stats[4]:.1f}%)")

    conn.close()

    # Clean up temp database
    if db_file.exists():
        db_file.unlink()
        log(f"\n  Cleaned up temp database: {db_file}")

    log("\n" + "=" * 70)
    log("COMPLETE")
    log("=" * 70)


if __name__ == "__main__":
    build_cycle_advisory_timeseries()
