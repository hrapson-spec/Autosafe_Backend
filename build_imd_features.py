"""
Build IMD (Index of Multiple Deprivation) Features
===================================================

Maps postcode areas to average IMD decile using:
1. Postcode → LSOA lookup
2. LSOA → IMD score

Output: imd_by_postcode_area.parquet

Usage:
    python3 build_imd_features.py

Created: 2026-01-14
"""

import pandas as pd
import numpy as np
from pathlib import Path

PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
IMD_DATA = PROJECT_ROOT / "imd_data"
OUTPUT_FILE = Path.home() / "autosafe_work/imd_by_postcode_area.parquet"


def main():
    print("=" * 60)
    print("BUILD IMD FEATURES BY POSTCODE AREA")
    print("=" * 60)

    # ========================================================================
    # Step 1: Load postcode → LSOA mapping
    # ========================================================================
    print("\n[1] Loading postcode → LSOA mapping...")
    pcd_file = IMD_DATA / "PCD_OA21_LSOA21_MSOA21_LAD_MAY24_UK_LU.csv"

    # Only load needed columns to save memory (file is 403MB)
    pcd_lsoa = pd.read_csv(pcd_file, usecols=['pcds', 'lsoa21cd'], encoding='latin-1')
    print(f"  Loaded {len(pcd_lsoa):,} postcodes")

    # ========================================================================
    # Step 2: Load IMD scores by LSOA
    # ========================================================================
    print("\n[2] Loading IMD scores by LSOA...")
    imd_file = IMD_DATA / "imd2019_lsoa.csv"
    imd_raw = pd.read_csv(imd_file)

    # Filter to IMD decile (1-10, where 1 = most deprived)
    imd_decile = imd_raw[
        (imd_raw['Indices of Deprivation'] == 'a. Index of Multiple Deprivation (IMD)') &
        (imd_raw['Measurement'] == 'Decile ')
    ][['FeatureCode', 'Value']].copy()
    imd_decile.columns = ['lsoa21cd', 'imd_decile']
    imd_decile['imd_decile'] = imd_decile['imd_decile'].astype(int)

    print(f"  Loaded {len(imd_decile):,} LSOA IMD scores")
    print(f"  IMD decile distribution: {imd_decile['imd_decile'].value_counts().sort_index().to_dict()}")

    # ========================================================================
    # Step 3: Join postcode → LSOA → IMD
    # ========================================================================
    print("\n[3] Joining postcode → LSOA → IMD...")
    pcd_imd = pcd_lsoa.merge(imd_decile, on='lsoa21cd', how='inner')
    print(f"  Matched {len(pcd_imd):,} postcodes with IMD")

    # ========================================================================
    # Step 4: Extract postcode_area (outcode prefix)
    # ========================================================================
    print("\n[4] Extracting postcode_area...")

    # Postcode format: "AB1 2CD" → outcode is "AB1" → area is "AB"
    # But dev set uses just the letters (e.g., "AB", "BS", "L")
    def extract_postcode_area(pcd):
        """Extract letter prefix from postcode (e.g., 'BS16 3LJ' → 'BS')"""
        if pd.isna(pcd):
            return None
        # Remove space and get first part
        outcode = pcd.split()[0] if ' ' in pcd else pcd[:4]
        # Extract letters only
        area = ''.join(c for c in outcode if c.isalpha())
        return area

    pcd_imd['postcode_area'] = pcd_imd['pcds'].apply(extract_postcode_area)

    # Remove invalid
    pcd_imd = pcd_imd[pcd_imd['postcode_area'].notna() & (pcd_imd['postcode_area'] != '')]
    print(f"  Valid postcode areas: {len(pcd_imd):,}")

    # ========================================================================
    # Step 5: Average IMD per postcode_area
    # ========================================================================
    print("\n[5] Computing average IMD per postcode_area...")

    area_imd = pcd_imd.groupby('postcode_area').agg({
        'imd_decile': ['mean', 'count']
    }).reset_index()
    area_imd.columns = ['postcode_area', 'imd_decile_avg', 'n_postcodes']

    # Round to nearest integer for decile
    area_imd['area_deprivation_decile'] = area_imd['imd_decile_avg'].round().astype(int)

    print(f"  Unique postcode areas: {len(area_imd):,}")
    print(f"\n  Sample:")
    print(area_imd.head(10).to_string(index=False))

    # ========================================================================
    # Step 6: Save output
    # ========================================================================
    print(f"\n[6] Saving to {OUTPUT_FILE}...")
    output_df = area_imd[['postcode_area', 'area_deprivation_decile', 'n_postcodes']]
    output_df.to_parquet(OUTPUT_FILE, index=False)

    print(f"  Saved {len(output_df):,} rows")

    # Stats
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Postcode areas with IMD: {len(output_df):,}")
    print(f"  IMD decile range: {output_df['area_deprivation_decile'].min()} - {output_df['area_deprivation_decile'].max()}")
    print(f"  Mean decile: {output_df['area_deprivation_decile'].mean():.2f}")
    print(f"\n  Decile distribution:")
    print(output_df['area_deprivation_decile'].value_counts().sort_index().to_string())


if __name__ == "__main__":
    main()
