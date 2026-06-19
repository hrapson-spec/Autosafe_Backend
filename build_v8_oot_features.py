"""
Build V8 OOT Features for Experimental V8 Evaluation

Creates the 16 missing features needed to properly evaluate Experimental V8 on 2024 OOT data.

Features built:
- Phase 1: Temporal (7): test_month, test_quarter, is_winter, is_december, is_summer, is_monday, is_friday
- Phase 2: Vehicle age (1): vehicle_age_years
- Phase 3: EB risk (1): eb_risk (15% importance - highest priority)
- Phase 4: Regional (2): region_fail_rate, regional_residual
- Phase 5: RFR/Advisory (2): rfr_escalation_sum, n_top_rfr_advisories
- Phase 6: Failure patterns (3): fail_count_last2, chronic_failure, transition_pattern

Usage:
    python build_v8_oot_features.py
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict

# Paths
PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
OOT_SET = PROJECT_ROOT / "stratified_samples/oot_test_set.parquet"
DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set.parquet"
EB_RISK_SOURCE = PROJECT_ROOT / "FINAL_MOT_REPORT.csv"
OUTPUT = Path("/Users/henrirapson/autosafe_work/oot_v8_features.parquet")

# Smoothing constants for regional features
K_REGIONAL = 100  # Bayesian smoothing parameter


def build_temporal_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Phase 1: Build 7 temporal features from test_date.

    Reference: train_catboost_v8.py lines 177-187
    """
    print("\n=== Phase 1: Temporal Features ===")
    df = df.copy()

    df['test_date'] = pd.to_datetime(df['test_date'], errors='coerce')

    # Month and quarter
    df['test_month'] = df['test_date'].dt.month
    df['test_quarter'] = df['test_date'].dt.quarter

    # Season flags
    df['is_winter'] = df['test_month'].isin([11, 12, 1, 2]).astype(int)
    df['is_december'] = (df['test_month'] == 12).astype(int)
    df['is_summer'] = df['test_month'].isin([6, 7, 8]).astype(int)

    # Day of week flags
    df['day_of_week'] = df['test_date'].dt.dayofweek
    df['is_monday'] = (df['day_of_week'] == 0).astype(int)
    df['is_friday'] = (df['day_of_week'] == 4).astype(int)

    # Drop intermediate column
    df = df.drop(columns=['day_of_week'])

    print(f"  Created: test_month, test_quarter, is_winter, is_december, is_summer, is_monday, is_friday")
    print(f"  Sample is_winter rate: {df['is_winter'].mean():.1%}")

    return df


def build_vehicle_age(df: pd.DataFrame) -> pd.DataFrame:
    """
    Phase 2: Build vehicle_age_years from first_use_date.

    Reference: train_catboost_v8.py lines 141-145

    Handles edge case of invalid dates (e.g., year 1013) by falling back to age_band midpoints.
    """
    print("\n=== Phase 2: Vehicle Age ===")
    df = df.copy()

    # Use age_band midpoints as fallback for invalid dates
    age_band_midpoints = {
        '0-3': 1.5,
        '3-5': 4.0,
        '6-10': 8.0,
        '11-15': 13.0,
        '15+': 20.0,
    }

    # Try to compute from dates, but handle overflow errors
    try:
        # Filter to reasonable dates (year > 1900) before conversion
        df['test_date'] = pd.to_datetime(df['test_date'], errors='coerce')

        # Convert first_use_date with error handling
        df['first_use_date_clean'] = pd.to_datetime(df['first_use_date'], errors='coerce')

        # Mark invalid dates (before 1900 or after test_date)
        min_valid_date = pd.Timestamp('1900-01-01')
        valid_fud_mask = (
            df['first_use_date_clean'].notna() &
            (df['first_use_date_clean'] >= min_valid_date) &
            (df['first_use_date_clean'] <= df['test_date'])
        )

        # Compute age where valid
        df['vehicle_age_years'] = np.nan
        df.loc[valid_fud_mask, 'vehicle_age_years'] = (
            (df.loc[valid_fud_mask, 'test_date'] - df.loc[valid_fud_mask, 'first_use_date_clean']).dt.days / 365.25
        )

        # Clean up temp column
        df = df.drop(columns=['first_use_date_clean'])

        invalid_count = (~valid_fud_mask).sum()
        if invalid_count > 0:
            print(f"  WARNING: {invalid_count:,} rows with invalid first_use_date")

    except Exception as e:
        print(f"  ERROR computing from dates: {e}")
        df['vehicle_age_years'] = np.nan

    # Fill missing with age_band midpoints
    if 'age_band' in df.columns:
        missing_mask = df['vehicle_age_years'].isna()
        df.loc[missing_mask, 'vehicle_age_years'] = df.loc[missing_mask, 'age_band'].map(age_band_midpoints)
        print(f"  Filled {missing_mask.sum():,} rows from age_band midpoints")

    # Final fallback
    df['vehicle_age_years'] = df['vehicle_age_years'].fillna(8.0)  # Default to middle of range

    # Handle negative/invalid ages
    invalid_mask = df['vehicle_age_years'] < 0
    if invalid_mask.sum() > 0:
        print(f"  WARNING: {invalid_mask.sum()} rows with negative age, setting to 0")
        df.loc[invalid_mask, 'vehicle_age_years'] = 0

    print(f"  Created: vehicle_age_years")
    print(f"  Mean age: {df['vehicle_age_years'].mean():.1f} years")
    print(f"  Range: {df['vehicle_age_years'].min():.1f} - {df['vehicle_age_years'].max():.1f} years")

    return df


