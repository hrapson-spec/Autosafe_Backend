"""
Advisory Signal Validation

Three high-value validation checks to ensure advisory features add genuine predictive signal:

1. Within-strata monotonicity: Compute fail rate by advisory band WITHIN age bands.
   If the gradient persists inside strata, advisories add independent signal
   rather than just proxying for age.

2. NO_HISTORY decomposition: Split NO_HISTORY into:
   - FIRST_TEST: True first observed MOT (vehicle age <= 3 years, first MOT due)
   - LINKAGE_FAIL: Should have history but join failed (vehicle age > 3 years)

   This prevents weight drift across DEV/OOT as the linkage failure mix changes.

3. Bucket stability check: Compare fail rates for each bucket across DEV/OOT years.
   If a bucket's fail rate shifts dramatically (e.g., 23.9% -> 11.9%), it is NOT
   a stable semantic group - it's partly a function of dataset window/linkage mechanics.
   Unstable buckets should not receive fixed weights as they will wash out incremental
   gains from other features and distort calibration.
"""

import pandas as pd
import numpy as np
import logging
import os
from typing import Dict, Tuple, Optional, List
from utils import get_age_band, get_mileage_band

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("advisory_validation.log"),
        logging.StreamHandler()
    ]
)


class ValidationConfig:
    """Configuration for advisory signal validation."""

    # Data sources (same as main pipeline)
    RESULTS_SOURCES = [
        ("MOT Test Results", ",", "test_result_*.csv"),
        ("MOT Test Results/2023", "|", "test_result.csv"),
        ("MOT Test Results/2022", "|", "test_result_2022.csv"),
    ]

    # Advisory data file (if available)
    ADVISORIES_FILE = "advisories_summary.csv"

    # Previous test linkage file (if available)
    PREV_TEST_FILE = "model_dataset_with_prev_condition.parquet"

    # Processing
    CHUNK_SIZE = 500_000

    # Age threshold for FIRST_TEST vs LINKAGE_FAIL
    # UK MOT is first required at 3 years old
    FIRST_MOT_AGE_THRESHOLD = 3.5  # years (with buffer for timing)

    # Advisory count bands
    ADVISORY_BANDS = {
        '0': lambda x: x == 0,
        '1-2': lambda x: (x >= 1) & (x <= 2),
        '3-4': lambda x: (x >= 3) & (x <= 4),
        '5+': lambda x: x >= 5,
    }

    # Bucket stability thresholds
    # If fail rate shifts by more than this between DEV/OOT, bucket is unstable
    STABILITY_THRESHOLD = 0.05  # 5 percentage points

    # DEV/OOT year configuration
    DEV_YEAR = 2023
    OOT_YEAR = 2024


def get_advisory_band(count: int) -> str:
    """Convert advisory count to band."""
    if pd.isna(count) or count == 0:
        return '0'
    elif count <= 2:
        return '1-2'
    elif count <= 4:
        return '3-4'
    else:
        return '5+'


def classify_history_status(age_years: float, has_prev_test: bool) -> str:
    """
    Classify a record's history status.

    Args:
        age_years: Vehicle age at time of test
        has_prev_test: Whether we found a previous test record

    Returns:
        One of: 'HAS_HISTORY', 'FIRST_TEST', 'LINKAGE_FAIL'
    """
    if has_prev_test:
        return 'HAS_HISTORY'

    # No previous test found
    if pd.isna(age_years):
        return 'LINKAGE_FAIL'  # Can't determine, assume linkage issue

    # Vehicles under ~3.5 years are having their first MOT
    if age_years <= ValidationConfig.FIRST_MOT_AGE_THRESHOLD:
        return 'FIRST_TEST'
    else:
        return 'LINKAGE_FAIL'


