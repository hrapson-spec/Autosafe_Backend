"""
Build SQLite database from compressed CSV on application startup.
This implements the 'Build-on-Boot' pattern for stateless deployments.

Usage:
    import build_db
    build_db.ensure_database()  # Call on app startup
"""
import gzip
import csv
import sqlite3
import os
import time
import logging

logger = logging.getLogger(__name__)

DB_FILE = 'autosafe.db'
DATA_FILE = 'prod_data_clean.csv.gz'
TABLE_NAME = 'risks'


def build_database():
    """Build SQLite database from compressed CSV."""
    start = time.time()
    logger.info(f"Building {DB_FILE} from {DATA_FILE}...")
    
    if not os.path.exists(DATA_FILE):
        logger.error(f"Data file {DATA_FILE} not found!")
        return False
    
    # Create database connection
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Drop existing table if exists
    cursor.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")
    
    # Read and parse gzipped CSV
    with gzip.open(DATA_FILE, 'rt', encoding='utf-8') as f:
        reader = csv.reader(f)
        headers = next(reader)
        
        # Clean headers for SQLite column names
        clean_headers = []
        for h in headers:
            clean = h.strip().replace(' ', '_').replace('-', '_')
            clean_headers.append(clean)
        
        # Create table - first 3 columns are TEXT, next 2 INTEGER, rest REAL
        col_defs = []
        for i, col in enumerate(clean_headers):
            if i < 3:
                col_defs.append(f'"{col}" TEXT')
            elif i < 5:
                col_defs.append(f'"{col}" INTEGER')
            else:
                col_defs.append(f'"{col}" REAL')
        
        create_sql = f"CREATE TABLE {TABLE_NAME} ({', '.join(col_defs)})"
        cursor.execute(create_sql)
        
        # Insert data in batches
        batch = []
        batch_size = 5000
        total_rows = 0
        placeholders = ','.join(['?' for _ in clean_headers])
        insert_sql = f"INSERT INTO {TABLE_NAME} VALUES ({placeholders})"
        
        for row in reader:
            # Clean and convert row data
            processed = []
            for i, val in enumerate(row):
                val = val.strip()
                if i < 3:
                    processed.append(val)
                elif i < 5:
                    processed.append(int(float(val)) if val else 0)
                else:
                    processed.append(float(val) if val else 0.0)
            batch.append(processed)
            
            if len(batch) >= batch_size:
                cursor.executemany(insert_sql, batch)
                total_rows += len(batch)
                batch = []
        
        # Insert remaining rows
        if batch:
            cursor.executemany(insert_sql, batch)
            total_rows += len(batch)
    
    # Create indexes for fast queries
    logger.info("Creating indexes...")
    cursor.execute(f'CREATE INDEX idx_model_id ON {TABLE_NAME} (model_id)')
    cursor.execute(f'CREATE INDEX idx_age_band ON {TABLE_NAME} (age_band)')
    cursor.execute(f'CREATE INDEX idx_mileage_band ON {TABLE_NAME} (mileage_band)')
    cursor.execute(f'CREATE INDEX idx_model_age_mileage ON {TABLE_NAME} (model_id, age_band, mileage_band)')
    
    conn.commit()
    conn.close()
    
    elapsed = time.time() - start
    size_mb = os.path.getsize(DB_FILE) / (1024 * 1024)
    logger.info(f"Database built: {total_rows} rows, {size_mb:.1f}MB, {elapsed:.2f}s")
    
    return True


def ensure_database():
    """Ensure database exists, building it if necessary."""
    if os.path.exists(DB_FILE):
        # Verify it's valid
        try:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
            count = cursor.fetchone()[0]
            conn.close()
            if count > 0:
                logger.info(f"Database exists with {count} rows")
                return True
        except Exception as e:
            logger.warning(f"Database exists but invalid: {e}")
            os.remove(DB_FILE)
    
    # Build the database
    return build_database()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ensure_database()
