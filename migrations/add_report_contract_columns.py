"""
Add v2 report-contract columns and lead mileage provenance.

Run this BEFORE deploying the v2 report API code. This is the "expand" half
of an expand-contract migration: it only adds nullable columns and relaxes
one constraint, so it is safe to run against the table the
currently-deployed (v1) code is still using. The v1 code selects/inserts a
fixed column list and simply ignores columns it doesn't know about, so
nothing here changes the meaning or nullability of any column the v1 code
reads or writes.

Usage:
    DATABASE_URL="postgresql://..." python migrations/add_report_contract_columns.py
    DATABASE_URL="postgresql://..." python migrations/add_report_contract_columns.py --dry-run
    DATABASE_URL="postgresql://..." python migrations/add_report_contract_columns.py --rollback
    DATABASE_URL="postgresql://..." python migrations/add_report_contract_columns.py --rollback --dry-run

--dry-run prints every statement this script would execute (forward or
rollback) without opening a database connection or requiring DATABASE_URL
to be set at all.

Rollback is NOT a full inverse of forward
------------------------------------------
--rollback drops the columns and indexes this migration adds, but
deliberately does NOT restore `risk_checks.registration NOT NULL` or
`leads.postcode NOT NULL`. Retention sets old registrations to NULL by
design, while reminder and emailed-report requests may intentionally create
leads without a postcode. Restoring either constraint could fail on valid
rows or require a destructive fabricated backfill. Both nullability changes
are therefore one-way doors: --rollback removes the new v2 surface, not the
relaxations that make retention and postcode-optional delivery safe.

The practical "rollback" for a bad v2 deploy is to redeploy the previous
(v1) image — the v1 code never looks at these columns, so the schema can
safely stay exactly as this migration (forward or rolled back) leaves it.
"""
import argparse
import os

import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL")

# (column, type) pairs added to risk_checks by this migration.
ADDED_COLUMNS = [
    ("contract_version", "VARCHAR(10)"),
    ("report_token", "VARCHAR(64)"),
    ("report_payload", "JSONB"),
    ("mileage_source", "VARCHAR(20)"),
    ("effective_mileage", "INTEGER"),
    ("match_scope", "VARCHAR(20)"),
    ("total_tests", "INTEGER"),
    ("total_failures", "INTEGER"),
    ("idempotency_key", "VARCHAR(100)"),
    ("expires_at", "TIMESTAMP"),
    ("pseudonymised_at", "TIMESTAMP"),
    ("vrm_hmac", "VARCHAR(64)"),
]

# The garage-lead request already carries this value. Persist it next to the
# mileage so downstream consumers do not have to infer whether that number was
# observed, user-entered, estimated, or missing.
LEAD_ADDED_COLUMNS = [
    ("vehicle_mileage_source", "VARCHAR(20)"),
    ("comparison_scope", "VARCHAR(20)"),
]

# (index name, CREATE statement) pairs added by this migration.
ADDED_INDEXES = [
    (
        "idx_risk_checks_report_token",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_risk_checks_report_token "
        "ON risk_checks(report_token) WHERE report_token IS NOT NULL",
    ),
    (
        "idx_risk_checks_idempotency_key",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_risk_checks_idempotency_key "
        "ON risk_checks(idempotency_key) WHERE idempotency_key IS NOT NULL",
    ),
    (
        "idx_risk_checks_vrm_hmac",
        "CREATE INDEX IF NOT EXISTS idx_risk_checks_vrm_hmac ON risk_checks(vrm_hmac)",
    ),
    (
        "idx_risk_checks_expires_at",
        "CREATE INDEX IF NOT EXISTS idx_risk_checks_expires_at ON risk_checks(expires_at)",
    ),
]

DROP_RISK_REGISTRATION_NOT_NULL_SQL = (
    "ALTER TABLE risk_checks ALTER COLUMN registration DROP NOT NULL"
)
DROP_LEAD_POSTCODE_NOT_NULL_SQL = (
    "ALTER TABLE leads ALTER COLUMN postcode DROP NOT NULL"
)


def forward_statements():
    """Statements run by default (no --rollback): the expand half."""
    statements = [
        DROP_RISK_REGISTRATION_NOT_NULL_SQL,
        DROP_LEAD_POSTCODE_NOT_NULL_SQL,
    ]
    statements += [
        "ALTER TABLE risk_checks ADD COLUMN IF NOT EXISTS {} {}".format(col, col_type)
        for col, col_type in ADDED_COLUMNS
    ]
    statements += [
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS {} {}".format(col, col_type)
        for col, col_type in LEAD_ADDED_COLUMNS
    ]
    statements += [create_sql for _name, create_sql in ADDED_INDEXES]
    return statements


def rollback_statements():
    """Statements run with --rollback: drop indexes + columns only.

    Deliberately never emits a SET NOT NULL on risk_checks.registration or
    leads.postcode — see the module docstring for why both are one-way doors.
    """
    statements = [
        "DROP INDEX IF EXISTS {}".format(name) for name, _create_sql in ADDED_INDEXES
    ]
    statements += [
        "ALTER TABLE risk_checks DROP COLUMN IF EXISTS {}".format(col)
        for col, _col_type in ADDED_COLUMNS
    ]
    statements += [
        "ALTER TABLE leads DROP COLUMN IF EXISTS {}".format(col)
        for col, _col_type in LEAD_ADDED_COLUMNS
    ]
    return statements


def run(statements, dry_run):
    if dry_run:
        print("-- DRY RUN: no statements executed, no database connection opened --")
        for sql in statements:
            print(sql + ";")
        return

    if not DATABASE_URL:
        print("DATABASE_URL not set, skipping migration")
        return

    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()

    for sql in statements:
        print("Executing: {}".format(sql))
        cur.execute(sql)

    cur.close()
    conn.close()
    print("Done.")


def main():
    parser = argparse.ArgumentParser(
        description="Add (or roll back) v2 report-contract columns on risk_checks."
    )
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="Drop the columns/indexes this migration adds. Does NOT restore "
             "risk_checks.registration or leads.postcode NOT NULL (see module "
             "docstring — one-way doors).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the SQL that would run, without connecting to a database.",
    )
    args = parser.parse_args()

    statements = rollback_statements() if args.rollback else forward_statements()
    run(statements, args.dry_run)


if __name__ == "__main__":
    main()
