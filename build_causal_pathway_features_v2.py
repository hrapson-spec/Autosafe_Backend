#!/usr/bin/env python3
"""
Build Causal Pathway Features v2
=================================

Fixed version that:
1. Parameterizes history window (HISTORY_START_YEAR to HISTORY_END_YEAR)
2. Validates input manifest (hard fail on missing years)
3. Handles different file formats per year (quarterly, sharded, single)
4. Has coverage-contract assertions at the end

Causal Pathways:
- DEFERRAL: Owner skipped basic pre-MOT checks (bulbs, wipers, tread, headlamp aim)
- CORROSION: Salt/environmental exposure (corroded, rust, perforated)
- ROAD_DAMAGE: Pothole/impact damage (bent, buckled, misaligned)
- WEAR: Natural component lifecycle (worn, deteriorated on vehicles >10yr)
- UNCATEGORIZED: Cannot determine cause

Output: causal_pathway_features_v2.parquet

Created: 2026-01-10
"""

import pandas as pd
import numpy as np
import duckdb
import re
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Tuple, Dict

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("build_causal_pathway_features_v2.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================

PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
WORK_DIR = Path("/Users/henrirapson/autosafe_work")

# History window parameters
HISTORY_START_YEAR = 2019  # First year with test_item data
HISTORY_END_YEAR = 2023    # Last year with test_item data

ITEM_DETAIL = PROJECT_ROOT / "item_detail.csv"
TEST_ITEMS_DIR = PROJECT_ROOT / "test_items"
CYCLE_HISTORY = PROJECT_ROOT / "cycle_first_with_history.parquet"
DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set.parquet"
OOT_SET = PROJECT_ROOT / "stratified_samples/oot_test_set.parquet"

OUTPUT_FILE = WORK_DIR / "causal_pathway_features_v2.parquet"
RFR_CLASSIFICATION_FILE = WORK_DIR / "rfr_causal_pathway_classification_v2.parquet"

# Vehicle age threshold for WEAR classification
WEAR_AGE_THRESHOLD = 10

# Coverage contract thresholds
MIN_TESTS_PER_YEAR = 1_000_000  # Expect at least 1M tests per year
MIN_COVERAGE_RATE = 0.50  # At least 50% of failures should be classified (non-UNCATEGORIZED)


# =============================================================================
# Input Manifest Validation
# =============================================================================

def get_test_item_files(year: int) -> List[Path]:
    """Return expected test_item files for a year, accounting for different formats."""
    base = TEST_ITEMS_DIR / str(year)

    if year in [2019, 2020]:
        # Quarterly files: dft_test_item-from-YYYY-*-to-*.csv
        pattern = f"dft_test_item-from-{year}-*.csv"
        files = list(base.glob(pattern))
        # Filter out __MACOSX files
        files = [f for f in files if '__MACOSX' not in str(f)]
        return files
    elif year == 2021:
        # Sharded files in subdirectory
        subdir = base / "test_item_2021"
        if subdir.exists():
            return list(subdir.glob("test_item_*.csv"))
        return []
    else:
        # Single file for 2022, 2023
        single = base / "test_item.csv"
        return [single] if single.exists() else []


def validate_input_manifest() -> Dict[int, List[Path]]:
    """
    Validate all required test_item files exist.
    HARD FAIL if any year is missing.
    Returns dict of year -> file paths.
    """
    logger.info("=" * 70)
    logger.info("VALIDATING INPUT MANIFEST")
    logger.info("=" * 70)
    logger.info(f"History window: {HISTORY_START_YEAR} to {HISTORY_END_YEAR}")

    manifest = {}
    missing = []

    for year in range(HISTORY_START_YEAR, HISTORY_END_YEAR + 1):
        files = get_test_item_files(year)
        if not files:
            missing.append(f"  {year}: No test_item files found in {TEST_ITEMS_DIR / str(year)}")
        else:
            manifest[year] = files
            logger.info(f"  {year}: {len(files)} file(s) found")
            for f in files[:3]:  # Show first 3
                logger.info(f"       - {f.name}")
            if len(files) > 3:
                logger.info(f"       ... and {len(files) - 3} more")

    if missing:
        error_msg = "ABORTING: Missing test_item files (no partial runs allowed):\n" + "\n".join(missing)
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)

    logger.info(f"\nManifest validated: {len(manifest)} years complete")
    return manifest


