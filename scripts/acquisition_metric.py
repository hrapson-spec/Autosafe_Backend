#!/usr/bin/env python3
"""Read-only completed-day v2 acquisition counts; never outputs an identifier."""
import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import acquisition_store as stores

async def compute(store, start, end):
    if start>=end or end>datetime.now(timezone.utc).date():
        raise ValueError('use a nonempty range of completed UTC days')
    agg=await store.landing_aggregate_metric(start,end)
    groups={p:await store.landing_aggregate_metric(start,end,p) for p in stores.PILOT_GROUPS}
    oldest=await store.fetch_all(f'SELECT MIN(window_start_minute) AS oldest FROM {stores.EVENTS}')
    m=oldest[0]['oldest']
    age=None if m is None else stores.minute(stores.utcnow())-m
    return {'metric_version':stores.METRIC_VERSION,'days_utc':[str(start),str(end)],
      'metric_name':'Observed Google-organic public arrivals reaching a supported fresh-check render within 30 minutes',
      **agg,'rate':agg['numerator']/agg['denominator'] if agg['denominator'] else None,
      'entry_pilot_groups':groups,'oldest_raw_age_minutes':age,'retention_overdue':age is not None and age>35,
      'coverage':'Observed subset only. Missing/GPC/objection/expired/failed events are unobserved; total coverage unknown.',
      'reporting':'Retained counts only. Source classification is not proof of unpaid traffic. UTC differs from GSC Pacific days.',
      'commercial':'No link to qualified enquiries, bookings or revenue. Report those operational counts separately.'}

async def main(args):
    pool=None
    if args.sqlite: store=stores.SqliteStore(args.sqlite)
    else:
        import asyncpg
        dsn=os.environ.get('DATABASE_URL')
        if not dsn: raise SystemExit('DATABASE_URL or --sqlite required')
        pool=await asyncpg.create_pool(dsn,min_size=1,max_size=1)
        async def get_pool(): return pool
        store=stores.PostgresStore(get_pool)
    try: print(json.dumps(await compute(store,date.fromisoformat(args.from_),date.fromisoformat(args.to)),indent=2))
    finally:
        if pool: await pool.close()
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sqlite');parser.add_argument('--from',dest='from_',required=True);parser.add_argument('--to',required=True)
    asyncio.run(main(parser.parse_args()))
