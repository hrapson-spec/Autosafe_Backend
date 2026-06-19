"""
Build Advisory Totals with 3-Domain Taxonomy (MDPS Step 1.1)
=============================================================

Aggregates advisory counts per test_id using the 3-domain chassis/safety taxonomy:
- brakes
- suspension_steering
- tyres

Reads from test_items files and joins with item_detail.csv to get section_id mapping.

Output: advisory_totals_3domain.parquet
Schema: test_id, adv_brakes, adv_suspension_steering, adv_tyres

Created: 2026-01-12
"""

import duckdb
from pathlib import Path


def log(msg):
    print(msg, flush=True)


# Paths
TEST_ITEMS_LAKE = Path.home() / "autosafe_work" / "test_items_lake"


# Section IDs for 3-domain mapping (from system_map_v1.py)
BRAKES_SECTIONS = [5430, 20003, 25003, 120]
SUSPENSION_STEERING_SECTIONS = [5190, 5100, 20106, 9000, 50, 25046]
TYRES_SECTIONS = [20218, 25081, 5650, 5670, 20449, 172, 20448, 25098, 25102]


def get_sql_case_statement_3domain(section_col: str = 'section_id') -> str:
    """Generate SQL CASE statement for 3-domain mapping."""
    brakes_list = ', '.join(str(s) for s in BRAKES_SECTIONS)
    susp_steer_list = ', '.join(str(s) for s in SUSPENSION_STEERING_SECTIONS)
    tyres_list = ', '.join(str(s) for s in TYRES_SECTIONS)

    return f"""CASE
        WHEN {section_col} IN ({brakes_list}) THEN 'brakes'
        WHEN {section_col} IN ({susp_steer_list}) THEN 'suspension_steering'
        WHEN {section_col} IN ({tyres_list}) THEN 'tyres'
        ELSE NULL
    END"""


def find_csv_files(test_items_dir: Path) -> list:
    """Find all CSV files in test_items directory, handling nested structure."""
    csv_files = []
    for item in test_items_dir.rglob("*.csv"):
        if not item.name.startswith('.'):
            csv_files.append(str(item))
    return sorted(csv_files)


