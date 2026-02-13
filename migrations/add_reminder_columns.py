"""Add reminder tracking columns to leads table for MOT reminder sending."""
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

    print("Adding reminder tracking columns to leads...")
    for col, col_type in [
        ("reminder_28d_sent_at", "TIMESTAMPTZ"),
        ("reminder_28d_opened_at", "TIMESTAMPTZ"),
        ("unsubscribed_at", "TIMESTAMPTZ"),
    ]:
        cur.execute(f"""
            ALTER TABLE leads ADD COLUMN IF NOT EXISTS {col} {col_type}
        """)

    print("Creating index for reminder query...")
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_leads_mot_reminder_due
        ON leads (mot_expiry_date)
        WHERE lead_type = 'mot_reminder'
          AND reminder_28d_sent_at IS NULL
          AND unsubscribed_at IS NULL
    """)

    cur.close()
    conn.close()
    print("Reminder columns migration complete.")


if __name__ == "__main__":
    migrate()
