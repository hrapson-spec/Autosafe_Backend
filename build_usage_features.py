"""
Build Usage Features (V10 - Full History Rebuild)
=================================================

Computes sequential usage features (miles-per-day intervals, acceleration, EWMA)
by joining to raw 2023 DVSA data for 97%+ coverage.

Key fix: Uses mileage_lookup_2023.parquet (42M tests) instead of validation_samples
which only had 28% coverage of needed prev_test_ids.

Coverage: ~97% OOT (up from 7.7%)

Created: 2026-01-05
Updated: 2026-01-06
"""

import duckdb
import pickle
from pathlib import Path
import pandas as pd
import numpy as np
from datetime import datetime

from usage_guardrails import (
    validate_mileage_delta_vectorized,
    compute_usage_accel_vectorized,
    ewma_mpd_vectorized,
    compute_odometer_anomaly_flag_vectorized,
)

# =============================================================================
# Configuration
# =============================================================================

PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set.parquet"
OOT_SET = PROJECT_ROOT / "stratified_samples/oot_test_set.parquet"
HISTORY = PROJECT_ROOT / "cycle_first_with_history.parquet"

OUTPUT_DIR = Path.home() / "autosafe_work"
MILEAGE_LOOKUP = OUTPUT_DIR / "mileage_lookup_2023.parquet"  # 42M tests, raw 2023 (97.7% OOT coverage)

# EWMA alpha parameter
EWMA_ALPHA = 0.6


# =============================================================================
# Core Query: Load with Full History Join
# =============================================================================

def build_usage_query_with_history(dataset_path: Path) -> str:
    """
    Load dataset with mileage from raw 2023 data via history join.

    Join path:
    dataset → history (get prev_cycle_test_id, days_since_prev_cycle) → mileage_lookup (get prev_mileage)
    """
    query = f"""
    SELECT
        d.test_id,
        d.vehicle_id,
        d.test_date,
        d.test_mileage,
        d.annualized_mileage,
        d.annualized_mileage_prev,
        d.prev_cycle_outcome_band,
        h.prev_cycle_test_id,
        h.days_since_prev_cycle as days_since_last_test,
        m.test_mileage as prev_mileage,
        d.test_mileage - m.test_mileage as miles_since_last_test
    FROM read_parquet('{dataset_path}') d
    LEFT JOIN read_parquet('{HISTORY}') h ON d.test_id = h.test_id
    LEFT JOIN read_parquet('{MILEAGE_LOOKUP}') m ON h.prev_cycle_test_id = m.test_id
    """
    return query


def load_dataset_with_history(dataset_path: Path) -> pd.DataFrame:
    """Load dataset with mileage from raw data join."""
    con = duckdb.connect()
    con.execute("SET memory_limit='4GB'")
    query = build_usage_query_with_history(dataset_path)
    df = con.execute(query).df()
    con.close()
    return df


# =============================================================================
# Feature Computation
# =============================================================================

