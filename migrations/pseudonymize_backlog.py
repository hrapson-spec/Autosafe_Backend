#!/usr/bin/env python3
"""Backlog pseudonymisation (owner ruling D1; LIA_RISK_CHECKS.md §5).

Rows collected before the corrected privacy notice went live were gathered
under an inaccurate transparency statement. This sets vrm_hmac, then NULLs
the plaintext registration and postcode, for every row created before the
cutoff. Fingerprint-based outcome linkage (history_json dates+odometers)
survives pseudonymisation by design.

Dry-run by default:

    DATABASE_URL=... VRM_HMAC_KEY=... python migrations/pseudonymize_backlog.py \
        --before "2026-07-04T00:00:00Z"            # dry-run: counts only
    ... --before "2026-07-04T00:00:00Z" --execute  # applies, batched

Idempotent (targets registration IS NOT NULL only). Requires
add_pseudonym_and_retention.py to have run first.
"""
import argparse
import asyncio
import hashlib
import hmac
import os
import sys
from datetime import datetime

import asyncpg

BATCH = 500


def vrm_hmac(key: str, vrm: str) -> str:
    return hmac.new(key.encode(), "".join(vrm.upper().split()).encode(), hashlib.sha256).hexdigest()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True, help="ISO timestamp: rows created before this are pseudonymised (the moment the corrected notice deployed)")
    ap.add_argument("--execute", action="store_true", help="apply changes (default: dry-run)")
    args = ap.parse_args()
    cutoff = datetime.fromisoformat(args.before.replace("Z", "+00:00"))

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
        total = await conn.fetchval(
            "SELECT count(*) FROM risk_checks WHERE created_at < $1 AND registration IS NOT NULL",
            cutoff,
        )
        already = await conn.fetchval(
            "SELECT count(*) FROM risk_checks WHERE created_at < $1 AND registration IS NULL",
            cutoff,
        )
        print(f"backlog rows with plaintext VRN: {total}  (already pseudonymised: {already})")
        if not args.execute:
            print("DRY-RUN — re-run with --execute to apply")
            return 0

        done = 0
        while True:
            rows = await conn.fetch(
                "SELECT id, registration FROM risk_checks "
                "WHERE created_at < $1 AND registration IS NOT NULL LIMIT $2",
                cutoff, BATCH,
            )
            if not rows:
                break
            await conn.executemany(
                "UPDATE risk_checks SET vrm_hmac=$2, registration=NULL, postcode=NULL WHERE id=$1",
                [(r["id"], vrm_hmac(key, r["registration"])) for r in rows],
            )
            done += len(rows)
            print(f"pseudonymised {done}/{total}", flush=True)

        remaining = await conn.fetchval(
            "SELECT count(*) FROM risk_checks WHERE created_at < $1 AND registration IS NOT NULL",
            cutoff,
        )
        print(f"DONE. remaining plaintext rows before cutoff: {remaining} (must be 0)")
        return 0 if remaining == 0 else 2
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
