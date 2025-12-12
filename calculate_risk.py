"""
Calculate Risk Model (Baseline)

This script processes MOT test results to calculate failure risks for vehicle segments.
It generates the final risk table (FINAL_MOT_REPORT.csv) used by the application.

Methodology:
- Segmentation: Make + Model + Age Band + Mileage Band
- Smoothing: Two-level Hierarchical Bayesian Smoothing
  1. Global Prior -> Make Prior
  2. Make Prior -> Segment Estimate
- Parameters:
  K_MAKE = 5 (Shrinkage towards make average)
  K_GLOBAL = 10 (Shrinkage towards global average)
"""

import pandas as pd
import glob
import os
import logging
from utils import get_age_band, get_mileage_band, FAILURE_CODES
from cycle_filter import collapse_same_day, assign_cycles
import argparse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("risk_calculation.log"),
        logging.StreamHandler()
    ]
)

class RiskConfig:
    # (folder, delimiter, file_pattern)
    RESULTS_SOURCES = [
        ("MOT Test Results", ",", "test_result_*.csv"),   # 2024 monthly files
        ("MOT Test Results/2023", "|", "test_result.csv"),
        ("MOT Test Results/2022", "|", "test_result_2022.csv"),
    ]
    DEFECTS_FILE = 'defects_summary.csv'
    OUTPUT_FILE = 'FINAL_MOT_REPORT.csv'
    CYCLE_FILTER_INDEX = 'cycle_first_tests.parquet'
    
    # Hierarchical Smoothing Parameters (Baseline)
    K_MAKE = 5      # Shrinkage toward make average
    K_GLOBAL = 10   # Shrinkage toward global average


def load_cycle_filter_ids(index_path: str) -> pd.Index:
    """Load cycle-first test IDs from index file as a pandas Index for fast isin()."""
    if not os.path.exists(index_path):
        logging.warning(f"Cycle filter index not found: {index_path}")
        logging.warning("Run 'python -c \"from cycle_filter import build_cycle_index; build_cycle_index()\"' to create it.")
        return None
    
    logging.info(f"Loading cycle filter index from {index_path}...")
    df = pd.read_parquet(index_path, columns=['test_id'])
    # Use pandas Index (not set) for 100x faster isin() operations
    cycle_ids = pd.Index(df['test_id'].values)
    logging.info(f"Loaded {len(cycle_ids):,} cycle-first test IDs")
    return cycle_ids