def load_test_data_with_advisories(sample_frac: float = 0.1) -> pd.DataFrame:
    """
    Load test data and merge with advisory counts.

    Args:
        sample_frac: Fraction of data to sample (for faster iteration)

    Returns:
        DataFrame with test results and advisory counts
    """
    import glob

    logging.info("Loading test result data...")

    # Collect all files
    file_sources = []
    for folder, delimiter, pattern in ValidationConfig.RESULTS_SOURCES:
        file_pattern = os.path.join(folder, pattern)
        matched_files = glob.glob(file_pattern)
        for f in matched_files:
            file_sources.append((f, delimiter))

    if not file_sources:
        logging.error("No test result files found")
        return pd.DataFrame()

    logging.info(f"Found {len(file_sources)} result files")

    # Load test results
    chunks = []
    for filename, sep in file_sources:
        logging.info(f"Reading {os.path.basename(filename)}...")
        cols = ['test_id', 'vehicle_id', 'test_date', 'first_use_date',
                'test_mileage', 'make', 'model', 'test_result']

        try:
            for chunk in pd.read_csv(filename, sep=sep, usecols=cols,
                                     chunksize=ValidationConfig.CHUNK_SIZE, low_memory=False):
                # Sample for faster processing
                if sample_frac < 1.0:
                    chunk = chunk.sample(frac=sample_frac, random_state=42)

                chunk['test_id'] = pd.to_numeric(chunk['test_id'], errors='coerce').fillna(0).astype('int64')
                chunk['vehicle_id'] = pd.to_numeric(chunk['vehicle_id'], errors='coerce').fillna(0).astype('int64')
                chunk['test_date'] = pd.to_datetime(chunk['test_date'], errors='coerce')
                chunk['first_use_date'] = pd.to_datetime(chunk['first_use_date'], errors='coerce')
                chunk['age_years'] = (chunk['test_date'] - chunk['first_use_date']).dt.days / 365.25
                chunk['age_band'] = chunk['age_years'].apply(get_age_band)
                chunk['test_mileage'] = pd.to_numeric(chunk['test_mileage'], errors='coerce')
                chunk['mileage_band'] = chunk['test_mileage'].apply(get_mileage_band)
                chunk['is_failure'] = chunk['test_result'].isin(['F', 'FAIL', 'PRS', 'ABA']).astype(int)
                chunk['test_year'] = chunk['test_date'].dt.year

                chunks.append(chunk[['test_id', 'vehicle_id', 'test_date', 'test_year',
                                    'age_years', 'age_band', 'mileage_band',
                                    'make', 'model', 'is_failure']])
        except Exception as e:
            logging.warning(f"Error reading {filename}: {e}")
            continue

    if not chunks:
        logging.error("No data loaded")
        return pd.DataFrame()

    df = pd.concat(chunks, ignore_index=True)
    logging.info(f"Loaded {len(df):,} test records")

    # Try to load and merge advisory counts
    if os.path.exists(ValidationConfig.ADVISORIES_FILE):
        logging.info(f"Loading advisory data from {ValidationConfig.ADVISORIES_FILE}...")
        try:
            adv_df = pd.read_csv(ValidationConfig.ADVISORIES_FILE,
                                dtype={'test_id': 'int64'})

            # Check what columns exist
            if 'advisory_count' in adv_df.columns:
                adv_cols = ['test_id', 'advisory_count']
            elif 'count' in adv_df.columns:
                adv_df['advisory_count'] = adv_df['count']
                adv_cols = ['test_id', 'advisory_count']
            else:
                # Sum all numeric columns except test_id as advisory count
                numeric_cols = adv_df.select_dtypes(include=[np.number]).columns.tolist()
                numeric_cols = [c for c in numeric_cols if c != 'test_id']
                if numeric_cols:
                    adv_df['advisory_count'] = adv_df[numeric_cols].sum(axis=1)
                    adv_cols = ['test_id', 'advisory_count']
                else:
                    logging.warning("Cannot determine advisory count column")
                    adv_df['advisory_count'] = 0
                    adv_cols = ['test_id', 'advisory_count']

            df = df.merge(adv_df[adv_cols], on='test_id', how='left')
            df['advisory_count'] = df['advisory_count'].fillna(0).astype(int)
            df['advisory_band'] = df['advisory_count'].apply(get_advisory_band)

            logging.info(f"Merged advisory data. Coverage: {(df['advisory_count'] > 0).mean():.1%}")
        except Exception as e:
            logging.warning(f"Error loading advisories: {e}")
            df['advisory_count'] = 0
            df['advisory_band'] = '0'
    else:
        logging.warning(f"Advisory file not found: {ValidationConfig.ADVISORIES_FILE}")
        df['advisory_count'] = 0
        df['advisory_band'] = '0'

    return df


