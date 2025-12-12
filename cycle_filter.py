"""
Cycle Filter Module

Filters MOT test data to retain only the "first test in a cycle" per vehicle,
removing retests that inflate false positive rates and suppress AUC.

A new cycle starts when:
1. It's the first test ever for a vehicle, OR
2. The previous test was a PASS, OR
3. More than CYCLE_GAP_DAYS (default 120) have passed since the last test.

Usage:
    from cycle_filter import build_cycle_index, load_cycle_index
    
    # Build once (slow, ~5 min for 150M records)
    build_cycle_index(output_path='cycle_first_tests.parquet')
    
    # Load for filtering (fast)
    valid_ids = load_cycle_index('cycle_first_tests.parquet')
"""

import pandas as pd
import numpy as np
import os
import glob
import logging
from typing import Set, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# Configuration
CYCLE_GAP_DAYS = 120  # Gap threshold for new cycle
PASS_CODES = {'P', 'PASS'}  # Test results that reset the cycle

# Source configuration (matches calculate_risk.py)
RESULTS_SOURCES = [
    ("MOT Test Results", ",", "test_result_*.csv"),   # 2024 monthly files
    ("MOT Test Results/2023", "|", "test_result.csv"),
    ("MOT Test Results/2022", "|", "test_result_2022.csv"),
]

# Columns needed for cycle detection
CYCLE_COLUMNS = ['test_id', 'vehicle_id', 'test_date', 'test_result']

# Priority for same-day collapsing: worst outcome wins (higher = worse)
RESULT_PRIORITY = {
    'F': 4, 'FAIL': 4,      # Worst - definite failure
    'PRS': 3,                # Partial repairs still needed
    'ABA': 2,                # Abandoned - unclear outcome
    'P': 1, 'PASS': 1,       # Best - passed
}
# Integer code for PASS
PASS_INT = 1


