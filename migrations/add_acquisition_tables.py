"""
Create the first-party acquisition collector tables (OA-005, DECISIONS.md D-005).

Adds two NEW tables and touches nothing that exists, so it is safe to run
before, after or without the collector code:

    acquisition_events  raw events, 90-day retention (random IDs, enums, booleans,
                        release SHA, received_at, derived is_bot; no IP, User-Agent,
                        Referer, query string or URL)
    acquisition_daily   daily aggregate counts, 25-month retention, no IDs

The DDL is imported from acquisition_store.py, the same statements the
collector uses to ensure its schema, so this script and the runtime cannot
drift. It is idempotent (CREATE ... IF NOT EXISTS).

Usage:
    DATABASE_URL="postgresql://..." python migrations/add_acquisition_tables.py
    DATABASE_URL="postgresql://..." python migrations/add_acquisition_tables.py --dry-run
    DATABASE_URL="postgresql://..." python migrations/add_acquisition_tables.py --rollback

--dry-run prints every statement without opening a database connection or
requiring DATABASE_URL.

Running it is optional: when ACQUISITION_INGEST_ENABLED is set, the collector
creates the same tables itself on first use. Running it explicitly lets the
owner create (and inspect) the tables before enabling ingest.

--rollback DROPS both tables and every row in them. It is the data-deletion
path if the collector is withdrawn; flipping ACQUISITION_COLLECTOR_ENABLED /
ACQUISITION_INGEST_ENABLED off stops collection without deleting anything.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2  # noqa: E402

from acquisition_store import POSTGRES_DDL, POSTGRES_ROLLBACK_DDL  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")


def forward_statements():
    return [" ".join(stmt.split()) for stmt in POSTGRES_DDL]


def rollback_statements():
    return list(POSTGRES_ROLLBACK_DDL)


def run(statements, dry_run):
    if dry_run:
        print("-- DRY RUN: no statements executed, no database connection opened --")
        for sql in statements:
            print(sql + ";")
        return

    if not DATABASE_URL:
        print("DATABASE_URL not set, skipping migration")
        return

    conn = psycopg2.connect(DATABASE_URL.replace("postgres://", "postgresql://"))
    conn.autocommit = True
    cur = conn.cursor()
    for sql in statements:
        print("Executing: {}".format(sql[:100]))
        cur.execute(sql)
    cur.close()
    conn.close()
    print("Done.")


def main():
    parser = argparse.ArgumentParser(description="Create (or drop) the acquisition collector tables.")
    parser.add_argument("--rollback", action="store_true",
                        help="DROP acquisition_events and acquisition_daily (deletes all collected rows).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the SQL that would run, without connecting to a database.")
    args = parser.parse_args()
    run(rollback_statements() if args.rollback else forward_statements(), args.dry_run)


if __name__ == "__main__":
    main()
