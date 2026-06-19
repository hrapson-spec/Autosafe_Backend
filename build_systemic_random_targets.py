#!/usr/bin/env python3
"""
Build Systemic vs Random Failure Target Labels
===============================================

Creates test-level binary labels distinguishing:
- SYSTEMIC: Wear/age-driven failures (predictable) - includes corrosion
- RANDOM: Stochastic breakage (unpredictable)

Pipeline:
  test_items(test_id, rfr_id)
  → JOIN item_detail(rfr_id, rfr_desc, section_id)
  → CLASSIFY rfr_desc → failure_mode (SYSTEMIC/RANDOM)
  → AGGREGATE by test_id

Output: filtered_failure_targets.parquet

Created: 2026-01-08
"""

import pandas as pd
import duckdb
import re
from pathlib import Path
from collections import defaultdict

# Paths
PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
ITEM_DETAIL = PROJECT_ROOT / "item_detail.csv"
TEST_ITEMS_DIR = PROJECT_ROOT / "test_items"
OUTPUT_FILE = PROJECT_ROOT / "filtered_failure_targets.parquet"

# Regex patterns for failure mode classification
# SYSTEMIC: Gradual degradation (wear, age, corrosion, functional decline) - predictable
SYSTEMIC_PATTERN = re.compile(
    # Physical wear patterns
    r'bush|play|worn|ball.?joint|movement|mounting|rubber|deteriorat|perish|clearance|'
    r'wear|corrod|rust|age|oxidat|weaken|perforat|holed|'
    r'stiff|notchy|rough|leak|seep|porous|eroded|degraded|thin|excessive|insufficient|'
    r'faded|discolour|misted|cloudy|opaque|pit|erosion|stretch|slack|loose|binding|'
    # Functional degradation (often age/use-related)
    r'not working|inoperative|faulty|malfunctioning|not functioning|not illuminat|'
    r'intensity.*(reduced|significantly)|reduced.*(intensity|efficiency)|efficiency.*(below|less)|'
    r'below.*(minimum|requirements)|contaminated|fluid level|less than.*mm|'
    r'flickers|flickering|not releasing|not holding|'
    # Position/mounting degradation
    r'displaced|deflated|incorrectly positioned|'
    # Electrical function loss
    r'warning lamp|does not illuminate|light source.*not|'
    # Lights/electrical - interference and functional issues
    r'adversely affected|obscured|not able to be operated|does not operate|'
    r'flashing.*(less than|more than)|not in good working order|'
    # Emissions - engine wear indicators
    r'emits.*(smoke|visible)|emissions.*exceed|idle speed.*too|'
    # Visibility - degradation
    r'does not clear|not operating|incapable of being adjusted|seriously obscured',
    re.IGNORECASE
)

# RANDOM: Sudden structural failures - stochastic/unpredictable
RANDOM_PATTERN = re.compile(
    # Structural failures
    r'fractur|broken|snapped|crack|detach|shatter|sever|split|bent|deform|misalign|'
    r'impact|sudden|collapsed|buckled|twisted|torn|ripped|puncture|'
    r'missing|insecure|disconnected|not attached|separated|'
    # Damage patterns
    r'kinked|knotted|bulging|chaffing|fouled|fouling|'
    # Repair/modification defects (often from prior damage)
    r'inappropriately.*(repaired|modified)|repaired by welding|modification unsafe|'
    r'inadequately.*(repaired|mounted)|defective|obviously.*(modified|repaired)|'
    # Position/aim errors (installation or impact related)
    r'does not face|too far to the|too (low|high)|incorrect.*(colour|position)|'
    # Seatbelt installation issues
    r'stitching.*not|stitching.*(incomplete|frayed)|locking mechanism|'
    r'webbing.*(cut|reworked)|unsuitable|cannot be secured|'
    # Tampering
    r'tampering|evidence of',
    re.IGNORECASE
)


def classify_rfr(rfr_desc: str) -> str:
    """
    Classify RFR description into failure mode.

    Priority: Check RANDOM first (more specific), then SYSTEMIC
    """
    if pd.isna(rfr_desc):
        return 'UNCATEGORIZED'

    rfr_desc = str(rfr_desc)

    # Check RANDOM first (sudden breakage patterns)
    if RANDOM_PATTERN.search(rfr_desc):
        return 'RANDOM'

    # Check SYSTEMIC (wear/age patterns)
    if SYSTEMIC_PATTERN.search(rfr_desc):
        return 'SYSTEMIC'

    return 'UNCATEGORIZED'