def collapse_same_day(df: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse multiple tests on the same vehicle+date to one record using 'worst-of-day' rule.
    
    Priority: FAIL (4) > PRS (3) > ABA (2) > PASS (1) (higher value = kept)
    
    Args:
        df: DataFrame with test_id, vehicle_id, test_date, test_result (int8) columns
    
    Returns:
        DataFrame with at most one test per vehicle per day
    """
    if df.empty:
        return df
    
    # Sort by vehicle_id, test_date, test_result (descending priority), test_id
    # Note: test_result is now already int8 priority (4=FAIL, 1=PASS)
    df = df.sort_values(
        ['vehicle_id', 'test_date', 'test_result', 'test_id'],
        ascending=[True, True, False, True]
    )
    
    # Keep first (highest priority) per vehicle+date
    df = df.drop_duplicates(subset=['vehicle_id', 'test_date'], keep='first')
    
    return df


def assign_cycles(df: pd.DataFrame, gap_days: int = CYCLE_GAP_DAYS, min_days_gap: int = 2) -> pd.DataFrame:
    """
    Assign cycle IDs and is_cycle_first flag to a sorted DataFrame.
    
    Memory-optimized version using numpy arrays directly.
    
    Args:
        df: DataFrame sorted by vehicle_id, test_date with columns:
            test_id, vehicle_id, test_date, test_result (int8)
        gap_days: Days of gap that triggers a new cycle
        min_days_gap: Minimum days required after PASS for a new cycle to start.
    
    Returns:
        DataFrame with 'cycle_id' and 'is_cycle_first' columns added
    """
    n = len(df)
    if n == 0:
        df['cycle_id'] = pd.Series(dtype='int64')
        df['is_cycle_first'] = pd.Series(dtype='bool')
        return df
    
    # Extract numpy arrays (views, no copy)
    vehicle_ids = df['vehicle_id'].values
    test_dates = df['test_date'].values  # datetime64[ns]
    test_results = df['test_result'].values  # int8
    
    # Pre-allocate output array (112M bools = 112MB)
    is_cycle_first = np.zeros(n, dtype=bool)
    is_cycle_first[0] = True  # First row is always cycle-first
    
    # Vectorized computation using shifted arrays
    # Different vehicle -> new cycle
    diff_vehicle = vehicle_ids[1:] != vehicle_ids[:-1]
    
    # Days gap calculation (vectorized)
    # datetime64 subtraction gives timedelta64, then extract days
    days_gap = (test_dates[1:] - test_dates[:-1]).astype('timedelta64[D]').astype(np.int32)
    
    # Previous result was PASS (1) and gap >= min_days_gap
    prev_was_pass = test_results[:-1] == PASS_INT
    pass_with_gap = prev_was_pass & (days_gap >= min_days_gap)
    
    # Gap exceeds threshold
    long_gap = days_gap > gap_days
    
    # Combine conditions
    is_cycle_first[1:] = diff_vehicle | pass_with_gap | long_gap
    
    # Add columns to DataFrame (no copy of the DataFrame itself)
    df['is_cycle_first'] = is_cycle_first
    df['cycle_id'] = is_cycle_first.cumsum()
    
    return df


def build_cycle_index(
    output_path: str = 'cycle_first_tests.parquet',
    cutoff_date: Optional[str] = None,
    sources: list = None
) -> int:
    """
    Build an index of cycle-first test IDs from raw CSV files.
    
    Args:
        output_path: Where to save the Parquet index
        cutoff_date: Optional YYYY-MM-DD date to filter tests
        sources: Override source configuration
    
    Returns:
        Number of cycle-first tests identified
    """
    if sources is None:
        sources = RESULTS_SOURCES
    
    start_time = datetime.now()
    logger.info(f"Building cycle index from {len(sources)} source configs...")
    
    # Collect all files
    file_sources = []
    for folder, delimiter, pattern in sources:
        file_pattern = os.path.join(folder, pattern)
        matched_files = glob.glob(file_pattern)
        for f in matched_files:
            file_sources.append((f, delimiter))
        logger.info(f"Found {len(matched_files)} files in '{folder}'")
    
    if not file_sources:
        raise ValueError("No CSV files found in source folders")
    
    logger.info(f"Total: {len(file_sources)} result files to process")
    
    # Load all test metadata (lightweight columns only)
    all_tests = []
    total_rows = 0
    
    for filename, sep in file_sources:
        logger.info(f"Reading {os.path.basename(filename)}...")
        
        try:
            # OPTIMIZATION: Process in chunks to minimize memory usage
            chunk_iterator = pd.read_csv(
                filename, 
                sep=sep, 
                usecols=CYCLE_COLUMNS,
                low_memory=False,
                chunksize=1_000_000  # Process 1M rows at a time
            )
            
            file_rows = 0
            for chunk in chunk_iterator:
                # Convert types
                chunk['test_id'] = pd.to_numeric(chunk['test_id'], errors='coerce').fillna(0).astype('int64')
                chunk['vehicle_id'] = pd.to_numeric(chunk['vehicle_id'], errors='coerce').fillna(0).astype('int64')
                chunk['test_date'] = pd.to_datetime(chunk['test_date'], errors='coerce')
                
                # MEMORY OPTIMIZATION: Convert string result to int8 immediately
                # 4=FAIL, 1=PASS, 0=Unknown
                chunk['test_result'] = chunk['test_result'].astype(str).str.upper().map(RESULT_PRIORITY).fillna(0).astype('int8')
                
                # Drop invalid rows
                chunk = chunk.dropna(subset=['test_date', 'vehicle_id'])
                chunk = chunk[chunk['vehicle_id'] > 0]
                
                # Apply cutoff
                if cutoff_date:
                    cutoff = pd.to_datetime(cutoff_date)
                    chunk = chunk[chunk['test_date'] <= cutoff]
                
                # Collapse same-day tests (worst-of-day rule) on the chunk
                # Note: Cross-chunk same-day tests for the same vehicle are rare
                # and handled by the final collapse after combining all data.
                chunk = collapse_same_day(chunk)
                
                if not chunk.empty:
                    all_tests.append(chunk)
                    file_rows += len(chunk)
            
            total_rows += file_rows
            logger.info(f"  Loaded {file_rows:,} valid rows (chunked & collapsed)")
            
        except Exception as e:
            logger.error(f"Error reading {filename}: {e}")
            raise
    
    logger.info(f"Combining {total_rows:,} total rows...")
    combined = pd.concat(all_tests, ignore_index=True)
    del all_tests  # Free memory
    
    # Sort and assign cycles
    logger.info("Sorting by vehicle_id, test_date...")
    combined = combined.sort_values(['vehicle_id', 'test_date']).reset_index(drop=True)

    # Final same-day collapse to handle cross-chunk and cross-file duplicates
    logger.info("Final same-day collapse...")
    combined = collapse_same_day(combined)

    logger.info("Assigning cycles (min_days_gap=2)...")
    combined = assign_cycles(combined, min_days_gap=2)
    
    # Filter to cycle-first only
    cycle_first = combined[combined['is_cycle_first']]
    
    # Calculate statistics
    total_tests = len(combined)
    cycle_first_count = len(cycle_first)
    retest_count = total_tests - cycle_first_count
    retest_rate = retest_count / total_tests if total_tests > 0 else 0
    
    logger.info(f"Total tests: {total_tests:,}")
    logger.info(f"Cycle-first tests: {cycle_first_count:,} ({cycle_first_count/total_tests*100:.1f}%)")
    logger.info(f"Retests removed: {retest_count:,} ({retest_rate*100:.1f}%)")
    
    # Save index (just test_ids)
    cycle_first_ids = cycle_first[['test_id', 'test_date', 'vehicle_id']].copy()
    cycle_first_ids.to_parquet(output_path, index=False)
    
    elapsed = (datetime.now() - start_time).total_seconds()
    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    logger.info(f"Saved {output_path}: {size_mb:.1f}MB, {elapsed:.1f}s")
    
    return cycle_first_count


def load_cycle_index(index_path: str = 'cycle_first_tests.parquet') -> Set[int]:
    """
    Load the cycle-first test ID index for filtering.
    
    Args:
        index_path: Path to the Parquet index file
    
    Returns:
        Set of test_ids that are cycle-first tests
    """
    if not os.path.exists(index_path):
        raise FileNotFoundError(
            f"Cycle index not found: {index_path}. "
            "Run build_cycle_index() first."
        )
    
    df = pd.read_parquet(index_path, columns=['test_id'])
    return set(df['test_id'].tolist())


def get_retest_prevalence(
    index_path: str = 'cycle_first_tests.parquet',
    windows: list = None
) -> dict:
    """
    Calculate retest prevalence statistics.
    
    Returns dict with:
        - total_tests: Total number of tests
        - cycle_first_count: Tests retained as cycle-first
        - retest_count: Tests removed as retests
        - window_stats: For each window, count of tests with prior test within N days
    """
    if windows is None:
        windows = [30, 60, 120]

    # This requires the full combined data, so we rebuild it
    # In practice, we'd cache this during build_cycle_index
    
    all_tests = []
    for folder, delimiter, pattern in RESULTS_SOURCES:
        file_pattern = os.path.join(folder, pattern)
        for f in glob.glob(file_pattern):
            df = pd.read_csv(f, sep=delimiter, usecols=CYCLE_COLUMNS, low_memory=False)
            df['test_id'] = pd.to_numeric(df['test_id'], errors='coerce').fillna(0).astype('int64')
            df['vehicle_id'] = pd.to_numeric(df['vehicle_id'], errors='coerce').fillna(0).astype('int64')
            df['test_date'] = pd.to_datetime(df['test_date'], errors='coerce')
            df = df.dropna(subset=['test_date', 'vehicle_id'])
            all_tests.append(df)
    
    combined = pd.concat(all_tests, ignore_index=True)
    combined = combined.sort_values(['vehicle_id', 'test_date']).reset_index(drop=True)
    
    # Calculate days since previous test for same vehicle
    combined['prev_vehicle'] = combined['vehicle_id'].shift(1)
    combined['prev_date'] = combined['test_date'].shift(1)
    combined['days_since_prev'] = np.where(
        combined['vehicle_id'] == combined['prev_vehicle'],
        (combined['test_date'] - combined['prev_date']).dt.days,
        np.nan
    )
    
    # Load cycle-first IDs
    valid_ids = load_cycle_index(index_path)
    combined['is_cycle_first'] = combined['test_id'].isin(valid_ids)
    
    # Calculate window stats
    window_stats = {}
    for w in windows:
        # Among ALL tests, how many have a prior test within W days?
        has_prior = (combined['days_since_prev'] <= w).sum()
        window_stats[f'all_with_prior_{w}d'] = int(has_prior)
        window_stats[f'all_with_prior_{w}d_pct'] = has_prior / len(combined) * 100
        
        # Among RETAINED tests, how many have a prior test within W days?
        retained = combined[combined['is_cycle_first']]
        has_prior_retained = (retained['days_since_prev'] <= w).sum()
        window_stats[f'retained_with_prior_{w}d'] = int(has_prior_retained)
        window_stats[f'retained_with_prior_{w}d_pct'] = has_prior_retained / len(retained) * 100 if len(retained) > 0 else 0
    
    return {
        'total_tests': len(combined),
        'cycle_first_count': len(combined[combined['is_cycle_first']]),
        'retest_count': len(combined[~combined['is_cycle_first']]),
        'window_stats': window_stats
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    import argparse
    parser = argparse.ArgumentParser(description="Build cycle-first test index")
    parser.add_argument("--output", default="cycle_first_tests.parquet", 
                        help="Output path for index")
    parser.add_argument("--cutoff", type=str, default=None,
                        help="YYYY-MM-DD cutoff date")
    args = parser.parse_args()
    
    count = build_cycle_index(output_path=args.output, cutoff_date=args.cutoff)
    print(f"\n✓ Built index with {count:,} cycle-first tests")
