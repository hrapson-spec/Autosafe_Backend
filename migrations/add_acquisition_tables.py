"""Add the three acquisition_v2 tables. Idempotent, additive and transactional.

Raw rows cover one fixed 30-minute journey; aggregation deletes them atomically.
Counts are retained for three calendar months. No existing customer or v1 table
is touched. --rollback is restricted to explicit disposable local staging.
"""
import argparse
import os
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2  # noqa: E402

from acquisition_store import POSTGRES_DDL, POSTGRES_ROLLBACK_DDL, _SCHEMA_LOCK_KEY  # noqa: E402

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
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK_KEY,))
                for sql in statements:
                    print("Executing: {}".format(sql[:100]))
                    cur.execute(sql)
    finally:
        conn.close()
    print("Done.")


def main():
    parser = argparse.ArgumentParser(description="Create (or drop) the acquisition collector tables.")
    parser.add_argument("--rollback", action="store_true",
                        help="Drop only acquisition_v2 tables in disposable local staging.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the SQL that would run, without connecting to a database.")
    args = parser.parse_args()
    if args.rollback and not args.dry_run:
        if os.environ.get("ACQUISITION_DISPOSABLE_STAGING") != "1" or urlparse(DATABASE_URL or "").hostname not in ("localhost", "127.0.0.1", "postgres"):
            raise SystemExit("rollback requires explicit disposable local staging")
    run(rollback_statements() if args.rollback else forward_statements(), args.dry_run)


if __name__ == "__main__":
    main()
