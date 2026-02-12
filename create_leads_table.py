"""
Create the leads table in PostgreSQL.
Run with: DATABASE_URL="..." python create_leads_table.py
"""
import os
import sys

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL and len(sys.argv) > 1:
    DATABASE_URL = sys.argv[1]

if not DATABASE_URL:
    print("Error: DATABASE_URL not set")
    print("Usage: DATABASE_URL='...' python create_leads_table.py")
    sys.exit(1)

# Railway uses postgres:// but psycopg2 needs postgresql://
DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://")

import psycopg2

def main():
    print("Connecting to PostgreSQL...")
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    print("Creating leads table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email VARCHAR(255) NOT NULL,
            postcode VARCHAR(10) NOT NULL,
            name VARCHAR(255),
            phone VARCHAR(50),
            lead_type VARCHAR(50) NOT NULL,
            vehicle_make VARCHAR(100),
            vehicle_model VARCHAR(100),
            vehicle_year INTEGER,
            vehicle_mileage INTEGER,
            failure_risk REAL,
            reliability_score INTEGER,
            top_risks JSONB,
            description TEXT,
            urgency VARCHAR(20),
            consent_given BOOLEAN NOT NULL DEFAULT FALSE,
            consent_timestamp TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW(),
            contacted_at TIMESTAMP,
            notes TEXT,

            -- Attribution
            utm_source VARCHAR(100),
            utm_medium VARCHAR(100),
            utm_campaign VARCHAR(100),
            referrer TEXT
        )
    """)

    # Add columns if they don't exist (for existing tables)
    cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='leads' AND column_name='name') THEN
                ALTER TABLE leads ADD COLUMN name VARCHAR(255);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='leads' AND column_name='phone') THEN
                ALTER TABLE leads ADD COLUMN phone VARCHAR(50);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='leads' AND column_name='description') THEN
                ALTER TABLE leads ADD COLUMN description TEXT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='leads' AND column_name='urgency') THEN
                ALTER TABLE leads ADD COLUMN urgency VARCHAR(20);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='leads' AND column_name='consent_given') THEN
                ALTER TABLE leads ADD COLUMN consent_given BOOLEAN NOT NULL DEFAULT FALSE;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='leads' AND column_name='consent_timestamp') THEN
                ALTER TABLE leads ADD COLUMN consent_timestamp TIMESTAMP;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='leads' AND column_name='registration') THEN
                ALTER TABLE leads ADD COLUMN registration VARCHAR(8);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='leads' AND column_name='mot_expiry_date') THEN
                ALTER TABLE leads ADD COLUMN mot_expiry_date DATE;
            END IF;
        END $$;
    """)

    print("Creating indexes...")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_postcode ON leads(postcode)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_created ON leads(created_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_type ON leads(lead_type)")

    conn.commit()

    # Verify table exists
    cur.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'leads'
        ORDER BY ordinal_position
    """)
    columns = cur.fetchall()

    print(f"\nLeads table created with {len(columns)} columns:")
    for col_name, col_type in columns:
        print(f"  - {col_name}: {col_type}")

    cur.close()
    conn.close()
    print("\nDone!")

if __name__ == "__main__":
    main()
