"""
Step 1: Validate Cycle History Dataset for Time-Sliced EB
"""
import duckdb
import pandas as pd
from pathlib import Path

CYCLE_HISTORY_PATH = "/Users/henrirapson/autosafe_work/cycle_history_dataset/cycle_history_full_combined_with_2023.parquet"

def validate_data():
    print(f"Validating {CYCLE_HISTORY_PATH}...")
    con = duckdb.connect()
    
    # 1. Check Schema & Columns
    print("\n[1] Checking Schema...")
    schema = con.execute(f"DESCRIBE SELECT * FROM '{CYCLE_HISTORY_PATH}' LIMIT 1").df()
    required_cols = ['test_date', 'outcome', 'make', 'model', 'first_use_date', 'test_mileage']
    missing = [c for c in required_cols if c not in schema['column_name'].values]
    
    if missing:
        print(f"❌ MISSING COLUMNS: {missing}")
        print(f"Available columns: {schema['column_name'].values.tolist()}")
        return False
    else:
        print("✅ All required columns present.")

    # 2. Check Completeness (Nulls)
    print("\n[2] Checking Completeness (NULL rates)...")
    total_rows = con.execute(f"SELECT COUNT(*) FROM '{CYCLE_HISTORY_PATH}'").fetchone()[0]
    print(f"Total rows: {total_rows:,}")
    
    for col in required_cols:
        null_count = con.execute(f"SELECT COUNT(*) FROM '{CYCLE_HISTORY_PATH}' WHERE {col} IS NULL").fetchone()[0]
        pct = (null_count / total_rows) * 100
        print(f"  {col}: {null_count:,} NULLs ({pct:.2f}%)")
        if pct > 10.0:
            print(f"  ⚠️ WARNING: High null rate for {col}")

    # 3. Check Date Range
    print("\n[3] Checking Date Range...")
    date_stats = con.execute(f"""
        SELECT MIN(test_date), MAX(test_date)
        FROM '{CYCLE_HISTORY_PATH}'
    """).fetchone()
    print(f"  Test Date Range: {date_stats[0]} to {date_stats[1]}")
    
    # 4. Check Outcome Mapping
    print("\n[4] Checking Outcome Distribution...")
    outcomes = con.execute(f"""
        SELECT outcome, COUNT(*) as n 
        FROM '{CYCLE_HISTORY_PATH}' 
        GROUP BY 1 
        ORDER BY 2 DESC
    """).df()
    print(outcomes.to_string())

    # 5. Check Age/Mileage inputs for segmentation
    print("\n[5] Checking Segmentation Inputs...")
    
    # Age calculation check
    age_check = con.execute(f"""
        SELECT 
            (test_date - first_use_date) / 365.25 as age_years,
            COUNT(*) as n
        FROM '{CYCLE_HISTORY_PATH}'
        WHERE test_date IS NOT NULL AND first_use_date IS NOT NULL
        GROUP BY 1
        ORDER BY 1
        LIMIT 5
    """).df()
    print("  Sample Age Years (Head):")
    print(age_check)

    con.close()
    print("\nValidation Complete.")

if __name__ == "__main__":
    validate_data()