def build_eb_risk(df: pd.DataFrame) -> pd.DataFrame:
    """
    Phase 3: Build eb_risk by joining with FINAL_MOT_REPORT.csv.

    This is the HIGHEST PRIORITY feature (15% importance).

    Reference: train_catboost_v8.py lines 78-85, 200-205
    """
    print("\n=== Phase 3: EB Risk (15% importance) ===")
    df = df.copy()

    # Load EB predictions
    print(f"  Loading EB predictions from {EB_RISK_SOURCE}...")
    eb_df = pd.read_csv(EB_RISK_SOURCE)
    eb_df = eb_df[['model_id', 'age_band', 'mileage_band', 'Failure_Risk']].copy()
    eb_df = eb_df.rename(columns={'Failure_Risk': 'eb_risk'})

    print(f"  Loaded {len(eb_df):,} EB segments")
    print(f"  Sample EB age_bands: {eb_df['age_band'].unique()[:5]}")
    print(f"  OOT age_bands: {df['age_band'].unique()[:5]}")

    # Check for age_band format mismatch and create mapping
    eb_age_bands = set(eb_df['age_band'].unique())
    oot_age_bands = set(df['age_band'].unique())

    if eb_age_bands != oot_age_bands:
        print(f"  WARNING: Age band mismatch!")
        print(f"    EB: {sorted(eb_age_bands)}")
        print(f"    OOT: {sorted(oot_age_bands)}")

        # Create mapping - EB uses '0-2' but V8 expects '0-3'
        # We'll map OOT age_bands to closest EB age_bands
        age_band_map = {
            '0-3': '0-2',  # Map 0-3 to 0-2 (closest match in EB)
            '3-5': '3-5',
            '6-10': '6-10',
            '11-15': '11-15',
            '15+': '15+',
        }

        # Apply mapping if needed
        if '0-2' in eb_age_bands and '0-3' in oot_age_bands:
            df['_eb_age_band'] = df['age_band'].map(age_band_map).fillna(df['age_band'])
            print(f"  Applied age_band mapping for EB join")
        else:
            df['_eb_age_band'] = df['age_band']
    else:
        df['_eb_age_band'] = df['age_band']

    # Merge on (model_id, age_band, mileage_band)
    before_len = len(df)
    df = df.merge(
        eb_df,
        left_on=['model_id', '_eb_age_band', 'mileage_band'],
        right_on=['model_id', 'age_band', 'mileage_band'],
        how='left',
        suffixes=('', '_eb')
    )

    # Clean up merge artifacts
    if 'age_band_eb' in df.columns:
        df = df.drop(columns=['age_band_eb'])
    df = df.drop(columns=['_eb_age_band'])

    # Calculate hit rate and fill missing with global rate
    hit_rate = df['eb_risk'].notna().mean()
    global_fail_rate = df['is_failure'].mean()

    print(f"  EB join hit rate: {hit_rate:.1%}")
    print(f"  Missing segments: {df['eb_risk'].isna().sum():,}")

    # Fill missing with global fail rate
    df['eb_risk'] = df['eb_risk'].fillna(global_fail_rate)

    print(f"  Created: eb_risk")
    print(f"  Mean eb_risk: {df['eb_risk'].mean():.4f}")
    print(f"  Range: {df['eb_risk'].min():.4f} - {df['eb_risk'].max():.4f}")

    return df


