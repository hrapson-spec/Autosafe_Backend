#!/usr/bin/env python3
"""
Retention sweep: rolling pseudonymisation of `risk_checks` rows past the
published retention window.

Per docs/LIA_RISK_CHECKS.md §2/§3: the privacy notice discloses a 24-month
retention period for vehicle check records. After that window, the
plaintext registration and postcode serve no purpose and must be removed;
the row is kept in pseudonymised form (HMAC-SHA256 of the registration,
keyed by a secret held outside the database) so that P2 (prediction-
accuracy measurement against later DVSA outcome data) can continue on the
de-identified backlog.

This script is the recurring mechanism for that commitment. For the
one-time backlog created under the PRIOR (inaccurate) notice, see
migrations/pseudonymize_backlog.py instead -- same mechanics, fixed
--before cutoff rather than a rolling window.

Safe by default:
  - dry-run (report only) unless --execute is passed.
  - --execute refuses to run without VRM_HMAC_KEY set to >= 32 chars.
  - both the SELECT and the UPDATE filter on created_at < cutoff -- rows
    newer than the cutoff are never touched, and the check is repeated at
    the point of mutation, not just at selection time.
  - idempotent: pseudonymised_at IS NULL gates both the SELECT and the
    UPDATE, so a second run (or a concurrent one) finds nothing left to
    do and issues zero UPDATE statements.
  - dry-run never even SELECTs the registration/postcode columns, so
    there is nothing PII-shaped in memory to leak into dry-run output.

Design notes (flagged per task instructions):

  1. Month arithmetic: RETENTION_MONTHS is a count of *calendar* months,
     so "24 months ago" must clamp correctly at month-end (e.g. 31 Jan
     minus 1 month -> 31 Dec, but 31 Mar minus 1 month -> 28/29 Feb, not
     an invalid "31 Feb" or a silently-wrong date obtained by subtracting
     a fixed number of days). We use dateutil.relativedelta for this.
     python-dateutil is NOT added to requirements.txt: it is already a
     transitive dependency here (requirements.txt pins pandas==2.2.0,
     and `pip show python-dateutil` reports `Required-by: matplotlib,
     pandas`) -- verified present in the target venv before choosing this
     approach. Per the brief's fallback instruction ("if not [importable],
     compute calendar months by hand"), hand-rolled arithmetic was the
     documented fallback; it was not needed.

  2. Code sharing with migrations/pseudonymize_backlog.py: NOT done via a
     shared module or a sys.path import between scripts/ and migrations/.
     Both existing migrations/*.py files in this repo (add_utm_tracking.py,
     add_reminder_columns.py) are fully standalone with zero cross-module
     imports -- that is the established convention for one-off scripts in
     this directory. Importing scripts/retention_sweep.py from
     migrations/pseudonymize_backlog.py via a path hack would break that
     convention for marginal benefit (the shared surface is small: the
     HMAC helpers, the key-length gate, the batch UPDATE, and the
     verification block -- roughly 100 lines, more than the ~60-line
     estimate but still small and low-churn). pseudonymize_backlog.py
     duplicates that logic instead, with a comment pointing back here.

  3. Update idiom: fetch each batch's (id, registration) rows, compute a
     per-row HMAC in Python (registration varies per row so a single
     uniform UPDATE can't set vrm_hmac correctly), then issue one
     conn.executemany() per batch with (hmac_or_none, id, cutoff) tuples.
     executemany() is the simplest correct asyncpg idiom for "same
     statement shape, many different parameter sets" -- it avoids
     hand-building an `UPDATE ... FROM (VALUES ...)` with explicit
     ::uuid/::timestamp casts for a dynamically-sized VALUES list, which
     is more failure-prone to get right than a parameterised statement
     executed N times.

Usage:
    python scripts/retention_sweep.py                    # dry run (default)
    python scripts/retention_sweep.py --execute           # perform the sweep
    python scripts/retention_sweep.py --months 1           # staging rehearsal, dry-run
    python scripts/retention_sweep.py --months 1 --execute --batch 50

Env:
    DATABASE_URL      Required. Railway-style postgres:// is rewritten to
                       postgresql:// for asyncpg, same as database.py.
    RETENTION_MONTHS  Default retention window in months. Defaults to 24 --
                       that number is the PUBLISHED privacy-notice
                       commitment (docs/LIA_RISK_CHECKS.md). Changing it in
                       production requires changing the notice FIRST; this
                       env override exists so staging can rehearse the
                       sweep against a short window without touching the
                       notice or the default.
    VRM_HMAC_KEY       Required for --execute (>= 32 chars). Not required
                       for dry-run.
    BATCH              Default batch size for the SELECT/UPDATE loop
                       (default 500). Overridable per-run via --batch.
"""
import argparse
import asyncio
import hashlib
import hmac
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
from dateutil.relativedelta import relativedelta

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Minimum acceptable length for VRM_HMAC_KEY. This can't verify entropy,
# but it rules out empty/trivially-short placeholders being used as a
# pseudonymisation key.
MIN_HMAC_KEY_LEN = 32


