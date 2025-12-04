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
    defects_df.index = defects_df.index.astype(str)
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
            chunk['test_id'] = chunk['test_id'].astype(str)
            
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
            merged['is_failure'] = merged['test_result'].isin(['F', 'FAIL', 'PRS']).astype(int)
            
            agg_targets = {'test_id': 'count', 'is_failure': 'sum'}
            for d in defect_cols: agg_targets[d] = 'sum'
            
            grouped = merged.groupby(['model_id', 'age_band', 'mileage_band']).agg(agg_targets).reset_index()
            global_stats.append(grouped)

    logging.info(f"Join stats: {total_matched:,} matched, {total_unmatched:,} unmatched")

    logging.info("Calculating final risks...")
    if global_stats:
        final_table = pd.concat(global_stats).groupby(['model_id', 'age_band', 'mileage_band']).sum().reset_index()
        final_table.rename(columns={'test_id': 'Total_Tests', 'is_failure': 'Total_Failures'}, inplace=True)
        
        # Bayesian Smoothing Parameters
        K = RiskConfig.BAYESIAN_K

        # 1. Calculate Global Averages (The "Prior")
        global_total_tests = final_table['Total_Tests'].sum()
        global_total_failures = final_table['Total_Failures'].sum()
        global_failure_rate = global_total_failures / global_total_tests if global_total_tests > 0 else 0

        logging.info(f"Global Failure Rate: {global_failure_rate:.4f}")

        # 2. Apply Smoothing to Overall Failure Risk
        # Formula: (Failures + K * Global_Rate) / (Tests + K)
        final_table['Failure_Risk'] = (final_table['Total_Failures'] + (K * global_failure_rate)) / (final_table['Total_Tests'] + K)

        # 3. Apply Smoothing to Component Risks
        for d in defect_cols:
            # Calculate global rate for this specific defect
            global_defect_rate = final_table[d].sum() / global_total_tests if global_total_tests > 0 else 0
            
            # Apply smoothing
            final_table[f'Risk_{d}'] = (final_table[d] + (K * global_defect_rate)) / (final_table['Total_Tests'] + K)

        final_table.to_csv(RiskConfig.OUTPUT_FILE, index=False)
        logging.info(f"DONE! Report saved to: {RiskConfig.OUTPUT_FILE}")

if __name__ == "__main__":
    calculate_risk_pipeline()