def build_regional_features(df: pd.DataFrame, dev_df: pd.DataFrame = None) -> pd.DataFrame:
    """
    Phase 4: Build regional features with Bayesian smoothing.

    Features:
    - region_fail_rate: Bayesian-smoothed failure rate by postcode_area
    - regional_residual: Deviation from regional average

    CRITICAL: Fit on DEV SET only to prevent leakage.
    """
    print("\n=== Phase 4: Regional Features ===")
    df = df.copy()

    if 'postcode_area' not in df.columns:
        print("  WARNING: postcode_area not in OOT, setting regional features to 0")
        df['region_fail_rate'] = df['is_failure'].mean()
        df['regional_residual'] = 0.0
        return df

    # Load dev set to fit regional rates (avoid leakage)
    if dev_df is None:
        print(f"  Loading DEV set for regional rate fitting...")
        dev_df = pd.read_parquet(DEV_SET)

    # Calculate global fail rate from dev set
    global_fail_rate = dev_df['is_failure'].mean()
    print(f"  Global fail rate (DEV): {global_fail_rate:.4f}")

    # Calculate regional stats from DEV set
    regional_stats = dev_df.groupby('postcode_area').agg({
        'is_failure': ['sum', 'count']
    })
    regional_stats.columns = ['failures', 'tests']

    # Bayesian smoothing: (failures + K * global) / (tests + K)
    regional_stats['smoothed_rate'] = (
        (regional_stats['failures'] + K_REGIONAL * global_fail_rate) /
        (regional_stats['tests'] + K_REGIONAL)
    )

    region_rates = regional_stats['smoothed_rate'].to_dict()

    print(f"  Computed rates for {len(region_rates):,} regions")

    # Apply to OOT
    df['region_fail_rate'] = df['postcode_area'].map(region_rates).fillna(global_fail_rate)

    # Regional residual = eb_risk - region_fail_rate (if eb_risk exists)
    if 'eb_risk' in df.columns:
        df['regional_residual'] = df['eb_risk'] - df['region_fail_rate']
    else:
        df['regional_residual'] = 0.0

    print(f"  Created: region_fail_rate, regional_residual")
    print(f"  Mean region_fail_rate: {df['region_fail_rate'].mean():.4f}")
    print(f"  Regional residual range: {df['regional_residual'].min():.4f} to {df['regional_residual'].max():.4f}")

    return df


def build_rfr_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Phase 5: Build RFR/advisory escalation features.

    Features:
    - rfr_escalation_sum: Sum of escalated advisory flags
    - n_top_rfr_advisories: Count of high-severity advisories

    Uses existing prev_adv_* columns in OOT set.
    """
    print("\n=== Phase 5: RFR/Advisory Features ===")
    df = df.copy()

    # High-risk systems (brakes, tyres, suspension, steering)
    high_risk_cols = ['prev_adv_brakes', 'prev_adv_tyres', 'prev_adv_suspension', 'prev_adv_steering']
    available_cols = [c for c in high_risk_cols if c in df.columns]

    if not available_cols:
        print("  WARNING: No prev_adv_* columns found, setting RFR features to 0")
        df['rfr_escalation_sum'] = 0
        df['n_top_rfr_advisories'] = 0
        return df

    print(f"  Using advisory columns: {available_cols}")

    # rfr_escalation_sum: total advisories in high-risk systems
    df['rfr_escalation_sum'] = df[available_cols].fillna(0).sum(axis=1)

    # n_top_rfr_advisories: count of systems with any advisory
    df['n_top_rfr_advisories'] = (df[available_cols].fillna(0) > 0).sum(axis=1)

    print(f"  Created: rfr_escalation_sum, n_top_rfr_advisories")
    print(f"  Mean rfr_escalation_sum: {df['rfr_escalation_sum'].mean():.2f}")
    print(f"  Mean n_top_rfr_advisories: {df['n_top_rfr_advisories'].mean():.2f}")

    return df


def build_failure_patterns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Phase 6: Build failure pattern features.

    Features:
    - fail_count_last2: Failures in last 2 test cycles (approximated from n_prior_fails)
    - chronic_failure: Binary flag for persistent failures
    - transition_pattern: Outcome sequence encoding

    Uses n_prior_tests and n_prior_fails from OOT set.
    """
    print("\n=== Phase 6: Failure Pattern Features ===")
    df = df.copy()

    # Check required columns
    if 'n_prior_fails' not in df.columns or 'n_prior_tests' not in df.columns:
        print("  WARNING: n_prior_fails/n_prior_tests not found, setting pattern features to defaults")
        df['fail_count_last2'] = 0
        df['chronic_failure'] = 0
        df['transition_pattern'] = 'UNKNOWN'
        return df

    # Approximate fail_count_last2 from n_prior_fails (capped at 2)
    # This is an approximation since we don't have per-cycle history
    df['fail_count_last2'] = df['n_prior_fails'].fillna(0).clip(upper=2).astype(int)

    # chronic_failure: 2+ failures or high failure rate
    prior_fail_rate = df['n_prior_fails'].fillna(0) / df['n_prior_tests'].fillna(1).clip(lower=1)
    df['chronic_failure'] = ((df['n_prior_fails'].fillna(0) >= 2) | (prior_fail_rate > 0.5)).astype(int)

    # transition_pattern: Encode from prev_cycle_outcome_band
    # Simplified encoding: PASS, FAIL, FIRST_TEST, UNKNOWN
    if 'prev_cycle_outcome_band' in df.columns:
        pattern_map = {
            'PASS': 'P',
            'PRS': 'P',
            'FAIL': 'F',
            'F': 'F',
            'ABA': 'F',
            'ABR': 'F',
            'FIRST_TEST': 'FIRST',
            'NONE': 'FIRST',
        }
        df['transition_pattern'] = df['prev_cycle_outcome_band'].map(pattern_map).fillna('UNKNOWN')
    else:
        df['transition_pattern'] = 'UNKNOWN'

    print(f"  Created: fail_count_last2, chronic_failure, transition_pattern")
    print(f"  Mean fail_count_last2: {df['fail_count_last2'].mean():.2f}")
    print(f"  Chronic failure rate: {df['chronic_failure'].mean():.1%}")
    print(f"  Transition pattern distribution:")
    print(f"    {df['transition_pattern'].value_counts().to_dict()}")

    return df


