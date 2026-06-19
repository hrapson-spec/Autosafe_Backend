"""
Build V56 Feature: 9-Category System Failure Profile
=====================================================

For each test in dev/oot sets, computes from PRIOR test history:
  1. dominant_failure_system_9cat: which system has failed most (categorical)
  2. system_failure_diversity: how many distinct systems have failed (int 0-9)

Data flow:
  dev/oot (target tests) → vehicle_id
  cycle_first_with_history (all tests) → vehicle_id + test_id + test_date
  system_failures_9cat (all tests) → test_id + 9 binary failure flags

  Join: target.vehicle_id → history(all tests for that vehicle before target date)
        → system_failures_9cat(per-system flags for those prior tests)
        → aggregate per system → dominant + diversity

Leakage-free: only uses failures from tests strictly before the target test date.

Output: ~/autosafe_work/system_failure_profile_9cat.parquet
        Columns: test_id, dominant_failure_system_9cat, system_failure_diversity,
                 plus per-system cumulative counts for diagnostics

Usage:
  python3 build_system_failure_profile_9cat.py
"""

import pandas as pd
import pyarrow.parquet as pq
import numpy as np
from pathlib import Path
import time

# ============================================================================
# Paths
# ============================================================================

BASE = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/AutoSafe"
DEV_SET = BASE / "stratified_samples/dev_set_with_advisory.parquet"
OOT_SET = BASE / "stratified_samples/oot_set_with_advisory.parquet"
HISTORY = BASE / "cycle_first_with_history.parquet"
SYSTEM_FAILURES = BASE / "system_failures_9cat.parquet"
OUTPUT = Path.home() / "autosafe_work/system_failure_profile_9cat.parquet"

# System columns in system_failures_9cat
SYSTEM_COLS = [
    'fail_brakes',
    'fail_tyres_wheels',
    'fail_suspension_steering',
    'fail_structure_corrosion',
    'fail_lights_electrical',
    'fail_visibility',
    'fail_seatbelts_restraints',
    'fail_exhaust_emissions',
    'fail_other',
]

# Human-readable system names for the categorical
SYSTEM_NAMES = {
    'fail_brakes': 'BRAKES',
    'fail_tyres_wheels': 'TYRES_WHEELS',
    'fail_suspension_steering': 'SUSPENSION_STEERING',
    'fail_structure_corrosion': 'STRUCTURE_CORROSION',
    'fail_lights_electrical': 'LIGHTS_ELECTRICAL',
    'fail_visibility': 'VISIBILITY',
    'fail_seatbelts_restraints': 'SEATBELTS_RESTRAINTS',
    'fail_exhaust_emissions': 'EXHAUST_EMISSIONS',
    'fail_other': 'OTHER',
}


def load_target_tests():
    """Load target test_ids with vehicle_id and test_date from dev + oot sets."""
    t0 = time.time()
    cols = ['test_id', 'vehicle_id', 'test_date']

    dev = pd.read_parquet(DEV_SET, columns=cols)
    print(f"  DEV: {len(dev):,} rows")

    oot = pd.read_parquet(OOT_SET, columns=cols)
    print(f"  OOT: {len(oot):,} rows")

    targets = pd.concat([dev, oot], ignore_index=True)
    targets['test_date'] = pd.to_datetime(targets['test_date'])
    print(f"  Combined: {len(targets):,} rows, {targets['vehicle_id'].nunique():,} unique vehicles")
    print(f"  Loaded in {time.time()-t0:.1f}s")
    return targets


def load_vehicle_history(target_vehicle_ids: set):
    """
    Load test_id, vehicle_id, test_date from cycle_first_with_history
    for only the target vehicles. Reads in chunks for memory efficiency.
    """
    t0 = time.time()
    pf = pq.ParquetFile(str(HISTORY))
    chunks = []
    n_read = 0
    n_kept = 0

    for batch in pf.iter_batches(batch_size=2_000_000, columns=['test_id', 'vehicle_id', 'test_date']):
        df = batch.to_pandas()
        n_read += len(df)
        df = df[df['vehicle_id'].isin(target_vehicle_ids)]
        n_kept += len(df)
        if len(df) > 0:
            chunks.append(df)

    history = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame(columns=['test_id', 'vehicle_id', 'test_date'])
    history['test_date'] = pd.to_datetime(history['test_date'])
    print(f"  History: {n_read:,} rows scanned, {n_kept:,} kept ({n_kept/n_read*100:.1f}%)")
    print(f"  Loaded in {time.time()-t0:.1f}s")
    return history


