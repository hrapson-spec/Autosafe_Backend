"""Paid consent, isolation, deduplication and failure precedence."""
import asyncio
import os
from dataclasses import asdict
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import Limiter
from slowapi.util import get_remote_address

import acquisition_routes as routes
import acquisition_store as stores


def event(kind='landing_observed', **changes):
    payload = dict(schema_version=2, metric_version=stores.PAID_METRIC_VERSION,
                   event_id=str(uuid4()), session_id=str(uuid4()), landing_id=str(uuid4()),
                   window_start_minute=stores.minute(stores.utcnow()), pilot_group='discover_owner',
                   page_family='home', source_group='paid_search', consent_granted=True,
                   event=kind, observation_state='observed')
    if kind not in ('landing_observed', 'page_viewed'):
        payload.pop('observation_state')
    payload.update(changes)
    return payload


def test_strict_paid_contract_and_organic_separation():
    for changes in ({'consent_granted':False}, {'consent_granted':1}, {'registration':'SECRET'},
                    {'pilot_group':'arbitrary'}, {'source_group':'google_organic'}, {'schema_version':True}):
        with pytest.raises(ValueError):
            routes.parse_paid_event(event(**changes))
    with pytest.raises(ValueError):
        routes.parse_event(event())
    parsed = routes.parse_paid_event(event('page_viewed'))
    assert parsed.event == 'page_viewed'
    assert parsed.metric_version == stores.PAID_METRIC_VERSION
    assert 'consent_granted' not in parsed.model_dump()


def test_route_flag_consent_gpc_and_duplicate(tmp_path, monkeypatch):
    app = FastAPI()
    app.state.limiter = Limiter(key_func=get_remote_address)
    routes.register_acquisition_routes(app, app.state.limiter)
    store = stores.SqliteStore(str(tmp_path/'events.db'))
    routes.set_store_for_tests(store)
    monkeypatch.setenv('ACQUISITION_INGEST_ENABLED','true')
    client = TestClient(app)
    payload = event()
    headers = {'user-agent':'Mozilla/5.0 Chrome/130 Safari/537.36'}
    try:
        assert client.post(routes.PAID_ROUTE_PATH,json=payload).status_code == 404
        monkeypatch.setenv('PAID_ACQUISITION_ENABLED','true')
        assert client.post(routes.PAID_ROUTE_PATH,json=event(consent_granted=False)).status_code == 400
        assert client.post(routes.PAID_ROUTE_PATH,json=payload,headers={**headers,'sec-gpc':'1'}).status_code == 202
        for _ in range(2):
            assert client.post(routes.PAID_ROUTE_PATH,json=payload,headers=headers).status_code == 202
        rows = asyncio.run(store.fetch_all(f'SELECT * FROM {stores.EVENTS}'))
        assert len(rows) == 1
        assert rows[0]['metric_version'] == stores.PAID_METRIC_VERSION
        assert client.post(routes.ROUTE_PATH,json=payload).status_code == 400
    finally:
        routes.set_store_for_tests(None)


@pytest.mark.parametrize('backend', ['sqlite', 'postgres'])
def test_paid_rollup_deduplicates_and_render_failure_wins(tmp_path, backend):
    if backend == 'postgres' and not os.environ.get('ACQUISITION_TEST_PG_DSN'):
        pytest.skip('Disposable PostgreSQL DSN not supplied')
    now = stores.utcnow().replace(second=0,microsecond=0)
    store = stores.SqliteStore(str(tmp_path/'events.db'), clock=lambda:now)
    lid, op = str(uuid4()), str(uuid4())
    def record(payload):
        return routes._record_from_event(routes.parse_paid_event(payload), now, False)
    payloads = [event(landing_id=lid), event('page_viewed',landing_id=lid),
                event('check_started',landing_id=lid,operation_id=op,entry_mode='fresh_check'),
                event('result_rendered',landing_id=lid,operation_id=op,entry_mode='fresh_check',
                      persistence_mode='saved',render_delivered=True,supported_result=True,
                      outcome_group='exact_comparison',rate_valid=True,sample_nonzero=True,
                      scope_visible=True,result_kind='comparison',match_scope='exact_band')]
    async def run():
        await store.ensure_schema()
        for payload in payloads:
            rec = record(payload)
            assert await store.insert_event(rec)
            assert not await store.insert_event(rec)
        rows = await store.fetch_all(f'SELECT * FROM {stores.EVENTS}')
        assert list(stores.summarise(rows)[1].values()) == [[1,1]]
        assert stores.metric(stores.summarise(rows)[1],now.date(),now.date()+timedelta(days=1))['denominator'] == 0
        await store.insert_event(record(event('render_failed',landing_id=lid,operation_id=op,
                                             entry_mode='fresh_check',stage='render')))
        rows = await store.fetch_all(f'SELECT * FROM {stores.EVENTS}')
        assert list(stores.summarise(rows)[1].values()) == [[1,0]]
        result = await store.run_retention(now+timedelta(minutes=31))
        assert result.deleted_raw_events == 5
        assert not await store.fetch_all(f'SELECT * FROM {stores.EVENTS}')
        rows = await store.fetch_all(f'SELECT * FROM {stores.LANDINGS}')
        assert rows[0]['landings'] == 1 and rows[0]['landings_with_supported_result'] == 0
        rows = await store.fetch_all(f'SELECT * FROM {stores.DAILY} WHERE event = ?', ('page_viewed',))
        assert rows[0]['count'] == 1
    async def isolated():
        nonlocal store
        if backend == 'sqlite':
            await run()
            return
        import asyncpg
        dsn = os.environ['ACQUISITION_TEST_PG_DSN']
        schema = 'paid_test_' + uuid4().hex
        admin = await asyncpg.connect(dsn)
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2,
                                        server_settings={'search_path': schema})
        async def get_pool():
            return pool
        store = stores.PostgresStore(get_pool, clock=lambda:now)
        try:
            await run()
        finally:
            await pool.close()
            await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
            await admin.close()
    asyncio.run(isolated())


def test_screen_defaults_require_second_batch_and_never_force_winner():
    from scripts.paid_acquisition_metric import screen
    assert screen(49,0,14) == 'INCONCLUSIVE_INSUFFICIENT_EXPOSURE'
    assert screen(50,0,14) == 'STOP_AND_INVESTIGATE_MEASUREMENT'
    assert screen(100,10,13) == 'INCONCLUSIVE_INSUFFICIENT_EXPOSURE'
    assert screen(100,10,14) == 'QUALIFIES_FOR_SECOND_BATCH'
    assert screen(100,9,14) == 'DOES_NOT_PASS_ENGAGEMENT_SCREEN'
    assert screen(100,10,14,False) == 'MEASUREMENT_UNRELIABLE'


def test_unreceived_arrival_does_not_generate_attributed_page_views():
    payload = event('page_viewed')
    rec = routes._record_from_event(routes.parse_paid_event(payload), stores.utcnow(), False)
    events, landings = stores.summarise([asdict(rec)])
    assert not events and not landings
