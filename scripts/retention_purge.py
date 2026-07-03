#!/usr/bin/env python3
"""Scheduled retention purge (notice commitment: 24 months; task 2.2).

Pseudonymises every risk_checks row older than the retention horizon exactly
like the backlog migration: vrm_hmac set, plaintext registration + postcode
NULLed. Run monthly (Railway cron / scheduled job). Dry-run by default.

    DATABASE_URL=... VRM_HMAC_KEY=... python scripts/retention_purge.py            # dry-run
    DATABASE_URL=... VRM_HMAC_KEY=... python scripts/retention_purge.py --execute
"""
import argparse
import asyncio
import hashlib
import hmac
import os
import sys

import asyncpg

RETENTION_MONTHS = 24
BATCH = 500


def vrm_hmac(key: str, vrm: str) -> str:
    return hmac.new(key.encode(), "".join(vrm.upper().split()).encode(), hashlib.sha256).hexdigest()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="apply (default: dry-run)")
    ap.add_argument("--months", type=int, default=RETENTION_MONTHS)
    args = ap.parse_args()

    url = os.environ.get("DATABASE_URL")
    key = os.environ.get("VRM_HMAC_KEY", "")
    if not url:
        print("DATABASE_URL not set", file=sys.stderr)
        return 1
    if len(key) < 32:
        print("VRM_HMAC_KEY missing or <32 chars", file=sys.stderr)
        return 1

    conn = await asyncpg.connect(url)
    try:
        where = f"created_at < now() - interval '{args.months} months' AND registration IS NOT NULL"
        total = await conn.fetchval(f"SELECT count(*) FROM risk_checks WHERE {where}")
        print(f"rows past {args.months}-month retention with plaintext VRN: {total}")
        if not args.execute:
            print("DRY-RUN — re-run with --execute to apply")
            return 0
        done = 0
        while True:
            rows = await conn.fetch(f"SELECT id, registration FROM risk_checks WHERE {where} LIMIT $1", BATCH)
            if not rows:
                break
            await conn.executemany(
                "UPDATE risk_checks SET vrm_hmac=$2, registration=NULL, postcode=NULL WHERE id=$1",
                [(r["id"], vrm_hmac(key, r["registration"])) for r in rows],
            )
            done += len(rows)
            print(f"purged {done}/{total}", flush=True)
        print("DONE")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