def load_system_failures(relevant_test_ids: set):
    """
    Load system failure flags from system_failures_9cat for relevant test_ids.
    Reads in chunks for memory efficiency.
    """
    t0 = time.time()
    pf = pq.ParquetFile(str(SYSTEM_FAILURES))
    chunks = []
    n_read = 0
    n_kept = 0

    for batch in pf.iter_batches(batch_size=2_000_000, columns=['test_id'] + SYSTEM_COLS):
        df = batch.to_pandas()
        n_read += len(df)
        df = df[df['test_id'].isin(relevant_test_ids)]
        n_kept += len(df)
        if len(df) > 0:
            chunks.append(df)

    failures = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame(columns=['test_id'] + SYSTEM_COLS)
    print(f"  System failures: {n_read:,} rows scanned, {n_kept:,} kept ({n_kept/n_read*100:.1f}%)")
    print(f"  Loaded in {time.time()-t0:.1f}s")
    return failures


def build_features(targets: pd.DataFrame, history: pd.DataFrame, failures: pd.DataFrame) -> pd.DataFrame:
    """
    For each target test, aggregate prior system failures and compute:
      - dominant_failure_system_9cat (categorical)
      - system_failure_diversity (int)
      - per-system prior failure counts (for diagnostics)
    """
    t0 = time.time()

    # Step 1: Attach system failure flags to history tests
    # history has (test_id, vehicle_id, test_date) for ALL tests of target vehicles
    # failures has (test_id, fail_brakes, ...) for tests that had any failure
    history_with_sys = history.merge(failures, on='test_id', how='left')
    # Tests not in system_failures_9cat had no failures → fill with 0
    for col in SYSTEM_COLS:
        history_with_sys[col] = history_with_sys[col].fillna(0).astype(np.int8)

    print(f"  History with system flags: {len(history_with_sys):,} rows")

    # Step 2: For each target test, find prior tests for same vehicle
    # Cross-reference: target.(vehicle_id, test_date) → history.(vehicle_id, test_date < target)
    # Efficient approach: merge on vehicle_id, then filter by date

    # Rename columns to distinguish target vs history
    targets_slim = targets[['test_id', 'vehicle_id', 'test_date']].copy()
    targets_slim = targets_slim.rename(columns={'test_id': 'target_test_id', 'test_date': 'target_date'})

    # Merge: all history rows for each target vehicle
    merged = targets_slim.merge(
        history_with_sys,
        on='vehicle_id',
        how='left'
    )
    print(f"  After vehicle_id merge: {len(merged):,} rows (before date filter)")

    # Filter: only prior tests (strictly before target date)
    merged = merged[merged['test_date'] < merged['target_date']]
    print(f"  After date filter (prior tests only): {len(merged):,} rows")

    # Step 3: Aggregate per-system failure counts per target test
    agg_dict = {col: 'sum' for col in SYSTEM_COLS}
    per_target = merged.groupby('target_test_id').agg(agg_dict).reset_index()
    per_target = per_target.rename(columns={'target_test_id': 'test_id'})

    # Rename columns: fail_brakes → prior_sys_fail_brakes
    rename_map = {col: f'prior_sys_{col}' for col in SYSTEM_COLS}
    per_target = per_target.rename(columns=rename_map)
    prior_cols = [f'prior_sys_{col}' for col in SYSTEM_COLS]

    # Step 4: Join back to all target tests (some may have no prior tests)
    result = targets[['test_id']].merge(per_target, on='test_id', how='left')
    for col in prior_cols:
        result[col] = result[col].fillna(0).astype(int)

    # Step 5: Compute dominant system
    prior_array = result[prior_cols].values  # (n_targets, 9)
    max_counts = prior_array.max(axis=1)
    # For each row, find the system with the highest count
    # Ties broken by column order (first system wins)
    dominant_idx = prior_array.argmax(axis=1)
    system_names_list = [SYSTEM_NAMES[col] for col in SYSTEM_COLS]

    dominant = []
    for i in range(len(result)):
        if max_counts[i] == 0:
            dominant.append('CLEAN')
        else:
            dominant.append(system_names_list[dominant_idx[i]])
    result['dominant_failure_system_9cat'] = dominant

    # Step 6: Compute failure concentration HHI (Herfindahl-Hirschman Index)
    # HHI = Σ(share_i²) where share_i = count_i / total_failures
    # Range: 0 (no failures/CLEAN), ~1/9 (equal spread), 1.0 (single-system monopoly)
    total_failures = prior_array.sum(axis=1).astype(float)
    safe_total = np.where(total_failures > 0, total_failures, 1.0)
    shares = prior_array / safe_total[:, None]  # (n, 9)
    hhi = (shares ** 2).sum(axis=1)
    hhi = np.where(total_failures > 0, hhi, 0.0)  # CLEAN → HHI = 0
    result['failure_concentration_hhi'] = np.round(hhi, 6)

    # Step 7: Compute coarse failure archetype (3-way split)
    # Boundaries from hierarchical clustering: single-system specialists have HHI ≈ 0.7-1.0
    # Multi-system vehicles have HHI ≈ 0.15-0.3; threshold 0.5 is the natural boundary
    archetype = np.where(hhi == 0.0, 'CLEAN',
                np.where(hhi > 0.5, 'SINGLE_DOMINANT', 'MULTI_SYSTEM'))
    result['coarse_failure_archetype'] = archetype

    print(f"  Features built in {time.time()-t0:.1f}s")
    print(f"  Output: {len(result):,} rows, {len(result.columns)} columns")
    return result