# =============================================================================
# Classification Patterns
# =============================================================================

DEFERRAL_PATTERN = re.compile(
    r'bulb|wiper|washer|screen wash|'
    r'tread depth|tread.*below|less than 1.6|'
    r'number plate|registration plate|'
    r'horn|mirror|'
    r'headlamp aim|beam.*pattern|aim.*incorrect|'
    r'fluid level|washer.*empty|reservoir|'
    r'lamp.*inoperative|lamp.*not\s|does not illuminate|not illuminat|'
    r'incorrect colour|does not face|flicker|'
    r'obscured|more than 50%|'
    r'adversely affected|faulty|'
    r'at least one does not|dipped beam|main beam|'
    r'\binsecure\b|not working|inoperative|'
    r'intensity.*reduced|reduced.*intensity|'
    r'obviously incorrectly positioned|'
    r'\bmissing\b',
    re.IGNORECASE
)

CORROSION_PATTERN = re.compile(
    r'corrod|rust|oxidat|perforat|holed|pitted|'
    r'electrochemical|ferric',
    re.IGNORECASE
)

ROAD_DAMAGE_PATTERN = re.compile(
    r'impact|pothole|kerb|bent|buckled|misalign|distort|'
    r'kinked|twisted|collapsed|deform|'
    r'fractur|cracked|broken|snapped|detach|shatter|sever|split|'
    r'likely to become detached|'
    r'inappropriately.*(repaired|modified)|repaired by welding|'
    r'modification unsafe|modified and.*weakened|inadequately repaired|'
    r'has had excessive heat applied',
    re.IGNORECASE
)

WEAR_PATTERN = re.compile(
    r'worn|wear|deteriorat|perish|aged|fatigue|'
    r'friction|degraded|thin|excessive play|slack|loose|binding|'
    r'efficiency.*below|below.*minimum|'
    r'play.*excessive|movement.*excessive|'
    r'\bleaking\b|leak|seep|'
    r'restricted.*movement|free movement',
    re.IGNORECASE
)


def classify_rfr_by_description(rfr_desc: str) -> str:
    """Classify an RFR description into a causal pathway."""
    if pd.isna(rfr_desc):
        return 'UNCATEGORIZED'

    rfr_desc = str(rfr_desc).lower()

    if DEFERRAL_PATTERN.search(rfr_desc):
        return 'DEFERRAL'
    if CORROSION_PATTERN.search(rfr_desc):
        return 'CORROSION'
    if ROAD_DAMAGE_PATTERN.search(rfr_desc):
        return 'ROAD_DAMAGE'
    if WEAR_PATTERN.search(rfr_desc):
        return 'WEAR'

    return 'UNCATEGORIZED'


def build_rfr_classification() -> pd.DataFrame:
    """Classify all RFR codes by causal pathway."""
    logger.info("\n" + "=" * 70)
    logger.info("BUILDING RFR CODE CLASSIFICATION")
    logger.info("=" * 70)

    item_detail = pd.read_csv(ITEM_DETAIL, sep='|', low_memory=False)
    logger.info(f"Loaded {len(item_detail):,} RFR records")

    rfr_unique = item_detail[['rfr_id', 'rfr_desc', 'test_item_set_section_id']].drop_duplicates('rfr_id')
    logger.info(f"Unique RFR IDs: {len(rfr_unique):,}")

    rfr_unique['causal_pathway'] = rfr_unique['rfr_desc'].apply(classify_rfr_by_description)

    pathway_counts = rfr_unique['causal_pathway'].value_counts()
    logger.info("\nRFR Classification Distribution:")
    for pathway, count in pathway_counts.items():
        pct = count / len(rfr_unique) * 100
        logger.info(f"  {pathway:<15} {count:>6,} ({pct:>5.1f}%)")

    return rfr_unique[['rfr_id', 'causal_pathway', 'test_item_set_section_id']]


