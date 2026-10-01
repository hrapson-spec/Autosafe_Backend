#!/usr/bin/env python3
"""Synthetic end-to-end check of the first-party acquisition collector (OA-005).

POSTs a scripted set of synthetic journeys to a collector, then (when given
direct database access) runs the retention/aggregation job and the primary
metric query and compares them with the numbers the script expects. It is the
LOCAL part of the OA-005 receipt, and the tool for the staging step of the
D-005 enable gate. It never targets production by default and refuses a
non-local base URL unless --allow-remote is given.

    # local server, local Postgres or SQLite (see docs/acquisition/COLLECTOR.md)
    python scripts/acquisition_synthetic_check.py --base-url http://127.0.0.1:8765 \
        --database-url postgresql://... --organic

Without --organic the journeys use source_group internal_test (excluded from
the KPI), which is what a staging/production synthetic event should use.
With --organic they use google_organic so the metric query has something to
count; do that only against a throwaway local database.
"""
import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from urllib import request as urlrequest
from urllib.error import HTTPError
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import acquisition_store as store_mod  # noqa: E402

BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36 AutoSafeSyntheticCheck"
BOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
METRIC_VERSION = "oa-metric-v1-draft"


def uid() -> str:
    return str(uuid.uuid4())


def post(base_url: str, body: dict, ua: str = BROWSER_UA) -> int:
    req = urlrequest.Request(
        base_url.rstrip("/") + "/api/acquisition/events",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": ua},
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=10) as resp:
            return resp.status
    except HTTPError as exc:
        return exc.code


def build_journeys(source: str):
    """(name, [events], user_agent). Returns also the expected metric."""

    def env(**kw):
        base = {"schema_version": 1, "metric_version": METRIC_VERSION, "event_id": uid(),
                "session_id": uid(), "page_family": "app", "source_group": source}
        base.update(kw)
        return base

    def landing(lid, family="guide", src=None, session=None):
        return env(event="landing_observed", landing_id=lid, page_family=family, observation_state="observed",
                   source_group=src or source, **({"session_id": session} if session else {}))

    def started(op, lid, session):
        return env(event="check_started", operation_id=op, entry_mode="fresh_check", landing_id=lid,
                   session_id=session)

    def created(op, lid, session, kind="vehicle_prediction", scope="model_prediction"):
        return env(event="report_created", operation_id=op, entry_mode="fresh_check", result_kind=kind,
                   match_scope=scope, persistence_mode="saved", landing_id=lid, session_id=session)

    def rendered(op, lid, session, **kw):
        ev = env(event="result_rendered", operation_id=op, entry_mode="fresh_check", persistence_mode="saved",
                 render_delivered=True, supported_result=True, outcome_group="prediction", rate_valid=True,
                 scope_visible=True, result_kind="vehicle_prediction", match_scope="model_prediction",
                 landing_id=lid, session_id=session)
        ev.update(kw)
        return ev

    journeys = []
    # J1 completes: guide landing -> prediction displayed
    l1, s1, o1 = uid(), uid(), uid()
    j1 = [landing(l1, "guide", session=s1), started(o1, l1, s1), created(o1, l1, s1), rendered(o1, l1, s1)]
    journeys.append(("J1 organic guide landing, supported prediction displayed", j1, BROWSER_UA))
    # J2 reference-only: home landing -> dataset reference (not a supported result)
    l2, s2, o2 = uid(), uid(), uid()
    journeys.append(("J2 organic home landing, dataset reference only", [
        landing(l2, "home", session=s2), started(o2, l2, s2),
        created(o2, l2, s2, "comparison", "population_default"),
        rendered(o2, l2, s2, supported_result=False, outcome_group="dataset_reference", result_kind="comparison",
                 match_scope="population_default", sample_nonzero=True),
    ], BROWSER_UA))
    # J3 no attempt
    l3, s3 = uid(), uid()
    journeys.append(("J3 organic model landing, no check attempted", [landing(l3, "model", session=s3)], BROWSER_UA))
    # J4 other search engine, completes (outside the Google-organic KPI)
    l4, s4, o4 = uid(), uid(), uid()
    journeys.append(("J4 other_search guide landing, completes (not Google organic)", [
        landing(l4, "guide", src="other_search" if source == "google_organic" else source, session=s4),
        started(o4, l4, s4), created(o4, l4, s4), rendered(o4, l4, s4)], BROWSER_UA))
    # J5 failed check
    l5, s5, o5 = uid(), uid(), uid()
    journeys.append(("J5 organic make landing, check failed", [
        landing(l5, "make", session=s5), started(o5, l5, s5),
        env(event="check_failed", operation_id=o5, entry_mode="fresh_check", error_category="network_error",
            stage="create_report", landing_id=l5, session_id=s5)], BROWSER_UA))
    # J6 render then render_failed for the same operation: render_failed wins
    l6, s6, o6 = uid(), uid(), uid()
    journeys.append(("J6 organic comparison landing, render then render_failed", [
        landing(l6, "comparison", session=s6), started(o6, l6, s6), created(o6, l6, s6), rendered(o6, l6, s6),
        env(event="render_failed", operation_id=o6, entry_mode="fresh_check", stage="render", landing_id=l6,
            session_id=s6)], BROWSER_UA))
    # J7 bot landing + completion (excluded)
    l7, s7, o7 = uid(), uid(), uid()
    journeys.append(("J7 crawler-like user agent, organic landing", [landing(l7, "guide", session=s7)], BOT_UA))
    # J8 paid_search landing that completes (excluded from the organic metric)
    l8, s8, o8 = uid(), uid(), uid()
    journeys.append(("J8 paid_search guide landing, completes (excluded: not organic)", [
        landing(l8, "guide", src="paid_search", session=s8), started(o8, l8, s8), created(o8, l8, s8),
        rendered(o8, l8, s8)], BROWSER_UA))
    expected = {"denominator": 5, "numerator": 1} if source == "google_organic" else None
    return journeys, expected, j1[0]


