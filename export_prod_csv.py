"""
Export Production-Ready CSV for AutoSafe deployment.

This script creates a slim CSV with only the columns needed by the API,
reducing file size from ~1GB to ~50MB for efficient Git deployment.
"""
import logging

import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Columns required by the API (from database.py get_risk function)
ESSENTIAL_COLUMNS = [
    'model_id',
    'age_band',
    'mileage_band',
    'Total_Tests',
    'Total_Failures',
    'Failure_Risk',
    # Component-specific risks used by the API
    'Risk_Brakes',
    'Risk_Suspension',
    'Risk_Tyres',
    'Risk_Steering',
    'Risk_Visibility',
    'Risk_Lamps, reflectors and electrical equipment',
    'Risk_Body, chassis, structure',
]

# Rename mapping for cleaner column names (matching PostgreSQL lowercase convention)
COLUMN_RENAMES = {
    'Risk_Lamps, reflectors and electrical equipment': 'Risk_Lamps_Reflectors_And_Electrical_Equipment',
    'Risk_Body, chassis, structure': 'Risk_Body_Chassis_Structure',
}

def export_prod_csv():
    logging.info("Loading FINAL_MOT_REPORT.csv...")
    df = pd.read_csv('FINAL_MOT_REPORT.csv')
    logging.info(f"Loaded {len(df):,} rows with {len(df.columns)} columns")
    
    # Check which essential columns exist
    missing = [c for c in ESSENTIAL_COLUMNS if c not in df.columns]
    if missing:
        logging.warning(f"Missing columns: {missing}")
    
    # Select only essential columns that exist
    available = [c for c in ESSENTIAL_COLUMNS if c in df.columns]
    slim_df = df[available].copy()
    
    # Rename columns for cleaner database schema
    slim_df = slim_df.rename(columns=COLUMN_RENAMES)
    
    # Round float columns to 6 decimal places to reduce file size
    float_cols = slim_df.select_dtypes(include=['float64']).columns
    for col in float_cols:
        slim_df[col] = slim_df[col].round(6)
    
    # Convert integers to save space
    int_cols = ['Total_Tests', 'Total_Failures']
    for col in int_cols:
        if col in slim_df.columns:
            slim_df[col] = slim_df[col].astype('int32')
    
    # Save production CSV
    output_file = 'prod_data.csv'
    slim_df.to_csv(output_file, index=False)
    
    # Report size
    import os
    size_mb = os.path.getsize(output_file) / (1024 * 1024)
    
    logging.info(f"Exported {len(slim_df):,} rows with {len(slim_df.columns)} columns")
    logging.info(f"Output: {output_file} ({size_mb:.1f} MB)")
    logging.info(f"Columns: {list(slim_df.columns)}")
    
    return slim_df

if __name__ == "__main__":
    export_prod_csv()
