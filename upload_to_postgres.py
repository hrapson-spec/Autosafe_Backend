"""
Upload FINAL_MOT_REPORT.csv to Railway PostgreSQL.
Run locally with: DATABASE_URL="postgresql://..." python upload_to_postgres.py
"""
import os
import sys
import csv
import re

# Increase CSV field size limit for large fields
csv.field_size_limit(sys.maxsize)

# Get DATABASE_URL from environment or command line
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL and len(sys.argv) > 1:
    DATABASE_URL = sys.argv[1]

if not DATABASE_URL:
    print("Error: DATABASE_URL not set")
    print("Usage: DATABASE_URL='...' python upload_to_postgres.py")
    print("   Or: python upload_to_postgres.py 'postgresql://...'")
    sys.exit(1)

# Railway uses postgres:// but psycopg2 needs postgresql://
DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://")

import psycopg2
from psycopg2.extras import execute_values

CSV_FILE = "FINAL_MOT_REPORT.csv"
TABLE_NAME = "mot_risk"
CHUNK_SIZE = 10000

def sanitize_column_name(name):
    """Convert CSV header to valid PostgreSQL column name."""
    # Replace special chars with underscores
    name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    # Remove consecutive underscores
    name = re.sub(r'_+', '_', name)
    # Remove leading/trailing underscores
    name = name.strip('_')
    return name.lower()

def main():
    print(f"Connecting to PostgreSQL...")
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    # Read CSV header
    with open(CSV_FILE, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        raw_headers = next(reader)
    
    # Sanitize column names
    columns = [sanitize_column_name(h) for h in raw_headers]
    print(f"Columns: {columns[:5]}... ({len(columns)} total)")
    
    # Drop existing table
    print(f"Dropping existing table if exists...")
    cur.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")
    
    # Create table
    # First 3 columns are TEXT (model_id, age_band, mileage_band)
    # Next 2 are INTEGER (Total_Tests, Total_Failures)
    # Rest are REAL (float)
    col_defs = []
    for i, col in enumerate(columns):
        if i < 3:
            col_defs.append(f'"{col}" VARCHAR(255)')  # Limit size for indexing
        elif i < 5:
            col_defs.append(f'"{col}" INTEGER')
        else:
            col_defs.append(f'"{col}" REAL')
    
    create_sql = f"CREATE TABLE {TABLE_NAME} ({', '.join(col_defs)})"
    print(f"Creating table...")
    cur.execute(create_sql)
    
    # Create indexes
    print("Creating indexes...")
    cur.execute(f'CREATE INDEX idx_model ON {TABLE_NAME} (model_id)')
    cur.execute(f'CREATE INDEX idx_age ON {TABLE_NAME} (age_band)')
    cur.execute(f'CREATE INDEX idx_mileage ON {TABLE_NAME} (mileage_band)')
    
    conn.commit()
    
    # Insert data in chunks
    print(f"Reading {CSV_FILE}...")
    total_rows = 0
    
    with open(CSV_FILE, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        next(reader)  # Skip header
        
        chunk = []
        skipped = 0
        for row in reader:
            # Skip rows with extremely long model_id (corrupted data)
            if len(row[0]) > 255:
                skipped += 1
                continue
                
            # Convert numeric fields
            processed_row = []
            for i, val in enumerate(row):
                if i < 3:
                    # Truncate text fields to 255 chars
                    processed_row.append(val[:255] if val else "")
                elif i < 5:
                    processed_row.append(int(float(val)) if val else 0)
                else:
                    processed_row.append(float(val) if val else 0.0)
            chunk.append(tuple(processed_row))
            
            if len(chunk) >= CHUNK_SIZE:
                execute_values(
                    cur,
                    f"INSERT INTO {TABLE_NAME} VALUES %s",
                    chunk
                )
                conn.commit()
                total_rows += len(chunk)
                print(f"  Inserted {total_rows} rows...")
                chunk = []
        
        # Insert remaining rows
        if chunk:
            execute_values(
                cur,
                f"INSERT INTO {TABLE_NAME} VALUES %s",
                chunk
            )
            conn.commit()
            total_rows += len(chunk)
    
    print(f"\nDone! Total rows inserted: {total_rows}")
    
    # Verify
    cur.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
    count = cur.fetchone()[0]
    print(f"Verification: {count} rows in table")
    
    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
