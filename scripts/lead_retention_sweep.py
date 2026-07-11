#!/usr/bin/env python3
"""Delete lead records after the published 12-calendar-month window.

The job is dry-run by default. ``--execute`` deletes dependent
``lead_assignments`` before each batch of ``leads`` inside one transaction,
then fails if verification finds any record older than the cutoff. It selects
and prints identifiers only; email addresses, postcodes, names, phone numbers
and registrations never enter the process output.

Usage:
    python scripts/lead_retention_sweep.py
    python scripts/lead_retention_sweep.py --execute
    python scripts/lead_retention_sweep.py --months 1 --batch 50

Environment:
    DATABASE_URL             Required for both modes.
    LEAD_RETENTION_MONTHS    Default 12; production changes require the
                             privacy notice to change first.
    LEAD_RETENTION_BATCH     Default 500.
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import asyncpg
from dateutil.relativedelta import relativedelta


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"ERROR: {name}={raw!r} is not an integer.", file=sys.stderr)
        raise SystemExit(1)


DEFAULT_RETENTION_MONTHS = _int_env("LEAD_RETENTION_MONTHS", 12)
DEFAULT_BATCH_SIZE = _int_env("LEAD_RETENTION_BATCH", 500)


def compute_cutoff(now: datetime, months: int) -> datetime:
    """Return ``now`` minus calendar months with month-end clamping."""
    return now - relativedelta(months=months)


def get_database_url() -> Optional[str]:
    url = os.environ.get("DATABASE_URL")
    return url.replace("postgres://", "postgresql://") if url else None


async def get_connection() -> asyncpg.Connection:
    url = get_database_url()
    if not url:
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        raise SystemExit(1)
    return await asyncpg.connect(url)


async def count_candidates(conn: Any, cutoff: datetime) -> int:
    return await conn.fetchval(
        "SELECT COUNT(*) FROM leads WHERE created_at < $1", cutoff
    )


async def type_breakdown(conn: Any, cutoff: datetime) -> List[Dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT lead_type, COUNT(*) AS n
        FROM leads
        WHERE created_at < $1
        GROUP BY lead_type
        ORDER BY lead_type
        """,
        cutoff,
    )
    return [dict(row) for row in rows]


async def sample_ids(conn: Any, cutoff: datetime, limit: int = 3) -> List[Any]:
    rows = await conn.fetch(
        """
        SELECT id
        FROM leads
        WHERE created_at < $1
        ORDER BY id
        LIMIT $2
        """,
        cutoff,
        limit,
    )
    return [row["id"] for row in rows]


async def fetch_batch(conn: Any, cutoff: datetime, batch_size: int) -> List[Any]:
    rows = await conn.fetch(
        """
        SELECT id
        FROM leads
        WHERE created_at < $1
        ORDER BY id
        LIMIT $2
        FOR UPDATE SKIP LOCKED
        """,
        cutoff,
        batch_size,
    )
    return [row["id"] for row in rows]


async def delete_batch(conn: Any, ids: List[Any], cutoff: datetime) -> int:
    """Delete dependent assignments, then the locked old lead rows."""
    if not ids:
        return 0
    await conn.fetchval(
        """
        WITH deleted AS (
            DELETE FROM lead_assignments
            WHERE lead_id = ANY($1::uuid[])
            RETURNING 1
        )
        SELECT COUNT(*) FROM deleted
        """,
        ids,
    )
    deleted_leads = await conn.fetchval(
        """
        WITH deleted AS (
            DELETE FROM leads
            WHERE id = ANY($1::uuid[])
              AND created_at < $2
            RETURNING 1
        )
        SELECT COUNT(*) FROM deleted
        """,
        ids,
        cutoff,
    )
    if deleted_leads != len(ids):
        raise RuntimeError(
            f"retention batch selected {len(ids)} rows but deleted {deleted_leads}"
        )
    return deleted_leads


async def verify(conn: Any, cutoff: datetime) -> int:
    return await conn.fetchval(
        "SELECT COUNT(*) FROM leads WHERE created_at < $1 /* verification */",
        cutoff,
    )


async def run_dry_run(conn: Any, cutoff: datetime) -> None:
    total = await count_candidates(conn, cutoff)
    print(f"[DRY RUN] cutoff = {cutoff.isoformat()}")
    print(f"[DRY RUN] leads eligible for deletion: {total}")
    for row in await type_breakdown(conn, cutoff):
        print(f"[DRY RUN] type {row['lead_type']}: {row['n']}")
    ids = await sample_ids(conn, cutoff)
    print(f"[DRY RUN] sample ids (no contact data): {[str(i) for i in ids]}")
    print("[DRY RUN] no DELETE statements were issued.")


async def run_execute(conn: Any, cutoff: datetime, batch_size: int) -> int:
    print(f"[EXECUTE] cutoff = {cutoff.isoformat()}, batch size = {batch_size}")
    total = 0
    batch_number = 0
    while True:
        async with conn.transaction():
            ids = await fetch_batch(conn, cutoff, batch_size)
            if not ids:
                break
            deleted = await delete_batch(conn, ids, cutoff)
        batch_number += 1
        total += deleted
        print(
            f"[EXECUTE] batch {batch_number}: {deleted} lead(s) deleted "
            f"(running total {total})"
        )
    print(f"[EXECUTE] done. total leads deleted: {total}")
    return total


async def run_sweep(
    conn: Any,
    cutoff: datetime,
    batch_size: int,
    execute: bool,
) -> Dict[str, int]:
    deleted = 0
    if execute:
        deleted = await run_execute(conn, cutoff, batch_size)
    else:
        await run_dry_run(conn, cutoff)
    stale = await verify(conn, cutoff)
    print(f"[VERIFY] leads older than cutoff remaining: {stale}")
    return {"deleted_leads": deleted, "stale_leads": stale}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Delete leads older than the retention window; dry-run by default."
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--months", type=int, default=None)
    parser.add_argument("--batch", type=int, default=None)
    return parser


async def _amain(args: argparse.Namespace) -> int:
    months = args.months if args.months is not None else DEFAULT_RETENTION_MONTHS
    batch_size = args.batch if args.batch is not None else DEFAULT_BATCH_SIZE
    if months <= 0 or batch_size <= 0:
        print("ERROR: retention months and batch size must be positive.", file=sys.stderr)
        return 1

    cutoff = compute_cutoff(datetime.utcnow(), months)
    conn = await get_connection()
    try:
        result = await run_sweep(conn, cutoff, batch_size, args.execute)
    finally:
        await conn.close()

    if args.execute and result["stale_leads"]:
        print("ERROR: post-delete verification found stale leads.", file=sys.stderr)
        return 1
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return asyncio.run(_amain(build_arg_parser().parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