async def open_store(args):
    if args.sqlite:
        return store_mod.SqliteStore(args.sqlite), None
    import asyncpg

    pool = await asyncpg.create_pool(args.database_url.replace("postgres://", "postgresql://"), min_size=1, max_size=2)

    async def get_pool():
        return pool

    return store_mod.PostgresStore(get_pool), pool


async def main_async(args) -> int:
    host = urlparse(args.base_url).hostname or ""
    if host not in ("127.0.0.1", "localhost", "::1") and not args.allow_remote:
        raise SystemExit("refusing a non-local base URL without --allow-remote")
    source = "google_organic" if args.organic else "internal_test"
    journeys, expected, dup_event = build_journeys(source)
    receipt = {"base_url": args.base_url, "source_group_used": source, "started_utc": datetime.now(timezone.utc).isoformat(),
               "posts": [], "duplicate_resend": None}
    sent_unique = 0
    for name, events, ua in journeys:
        statuses = [post(args.base_url, ev, ua) for ev in events]
        sent_unique += len(events)
        receipt["posts"].append({"journey": name, "events": [e["event"] for e in events], "statuses": statuses})
    receipt["duplicate_resend"] = post(args.base_url, dup_event)  # same event_id again: must be 202 and add no row
    ok_http = all(s == 202 for p in receipt["posts"] for s in p["statuses"]) and receipt["duplicate_resend"] == 202
    receipt["all_202"] = ok_http
    receipt["unique_events_sent"] = sent_unique

    if args.sqlite or args.database_url:
        store, pool = await open_store(args)
        try:
            now = datetime.now(timezone.utc)
            window = (now - timedelta(hours=1), now + timedelta(hours=1))
            raw = (await store.fetch_all("SELECT COUNT(*) AS n FROM acquisition_events"))[0]["n"]
            receipt["raw_rows"] = int(raw)
            receipt["metric_before_rollup"] = await store.primary_metric(*window)
            r1 = await store.run_retention(now + timedelta(days=2))
            r2 = await store.run_retention(now + timedelta(days=2))
            daily = await store.fetch_all("SELECT COALESCE(SUM(count),0) AS n FROM acquisition_daily")
            by_event = await store.fetch_all(
                "SELECT event, SUM(count) AS n FROM acquisition_daily GROUP BY event ORDER BY event")
            receipt["rollup_first_run"] = {"rolled_up": r1.rolled_up_events, "deleted_raw": r1.deleted_raw_events}
            receipt["rollup_rerun"] = {"rolled_up": r2.rolled_up_events, "deleted_raw": r2.deleted_raw_events}
            receipt["daily_total"] = int(daily[0]["n"])
            receipt["daily_by_event"] = {r["event"]: int(r["n"]) for r in by_event}
            receipt["metric_after_rollup"] = await store.primary_metric(*window)
            receipt["checks"] = {
                "raw_rows_equals_unique_events_sent": receipt["raw_rows"] == sent_unique,
                "rollup_counts_every_event_once": r1.rolled_up_events == sent_unique and receipt["daily_total"] == sent_unique,
                "rerun_adds_nothing": r2.rolled_up_events == 0,
                "metric_matches_expectation": (expected is None) or receipt["metric_before_rollup"] == expected,
            }
            receipt["expected_metric"] = expected
        finally:
            if pool is not None:
                await pool.close()
    print(json.dumps(receipt, indent=2, default=str))
    failed = [k for k, v in receipt.get("checks", {}).items() if not v]
    return 0 if ok_http and not failed else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", required=True)
    p.add_argument("--sqlite", help="local SQLite file the server writes to")
    p.add_argument("--database-url", help="Postgres URL the server writes to (local throwaway only)")
    p.add_argument("--organic", action="store_true", help="use google_organic (throwaway local DB only)")
    p.add_argument("--allow-remote", action="store_true")
    args = p.parse_args()
    if args.organic and (args.allow_remote or not (args.sqlite or args.database_url)):
        raise SystemExit("--organic requires a local database and a local base URL")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
