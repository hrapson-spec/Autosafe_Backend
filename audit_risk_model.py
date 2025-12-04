import pandas as pd
import numpy as np
import logging
import os

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def audit_risk_model(file_path, golden_file_path=None):
    logging.info(f"--- LOADING DATA FROM {file_path} ---")
    try:
        df = pd.read_csv(file_path)
        logging.info(f"Loaded {len(df)} rows.")
    except Exception as e:
        logging.error(f"Error loading file: {e}")
        return

    # ---------------------------------------------------------
    # 1. SCHEMA VALIDATION
    # ---------------------------------------------------------
    logging.info("\n--- TEST 1: SCHEMA VALIDATION ---")
    required_columns = ['model_id', 'age_band', 'mileage_band', 'Total_Tests', 'Total_Failures']
    missing_cols = [c for c in required_columns if c not in df.columns]
    
    if missing_cols:
        logging.error(f"FAIL: Missing required columns: {missing_cols}")
        return
    else:
        logging.info("PASS: All required base columns present.")

    # Check for Risk columns
    risk_cols = [c for c in df.columns if str(c).startswith('Risk_')]
    if not risk_cols:
        logging.warning("WARNING: No 'Risk_' columns found.")
    else:
        logging.info(f"PASS: Found {len(risk_cols)} risk columns.")

    # Data Type Checks
    if not pd.api.types.is_numeric_dtype(df['Total_Tests']):
        logging.error("FAIL: 'Total_Tests' is not numeric.")
    if not pd.api.types.is_numeric_dtype(df['Total_Failures']):
        logging.error("FAIL: 'Total_Failures' is not numeric.")

    # ---------------------------------------------------------
    # 2. BOUNDARY VALUE ANALYSIS
    # ---------------------------------------------------------
    logging.info("\n--- TEST 2: BOUNDARY ANALYSIS (0.0 vs 1.0) ---")
    hard_zeros = 0
    hard_ones = 0
    
    for col in risk_cols:
        hard_zeros += (df[col] == 0.0).sum()
        hard_ones += (df[col] >= 1.0).sum()

    logging.info(f"Total 'Hard Zero' Predictions (0.0): {hard_zeros}")
    logging.info(f"Total 'Certain Failure' Predictions (>=1.0): {hard_ones}")
    
    if hard_zeros > 0:
        logging.warning("WARNING: Hard zeros detected. This implies 'impossibility', which breaks log-loss calculations.")
    else:
        logging.info("PASS: No hard zeros found (Model uses proper smoothing).")

    # ---------------------------------------------------------
    # 3. ROBUSTNESS / SMOOTHING TEST
    # ---------------------------------------------------------
    logging.info("\n--- TEST 3: SMALL SAMPLE ROBUSTNESS ---")
    low_data_df = df[(df['Total_Tests'] > 0) & (df['Total_Tests'] < 20) & (df['Total_Failures'] == 0)]
    
    if len(low_data_df) > 0:
        low_data_df['avg_risk_sum'] = low_data_df[risk_cols].sum(axis=1)
        non_zero_predictions = (low_data_df['avg_risk_sum'] > 0).sum()
        
        logging.info(f"Found {len(low_data_df)} rows with Low Data (<20 tests) and Zero Failures.")
        
        if non_zero_predictions == len(low_data_df):
            logging.info("PASS: Model effectively smoothes sparse data.")
        else:
            logging.warning("FAIL: Model overfits to zero-failure data in some cases.")
    else:
        logging.info("SKIP: No sparse data rows found to test.")

    # ---------------------------------------------------------
    # 4. MONOTONICITY (Mileage Test)
    # ---------------------------------------------------------
    logging.info("\n--- TEST 4: LOGICAL MONOTONICITY (Mileage) ---")
    mileage_map = {'0-30k': 15000, '30k-60k': 45000, '60k-100k': 80000, '100k+': 120000, 'Unknown': -1}
    df['Mileage_Numeric'] = df['mileage_band'].map(mileage_map)
    
    # Filter out unknown mileage
    valid_mileage = df[df['Mileage_Numeric'] > 0]
    
    if not valid_mileage.empty and len(risk_cols) > 0:
        # Check correlation for a sample of models
        model_counts = valid_mileage['model_id'].value_counts()
        sample_models = model_counts[model_counts > 3].index[:50]
        
        pos_trends = 0
        neg_trends = 0
        
        for model in sample_models:
            model_df = valid_mileage[valid_mileage['model_id'] == model]
            if len(model_df) > 2:
                # Check correlation of mileage vs first risk column (usually Body or Brakes)
                # Using the first risk column as a proxy for general wear
                trend = model_df['Mileage_Numeric'].corr(model_df[risk_cols[0]])
                if trend > 0.1: # Strict positive correlation
                    pos_trends += 1
                elif trend < -0.1:
                    neg_trends += 1
                    
        logging.info(f"Analyzed {len(sample_models)} vehicle models.")
        logging.info(f"Models with increasing risk: {pos_trends}")
        logging.info(f"Models with decreasing risk: {neg_trends}")
        
        if neg_trends > pos_trends:
            logging.warning("FAIL: More cars show DECREASING risk as mileage goes up. Check for survivorship bias.")
        else:
            logging.info("PASS: Majority of models show increasing risk with mileage.")
    else:
        logging.info("SKIP: Could not perform mileage monotonicity test.")

    # ---------------------------------------------------------
    # 5. REGRESSION TEST (Golden Dataset)
    # ---------------------------------------------------------
    if golden_file_path and os.path.exists(golden_file_path):
        logging.info("\n--- TEST 5: REGRESSION TESTING ---")
        golden_df = pd.read_csv(golden_file_path)
        
        # Compare shapes
        if df.shape != golden_df.shape:
            logging.warning(f"Shape mismatch: Current {df.shape} vs Golden {golden_df.shape}")
        
        # Compare a key metric (e.g., Global Average Risk)
        current_avg_risk = df['Failure_Risk'].mean() if 'Failure_Risk' in df.columns else 0
        golden_avg_risk = golden_df['Failure_Risk'].mean() if 'Failure_Risk' in golden_df.columns else 0
        
        diff = abs(current_avg_risk - golden_avg_risk)
        logging.info(f"Average Failure Risk: Current={current_avg_risk:.4f}, Golden={golden_avg_risk:.4f}, Diff={diff:.4f}")
        
        if diff > 0.01:
            logging.error("FAIL: Significant deviation from golden dataset.")
        else:
            logging.info("PASS: Regression test passed (within tolerance).")
    elif golden_file_path:
        logging.warning(f"Golden file {golden_file_path} not found. Skipping regression test.")

if __name__ == "__main__":
    import sys
    file_path = 'FINAL_MOT_REPORT.csv'
    golden_path = 'GOLDEN_MOT_REPORT.csv' # Placeholder
    
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        
    audit_risk_model(file_path, golden_path)
