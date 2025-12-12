import pandas as pd
import glob
import os
import logging
from utils import get_age_band, get_mileage_band

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
    RESULTS_FOLDER = 'MOT Test Results'
    DEFECTS_FILE = 'defects_summary.csv'
    OUTPUT_FILE = 'FINAL_MOT_REPORT.csv'
    BAYESIAN_K = 10  # Smoothing factor

def calculate_risk_pipeline():
    logging.info("--- STARTING PART 2: Analyzing Risks ---")

    if not os.path.exists(RiskConfig.DEFECTS_FILE):
        logging.error(f"{RiskConfig.DEFECTS_FILE} not found. Run process_defects.py first!")
        return

    logging.info("Loading defect summary...")
    defects_df = pd.read_csv(RiskConfig.DEFECTS_FILE, index_col='test_id')
    defects_df.index = pd.Index(defects_df.index.astype('int64'), name='test_id')
    defect_cols = defects_df.columns.tolist()
    logging.info(f"Loaded {len(defects_df):,} defect records with columns: {defect_cols}")

    file_pattern = os.path.join(RiskConfig.RESULTS_FOLDER, "test_result_*.csv")
    all_files = glob.glob(file_pattern)

    if not all_files:
        logging.error(f"No CSV files found in '{RiskConfig.RESULTS_FOLDER}'")
        return

    logging.info(f"Found {len(all_files)} monthly files. Processing...")
    global_stats = []
    total_matched = 0
    total_unmatched = 0

    for filename in all_files:
        logging.info(f"Reading {os.path.basename(filename)}...")
        cols = ['test_id', 'test_date', 'first_use_date', 'test_mileage', 'make', 'model', 'test_result']
        
        for chunk in pd.read_csv(filename, sep=',', usecols=cols, chunksize=500000, low_memory=False):
            chunk['test_id'] = pd.to_numeric(chunk['test_id'], errors='coerce').fillna(0).astype('int64')
            
            chunk['test_date'] = pd.to_datetime(chunk['test_date'], errors='coerce')
            chunk['first_use_date'] = pd.to_datetime(chunk['first_use_date'], errors='coerce')
            chunk['age_years'] = (chunk['test_date'] - chunk['first_use_date']).dt.days / 365.25
            chunk['age_band'] = chunk['age_years'].apply(get_age_band)
            
            chunk['test_mileage'] = pd.to_numeric(chunk['test_mileage'], errors='coerce')
            chunk['mileage_band'] = chunk['test_mileage'].apply(get_mileage_band)
            
            chunk['model_id'] = chunk['make'].astype(str) + " " + chunk['model'].astype(str)
            
            merged = chunk.join(defects_df, on='test_id', how='left')
            
            matched = merged[defect_cols[0]].notna().sum()
            unmatched = merged[defect_cols[0]].isna().sum()
            total_matched += matched
            total_unmatched += unmatched
            
            merged[defect_cols] = merged[defect_cols].fillna(0)
            merged['is_failure'] = merged['test_result'].isin(['F', 'FAIL', 'PRS', 'ABA']).astype(int)
            
            agg_targets = {'test_id': 'count', 'is_failure': 'sum'}
            for d in defect_cols: agg_targets[d] = 'sum'
            
            grouped = merged.groupby(['model_id', 'age_band', 'mileage_band']).agg(agg_targets).reset_index()
            global_stats.append(grouped)

    logging.info(f"Join stats: {total_matched:,} matched, {total_unmatched:,} unmatched")

    logging.info("Calculating final risks...")
    if global_stats:
        final_table = pd.concat(global_stats).groupby(['model_id', 'age_band', 'mileage_band']).sum().reset_index()
        final_table.rename(columns={'test_id': 'Total_Tests', 'is_failure': 'Total_Failures'}, inplace=True)
        
        # Extract make from model_id (first word)
        final_table['make'] = final_table['model_id'].str.split().str[0]
        
        # Hierarchical Bayesian Smoothing Parameters
        K_make = 5    # Shrinkage strength toward make average
        K_global = RiskConfig.BAYESIAN_K  # Shrinkage strength toward global

        # 1. Calculate Global Averages (Level 2 Prior)
        global_total_tests = final_table['Total_Tests'].sum()
        global_total_failures = final_table['Total_Failures'].sum()
        global_failure_rate = global_total_failures / global_total_tests if global_total_tests > 0 else 0
        logging.info(f"Global Failure Rate: {global_failure_rate:.4f}")

        # 2. Calculate Make-Level Averages (Level 1 Prior)
        make_stats = final_table.groupby('make').agg({
            'Total_Tests': 'sum',
            'Total_Failures': 'sum'
        }).reset_index()
        
        # Shrink make rates toward global rate
        make_stats['make_failure_rate'] = (
            (make_stats['Total_Failures'] + K_global * global_failure_rate) /
            (make_stats['Total_Tests'] + K_global)
        )
        make_rate_map = dict(zip(make_stats['make'], make_stats['make_failure_rate']))
        final_table['make_rate'] = final_table['make'].map(make_rate_map)
        
        logging.info(f"Calculated hierarchical rates for {len(make_stats)} makes")

        # 3. Apply Two-Level Hierarchical Shrinkage to Overall Failure Risk
        # Shrink variant estimates toward make rate (which is already shrunk toward global)
        final_table['Failure_Risk'] = (
            (final_table['Total_Failures'] + K_make * final_table['make_rate']) /
            (final_table['Total_Tests'] + K_make)
        )

        # 4. Apply Hierarchical Smoothing to Component Risks
        for d in defect_cols:
            # Calculate global rate for this defect
            global_defect_rate = final_table[d].sum() / global_total_tests if global_total_tests > 0 else 0
            
            # Calculate make-level defect rates (shrunk toward global)
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
            
            # Apply two-level shrinkage
            final_table[f'Risk_{d}'] = (
                (final_table[d] + K_make * final_table[f'make_{d}_rate']) /
                (final_table['Total_Tests'] + K_make)
            )
            
            # Clean up intermediate column
            final_table.drop(columns=[f'make_{d}_rate'], inplace=True)

        # Clean up intermediate columns before saving
        final_table.drop(columns=['make', 'make_rate'], inplace=True)
        
        final_table.to_csv(RiskConfig.OUTPUT_FILE, index=False)
        logging.info(f"DONE! Report saved to: {RiskConfig.OUTPUT_FILE}")

if __name__ == "__main__":
    calculate_risk_pipeline()
