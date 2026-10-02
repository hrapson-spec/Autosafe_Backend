#!/usr/bin/env python3
"""Disposable staging HTTP/PG acceptance for the bounded collector.

Never run against production: this advances retention's clock for synthetic
fixtures and drops/recreates only the collector v2 tables to verify rollback.
A production receipt must instead use internal_test events and real time.
"""
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime,timedelta,timezone
from urllib.parse import urlparse
import httpx
import asyncpg
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import acquisition_store as stores

async def main():
    base=os.environ.get('BASE_URL','http://app:8000')
    dsn=os.environ.get('DATABASE_URL','')
    if os.environ.get('ACQUISITION_DISPOSABLE_STAGING')!='1' or urlparse(base).hostname not in ('app','localhost','127.0.0.1') or urlparse(dsn).hostname not in ('postgres','localhost','127.0.0.1'):
        raise SystemExit('requires explicit disposable local/compose staging; never production')
    pool=await asyncpg.create_pool(dsn,min_size=1,max_size=4)
    async def get_pool():return pool
    store=stores.PostgresStore(get_pool)
    now=datetime.now(timezone.utc);start=stores.minute(now);uid=lambda:str(uuid.uuid4())
    session=uid()
    def event(name,lid,op=None,**extra):
        out=dict(schema_version=2,metric_version=stores.METRIC_VERSION,event_id=uid(),session_id=session,
                 landing_id=lid,window_start_minute=start,pilot_group='cost',page_family='guide' if name=='landing_observed' else 'app',source_group='google_organic',event=name)
        if op:out.update(operation_id=op,entry_mode='fresh_check')
        out.update(extra);return out
    async def post(client,body,**headers):
        return await client.post(base+'/api/acquisition/events',json=body,headers={'User-Agent':'Mozilla/5.0 AutoSafeSynthetic',**headers})
    receipt={}
    try:
        # The migration container created the real tables before server startup.
        assert await store.tables_exist()
        await store.ensure_schema() # idempotent second migration
        async with httpx.AsyncClient(timeout=10) as client:
            lid,op=uid(),uid();landing=event('landing_observed',lid,observation_state='observed')
            start_ev=event('check_started',lid,op)
            render=event('result_rendered',lid,op,persistence_mode='saved',render_delivered=True,supported_result=True,outcome_group='prediction',rate_valid=True,scope_visible=True,result_kind='vehicle_prediction',match_scope='model_prediction')
            events=[landing,start_ev,render]
            for e in events: assert (await post(client,e)).status_code==202
            assert (await post(client,landing)).status_code==202
            assert (await post(client,event('landing_observed',uid(),observation_state='observed'),**{'Sec-GPC':'1'})).status_code==202
            assert (await post(client,{**landing,'event_id':uid(),'registration':'SYNTHETIC-SECRET'})).status_code==400
            receipt['receipt_dedup_gpc_strict_body']=(await store.fetch_all(f'SELECT COUNT(*) AS n FROM {stores.EVENTS}'))[0]['n']==3
            # A second successful arrival and a failed first operation.
            failure=event('render_failed',lid,op,stage='render')
            assert (await post(client,failure)).status_code==202
            lid2,op2=uid(),uid()
            for e in [event('landing_observed',lid2,observation_state='observed'),event('check_started',lid2,op2),{**render,'event_id':uid(),'landing_id':lid2,'operation_id':op2}]:
                assert (await post(client,e)).status_code==202
            window=(now.replace(hour=0,minute=0,second=0,microsecond=0),now.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1))
            raw=await store.raw_metric(*window)
            assert raw=={'denominator':2,'numerator':1}
            receipt['raw_failure_precedence']=True
            first=await store.run_retention(now+timedelta(minutes=31))
            second=await store.run_retention(now+timedelta(minutes=31))
            aggregate=await store.primary_metric(*window)
            receipt['atomic_aggregate_delete']=first.rolled_up_events==first.deleted_raw_events==7 and aggregate==raw
            receipt['rerun_idempotent']=second.rolled_up_events==0
            receipt['no_individual_rows_after_aggregation']=(await store.fetch_all(f'SELECT COUNT(*) AS n FROM {stores.EVENTS}'))[0]['n']==0
            # Replay an expired envelope using real server time; never recreate IDs.
            old={**landing,'event_id':uid(),'window_start_minute':start-31}
            assert (await post(client,old)).status_code==202
            receipt['expired_replay_dropped']=(await store.fetch_all(f'SELECT COUNT(*) AS n FROM {stores.EVENTS}'))[0]['n']==0
        # Reversible database rollback only in this explicitly disposable stack.
        async with pool.acquire() as conn:
            async with conn.transaction():
                for statement in stores.POSTGRES_ROLLBACK_DDL: await conn.execute(statement)
        receipt['rollback_removed_only_v2_tables']=not await store.tables_exist()
        await store.ensure_schema()
        receipt['reapply_migration']=await store.tables_exist()
        print(json.dumps({'synthetic_disposable_staging':True,'checks':receipt,'passed':all(receipt.values())},indent=2))
        assert all(receipt.values())
    finally: await pool.close()

if __name__=='__main__':asyncio.run(main())
