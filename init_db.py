import logging
import os
import sqlite3

import pandas as pd

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

CSV_FILE = 'prod_data_clean.csv.gz'
DB_FILE = 'autosafe.db'

def init_db():
    if not os.path.exists(CSV_FILE):
        logging.error(f"{CSV_FILE} not found. Run export_prod_csv.py first.")
        return

    logging.info(f"Loading {CSV_FILE} (compressed)...")
    df = pd.read_csv(CSV_FILE, compression='gzip')
    
    # Clean column names (replace spaces and commas with underscores)
    df.columns = [c.replace(' ', '_').replace(',', '').replace('__', '_') for c in df.columns]
    
    logging.info(f"Connecting to {DB_FILE}...")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Drop table if exists
    cursor.execute("DROP TABLE IF EXISTS risks")
    
    logging.info("Creating table 'risks'...")
    # Use pandas to write the table
    df.to_sql('risks', conn, if_exists='replace', index=False)
    
    # Create indices for fast searching
    logging.info("Creating indices...")
    cursor.execute("CREATE INDEX idx_model ON risks (model_id)")
    cursor.execute("CREATE INDEX idx_age ON risks (age_band)")
    cursor.execute("CREATE INDEX idx_mileage ON risks (mileage_band)")
    
    conn.commit()
    conn.close()
    
    logging.info("Database initialization complete.")

if __name__ == "__main__":
    init_db()