def compute_usage_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute usage features from joined data.

    Adds:
    - mpd_last_interval = miles_since / days_since
    - mpd_prev_interval = annualized_mileage_prev / 365 (proxy)
    - usage_accel_mpd = mpd_last - mpd_prev
    - usage_ewma_mpd_2 = EWMA of mpd values
    - odometer_anomaly_flag
    - has_valid_mpd, has_usage_accel
    """
    print(f"  Computing usage features for {len(df):,} records...")

    # Convert to numeric
    df['miles_since_last_test'] = pd.to_numeric(df['miles_since_last_test'], errors='coerce')
    df['days_since_last_test'] = pd.to_numeric(df['days_since_last_test'], errors='coerce')
    df['annualized_mileage_prev'] = pd.to_numeric(df.get('annualized_mileage_prev', pd.Series()), errors='coerce')

    # Get values
    miles_since = df['miles_since_last_test'].copy()
    days_since = df['days_since_last_test'].copy()

    # Initialize results
    mpd_last = pd.Series(index=df.index, dtype=float)
    mpd_last[:] = np.nan
    anomaly_last = pd.Series(index=df.index, dtype=object)
    anomaly_last[:] = None

    # Flag anomalies
    missing_mask = miles_since.isna() | days_since.isna()
    anomaly_last.loc[missing_mask] = 'MISSING'

    rollback_mask = (miles_since < 0) & ~missing_mask
    anomaly_last.loc[rollback_mask] = 'ROLLBACK'

    invalid_days_mask = (days_since < 1) & ~missing_mask & ~rollback_mask
    anomaly_last.loc[invalid_days_mask] = 'INVALID_DAYS'

    # Compute mpd where valid
    valid_mask = ~missing_mask & ~rollback_mask & ~invalid_days_mask
    mpd_raw = miles_since / days_since

    # Flag implausibly high (>500 mpd = >182k miles/year)
    implausible_mask = (mpd_raw > 500) & valid_mask
    anomaly_last.loc[implausible_mask] = 'IMPLAUSIBLE_HIGH'

    # Set valid mpd (capped at 200)
    final_valid = valid_mask & ~implausible_mask
    mpd_last.loc[final_valid] = mpd_raw.loc[final_valid].clip(upper=200)

    # For previous interval: use annualized_mileage_prev / 365 as proxy
    mpd_prev = pd.Series(index=df.index, dtype=float)
    mpd_prev[:] = np.nan
    if 'annualized_mileage_prev' in df.columns:
        valid_prev = df['annualized_mileage_prev'].notna()
        mpd_prev.loc[valid_prev] = (df.loc[valid_prev, 'annualized_mileage_prev'] / 365.0).clip(upper=200)

    anomaly_prev = pd.Series(index=df.index, dtype=object)
    anomaly_prev[:] = None

    # Usage acceleration
    usage_accel = compute_usage_accel_vectorized(mpd_last, mpd_prev)

    # EWMA
    usage_ewma = ewma_mpd_vectorized(mpd_last, mpd_prev, EWMA_ALPHA)

    # Anomaly flag
    odometer_anomaly = anomaly_last.copy()

    # Validity flags
    has_valid_mpd = mpd_last.notna()
    has_usage_accel = usage_accel.notna()

    # Add to DataFrame
    df['mpd_last_interval'] = mpd_last
    df['mpd_prev_interval'] = mpd_prev
    df['usage_accel_mpd'] = usage_accel
    df['usage_ewma_mpd_2'] = usage_ewma
    df['anomaly_last'] = anomaly_last
    df['anomaly_prev'] = anomaly_prev
    df['odometer_anomaly_flag'] = odometer_anomaly
    df['has_valid_mpd'] = has_valid_mpd
    df['has_usage_accel'] = has_usage_accel

    return df


# =============================================================================
# Main Function
# =============================================================================

def compute_usage_features_full_history(
    dev_set_path: Path = DEV_SET,
    oot_set_path: Path = OOT_SET,
    output_dir: Path = OUTPUT_DIR
):
    """
    Compute usage features using full history join and save as parquet files.

    Creates:
    - usage_features_dev_full.parquet
    - usage_features_oot_full.parquet
    """
    print("=" * 70)
    print("BUILDING USAGE FEATURES (V10 - Full History Rebuild)")
    print("=" * 70)
    print(f"Using mileage lookup: {MILEAGE_LOOKUP}")

    # Process dev set
    print("\n[DEV SET]")
    dev_df = load_dataset_with_history(dev_set_path)
    print(f"  Loaded {len(dev_df):,} records")
    print(f"  Records with prev_mileage (raw join): {dev_df['prev_mileage'].notna().sum():,} ({dev_df['prev_mileage'].notna().mean()*100:.1f}%)")

    dev_df = compute_usage_features(dev_df)

    # Select usage columns
    usage_cols = [
        'test_id', 'vehicle_id',
        'mpd_last_interval', 'mpd_prev_interval',
        'usage_accel_mpd', 'usage_ewma_mpd_2',
        'odometer_anomaly_flag',
        'has_valid_mpd', 'has_usage_accel'
    ]
    dev_usage = dev_df[usage_cols]
    dev_output = output_dir / "usage_features_dev_full.parquet"
    dev_usage.to_parquet(dev_output, index=False)
    print(f"  Saved to {dev_output}")

    # Coverage report
    print(f"\n  Coverage:")
    print(f"    Valid mpd: {dev_df['has_valid_mpd'].sum():,} ({dev_df['has_valid_mpd'].mean()*100:.1f}%)")
    print(f"    Usage accel: {dev_df['has_usage_accel'].sum():,} ({dev_df['has_usage_accel'].mean()*100:.1f}%)")

    # Anomaly breakdown
    print(f"\n  Anomaly breakdown:")
    anomaly_counts = dev_df['anomaly_last'].value_counts(dropna=False)
    for flag, count in anomaly_counts.head(6).items():
        pct = count / len(dev_df) * 100
        print(f"    {flag}: {count:,} ({pct:.1f}%)")

    # Process OOT set
    print("\n[OOT SET]")
    oot_df = load_dataset_with_history(oot_set_path)
    print(f"  Loaded {len(oot_df):,} records")
    print(f"  Records with prev_mileage (raw join): {oot_df['prev_mileage'].notna().sum():,} ({oot_df['prev_mileage'].notna().mean()*100:.1f}%)")

    oot_df = compute_usage_features(oot_df)

    oot_usage = oot_df[usage_cols]
    oot_output = output_dir / "usage_features_oot_full.parquet"
    oot_usage.to_parquet(oot_output, index=False)
    print(f"  Saved to {oot_output}")

    # Coverage report
    print(f"\n  Coverage:")
    print(f"    Valid mpd: {oot_df['has_valid_mpd'].sum():,} ({oot_df['has_valid_mpd'].mean()*100:.1f}%)")
    print(f"    Usage accel: {oot_df['has_usage_accel'].sum():,} ({oot_df['has_usage_accel'].mean()*100:.1f}%)")

    # Anomaly breakdown
    print(f"\n  Anomaly breakdown:")
    anomaly_counts = oot_df['anomaly_last'].value_counts(dropna=False)
    for flag, count in anomaly_counts.head(6).items():
        pct = count / len(oot_df) * 100
        print(f"    {flag}: {count:,} ({pct:.1f}%)")

    # MPD distribution
    print("\n[MPD Distribution - OOT Set]")
    valid_mpd = oot_df.loc[oot_df['has_valid_mpd'], 'mpd_last_interval']
    if len(valid_mpd) > 0:
        print(f"  n = {len(valid_mpd):,}")
        print(f"  mean = {valid_mpd.mean():.1f} mpd")
        print(f"  median = {valid_mpd.median():.1f} mpd")
        print(f"  p10 = {valid_mpd.quantile(0.10):.1f} mpd")
        print(f"  p90 = {valid_mpd.quantile(0.90):.1f} mpd")
        print(f"  max = {valid_mpd.max():.1f} mpd")

    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)

    return dev_df, oot_df


# Legacy alias
def compute_usage_features_quick(*args, **kwargs):
    return compute_usage_features_full_history(*args, **kwargs)


if __name__ == "__main__":
    compute_usage_features_full_history()