def _int_env(name: str, default: int) -> int:
    """Read an integer env var, falling back to `default` if unset/empty,
    and refusing loudly (rather than raising an opaque ValueError at
    import time) if it's set to something non-numeric."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"ERROR: {name}={raw!r} is not a valid integer.", file=sys.stderr)
        sys.exit(1)


# 24 is the PUBLISHED privacy-notice commitment -- see the module docstring
# and docs/LIA_RISK_CHECKS.md §2/§3. Do not bump this constant to quietly
# change production retention behaviour; change the notice first.
DEFAULT_RETENTION_MONTHS = _int_env("RETENTION_MONTHS", 24)

# Default batch size for the SELECT/UPDATE loop. Overridable via BATCH env
# (ops tuning without a redeploy) and via --batch (single-run override).
DEFAULT_BATCH_SIZE = _int_env("BATCH", 500)

# Shared WHERE-clause fragment: a row is a pseudonymisation candidate if it
# predates the cutoff, hasn't already been pseudonymised, AND still carries
# at least one of the fields pseudonymisation would touch. `$1` is always
# the cutoff timestamp in every query that embeds this fragment.
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
    database.py's get_pool(): Railway hands out postgres://, asyncpg
    requires the postgresql:// scheme."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        return None
    return url.replace("postgres://", "postgresql://")


async def get_connection() -> asyncpg.Connection:
    """Open a single standalone asyncpg connection. This is a one-shot CLI
    tool, not a long-lived server process, so a bare connection is simpler
    and sufficient -- no pool needed."""
    url = get_database_url()
    if not url:
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)
    return await asyncpg.connect(url)


# ---------------------------------------------------------------------------
# Pseudonymisation primitives (pure functions -- no DB, no I/O)
# ---------------------------------------------------------------------------

def normalize_vrm(vrm: str) -> str:
    """Normalize a VRM before hashing: uppercase, strip ALL whitespace (not
    just leading/trailing) so "ab12 cde" and "AB12CDE" normalize
    identically."""
    return "".join(vrm.upper().split())


def vrm_hmac(key: str, vrm: str) -> str:
    """HMAC-SHA256 hexdigest of the normalized VRM, keyed by VRM_HMAC_KEY.

    Deterministic (same key + vrm -> same digest always) and, without the
    key, not reversible to the plaintext VRM -- this is the
    pseudonymisation primitive that lets us null the plaintext
    registration while keeping a stable per-vehicle join key for P2
    (accuracy measurement), per docs/LIA_RISK_CHECKS.md §2.
    """
    normalized = normalize_vrm(vrm)
    return hmac.new(key.encode("utf-8"), normalized.encode("utf-8"), hashlib.sha256).hexdigest()


def require_hmac_key(execute: bool) -> Optional[str]:
    """Read VRM_HMAC_KEY from the environment.

    Required (>= MIN_HMAC_KEY_LEN chars) when execute=True; refuses via
    sys.exit(1) otherwise. For dry-run (execute=False), no requirement is
    enforced -- dry-run never computes an HMAC and never uses this value.
    """
    key = os.environ.get("VRM_HMAC_KEY")
    if execute and (not key or len(key) < MIN_HMAC_KEY_LEN):
        print(
            f"ERROR: --execute requires VRM_HMAC_KEY to be set to at least "
            f"{MIN_HMAC_KEY_LEN} characters. Refusing to run.",
            file=sys.stderr,
        )
        sys.exit(1)
    return key


def compute_cutoff(now: datetime, months: int) -> datetime:
    """Calendar-month-accurate cutoff: `now` minus `months` months, with
    day-of-month clamped to the last valid day of the target month (e.g.
    31 Mar - 1 month -> 28 or 29 Feb depending on leap year; never an
    invalid "31 Feb" and never off-by-a-few-days the way a fixed
    days-per-month approximation would be).

    Delegates to dateutil.relativedelta -- see the module docstring's
    "Design notes" #1 for why this doesn't add a new dependency.
    """
    return now - relativedelta(months=months)


# ---------------------------------------------------------------------------
# Queries (all take a connection object so a fake can inject cleanly in tests)
# ---------------------------------------------------------------------------

async def count_candidates(conn: Any, cutoff: datetime) -> int:
    """Total rows matching the candidate filter (dry-run headline number)."""
    sql = f"SELECT COUNT(*) FROM risk_checks WHERE {CANDIDATE_FILTER_SQL}"
    return await conn.fetchval(sql, cutoff)


async def year_breakdown(conn: Any, cutoff: datetime) -> List[Tuple[int, int]]:
    """Candidate counts grouped by created_at year, for dry-run reporting."""
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
    """Up to `limit` candidate row ids, for dry-run reporting.

    Deliberately selects `id` ONLY -- never registration/postcode -- so
    there is no PII in memory to leak into dry-run output even by
    accident.
    """
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
    """Fetch up to `batch_size` candidate (id, registration) rows, ordered
    by id for deterministic batching.

    No OFFSET/cursor bookkeeping is needed: re-running this exact query
    after each batch's UPDATE naturally excludes the rows just processed
    (their pseudonymised_at is no longer NULL), so the loop is
    self-draining and idempotent by construction.
    """
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
    """Pseudonymise one batch.

    For each row: compute an HMAC if it has a plaintext registration
    (rows without one keep whatever vrm_hmac they already had, via
    COALESCE -- NULL stays NULL), then null registration/postcode/
    report_payload/report_token and stamp pseudonymised_at.

    The WHERE clause repeats `pseudonymised_at IS NULL AND created_at <
    cutoff` even though the caller already filtered on both when it
    produced `rows` -- a deliberate belt-and-braces guard so the mutation
    itself can never touch an already-pseudonymised or too-recent row,
    even if a future caller reuses this function against a differently
    filtered row list.
    """
    if not rows:
        return 0
    if not key or len(key) < MIN_HMAC_KEY_LEN:
        # Defense in depth: require_hmac_key() should already have caught
        # this before we ever get here. This path must never proceed with
        # an invalid key regardless of how it was reached.
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
    """Post-run sanity checks. Run after ANY mode (dry-run or execute) --
    in dry-run this just reports the pre-existing state, since nothing
    changed.

    (a) rows older than cutoff that still have a plaintext registration --
        expect 0 after a successful --execute.
    (b) pseudonymised-row counts: total pseudonymised, and how many of
        those have vrm_hmac set. The brief's originally-specified "had a
        registration" split can't actually be reconstructed post-hoc: once
        registration is NULLed, a row that never had one and a row that
        did are indistinguishable by that column alone. Rather than
        fabricate a distinction the data can't support, we report the two
        counts that ARE honestly computable (per the brief's own fallback
        instruction for this check).
    (c) pseudonymised rows that still carry a report_payload -- expect 0.
    """
    stale_plaintext = await conn.fetchval(
        "SELECT COUNT(*) FROM risk_checks WHERE created_at < $1 AND registration IS NOT NULL",
        cutoff,
    )
    pseudonymised_total = await conn.fetchval(
        "SELECT COUNT(*) FROM risk_checks WHERE pseudonymised_at IS NOT NULL"
    )
    pseudonymised_with_hmac = await conn.fetchval(
        "SELECT COUNT(*) FROM risk_checks WHERE pseudonymised_at IS NOT NULL AND vrm_hmac IS NOT NULL"
    )
    pseudonymised_with_payload = await conn.fetchval(
        "SELECT COUNT(*) FROM risk_checks WHERE pseudonymised_at IS NOT NULL AND report_payload IS NOT NULL"
    )

    print("\n--- Verification ---")
    print(f"(a) rows < cutoff still with plaintext registration : {stale_plaintext}")
    print(f"(b) pseudonymised rows, total                        : {pseudonymised_total}")
    print(f"(b) pseudonymised rows with vrm_hmac set             : {pseudonymised_with_hmac}")
    print(f"(c) pseudonymised rows with report_payload remaining : {pseudonymised_with_payload}")

    return {
        "stale_plaintext": stale_plaintext,
        "pseudonymised_total": pseudonymised_total,
        "pseudonymised_with_hmac": pseudonymised_with_hmac,
        "pseudonymised_with_payload": pseudonymised_with_payload,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def run_dry_run(conn: Any, cutoff: datetime) -> None:
    total = await count_candidates(conn, cutoff)
    print(f"[DRY RUN] cutoff = {cutoff.isoformat()}")
    print(f"[DRY RUN] candidates matching retention filter: {total}")
    print("[DRY RUN] per-year breakdown:")
    for year_num, n in await year_breakdown(conn, cutoff):
        print(f"    {year_num}: {n}")
    ids = await sample_ids(conn, cutoff, 3)
    print(f"[DRY RUN] sample ids (up to 3, no PII): {[str(i) for i in ids]}")
    print("[DRY RUN] no UPDATE statements were issued.")


async def run_execute(conn: Any, key: str, cutoff: datetime, batch_size: int) -> int:
    print(f"[EXECUTE] cutoff = {cutoff.isoformat()}, batch size = {batch_size}")
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
    """Single entry point for one full sweep (dry-run or execute) plus the
    post-run verification block. Used by both the CLI (_amain) and tests
    (against a fake connection)."""
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
            "Rolling retention sweep for risk_checks: pseudonymises "
            "(HMACs the registration, nulls PII/report fields) rows older "
            "than the retention window. Dry-run (report only) by default."
        )
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform the sweep. Default is dry-run (report only, no writes).",
    )
    parser.add_argument(
        "--months",
        type=int,
        default=None,
        help=(
            "Override the retention window in months (default: RETENTION_MONTHS "
            f"env or {DEFAULT_RETENTION_MONTHS}). For staging rehearsal only -- "
            "production retention is a privacy-notice commitment, not a knob."
        ),
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=None,
        help=f"Override batch size (default: BATCH env or {DEFAULT_BATCH_SIZE}).",
    )
    return parser


async def _amain(args: argparse.Namespace) -> int:
    months = args.months if args.months is not None else DEFAULT_RETENTION_MONTHS
    batch_size = args.batch if args.batch is not None else DEFAULT_BATCH_SIZE

    if months <= 0:
        print(f"ERROR: --months/RETENTION_MONTHS must be positive, got {months}.", file=sys.stderr)
        return 1
    if batch_size <= 0:
        print(f"ERROR: --batch/BATCH must be positive, got {batch_size}.", file=sys.stderr)
        return 1

    # Validate the key BEFORE touching the database at all.
    key = require_hmac_key(args.execute)
    cutoff = compute_cutoff(datetime.utcnow(), months)

    conn = await get_connection()
    try:
        result = await run_sweep(conn, key, cutoff, batch_size, args.execute)
    finally:
        await conn.close()

    if args.execute and (result["stale_plaintext"] > 0 or result["pseudonymised_with_payload"] > 0):
        print("ERROR: post-execute verification failed -- see (a)/(c) above.", file=sys.stderr)
        return 1
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
