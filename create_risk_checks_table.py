"""
Create the risk_checks table in PostgreSQL.
Run with: DATABASE_URL="..." python create_risk_checks_table.py

This table stores all risk check requests for model training data.
VRM + postcode combinations are valuable for improving predictions.
"""
import os
import sys

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL and len(sys.argv) > 1:
    DATABASE_URL = sys.argv[1]

if not DATABASE_URL:
    print("Error: DATABASE_URL not set")
    print("Usage: DATABASE_URL='...' python create_risk_checks_table.py")
    sys.exit(1)

# Railway uses postgres:// but psycopg2 needs postgresql://
DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://")

import psycopg2

def main():
    print("Connecting to PostgreSQL...")
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    print("Creating risk_checks table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS risk_checks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at TIMESTAMP DEFAULT NOW(),

            -- Request input
            registration VARCHAR(8) NOT NULL,
            postcode VARCHAR(10),

            -- Vehicle info from DVSA
            vehicle_make VARCHAR(100),
            vehicle_model VARCHAR(100),
            vehicle_year INTEGER,
            vehicle_fuel_type VARCHAR(50),
            mileage INTEGER,

            -- Latest MOT context
            last_mot_date DATE,
            last_mot_result VARCHAR(20),

            -- Risk prediction output
            failure_risk REAL,
            confidence_level VARCHAR(20),
            risk_components JSONB,
            repair_cost_estimate JSONB,

            -- Metadata
            model_version VARCHAR(20),
            prediction_source VARCHAR(50),
            is_dvsa_data BOOLEAN DEFAULT FALSE
        )
    """)

    print("Creating indexes...")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_risk_checks_registration ON risk_checks(registration)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_risk_checks_postcode ON risk_checks(postcode)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_risk_checks_created ON risk_checks(created_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_risk_checks_make_model ON risk_checks(vehicle_make, vehicle_model)")

    conn.commit()

    # Verify table exists
    cur.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'risk_checks'
        ORDER BY ordinal_position
    """)
    columns = cur.fetchall()

    print(f"\nrisk_checks table created with {len(columns)} columns:")
    for col_name, col_type in columns:
        print(f"  - {col_name}: {col_type}")

    cur.close()
    conn.close()
    print("\nDone!")

if __name__ == "__main__":
    main()
