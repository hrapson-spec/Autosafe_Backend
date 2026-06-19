#!/usr/bin/env python3
"""
Build Suspension Sub-Target Labels
==================================

Creates test-level binary labels for suspension failure modes:
- has_wear_play: Bush, play, worn components (mileage-driven)
- has_acute_breakage: Fractured, broken, snapped (stochastic)
- has_corrosion: Rust, corroded, weakened (age-driven)

Pipeline:
  test_items(test_id, rfr_id)
  → JOIN item_detail(rfr_id, rfr_desc, section_id)
  → FILTER suspension section_ids
  → CLASSIFY → failure_mode
  → AGGREGATE by test_id

Output: suspension_subtargets.parquet

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
OUTPUT_FILE = PROJECT_ROOT / "suspension_subtargets.parquet"

# Suspension-related section IDs
SUSPENSION_SECTION_IDS = {5190, 5100, 20106, 9000, 50, 25046}

# Regex patterns for failure mode classification
PATTERNS = {
    'WEAR_PLAY': re.compile(
        r'bush|play|worn|ball.?joint|movement|mounting|rubber|deteriorat|perish|clearance|stiff|notchy|rough',
        re.IGNORECASE
    ),
    'ACUTE_BREAKAGE': re.compile(
        r'fractur|broken|snapped|crack|detach|coil.?spring|shatter|sever|split|bent|deform|misalign',
        re.IGNORECASE
    ),
    'CORROSION': re.compile(
        r'corrod|rust|weaken|perforat|subframe|holed|oxidat',
        re.IGNORECASE
    ),
}


def classify_rfr(rfr_desc: str) -> str:
    """Classify RFR description into failure mode."""
    if pd.isna(rfr_desc):
        return 'OTHER'
    rfr_desc = str(rfr_desc)
    for mode, pattern in PATTERNS.items():
        if pattern.search(rfr_desc):
            return mode
    return 'OTHER'


def main():
    print("=" * 70)
    print("BUILD SUSPENSION SUB-TARGET LABELS")
    print("=" * 70)

    # =========================================================================
    # Step 1: Load and classify item_detail
    # =========================================================================
    print("\n[Step 1] Loading item_detail and classifying RFR codes...")

    item_detail = pd.read_csv(ITEM_DETAIL, sep='|', low_memory=False)
    print(f"  Total RFR codes: {len(item_detail):,}")

    # Filter to suspension sections
    susp_rfr = item_detail[item_detail['test_item_set_section_id'].isin(SUSPENSION_SECTION_IDS)].copy()
    print(f"  Suspension RFR codes: {len(susp_rfr):,}")

    # Classify each RFR
    susp_rfr['failure_mode'] = susp_rfr['rfr_desc'].apply(classify_rfr)

    # Create rfr_id -> failure_mode mapping
    rfr_to_mode = dict(zip(susp_rfr['rfr_id'], susp_rfr['failure_mode']))

    # Show classification distribution
    print("\n  Classification distribution:")
    for mode, count in susp_rfr['failure_mode'].value_counts().items():
        pct = count / len(susp_rfr) * 100
        print(f"    {mode:<15} {count:>5} ({pct:>5.1f}%)")

    # Get set of suspension rfr_ids for filtering
    suspension_rfr_ids = set(susp_rfr['rfr_id'])
    print(f"\n  Suspension RFR IDs to match: {len(suspension_rfr_ids):,}")

    # =========================================================================
    # Step 2: Process test_items files
    # =========================================================================
    print("\n[Step 2] Processing test_items files...")

    # Accumulate test-level failure modes
    test_modes = defaultdict(lambda: {'WEAR_PLAY': 0, 'ACUTE_BREAKAGE': 0, 'CORROSION': 0, 'OTHER': 0})
    total_items = 0
    matched_items = 0

    for year in ['2019', '2020', '2021', '2022', '2023']:
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
        for chunk in chunks:
            chunk_count += 1
            total_items += len(chunk)

            # Filter to suspension rfr_ids
            susp_items = chunk[chunk['rfr_id'].isin(suspension_rfr_ids)]
            matched_items += len(susp_items)

            # Map to failure modes and aggregate
            for _, row in susp_items.iterrows():
                test_id = row['test_id']
                rfr_id = row['rfr_id']
                mode = rfr_to_mode.get(rfr_id, 'OTHER')
                test_modes[test_id][mode] += 1

            if chunk_count % 5 == 0:
                print(f"    Processed {chunk_count * 2}M items, {len(test_modes):,} tests with suspension defects")

    print(f"\n  Total items processed: {total_items:,}")
    print(f"  Suspension items matched: {matched_items:,}")
    print(f"  Tests with suspension defects: {len(test_modes):,}")

    # =========================================================================
    # Step 3: Convert to DataFrame with binary flags
    # =========================================================================
    print("\n[Step 3] Creating sub-target DataFrame...")

    records = []
    for test_id, modes in test_modes.items():
        records.append({
            'test_id': test_id,
            'has_wear_play': 1 if modes['WEAR_PLAY'] > 0 else 0,
            'has_acute_breakage': 1 if modes['ACUTE_BREAKAGE'] > 0 else 0,
            'has_corrosion': 1 if modes['CORROSION'] > 0 else 0,
            'has_suspension_defect': 1,  # All rows have at least one suspension defect
            'wear_count': modes['WEAR_PLAY'],
            'breakage_count': modes['ACUTE_BREAKAGE'],
            'corrosion_count': modes['CORROSION'],
        })

    subtargets_df = pd.DataFrame(records)

    # Show sub-target distribution
    print("\n  Sub-target distribution (tests with at least one):")
    print(f"    has_wear_play:       {subtargets_df['has_wear_play'].sum():,} ({subtargets_df['has_wear_play'].mean()*100:.1f}%)")
    print(f"    has_acute_breakage:  {subtargets_df['has_acute_breakage'].sum():,} ({subtargets_df['has_acute_breakage'].mean()*100:.1f}%)")
    print(f"    has_corrosion:       {subtargets_df['has_corrosion'].sum():,} ({subtargets_df['has_corrosion'].mean()*100:.1f}%)")

    # =========================================================================
    # Step 4: Save to parquet
    # =========================================================================
    print("\n[Step 4] Saving to parquet...")

    subtargets_df.to_parquet(OUTPUT_FILE, index=False)
    print(f"  Saved to: {OUTPUT_FILE}")
    print(f"  Total rows: {len(subtargets_df):,}")

    # =========================================================================
    # Step 5: Verify coverage with dev/OOT sets
    # =========================================================================
    print("\n[Step 5] Checking coverage with training data...")

    con = duckdb.connect()
    con.execute("SET memory_limit='4GB'")

    DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set.parquet"
    OOT_SET = PROJECT_ROOT / "stratified_samples/oot_test_set.parquet"

    # Check overlap
    dev_count = con.execute(f"SELECT COUNT(*) FROM read_parquet('{DEV_SET}')").fetchone()[0]
    oot_count = con.execute(f"SELECT COUNT(*) FROM read_parquet('{OOT_SET}')").fetchone()[0]

    subtarget_ids = set(subtargets_df['test_id'])

    dev_ids = set(con.execute(f"SELECT test_id FROM read_parquet('{DEV_SET}')").df()['test_id'])
    oot_ids = set(con.execute(f"SELECT test_id FROM read_parquet('{OOT_SET}')").df()['test_id'])

    dev_overlap = len(dev_ids.intersection(subtarget_ids))
    oot_overlap = len(oot_ids.intersection(subtarget_ids))

    print(f"\n  Dev set:  {dev_count:,} tests, {dev_overlap:,} with sub-targets ({dev_overlap/dev_count*100:.1f}%)")
    print(f"  OOT set:  {oot_count:,} tests, {oot_overlap:,} with sub-targets ({oot_overlap/oot_count*100:.1f}%)")

    con.close()

    # =========================================================================
    # Summary
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"""
  Sub-targets built successfully!

  Output: {OUTPUT_FILE}
  Tests with suspension defects: {len(subtargets_df):,}

  Sub-target prevalence:
    WEAR_PLAY:       {subtargets_df['has_wear_play'].mean()*100:.1f}%
    ACUTE_BREAKAGE:  {subtargets_df['has_acute_breakage'].mean()*100:.1f}%
    CORROSION:       {subtargets_df['has_corrosion'].mean()*100:.1f}%

  Next: Run train_suspension_submodels.py to train specialized models
    """)
    print("=" * 70)


if __name__ == "__main__":
    main()
