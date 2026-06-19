"""
Build MDPS Sequence Features (Step 2)
=====================================

Builds sequence features from cycle-level advisory time series:
1. Persistence - consecutive cycles with advisories
2. Recency - exponential decay weighted advisory signal
3. Domain switching - transitions between domain states

Input:
  - cycle_advisory_timeseries_dev.parquet
  - cycle_advisory_timeseries_oot.parquet

Output:
  - mdps_sequence_features_dev.parquet
  - mdps_sequence_features_oot.parquet

Created: 2026-01-12
"""

import numpy as np
import pandas as pd
from pathlib import Path
from typing import Tuple


def log(msg):
    print(msg, flush=True)


# Configuration
DECAY_FACTOR = 0.7  # Exponential decay for recency weighting


def compute_persistence_features(group: pd.DataFrame) -> pd.DataFrame:
    """
    Compute persistence features for a single vehicle's cycle history.

    Persistence = consecutive cycles ending at current where domain > 0.
    """
    n = len(group)

    # Initialize arrays
    persist_brakes = np.zeros(n, dtype=np.int32)
    persist_susp = np.zeros(n, dtype=np.int32)
    persist_tyres = np.zeros(n, dtype=np.int32)
    persist_any = np.zeros(n, dtype=np.int32)
    max_persist = np.zeros(n, dtype=np.int32)

    # Get advisory columns as arrays
    brakes = group['adv_brakes'].values
    susp = group['adv_suspension_steering'].values
    tyres = group['adv_tyres'].values
    any_adv = (brakes > 0) | (susp > 0) | (tyres > 0)

    # Running counters for current streak
    run_brakes = 0
    run_susp = 0
    run_tyres = 0
    run_any = 0
    max_run_any = 0

    for i in range(n):
        # Update streak counters (looking at PRIOR cycles, so use values from i-1)
        # At cycle i, persistence is the run length ending at i-1
        if i == 0:
            # First cycle: no prior history
            persist_brakes[i] = 0
            persist_susp[i] = 0
            persist_tyres[i] = 0
            persist_any[i] = 0
            max_persist[i] = 0
        else:
            # Check if prior cycle (i-1) had advisories
            if brakes[i-1] > 0:
                run_brakes += 1
            else:
                run_brakes = 0

            if susp[i-1] > 0:
                run_susp += 1
            else:
                run_susp = 0

            if tyres[i-1] > 0:
                run_tyres += 1
            else:
                run_tyres = 0

            if any_adv[i-1]:
                run_any += 1
            else:
                run_any = 0

            max_run_any = max(max_run_any, run_any)

            persist_brakes[i] = run_brakes
            persist_susp[i] = run_susp
            persist_tyres[i] = run_tyres
            persist_any[i] = run_any
            max_persist[i] = max_run_any

    return pd.DataFrame({
        'persist_brakes': persist_brakes,
        'persist_susp_steer': persist_susp,
        'persist_tyres': persist_tyres,
        'persist_any': persist_any,
        'max_persist_all_time': max_persist
    })


def compute_recency_features(group: pd.DataFrame, decay: float = DECAY_FACTOR) -> pd.DataFrame:
    """
    Compute recency-weighted features using exponential decay.

    recency_domain = Σ(adv_domain[i] * decay^(current - i - 1)) for i < current
    """
    n = len(group)

    # Initialize arrays
    recency_brakes = np.zeros(n, dtype=np.float32)
    recency_susp = np.zeros(n, dtype=np.float32)
    recency_tyres = np.zeros(n, dtype=np.float32)
    recency_total = np.zeros(n, dtype=np.float32)

    # Get advisory columns as arrays
    brakes = group['adv_brakes'].values.astype(np.float32)
    susp = group['adv_suspension_steering'].values.astype(np.float32)
    tyres = group['adv_tyres'].values.astype(np.float32)

    for i in range(n):
        if i == 0:
            # First cycle: no prior history
            continue

        # Compute weighted sum over prior cycles
        # Weight for cycle j when computing at cycle i: decay^(i - j - 1)
        # So most recent prior (j=i-1) has weight decay^0 = 1.0
        weights = np.power(decay, np.arange(i-1, -1, -1, dtype=np.float32))

        recency_brakes[i] = np.dot(brakes[:i], weights)
        recency_susp[i] = np.dot(susp[:i], weights)
        recency_tyres[i] = np.dot(tyres[:i], weights)
        recency_total[i] = recency_brakes[i] + recency_susp[i] + recency_tyres[i]

    return pd.DataFrame({
        'recency_brakes': recency_brakes,
        'recency_susp_steer': recency_susp,
        'recency_tyres': recency_tyres,
        'recency_total': recency_total
    })