def calculate_risk_pipeline(cutoff_date=None):
    logging.info("--- STARTING RISK CALCULATION (Baseline Model) ---")
    
    if cutoff_date:
        cutoff_date = pd.to_datetime(cutoff_date)
        logging.info(f"Temporal Integrity Check Active: Cutoff Date = {cutoff_date.date()}")
    
    # Validate all source files before processing
    from dvsa_schema import validate_all_sources
    validate_all_sources(RiskConfig.RESULTS_SOURCES, "test_result")

    if not os.path.exists(RiskConfig.DEFECTS_FILE):
        logging.error(f"{RiskConfig.DEFECTS_FILE} not found. Run process_defects.py first!")
        return

    logging.info("Loading defect summary...")
    try:
        header_df = pd.read_csv(RiskConfig.DEFECTS_FILE, nrows=0)
        defect_cols = [c for c in header_df.columns if c != 'test_id']
        
        dtype_map = {'test_id': 'int64'}
        defects_df = pd.read_csv(RiskConfig.DEFECTS_FILE, dtype=dtype_map)
        defects_df.set_index('test_id', inplace=True)
        
        for col in defect_cols:
            defects_df[col] = defects_df[col].fillna(0).astype('int8')
        
    except Exception as e:
        logging.error(f"Error loading defects file: {e}")
        return

    logging.info(f"Loaded {len(defects_df):,} defect records with columns: {defect_cols}")

    # Load cycle filter index
    cycle_filter_ids = load_cycle_filter_ids(RiskConfig.CYCLE_FILTER_INDEX)
    if cycle_filter_ids is not None:
        logging.info(f"Cycle filter ACTIVE: {len(cycle_filter_ids):,} cycle-first tests")
    else:
        logging.info("Cycle filter NOT active - using all tests")

    # Collect all files
    file_sources = []
    for folder, delimiter, pattern in RiskConfig.RESULTS_SOURCES:
        file_pattern = os.path.join(folder, pattern)
        matched_files = glob.glob(file_pattern)
        for f in matched_files:
            file_sources.append((f, delimiter))
        logging.info(f"Found {len(matched_files)} files in '{folder}'")

    if not file_sources:
        logging.error("No CSV files found in any source folder")
        return

    logging.info(f"Total: {len(file_sources)} result files to process.")
    global_stats = []
    total_matched = 0
    total_unmatched = 0
    total_raw = 0
    total_filtered = 0

    for filename, sep in file_sources:
        logging.info(f"Reading {os.path.basename(filename)} (delimiter='{sep}')...")
        cols = ['test_id', 'test_date', 'first_use_date', 'test_mileage', 'make', 'model', 
                'test_result']
        
        for chunk in pd.read_csv(filename, sep=sep, usecols=cols, chunksize=500000, low_memory=False):
            chunk['test_id'] = pd.to_numeric(chunk['test_id'], errors='coerce').fillna(0).astype('int64')
            
            chunk['test_date'] = pd.to_datetime(chunk['test_date'], errors='coerce')
            chunk['first_use_date'] = pd.to_datetime(chunk['first_use_date'], errors='coerce')
            
            # Temporal Integrity Check
            if cutoff_date:
                chunk = chunk[chunk['test_date'] <= cutoff_date]
                if chunk.empty:
                    continue
                max_date = chunk['test_date'].max()
                assert max_date <= cutoff_date, f"Temporal Leakage: Found data from {max_date} > {cutoff_date}"
            
            # Apply cycle filter
            total_raw += len(chunk)
            if cycle_filter_ids is not None:
                chunk = chunk[chunk['test_id'].isin(cycle_filter_ids)]
                if chunk.empty:
                    continue
            total_filtered += len(chunk)
            
            chunk['age_years'] = (chunk['test_date'] - chunk['first_use_date']).dt.days / 365.25
            chunk['age_band'] = chunk['age_years'].apply(get_age_band)
            
            chunk['test_mileage'] = pd.to_numeric(chunk['test_mileage'], errors='coerce')
            chunk['mileage_band'] = chunk['test_mileage'].apply(get_mileage_band)
            
            # Create Model ID (Baseline: Make + Model)
            chunk['model_id'] = chunk['make'].astype(str) + " " + chunk['model'].astype(str)
            
            # Left join on int64 index
            merged = chunk.join(defects_df, on='test_id', how='left')
            
            matched = merged[defect_cols[0]].notna().sum()
            unmatched = merged[defect_cols[0]].isna().sum()
            total_matched += matched
            total_unmatched += unmatched
            
            merged[defect_cols] = merged[defect_cols].fillna(0).astype('int8')
            merged['is_failure'] = merged['test_result'].isin(FAILURE_CODES).astype(int)
            
            agg_targets = {'test_id': 'count', 'is_failure': 'sum'}
            for d in defect_cols: agg_targets[d] = 'sum'
            
            grouped = merged.groupby(['model_id', 'make', 'age_band', 'mileage_band']).agg(agg_targets).reset_index()
            global_stats.append(grouped)

    logging.info(f"Join stats: {total_matched:,} matched, {total_unmatched:,} unmatched")
    
    if cycle_filter_ids is not None:
        filter_rate = (1 - total_filtered / total_raw) * 100 if total_raw > 0 else 0
        logging.info(f"Cycle filter stats: {total_raw:,} raw -> {total_filtered:,} filtered ({filter_rate:.1f}% removed)")

    logging.info("Calculating final risks with hierarchical smoothing...")
    if global_stats:
        final_table = pd.concat(global_stats).groupby(['model_id', 'make', 'age_band', 'mileage_band']).sum().reset_index()
        final_table.rename(columns={'test_id': 'Total_Tests', 'is_failure': 'Total_Failures'}, inplace=True)
        
        K_make = RiskConfig.K_MAKE
        K_global = RiskConfig.K_GLOBAL

        # 1. Calculate Global Averages
        global_total_tests = final_table['Total_Tests'].sum()
        global_total_failures = final_table['Total_Failures'].sum()
        global_failure_rate = global_total_failures / global_total_tests if global_total_tests > 0 else 0
        logging.info(f"Global Failure Rate: {global_failure_rate:.4f}")

        # 2. Calculate Make-Level Averages
        make_stats = final_table.groupby('make').agg({
            'Total_Tests': 'sum',
            'Total_Failures': 'sum'
        }).reset_index()
        
        make_stats['make_failure_rate'] = (
            (make_stats['Total_Failures'] + K_global * global_failure_rate) /
            (make_stats['Total_Tests'] + K_global)
        )
        make_rate_map = dict(zip(make_stats['make'], make_stats['make_failure_rate']))
        final_table['make_rate'] = final_table['make'].map(make_rate_map)
        
        logging.info(f"Calculated hierarchical rates for {len(make_stats)} makes")

        # 3. Apply Two-Level Hierarchical Shrinkage
        final_table['Failure_Risk'] = (
            (final_table['Total_Failures'] + K_make * final_table['make_rate']) /
            (final_table['Total_Tests'] + K_make)
        )

        # 4. Apply Hierarchical Smoothing to Component Risks
        for d in defect_cols:
            global_defect_rate = final_table[d].sum() / global_total_tests if global_total_tests > 0 else 0
            
            make_defect_stats = final_table.groupby('make').agg({
                'Total_Tests': 'sum',
                d: 'sum'
            }).reset_index()
            make_defect_stats[f'make_{d}_rate'] = (
                (make_defect_stats[d] + K_global * global_defect_rate) /
                (make_defect_stats['Total_Tests'] + K_global)
            )
            make_defect_map = dict(zip(make_defect_stats['make'], make_defect_stats[f'make_{d}_rate']))
            final_table[f'make_{d}_rate'] = final_table['make'].map(make_defect_map)
            
            final_table[f'Risk_{d}'] = (
                (final_table[d] + K_make * final_table[f'make_{d}_rate']) /
                (final_table['Total_Tests'] + K_make)
            )
            
            final_table.drop(columns=[f'make_{d}_rate'], inplace=True)

        # Clean up
        final_table.drop(columns=['make_rate'], inplace=True)
        
        final_table.to_csv(RiskConfig.OUTPUT_FILE, index=False)
        logging.info(f"DONE! Report saved to: {RiskConfig.OUTPUT_FILE}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calculate risk models (Baseline).")
    parser.add_argument("--cutoff_date", type=str, help="YYYY-MM-DD cutoff date for training data")
    args = parser.parse_args()
    
    calculate_risk_pipeline(cutoff_date=args.cutoff_date)
