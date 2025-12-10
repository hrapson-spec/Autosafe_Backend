#!/usr/bin/env python3
"""
Populate model_years table with production year ranges derived from MOT data.

For each model, we estimate:
- first_year: Earliest year a vehicle of this model could have been manufactured
- last_year: Latest year a vehicle of this model could have been manufactured

The estimation is based on age bands observed in the MOT data.
"""
import os
import sqlite3

DB_FILE = 'autosafe.db'

# Age band to approximate age mapping (use midpoint of range)
AGE_BAND_TO_YEARS = {
    '0-3': 1,      # ~1 year old on average
    '3-5': 4,      # ~4 years old on average
    '6-10': 8,     # ~8 years old on average
    '10-15': 12,   # ~12 years old on average
    '10+': 12,     # Same as 10-15
    '15+': 18,     # ~18 years old on average
    'Unknown': None
}

# Assume MOT data is from tests conducted in 2024 (adjust if needed)
TEST_YEAR = 2024


def populate_model_years():
    """Create and populate the model_years table."""
    if not os.path.exists(DB_FILE):
        print(f"Error: {DB_FILE} not found")
        return False
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Create table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS model_years (
            model_id TEXT PRIMARY KEY,
            first_year INTEGER,
            last_year INTEGER,
            total_tests INTEGER
        )
    """)
    
    # Clear existing data
    cursor.execute("DELETE FROM model_years")
    
    # Get all models with their age bands
    cursor.execute("""
        SELECT model_id, age_band, SUM(Total_Tests) as tests
        FROM risks
        WHERE age_band != 'Unknown'
        GROUP BY model_id, age_band
    """)
    
    rows = cursor.fetchall()
    
    # Group by model
    model_data = {}
    for model_id, age_band, tests in rows:
        if model_id not in model_data:
            model_data[model_id] = {'age_bands': [], 'total_tests': 0}
        if age_band in AGE_BAND_TO_YEARS and AGE_BAND_TO_YEARS[age_band] is not None:
            model_data[model_id]['age_bands'].append(age_band)
            model_data[model_id]['total_tests'] += tests
    
    # Calculate production years for each model
    inserted = 0
    for model_id, data in model_data.items():
        if not data['age_bands']:
            continue
        
        # Find the range of ages observed
        min_age = min(AGE_BAND_TO_YEARS[ab] for ab in data['age_bands'])
        max_age = max(AGE_BAND_TO_YEARS[ab] for ab in data['age_bands'])
        
        # Estimate production years
        # If youngest observed is ~1 year old, last production year is TEST_YEAR - 1
        # If oldest observed is ~18 years old, first production year is TEST_YEAR - 18
        last_year = TEST_YEAR - min_age
        first_year = TEST_YEAR - max_age - 5  # Add buffer for older vehicles
        
        # Sanity checks
        first_year = max(first_year, 1990)  # No cars before 1990 in practical terms
        last_year = min(last_year, TEST_YEAR)  # Can't be newer than test year
        
        cursor.execute("""
            INSERT INTO model_years (model_id, first_year, last_year, total_tests)
            VALUES (?, ?, ?, ?)
        """, (model_id, first_year, last_year, data['total_tests']))
        inserted += 1
    
    conn.commit()
    
    # Create index for fast lookups
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_model_years_model ON model_years(model_id)")
    
    conn.commit()
    conn.close()
    
    print(f"Populated model_years table with {inserted} models")
    return True


def check_model_year(model_id: str, year: int) -> dict:
    """
    Check if a model+year combination is valid.
    
    TEMPORARY FIX: Always return valid. 
    The original implementation relied on a local SQLite database which does not exist
    in the production environment (PostgreSQL). This caused Internal Server Errors.
    We are bypassing this check to restore service availability.
    """
    return {'valid': True, 'message': None}


if __name__ == "__main__":
    populate_model_years()
    
    # Test with Toyota Avensis
    result = check_model_year("TOYOTA AVENSIS", 2022)
    print(f"\nTest: TOYOTA AVENSIS 2022")
    print(f"Valid: {result['valid']}")
    if result['message']:
        print(f"Message: {result['message']}")
    
    result = check_model_year("TOYOTA AVENSIS", 2015)
    print(f"\nTest: TOYOTA AVENSIS 2015")
    print(f"Valid: {result['valid']}")