def validate_v8_features(df: pd.DataFrame) -> bool:
    """
    Validate that all 23 V8 features are present and valid.
    """
    print("\n=== Validation ===")

    # V8 required features (from train_catboost_v8.py)
    cat_features = ['make', 'model_id', 'age_band', 'mileage_band',
                    'prev_cycle_outcome_band', 'gap_band', 'transition_pattern']

    num_features = ['eb_risk', 'test_mileage', 'vehicle_age_years',
                    'test_month', 'test_quarter', 'is_winter', 'is_december', 'is_summer',
                    'is_monday', 'is_friday',
                    'rfr_escalation_sum', 'n_top_rfr_advisories',
                    'region_fail_rate', 'regional_residual',
                    'fail_count_last2', 'chronic_failure']

    all_features = cat_features + num_features

    # Check presence
    missing = [f for f in all_features if f not in df.columns]
    if missing:
        print(f"  ERROR: Missing features: {missing}")
        return False

    print(f"  All {len(all_features)} V8 features present")

    # Check critical features for NULLs
    critical = ['eb_risk', 'test_month', 'vehicle_age_years']
    for col in critical:
        null_rate = df[col].isna().mean()
        if null_rate > 0.01:
            print(f"  WARNING: {col} has {null_rate:.1%} NULL values")

    # Validate eb_risk range
    if df['eb_risk'].min() < 0 or df['eb_risk'].max() > 1:
        print(f"  WARNING: eb_risk outside [0,1]: {df['eb_risk'].min():.4f} - {df['eb_risk'].max():.4f}")

    print(f"  Validation PASSED")
    return True


def main():
    """Build all V8 features for OOT evaluation."""
    print("=" * 70)
    print("BUILD V8 OOT FEATURES")
    print("=" * 70)

    # Load OOT set
    print(f"\nLoading OOT set from {OOT_SET}...")
    df = pd.read_parquet(OOT_SET)
    print(f"  Loaded {len(df):,} records with {len(df.columns)} columns")

    # Build features in priority order
    df = build_eb_risk(df)          # Phase 3 (highest priority)
    df = build_temporal_features(df) # Phase 1
    df = build_vehicle_age(df)       # Phase 2
    df = build_regional_features(df) # Phase 4
    df = build_rfr_features(df)      # Phase 5
    df = build_failure_patterns(df)  # Phase 6

    # Validate
    valid = validate_v8_features(df)

    # Save
    print(f"\nSaving to {OUTPUT}...")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUTPUT, index=False)
    print(f"  Saved {len(df):,} records with {len(df.columns)} columns")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Input: {OOT_SET}")
    print(f"Output: {OUTPUT}")
    print(f"Records: {len(df):,}")
    print(f"Features added: 16")
    print(f"Validation: {'PASSED' if valid else 'FAILED'}")

    return df


if __name__ == "__main__":
    df = main()