def get_dominant_domain(brakes: int, susp: int, tyres: int) -> int:
    """
    Get dominant domain (0=none, 1=brakes, 2=susp_steer, 3=tyres).
    If tie, prefer in order: brakes > susp > tyres.
    """
    if brakes == 0 and susp == 0 and tyres == 0:
        return 0  # none

    max_val = max(brakes, susp, tyres)
    if brakes == max_val:
        return 1
    elif susp == max_val:
        return 2
    else:
        return 3


def compute_domain_features(group: pd.DataFrame) -> pd.DataFrame:
    """
    Compute domain switching and entropy features.

    - n_domain_switches: count of dominant domain changes in history
    - domain_entropy: entropy of domain distribution
    - dominant_domain: most common domain in history
    """
    n = len(group)

    # Initialize arrays
    n_switches = np.zeros(n, dtype=np.int32)
    entropy = np.zeros(n, dtype=np.float32)
    dominant = np.zeros(n, dtype=np.int8)

    # Get advisory columns
    brakes = group['adv_brakes'].values
    susp = group['adv_suspension_steering'].values
    tyres = group['adv_tyres'].values

    # Track cumulative counts for entropy
    cum_brakes = 0
    cum_susp = 0
    cum_tyres = 0

    # Track domain state for switching
    prev_state = 0  # none
    switch_count = 0

    for i in range(n):
        if i == 0:
            # First cycle: no prior history
            dominant[i] = 0
            entropy[i] = 0.0
            n_switches[i] = 0
            continue

        # Update cumulative counts from prior cycle
        cum_brakes += brakes[i-1]
        cum_susp += susp[i-1]
        cum_tyres += tyres[i-1]

        # Compute entropy from cumulative distribution
        total = cum_brakes + cum_susp + cum_tyres
        if total > 0:
            p_brakes = cum_brakes / total
            p_susp = cum_susp / total
            p_tyres = cum_tyres / total

            # Entropy: -Σ(p * log(p)), only for non-zero p
            ent = 0.0
            for p in [p_brakes, p_susp, p_tyres]:
                if p > 0:
                    ent -= p * np.log(p + 1e-10)
            entropy[i] = np.float32(ent)

            # Dominant domain based on cumulative counts
            dominant[i] = get_dominant_domain(cum_brakes, cum_susp, cum_tyres)
        else:
            entropy[i] = 0.0
            dominant[i] = 0

        # Check domain switching (compare prior cycle's state to the one before)
        curr_state = get_dominant_domain(brakes[i-1], susp[i-1], tyres[i-1])
        if i > 1 and curr_state != 0 and prev_state != 0 and curr_state != prev_state:
            switch_count += 1
        prev_state = curr_state

        n_switches[i] = switch_count

    return pd.DataFrame({
        'n_domain_switches': n_switches,
        'domain_entropy': entropy,
        'dominant_domain': dominant
    })


def compute_all_features_for_vehicle(group: pd.DataFrame) -> pd.DataFrame:
    """Compute all sequence features for a single vehicle."""
    # Ensure sorted by cycle_index
    group = group.sort_values('cycle_index').reset_index(drop=True)

    # Compute each feature set
    persist_df = compute_persistence_features(group)
    recency_df = compute_recency_features(group)
    domain_df = compute_domain_features(group)

    # Combine with test_id
    result = pd.DataFrame({
        'test_id': group['test_id'].values,
        'vehicle_id': group['vehicle_id'].values,
        'cycle_index': group['cycle_index'].values,
    })
    result = pd.concat([result, persist_df, recency_df, domain_df], axis=1)

    return result


