"""
Create the garages and lead_assignments tables in PostgreSQL.
Run with: DATABASE_URL="..." python create_garages_table.py
"""
import os
import sys

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL and len(sys.argv) > 1:
    DATABASE_URL = sys.argv[1]

if not DATABASE_URL:
    print("Error: DATABASE_URL not set")
    print("Usage: DATABASE_URL='...' python create_garages_table.py")
    sys.exit(1)

# Railway uses postgres:// but psycopg2 needs postgresql://
DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://")

import psycopg2

def main():
    print("Connecting to PostgreSQL...")
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    print("Creating garages table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS garages (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(255) NOT NULL,
            contact_name VARCHAR(255),
            email VARCHAR(255) NOT NULL,
            phone VARCHAR(50),
            postcode VARCHAR(10) NOT NULL,
            latitude DECIMAL(9, 6),
            longitude DECIMAL(9, 6),
            status VARCHAR(20) DEFAULT 'active',
            tier VARCHAR(20) DEFAULT 'free',
            leads_received INTEGER DEFAULT 0,
            leads_converted INTEGER DEFAULT 0,
            source VARCHAR(50),
            notes TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)

    print("Creating lead_assignments table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lead_assignments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            lead_id UUID REFERENCES leads(id),
            garage_id UUID REFERENCES garages(id),
            distance_miles DECIMAL(5, 2),
            email_sent_at TIMESTAMP,
            outcome VARCHAR(20),
            outcome_reported_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)

    # Add distribution_status to leads table if it doesn't exist
    print("Adding distribution_status to leads table...")
    cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='leads' AND column_name='distribution_status') THEN
                ALTER TABLE leads ADD COLUMN distribution_status VARCHAR(20) DEFAULT 'pending';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='leads' AND column_name='distributed_at') THEN
                ALTER TABLE leads ADD COLUMN distributed_at TIMESTAMP;
            END IF;
        END $$;
    """)

    print("Creating indexes...")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_garages_email ON garages(email)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_garages_postcode ON garages(postcode)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_garages_status ON garages(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_garages_location ON garages(latitude, longitude)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_lead_assignments_lead ON lead_assignments(lead_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_lead_assignments_garage ON lead_assignments(garage_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_lead_assignments_sent ON lead_assignments(email_sent_at)")

    conn.commit()

    # Verify tables exist
    for table_name in ['garages', 'lead_assignments']:
        cur.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = %s
            ORDER BY ordinal_position
        """, (table_name,))
        columns = cur.fetchall()

        print(f"\n{table_name} table created with {len(columns)} columns:")
        for col_name, col_type in columns:
            print(f"  - {col_name}: {col_type}")

    cur.close()
    conn.close()
    print("\nDone!")

if __name__ == "__main__":
    main()
