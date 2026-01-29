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
    # 4. MONOTONICITY (Component-Level Check)
    # ---------------------------------------------------------
    logging.info("\n--- TEST 4: COMPONENT-LEVEL MONOTONICITY ---")
    
    try:
        from monotonicity import (
            MonotonicityConfig,
            audit_monotonicity_per_model,
            audit_monotonicity_global,
            MonotonicityAuditResult
        )
        from datetime import datetime
        
        if risk_cols and 'mileage_band' in df.columns and 'age_band' in df.columns:
            # Run per-model and global audits
            per_model_violations = audit_monotonicity_per_model(df, risk_cols, sample_models=100)
            global_violations = audit_monotonicity_global(df, risk_cols)
            
            all_violations = per_model_violations + global_violations
            hard_violations = [v for v in all_violations if v.is_hard]
            soft_violations = [v for v in all_violations if not v.is_hard]
            
            logging.info(f"Checked {len(risk_cols)} component risk columns")
            logging.info(f"Hard violations: {len(hard_violations)}")
            logging.info(f"Soft violations (within {MonotonicityConfig.TOLERANCE_PERCENT}% tolerance): {len(soft_violations)}")
            
            if hard_violations:
                logging.warning(f"FAIL: {len(hard_violations)} hard monotonicity violations detected")
                for v in hard_violations[:5]:
                    logging.warning(
                        f"  {v.component}: {v.segment_key} | "
                        f"{v.lower_band} -> {v.higher_band} | "
                        f"{v.lower_risk:.4f} -> {v.higher_risk:.4f} "
                        f"({v.decrease_percent:.1f}% decrease)"
                    )
                if len(hard_violations) > 5:
                    logging.warning(f"  ... and {len(hard_violations) - 5} more")
                    
                # Save detailed report
                result = MonotonicityAuditResult(
                    timestamp=datetime.now().isoformat(),
                    config={
                        "tolerance_percent": MonotonicityConfig.TOLERANCE_PERCENT,
                        "min_sample_for_hard_fail": MonotonicityConfig.MIN_SAMPLE_FOR_HARD_FAIL
                    },
                    violations=all_violations,
                    summary={
                        "hard_violations": len(hard_violations),
                        "soft_violations": len(soft_violations),
                        "passed": len(hard_violations) == 0
                    }
                )
                
                report_path = "monotonicity_audit_report.json"
                with open(report_path, "w") as f:
                    f.write(result.to_json())
                logging.info(f"Full report saved to {report_path}")
            else:
                logging.info("PASS: No hard monotonicity violations detected")
        else:
            logging.info("SKIP: Missing required columns for monotonicity check.")
            
    except ImportError:
        logging.warning("SKIP: monotonicity module not available. Using basic check.")
        
        # Fallback: Basic mileage correlation check
        mileage_map = {'0-30k': 15000, '30k-60k': 45000, '60k-100k': 80000, '100k+': 120000, 'Unknown': -1}
        df['Mileage_Numeric'] = df['mileage_band'].map(mileage_map)
        valid_mileage = df[df['Mileage_Numeric'] > 0]
        
        if not valid_mileage.empty and len(risk_cols) > 0:
            model_counts = valid_mileage['model_id'].value_counts()
            sample_models = model_counts[model_counts > 3].index[:50]
            
            pos_trends = neg_trends = 0
            for model in sample_models:
                model_df = valid_mileage[valid_mileage['model_id'] == model]
                if len(model_df) > 2:
                    trend = model_df['Mileage_Numeric'].corr(model_df[risk_cols[0]])
                    if trend > 0.1:
                        pos_trends += 1
                    elif trend < -0.1:
                        neg_trends += 1
                        
            logging.info(f"Analyzed {len(sample_models)} vehicle models.")
            if neg_trends > pos_trends:
                logging.warning("FAIL: More cars show DECREASING risk as mileage goes up.")
            else:
                logging.info("PASS: Majority show increasing risk with mileage.")
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

    # ---------------------------------------------------------
    # 6. BRIER SCORE (Probability Calibration)
    # ---------------------------------------------------------
    logging.info("\n--- TEST 6: BRIER SCORE (CALIBRATION) ---")
    if 'Failure_Risk' in df.columns:
        # Calculate observed failure rate per row
        df['observed_rate'] = df['Total_Failures'] / df['Total_Tests'].clip(lower=1)
        
        # Weighted Brier score (weighted by sample size)
        weights = df['Total_Tests']
        squared_errors = (df['Failure_Risk'] - df['observed_rate']) ** 2
        weighted_brier = (squared_errors * weights).sum() / weights.sum()
        unweighted_brier = squared_errors.mean()
        
        logging.info(f"Weighted Brier Score: {weighted_brier:.6f}")
        logging.info(f"Unweighted Brier Score: {unweighted_brier:.6f}")
        
        # Interpretation
        if weighted_brier < 0.001:
            logging.info("PASS: Excellent calibration (Brier < 0.001)")
        elif weighted_brier < 0.01:
            logging.info("PASS: Good calibration (Brier < 0.01)")
        elif weighted_brier < 0.05:
            logging.warning("WARNING: Moderate calibration (Brier < 0.05)")
        else:
            logging.error("FAIL: Poor calibration (Brier >= 0.05)")
        
        # Check for systematic over/under prediction
        mean_predicted = df['Failure_Risk'].mean()
        mean_observed = df['observed_rate'].mean()
        bias = mean_predicted - mean_observed
        logging.info(f"Prediction Bias: {bias:+.4f} ({'over-predicting' if bias > 0 else 'under-predicting'})")
        
        df.drop(columns=['observed_rate'], inplace=True)
    else:
        logging.warning("SKIP: 'Failure_Risk' column not found for Brier score calculation.")

if __name__ == "__main__":
    import sys
    file_path = 'FINAL_MOT_REPORT.csv'
    golden_path = 'GOLDEN_MOT_REPORT.csv' # Placeholder
    
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        
    audit_risk_model(file_path, golden_path)
