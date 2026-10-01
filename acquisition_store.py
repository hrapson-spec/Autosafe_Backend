"""Storage, retention and the primary-metric query for the first-party
acquisition collector (OA-005, DECISIONS.md D-005).

Two backends share one interface:

* ``PostgresStore``: production. Uses the existing asyncpg pool (``database.get_pool``)
  against the existing Railway Postgres. No new processor.
* ``SqliteStore``: local synthetic runs and tests only. It is selected solely
  when ``ACQUISITION_SQLITE_PATH`` is set, which production never sets.

Privacy boundary (D-005): a stored row holds only enums, booleans, random
identifiers, the release SHA, ``received_at`` and the derived ``is_bot`` flag.
No IP address, User-Agent, Referer, query string or URL ever reaches this
module: ``AcquisitionRecord`` has no field that could carry one.

Retention (D-005, D-007): raw events are rolled into ``acquisition_daily``
(event counts) and ``acquisition_landing_daily`` (landing-level counts, so the
primary metric stays reproducible after raw deletion) once they are older than
one day, deleted after 90 days, and aggregates are deleted after 25 months. The rollup is exactly-once because each raw row is marked
``rolled_up_at`` in the same transaction that adds it to the aggregate, so a
rerun (or a concurrent second worker) cannot count a row twice.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Retention constants (DECISIONS.md D-005 "Retention").
ROLLUP_AFTER = timedelta(days=1)
RAW_RETENTION = timedelta(days=90)
AGGREGATE_RETENTION_MONTHS = 25

# Aggregate day = UTC calendar day of received_at. MEASUREMENT.md requires the
# onsite reporting timezone to be recorded independently; this is it.
REPORTING_TIMEZONE = "UTC"

# Sentinel stored in the aggregate key for an absent optional dimension (a
# NULL in a unique key would let duplicate keys coexist).
NONE_KEY = "none"

# Page families that count as an eligible *public* landing (MEASUREMENT.md:
# "starts on a public landing page"). `app` is the product itself, not a public
# landing page, and is reported separately.
PUBLIC_PAGE_FAMILIES = (
    "home",
    "guide",
    "make",
    "model",
    "comparison",
    "pillar",
    "problem_hub",
    "other_public",
)

# Advisory-lock key for the retention job. Arbitrary constant, unique to this job.
_RETENTION_LOCK_KEY = 7_052_005
_SCHEMA_LOCK_KEY = 7_052_006


@dataclass(frozen=True)
class AcquisitionRecord:
    """One validated event plus the two server-derived fields."""

    event_id: str
    received_at: datetime
    schema_version: int
    metric_version: str
    event: str
    session_id: str
    page_family: str
    source_group: str
    is_bot: bool
    release_sha: Optional[str] = None
    landing_id: Optional[str] = None
    operation_id: Optional[str] = None
    entry_mode: Optional[str] = None
    persistence_mode: Optional[str] = None
    outcome_group: Optional[str] = None
    supported_result: Optional[bool] = None
    render_delivered: Optional[bool] = None
    rate_valid: Optional[bool] = None
    sample_nonzero: Optional[bool] = None
    scope_visible: Optional[bool] = None
    result_kind: Optional[str] = None
    match_scope: Optional[str] = None
    reason: Optional[str] = None
    error_category: Optional[str] = None
    stage: Optional[str] = None
    observation_state: Optional[str] = None


COLUMNS: Sequence[str] = tuple(f.name for f in fields(AcquisitionRecord))

AGGREGATE_KEY = (
    "page_family",
    "source_group",
    "event",
    "outcome_group",
    "entry_mode",
    "persistence_mode",
    "is_bot",
)


@dataclass
class RetentionResult:
    skipped_locked: bool = False
    rolled_up_events: int = 0
    rolled_up_landings: int = 0
    deleted_raw_events: int = 0
    deleted_aggregate_rows: int = 0
    aggregate_cutoff_day: Optional[str] = None
    raw_cutoff: Optional[str] = None
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def months_before(day: date, months: int) -> date:
    """Calendar-month subtraction, clamping the day (31 Mar - 1 month = 28/29 Feb)."""
    index = day.year * 12 + (day.month - 1) - months
    year, month0 = divmod(index, 12)
    month = month0 + 1
    for d in (day.day, 30, 29, 28):
        try:
            return date(year, month, d)
        except ValueError:
            continue
    raise ValueError("unreachable")  # pragma: no cover


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("naive datetime not allowed")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    """Fixed-width UTC ISO form: lexicographic order == chronological order."""
    return _as_utc(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------
# DDL (shared by migrations/add_acquisition_tables.py and runtime ensure_schema)
# ---------------------------------------------------------------------------

POSTGRES_DDL: Sequence[str] = (
    """
    CREATE TABLE IF NOT EXISTS acquisition_events (
        id BIGSERIAL PRIMARY KEY,
        event_id UUID NOT NULL,
        received_at TIMESTAMPTZ NOT NULL,
        rolled_up_at TIMESTAMPTZ,
        schema_version SMALLINT NOT NULL,
        metric_version VARCHAR(32) NOT NULL,
        release_sha VARCHAR(40),
        event VARCHAR(24) NOT NULL,
        session_id UUID NOT NULL,
        landing_id UUID,
        operation_id UUID,
        page_family VARCHAR(16) NOT NULL,
        source_group VARCHAR(16) NOT NULL,
        is_bot BOOLEAN NOT NULL,
        entry_mode VARCHAR(16),
        persistence_mode VARCHAR(16),
        outcome_group VARCHAR(32),
        supported_result BOOLEAN,
        render_delivered BOOLEAN,
        rate_valid BOOLEAN,
        sample_nonzero BOOLEAN,
        scope_visible BOOLEAN,
        result_kind VARCHAR(24),
        match_scope VARCHAR(24),
        reason VARCHAR(16),
        error_category VARCHAR(32),
        stage VARCHAR(24),
        observation_state VARCHAR(24),
        CONSTRAINT uq_acquisition_events_event_id UNIQUE (event_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_acquisition_events_received_at "
    "ON acquisition_events (received_at)",
    "CREATE INDEX IF NOT EXISTS idx_acquisition_events_unrolled "
    "ON acquisition_events (received_at) WHERE rolled_up_at IS NULL",
    "CREATE INDEX IF NOT EXISTS idx_acquisition_events_landing "
    "ON acquisition_events (landing_id) WHERE landing_id IS NOT NULL",
    """
    CREATE TABLE IF NOT EXISTS acquisition_daily (
        day DATE NOT NULL,
        page_family VARCHAR(16) NOT NULL,
        source_group VARCHAR(16) NOT NULL,
        event VARCHAR(24) NOT NULL,
        outcome_group VARCHAR(32) NOT NULL DEFAULT 'none',
        entry_mode VARCHAR(16) NOT NULL DEFAULT 'none',
        persistence_mode VARCHAR(16) NOT NULL DEFAULT 'none',
        is_bot BOOLEAN NOT NULL,
        count BIGINT NOT NULL,
        PRIMARY KEY (day, page_family, source_group, event, outcome_group,
                     entry_mode, persistence_mode, is_bot)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS acquisition_landing_daily (
        day DATE NOT NULL,
        source_group VARCHAR(16) NOT NULL,
        page_family VARCHAR(16) NOT NULL,
        landings BIGINT NOT NULL,
        landings_with_supported_result BIGINT NOT NULL,
        PRIMARY KEY (day, source_group, page_family)
    )
    """,
)

POSTGRES_ROLLBACK_DDL: Sequence[str] = (
    "DROP TABLE IF EXISTS acquisition_landing_daily",
    "DROP TABLE IF EXISTS acquisition_daily",
    "DROP TABLE IF EXISTS acquisition_events",
)

SQLITE_DDL: Sequence[str] = (
    """
    CREATE TABLE IF NOT EXISTS acquisition_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        received_at TEXT NOT NULL,
        rolled_up_at TEXT,
        schema_version INTEGER NOT NULL,
        metric_version TEXT NOT NULL,
        release_sha TEXT,
        event TEXT NOT NULL,
        session_id TEXT NOT NULL,
        landing_id TEXT,
        operation_id TEXT,
        page_family TEXT NOT NULL,
        source_group TEXT NOT NULL,
        is_bot INTEGER NOT NULL,
        entry_mode TEXT,
        persistence_mode TEXT,
        outcome_group TEXT,
        supported_result INTEGER,
        render_delivered INTEGER,
        rate_valid INTEGER,
        sample_nonzero INTEGER,
        scope_visible INTEGER,
        result_kind TEXT,
        match_scope TEXT,
        reason TEXT,
        error_category TEXT,
        stage TEXT,
        observation_state TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_acquisition_events_received_at "
    "ON acquisition_events (received_at)",
    "CREATE INDEX IF NOT EXISTS idx_acquisition_events_landing "
    "ON acquisition_events (landing_id)",
    """
    CREATE TABLE IF NOT EXISTS acquisition_daily (
        day TEXT NOT NULL,
        page_family TEXT NOT NULL,
        source_group TEXT NOT NULL,
        event TEXT NOT NULL,
        outcome_group TEXT NOT NULL DEFAULT 'none',
        entry_mode TEXT NOT NULL DEFAULT 'none',
        persistence_mode TEXT NOT NULL DEFAULT 'none',
        is_bot INTEGER NOT NULL,
        count INTEGER NOT NULL,
        PRIMARY KEY (day, page_family, source_group, event, outcome_group,
                     entry_mode, persistence_mode, is_bot)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS acquisition_landing_daily (
        day TEXT NOT NULL,
        source_group TEXT NOT NULL,
        page_family TEXT NOT NULL,
        landings INTEGER NOT NULL,
        landings_with_supported_result INTEGER NOT NULL,
        PRIMARY KEY (day, source_group, page_family)
    )
    """,
)

# Completion rule shared by the raw metric and the landing-level rollup: a
# supported result_rendered carrying the landing_id, unless render_failed exists
# for the same operation (render_failed wins).


# ---------------------------------------------------------------------------
# Primary-metric query (portable SQL: identical text runs on Postgres and SQLite)
# ---------------------------------------------------------------------------

def primary_metric_sql(style: str) -> str:
    """Eligible organic landings (denominator) and those with a displayed
    supported result sharing the landing_id (numerator).

    ``style`` is ``"pg"`` ($1..$n placeholders) or ``"sqlite"`` (?).
    Parameters: from_ts (inclusive), to_ts (exclusive).

    * Landing: ``landing_observed``, ``source_group = 'google_organic'``
      (so ``paid_search``, which wins over the referrer on the client, is in
      neither numerator nor denominator: D-006), not a bot, public page
      family. One row per landing_id.
    * Completion: ``result_rendered`` with ``supported_result`` true carrying
      that landing_id, excluding any operation that also has a
      ``render_failed`` (render_failed wins, EVENT_SCHEMA_v1.md).
    * The numerator counts a landing once however many results it rendered.
    """
    p1, p2 = ("$1", "$2") if style == "pg" else ("?", "?")
    families = ", ".join("'%s'" % f for f in PUBLIC_PAGE_FAMILIES)
    true_ = "TRUE" if style == "pg" else "1"
    false_ = "FALSE" if style == "pg" else "0"
    return f"""
    WITH landings AS (
        SELECT landing_id
        FROM acquisition_events
        WHERE event = 'landing_observed'
          AND source_group = 'google_organic'
          AND is_bot = {false_}
          AND page_family IN ({families})
          AND landing_id IS NOT NULL
          AND received_at >= {p1} AND received_at < {p2}
        GROUP BY landing_id
    ),
    completions AS (
        SELECT DISTINCT r.landing_id
        FROM acquisition_events r
        WHERE r.event = 'result_rendered'
          AND r.supported_result = {true_}
          AND r.is_bot = {false_}
          AND r.landing_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM acquisition_events f
              WHERE f.event = 'render_failed'
                AND r.operation_id IS NOT NULL
                AND f.operation_id = r.operation_id
          )
    )
    SELECT COUNT(*) AS denominator,
           COUNT(c.landing_id) AS numerator
    FROM landings l
    LEFT JOIN completions c ON c.landing_id = l.landing_id
    """


def landing_aggregate_sql(style: str) -> str:
    """The primary metric from ``acquisition_landing_daily`` (counts only):
    Google-organic landings on public page families (denominator) and those
    with at least one supported displayed result (numerator), for UTC days
    ``from_day <= day < to_day``. ``paid_search`` and every other source group
    are excluded by the same ``source_group`` filter as the raw query.
    Parameters: from_day, to_day."""
    p1, p2 = ("$1", "$2") if style == "pg" else ("?", "?")
    families = ", ".join("'%s'" % f for f in PUBLIC_PAGE_FAMILIES)
    return f"""
    SELECT COALESCE(SUM(landings), 0) AS denominator,
           COALESCE(SUM(landings_with_supported_result), 0) AS numerator
    FROM acquisition_landing_daily
    WHERE source_group = 'google_organic'
      AND page_family IN ({families})
      AND day >= {p1} AND day < {p2}
    """


# ---------------------------------------------------------------------------
# SQLite backend (local synthetic runs and tests only)
# ---------------------------------------------------------------------------

def _sqlite_value(v: Any) -> Any:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, datetime):
        return _iso(v)
    return v


class SqliteStore:
    """File-backed SQLite store. One short-lived connection per call; a
    retention run holds ``BEGIN IMMEDIATE`` (the single-writer lock) for its
    whole transaction, which is the SQLite analogue of the Postgres advisory
    lock."""

    backend = "sqlite"

    def __init__(self, path: str):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    # -- schema ------------------------------------------------------------
    async def ensure_schema(self) -> None:
        await asyncio.to_thread(self._ensure_schema)

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            for stmt in SQLITE_DDL:
                conn.execute(stmt)
        finally:
            conn.close()

    async def tables_exist(self) -> bool:
        return await asyncio.to_thread(self._tables_exist)

    def _tables_exist(self) -> bool:
        if not os.path.exists(self.path):
            return False
        conn = self._connect()
        try:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        return {"acquisition_events", "acquisition_daily", "acquisition_landing_daily"} <= names

    # -- insert ------------------------------------------------------------
    async def insert_event(self, rec: AcquisitionRecord) -> bool:
        """True if a new row was written, False if event_id already existed."""
        return await asyncio.to_thread(self._insert_event, rec)

    def _insert_event(self, rec: AcquisitionRecord) -> bool:
        conn = self._connect()
        try:
            cols = ", ".join(COLUMNS)
            marks = ", ".join("?" for _ in COLUMNS)
            cur = conn.execute(
                f"INSERT OR IGNORE INTO acquisition_events ({cols}) VALUES ({marks})",
                [_sqlite_value(getattr(rec, c)) for c in COLUMNS],
            )
            return cur.rowcount == 1
        finally:
            conn.close()

    # -- retention ---------------------------------------------------------
    async def run_retention(self, now: datetime) -> RetentionResult:
        return await asyncio.to_thread(self._run_retention, now)

    def _run_retention(self, now: datetime) -> RetentionResult:
        now = _as_utc(now)
        result = RetentionResult()
        rollup_cutoff = _iso(now - ROLLUP_AFTER)
        raw_cutoff = _iso(now - RAW_RETENTION)
        agg_cutoff_day = months_before(now.date(), AGGREGATE_RETENTION_MONTHS)
        result.raw_cutoff = raw_cutoff
        result.aggregate_cutoff_day = agg_cutoff_day.isoformat()

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                key_cols = ", ".join(
                    f"COALESCE({c}, '{NONE_KEY}')" if c in ("outcome_group", "entry_mode", "persistence_mode") else c
                    for c in AGGREGATE_KEY
                )
                groups = conn.execute(
                    f"SELECT substr(received_at, 1, 10) AS day, {key_cols}, COUNT(*) AS n "
                    "FROM acquisition_events "
                    "WHERE rolled_up_at IS NULL AND received_at < ? "
                    "GROUP BY 1, 2, 3, 4, 5, 6, 7, 8",
                    (rollup_cutoff,),
                ).fetchall()
                for g in groups:
                    conn.execute(
                        "INSERT INTO acquisition_daily "
                        "(day, page_family, source_group, event, outcome_group, entry_mode, "
                        " persistence_mode, is_bot, count) VALUES (?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT (day, page_family, source_group, event, outcome_group, "
                        "entry_mode, persistence_mode, is_bot) DO UPDATE SET count = count + excluded.count",
                        tuple(g),
                    )
                # Landing-level counts, from the SAME rows the UPDATE below marks
                # (computed first, inside the same transaction). A landing_id
                # already counted by an earlier run is skipped.
                landings = {}
                for r in conn.execute(
                    "SELECT landing_id, substr(received_at, 1, 10) AS day, source_group, page_family "
                    "FROM acquisition_events a "
                    "WHERE rolled_up_at IS NULL AND received_at < ? AND event = 'landing_observed' "
                    "AND is_bot = 0 AND landing_id IS NOT NULL "
                    "AND NOT EXISTS (SELECT 1 FROM acquisition_events p WHERE p.event = 'landing_observed' "
                    "  AND p.landing_id = a.landing_id AND p.rolled_up_at IS NOT NULL) "
                    "ORDER BY received_at, id",
                    (rollup_cutoff,),
                ).fetchall():
                    landings.setdefault(r["landing_id"], (r["day"], r["source_group"], r["page_family"]))
                tally = {}
                for landing_id, (day, source_group, page_family) in landings.items():
                    done = conn.execute(
                        "SELECT 1 FROM acquisition_events r WHERE r.event = 'result_rendered' "
                        "AND r.supported_result = 1 AND r.is_bot = 0 AND r.landing_id = ? "
                        "AND NOT EXISTS (SELECT 1 FROM acquisition_events f WHERE f.event = 'render_failed' "
                        "  AND r.operation_id IS NOT NULL AND f.operation_id = r.operation_id) LIMIT 1",
                        (landing_id,),
                    ).fetchone()
                    t = tally.setdefault((day, source_group, page_family), [0, 0])
                    t[0] += 1
                    t[1] += 1 if done else 0
                for (day, source_group, page_family), (n, d) in tally.items():
                    conn.execute(
                        "INSERT INTO acquisition_landing_daily "
                        "(day, source_group, page_family, landings, landings_with_supported_result) "
                        "VALUES (?,?,?,?,?) ON CONFLICT (day, source_group, page_family) DO UPDATE SET "
                        "landings = landings + excluded.landings, "
                        "landings_with_supported_result = landings_with_supported_result "
                        "+ excluded.landings_with_supported_result",
                        (day, source_group, page_family, n, d),
                    )
                result.rolled_up_landings = len(landings)
                cur = conn.execute(
                    "UPDATE acquisition_events SET rolled_up_at = ? "
                    "WHERE rolled_up_at IS NULL AND received_at < ?",
                    (_iso(now), rollup_cutoff),
                )
                result.rolled_up_events = cur.rowcount
                # Anything past 90 days is already rolled up (the rollup cutoff is earlier).
                cur = conn.execute(
                    "DELETE FROM acquisition_events WHERE received_at < ? AND rolled_up_at IS NOT NULL",
                    (raw_cutoff,),
                )
                result.deleted_raw_events = cur.rowcount
                cur = conn.execute(
                    "DELETE FROM acquisition_daily WHERE day < ?", (agg_cutoff_day.isoformat(),)
                )
                result.deleted_aggregate_rows = cur.rowcount
                cur = conn.execute(
                    "DELETE FROM acquisition_landing_daily WHERE day < ?", (agg_cutoff_day.isoformat(),)
                )
                result.deleted_aggregate_rows += cur.rowcount
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
        return result

    # -- read helpers ------------------------------------------------------
    async def primary_metric(self, from_ts: datetime, to_ts: datetime) -> Dict[str, int]:
        return await asyncio.to_thread(self._primary_metric, from_ts, to_ts)

    def _primary_metric(self, from_ts: datetime, to_ts: datetime) -> Dict[str, int]:
        conn = self._connect()
        try:
            row = conn.execute(primary_metric_sql("sqlite"), (_iso(from_ts), _iso(to_ts))).fetchone()
            return {"denominator": row["denominator"], "numerator": row["numerator"]}
        finally:
            conn.close()

    async def landing_aggregate_metric(self, from_day: date, to_day: date) -> Dict[str, int]:
        sql = landing_aggregate_sql("sqlite")
        rows = await self.fetch_all(sql, (from_day.isoformat(), to_day.isoformat()))
        return {"denominator": int(rows[0]["denominator"]), "numerator": int(rows[0]["numerator"])}

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        def _run() -> List[Dict[str, Any]]:
            conn = self._connect()
            try:
                return [dict(r) for r in conn.execute(sql, params).fetchall()]
            finally:
                conn.close()

        return await asyncio.to_thread(_run)


# ---------------------------------------------------------------------------
# PostgreSQL backend (production)
# ---------------------------------------------------------------------------

class PostgresStore:
    backend = "postgres"

    def __init__(self, get_pool):
        self._get_pool = get_pool

    async def _pool(self):
        pool = await self._get_pool()
        if pool is None:
            raise RuntimeError("postgres pool unavailable")
        return pool

    async def ensure_schema(self) -> None:
        pool = await self._pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Two workers (or a worker and the migration script) running
                # CREATE TABLE IF NOT EXISTS at once can still collide inside
                # Postgres; serialise the DDL on an advisory lock.
                await conn.execute("SELECT pg_advisory_xact_lock($1)", _SCHEMA_LOCK_KEY)
                for stmt in POSTGRES_DDL:
                    await conn.execute(stmt)

    async def tables_exist(self) -> bool:
        pool = await self._pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT to_regclass('acquisition_events') IS NOT NULL AS a, "
                "to_regclass('acquisition_daily') IS NOT NULL AS b, "
                "to_regclass('acquisition_landing_daily') IS NOT NULL AS c"
            )
        return bool(row["a"] and row["b"] and row["c"])

    async def insert_event(self, rec: AcquisitionRecord) -> bool:
        pool = await self._pool()
        cols = ", ".join(COLUMNS)
        marks = ", ".join(f"${i + 1}" for i in range(len(COLUMNS)))
        sql = (
            f"INSERT INTO acquisition_events ({cols}) VALUES ({marks}) "
            "ON CONFLICT (event_id) DO NOTHING"
        )
        args = []
        for c in COLUMNS:
            v = getattr(rec, c)
            if c in ("event_id", "session_id", "landing_id", "operation_id") and v is not None:
                v = uuid.UUID(v)
            args.append(v)
        async with pool.acquire() as conn:
            status = await conn.execute(sql, *args)
        # asyncpg returns the command tag, e.g. "INSERT 0 1" or "INSERT 0 0".
        return status.endswith(" 1")

    async def run_retention(self, now: datetime) -> RetentionResult:
        now = _as_utc(now)
        result = RetentionResult()
        rollup_cutoff = now - ROLLUP_AFTER
        raw_cutoff = now - RAW_RETENTION
        agg_cutoff_day = months_before(now.date(), AGGREGATE_RETENTION_MONTHS)
        result.raw_cutoff = raw_cutoff.isoformat()
        result.aggregate_cutoff_day = agg_cutoff_day.isoformat()

        pool = await self._pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Transaction-scoped advisory lock: with `uvicorn --workers 2`
                # both workers run this at startup; the second skips. The lock
                # releases automatically at commit/rollback.
                got = await conn.fetchval("SELECT pg_try_advisory_xact_lock($1)", _RETENTION_LOCK_KEY)
                if not got:
                    result.skipped_locked = True
                    return result
                # Mark + aggregate in ONE statement: the rows that are marked
                # are exactly the rows that are counted, so a rerun cannot
                # double count.
                rolled_row = await conn.fetchrow(
                    """
                    WITH moved AS (
                        UPDATE acquisition_events
                           SET rolled_up_at = $1
                         WHERE rolled_up_at IS NULL AND received_at < $2
                     RETURNING (received_at AT TIME ZONE 'UTC')::date AS day,
                               page_family, source_group, event,
                               COALESCE(outcome_group, 'none') AS outcome_group,
                               COALESCE(entry_mode, 'none') AS entry_mode,
                               COALESCE(persistence_mode, 'none') AS persistence_mode,
                               is_bot, landing_id
                    ),
                    agg AS (
                        SELECT day, page_family, source_group, event, outcome_group,
                               entry_mode, persistence_mode, is_bot, COUNT(*) AS n
                          FROM moved
                         GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
                    ),
                    ins AS (
                        INSERT INTO acquisition_daily
                            (day, page_family, source_group, event, outcome_group,
                             entry_mode, persistence_mode, is_bot, count)
                        SELECT day, page_family, source_group, event, outcome_group,
                               entry_mode, persistence_mode, is_bot, n FROM agg
                        ON CONFLICT (day, page_family, source_group, event, outcome_group,
                                     entry_mode, persistence_mode, is_bot)
                        DO UPDATE SET count = acquisition_daily.count + EXCLUDED.count
                        RETURNING 1
                    ),
                    land AS (
                        SELECT m.landing_id, MIN(m.day) AS day, MIN(m.source_group) AS source_group,
                               MIN(m.page_family) AS page_family
                          FROM moved m
                         WHERE m.event = 'landing_observed' AND NOT m.is_bot AND m.landing_id IS NOT NULL
                           AND NOT EXISTS (SELECT 1 FROM acquisition_events p
                                            WHERE p.event = 'landing_observed'
                                              AND p.landing_id = m.landing_id
                                              AND p.rolled_up_at IS NOT NULL)
                         GROUP BY m.landing_id
                    ),
                    land_done AS (
                        SELECT l.day, l.source_group, l.page_family,
                               EXISTS (
                                   SELECT 1 FROM acquisition_events r
                                    WHERE r.event = 'result_rendered' AND r.supported_result
                                      AND NOT r.is_bot AND r.landing_id = l.landing_id
                                      AND NOT EXISTS (SELECT 1 FROM acquisition_events f
                                                       WHERE f.event = 'render_failed'
                                                         AND r.operation_id IS NOT NULL
                                                         AND f.operation_id = r.operation_id)
                               ) AS done
                          FROM land l
                    ),
                    land_agg AS (
                        SELECT day, source_group, page_family, COUNT(*) AS n,
                               COUNT(*) FILTER (WHERE done) AS d
                          FROM land_done GROUP BY 1, 2, 3
                    ),
                    ins2 AS (
                        INSERT INTO acquisition_landing_daily
                            (day, source_group, page_family, landings, landings_with_supported_result)
                        SELECT day, source_group, page_family, n, d FROM land_agg
                        ON CONFLICT (day, source_group, page_family)
                        DO UPDATE SET
                            landings = acquisition_landing_daily.landings + EXCLUDED.landings,
                            landings_with_supported_result =
                                acquisition_landing_daily.landings_with_supported_result
                                + EXCLUDED.landings_with_supported_result
                        RETURNING 1
                    )
                    SELECT COALESCE((SELECT SUM(n) FROM agg), 0)::bigint,
                           COALESCE((SELECT SUM(n) FROM land_agg), 0)::bigint
                    """,
                    now,
                    rollup_cutoff,
                )
                result.rolled_up_events = int(rolled_row[0] or 0)
                result.rolled_up_landings = int(rolled_row[1] or 0)
                tag = await conn.execute(
                    "DELETE FROM acquisition_events WHERE received_at < $1 AND rolled_up_at IS NOT NULL",
                    raw_cutoff,
                )
                result.deleted_raw_events = int(tag.split()[-1])
                tag = await conn.execute(
                    "DELETE FROM acquisition_daily WHERE day < $1", agg_cutoff_day
                )
                result.deleted_aggregate_rows = int(tag.split()[-1])
                tag = await conn.execute(
                    "DELETE FROM acquisition_landing_daily WHERE day < $1", agg_cutoff_day
                )
                result.deleted_aggregate_rows += int(tag.split()[-1])
        return result

    async def primary_metric(self, from_ts: datetime, to_ts: datetime) -> Dict[str, int]:
        pool = await self._pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(primary_metric_sql("pg"), _as_utc(from_ts), _as_utc(to_ts))
        return {"denominator": int(row["denominator"]), "numerator": int(row["numerator"])}

    async def landing_aggregate_metric(self, from_day: date, to_day: date) -> Dict[str, int]:
        pool = await self._pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(landing_aggregate_sql("pg"), from_day, to_day)
        return {"denominator": int(row["denominator"]), "numerator": int(row["numerator"])}

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        pool = await self._pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

def select_store():
    """SQLite only when explicitly asked (local/test); else Postgres when
    DATABASE_URL is configured; else None (collector cannot record)."""
    path = os.environ.get("ACQUISITION_SQLITE_PATH")
    if path:
        return SqliteStore(path)
    if os.environ.get("DATABASE_URL"):
        import database as db

        return PostgresStore(db.get_pool)
    return None