def check_within_strata_monotonicity(df: pd.DataFrame) -> Dict:
    """
    Check 1: Within-strata monotonicity

    Compute fail rate by advisory band WITHIN each age band.
    If gradient persists within strata, advisories add independent signal.

    Returns:
        Dict with results for each age band and overall assessment
    """
    logging.info("\n" + "="*70)
    logging.info("CHECK 1: WITHIN-STRATA MONOTONICITY")
    logging.info("="*70)

    results = {
        'by_age_band': {},
        'by_mileage_band': {},
        'overall_gradient': None,
        'within_strata_gradient': None,
        'is_independent_signal': None
    }

    # Define band ordering for monotonicity check
    advisory_order = ['0', '1-2', '3-4', '5+']
    age_band_order = ['0-3', '3-5', '6-10', '10-15', '15+']
    mileage_band_order = ['0-30k', '30k-60k', '60k-100k', '100k+']

    # Overall gradient (not controlling for age)
    print("\n--- OVERALL FAIL RATE BY ADVISORY BAND (not controlling for age) ---")
    print(f"{'Advisory Band':<15} {'N':>12} {'Fail Rate':>12} {'95% CI':>15}")
    print("-" * 55)

    overall_stats = df.groupby('advisory_band').agg(
        n=('is_failure', 'count'),
        failures=('is_failure', 'sum'),
        fail_rate=('is_failure', 'mean')
    ).reindex(advisory_order)

    overall_rates = []
    for band in advisory_order:
        if band in overall_stats.index:
            row = overall_stats.loc[band]
            n, rate = int(row['n']), row['fail_rate']
            # Wilson score CI
            if n > 0:
                z = 1.96
                p = rate
                ci_low = (p + z*z/(2*n) - z*np.sqrt((p*(1-p) + z*z/(4*n))/n)) / (1 + z*z/n)
                ci_high = (p + z*z/(2*n) + z*np.sqrt((p*(1-p) + z*z/(4*n))/n)) / (1 + z*z/n)
                ci_str = f"[{ci_low:.3f}, {ci_high:.3f}]"
            else:
                ci_str = "N/A"
            print(f"{band:<15} {n:>12,} {rate:>12.3f} {ci_str:>15}")
            overall_rates.append(rate)

    # Check if overall gradient is monotonic increasing
    overall_monotonic = all(overall_rates[i] <= overall_rates[i+1]
                          for i in range(len(overall_rates)-1))
    results['overall_gradient'] = {
        'rates': overall_rates,
        'is_monotonic': overall_monotonic
    }
    print(f"\nOverall gradient monotonic: {overall_monotonic}")

    # Within age band analysis
    print("\n--- FAIL RATE BY ADVISORY BAND WITHIN AGE BANDS ---")

    within_strata_monotonic_count = 0
    within_strata_total = 0

    for age_band in age_band_order:
        subset = df[df['age_band'] == age_band]
        if len(subset) < 1000:
            continue

        print(f"\n  Age Band: {age_band} (n={len(subset):,})")
        print(f"  {'Advisory':<12} {'N':>10} {'Fail Rate':>10} {'Gradient':>10}")
        print("  " + "-" * 45)

        stats = subset.groupby('advisory_band').agg(
            n=('is_failure', 'count'),
            fail_rate=('is_failure', 'mean')
        ).reindex(advisory_order)

        rates = []
        prev_rate = None
        for band in advisory_order:
            if band in stats.index and stats.loc[band, 'n'] >= 100:
                n = int(stats.loc[band, 'n'])
                rate = stats.loc[band, 'fail_rate']

                if prev_rate is not None:
                    gradient = rate - prev_rate
                    gradient_str = f"{gradient:+.3f}"
                else:
                    gradient_str = "-"

                print(f"  {band:<12} {n:>10,} {rate:>10.3f} {gradient_str:>10}")
                rates.append(rate)
                prev_rate = rate

        # Check monotonicity within this age band
        if len(rates) >= 2:
            is_monotonic = all(rates[i] <= rates[i+1] for i in range(len(rates)-1))
            results['by_age_band'][age_band] = {
                'rates': rates,
                'is_monotonic': is_monotonic
            }
            within_strata_total += 1
            if is_monotonic:
                within_strata_monotonic_count += 1
            print(f"  Monotonic within {age_band}: {is_monotonic}")

    # Within mileage band analysis
    print("\n--- FAIL RATE BY ADVISORY BAND WITHIN MILEAGE BANDS ---")

    for mileage_band in mileage_band_order:
        subset = df[df['mileage_band'] == mileage_band]
        if len(subset) < 1000:
            continue

        print(f"\n  Mileage Band: {mileage_band} (n={len(subset):,})")
        print(f"  {'Advisory':<12} {'N':>10} {'Fail Rate':>10} {'Gradient':>10}")
        print("  " + "-" * 45)

        stats = subset.groupby('advisory_band').agg(
            n=('is_failure', 'count'),
            fail_rate=('is_failure', 'mean')
        ).reindex(advisory_order)

        rates = []
        prev_rate = None
        for band in advisory_order:
            if band in stats.index and stats.loc[band, 'n'] >= 100:
                n = int(stats.loc[band, 'n'])
                rate = stats.loc[band, 'fail_rate']

                if prev_rate is not None:
                    gradient = rate - prev_rate
                    gradient_str = f"{gradient:+.3f}"
                else:
                    gradient_str = "-"

                print(f"  {band:<12} {n:>10,} {rate:>10.3f} {gradient_str:>10}")
                rates.append(rate)
                prev_rate = rate

        if len(rates) >= 2:
            is_monotonic = all(rates[i] <= rates[i+1] for i in range(len(rates)-1))
            results['by_mileage_band'][mileage_band] = {
                'rates': rates,
                'is_monotonic': is_monotonic
            }
            within_strata_total += 1
            if is_monotonic:
                within_strata_monotonic_count += 1
            print(f"  Monotonic within {mileage_band}: {is_monotonic}")

    # Summary
    if within_strata_total > 0:
        monotonic_pct = within_strata_monotonic_count / within_strata_total
        results['within_strata_gradient'] = {
            'monotonic_count': within_strata_monotonic_count,
            'total_strata': within_strata_total,
            'monotonic_pct': monotonic_pct
        }
        results['is_independent_signal'] = monotonic_pct >= 0.7  # >70% strata show gradient

        print(f"\n{'='*70}")
        print("MONOTONICITY SUMMARY")
        print(f"{'='*70}")
        print(f"Strata with monotonic gradient: {within_strata_monotonic_count}/{within_strata_total} ({monotonic_pct:.1%})")
        print(f"Advisory adds independent signal: {results['is_independent_signal']}")
        print(f"{'='*70}")

    return results


