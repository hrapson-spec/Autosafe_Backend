#!/usr/bin/env python3
"""Migration: vrm_hmac column + registration nullability (task 2.2).

Additive + constraint-relax only; no data is modified. Run once against the
production DB BEFORE deploying the named-params database.py:

    DATABASE_URL="postgresql://..." python migrations/add_pseudonym_and_retention.py

Idempotent. Pairs with migrations/pseudonymize_backlog.py (data change,
separate deliberate step) and scripts/retention_purge.py (scheduled).
"""
import asyncio
import os
import sys

import asyncpg

STATEMENTS = [
    # pseudonymous analytics key (HMAC-SHA256 of normalised VRM; key in env)
    "ALTER TABLE risk_checks ADD COLUMN IF NOT EXISTS vrm_hmac VARCHAR(64)",
    "CREATE INDEX IF NOT EXISTS idx_risk_checks_vrm_hmac ON risk_checks(vrm_hmac)",
    # backlog pseudonymisation nulls the plaintext VRM, so the NOT NULL
    # constraint from create_risk_checks_table.py must be relaxed
    "ALTER TABLE risk_checks ALTER COLUMN registration DROP NOT NULL",
]


async def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL not set", file=sys.stderr)
        return 1
    conn = await asyncpg.connect(url)
    try:
        for stmt in STATEMENTS:
            await conn.execute(stmt)
            print(f"OK: {stmt[:72]}")
        cols = await conn.fetch(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name='risk_checks' AND column_name IN ('vrm_hmac','registration')"
        )
        for c in cols:
            print(f"verify: {c['column_name']} nullable={c['is_nullable']}")
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
