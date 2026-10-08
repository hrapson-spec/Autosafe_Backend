#!/usr/bin/env python3
"""Read-only paid aggregate counts. No random identifier or raw row is printed."""
import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime, timezone
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import acquisition_store as stores


def screen(arrivals, useful, days, measurement_ok=True):
    if not measurement_ok:
        return 'MEASUREMENT_UNRELIABLE'
    if arrivals >= 50 and useful == 0:
        return 'STOP_AND_INVESTIGATE_MEASUREMENT'
    if arrivals < 100 or days < 14:
        return 'INCONCLUSIVE_INSUFFICIENT_EXPOSURE'
    return 'QUALIFIES_FOR_SECOND_BATCH' if useful/arrivals >= .10 else 'DOES_NOT_PASS_ENGAGEMENT_SCREEN'


async def compute(store, start, end):
    if start >= end or end > datetime.now(timezone.utc).date():
        raise ValueError('Use a nonempty range of completed UTC days')
    landings=await store.fetch_all(f'SELECT * FROM {stores.LANDINGS} WHERE metric_version = ?' if store.backend=='sqlite' else f'SELECT * FROM {stores.LANDINGS} WHERE metric_version = $1', (stores.PAID_METRIC_VERSION,))
    events=await store.fetch_all(f'SELECT * FROM {stores.DAILY} WHERE metric_version = ?' if store.backend=='sqlite' else f'SELECT * FROM {stores.DAILY} WHERE metric_version = $1', (stores.PAID_METRIC_VERSION,))
    oldest=await store.fetch_all(f'SELECT MIN(window_start_minute) AS oldest FROM {stores.EVENTS}')
    minute=oldest[0]['oldest'];overdue=minute is not None and stores.minute(stores.utcnow())-minute>35
    groups={}
    for group in stores.PAID_GROUPS:
        ls=[r for r in landings if str(start)<=r['day']<str(end) and r['pilot_group']==group and r['source_group']=='paid_search']
        es=[r for r in events if str(start)<=r['day']<str(end) and r['pilot_group']==group and r['source_group']=='paid_search' and not r['is_bot']]
        arrivals=sum(r['landings'] for r in ls); useful=sum(r['landings_with_supported_result'] for r in ls)
        first=min((date.fromisoformat(r['day']) for r in ls),default=None)
        # No arrivals means elapsed test duration cannot be inferred from events.
        days=(end-first).days if first else None
        groups[group]={'observed_arrivals':arrivals,'observed_page_views':sum(r['count'] for r in es if r['event']=='page_viewed'),
                       'arrivals_with_useful_action':useful,'engagement_rate':useful/arrivals if arrivals else None,
                       'days_since_first_observed_arrival':days,'screen':screen(arrivals,useful,days or 0,not overdue)}
    return {'metric_version':stores.PAID_METRIC_VERSION,'completed_days_utc':[str(start),str(end)],'groups':groups,
            'retention_overdue':overdue,'coverage':'Consented observed arrivals only. Unobserved traffic is unknown. Platform clicks are a separate population.',
            'economics':'Join aggregate campaign/date totals to the cash ledger only. Include allocated shared costs once; no customer or identifier joins.',
            'status':'Aggregate counts only; not a live-launch or scientific-quality receipt.'}


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
