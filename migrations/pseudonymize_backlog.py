#!/usr/bin/env python3
"""
One-time backlog remediation: pseudonymise all `risk_checks` rows created
before the corrected privacy notice went live.

Per docs/LIA_RISK_CHECKS.md §6 and owner ruling D1 (2026-07-03): rows
collected under the PRIOR, inaccurate privacy notice are remediated
immediately -- rather than waiting out the normal 24-month retention
horizon -- by pseudonymising them the same way the ongoing retention
sweep does: HMAC the registration, null the plaintext PII/report fields,
stamp pseudonymised_at. The stable keyed digest may support a separately
reviewed aggregate-quality workflow, but this migration neither implements nor
approves any later-outcome source or linkage method.

This script IMPLEMENTS that remediation (previously only a doc reference
to a script that never existed in git history -- see docs/LIA_RISK_CHECKS.md
§6). It has not been executed against production as of this RC; that run
is owner-scheduled, using the --before value below.

--before is REQUIRED and must be supplied by the owner: it is the exact
moment the corrected notice went live. There is no defensible default to
guess here, so this script refuses to run without it.

Usage:
    python migrations/pseudonymize_backlog.py --before 2026-07-03T00:00:00
    python migrations/pseudonymize_backlog.py --before 2026-07-03T00:00:00Z --execute

Safe by default:
  - dry-run (report only) unless --execute is passed.
  - --execute refuses to run without VRM_HMAC_KEY set to >= 32 chars.
  - both the SELECT and the UPDATE filter on created_at < --before -- rows
    at or after that moment are never touched, and the check is repeated
    at the point of mutation, not just at selection time.
  - idempotent: pseudonymised_at IS NULL gates both the SELECT and the
    UPDATE, so a second run finds nothing left to do and issues zero
    UPDATE statements.
  - dry-run never even SELECTs the registration/postcode columns, so
    there is nothing PII-shaped in memory to leak into dry-run output.

Env:
    DATABASE_URL   Required. Railway-style postgres:// is rewritten to
                   postgresql:// for asyncpg, same as database.py.
    VRM_HMAC_KEY   Required for --execute (>= 32 chars). Not required for
                   dry-run. Must be the SAME key scripts/retention_sweep.py
                   uses -- these are two remediation paths for the same
                   column, not two different pseudonymisation schemes.
    BATCH          Default batch size for the SELECT/UPDATE loop
                   (default 500). Overridable per-run via --batch.

Why this duplicates scripts/retention_sweep.py instead of importing it:
Both existing migrations/*.py scripts in this repo (add_utm_tracking.py,
add_reminder_columns.py) are fully standalone with zero cross-module
imports -- that is the established convention for one-off scripts in this
directory. A sys.path bootstrap from migrations/ into scripts/ would be a
fragile, repo-convention-breaking way to save a modest amount of code (the
shared surface -- HMAC helpers, key-length gate, batch UPDATE,
verification block -- is small and low-churn). If the pseudonymisation
SQL shape ever changes, change it in BOTH files.
"""
import argparse
import asyncio
import hashlib
import hmac
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MIN_HMAC_KEY_LEN = 32


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"ERROR: {name}={raw!r} is not a valid integer.", file=sys.stderr)
        sys.exit(1)


DEFAULT_BATCH_SIZE = _int_env("BATCH", 500)

# Shared WHERE-clause fragment -- kept identical to
# scripts/retention_sweep.py's CANDIDATE_FILTER_SQL. `$1` is always the
# --before cutoff timestamp in every query that embeds this fragment.
CANDIDATE_FILTER_SQL = """
    created_at < $1
    AND pseudonymised_at IS NULL
    AND (
        registration IS NOT NULL
        OR postcode IS NOT NULL
        OR report_payload IS NOT NULL
        OR report_token IS NOT NULL
    )
"""


# ---------------------------------------------------------------------------
# Connection (standalone -- no import from main.py / database.py)
# ---------------------------------------------------------------------------

