"""Test harness. Real PostgreSQL 16 via pgserver (embedded binaries, unix socket, no Docker), or any
server named in GARAGE_TEST_DATABASE_URL (a superuser DSN for a scratch cluster). Each test gets a fresh
database cloned from a migrated template, so tests are isolated and parallel-safe by construction."""
from __future__ import annotations

import itertools
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import psycopg2
import pytest
from fastapi.testclient import TestClient

from garage.api import app as app_mod
from garage.api import auth_store, db as dbmod
from garage.api.config import Settings
from garage.api.context import Ctx
from garage.api.dvsa import DvsaHistory, DvsaTest, FakeDvsaClient
from garage.api.mailer import FakeEmailSender
from garage.api.provider import StubProvider
from garage.api.timeutil import utcnow

ORIGIN = "https://garage.test"
_counter = itertools.count()


def _admin_dsn_and_base(tmp_path_factory):
    url = os.environ.get("GARAGE_TEST_DATABASE_URL")
    if url:
        return url, None
    import pgserver
    d = tmp_path_factory.mktemp("pg")
    srv = pgserver.get_server(d, cleanup_mode="stop")
    return srv.get_uri(), srv


@pytest.fixture(scope="session")
def pg_admin(tmp_path_factory):
    uri, srv = _admin_dsn_and_base(tmp_path_factory)
    admin = psycopg2.connect(uri)
    admin.autocommit = True
    cur = admin.cursor()
    cur.execute("DROP DATABASE IF EXISTS garage_tpl")
    cur.execute("CREATE DATABASE garage_tpl")
    dbmod.migrate(_with_db(uri, "garage_tpl"))
    yield uri, admin
    admin.close()
    if srv is not None:
        srv.cleanup()


def _with_db(uri: str, name: str) -> str:
    # works for both  postgresql://u@host/db?x=y  and  postgresql://u:@/db?host=/sock
    head, _, tail = uri.partition("?")
    base, _, _old = head.rpartition("/")
    return f"{base}/{name}" + (f"?{tail}" if tail else "")


@pytest.fixture()
def dsn(pg_admin):
    uri, admin = pg_admin
    name = f"t_{next(_counter)}_{uuid.uuid4().hex[:6]}"
    cur = admin.cursor()
    cur.execute(f'CREATE DATABASE "{name}" TEMPLATE garage_tpl')
    yield _with_db(uri, name)
    cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


class Clock:
    """Controllable application clock (the DB clock is separate: expiry tests edit rows)."""

    def __init__(self):
        self.offset = timedelta(0)

    def __call__(self):
        return utcnow() + self.offset

    def advance(self, **kw):
        self.offset += timedelta(**kw)


class ProgrammableProvider:
    name, version = "test", "1"

    def __init__(self):
        self.calls = []
        self.result = None   # ProviderResult or callable(req) -> ProviderResult or Exception

    def assess(self, req):
        self.calls.append(req)
        r = self.result(req) if callable(self.result) else self.result
        if isinstance(r, Exception):
            raise r
        return r or StubProvider().assess(req)


@dataclass
class Env:
    ctx: Ctx
    dvsa: FakeDvsaClient
    mail: FakeEmailSender
    clock: Clock
    provider: ProgrammableProvider
    dsn: str
    clients: list = field(default_factory=list)


@pytest.fixture()
def make_env(dsn):
    def _make(**settings_kw):
        settings = Settings(allow_simulated_assessments=True, database_url=dsn, base_url=ORIGIN, allowed_origins=(ORIGIN,), app_url=ORIGIN + "/",
                            job_backoff_seconds=0, **settings_kw)
        db = dbmod.Database(dsn, 1, 12)
        fake = FakeDvsaClient()
        clock = Clock()
        prov = ProgrammableProvider()
        mail = FakeEmailSender()
        ctx = Ctx(db=db, settings=settings, dvsa=fake, provider=prov, mailer=mail, clock=clock)
        return Env(ctx, fake, mail, clock, prov, dsn)
    envs = []

    def factory(**kw):
        e = _make(**kw)
        envs.append(e)
        return e
    yield factory
    for e in envs:
        for c in e.clients:
            c.close()
        e.ctx.db.close()


@pytest.fixture()
def env(make_env):
    return make_env()


class _ContractClient(TestClient):
    """Every response from every test is validated against garage/contract/openapi.yaml."""

    def request(self, method, url, *a, **kw):
        r = super().request(method, url, *a, **kw)
        from .contract_check import check_response
        check_response(method.upper(), str(r.request.url.path), r)
        return r


def new_client(env: Env) -> TestClient:
    app = app_mod.create_app(env.ctx)
    c = _ContractClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN, "Accept": "application/json"})
    env.clients.append(c)
    return c