def load_test_items_for_year(
    year: int,
    files: List[Path],
    rfr_classification: pd.DataFrame
) -> Tuple[pd.DataFrame, int, int]:
    """
    Load test_items for a given year from multiple files.
    Returns (failures_df, failure_count, advisory_count).

    Note: Different years have different delimiters:
    - 2019-2021: comma-separated
    - 2022-2023: pipe-separated
    """
    all_failures = []
    total_failures = 0
    total_advisories = 0

    # Determine delimiter based on year
    delimiter = ',' if year <= 2021 else '|'

    for csv_path in files:
        try:
            df = pd.read_csv(
                csv_path,
                sep=delimiter,
                usecols=['test_id', 'rfr_id', 'rfr_type_code'],
                dtype={'test_id': 'int64', 'rfr_id': 'int64', 'rfr_type_code': 'str'}
            )

            failures = df[df['rfr_type_code'].isin(['F', 'M'])].copy()
            advisories = df[df['rfr_type_code'] == 'A']

            total_failures += len(failures)
            total_advisories += len(advisories)

            if len(failures) > 0:
                failures = failures.merge(
                    rfr_classification[['rfr_id', 'causal_pathway']],
                    on='rfr_id',
                    how='left'
                )
                failures['causal_pathway'] = failures['causal_pathway'].fillna('UNCATEGORIZED')
                failures['year'] = year
                all_failures.append(failures)

        except Exception as e:
            logger.error(f"Error reading {csv_path}: {e}")
            raise

    if all_failures:
        result = pd.concat(all_failures, ignore_index=True)
    else:
        result = pd.DataFrame()

    return result, total_failures, total_advisories