def get_database_url() -> Optional[str]:
    """Read DATABASE_URL and rewrite postgres:// -> postgresql://, same as
    database.py's get_pool()."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        return None
    return url.replace("postgres://", "postgresql://")


async def get_connection() -> asyncpg.Connection:
    """Open a single standalone asyncpg connection -- this is a one-shot
    CLI tool, not a long-lived server process."""
    url = get_database_url()
    if not url:
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)
    return await asyncpg.connect(url)


# ---------------------------------------------------------------------------
# Pseudonymisation primitives (pure functions -- no DB, no I/O)
# ---------------------------------------------------------------------------

def normalize_vrm(vrm: str) -> str:
    """Normalize a VRM before hashing: uppercase, strip ALL whitespace."""
    return "".join(vrm.upper().split())


def vrm_hmac(key: str, vrm: str) -> str:
    """HMAC-SHA256 hexdigest of the normalized VRM. Must match
    scripts/retention_sweep.py's implementation exactly -- both scripts
    pseudonymise the same column and must produce the same digest for the
    same (key, vrm)."""
    normalized = normalize_vrm(vrm)
    return hmac.new(key.encode("utf-8"), normalized.encode("utf-8"), hashlib.sha256).hexdigest()


def require_hmac_key(execute: bool) -> Optional[str]:
    """Read VRM_HMAC_KEY; required (>= MIN_HMAC_KEY_LEN chars) when
    execute=True, not required for dry-run."""
    key = os.environ.get("VRM_HMAC_KEY")
    if execute and (not key or len(key) < MIN_HMAC_KEY_LEN):
        print(
            f"ERROR: --execute requires VRM_HMAC_KEY to be set to at least "
            f"{MIN_HMAC_KEY_LEN} characters. Refusing to run.",
            file=sys.stderr,
        )
        sys.exit(1)
    return key


def parse_before(value: str) -> datetime:
    """Parse the required --before ISO-8601 argument into a naive UTC
    datetime (matching risk_checks.created_at, a TIMESTAMP WITHOUT TIME
    ZONE column).

    A trailing "Z" is normalised explicitly to "+00:00" before delegating
    to datetime.fromisoformat. This keeps the input rule unambiguous across
    supported Python runtimes and handles values copied from a JavaScript
    `Date.toISOString()`. Any resulting offset is converted to UTC and the
    tzinfo dropped, to compare cleanly against the naive `created_at` column.
    """
    text = value.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        print(f"ERROR: --before {value!r} is not a valid ISO-8601 timestamp: {exc}", file=sys.stderr)
        sys.exit(1)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


# ---------------------------------------------------------------------------
# Queries (all take a connection object so a fake can inject cleanly in tests)
# ---------------------------------------------------------------------------

async def count_candidates(conn: Any, cutoff: datetime) -> int:
    sql = f"SELECT COUNT(*) FROM risk_checks WHERE {CANDIDATE_FILTER_SQL}"
    return await conn.fetchval(sql, cutoff)


async def year_breakdown(conn: Any, cutoff: datetime) -> List[Tuple[int, int]]:
    sql = f"""
        SELECT date_trunc('year', created_at) AS yr, COUNT(*) AS n
        FROM risk_checks
        WHERE {CANDIDATE_FILTER_SQL}
        GROUP BY 1
        ORDER BY 1
    """
    rows = await conn.fetch(sql, cutoff)
    result = []
    for r in rows:
        yr = r["yr"]
        year_num = yr.year if hasattr(yr, "year") else int(yr)
        result.append((year_num, r["n"]))
    return result


async def sample_ids(conn: Any, cutoff: datetime, limit: int = 3) -> List[Any]:
    """Up to `limit` candidate row ids only -- never registration/postcode."""
    sql = f"""
        SELECT id
        FROM risk_checks
        WHERE {CANDIDATE_FILTER_SQL}
        ORDER BY id
        LIMIT $2
    """
    rows = await conn.fetch(sql, cutoff, limit)
    return [r["id"] for r in rows]


async def fetch_candidate_batch(conn: Any, cutoff: datetime, batch_size: int) -> List[Tuple[Any, Optional[str]]]:
    """Fetch up to `batch_size` candidate (id, registration) rows. See
    scripts/retention_sweep.py's version of this function for why no
    OFFSET/cursor is needed."""
    sql = f"""
        SELECT id, registration
        FROM risk_checks
        WHERE {CANDIDATE_FILTER_SQL}
        ORDER BY id
        LIMIT $2
    """
    rows = await conn.fetch(sql, cutoff, batch_size)
    return [(r["id"], r["registration"]) for r in rows]


async def apply_batch(conn: Any, key: str, rows: List[Tuple[Any, Optional[str]]], cutoff: datetime) -> int:
    """Pseudonymise one batch. Identical mechanics to
    scripts/retention_sweep.py's apply_batch -- see that function's
    docstring for the rationale (COALESCE for vrm_hmac, belt-and-braces
    WHERE clause)."""
    if not rows:
        return 0
    if not key or len(key) < MIN_HMAC_KEY_LEN:
        print(
            f"ERROR: refusing to execute without a valid VRM_HMAC_KEY (>= {MIN_HMAC_KEY_LEN} chars).",
            file=sys.stderr,
        )
        sys.exit(1)

    sql = """
        UPDATE risk_checks
        SET vrm_hmac = COALESCE($1, vrm_hmac),
            registration = NULL,
            postcode = NULL,
            report_payload = NULL,
            report_token = NULL,
            pseudonymised_at = NOW()
        WHERE id = $2
          AND pseudonymised_at IS NULL
          AND created_at < $3
    """
    args = []
    for row_id, registration in rows:
        digest = vrm_hmac(key, registration) if registration else None
        args.append((digest, row_id, cutoff))

    await conn.executemany(sql, args)
    return len(rows)


async def run_verification(conn: Any, cutoff: datetime) -> Dict[str, int]:
    """Verify every field this migration promises to remove.

    This intentionally mirrors the rolling sweep: checking only registration
    or only the payload could report success while postcode or a live bearer
    token remained on an old row.
    """
    stale_sensitive_fields = await conn.fetchval(
        """
        SELECT COUNT(*) FROM risk_checks
        WHERE created_at < $1
          AND (
              registration IS NOT NULL
              OR postcode IS NOT NULL
              OR report_payload IS NOT NULL
              OR report_token IS NOT NULL
          )
        /* stale sensitive fields */
        """,
        cutoff,
    )
    pseudonymised_total = await conn.fetchval(
        "SELECT COUNT(*) FROM risk_checks WHERE pseudonymised_at IS NOT NULL"
    )
    pseudonymised_with_hmac = await conn.fetchval(
        "SELECT COUNT(*) FROM risk_checks WHERE pseudonymised_at IS NOT NULL AND vrm_hmac IS NOT NULL"
    )
    pseudonymised_with_sensitive_fields = await conn.fetchval(
        """
        SELECT COUNT(*) FROM risk_checks
        WHERE pseudonymised_at IS NOT NULL
          AND (
              registration IS NOT NULL
              OR postcode IS NOT NULL
              OR report_payload IS NOT NULL
              OR report_token IS NOT NULL
          )
        /* pseudonymised sensitive fields */
        """
    )

    print("\n--- Verification ---")
    print(f"(a) rows < cutoff still with sensitive fields       : {stale_sensitive_fields}")
    print(f"(b) pseudonymised rows, total                        : {pseudonymised_total}")
    print(f"(b) pseudonymised rows with vrm_hmac set             : {pseudonymised_with_hmac}")
    print(f"(c) pseudonymised rows with sensitive fields         : {pseudonymised_with_sensitive_fields}")

    return {
        "stale_sensitive_fields": stale_sensitive_fields,
        "pseudonymised_total": pseudonymised_total,
        "pseudonymised_with_hmac": pseudonymised_with_hmac,
        "pseudonymised_with_sensitive_fields": pseudonymised_with_sensitive_fields,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def run_dry_run(conn: Any, cutoff: datetime) -> None:
    total = await count_candidates(conn, cutoff)
    print(f"[DRY RUN] before = {cutoff.isoformat()}")
    print(f"[DRY RUN] candidates matching backlog filter: {total}")
    print("[DRY RUN] per-year breakdown:")
    for year_num, n in await year_breakdown(conn, cutoff):
        print(f"    {year_num}: {n}")
    ids = await sample_ids(conn, cutoff, 3)
    print(f"[DRY RUN] sample ids (up to 3, no PII): {[str(i) for i in ids]}")
    print("[DRY RUN] no UPDATE statements were issued.")


async def run_execute(conn: Any, key: str, cutoff: datetime, batch_size: int) -> int:
    print(f"[EXECUTE] before = {cutoff.isoformat()}, batch size = {batch_size}")
    total_updated = 0
    batch_num = 0
    while True:
        batch = await fetch_candidate_batch(conn, cutoff, batch_size)
        if not batch:
            break
        batch_num += 1
        updated = await apply_batch(conn, key, batch, cutoff)
        total_updated += updated
        print(f"[EXECUTE] batch {batch_num}: {updated} row(s) updated (running total {total_updated})")
    print(f"[EXECUTE] done. total rows updated: {total_updated}")
    return total_updated


async def run_sweep(conn: Any, key: Optional[str], cutoff: datetime, batch_size: int, execute: bool) -> Dict[str, int]:
    if execute:
        await run_execute(conn, key, cutoff, batch_size)
    else:
        await run_dry_run(conn, cutoff)
    return await run_verification(conn, cutoff)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "One-time backlog remediation (docs/LIA_RISK_CHECKS.md §6 / "
            "owner ruling D1): pseudonymise risk_checks rows created "
            "before the corrected privacy notice went live. --before is "
            "required. Dry-run (report only) by default."
        )
    )
    parser.add_argument(
        "--before",
        required=True,
        help="ISO-8601 timestamp: the moment the corrected privacy notice went live (owner-supplied).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform the remediation. Default is dry-run (report only, no writes).",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=None,
        help=f"Override batch size (default: BATCH env or {DEFAULT_BATCH_SIZE}).",
    )
    return parser


async def _amain(args: argparse.Namespace) -> int:
    batch_size = args.batch if args.batch is not None else DEFAULT_BATCH_SIZE
    if batch_size <= 0:
        print(f"ERROR: --batch/BATCH must be positive, got {batch_size}.", file=sys.stderr)
        return 1

    # Validate the key BEFORE touching the database at all.
    key = require_hmac_key(args.execute)
    cutoff = parse_before(args.before)

    conn = await get_connection()
    try:
        result = await run_sweep(conn, key, cutoff, batch_size, args.execute)
    finally:
        await conn.close()

    if args.execute and (
        result["stale_sensitive_fields"] > 0
        or result["pseudonymised_with_sensitive_fields"] > 0
    ):
        print("ERROR: post-execute verification failed -- see (a)/(c) above.", file=sys.stderr)
        return 1
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