def main():
    print("=" * 60)
    print("Building 9-cat system failure profile features")
    print("=" * 60)

    # Step 1: Load target tests
    print("\n[1] Loading target tests (dev + oot)...")
    targets = load_target_tests()

    # Step 2: Load vehicle history (only for target vehicles)
    target_vehicle_ids = set(targets['vehicle_id'].unique())
    print(f"\n[2] Loading vehicle history for {len(target_vehicle_ids):,} vehicles...")
    history = load_vehicle_history(target_vehicle_ids)

    # Step 3: Load system failures (only for relevant test_ids)
    # Relevant = all history test_ids for target vehicles
    relevant_test_ids = set(history['test_id'].unique())
    print(f"\n[3] Loading system failures for {len(relevant_test_ids):,} test IDs...")
    failures = load_system_failures(relevant_test_ids)

    # Step 4: Build features
    print(f"\n[4] Building features...")
    result = build_features(targets, history, failures)

    # Step 5: Diagnostics
    print(f"\n[5] Diagnostics:")
    print(f"  Dominant system distribution:")
    dist = result['dominant_failure_system_9cat'].value_counts()
    for sys_name, count in dist.items():
        print(f"    {sys_name}: {count:,} ({count/len(result)*100:.1f}%)")
    print(f"\n  Coarse failure archetype distribution:")
    arch_dist = result['coarse_failure_archetype'].value_counts()
    for arch, count in arch_dist.items():
        print(f"    {arch}: {count:,} ({count/len(result)*100:.1f}%)")
    print(f"\n  HHI distribution (non-CLEAN vehicles):")
    non_clean = result[result['failure_concentration_hhi'] > 0]['failure_concentration_hhi']
    if len(non_clean) > 0:
        print(f"    count={len(non_clean):,}, mean={non_clean.mean():.3f}, "
              f"median={non_clean.median():.3f}, p25={non_clean.quantile(0.25):.3f}, "
              f"p75={non_clean.quantile(0.75):.3f}")

    # Step 6: Save
    print(f"\n[6] Saving to {OUTPUT}...")
    result.to_parquet(OUTPUT, index=False)
    print(f"  Done. {OUTPUT.stat().st_size / 1024:.0f} KB")


if __name__ == '__main__':
    main()