def check_no_history_decomposition(df: pd.DataFrame,
                                   prev_test_linkage: Optional[pd.DataFrame] = None) -> Dict:
    """
    Check 2: NO_HISTORY decomposition

    Split records without previous test linkage into:
    - FIRST_TEST: True first MOT (age <= 3.5 years)
    - LINKAGE_FAIL: Should have history but join failed (age > 3.5 years)

    Args:
        df: Test data with age information
        prev_test_linkage: Optional DataFrame with prev_test_id column

    Returns:
        Dict with decomposition stats and recommendations
    """
    logging.info("\n" + "="*70)
    logging.info("CHECK 2: NO_HISTORY DECOMPOSITION")
    logging.info("="*70)

    results = {
        'by_year': {},
        'overall': {},
        'weight_drift_risk': None
    }

    # If we have prev_test linkage data, use it
    if prev_test_linkage is not None and 'prev_test_id' in prev_test_linkage.columns:
        logging.info("Using provided prev_test linkage data")
        df = df.merge(prev_test_linkage[['test_id', 'prev_test_id']],
                     on='test_id', how='left')
        df['has_prev_test'] = df['prev_test_id'].notna()
    else:
        # Simulate: assume we have no prev_test linkage (worst case)
        # In reality, you'd join to your prev_test table
        logging.info("No prev_test linkage provided - using age-based classification")
        df['has_prev_test'] = False

    # Classify each record
    df['history_status'] = df.apply(
        lambda row: classify_history_status(row['age_years'], row['has_prev_test']),
        axis=1
    )

    # Overall decomposition
    print("\n--- OVERALL NO_HISTORY DECOMPOSITION ---")
    print(f"{'Status':<20} {'N':>12} {'%':>8} {'Fail Rate':>12}")
    print("-" * 55)

    status_stats = df.groupby('history_status').agg(
        n=('is_failure', 'count'),
        fail_rate=('is_failure', 'mean')
    )

    total = len(df)
    for status in ['HAS_HISTORY', 'FIRST_TEST', 'LINKAGE_FAIL']:
        if status in status_stats.index:
            n = int(status_stats.loc[status, 'n'])
            pct = n / total * 100
            rate = status_stats.loc[status, 'fail_rate']
            print(f"{status:<20} {n:>12,} {pct:>7.1f}% {rate:>12.3f}")
            results['overall'][status] = {'n': n, 'pct': pct, 'fail_rate': rate}

    # Decomposition by year
    print("\n--- NO_HISTORY DECOMPOSITION BY TEST YEAR ---")
    print(f"{'Year':<8} {'HAS_HISTORY':>15} {'FIRST_TEST':>15} {'LINKAGE_FAIL':>15} {'Linkage Rate':>15}")
    print("-" * 70)

    year_decomp = df.groupby(['test_year', 'history_status']).size().unstack(fill_value=0)

    linkage_rates = {}
    for year in sorted(df['test_year'].dropna().unique()):
        year = int(year)
        if year not in year_decomp.index:
            continue

        row = year_decomp.loc[year]
        has_hist = row.get('HAS_HISTORY', 0)
        first_test = row.get('FIRST_TEST', 0)
        linkage_fail = row.get('LINKAGE_FAIL', 0)

        # Linkage rate: of records that SHOULD have history (age > 3.5), how many do?
        should_have = has_hist + linkage_fail
        linkage_rate = has_hist / should_have if should_have > 0 else 0
        linkage_rates[year] = linkage_rate

        print(f"{year:<8} {has_hist:>15,} {first_test:>15,} {linkage_fail:>15,} {linkage_rate:>14.1%}")

        results['by_year'][year] = {
            'HAS_HISTORY': int(has_hist),
            'FIRST_TEST': int(first_test),
            'LINKAGE_FAIL': int(linkage_fail),
            'linkage_rate': linkage_rate
        }

    # Fail rate by history status and year
    print("\n--- FAIL RATE BY HISTORY STATUS AND YEAR ---")
    print(f"{'Year':<8} {'HAS_HISTORY':>15} {'FIRST_TEST':>15} {'LINKAGE_FAIL':>15}")
    print("-" * 55)

    fail_rates = df.groupby(['test_year', 'history_status'])['is_failure'].mean().unstack()

    for year in sorted(df['test_year'].dropna().unique()):
        year = int(year)
        if year not in fail_rates.index:
            continue

        row = fail_rates.loc[year]
        has_hist = row.get('HAS_HISTORY', np.nan)
        first_test = row.get('FIRST_TEST', np.nan)
        linkage_fail = row.get('LINKAGE_FAIL', np.nan)

        has_hist_str = f"{has_hist:.3f}" if not pd.isna(has_hist) else "N/A"
        first_test_str = f"{first_test:.3f}" if not pd.isna(first_test) else "N/A"
        linkage_fail_str = f"{linkage_fail:.3f}" if not pd.isna(linkage_fail) else "N/A"

        print(f"{year:<8} {has_hist_str:>15} {first_test_str:>15} {linkage_fail_str:>15}")

    # Weight drift risk assessment
    if linkage_rates:
        min_linkage = min(linkage_rates.values())
        max_linkage = max(linkage_rates.values())
        linkage_variance = max_linkage - min_linkage

        # High drift risk if linkage rates vary significantly across years
        high_drift_risk = linkage_variance > 0.3

        results['weight_drift_risk'] = {
            'min_linkage_rate': min_linkage,
            'max_linkage_rate': max_linkage,
            'variance': linkage_variance,
            'is_high_risk': high_drift_risk
        }

        print(f"\n{'='*70}")
        print("WEIGHT DRIFT RISK ASSESSMENT")
        print(f"{'='*70}")
        print(f"Linkage rate range: {min_linkage:.1%} - {max_linkage:.1%}")
        print(f"Variance: {linkage_variance:.1%}")
        print(f"High drift risk: {high_drift_risk}")

        if high_drift_risk:
            print("\n*** WARNING: High weight drift risk detected! ***")
            print("The NO_HISTORY mix changes significantly across years.")
            print("Recommendation: Split NO_HISTORY into FIRST_TEST and LINKAGE_FAIL")
            print("to prevent learned weights from drifting across DEV/OOT.")

    print(f"{'='*70}")

    return results


