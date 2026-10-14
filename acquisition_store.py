"""Bounded first-party service measurement. See docs/acquisition/COLLECTOR.md.

The v2 tables are separate from the unreleased v1 design. A journey has one
fixed 30-minute receipt window. Inserts and finalisation share a database
lock. Finalisation adds counts and deletes all closed individual events in
one transaction. The same reducer defines raw and retained measurements.
No customer/report identifiers, URL values or network metadata belong here.
"""
from __future__ import annotations
import asyncio
import os
import sqlite3
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, fields, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

METRIC_VERSION = "oa-journey-30m-v2"
PAID_METRIC_VERSION = "paid-journey-30m-v1"
PAID_GROUPS = ("discover_owner", "discover_buyer", "search_owner", "search_buyer")
WINDOW_MINUTES = 30
RAW_RETENTION = ROLLUP_AFTER = timedelta(minutes=WINDOW_MINUTES)
AGGREGATE_RETENTION_MONTHS = 3
REPORTING_TIMEZONE = "UTC"
PUBLIC_PAGE_FAMILIES = ("home", "guide", "make", "model", "comparison", "pillar", "problem_hub", "other_public")
PILOT_GROUPS = ("none", "cost", "checklist", "corsa", "c3", "clio208", "polofiesta", "yarisjazz")
_RETENTION_LOCK_KEY = 7_052_205
_SCHEMA_LOCK_KEY = 7_052_206
EVENTS = "acquisition_v2_events"
DAILY = "acquisition_v2_daily"
LANDINGS = "acquisition_v2_landing_daily"


def utcnow():
    return datetime.now(timezone.utc)


def _as_utc(value):
    if value.tzinfo is None:
        raise ValueError("naive datetime not allowed")
    return value.astimezone(timezone.utc)


