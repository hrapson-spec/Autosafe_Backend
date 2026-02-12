"""Add UTM tracking columns to risk_checks and leads tables."""
import os
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL")

def migrate():
    if not DATABASE_URL:
        print("DATABASE_URL not set, skipping migration")
        return

    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()

    print("Adding UTM columns to risk_checks...")
    for col, col_type in [
        ("utm_source", "VARCHAR(100)"),
        ("utm_medium", "VARCHAR(100)"),
        ("utm_campaign", "VARCHAR(100)"),
        ("referrer", "TEXT"),
    ]:
        cur.execute(f"""
            ALTER TABLE risk_checks ADD COLUMN IF NOT EXISTS {col} {col_type}
        """)

    print("Adding UTM columns to leads...")
    for col, col_type in [
        ("utm_source", "VARCHAR(100)"),
        ("utm_medium", "VARCHAR(100)"),
        ("utm_campaign", "VARCHAR(100)"),
        ("referrer", "TEXT"),
    ]:
        cur.execute(f"""
            ALTER TABLE leads ADD COLUMN IF NOT EXISTS {col} {col_type}
        """)

    print("Creating indexes on utm_source...")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_risk_checks_utm_source ON risk_checks(utm_source)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_utm_source ON leads(utm_source)")

    cur.close()
    conn.close()
    print("UTM tracking migration complete.")

if __name__ == "__main__":
    migrate()
