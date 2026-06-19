"""
Code-Level Apathy Features Builder

Computes code-level advisory-to-failure matching at anchor_3 granularity.
This goes beyond category-level matching (brakes -> brakes) to specific
sub-component matching (e.g., 5.3.7 ball joints advisory -> 5.3.7 ball joints failure).

Features computed:
- code_level_apathy_hits: Count of anchor_3 codes that appeared as advisory in t-1
                          AND as failure in t (same vehicle)
- code_level_apathy_rate: Smoothed rate = (hits + alpha) / (prev_advisories + alpha + beta)
- has_code_level_apathy: Binary flag for any code-level match

Usage:
    python build_code_level_apathy_features.py
"""

import duckdb
import sys
import logging
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("build_code_level_apathy.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
AUTOSAFE_ROOT = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/AutoSafe"
WORK_ROOT = Path(__file__).parent
CYCLE_HISTORY_PATH = AUTOSAFE_ROOT / "cycle_first_with_history.parquet"
OUTPUT_PATH = WORK_ROOT / "code_level_apathy_features.parquet"
TEMP_DIR = Path("/tmp/autosafe_apathy")
TEMP_DIR.mkdir(exist_ok=True)

# Add AutoSafe root to path for importing dvsa_anchor_mapping
sys.path.insert(0, str(AUTOSAFE_ROOT))

# Test item directories - will glob for all CSV files within
TEST_ITEM_DIRS = [
    AUTOSAFE_ROOT / "test_items/2019",
    AUTOSAFE_ROOT / "test_items/2020",
    AUTOSAFE_ROOT / "test_items/2021",
    AUTOSAFE_ROOT / "test_items/2022",
    AUTOSAFE_ROOT / "test_items/2023",
]

def find_test_item_files():
    """Find all test_item CSV files across all years."""
    import glob
    all_files = []
    for dir_path in TEST_ITEM_DIRS:
        if not dir_path.exists():
            continue
        # Find CSV files recursively
        for pattern in ['*.csv', '*/*.csv', '**/*.csv']:
            files = list(dir_path.glob(pattern))
            for f in files:
                if 'test_item' in f.name.lower() or 'dft_test_item' in f.name.lower():
                    if '__MACOSX' not in str(f):
                        all_files.append(f)
    return list(set(all_files))  # Dedupe

# For backwards compatibility
TEST_ITEM_SOURCES = []  # Will be populated by find_test_item_files()

# Smoothing parameters for apathy rate (Bayesian prior)
ALPHA = 1  # Prior hits
BETA = 5   # Prior non-hits (shrinks toward ~17% base rate)


def build_anchor_mapping_table(conn: duckdb.DuckDBPyConnection) -> None:
    """Build RFR ID to anchor_3 mapping table in DuckDB."""
    logger.info("Building RFR -> anchor_3 mapping table...")

    try:
        from dvsa_anchor_mapping import AnchorMapper
        mapper = AnchorMapper(AUTOSAFE_ROOT)
        mapping_df = mapper.create_rfr_anchor_mapping()

        # Register as DuckDB table
        conn.register('rfr_anchor_mapping', mapping_df)
        count = conn.execute("SELECT COUNT(*) FROM rfr_anchor_mapping").fetchone()[0]
        logger.info(f"  Loaded {count:,} RFR -> anchor mappings")

        # Show distribution
        dist = conn.execute("""
            SELECT anchor_1, COUNT(*) as n
            FROM rfr_anchor_mapping
            GROUP BY 1 ORDER BY 1
        """).df()
        logger.info(f"  Anchor_1 distribution:\n{dist.to_string(index=False)}")

    except Exception as e:
        logger.error(f"Failed to load AnchorMapper: {e}")
        raise


def load_test_items(conn: duckdb.DuckDBPyConnection) -> int:
    """Load test item data (rfr_id, rfr_type_code per test) into temp table."""
    logger.info("Loading test item data...")

    temp_path = TEMP_DIR / "test_items_all.parquet"

    # Check if already built
    if temp_path.exists() and temp_path.stat().st_size > 0:
        count = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{temp_path}')").fetchone()[0]
        if count > 1_000_000:
            logger.info(f"  Using cached test_items ({count:,} records)")
            return count

    # Find all test_item CSV files
    source_files = find_test_item_files()
    logger.info(f"  Found {len(source_files)} test_item CSV files")
    for f in source_files:
        logger.info(f"    - {f}")

    # Process each source file
    parts = []
    for i, source_path in enumerate(source_files):
        logger.info(f"  Processing [{i+1}/{len(source_files)}] {source_path.name}...")
        part_path = TEMP_DIR / f"test_items_part_{i}.parquet"

        try:
            conn.execute(f"""
                COPY (
                    SELECT
                        CAST(test_id AS BIGINT) as test_id,
                        CAST(rfr_id AS INTEGER) as rfr_id,
                        CAST(rfr_type_code AS VARCHAR) as rfr_type_code
                    FROM read_csv('{source_path}', delim='|', header=true,
                                 auto_detect=true, ignore_errors=true)
                    WHERE rfr_type_code IN ('A', 'F', 'M')  -- Advisory, Failure, Minor
                ) TO '{part_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """)

            count = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{part_path}')").fetchone()[0]
            logger.info(f"    Loaded {count:,} defect records")
            parts.append(str(part_path))
        except Exception as e:
            logger.warning(f"    Failed to process {source_path.name}: {e}")

    if not parts:
        raise FileNotFoundError("No test_item files found!")

    # Merge all parts
    files_str = "', '".join(parts)
    conn.execute(f"""
        COPY (
            SELECT * FROM read_parquet(['{files_str}'])
        ) TO '{temp_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)

    total = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{temp_path}')").fetchone()[0]
    logger.info(f"  Total defect records: {total:,}")

    # Cleanup parts
    for part in parts:
        Path(part).unlink(missing_ok=True)

    return total


def build_code_level_apathy_features() -> int:
    """Main function to build code-level apathy features."""
    start_time = datetime.now()

    logger.info("=" * 60)
    logger.info("CODE-LEVEL APATHY FEATURES BUILDER")
    logger.info("=" * 60)

    conn = duckdb.connect()
    conn.execute("SET threads = 2")
    conn.execute("SET memory_limit = '6GB'")
    conn.execute("SET preserve_insertion_order = false")  # Save memory
    conn.execute(f"SET temp_directory = '{TEMP_DIR}'")
    conn.execute("SET max_temp_directory_size = '50GB'")

    # Step 1: Build anchor mapping
    build_anchor_mapping_table(conn)

    # Step 2: Load test items
    test_items_path = TEMP_DIR / "test_items_all.parquet"
    load_test_items(conn)

    # Step 3: Create anchor_3 sets per test
    # Filter to only test_ids in cycle history to save space
    logger.info("\nBuilding anchor_3 sets per test (filtered to cycle history)...")

    advisory_anchors_path = TEMP_DIR / "advisory_anchors.parquet"
    failure_anchors_path = TEMP_DIR / "failure_anchors.parquet"

    # Get test_ids we care about (current + previous cycle)
    relevant_tests_path = TEMP_DIR / "relevant_test_ids.parquet"
    conn.execute(f"""
        COPY (
            SELECT DISTINCT test_id FROM (
                SELECT test_id FROM read_parquet('{CYCLE_HISTORY_PATH}')
                UNION ALL
                SELECT prev_cycle_test_id as test_id FROM read_parquet('{CYCLE_HISTORY_PATH}')
                WHERE prev_cycle_test_id IS NOT NULL
            )
        ) TO '{relevant_tests_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    relevant_count = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{relevant_tests_path}')").fetchone()[0]
    logger.info(f"  Relevant test_ids: {relevant_count:,}")

    # Advisory anchor_3 codes per test (filtered)
    conn.execute(f"""
        COPY (
            SELECT
                ti.test_id,
                LIST(DISTINCT m.anchor_3) as advisory_anchor3_list,
                COUNT(DISTINCT m.anchor_3) as advisory_anchor3_count
            FROM read_parquet('{test_items_path}') ti
            JOIN rfr_anchor_mapping m ON ti.rfr_id = m.rfr_id
            JOIN read_parquet('{relevant_tests_path}') r ON ti.test_id = r.test_id
            WHERE ti.rfr_type_code = 'A'
            GROUP BY ti.test_id
        ) TO '{advisory_anchors_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)

    adv_count = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{advisory_anchors_path}')").fetchone()[0]
    logger.info(f"  Tests with advisories: {adv_count:,}")

    # Failure anchor_3 codes per test (filtered)
    conn.execute(f"""
        COPY (
            SELECT
                ti.test_id,
                LIST(DISTINCT m.anchor_3) as failure_anchor3_list,
                COUNT(DISTINCT m.anchor_3) as failure_anchor3_count
            FROM read_parquet('{test_items_path}') ti
            JOIN rfr_anchor_mapping m ON ti.rfr_id = m.rfr_id
            JOIN read_parquet('{relevant_tests_path}') r ON ti.test_id = r.test_id
            WHERE ti.rfr_type_code IN ('F', 'M')
            GROUP BY ti.test_id
        ) TO '{failure_anchors_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)

    fail_count = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{failure_anchors_path}')").fetchone()[0]
    logger.info(f"  Tests with failures: {fail_count:,}")

    # Step 4: Join with cycle history to link t-1 and t tests
    logger.info("\nJoining with cycle history to compute apathy hits...")

    # The cycle_first_with_history.parquet has prev_cycle_test_id
    # We need: advisory anchors from prev_cycle_test_id, failure anchors from current test_id

    conn.execute(f"""
        COPY (
            SELECT
                h.test_id,
                h.vehicle_id,
                h.test_date,
                h.outcome,
                h.prev_cycle_test_id,

                -- Previous cycle advisory anchor3 codes
                COALESCE(a.advisory_anchor3_list, []) as prev_advisory_anchor3_list,
                COALESCE(a.advisory_anchor3_count, 0) as prev_advisory_anchor3_count,

                -- Current cycle failure anchor3 codes
                COALESCE(f.failure_anchor3_list, []) as curr_failure_anchor3_list,
                COALESCE(f.failure_anchor3_count, 0) as curr_failure_anchor3_count,

                -- Compute code-level apathy hits (intersection of lists)
                list_intersect(
                    COALESCE(a.advisory_anchor3_list, []),
                    COALESCE(f.failure_anchor3_list, [])
                ) as apathy_anchor3_matches,

                len(list_intersect(
                    COALESCE(a.advisory_anchor3_list, []),
                    COALESCE(f.failure_anchor3_list, [])
                )) as code_level_apathy_hits,

                -- Binary flag
                CASE
                    WHEN len(list_intersect(
                        COALESCE(a.advisory_anchor3_list, []),
                        COALESCE(f.failure_anchor3_list, [])
                    )) > 0 THEN 1
                    ELSE 0
                END as has_code_level_apathy,

                -- Smoothed apathy rate (Bayesian)
                CASE
                    WHEN COALESCE(a.advisory_anchor3_count, 0) = 0 THEN NULL
                    ELSE (
                        len(list_intersect(
                            COALESCE(a.advisory_anchor3_list, []),
                            COALESCE(f.failure_anchor3_list, [])
                        )) + {ALPHA}
                    ) * 1.0 / (
                        COALESCE(a.advisory_anchor3_count, 0) + {ALPHA} + {BETA}
                    )
                END as code_level_apathy_rate

            FROM read_parquet('{CYCLE_HISTORY_PATH}') h
            LEFT JOIN read_parquet('{advisory_anchors_path}') a
                ON h.prev_cycle_test_id = a.test_id
            LEFT JOIN read_parquet('{failure_anchors_path}') f
                ON h.test_id = f.test_id
        ) TO '{OUTPUT_PATH}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
    """)

    # Step 5: Diagnostics
    logger.info("\n" + "=" * 60)
    logger.info("DIAGNOSTICS")
    logger.info("=" * 60)

    row_count = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{OUTPUT_PATH}')").fetchone()[0]
    logger.info(f"Total rows: {row_count:,}")

    # Apathy hit distribution
    hit_dist = conn.execute(f"""
        SELECT
            code_level_apathy_hits,
            COUNT(*) as n,
            ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) as pct,
            ROUND(100.0 * SUM(CASE WHEN outcome = 'FAIL' THEN 1 ELSE 0 END) / COUNT(*), 2) as fail_rate_pct
        FROM read_parquet('{OUTPUT_PATH}')
        WHERE prev_advisory_anchor3_count > 0  -- Only tests with prior advisories
        GROUP BY 1
        ORDER BY 1
        LIMIT 10
    """).df()
    logger.info(f"\nApathy Hits Distribution (tests with prior advisories):\n{hit_dist.to_string(index=False)}")

    # Compare with category-level (if available)
    comparison = conn.execute(f"""
        SELECT
            'Category-level (any_same_component_hit)' as method,
            SUM(CASE WHEN any_same_component_hit = 1 THEN 1 ELSE 0 END) as n_hits,
            COUNT(*) as n_total,
            ROUND(100.0 * SUM(CASE WHEN any_same_component_hit = 1 THEN 1 ELSE 0 END) / COUNT(*), 2) as hit_rate_pct
        FROM read_parquet('{CYCLE_HISTORY_PATH}')
        WHERE prev_advisory_known = 1
        UNION ALL
        SELECT
            'Code-level (has_code_level_apathy)' as method,
            SUM(has_code_level_apathy) as n_hits,
            COUNT(*) as n_total,
            ROUND(100.0 * SUM(has_code_level_apathy) / COUNT(*), 2) as hit_rate_pct
        FROM read_parquet('{OUTPUT_PATH}')
        WHERE prev_advisory_anchor3_count > 0
    """).df()
    logger.info(f"\nCategory vs Code-Level Comparison:\n{comparison.to_string(index=False)}")

    # Signal strength: fail rate by apathy flag
    signal = conn.execute(f"""
        SELECT
            has_code_level_apathy,
            COUNT(*) as n,
            ROUND(100.0 * SUM(CASE WHEN outcome = 'FAIL' THEN 1 ELSE 0 END) / COUNT(*), 2) as fail_rate_pct
        FROM read_parquet('{OUTPUT_PATH}')
        WHERE prev_advisory_anchor3_count > 0
        GROUP BY 1
        ORDER BY 1
    """).df()
    logger.info(f"\nFail Rate by Code-Level Apathy Flag:\n{signal.to_string(index=False)}")

    # Top anchor_3 codes that convert
    try:
        top_converters = conn.execute(f"""
            WITH unnested AS (
                SELECT UNNEST(apathy_anchor3_matches) as anchor3_code
                FROM read_parquet('{OUTPUT_PATH}')
                WHERE code_level_apathy_hits > 0
            )
            SELECT anchor3_code, COUNT(*) as conversion_count
            FROM unnested
            GROUP BY 1
            ORDER BY 2 DESC
            LIMIT 10
        """).df()
        logger.info(f"\nTop Converting anchor_3 Codes:\n{top_converters.to_string(index=False)}")
    except Exception as e:
        logger.warning(f"Could not compute top converters: {e}")

    conn.close()

    # Cleanup temp files
    for temp_file in TEMP_DIR.glob("*.parquet"):
        temp_file.unlink(missing_ok=True)

    elapsed = (datetime.now() - start_time).total_seconds()
    output_size_mb = OUTPUT_PATH.stat().st_size / (1024 * 1024)

    logger.info("\n" + "=" * 60)
    logger.info("BUILD COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Output: {OUTPUT_PATH}")
    logger.info(f"Records: {row_count:,}")
    logger.info(f"Size: {output_size_mb:.1f} MB")
    logger.info(f"Time: {elapsed:.1f}s")

    return row_count


if __name__ == "__main__":
    count = build_code_level_apathy_features()
    print(f"\nBuilt code-level apathy features for {count:,} records")