def main():
    print("=" * 70)
    print("BUILD SYSTEMIC VS RANDOM FAILURE TARGETS")
    print("=" * 70)

    # =========================================================================
    # Step 1: Load and classify ALL item_detail RFR codes
    # =========================================================================
    print("\n[Step 1] Loading item_detail and classifying RFR codes...")

    item_detail = pd.read_csv(ITEM_DETAIL, sep='|', low_memory=False)
    print(f"  Total RFR codes: {len(item_detail):,}")

    # Count unique RFR IDs
    unique_rfr_ids = item_detail['rfr_id'].nunique()
    print(f"  Unique RFR IDs: {unique_rfr_ids:,}")

    # Classify each RFR
    item_detail['failure_mode'] = item_detail['rfr_desc'].apply(classify_rfr)

    # Create rfr_id -> failure_mode mapping (use first occurrence for duplicates)
    rfr_to_mode = {}
    for _, row in item_detail[['rfr_id', 'failure_mode']].drop_duplicates('rfr_id').iterrows():
        rfr_to_mode[row['rfr_id']] = row['failure_mode']

    # Show classification distribution
    print("\n  Classification distribution (by RFR code):")
    mode_counts = item_detail['failure_mode'].value_counts()
    total = len(item_detail)
    for mode, count in mode_counts.items():
        pct = count / total * 100
        print(f"    {mode:<15} {count:>6,} ({pct:>5.1f}%)")

    # Calculate coverage
    categorized = total - mode_counts.get('UNCATEGORIZED', 0)
    coverage = categorized / total * 100
    print(f"\n  Classification coverage: {coverage:.1f}%")

    if coverage < 80:
        print(f"  ⚠️  Warning: Coverage {coverage:.1f}% is below 80% target")
    else:
        print(f"  ✓ Coverage meets 80% threshold")

    # Section ID to system mapping (from system_map_v1.py)
    from system_map_v1 import SECTION_TO_SYSTEM, get_system

    # Show breakdown by system
    print("\n  Classification by system:")
    item_detail['system'] = item_detail['test_item_set_section_id'].apply(get_system)
    for system in sorted(item_detail['system'].unique()):
        sys_df = item_detail[item_detail['system'] == system]
        sys_modes = sys_df['failure_mode'].value_counts()
        systemic = sys_modes.get('SYSTEMIC', 0)
        random = sys_modes.get('RANDOM', 0)
        uncat = sys_modes.get('UNCATEGORIZED', 0)
        sys_total = len(sys_df)
        print(f"    {system:25s}: SYSTEMIC={systemic:>4} RANDOM={random:>4} UNCAT={uncat:>4} (total {sys_total:>5})")

    # Get all rfr_ids that are NOT uncategorized
    categorized_rfr_ids = set(item_detail[item_detail['failure_mode'] != 'UNCATEGORIZED']['rfr_id'])
    print(f"\n  Categorized RFR IDs to match: {len(categorized_rfr_ids):,}")

    # =========================================================================
    # Step 2: Process test_items files
    # =========================================================================
    print("\n[Step 2] Processing test_items files...")

    # Accumulate test-level failure modes
    test_modes = defaultdict(lambda: {'SYSTEMIC': 0, 'RANDOM': 0})
    total_items = 0
    matched_items = 0

    for year in ['2019', '2020', '2021', '2022', '2023', '2024']:
        csv_file = TEST_ITEMS_DIR / year / "test_item.csv"
        if not csv_file.exists():
            print(f"  Warning: {csv_file} not found")
            continue

        print(f"\n  Processing {year}...")

        # Read in chunks to manage memory
        chunks = pd.read_csv(
            csv_file,
            sep='|',
            usecols=['test_id', 'rfr_id'],
            dtype={'test_id': 'int64', 'rfr_id': 'int64'},
            chunksize=2_000_000
        )

        chunk_count = 0
        year_items = 0
        year_matched = 0

        for chunk in chunks:
            chunk_count += 1
            total_items += len(chunk)
            year_items += len(chunk)

            # Filter to categorized rfr_ids only
            categorized_items = chunk[chunk['rfr_id'].isin(categorized_rfr_ids)]
            matched_items += len(categorized_items)
            year_matched += len(categorized_items)

            # Map to failure modes and aggregate
            for _, row in categorized_items.iterrows():
                test_id = row['test_id']
                rfr_id = row['rfr_id']
                mode = rfr_to_mode.get(rfr_id, 'UNCATEGORIZED')
                if mode != 'UNCATEGORIZED':
                    test_modes[test_id][mode] += 1

            if chunk_count % 5 == 0:
                print(f"    Processed {chunk_count * 2}M items, {len(test_modes):,} tests with failures")

        print(f"    Year {year}: {year_items:,} items, {year_matched:,} matched ({year_matched/year_items*100:.1f}%)")

    print(f"\n  Total items processed: {total_items:,}")
    print(f"  Items matched to SYSTEMIC/RANDOM: {matched_items:,} ({matched_items/total_items*100:.1f}%)")
    print(f"  Tests with categorized failures: {len(test_modes):,}")

    # =========================================================================
    # Step 3: Convert to DataFrame with binary flags
    # =========================================================================
    print("\n[Step 3] Creating target DataFrame...")

    records = []
    for test_id, modes in test_modes.items():
        records.append({
            'test_id': test_id,
            'has_systemic_failure': 1 if modes['SYSTEMIC'] > 0 else 0,
            'has_random_failure': 1 if modes['RANDOM'] > 0 else 0,
            'systemic_count': modes['SYSTEMIC'],
            'random_count': modes['RANDOM'],
            'has_any_failure': 1,  # All rows have at least one categorized defect
        })

    targets_df = pd.DataFrame(records)

    # Show target distribution
    print("\n  Target distribution (tests with at least one):")
    print(f"    has_systemic_failure: {targets_df['has_systemic_failure'].sum():,} ({targets_df['has_systemic_failure'].mean()*100:.1f}%)")
    print(f"    has_random_failure:   {targets_df['has_random_failure'].sum():,} ({targets_df['has_random_failure'].mean()*100:.1f}%)")

    # Overlap analysis
    both = ((targets_df['has_systemic_failure'] == 1) & (targets_df['has_random_failure'] == 1)).sum()
    systemic_only = ((targets_df['has_systemic_failure'] == 1) & (targets_df['has_random_failure'] == 0)).sum()
    random_only = ((targets_df['has_systemic_failure'] == 0) & (targets_df['has_random_failure'] == 1)).sum()

    print(f"\n  Overlap analysis:")
    print(f"    SYSTEMIC only:  {systemic_only:,} ({systemic_only/len(targets_df)*100:.1f}%)")
    print(f"    RANDOM only:    {random_only:,} ({random_only/len(targets_df)*100:.1f}%)")
    print(f"    Both:           {both:,} ({both/len(targets_df)*100:.1f}%)")

    # =========================================================================
    # Step 4: Save to parquet
    # =========================================================================
    print("\n[Step 4] Saving to parquet...")

    targets_df.to_parquet(OUTPUT_FILE, index=False)
    print(f"  Saved to: {OUTPUT_FILE}")
    print(f"  Total rows: {len(targets_df):,}")

    # =========================================================================
    # Step 5: Verify coverage with dev/OOT sets
    # =========================================================================
    print("\n[Step 5] Checking coverage with training data...")

    con = duckdb.connect()
    con.execute("SET memory_limit='4GB'")

    DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set.parquet"
    OOT_SET = PROJECT_ROOT / "stratified_samples/oot_test_set.parquet"

    target_ids = set(targets_df['test_id'])

    # Check dev set
    if DEV_SET.exists():
        dev_ids = set(con.execute(f"SELECT test_id FROM read_parquet('{DEV_SET}')").df()['test_id'])
        dev_count = len(dev_ids)
        dev_overlap = len(dev_ids.intersection(target_ids))
        print(f"  Dev set:  {dev_count:,} tests, {dev_overlap:,} with targets ({dev_overlap/dev_count*100:.1f}%)")
    else:
        print(f"  Dev set not found: {DEV_SET}")

    # Check OOT set
    if OOT_SET.exists():
        oot_ids = set(con.execute(f"SELECT test_id FROM read_parquet('{OOT_SET}')").df()['test_id'])
        oot_count = len(oot_ids)
        oot_overlap = len(oot_ids.intersection(target_ids))
        print(f"  OOT set:  {oot_count:,} tests, {oot_overlap:,} with targets ({oot_overlap/oot_count*100:.1f}%)")
    else:
        print(f"  OOT set not found: {OOT_SET}")

    con.close()

    # =========================================================================
    # Summary
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"""
  Targets built successfully!

  Output: {OUTPUT_FILE}
  Tests with categorized failures: {len(targets_df):,}

  Target prevalence (among tests with defects):
    SYSTEMIC:  {targets_df['has_systemic_failure'].mean()*100:.1f}%
    RANDOM:    {targets_df['has_random_failure'].mean()*100:.1f}%

  Overlap:
    SYSTEMIC only:  {systemic_only:,} ({systemic_only/len(targets_df)*100:.1f}%)
    RANDOM only:    {random_only:,} ({random_only/len(targets_df)*100:.1f}%)
    Both:           {both:,} ({both/len(targets_df)*100:.1f}%)

  Next: Run train_systemic_model.py to train and compare models
    """)
    print("=" * 70)


if __name__ == "__main__":
    main()
