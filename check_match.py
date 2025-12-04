import pandas as pd
import glob
import os

print("--- DIAGNOSTIC: CHECKING ID MATCHES ---")

# 1. Load the Defects file
if os.path.exists('defects_summary.csv'):
    defects = pd.read_csv('defects_summary.csv', index_col='test_id')
    # Apply your cleaning fix just to be sure
    defects.index = defects.index.astype(str).str.split('.').str[0]
    print(f"Loaded {len(defects)} rows from defects_summary.csv")
    print(f"Sample Defect IDs: {defects.index[:5].tolist()}")
else:
    print("ERROR: defects_summary.csv not found.")
    exit()

# 2. Load ONE Results file
result_files = sorted(glob.glob('MOT Test Results 24/test_result_*.csv'))
if result_files:
    print(f"\nChecking against file: {result_files[0]}")
    results = pd.read_csv(result_files[0])
    # Apply your cleaning fix
    results['test_id'] = results['test_id'].astype(str).str.split('.').str[0]
    print(f"Sample Result IDs: {results['test_id'].head(5].tolist()}")
    
    # 3. Calculate Overlap
    # Check how many IDs from results exist in the defects list
    common_ids = results[results['test_id'].isin(defects.index)]
    match_count = len(common_ids)
    
    print(f"\n------------------------------------------------")
    print(f"TOTAL MATCHES FOUND: {match_count}")
    print(f"------------------------------------------------")
    
    if match_count == 0:
        print(">> CONCLUSION: The files contain completely different datasets.")
        print(">> FIX: You must re-run 'Script 1' (Process Defects) on the 'MOT Test Results 24' folder.")
    else:
        print(">> CONCLUSION: Matches found! The join should be working.")
else:
    print("ERROR: No test result files found.")