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
import fcntl

logger = logging.getLogger(__name__)

# =============================================================================
# Column Type Specification
# =============================================================================
# The CSV has a fixed column order from calculate_risk.py:
# Columns 0-2: TEXT (model_id, age_band, mileage_band)
# Columns 3-4: INTEGER (Total_Tests, Total_Failures)
# Columns 5+: REAL (Failure_Risk, Risk_Brakes, Risk_Suspension, etc.)
# =============================================================================
TEXT_COLUMNS = 3      # First N columns are TEXT
INTEGER_COLUMNS = 2   # Next N columns are INTEGER (after TEXT)
# Remaining columns are REAL

LOCK_FILE = '/tmp/autosafe_db_build.lock'
DB_FILE = '/tmp/autosafe.db'
DATA_FILE = 'prod_data_clean.csv.gz'
TABLE_NAME = 'risks'


def build_database() -> bool:
    """Build SQLite database from compressed CSV.

    Returns:
        True if database built successfully, False on error.
    """
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

        # Determine column types based on position (see constants at top of file)
        col_defs = []
        for i, col in enumerate(clean_headers):
            if i < TEXT_COLUMNS:
                col_defs.append(f'"{col}" TEXT')
            elif i < TEXT_COLUMNS + INTEGER_COLUMNS:
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
            # Convert row values based on column type
            processed = []
            for i, val in enumerate(row):
                val = val.strip()
                if i < TEXT_COLUMNS:
                    processed.append(val)
                elif i < TEXT_COLUMNS + INTEGER_COLUMNS:
                    # Fix: Use round instead of truncation for integer columns
                    processed.append(round(float(val)) if val else 0)
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

    # Create indexes for fast queries (IF NOT EXISTS to handle race conditions)
    logger.info("Creating indexes...")
    cursor.execute(f'CREATE INDEX IF NOT EXISTS idx_model_id ON {TABLE_NAME} (model_id)')
    cursor.execute(f'CREATE INDEX IF NOT EXISTS idx_age_band ON {TABLE_NAME} (age_band)')
    cursor.execute(f'CREATE INDEX IF NOT EXISTS idx_mileage_band ON {TABLE_NAME} (mileage_band)')
    cursor.execute(f'CREATE INDEX IF NOT EXISTS idx_model_age_mileage ON {TABLE_NAME} (model_id, age_band, mileage_band)')

    conn.commit()
    conn.close()

    elapsed = time.time() - start
    size_mb = os.path.getsize(DB_FILE) / (1024 * 1024)
    logger.info(f"Database built: {total_rows} rows, {size_mb:.1f}MB, {elapsed:.2f}s")

    # Populate model_years table for production range validation
    try:
        from populate_model_years import populate_model_years
        logger.info("Populating model_years table...")
        populate_model_years()
    except Exception as e:
        logger.warning(f"Could not populate model_years: {e}")

    return True


def ensure_database() -> bool:
    """Ensure database exists, building it if necessary.

    Uses file locking to prevent multiple workers from building simultaneously.

    Returns:
        True if database is ready, False on error.
    """
    # Quick check if DB already exists and is valid
    if os.path.exists(DB_FILE):
        try:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
            count = cursor.fetchone()[0]
            conn.close()
            if count > 0:
                logger.info(f"Database exists with {count} rows")
                return True
        except (sqlite3.Error, sqlite3.DatabaseError) as e:
            logger.warning(f"Database exists but invalid: {e}")

    # Use file lock to ensure only one worker builds the database
    lock_fd = open(LOCK_FILE, 'w')
    try:
        # Try to acquire exclusive lock (non-blocking first)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            logger.info("Acquired lock, building database...")
        except BlockingIOError:
            # Another process is building, wait for it
            logger.info("Another worker is building database, waiting...")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)  # Blocking wait
            logger.info("Lock acquired after waiting")

        # Check again if DB was built while we waited
        if os.path.exists(DB_FILE):
            try:
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
                count = cursor.fetchone()[0]
                conn.close()
                if count > 0:
                    logger.info(f"Database built by another worker: {count} rows")
                    return True
            except Exception as e:
                logger.warning(f"Database still invalid after wait: {e}")
                try:
                    os.remove(DB_FILE)
                except OSError as e:
                    logger.warning(f"Failed to remove invalid database file: {e}")

        # Build the database
        return build_database()
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()
        try:
            os.remove(LOCK_FILE)
        except OSError:
            pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ensure_database()
