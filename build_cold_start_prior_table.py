"""
Build Cold-Start Prior Table for V56
=====================================

Creates time-sliced, empirically-estimated cold-start base rates stratified by:
- age_band: (3y, 4y, 5y, 6y+)
- fuel_type: (PETROL, DIESEL, ELECTRIC, HYBRID, OTHER)

Uses Beta smoothing (same EB machinery as existing priors) and time-slicing
to prevent leakage.

Output: cold_start_prior_table.parquet
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Paths
PROJECT_ROOT = Path(__file__).parent
DEV_SET_PATH = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/AutoSafe/stratified_samples/dev_set_with_advisory.parquet"
DIM_VEHICLE_PATH = PROJECT_ROOT / "dim_vehicle.parquet"
OUTPUT_PATH = PROJECT_ROOT / "cold_start_prior_table.parquet"

# Smoothing parameter (similar to existing EB priors)
K_SMOOTH = 50  # Pseudo-observations for Beta smoothing


def compute_age_at_test(df: pd.DataFrame) -> pd.DataFrame:
    """Compute vehicle age at test date."""
    df = df.copy()
    df['first_use_date'] = pd.to_datetime(df['first_use_date'], errors='coerce')
    df['test_date'] = pd.to_datetime(df['test_date'], errors='coerce')

    # Age in years at test
    df['age_at_test'] = (df['test_date'] - df['first_use_date']).dt.days / 365.25

    # Age bands matching expert recommendation
    df['cold_start_age_band'] = pd.cut(
        df['age_at_test'],
        bins=[0, 3.5, 4.5, 5.5, float('inf')],
        labels=['3y', '4y', '5y', '6y+'],
        right=False
    ).astype(str)

    return df


def normalize_fuel_type(fuel_type: str) -> str:
    """Normalize fuel type to standard categories."""
    if pd.isna(fuel_type):
        return 'OTHER'

    fuel_upper = str(fuel_type).upper().strip()

    if 'ELECTRIC' in fuel_upper or fuel_upper == 'EV':
        return 'ELECTRIC'
    elif 'HYBRID' in fuel_upper or 'HEV' in fuel_upper or 'PHEV' in fuel_upper:
        return 'HYBRID'
    elif 'PETROL' in fuel_upper or 'GASOLINE' in fuel_upper:
        return 'PETROL'
    elif 'DIESEL' in fuel_upper:
        return 'DIESEL'
    else:
        return 'OTHER'


def beta_smooth(n_fail: int, n_total: int, prior_rate: float, k: int) -> float:
    """
    Apply Beta smoothing to failure rate estimate.

    Posterior mean = (n_fail + k * prior_rate) / (n_total + k)

    Args:
        n_fail: Number of failures
        n_total: Total observations
        prior_rate: Global prior rate
        k: Smoothing strength (pseudo-observations)

    Returns:
        Smoothed failure rate
    """
    return (n_fail + k * prior_rate) / (n_total + k)


def main():
    logger.info("=" * 70)
    logger.info("BUILDING COLD-START PRIOR TABLE FOR V56")
    logger.info("=" * 70)

    # Load dev set
    logger.info(f"\n[1] Loading dev set from {DEV_SET_PATH}...")
    dev_df = pd.read_parquet(DEV_SET_PATH)
    logger.info(f"    Loaded {len(dev_df):,} records")

    # Filter to cold-start only
    cold_start_mask = dev_df['n_prior_tests'] == 0
    cold_df = dev_df[cold_start_mask].copy()
    logger.info(f"    Cold-start records: {len(cold_df):,} ({cold_start_mask.mean()*100:.1f}%)")

    # Compute age at test
    logger.info("\n[2] Computing vehicle age at test...")
    cold_df = compute_age_at_test(cold_df)
    logger.info(f"    Age distribution:")
    logger.info(cold_df['cold_start_age_band'].value_counts().to_string())

    # Join fuel type from dim_vehicle
    logger.info(f"\n[3] Joining fuel type from {DIM_VEHICLE_PATH}...")
    if DIM_VEHICLE_PATH.exists():
        dim_vehicle = pd.read_parquet(DIM_VEHICLE_PATH, columns=['vehicle_id', 'fuel_type'])
        cold_df = cold_df.merge(dim_vehicle, on='vehicle_id', how='left')
        cold_df['fuel_type_norm'] = cold_df['fuel_type'].apply(normalize_fuel_type)
        logger.info(f"    Fuel type distribution:")
        logger.info(cold_df['fuel_type_norm'].value_counts().to_string())
    else:
        logger.warning(f"    dim_vehicle not found, using 'OTHER' for all")
        cold_df['fuel_type_norm'] = 'OTHER'

    # Global cold-start fail rate (for smoothing prior)
    global_fail_rate = cold_df['is_failure'].mean()
    logger.info(f"\n[4] Global cold-start fail rate: {global_fail_rate:.4f}")

    # Time-slice: use test_date month as asof_month
    cold_df['asof_month'] = cold_df['test_date'].dt.to_period('M').dt.to_timestamp()

    # Build aggregates with time-slicing
    logger.info("\n[5] Building time-sliced aggregates...")

    # Sort by month for cumulative calculation
    cold_df = cold_df.sort_values('asof_month')

    # Aggregate by (age_band, fuel_type, month)
    monthly_agg = cold_df.groupby(['cold_start_age_band', 'fuel_type_norm', 'asof_month']).agg(
        n_fail_m=('is_failure', 'sum'),
        n_total_m=('is_failure', 'count')
    ).reset_index()

    logger.info(f"    Monthly aggregates: {len(monthly_agg):,} rows")

    # Cumulative sums for time-slicing (prior month logic)
    monthly_agg = monthly_agg.sort_values(['cold_start_age_band', 'fuel_type_norm', 'asof_month'])
    monthly_agg['cum_fail'] = monthly_agg.groupby(['cold_start_age_band', 'fuel_type_norm'])['n_fail_m'].cumsum()
    monthly_agg['cum_total'] = monthly_agg.groupby(['cold_start_age_band', 'fuel_type_norm'])['n_total_m'].cumsum()

    # Shift to get "prior month" stats (as-of logic)
    monthly_agg['cum_fail_prior'] = monthly_agg.groupby(['cold_start_age_band', 'fuel_type_norm'])['cum_fail'].shift(1)
    monthly_agg['cum_total_prior'] = monthly_agg.groupby(['cold_start_age_band', 'fuel_type_norm'])['cum_total'].shift(1)

    # Fill first month with global prior
    monthly_agg['cum_fail_prior'] = monthly_agg['cum_fail_prior'].fillna(0)
    monthly_agg['cum_total_prior'] = monthly_agg['cum_total_prior'].fillna(0)

    # Apply Beta smoothing
    monthly_agg['eb_cold_start_prior'] = monthly_agg.apply(
        lambda row: beta_smooth(
            int(row['cum_fail_prior']),
            int(row['cum_total_prior']),
            global_fail_rate,
            K_SMOOTH
        ),
        axis=1
    )

    # Build fallback hierarchy
    logger.info("\n[6] Building fallback hierarchy...")

    # Level 1: Age-band only (for sparse fuel_type cells)
    age_only_agg = cold_df.groupby(['cold_start_age_band', 'asof_month']).agg(
        n_fail_age=('is_failure', 'sum'),
        n_total_age=('is_failure', 'count')
    ).reset_index().sort_values(['cold_start_age_band', 'asof_month'])

    age_only_agg['cum_fail_age'] = age_only_agg.groupby('cold_start_age_band')['n_fail_age'].cumsum().shift(1).fillna(0)
    age_only_agg['cum_total_age'] = age_only_agg.groupby('cold_start_age_band')['n_total_age'].cumsum().shift(1).fillna(0)

    age_only_agg['eb_cold_start_prior_age_only'] = age_only_agg.apply(
        lambda row: beta_smooth(
            int(row['cum_fail_age']),
            int(row['cum_total_age']),
            global_fail_rate,
            K_SMOOTH
        ),
        axis=1
    )

    # Merge fallback
    output = monthly_agg[['cold_start_age_band', 'fuel_type_norm', 'asof_month',
                          'eb_cold_start_prior', 'cum_total_prior']].copy()

    output = output.merge(
        age_only_agg[['cold_start_age_band', 'asof_month', 'eb_cold_start_prior_age_only']],
        on=['cold_start_age_band', 'asof_month'],
        how='left'
    )

    # Apply fallback: if cell has <20 observations, use age-only rate
    MIN_OBS = 20
    output['eb_cold_start_prior_final'] = np.where(
        output['cum_total_prior'] >= MIN_OBS,
        output['eb_cold_start_prior'],
        output['eb_cold_start_prior_age_only']
    )

    # Add global fallback
    output['global_cold_start_rate'] = global_fail_rate

    # Rename for clarity
    output = output.rename(columns={
        'cold_start_age_band': 'age_band',
        'fuel_type_norm': 'fuel_type'
    })

    # Select final columns
    output = output[['asof_month', 'age_band', 'fuel_type',
                     'eb_cold_start_prior_final', 'eb_cold_start_prior_age_only',
                     'global_cold_start_rate', 'cum_total_prior']]

    # QA: Check rate bounds
    logger.info("\n[7] QA Checks...")
    rate_min = output['eb_cold_start_prior_final'].min()
    rate_max = output['eb_cold_start_prior_final'].max()
    logger.info(f"    Rate bounds: [{rate_min:.4f}, {rate_max:.4f}]")
    logger.info(f"    Global rate: {global_fail_rate:.4f}")

    if rate_min < 0 or rate_max > 1:
        logger.error("    FAILED: Rates outside [0,1]!")
    else:
        logger.info("    PASSED: Rates within valid range")

    # Show sample
    logger.info("\n[8] Sample output:")
    sample = output[output['asof_month'] >= '2023-01-01'].head(20)
    print(sample.to_string())

    # Save
    logger.info(f"\n[9] Saving to {OUTPUT_PATH}...")
    output.to_parquet(OUTPUT_PATH, index=False)
    logger.info(f"    Saved {len(output):,} rows")

    # Summary stats by age_band
    logger.info("\n" + "=" * 70)
    logger.info("SUMMARY: Cold-Start Prior by Age Band (Latest Month)")
    logger.info("=" * 70)

    latest_month = output['asof_month'].max()
    latest = output[output['asof_month'] == latest_month]
    summary = latest.groupby('age_band')['eb_cold_start_prior_final'].agg(['mean', 'min', 'max', 'count'])
    print(summary.to_string())

    logger.info("\n✓ Cold-start prior table built successfully")


if __name__ == "__main__":
    main()
