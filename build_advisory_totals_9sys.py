"""
Build Advisory Totals with 9-System Taxonomy
=============================================

Aggregates advisory counts per test_id using the new 9-bucket system taxonomy.
Reads from test_items files and joins with item_detail.csv to get section_id mapping.

Output: advisory_totals_9sys.parquet
Columns: test_id, advisory_count, adv_brakes, adv_tyres_wheels, adv_suspension_steering,
         adv_structure_corrosion, adv_lights_electrical, adv_visibility,
         adv_seatbelts_restraints, adv_exhaust_emissions, adv_other

Created: 2026-01-04
"""

import duckdb
from pathlib import Path
from system_map_v1 import SYSTEMS, get_sql_case_statement


def log(msg):
    print(msg, flush=True)


def find_csv_files(test_items_dir: Path) -> list:
    """Find all CSV files in test_items directory, handling nested structure."""
    csv_files = []
    for item in test_items_dir.rglob("*.csv"):
        if not item.name.startswith('.'):
            csv_files.append(str(item))
    return csv_files


def build_advisory_totals_9sys():
    """Build advisory totals using 9-bucket system taxonomy."""
    log("=" * 70)
    log("BUILDING ADVISORY TOTALS (9-SYSTEM TAXONOMY)")
    log("=" * 70)

    PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
    TEST_ITEMS_DIR = PROJECT_ROOT / "test_items"
    ITEM_DETAIL = PROJECT_ROOT / "item_detail.csv"
    OUTPUT = Path.home() / "autosafe_work" / "advisory_totals_9sys.parquet"

    # Use disk-based database for large data processing
    db_file = Path.home() / "autosafe_work" / "temp_advisory.duckdb"
    if db_file.exists():
        db_file.unlink()

    conn = duckdb.connect(str(db_file))
    conn.execute("SET memory_limit='4GB'")
    conn.execute("SET threads=2")
    conn.execute("SET preserve_insertion_order=false")
    conn.execute("PRAGMA max_temp_directory_size='50GB'")

    # Step 1: Load item_detail.csv to get rfr_id -> section_id mapping
    log("\n[STEP 1] Loading item_detail.csv for RFR -> Section mapping...")
    conn.execute(f"""
        CREATE TABLE item_detail AS
        SELECT DISTINCT
            CAST(rfr_id AS BIGINT) as rfr_id,
            CAST(test_item_set_section_id AS INTEGER) as section_id
        FROM read_csv('{ITEM_DETAIL}', delim='|', header=true)
    """)
    item_cnt = conn.execute("SELECT COUNT(*) FROM item_detail").fetchone()[0]
    log(f"  Loaded {item_cnt:,} RFR -> Section mappings")

    # Step 2: Find all test_item CSV files
    log("\n[STEP 2] Finding test_item CSV files...")
    csv_files = find_csv_files(TEST_ITEMS_DIR)
    for f in csv_files[:5]:
        log(f"  Found: {Path(f).name}")
    if len(csv_files) > 5:
        log(f"  ... and {len(csv_files) - 5} more files")
    log(f"  Total files: {len(csv_files)}")

    if not csv_files:
        log("  ERROR: No CSV files found!")
        return

    # Step 3: Load all test_items using glob with auto-detect
    log("\n[STEP 3] Loading all test_items (auto-detecting format)...")

    # Use DuckDB's read_csv_auto with glob to handle mixed formats
    # Process in batches to avoid memory issues
    conn.execute("CREATE TABLE test_items_all (test_id BIGINT, rfr_id BIGINT, rfr_type_code VARCHAR)")

    for i, csv_file in enumerate(csv_files):
        log(f"  [{i+1}/{len(csv_files)}] Loading {Path(csv_file).name}...")
        try:
            conn.execute(f"""
                INSERT INTO test_items_all
                SELECT
                    test_id::BIGINT,
                    rfr_id::BIGINT,
                    rfr_type_code::VARCHAR
                FROM read_csv_auto('{csv_file}')
            """)
        except Exception as e:
            log(f"    WARNING: Error loading {csv_file}: {e}")
            continue

    total_items = conn.execute("SELECT COUNT(*) FROM test_items_all").fetchone()[0]
    log(f"\n  Total test items loaded: {total_items:,}")

    # Filter to advisories only
    advisory_cnt = conn.execute(
        "SELECT COUNT(*) FROM test_items_all WHERE rfr_type_code = 'A'"
    ).fetchone()[0]
    log(f"  Advisory items (rfr_type_code='A'): {advisory_cnt:,}")

    # Step 4: Join with item_detail to get section_id, then map to system
    log("\n[STEP 4] Joining with item_detail and mapping to 9-system taxonomy...")

    # Get the SQL CASE statement for system mapping
    system_case = get_sql_case_statement('id.section_id')

    conn.execute(f"""
        CREATE TABLE items_with_system AS
        SELECT
            t.test_id,
            t.rfr_id,
            id.section_id,
            {system_case} as system
        FROM test_items_all t
        LEFT JOIN item_detail id ON t.rfr_id = id.rfr_id
        WHERE t.rfr_type_code = 'A'  -- Advisories only
    """)

    mapped_cnt = conn.execute("SELECT COUNT(*) FROM items_with_system").fetchone()[0]
    log(f"  Mapped advisories: {mapped_cnt:,}")

    # Check system distribution
    log("\n  System distribution:")
    system_dist = conn.execute("""
        SELECT system, COUNT(*) as cnt
        FROM items_with_system
        GROUP BY system
        ORDER BY cnt DESC
    """).fetchall()
    for system, cnt in system_dist:
        log(f"    {system:25s}: {cnt:>12,}")

    # Step 5: Aggregate by test_id
    log("\n[STEP 5] Aggregating by test_id...")

    # Build pivot columns
    pivot_cols = []
    for sys in SYSTEMS:
        pivot_cols.append(
            f"COALESCE(SUM(CASE WHEN system = '{sys}' THEN 1 ELSE 0 END), 0)::INTEGER as adv_{sys}"
        )
    pivot_sql = ", ".join(pivot_cols)

    conn.execute(f"""
        CREATE TABLE advisory_totals AS
        SELECT
            test_id,
            COUNT(*)::INTEGER as advisory_count,
            {pivot_sql}
        FROM items_with_system
        GROUP BY test_id
    """)

    final_cnt = conn.execute("SELECT COUNT(*) FROM advisory_totals").fetchone()[0]
    log(f"  Unique test_ids with advisories: {final_cnt:,}")

    # Step 6: Validate and save
    log("\n[STEP 6] Validating and saving...")

    # Check for duplicates
    dup_check = conn.execute(
        "SELECT COUNT(*) - COUNT(DISTINCT test_id) FROM advisory_totals"
    ).fetchone()[0]
    log(f"  Duplicate test_ids: {dup_check}")

    # Sample output
    log("\n  Sample output:")
    sample = conn.execute("SELECT * FROM advisory_totals LIMIT 3").fetchdf()
    print(sample)

    # Save to parquet
    conn.execute(f"COPY advisory_totals TO '{OUTPUT}' (FORMAT PARQUET)")
    log(f"\n  Saved to: {OUTPUT}")

    # Final schema
    log("\n  Output columns:")
    cols = conn.execute("DESCRIBE SELECT * FROM advisory_totals").fetchall()
    for col in cols:
        log(f"    - {col[0]}: {col[1]}")

    conn.close()

    # Clean up temp database
    if db_file.exists():
        db_file.unlink()
        log(f"  Cleaned up temp database: {db_file}")

    log("\n" + "=" * 70)
    log("COMPLETE")
    log("=" * 70)


if __name__ == "__main__":
    build_advisory_totals_9sys()