def check_bucket_stability(df: pd.DataFrame) -> Dict:
    """
    Check 3: Bucket stability across DEV/OOT

    Compare fail rates for each history_status bucket between DEV and OOT years.
    Unstable buckets (large fail rate shift) should not receive fixed weights.

    Key insight: If LINKAGE_FAIL shows 23.9% fail rate in DEV but 11.9% in OOT,
    it's not a stable semantic group - it's partly a function of linkage mechanics.

    Args:
        df: DataFrame with 'test_year', 'history_status', 'is_failure' columns

    Returns:
        Dict with stability assessment per bucket
    """
    logging.info("\n" + "="*70)
    logging.info("CHECK 3: BUCKET STABILITY ACROSS DEV/OOT")
    logging.info("="*70)

    results = {
        'by_bucket': {},
        'unstable_buckets': [],
        'recommendation': None
    }

    dev_year = ValidationConfig.DEV_YEAR
    oot_year = ValidationConfig.OOT_YEAR
    threshold = ValidationConfig.STABILITY_THRESHOLD

    # Filter to DEV and OOT years
    dev_df = df[df['test_year'] == dev_year]
    oot_df = df[df['test_year'] == oot_year]

    if len(dev_df) == 0:
        logging.warning(f"No data for DEV year {dev_year}")
        return results
    if len(oot_df) == 0:
        logging.warning(f"No data for OOT year {oot_year}")
        return results

    print(f"\nComparing fail rates between DEV ({dev_year}) and OOT ({oot_year})")
    print(f"Stability threshold: {threshold:.1%} fail rate shift")
    print()
    print(f"{'Bucket':<20} {'DEV N':>12} {'DEV Rate':>10} {'OOT N':>12} {'OOT Rate':>10} {'Shift':>10} {'Status':>12}")
    print("-" * 90)

    # Get all unique buckets
    all_buckets = df['history_status'].dropna().unique()

    for bucket in ['HAS_HISTORY', 'FIRST_TEST', 'LINKAGE_FAIL']:
        if bucket not in all_buckets:
            continue

        dev_bucket = dev_df[dev_df['history_status'] == bucket]
        oot_bucket = oot_df[oot_df['history_status'] == bucket]

        dev_n = len(dev_bucket)
        oot_n = len(oot_bucket)

        if dev_n < 100 or oot_n < 100:
            status = "SKIP (n<100)"
            print(f"{bucket:<20} {dev_n:>12,} {'N/A':>10} {oot_n:>12,} {'N/A':>10} {'N/A':>10} {status:>12}")
            continue

        dev_rate = dev_bucket['is_failure'].mean()
        oot_rate = oot_bucket['is_failure'].mean()
        shift = abs(dev_rate - oot_rate)

        is_stable = shift <= threshold
        status = "STABLE" if is_stable else "UNSTABLE"

        if not is_stable:
            results['unstable_buckets'].append(bucket)

        results['by_bucket'][bucket] = {
            'dev_n': int(dev_n),
            'dev_rate': float(dev_rate),
            'oot_n': int(oot_n),
            'oot_rate': float(oot_rate),
            'shift': float(shift),
            'is_stable': is_stable
        }

        shift_str = f"{shift:+.1%}" if dev_rate <= oot_rate else f"{-shift:+.1%}"
        print(f"{bucket:<20} {dev_n:>12,} {dev_rate:>10.1%} {oot_n:>12,} {oot_rate:>10.1%} {shift_str:>10} {status:>12}")

    # Summary and recommendations
    print()
    print(f"{'='*70}")
    print("BUCKET STABILITY SUMMARY")
    print(f"{'='*70}")

    if results['unstable_buckets']:
        print(f"\n*** UNSTABLE BUCKETS DETECTED: {results['unstable_buckets']} ***")
        print()
        print("These buckets have fail rates that shift significantly between DEV and OOT.")
        print("This indicates they are NOT stable semantic groups - they're partly a function")
        print("of dataset window and linkage mechanics.")
        print()
        print("IMPLICATIONS:")
        print("  1. Any fixed weight learned on DEV will be miscalibrated on OOT")
        print("  2. Learned weights for unstable buckets can wash out gains from other features")
        print("  3. If two buckets have similar weights despite 2x different fail rates,")
        print("     the weights may not be fit correctly or features are interacting strongly")
        print()
        print("RECOMMENDATIONS:")
        print("  1. DO NOT use unstable buckets as categorical features with fixed weights")
        print("  2. Instead, impute missing values with age-band or segment-level averages")
        print("  3. Or use the bucket only for records where linkage is known to work")

        results['recommendation'] = 'DO_NOT_USE_UNSTABLE_BUCKETS'
    else:
        print("\nAll buckets are stable across DEV/OOT.")
        results['recommendation'] = 'BUCKETS_OK'

    print(f"{'='*70}")

    return results