def aggregate_test_level_features_duckdb(
    conn: duckdb.DuckDBPyConnection,
    failures_parquet_path: Path,
    vehicle_ages_path: Path,
    output_path: Path
) -> Dict[str, int]:
    """
    Aggregate failure-level classifications to test-level features using DuckDB.
    Writes directly to parquet to avoid memory issues.
    Returns year counts for contract validation.
    """
    logger.info("\n" + "=" * 70)
    logger.info("AGGREGATING TO TEST-LEVEL FEATURES (DuckDB)")
    logger.info("=" * 70)

    # First, apply WEAR gating and write aggregated results
    logger.info("Applying WEAR gating and aggregating...")

    query = f"""
        WITH failures_with_age AS (
            SELECT
                f.test_id,
                f.year,
                CASE
                    WHEN f.causal_pathway = 'WEAR'
                     AND COALESCE(a.vehicle_age, 999) < {WEAR_AGE_THRESHOLD}
                    THEN 'UNCATEGORIZED'
                    ELSE f.causal_pathway
                END as causal_pathway
            FROM read_parquet('{failures_parquet_path}') f
            LEFT JOIN read_parquet('{vehicle_ages_path}') a ON f.test_id = a.test_id
        ),
        pathway_counts AS (
            SELECT
                test_id,
                year,
                SUM(CASE WHEN causal_pathway = 'DEFERRAL' THEN 1 ELSE 0 END) as deferral_failure_count,
                SUM(CASE WHEN causal_pathway = 'CORROSION' THEN 1 ELSE 0 END) as corrosion_failure_count,
                SUM(CASE WHEN causal_pathway = 'ROAD_DAMAGE' THEN 1 ELSE 0 END) as road_damage_failure_count,
                SUM(CASE WHEN causal_pathway = 'WEAR' THEN 1 ELSE 0 END) as wear_failure_count,
                SUM(CASE WHEN causal_pathway = 'UNCATEGORIZED' THEN 1 ELSE 0 END) as uncategorized_failure_count,
                COUNT(*) as total_classified_failures
            FROM failures_with_age
            GROUP BY test_id, year
        )
        SELECT
            test_id,
            year,
            deferral_failure_count,
            corrosion_failure_count,
            road_damage_failure_count,
            wear_failure_count,
            uncategorized_failure_count,
            total_classified_failures,
            CASE WHEN deferral_failure_count > 0 THEN 1 ELSE 0 END as has_deferral_failure,
            CASE WHEN corrosion_failure_count > 0 THEN 1 ELSE 0 END as has_corrosion_failure,
            CASE WHEN road_damage_failure_count > 0 THEN 1 ELSE 0 END as has_road_damage_failure,
            CASE WHEN wear_failure_count > 0 THEN 1 ELSE 0 END as has_wear_failure,
            -- Primary pathway (most common)
            CASE
                WHEN deferral_failure_count >= corrosion_failure_count
                 AND deferral_failure_count >= road_damage_failure_count
                 AND deferral_failure_count >= wear_failure_count
                 AND deferral_failure_count >= uncategorized_failure_count
                THEN 'DEFERRAL'
                WHEN corrosion_failure_count >= road_damage_failure_count
                 AND corrosion_failure_count >= wear_failure_count
                 AND corrosion_failure_count >= uncategorized_failure_count
                THEN 'CORROSION'
                WHEN road_damage_failure_count >= wear_failure_count
                 AND road_damage_failure_count >= uncategorized_failure_count
                THEN 'ROAD_DAMAGE'
                WHEN wear_failure_count >= uncategorized_failure_count
                THEN 'WEAR'
                ELSE 'UNCATEGORIZED'
            END as causal_pathway_primary
        FROM pathway_counts
    """

    # Write directly to parquet
    conn.execute(f"COPY ({query}) TO '{output_path}' (FORMAT PARQUET)")

    # Get year counts for validation
    year_counts = conn.execute(f"""
        SELECT year, COUNT(*) as cnt
        FROM read_parquet('{output_path}')
        GROUP BY year
        ORDER BY year
    """).fetchdf()

    year_dict = dict(zip(year_counts['year'].astype(int), year_counts['cnt']))

    total = sum(year_dict.values())
    logger.info(f"Generated features for {total:,} tests")
    for year, cnt in sorted(year_dict.items()):
        logger.info(f"  {year}: {cnt:>12,}")

    return year_dict


