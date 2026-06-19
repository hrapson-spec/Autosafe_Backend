#!/usr/bin/env python3
"""
Build Causal Pathway Features
=============================

Classifies MOT failures by their underlying CAUSE rather than mechanism.
Based on "Anatomy of MOT Failure" document analysis.

Causal Pathways:
- APATHY: Owner ignored prior advisory (same anchor_3 code advisory → failure)
- DEFERRAL: Owner skipped basic pre-MOT checks (bulbs, wipers, tread, headlamp aim)
- CORROSION: Salt/environmental exposure (corroded, rust, perforated)
- ROAD_DAMAGE: Pothole/impact damage (bent, buckled, misaligned)
- WEAR: Natural component lifecycle (worn, deteriorated on vehicles >10yr)
- UNCATEGORIZED: Cannot determine cause

Output: causal_pathway_features.parquet

Created: 2026-01-09
"""

import pandas as pd
import numpy as np
import duckdb
import re
import logging
from pathlib import Path
from collections import defaultdict
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("build_causal_pathway_features.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================

PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
WORK_DIR = Path("/Users/henrirapson/autosafe_work")

ITEM_DETAIL = PROJECT_ROOT / "item_detail.csv"
TEST_ITEMS_DIR = PROJECT_ROOT / "test_items"
CYCLE_HISTORY = PROJECT_ROOT / "cycle_first_with_history.parquet"
DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set.parquet"
OOT_SET = PROJECT_ROOT / "stratified_samples/oot_test_set.parquet"

OUTPUT_FILE = WORK_DIR / "causal_pathway_features.parquet"
RFR_CLASSIFICATION_FILE = WORK_DIR / "rfr_causal_pathway_classification.parquet"

# Vehicle age threshold for WEAR classification (from document Section 3.1)
WEAR_AGE_THRESHOLD = 10

# =============================================================================
# Classification Patterns (from Plan - aligned with "Anatomy of MOT Failure")
# =============================================================================

# DEFERRAL: Items document identifies as "easily preventable" pre-MOT checks
# Document Section 4.2: "inoperative bulbs, worn-out windscreen wipers, empty screen wash reservoirs"
# Document Section 2.1: "incorrect headlamp aim" is single most frequent failure reason
# Extended to include common lighting failures (Section 1) - all easily preventable
DEFERRAL_PATTERN = re.compile(
    # Original items
    r'bulb|wiper|washer|screen wash|'
    r'tread depth|tread.*below|less than 1.6|'
    r'number plate|registration plate|'
    r'horn|mirror|'
    r'headlamp aim|beam.*pattern|aim.*incorrect|'
    r'fluid level|washer.*empty|reservoir|'
    # Lighting failures (Section 1) - all preventable with pre-MOT check
    r'lamp.*inoperative|lamp.*not\s|does not illuminate|not illuminat|'
    r'incorrect colour|does not face|flicker|'
    r'obscured|more than 50%|'
    r'adversely affected|faulty|'
    r'at least one does not|dipped beam|main beam|'
    # High-frequency uncategorized that are DEFERRAL
    r'\binsecure\b|not working|inoperative|'
    r'intensity.*reduced|reduced.*intensity|'
    r'obviously incorrectly positioned|'
    # Missing items (generally DEFERRAL)
    r'\bmissing\b',
    re.IGNORECASE
)

# CORROSION: Document Section 2.2 - "corrosion-assisted fatigue", road salt exposure
# Check this BEFORE ROAD_DAMAGE to correctly classify corroded springs
CORROSION_PATTERN = re.compile(
    r'corrod|rust|oxidat|perforat|holed|pitted|'
    r'electrochemical|ferric',
    re.IGNORECASE
)

# ROAD_DAMAGE: Document Section 5.1 - pothole/impact damage
# Only if NO corrosion indicators present
# Includes structural failures and inappropriate repairs (often from prior damage)
ROAD_DAMAGE_PATTERN = re.compile(
    r'impact|pothole|kerb|bent|buckled|misalign|distort|'
    r'kinked|twisted|collapsed|deform|'
    # Structural failures
    r'fractur|cracked|broken|snapped|detach|shatter|sever|split|'
    r'likely to become detached|'
    # Inappropriate repairs (often from prior damage)
    r'inappropriately.*(repaired|modified)|repaired by welding|'
    r'modification unsafe|modified and.*weakened|inadequately repaired|'
    r'has had excessive heat applied',
    re.IGNORECASE
)

# WEAR: Natural lifecycle degradation
# Document Section 2.3: "brake pads and discs have a finite lifespan"
WEAR_PATTERN = re.compile(
    r'worn|wear|deteriorat|perish|aged|fatigue|'
    r'friction|degraded|thin|excessive play|slack|loose|binding|'
    r'efficiency.*below|below.*minimum|'
    r'play.*excessive|movement.*excessive|'
    # Leaking is usually seals/gaskets wearing out
    r'\bleaking\b|leak|seep|'
    # Restricted movement often from wear
    r'restricted.*movement|free movement',
    re.IGNORECASE
)

# Section ID mapping for context
SECTION_NAMES = {
    1: 'LIGHTING',
    2: 'STEERING_SUSPENSION',
    3: 'BRAKING',
    4: 'TYRES_WHEELS',
    5: 'BODY_STRUCTURE',
    6: 'EXHAUST_EMISSIONS',
    7: 'SEATBELTS',
    8: 'OTHER'
}


def classify_rfr_by_description(rfr_desc: str, section_id: int = None) -> str:
    """
    Classify an RFR description into a causal pathway.

    Priority (highest to lowest):
    1. DEFERRAL - easily preventable items
    2. CORROSION - check BEFORE road damage (per document)
    3. ROAD_DAMAGE - impact/pothole damage (only if no corrosion)
    4. WEAR - natural lifecycle (will be gated by vehicle age later)
    5. UNCATEGORIZED - fallback

    Note: APATHY is detected separately via prior advisory matching.
    """
    if pd.isna(rfr_desc):
        return 'UNCATEGORIZED'

    rfr_desc = str(rfr_desc).lower()

    # 1. DEFERRAL - easily preventable items
    if DEFERRAL_PATTERN.search(rfr_desc):
        return 'DEFERRAL'

    # 2. CORROSION - salt/environmental (check BEFORE road damage)
    if CORROSION_PATTERN.search(rfr_desc):
        return 'CORROSION'

    # 3. ROAD_DAMAGE - pothole/impact (only if no corrosion indicators)
    if ROAD_DAMAGE_PATTERN.search(rfr_desc):
        return 'ROAD_DAMAGE'

    # 4. WEAR - natural lifecycle
    if WEAR_PATTERN.search(rfr_desc):
        return 'WEAR'

    # 5. UNCATEGORIZED
    return 'UNCATEGORIZED'


def build_rfr_classification() -> pd.DataFrame:
    """
    Classify all RFR codes by causal pathway.
    Returns DataFrame with rfr_id -> causal_pathway mapping.
    """
    logger.info("Building RFR code classification...")

    # Load item_detail
    item_detail = pd.read_csv(ITEM_DETAIL, sep='|', low_memory=False)
    logger.info(f"  Loaded {len(item_detail):,} RFR records")

    # Classify each unique rfr_id
    rfr_unique = item_detail[['rfr_id', 'rfr_desc', 'test_item_set_section_id']].drop_duplicates('rfr_id')
    logger.info(f"  Unique RFR IDs: {len(rfr_unique):,}")

    # Apply classification
    rfr_unique['causal_pathway'] = rfr_unique.apply(
        lambda row: classify_rfr_by_description(row['rfr_desc'], row['test_item_set_section_id']),
        axis=1
    )

    # Show distribution
    pathway_counts = rfr_unique['causal_pathway'].value_counts()
    logger.info("\n  RFR Classification Distribution:")
    for pathway, count in pathway_counts.items():
        pct = count / len(rfr_unique) * 100
        logger.info(f"    {pathway:<15} {count:>6,} ({pct:>5.1f}%)")

    # Show examples for each pathway
    logger.info("\n  Example RFR descriptions per pathway:")
    for pathway in ['DEFERRAL', 'CORROSION', 'ROAD_DAMAGE', 'WEAR', 'UNCATEGORIZED']:
        examples = rfr_unique[rfr_unique['causal_pathway'] == pathway]['rfr_desc'].head(3).tolist()
        logger.info(f"    {pathway}:")
        for ex in examples:
            logger.info(f"      - {ex[:60]}...")

    return rfr_unique[['rfr_id', 'causal_pathway', 'test_item_set_section_id']]


def load_test_items_with_pathways(rfr_classification: pd.DataFrame, year: str) -> pd.DataFrame:
    """
    Load test_items for a given year and join with causal pathway classification.
    Returns DataFrame with test_id, rfr_id, rfr_type_code, causal_pathway.
    """
    csv_path = TEST_ITEMS_DIR / year / "test_item.csv"
    if not csv_path.exists():
        logger.warning(f"  {csv_path} not found, skipping")
        return pd.DataFrame()

    # Load test_items
    df = pd.read_csv(
        csv_path,
        sep='|',
        usecols=['test_id', 'rfr_id', 'rfr_type_code'],
        dtype={'test_id': 'int64', 'rfr_id': 'int64', 'rfr_type_code': 'str'}
    )

    # Filter to failures only (F = Failure, M = Minor defect that causes failure)
    # Advisory (A) is handled separately for APATHY detection
    failures = df[df['rfr_type_code'].isin(['F', 'M'])].copy()
    advisories = df[df['rfr_type_code'] == 'A'].copy()

    # Join with classification
    failures = failures.merge(
        rfr_classification[['rfr_id', 'causal_pathway']],
        on='rfr_id',
        how='left'
    )
    failures['causal_pathway'] = failures['causal_pathway'].fillna('UNCATEGORIZED')

    logger.info(f"    {year}: {len(failures):,} failures, {len(advisories):,} advisories")

    return failures, advisories


def build_apathy_detection(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """
    Detect APATHY pathway: prior advisory in same anchor_3 code → current failure.

    Uses cycle_first_with_history.parquet which has prev_cycle_test_id.
    """
    logger.info("\nBuilding APATHY detection (prior advisory → current failure)...")

    # We need to:
    # 1. Get advisories from t-1 (prev_cycle_test_id)
    # 2. Get failures from t (test_id)
    # 3. Match on anchor_3 code (via rfr_id → anchor mapping)

    # For now, use a simplified approach: match on rfr_id directly
    # (anchor_3 matching is in code_level_apathy_features.py)

    # Load cycle history to get prev_cycle_test_id linkage
    cycle_query = f"""
        SELECT test_id, prev_cycle_test_id, vehicle_id
        FROM read_parquet('{CYCLE_HISTORY}')
        WHERE prev_cycle_test_id IS NOT NULL
    """
    cycle_df = conn.execute(cycle_query).fetchdf()
    logger.info(f"  Tests with prior cycle: {len(cycle_df):,}")

    return cycle_df


def aggregate_test_level_features(
    all_failures: pd.DataFrame,
    apathy_tests: set,
    vehicle_ages: pd.DataFrame
) -> pd.DataFrame:
    """
    Aggregate failure-level classifications to test-level features.

    Features:
    - causal_pathway_primary: Most common pathway for this test
    - {pathway}_failure_count: Count per pathway
    - has_{pathway}_failure: Binary flags
    - apathy_rate: apathy_count / total_failures (smoothed)
    """
    logger.info("\nAggregating to test-level features...")

    # Join with vehicle ages for WEAR gating
    if vehicle_ages is not None and len(vehicle_ages) > 0:
        all_failures = all_failures.merge(
            vehicle_ages[['test_id', 'vehicle_age']],
            on='test_id',
            how='left'
        )
        # Gate WEAR by vehicle age
        mask = (all_failures['causal_pathway'] == 'WEAR') & \
               (all_failures['vehicle_age'].fillna(0) < WEAR_AGE_THRESHOLD)
        all_failures.loc[mask, 'causal_pathway'] = 'UNCATEGORIZED'
        logger.info(f"  Re-classified {mask.sum():,} WEAR items to UNCATEGORIZED (vehicle age < {WEAR_AGE_THRESHOLD})")

    # Override to APATHY if test_id is in apathy_tests
    # (APATHY takes highest priority)
    all_failures['is_apathy_test'] = all_failures['test_id'].isin(apathy_tests)

    # Group by test_id and compute aggregates
    pathway_counts = all_failures.groupby(['test_id', 'causal_pathway']).size().unstack(fill_value=0)

    # Ensure all pathways exist as columns
    for pathway in ['APATHY', 'DEFERRAL', 'CORROSION', 'ROAD_DAMAGE', 'WEAR', 'UNCATEGORIZED']:
        if pathway not in pathway_counts.columns:
            pathway_counts[pathway] = 0

    # Add APATHY count based on apathy_tests set
    apathy_override = all_failures.groupby('test_id')['is_apathy_test'].any()
    pathway_counts['APATHY'] = pathway_counts['APATHY'] + apathy_override.astype(int)

    # Compute features
    result = pd.DataFrame(index=pathway_counts.index)

    # Counts per pathway
    result['apathy_failure_count'] = pathway_counts['APATHY']
    result['deferral_failure_count'] = pathway_counts['DEFERRAL']
    result['corrosion_failure_count'] = pathway_counts['CORROSION']
    result['road_damage_failure_count'] = pathway_counts['ROAD_DAMAGE']
    result['wear_failure_count'] = pathway_counts['WEAR']
    result['uncategorized_failure_count'] = pathway_counts['UNCATEGORIZED']

    # Binary flags
    result['has_apathy_failure'] = (result['apathy_failure_count'] > 0).astype(int)
    result['has_deferral_failure'] = (result['deferral_failure_count'] > 0).astype(int)
    result['has_corrosion_failure'] = (result['corrosion_failure_count'] > 0).astype(int)
    result['has_road_damage_failure'] = (result['road_damage_failure_count'] > 0).astype(int)
    result['has_wear_failure'] = (result['wear_failure_count'] > 0).astype(int)

    # Total failures
    result['total_classified_failures'] = pathway_counts.sum(axis=1)

    # Primary pathway (most common)
    result['causal_pathway_primary'] = pathway_counts.idxmax(axis=1)

    # Smoothed rates (with Laplace smoothing: alpha=1, beta=5)
    alpha, beta = 1, 5
    result['apathy_rate'] = (result['apathy_failure_count'] + alpha) / (result['total_classified_failures'] + alpha + beta)
    result['deferral_rate'] = (result['deferral_failure_count'] + alpha) / (result['total_classified_failures'] + alpha + beta)
    result['behavioral_rate'] = (result['apathy_failure_count'] + result['deferral_failure_count'] + alpha) / (result['total_classified_failures'] + alpha + beta)

    result = result.reset_index()

    logger.info(f"  Generated features for {len(result):,} tests")

    return result


def main():
    start_time = datetime.now()

    logger.info("=" * 70)
    logger.info("BUILD CAUSAL PATHWAY FEATURES")
    logger.info("=" * 70)

    conn = duckdb.connect()
    conn.execute("SET memory_limit='4GB'")

    # Step 1: Build RFR classification
    rfr_classification = build_rfr_classification()

    # Save RFR classification for reference
    rfr_classification.to_parquet(RFR_CLASSIFICATION_FILE, index=False)
    logger.info(f"\n  Saved RFR classification to: {RFR_CLASSIFICATION_FILE}")

    # Step 2: Load test items for all years and classify
    logger.info("\nLoading test items and applying classification...")
    all_failures = []
    all_advisories = []

    for year in ['2019', '2020', '2021', '2022', '2023', '2024']:
        try:
            failures, advisories = load_test_items_with_pathways(rfr_classification, year)
            if len(failures) > 0:
                all_failures.append(failures)
            if len(advisories) > 0:
                all_advisories.append(advisories)
        except Exception as e:
            logger.warning(f"  Error processing {year}: {e}")

    if not all_failures:
        logger.error("No failure data loaded!")
        return

    all_failures_df = pd.concat(all_failures, ignore_index=True)
    logger.info(f"\n  Total failures loaded: {len(all_failures_df):,}")

    # Show pathway distribution across all failures
    pathway_dist = all_failures_df['causal_pathway'].value_counts()
    logger.info("\n  Pathway distribution (all failures):")
    for pathway, count in pathway_dist.items():
        pct = count / len(all_failures_df) * 100
        logger.info(f"    {pathway:<15} {count:>10,} ({pct:>5.1f}%)")

    # Step 3: Build APATHY detection
    cycle_df = build_apathy_detection(conn)

    # For APATHY, we need to match prior advisories to current failures
    # Simplified approach: use existing code_level_apathy_features if available
    apathy_features_path = WORK_DIR / "code_level_apathy_features.parquet"
    apathy_tests = set()

    if apathy_features_path.exists():
        logger.info(f"\n  Loading existing apathy detection from {apathy_features_path}")
        # Use DuckDB for memory-efficient loading of large file
        apathy_query = f"""
            SELECT test_id
            FROM read_parquet('{apathy_features_path}')
            WHERE has_code_level_apathy = 1
        """
        apathy_result = conn.execute(apathy_query).fetchdf()
        apathy_tests = set(apathy_result['test_id'])
        logger.info(f"  Found {len(apathy_tests):,} tests with APATHY (code-level match)")
    else:
        logger.warning(f"  {apathy_features_path} not found - APATHY detection will be limited")

    # Step 4: Load vehicle ages for WEAR gating
    logger.info("\nLoading vehicle ages for WEAR classification gating...")
    vehicle_ages = conn.execute(f"""
        SELECT test_id,
               EXTRACT(YEAR FROM test_date) - EXTRACT(YEAR FROM first_use_date) as vehicle_age
        FROM read_parquet('{DEV_SET}')
        WHERE first_use_date IS NOT NULL
    """).fetchdf()
    logger.info(f"  Loaded ages for {len(vehicle_ages):,} tests")

    # Also load from OOT
    try:
        oot_ages = conn.execute(f"""
            SELECT test_id,
                   EXTRACT(YEAR FROM test_date) - EXTRACT(YEAR FROM first_use_date) as vehicle_age
            FROM read_parquet('{OOT_SET}')
            WHERE first_use_date IS NOT NULL
        """).fetchdf()
        vehicle_ages = pd.concat([vehicle_ages, oot_ages], ignore_index=True)
        logger.info(f"  Total with ages: {len(vehicle_ages):,} tests")
    except Exception as e:
        logger.warning(f"  Could not load OOT ages: {e}")

    # Step 5: Aggregate to test-level features
    result = aggregate_test_level_features(all_failures_df, apathy_tests, vehicle_ages)

    # Step 6: Save output
    result.to_parquet(OUTPUT_FILE, index=False)
    logger.info(f"\nSaved to: {OUTPUT_FILE}")

    # Step 7: Diagnostics
    logger.info("\n" + "=" * 70)
    logger.info("DIAGNOSTICS")
    logger.info("=" * 70)

    # Primary pathway distribution
    primary_dist = result['causal_pathway_primary'].value_counts()
    logger.info("\nPrimary pathway distribution:")
    for pathway, count in primary_dist.items():
        pct = count / len(result) * 100
        logger.info(f"  {pathway:<15} {count:>10,} ({pct:>5.1f}%)")

    # Coverage stats
    behavioral_coverage = (result['has_apathy_failure'] | result['has_deferral_failure']).mean() * 100
    any_classified = (result['total_classified_failures'] > result['uncategorized_failure_count']).mean() * 100

    logger.info(f"\nCoverage:")
    logger.info(f"  Tests with APATHY or DEFERRAL: {behavioral_coverage:.1f}%")
    logger.info(f"  Tests with any classified pathway: {any_classified:.1f}%")

    # Validate against document benchmarks
    logger.info("\nDocument Benchmark Comparison:")
    logger.info(f"  Expected DEFERRAL: 25-35%, Actual: {(result['causal_pathway_primary'] == 'DEFERRAL').mean()*100:.1f}%")
    logger.info(f"  Expected WEAR: 25-35%, Actual: {(result['causal_pathway_primary'] == 'WEAR').mean()*100:.1f}%")
    logger.info(f"  Expected CORROSION: 10-15%, Actual: {(result['causal_pathway_primary'] == 'CORROSION').mean()*100:.1f}%")
    logger.info(f"  Expected APATHY: 10-20%, Actual: {(result['causal_pathway_primary'] == 'APATHY').mean()*100:.1f}%")
    logger.info(f"  Expected ROAD_DAMAGE: 5-10%, Actual: {(result['causal_pathway_primary'] == 'ROAD_DAMAGE').mean()*100:.1f}%")

    conn.close()

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"\nCompleted in {elapsed:.1f}s")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
