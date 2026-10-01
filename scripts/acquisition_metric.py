#!/usr/bin/env python3
"""Primary acquisition metric from the first-party collector tables (OA-005).

Eligible organic landings (denominator) and those with a displayed supported
result sharing the landing_id (numerator), over a half-open UTC window.

    DATABASE_URL=postgresql://... python scripts/acquisition_metric.py --from 2026-10-02 --to 2026-10-30
    python scripts/acquisition_metric.py --sqlite /path/to/local.sqlite --from 2026-10-02 --to 2026-10-30

Definitions (see docs/acquisition/COLLECTOR.md for the caveats that MUST
accompany any figure from this script):

* landing:    landing_observed, source_group google_organic (paid_search is a
              separate group and is excluded from numerator and denominator),
              not is_bot, page_family in the public set (not `app`), one per
              landing_id. A reload is not a landing (the client does not emit).
* completion: result_rendered with supported_result true carrying that
              landing_id, unless render_failed exists for the same operation.
* Raw events exist for 90 days only: windows reaching further back than that
  are incomplete (the script warns). Older periods are only available as the
  identifier-free daily counts in acquisition_daily.

Read-only. Never prints an identifier.
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import acquisition_store as store_mod  # noqa: E402


def _day(value: str) -> datetime:
    d = date.fromisoformat(value)
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


async def _open_store(args):
    if args.sqlite:
        return store_mod.SqliteStore(args.sqlite), None
    dsn = args.database_url or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("give --sqlite PATH, --database-url, or set DATABASE_URL")
    import asyncpg

    pool = await asyncpg.create_pool(dsn.replace("postgres://", "postgresql://"), min_size=1, max_size=1)

    async def get_pool():
        return pool

    return store_mod.PostgresStore(get_pool), pool


async def compute(store, from_ts: datetime, to_ts: datetime) -> dict:
    metric = await store.primary_metric(from_ts, to_ts)
    ph = "$1" if store.backend == "postgres" else "?"
    ph2 = "$2" if store.backend == "postgres" else "?"
    params = (from_ts, to_ts) if store.backend == "postgres" else (store_mod._iso(from_ts), store_mod._iso(to_ts))
    landings = await store.fetch_all(
        "SELECT source_group, page_family, is_bot, COUNT(DISTINCT landing_id) AS landings "
        "FROM acquisition_events WHERE event = 'landing_observed' "
        f"AND received_at >= {ph} AND received_at < {ph2} "
        "GROUP BY source_group, page_family, is_bot ORDER BY source_group, page_family, is_bot",
        params,
    )
    paid = [r for r in landings if r["source_group"] == "paid_search" and not r["is_bot"]]
    earliest = await store.fetch_all("SELECT MIN(received_at) AS earliest FROM acquisition_events")
    earliest_raw = earliest[0]["earliest"] if earliest else None
    den, num = metric["denominator"], metric["numerator"]
    return {
        "window_utc": [from_ts.isoformat(), to_ts.isoformat()],
        "eligible_organic_landings": den,
        "eligible_with_displayed_supported_result": num,
        "rate": (num / den) if den else None,
        "paid_search_landings_excluded": sum(int(r["landings"]) for r in paid),
        "all_landing_observations_by_group": [
            {"source_group": r["source_group"], "page_family": r["page_family"], "is_bot": bool(r["is_bot"]),
             "landings": int(r["landings"])} for r in landings
        ],
        "earliest_raw_event": str(earliest_raw) if earliest_raw else None,
    }


async def main_async(args) -> int:
    if args.from_ is None or args.to is None:
        today = datetime.now(timezone.utc).date()
        to_ts = _day(today.isoformat())
        from_ts = to_ts - timedelta(days=28)
    else:
        from_ts, to_ts = _day(args.from_), _day(args.to)
    if to_ts <= from_ts:
        raise SystemExit("--to must be after --from")
    store, pool = await _open_store(args)
    try:
        out = await compute(store, from_ts, to_ts)
    finally:
        if pool is not None:
            await pool.close()
    if from_ts < datetime.now(timezone.utc) - store_mod.RAW_RETENTION:
        out["warning"] = "window starts before the 90-day raw retention limit; the figure is incomplete"
    out["caveats"] = [
        "Client-reported acknowledgements; they do not prove accuracy or comprehension.",
        "A reload, new tab or typed URL starts an unattributed session (not in this metric).",
        "Paid search is separated only when the landing URL carried gclid, gbraid, wbraid or utm_medium cpc/ppc/paid; other paid traffic is indistinguishable from organic.",
        "Landings blocked by Global Privacy Control, script blockers or old cached pages are unobserved.",
    ]
    print(json.dumps(out, indent=2, default=str))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sqlite", help="local SQLite file (synthetic runs and tests only)")
    p.add_argument("--database-url", help="Postgres URL (default: $DATABASE_URL)")
    p.add_argument("--from", dest="from_", help="window start, UTC date (inclusive)")
    p.add_argument("--to", help="window end, UTC date (exclusive)")
    return asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