def run_coverage_contract_assertions(
    result: pd.DataFrame,
    year_counts: Dict[int, int],
    conn: duckdb.DuckDBPyConnection
) -> bool:
    """
    Run coverage contract assertions. Returns True if all pass.
    """
    logger.info("\n" + "=" * 70)
    logger.info("COVERAGE CONTRACT ASSERTIONS")
    logger.info("=" * 70)

    all_passed = True

    # 1. Check year range
    min_year = result['year'].min()
    max_year = result['year'].max()

    logger.info(f"\n1. Year range check:")
    logger.info(f"   Expected: {HISTORY_START_YEAR} to {HISTORY_END_YEAR}")
    logger.info(f"   Actual:   {min_year} to {max_year}")

    if min_year != HISTORY_START_YEAR or max_year != HISTORY_END_YEAR:
        logger.error("   FAILED: Year range incomplete!")
        all_passed = False
    else:
        logger.info("   PASSED")

    # 2. Check row counts by year
    logger.info(f"\n2. Row counts by year (min required: {MIN_TESTS_PER_YEAR:,}):")
    for year in range(HISTORY_START_YEAR, HISTORY_END_YEAR + 1):
        count = year_counts.get(year, 0)
        status = "PASSED" if count >= MIN_TESTS_PER_YEAR else "FAILED"
        logger.info(f"   {year}: {count:>12,} tests - {status}")
        if count < MIN_TESTS_PER_YEAR:
            all_passed = False

    # 3. Check unique test_id count
    logger.info(f"\n3. Unique test_id count:")
    unique_tests = result['test_id'].nunique()
    total_rows = len(result)
    logger.info(f"   Unique test_ids: {unique_tests:,}")
    logger.info(f"   Total rows: {total_rows:,}")
    if unique_tests != total_rows:
        logger.warning(f"   WARNING: Duplicate test_ids detected ({total_rows - unique_tests:,} duplicates)")

    # 4. Check classification coverage rate
    logger.info(f"\n4. Classification coverage (min required: {MIN_COVERAGE_RATE*100:.0f}%):")
    classified = (result['causal_pathway_primary'] != 'UNCATEGORIZED').sum()
    coverage_rate = classified / len(result)
    status = "PASSED" if coverage_rate >= MIN_COVERAGE_RATE else "FAILED"
    logger.info(f"   Classified (non-UNCATEGORIZED): {classified:,} / {len(result):,} ({coverage_rate*100:.1f}%) - {status}")
    if coverage_rate < MIN_COVERAGE_RATE:
        all_passed = False

    # 5. Check dev/OOT coverage
    logger.info(f"\n5. Dev/OOT set coverage:")
    try:
        dev_ids = conn.execute(f"SELECT test_id FROM read_parquet('{DEV_SET}')").fetchdf()['test_id']
        oot_ids = conn.execute(f"SELECT test_id FROM read_parquet('{OOT_SET}')").fetchdf()['test_id']

        dev_coverage = result['test_id'].isin(dev_ids).sum()
        oot_coverage = result['test_id'].isin(oot_ids).sum()

        dev_pct = dev_coverage / len(dev_ids) * 100
        oot_pct = oot_coverage / len(oot_ids) * 100

        logger.info(f"   Dev set: {dev_coverage:,} / {len(dev_ids):,} ({dev_pct:.1f}%)")
        logger.info(f"   OOT set: {oot_coverage:,} / {len(oot_ids):,} ({oot_pct:.1f}%)")

        # Dev/OOT are 2022-2023, so should have ~100% coverage
        if dev_pct < 90 or oot_pct < 90:
            logger.warning("   WARNING: Low dev/OOT coverage - some tests may be missing pathway data")
    except Exception as e:
        logger.warning(f"   Could not check dev/OOT coverage: {e}")

    # Final status
    logger.info("\n" + "=" * 70)
    if all_passed:
        logger.info("ALL COVERAGE CONTRACT ASSERTIONS PASSED")
    else:
        logger.error("COVERAGE CONTRACT ASSERTIONS FAILED - Review warnings above")
    logger.info("=" * 70)

    return all_passed


