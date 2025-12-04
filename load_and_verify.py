import pandas as pd
import os
import time

OUTPUT_FILE = 'FINAL_MOT_REPORT.csv'

if not os.path.exists(OUTPUT_FILE):
    print(f"Error: {OUTPUT_FILE} does not exist.")
    exit(1)

print(f"Loading {OUTPUT_FILE}...")
df = pd.read_csv(OUTPUT_FILE)
print("Dataframe loaded.")
print(f"Shape: {df.shape}")
print("Columns:")
print(df.columns.tolist())

required_columns = ['model_id', 'age_band', 'mileage_band', 'Total_Tests', 'Total_Failures']
missing = [c for c in required_columns if c not in df.columns]

if missing:
    print(f"Missing required columns: {missing}")
else:
    print("All required base columns present.")

risk_cols = [c for c in df.columns if c.startswith('Risk_')]
print(f"Found {len(risk_cols)} risk columns: {risk_cols}")

if len(risk_cols) > 0:
    print("Risk columns present.")
else:
    print("No risk columns found!")
