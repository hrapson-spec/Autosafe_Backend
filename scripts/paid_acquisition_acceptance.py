"""Disposable-schema paid storage acceptance, called only by staging evidence."""
from datetime import timedelta
from uuid import uuid4

import asyncpg
import acquisition_routes as routes
import acquisition_store as stores


async def check_paid_store(dsn):
    now = stores.utcnow().replace(second=0, microsecond=0)
    schema = 'paid_acceptance_' + uuid4().hex
    admin = await asyncpg.connect(dsn)
    pool = None
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2,
                                        server_settings={'search_path': schema})
        async def get_pool():
            return pool
        store = stores.PostgresStore(get_pool, clock=lambda: now)
        await store.ensure_schema()
        landing, operation = str(uuid4()), str(uuid4())
        def record(kind, **fields):
            payload = dict(schema_version=2, metric_version=stores.PAID_METRIC_VERSION,
                           event_id=str(uuid4()), session_id=str(uuid4()), landing_id=landing,
                           window_start_minute=stores.minute(now), pilot_group='discover_owner',
                           page_family='app', source_group='paid_search', consent_granted=True,
                           event=kind, **fields)
            return routes._record_from_event(routes.parse_paid_event(payload), now, False)
        records = [record('landing_observed', observation_state='observed'),
                   record('page_viewed', observation_state='observed'),
                   record('check_started', operation_id=operation, entry_mode='fresh_check'),
                   record('result_rendered', operation_id=operation, entry_mode='fresh_check',
                          persistence_mode='saved', render_delivered=True, supported_result=True,
                          outcome_group='exact_comparison', rate_valid=True, sample_nonzero=True,
                          scope_visible=True, result_kind='comparison', match_scope='exact_band')]
        for rec in records:
            assert await store.insert_event(rec)
            assert not await store.insert_event(rec)
        rows = await store.fetch_all(f'SELECT * FROM {stores.EVENTS}')
        assert list(stores.summarise(rows)[1].values()) == [[1, 1]]
        assert stores.metric(stores.summarise(rows)[1], now.date(), now.date()+timedelta(days=1))['denominator'] == 0
        assert await store.insert_event(record('render_failed', operation_id=operation,
                                              entry_mode='fresh_check', stage='render'))
        result = await store.run_retention(now+timedelta(minutes=31))
        assert result.deleted_raw_events == 5
        assert not await store.fetch_all(f'SELECT * FROM {stores.EVENTS}')
        rows = await store.fetch_all(f'SELECT * FROM {stores.LANDINGS}')
        assert rows[0]['landings'] == 1 and rows[0]['landings_with_supported_result'] == 0
        rows = await store.fetch_all(f'SELECT * FROM {stores.DAILY} WHERE event = ?', ('page_viewed',))
        assert rows[0]['count'] == 1
        return {'backend': 'postgres', 'raw_deleted': 5, 'arrivals': 1, 'useful': 0, 'page_views': 1}
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()