def token_from_mail(env: Env, to: str) -> str:
    for m in reversed(env.mail.outbox):
        if m.to == to:
            return m.text.split("/auth/links/")[1].split()[0]
    raise AssertionError(f"no mail to {to}")


def sign_in(env: Env, client: TestClient, email: str):
    """Request a link, read it from the fake outbox, and consume it (what a user does)."""
    r = client.post("/api/garage/v1/auth/login-links", json={"email": email})
    assert r.status_code == 202
    tok = token_from_mail(env, email)
    r = client.post(f"/api/garage/v1/auth/links/{tok}/consume")
    assert r.status_code == 200, r.text
    return r.json()


def make_invite(env: Env, note="t") -> str:
    with env.ctx.db.tx() as cur:
        return auth_store.create_invite(cur, note, 14)


def make_workshop(env: Env, name: str, email: str) -> TestClient:
    """Invite -> signup -> consume the emailed link. Returns a signed-in client."""
    c = new_client(env)
    code = make_invite(env, name)
    r = c.post("/api/garage/v1/auth/signup", json={"invite_code": code, "workshop_name": name, "email": email})
    assert r.status_code == 202, r.text
    tok = token_from_mail(env, email)
    r = c.post(f"/api/garage/v1/auth/links/{tok}/consume")
    assert r.status_code == 200, r.text
    return c


def add_staff_and_sign_in(env: Env, owner: TestClient, email: str) -> TestClient:
    r = owner.post("/api/garage/v1/staff", json={"email": email})
    assert r.status_code == 201, r.text
    c = new_client(env)
    tok = token_from_mail(env, email)
    assert c.post(f"/api/garage/v1/auth/links/{tok}/consume").status_code == 200
    return c


def sql(env: Env, query: str, args=()):
    """Direct DB access for assertions/set-up (not part of the app)."""
    with env.ctx.db.tx() as cur:
        cur.execute(query, args)
        return cur.fetchall() if cur.description else None


def available(percent: int = 25, score: float | None = None, **kw):
    """A valid 'available' provider result (stub display policy; unrounded score defaults to the percent)."""
    from garage.api.provider import STUB_DISPLAY_POLICY, ProviderResult
    return ProviderResult(state="available", percent_rounded=percent,
                          band=STUB_DISPLAY_POLICY.band_for(percent), provider_name="t", provider_version="1",
                          display_policy=__import__("dataclasses").replace(STUB_DISPLAY_POLICY, display_state="simulated", suppression_reason=None, ranking_enabled=True), rank_score=percent / 100 if score is None else score, **kw)


def history(reg: str, tests: list[tuple], **kw) -> DvsaHistory:
    """tests: (iso_date, 'PASSED'|'FAILED'[, test_number])"""
    ts = []
    for i, t in enumerate(tests):
        d, res = t[0], t[1]
        ts.append(DvsaTest(completed_at=datetime.fromisoformat(d + "T10:00:00+00:00"), result=res,
                           test_number=t[2] if len(t) > 2 else f"{abs(hash((reg, i))) % 10**12:012d}"))
    return DvsaHistory(registration=reg, make="FORD", model="FOCUS", tests=tuple(sorted(ts, key=lambda x: x.completed_at)), **kw)


def day(offset: int):
    from garage.api.timeutil import london_date
    return london_date(utcnow()) + timedelta(days=offset)


def commit_and_run(client: TestClient, import_id: str) -> dict:
    """POST commit, run the import job(s) to completion, return the import record."""
    from garage.api import worker
    r = client.post(f"/api/garage/v1/imports/{import_id}/commit")
    assert r.status_code in (200, 202), r.text
    worker.drain(client.app.state.ctx, kinds=("import_commit",))
    return client.get(f"/api/garage/v1/imports/{import_id}").json()


def import_rows(client: TestClient, rows: list[tuple]):
    """Preview + commit manual rows (reg, date, type[, time]) and run the import job to completion.
    Returns the finished import record (GET /imports/{id})."""
    from garage.api import worker
    prev = client.post("/api/garage/v1/imports/preview", json={
        "format": "manual", "rows": [{"registration": r[0], "date": str(r[1]), "type": r[2],
                                      **({"time": r[3]} if len(r) > 3 else {})} for r in rows]})
    assert prev.status_code == 200, prev.text
    iid = prev.json()["import_id"]
    com = client.post(f"/api/garage/v1/imports/{iid}/commit")
    assert com.status_code == 202, com.text
    worker.drain(client.app.state.ctx, kinds=("import_commit",))
    rec = client.get(f"/api/garage/v1/imports/{iid}")
    assert rec.status_code == 200 and rec.json()["status"] == "committed", rec.text
    return rec.json()


def make_due(env: Env, kinds=("assess_booking", "outcome_check")):
    """Pretend the scheduled time has arrived for queued jobs."""
    sql(env, "UPDATE jobs SET run_at = now() WHERE status = 'queued' AND kind = ANY(%s)", (list(kinds),))