def main():
    start_time = datetime.now()

    logger.info("=" * 70)
    logger.info("BUILD CAUSAL PATHWAY FEATURES V2")
    logger.info("=" * 70)
    logger.info(f"History window: {HISTORY_START_YEAR} to {HISTORY_END_YEAR}")

    conn = duckdb.connect()
    conn.execute("SET memory_limit='8GB'")

    # Step 1: Validate input manifest (HARD FAIL if missing)
    manifest = validate_input_manifest()

    # Step 2: Build RFR classification
    rfr_classification = build_rfr_classification()
    rfr_classification.to_parquet(RFR_CLASSIFICATION_FILE, index=False)
    logger.info(f"\nSaved RFR classification to: {RFR_CLASSIFICATION_FILE}")

    # Step 3: Load test items for all years
    logger.info("\n" + "=" * 70)
    logger.info("LOADING TEST ITEMS BY YEAR")
    logger.info("=" * 70)

    all_failures = []
    year_counts = {}

    for year in range(HISTORY_START_YEAR, HISTORY_END_YEAR + 1):
        files = manifest[year]
        failures_df, failure_count, advisory_count = load_test_items_for_year(
            year, files, rfr_classification
        )
        logger.info(f"  {year}: {failure_count:>12,} failures, {advisory_count:>12,} advisories")

        if len(failures_df) > 0:
            all_failures.append(failures_df)
            year_counts[year] = failures_df['test_id'].nunique()

    if not all_failures:
        logger.error("No failure data loaded!")
        return

    all_failures_df = pd.concat(all_failures, ignore_index=True)
    logger.info(f"\nTotal failure items loaded: {len(all_failures_df):,}")
    logger.info(f"Unique tests with failures: {all_failures_df['test_id'].nunique():,}")

    # Show pathway distribution
    pathway_dist = all_failures_df['causal_pathway'].value_counts()
    logger.info("\nPathway distribution (all failure items):")
    for pathway, count in pathway_dist.items():
        pct = count / len(all_failures_df) * 100
        logger.info(f"  {pathway:<15} {count:>12,} ({pct:>5.1f}%)")

    # Step 4: Save failures to temporary parquet for DuckDB processing
    logger.info("\n" + "=" * 70)
    logger.info("SAVING INTERMEDIATE FILES FOR DUCKDB PROCESSING")
    logger.info("=" * 70)

    failures_temp = WORK_DIR / "temp_failures_v2.parquet"
    ages_temp = WORK_DIR / "temp_ages_v2.parquet"

    logger.info(f"Writing {len(all_failures_df):,} failure items to temporary parquet...")
    all_failures_df[['test_id', 'year', 'causal_pathway']].to_parquet(failures_temp, index=False)
    logger.info(f"  Saved to: {failures_temp}")

    # Free memory
    del all_failures_df

    # Step 5: Load vehicle ages for WEAR gating
    logger.info("\nLoading vehicle ages for WEAR gating...")
    try:
        conn.execute(f"""
            COPY (
                SELECT test_id,
                       EXTRACT(YEAR FROM test_date) - EXTRACT(YEAR FROM first_use_date) as vehicle_age
                FROM read_parquet('{DEV_SET}')
                WHERE first_use_date IS NOT NULL
                UNION ALL
                SELECT test_id,
                       EXTRACT(YEAR FROM test_date) - EXTRACT(YEAR FROM first_use_date) as vehicle_age
                FROM read_parquet('{OOT_SET}')
                WHERE first_use_date IS NOT NULL
            ) TO '{ages_temp}' (FORMAT PARQUET)
        """)
        ages_count = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{ages_temp}')").fetchone()[0]
        logger.info(f"  Saved ages for {ages_count:,} dev+OOT tests")
        logger.info("  Note: WEAR gating only applies to dev/OOT tests")
    except Exception as e:
        logger.warning(f"Could not load vehicle ages: {e}")
        # Create empty ages file
        pd.DataFrame({'test_id': [], 'vehicle_age': []}).to_parquet(ages_temp, index=False)

    # Step 6: Aggregate to test-level features using DuckDB
    year_counts = aggregate_test_level_features_duckdb(
        conn, failures_temp, ages_temp, OUTPUT_FILE
    )

    logger.info(f"\nSaved to: {OUTPUT_FILE}")

    # Cleanup temp files
    failures_temp.unlink()
    ages_temp.unlink()
    logger.info("Cleaned up temporary files")

    # Step 7: Run coverage contract assertions
    # Read output for validation (this is small enough to fit in memory)
    result = conn.execute(f"SELECT * FROM read_parquet('{OUTPUT_FILE}')").fetchdf()
    contracts_passed = run_coverage_contract_assertions(result, year_counts, conn)

    # Step 8: Diagnostics
    logger.info("\n" + "=" * 70)
    logger.info("DIAGNOSTICS")
    logger.info("=" * 70)

    # Primary pathway distribution
    primary_dist = result['causal_pathway_primary'].value_counts()
    logger.info("\nPrimary pathway distribution (test-level):")
    for pathway, count in primary_dist.items():
        pct = count / len(result) * 100
        logger.info(f"  {pathway:<15} {count:>10,} ({pct:>5.1f}%)")

    # Year distribution
    logger.info("\nTests by year:")
    year_dist = result['year'].value_counts().sort_index()
    for year, count in year_dist.items():
        logger.info(f"  {int(year)}: {count:>10,}")

    conn.close()

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"\nCompleted in {elapsed:.1f}s")
    logger.info("=" * 70)

    if not contracts_passed:
        logger.warning("\nWARNING: Some coverage contracts failed - review output carefully")


if __name__ == "__main__":
    main()