def _iso(value):
    return _as_utc(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def minute(value):
    return int(_as_utc(value).timestamp() // 60)


def months_before(day, months):
    index = day.year * 12 + day.month - 1 - months
    year, month0 = divmod(index, 12)
    for d in (day.day, 30, 29, 28):
        try:
            return date(year, month0 + 1, d)
        except ValueError:
            pass


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
    window_start_minute: int
    pilot_group: str
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


COLUMNS = tuple(f.name for f in fields(AcquisitionRecord))
LANDING_KEY = ("day", "metric_version", "source_group", "pilot_group", "page_family")
EVENT_KEY = LANDING_KEY + ("event", "outcome_group", "entry_mode", "persistence_mode", "is_bot")


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


def ddl(pg):
    types = {"received_at": "TIMESTAMPTZ" if pg else "TEXT", "window_start_minute": "BIGINT", "schema_version": "SMALLINT"}
    for c in ("is_bot", "supported_result", "render_delivered", "rate_valid", "sample_nonzero", "scope_visible"):
        types[c] = "BOOLEAN" if pg else "INTEGER"
    for c in ("event_id", "session_id", "landing_id", "operation_id"):
        types[c] = "UUID" if pg else "TEXT"
    required = {"event_id", "received_at", "schema_version", "metric_version", "event", "session_id", "landing_id", "page_family", "source_group", "is_bot", "window_start_minute", "pilot_group"}
    columns = ", ".join(c + " " + types.get(c, "TEXT") + (" PRIMARY KEY" if c == "event_id" else " NOT NULL" if c in required else "") for c in COLUMNS)
    statements = [f"CREATE TABLE IF NOT EXISTS {EVENTS} ({columns})",
                  f"CREATE INDEX IF NOT EXISTS idx_acq_v2_window ON {EVENTS} (window_start_minute)",
                  f"CREATE INDEX IF NOT EXISTS idx_acq_v2_landing ON {EVENTS} (landing_id)"]
    for table, keys, measures in ((DAILY, EVENT_KEY, ("count",)), (LANDINGS, LANDING_KEY, ("landings", "landings_with_supported_result"))):
        cols = [f"{k} {'BOOLEAN' if pg else 'INTEGER'} NOT NULL" if k == "is_bot" else f"{k} TEXT NOT NULL" for k in keys]
        cols += [f"{m} BIGINT NOT NULL" for m in measures]
        statements.append(f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(cols)}, PRIMARY KEY ({', '.join(keys)}))")
    return tuple(statements)


POSTGRES_DDL, SQLITE_DDL = ddl(True), ddl(False)
POSTGRES_ROLLBACK_DDL = tuple(f"DROP TABLE IF EXISTS {t}" for t in (LANDINGS, DAILY, EVENTS))


def admitted(rec, now):
    # Checked again INSIDE the write lock. An old retry cannot recreate an
    # already-finalised journey after its IDs have been deleted.
    start = rec.window_start_minute
    organic = (rec.metric_version == METRIC_VERSION and rec.pilot_group in PILOT_GROUPS
               and rec.source_group != "paid_search")
    paid = (rec.metric_version == PAID_METRIC_VERSION and rec.pilot_group in PAID_GROUPS
            and rec.source_group == "paid_search")
    return ((organic or paid) and rec.schema_version == 2 and rec.landing_id is not None
            and start <= minute(now) < start + WINDOW_MINUTES)



def summarise(rows):
    """One reducer for raw inspection AND rollup, including failure precedence.

    Each tuple (version, landing_id, window start) is one journey. The route
    and store prevent source/pilot drift. A completion requires a fresh
    check_started and a supported, delivered render for the same operation;
    any render_failed in that window cancels that operation. Another genuine
    successful operation can still complete the journey. Restores never count.
    """
    events = Counter()
    # Page-view economics require a received, non-bot observed paid arrival.
    # Missing arrival receipts are unknown, not attributed page views.
    paid_arrivals = {(str(r["landing_id"]), r["window_start_minute"]) for r in rows
                     if r["metric_version"] == PAID_METRIC_VERSION
                     and r["event"] == "landing_observed" and not r["is_bot"]
                     and r["observation_state"] == "observed"}
    journeys = defaultdict(list)
    for r in rows:
        if (r["metric_version"] == PAID_METRIC_VERSION and r["event"] == "page_viewed"
                and (str(r["landing_id"]), r["window_start_minute"]) not in paid_arrivals):
            continue
        day = datetime.fromtimestamp(r["window_start_minute"] * 60, timezone.utc).date().isoformat()
        key = (day, r["metric_version"], r["source_group"], r["pilot_group"], r["page_family"])
        events[key + (r["event"], r["outcome_group"] or "none", r["entry_mode"] or "none", r["persistence_mode"] or "none", bool(r["is_bot"]))] += 1
        journeys[(r["metric_version"], str(r["landing_id"]), r["window_start_minute"])].append(r)
    landings = defaultdict(lambda: [0, 0])
    for group in journeys.values():
        arrivals = [r for r in group if r["event"] == "landing_observed" and not r["is_bot"] and r["observation_state"] == "observed"]
        if not arrivals:
            continue
        l = arrivals[0]
        day = datetime.fromtimestamp(l["window_start_minute"] * 60, timezone.utc).date().isoformat()
        key = (day, l["metric_version"], l["source_group"], l["pilot_group"], l["page_family"])
        started = {str(r["operation_id"]) for r in group if r["event"] == "check_started" and r["operation_id"] and r["entry_mode"] == "fresh_check" and not r["is_bot"]}
        failed = {str(r["operation_id"]) for r in group if r["event"] == "render_failed" and r["operation_id"] and not r["is_bot"]}
        done = any(r["event"] == "result_rendered" and r["entry_mode"] == "fresh_check"
                   and r["supported_result"] and r["render_delivered"] and not r["is_bot"]
                   and str(r["operation_id"]) in started - failed for r in group)
        landings[key][0] += 1
        landings[key][1] += int(done)
    return events, landings


def metric(tally, from_day, to_day, pilot_group=None, metric_version=METRIC_VERSION):
    den = num = 0
    for (day, version, source, pilot, family), pair in tally.items():
        if (str(from_day) <= day < str(to_day) and version == metric_version and source == "google_organic"
                and family in PUBLIC_PAGE_FAMILIES and (pilot_group is None or pilot == pilot_group)):
            den += pair[0]
            num += pair[1]
    return {"denominator": den, "numerator": num}


def upsert_sql(table, keys, measures, pg):
    cols = keys + measures
    marks = ",".join(f"${i+1}" if pg else "?" for i in range(len(cols)))
    assignments = ", ".join(f"{m} = {table}.{m} + excluded.{m}" for m in measures)
    return f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({marks}) ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {assignments}"


def values(rec, pg):
    out = []
    for c in COLUMNS:
        v = getattr(rec, c)
        if pg and c in ("event_id", "session_id", "landing_id", "operation_id") and v is not None:
            v = uuid.UUID(v)
        if not pg and isinstance(v, datetime):
            v = _iso(v)
        out.append(v)
    return out


def compatible(existing, rec):
    return not existing or all(existing[c] == getattr(rec, c) for c in ("metric_version", "window_start_minute", "source_group", "pilot_group"))


class _ReadMethods:
    async def raw_metric(self, from_ts, to_ts, pilot_group=None):
        rows = await self.fetch_all(f"SELECT * FROM {EVENTS}")
        return metric(summarise(rows)[1], _as_utc(from_ts).date(), _as_utc(to_ts).date(), pilot_group)

    async def landing_aggregate_metric(self, from_day, to_day, pilot_group=None):
        rows = await self.fetch_all(f"SELECT * FROM {LANDINGS}")
        tally = {tuple(r[k] for k in LANDING_KEY): (r["landings"], r["landings_with_supported_result"]) for r in rows}
        return metric(tally, from_day, to_day, pilot_group)

    async def primary_metric(self, from_ts, to_ts, pilot_group=None):
        # Canonical completed-day reports use retained counts. Open windows
        # are deliberately absent; raw_metric is explicitly provisional.
        return await self.landing_aggregate_metric(_as_utc(from_ts).date(), _as_utc(to_ts).date(), pilot_group)


class RetentionOverdue(RuntimeError):
    """Fixed error category; never include row data in this exception."""


class SqliteStore(_ReadMethods):
    backend = "sqlite"
    def __init__(self, path, clock=None):
        self.path, self.clock = path, clock or (lambda: utcnow())

    def _connect(self):
        c = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    async def ensure_schema(self):
        def work():
            c = self._connect()
            try:
                for stmt in SQLITE_DDL:
                    c.execute(stmt)
            finally:
                c.close()
        await asyncio.to_thread(work)

    async def tables_exist(self):
        if not os.path.exists(self.path):
            return False
        rows = await self.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")
        return {EVENTS, DAILY, LANDINGS} <= {r["name"] for r in rows}

    async def insert_event(self, rec):
        def work():
            c = self._connect()
            try:
                c.execute("BEGIN IMMEDIATE")
                now = self.clock()
                oldest = c.execute(f"SELECT MIN(window_start_minute) FROM {EVENTS}").fetchone()[0]
                if oldest is not None and oldest < minute(now) - 35:
                    c.execute("ROLLBACK")
                    raise RetentionOverdue()
                if not admitted(rec, now):
                    c.execute("ROLLBACK")
                    return False
                old = c.execute(f"SELECT * FROM {EVENTS} WHERE landing_id = ? LIMIT 1", (rec.landing_id,)).fetchone()
                if not compatible(old, rec):
                    c.execute("ROLLBACK")
                    return False
                marks = ','.join('?' for _ in COLUMNS)
                n = c.execute(f"INSERT OR IGNORE INTO {EVENTS} ({', '.join(COLUMNS)}) VALUES ({marks})", values(rec, False)).rowcount
                c.execute("COMMIT")
                return n == 1
            finally:
                c.close()
        return await asyncio.to_thread(work)

    async def run_retention(self, now):
        cutoff = minute(now) - WINDOW_MINUTES
        agg_cutoff = months_before(_as_utc(now).date(), AGGREGATE_RETENTION_MONTHS).isoformat()
        def work():
            c = self._connect()
            result = RetentionResult(raw_cutoff=str(cutoff), aggregate_cutoff_day=agg_cutoff)
            try:
                c.execute("BEGIN IMMEDIATE")
                rows = [dict(r) for r in c.execute(f"SELECT * FROM {EVENTS} WHERE window_start_minute <= ?", (cutoff,))]
                events, landings = summarise(rows)
                for key, count in events.items():
                    c.execute(upsert_sql(DAILY, EVENT_KEY, ("count",), False), key + (count,))
                for key, pair in landings.items():
                    c.execute(upsert_sql(LANDINGS, LANDING_KEY, ("landings", "landings_with_supported_result"), False), key + tuple(pair))
                result.rolled_up_events = len(rows)
                result.rolled_up_landings = sum(p[0] for p in landings.values())
                result.deleted_raw_events = c.execute(f"DELETE FROM {EVENTS} WHERE window_start_minute <= ?", (cutoff,)).rowcount
                for t in (DAILY, LANDINGS):
                    result.deleted_aggregate_rows += c.execute(f"DELETE FROM {t} WHERE day < ?", (agg_cutoff,)).rowcount
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
            finally:
                c.close()
            return result
        return await asyncio.to_thread(work)

    async def fetch_all(self, sql, params=()):
        def work():
            c = self._connect()
            try:
                return [dict(r) for r in c.execute(sql, params)]
            finally:
                c.close()
        return await asyncio.to_thread(work)


class PostgresStore(_ReadMethods):
    backend = "postgres"
    def __init__(self, get_pool, clock=None):
        self._get_pool, self.clock = get_pool, clock or (lambda: utcnow())

    async def _pool(self):
        pool = await self._get_pool()
        if pool is None:
            raise RuntimeError("postgres pool unavailable")
        return pool

    async def ensure_schema(self):
        async with (await self._pool()).acquire() as c:
            async with c.transaction():
                await c.execute("SELECT pg_advisory_xact_lock($1)", _SCHEMA_LOCK_KEY)
                for stmt in POSTGRES_DDL:
                    await c.execute(stmt)

    async def tables_exist(self):
        async with (await self._pool()).acquire() as c:
            return all([await c.fetchval("SELECT to_regclass($1) IS NOT NULL", t) for t in (EVENTS, DAILY, LANDINGS)])

    async def insert_event(self, rec):
        async with (await self._pool()).acquire() as c:
            async with c.transaction():
                # Exclusive also serialises first events for a journey, so
                # parallel conflicting envelopes cannot both be admitted.
                await c.execute("SELECT pg_advisory_xact_lock($1)", _RETENTION_LOCK_KEY)
                now = self.clock()
                oldest = await c.fetchval(f"SELECT MIN(window_start_minute) FROM {EVENTS}")
                if oldest is not None and oldest < minute(now) - 35:
                    raise RetentionOverdue()
                if not admitted(rec, now):
                    return False
                old = await c.fetchrow(f"SELECT * FROM {EVENTS} WHERE landing_id = $1 LIMIT 1", uuid.UUID(rec.landing_id))
                if not compatible(old, rec):
                    return False
                marks = ','.join(f'${i+1}' for i in range(len(COLUMNS)))
                tag = await c.execute(f"INSERT INTO {EVENTS} ({', '.join(COLUMNS)}) VALUES ({marks}) ON CONFLICT (event_id) DO NOTHING", *values(rec, True))
                return tag.endswith(" 1")

    async def run_retention(self, now):
        cutoff = minute(now) - WINDOW_MINUTES
        agg_cutoff = months_before(_as_utc(now).date(), AGGREGATE_RETENTION_MONTHS).isoformat()
        result = RetentionResult(raw_cutoff=str(cutoff), aggregate_cutoff_day=agg_cutoff)
        async with (await self._pool()).acquire() as c:
            async with c.transaction():
                if not await c.fetchval("SELECT pg_try_advisory_xact_lock($1)", _RETENTION_LOCK_KEY):
                    result.skipped_locked = True
                    return result
                rows = [dict(r) for r in await c.fetch(f"SELECT * FROM {EVENTS} WHERE window_start_minute <= $1", cutoff)]
                events, landings = summarise(rows)
                for key, count in events.items():
                    await c.execute(upsert_sql(DAILY, EVENT_KEY, ("count",), True), *key, count)
                for key, pair in landings.items():
                    await c.execute(upsert_sql(LANDINGS, LANDING_KEY, ("landings", "landings_with_supported_result"), True), *key, *pair)
                result.rolled_up_events = len(rows)
                result.rolled_up_landings = sum(p[0] for p in landings.values())
                tag = await c.execute(f"DELETE FROM {EVENTS} WHERE window_start_minute <= $1", cutoff)
                result.deleted_raw_events = int(tag.split()[-1])
                for t in (DAILY, LANDINGS):
                    tag = await c.execute(f"DELETE FROM {t} WHERE day < $1", agg_cutoff)
                    result.deleted_aggregate_rows += int(tag.split()[-1])
        return result

    async def fetch_all(self, sql, params=()):
        async with (await self._pool()).acquire() as c:
            return [dict(r) for r in await c.fetch(sql, *params)]


def select_store():
    path = os.environ.get("ACQUISITION_SQLITE_PATH")
    if path:
        return SqliteStore(path)
    if os.environ.get("DATABASE_URL"):
        import database
        return PostgresStore(database.get_pool)
    return None