def run_advisory_validation(sample_frac: float = 0.1):
    """
    Run all advisory signal validation checks.

    Args:
        sample_frac: Fraction of data to sample (default 10% for speed)
    """
    logging.info("="*70)
    logging.info("ADVISORY SIGNAL VALIDATION")
    logging.info(f"Sample fraction: {sample_frac:.0%}")
    logging.info("="*70)

    # Load data
    df = load_test_data_with_advisories(sample_frac=sample_frac)
    if df.empty:
        logging.error("No data loaded - cannot run validation")
        return None

    logging.info(f"Loaded {len(df):,} records for validation")
    logging.info(f"Years: {sorted(df['test_year'].dropna().unique())}")

    # Run checks
    results = {}

    # Check 1: Within-strata monotonicity
    results['monotonicity'] = check_within_strata_monotonicity(df)

    # Check 2: NO_HISTORY decomposition
    # Try to load prev_test linkage if available
    prev_test_df = None
    if os.path.exists(ValidationConfig.PREV_TEST_FILE):
        try:
            prev_test_df = pd.read_parquet(ValidationConfig.PREV_TEST_FILE,
                                           columns=['test_id', 'prev_test_id'])
            logging.info(f"Loaded prev_test linkage from {ValidationConfig.PREV_TEST_FILE}")
        except Exception as e:
            logging.warning(f"Could not load prev_test file: {e}")

    results['no_history'] = check_no_history_decomposition(df, prev_test_df)

    # Check 3: Bucket stability (requires history_status from Check 2)
    # Re-classify if not already done
    if 'history_status' not in df.columns:
        df['has_prev_test'] = False  # Assume no linkage for stability check
        df['history_status'] = df.apply(
            lambda row: classify_history_status(row['age_years'], row['has_prev_test']),
            axis=1
        )
    results['bucket_stability'] = check_bucket_stability(df)

    # Summary
    print("\n" + "="*70)
    print("VALIDATION SUMMARY")
    print("="*70)

    mono_result = results['monotonicity'].get('is_independent_signal')
    drift_result = results['no_history'].get('weight_drift_risk', {}).get('is_high_risk')
    unstable_buckets = results['bucket_stability'].get('unstable_buckets', [])

    print(f"1. Advisory adds independent signal: {mono_result}")
    print(f"2. High weight drift risk: {drift_result}")
    print(f"3. Unstable buckets: {unstable_buckets if unstable_buckets else 'None'}")

    if mono_result and not drift_result and not unstable_buckets:
        print("\n*** PASS: Advisory features are valid for production ***")
    elif mono_result and (drift_result or unstable_buckets):
        print("\n*** CONDITIONAL PASS: Advisory signal is real, but bucket instability detected ***")
        if unstable_buckets:
            print(f"    Do NOT use {unstable_buckets} as categorical features with fixed weights")
            print("    Instead, impute with age-band/segment averages for records with linkage failure")
    elif not mono_result:
        print("\n*** FAIL: Advisory may just be proxying for age - investigate further ***")

    print("="*70)

    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Validate advisory feature signal")
    parser.add_argument("--sample", type=float, default=0.1,
                       help="Sample fraction for faster processing (default: 0.1)")
    parser.add_argument("--full", action="store_true",
                       help="Run on full dataset (overrides --sample)")
    args = parser.parse_args()

    sample_frac = 1.0 if args.full else args.sample
    run_advisory_validation(sample_frac=sample_frac)