def build_sequence_features(ts_path: Path, target_path: Path, output_path: Path, name: str):
    """Build sequence features for a single dataset (DEV or OOT)."""
    log(f"\n{'='*60}")
    log(f"Building {name} sequence features")
    log(f"{'='*60}")

    # Load time series
    log(f"\n[1] Loading time series from {ts_path.name}...")
    ts_df = pd.read_parquet(ts_path)
    log(f"    Loaded {len(ts_df):,} cycles for {ts_df['vehicle_id'].nunique():,} vehicles")

    # Load target test_ids
    log(f"\n[2] Loading target test_ids from {target_path.name}...")
    target_df = pd.read_parquet(target_path, columns=['test_id'])
    target_ids = set(target_df['test_id'].values)
    log(f"    {len(target_ids):,} target test_ids")

    # Process by vehicle
    log(f"\n[3] Computing sequence features by vehicle...")

    results = []
    vehicles = ts_df['vehicle_id'].unique()
    n_vehicles = len(vehicles)

    for idx, vid in enumerate(vehicles):
        if idx > 0 and idx % 100000 == 0:
            log(f"    Processed {idx:,}/{n_vehicles:,} vehicles ({100*idx/n_vehicles:.1f}%)")

        vehicle_df = ts_df[ts_df['vehicle_id'] == vid]
        features = compute_all_features_for_vehicle(vehicle_df)

        # Only keep target test_ids
        features = features[features['test_id'].isin(target_ids)]
        if len(features) > 0:
            results.append(features)

    log(f"    Processed {n_vehicles:,}/{n_vehicles:,} vehicles (100.0%)")

    # Combine results
    log(f"\n[4] Combining results...")
    result_df = pd.concat(results, ignore_index=True)
    log(f"    Total rows: {len(result_df):,}")

    # Verify all targets found
    found_ids = set(result_df['test_id'].values)
    missing = target_ids - found_ids
    if missing:
        log(f"    WARNING: {len(missing)} target test_ids not found!")
    else:
        log(f"    All {len(target_ids):,} target test_ids found")

    # Drop vehicle_id and cycle_index (not needed in output)
    output_df = result_df.drop(columns=['vehicle_id', 'cycle_index'])

    # Ensure correct dtypes
    output_df = output_df.astype({
        'test_id': 'int64',
        'persist_brakes': 'int32',
        'persist_susp_steer': 'int32',
        'persist_tyres': 'int32',
        'persist_any': 'int32',
        'max_persist_all_time': 'int32',
        'recency_brakes': 'float32',
        'recency_susp_steer': 'float32',
        'recency_tyres': 'float32',
        'recency_total': 'float32',
        'n_domain_switches': 'int32',
        'domain_entropy': 'float32',
        'dominant_domain': 'int8',
    })

    # Save
    log(f"\n[5] Saving to {output_path.name}...")
    output_df.to_parquet(output_path, index=False)
    log(f"    Saved {len(output_df):,} rows")

    # Stats
    log(f"\n[6] Feature statistics:")
    for col in output_df.columns:
        if col == 'test_id':
            continue
        nonzero = (output_df[col] != 0).sum()
        log(f"    {col:25s}: mean={output_df[col].mean():.4f}, nonzero={nonzero:,} ({100*nonzero/len(output_df):.1f}%)")

    return output_df


def main():
    log("=" * 70)
    log("BUILDING MDPS SEQUENCE FEATURES")
    log("=" * 70)

    WORK_DIR = Path.home() / "autosafe_work"
    PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")

    # Paths
    TS_DEV = WORK_DIR / "cycle_advisory_timeseries_dev.parquet"
    TS_OOT = WORK_DIR / "cycle_advisory_timeseries_oot.parquet"
    TARGET_DEV = PROJECT_ROOT / "stratified_samples" / "dev_set.parquet"
    TARGET_OOT = PROJECT_ROOT / "stratified_samples" / "oot_test_set.parquet"
    OUTPUT_DEV = WORK_DIR / "mdps_sequence_features_dev.parquet"
    OUTPUT_OOT = WORK_DIR / "mdps_sequence_features_oot.parquet"

    # Check inputs exist
    for p in [TS_DEV, TS_OOT, TARGET_DEV, TARGET_OOT]:
        if not p.exists():
            log(f"ERROR: {p} not found!")
            return

    # Build DEV features
    build_sequence_features(TS_DEV, TARGET_DEV, OUTPUT_DEV, "DEV")

    # Build OOT features
    build_sequence_features(TS_OOT, TARGET_OOT, OUTPUT_OOT, "OOT")

    log("\n" + "=" * 70)
    log("COMPLETE")
    log("=" * 70)


if __name__ == "__main__":
    main()