def build_advisory_totals_3domain():
    """Build advisory totals using 3-domain chassis/safety taxonomy."""
    log("=" * 70)
    log("BUILDING ADVISORY TOTALS (3-DOMAIN TAXONOMY)")
    log("=" * 70)

    PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
    TEST_ITEMS_DIR = PROJECT_ROOT / "test_items"
    ITEM_DETAIL = PROJECT_ROOT / "item_detail.csv"
    OUTPUT = Path.home() / "autosafe_work" / "advisory_totals_3domain.parquet"

    # Use disk-based database for large data processing
    db_file = Path.home() / "autosafe_work" / "temp_advisory_3domain.duckdb"
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

    # Step 3: Load test_items (prefer lake if available, otherwise CSV)
    log("\n[STEP 3] Loading test_items...")

    # Check if lake exists and has data
    lake_has_data = TEST_ITEMS_LAKE.exists() and any(TEST_ITEMS_LAKE.glob("**/*.parquet"))

    if lake_has_data:
        # Use lake directly - already deduplicated and compressed
        log("  Using test_items_lake (parquet) - faster and memory-efficient...")
        lake_pattern = str(TEST_ITEMS_LAKE / "**/*.parquet")
        conn.execute(f"""
            CREATE TABLE test_items_all AS
            SELECT DISTINCT test_id, rfr_id, rfr_type_code
            FROM read_parquet('{lake_pattern}')
        """)
        total_items = conn.execute("SELECT COUNT(*) FROM test_items_all").fetchone()[0]
        log(f"    Loaded {total_items:,} unique items from lake")
    else:
        # Fallback to CSV files
        log("  No parquet lake found, loading from CSV files...")
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

    # Step 4: Join with item_detail to get section_id, then map to 3-domain
    log("\n[STEP 4] Joining with item_detail and mapping to 3-domain taxonomy...")

    domain_case = get_sql_case_statement_3domain('id.section_id')

    conn.execute(f"""
        CREATE TABLE items_with_domain AS
        SELECT
            t.test_id,
            t.rfr_id,
            id.section_id,
            {domain_case} as domain
        FROM test_items_all t
        LEFT JOIN item_detail id ON t.rfr_id = id.rfr_id
        WHERE t.rfr_type_code = 'A'  -- Advisories only
    """)

    mapped_cnt = conn.execute("SELECT COUNT(*) FROM items_with_domain").fetchone()[0]
    log(f"  Mapped advisories: {mapped_cnt:,}")

    # Check domain distribution
    log("\n  Domain distribution:")
    domain_dist = conn.execute("""
        SELECT domain, COUNT(*) as cnt
        FROM items_with_domain
        GROUP BY domain
        ORDER BY cnt DESC
    """).fetchall()
    for domain, cnt in domain_dist:
        domain_str = domain if domain else 'NULL (other)'
        log(f"    {domain_str:25s}: {cnt:>12,}")

    # Step 5: Aggregate by test_id (only include tests with at least one 3-domain advisory)
    log("\n[STEP 5] Aggregating by test_id...")

    conn.execute("""
        CREATE TABLE advisory_totals AS
        SELECT
            test_id,
            COALESCE(SUM(CASE WHEN domain = 'brakes' THEN 1 ELSE 0 END), 0)::INTEGER as adv_brakes,
            COALESCE(SUM(CASE WHEN domain = 'suspension_steering' THEN 1 ELSE 0 END), 0)::INTEGER as adv_suspension_steering,
            COALESCE(SUM(CASE WHEN domain = 'tyres' THEN 1 ELSE 0 END), 0)::INTEGER as adv_tyres
        FROM items_with_domain
        WHERE domain IS NOT NULL  -- Only 3-domain advisories
        GROUP BY test_id
    """)

    final_cnt = conn.execute("SELECT COUNT(*) FROM advisory_totals").fetchone()[0]
    log(f"  Unique test_ids with 3-domain advisories: {final_cnt:,}")

    # Step 6: Validate and save
    log("\n[STEP 6] Validating and saving...")

    # Check for duplicates
    dup_check = conn.execute(
        "SELECT COUNT(*) - COUNT(DISTINCT test_id) FROM advisory_totals"
    ).fetchone()[0]
    log(f"  Duplicate test_ids: {dup_check}")

    # Check domain stats
    stats = conn.execute("""
        SELECT
            AVG(adv_brakes) as avg_brakes,
            AVG(adv_suspension_steering) as avg_susp_steer,
            AVG(adv_tyres) as avg_tyres,
            SUM(CASE WHEN adv_brakes > 0 THEN 1 ELSE 0 END) as has_brakes,
            SUM(CASE WHEN adv_suspension_steering > 0 THEN 1 ELSE 0 END) as has_susp_steer,
            SUM(CASE WHEN adv_tyres > 0 THEN 1 ELSE 0 END) as has_tyres
        FROM advisory_totals
    """).fetchone()
    log(f"\n  Average advisories per test:")
    log(f"    brakes:             {stats[0]:.3f}")
    log(f"    suspension_steering: {stats[1]:.3f}")
    log(f"    tyres:              {stats[2]:.3f}")
    log(f"\n  Tests with at least one advisory:")
    log(f"    brakes:             {stats[3]:,} ({100*stats[3]/final_cnt:.1f}%)")
    log(f"    suspension_steering: {stats[4]:,} ({100*stats[4]/final_cnt:.1f}%)")
    log(f"    tyres:              {stats[5]:,} ({100*stats[5]/final_cnt:.1f}%)")

    # Sample output
    log("\n  Sample output:")
    sample = conn.execute("SELECT * FROM advisory_totals LIMIT 5").fetchdf()
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
    build_advisory_totals_3domain()
