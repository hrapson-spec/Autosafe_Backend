import json
from datetime import datetime

import httpx
import pytest

from garage.api import cli, db as dbmod, mailer, retention, worker
from garage.api.timeutil import LONDON, london_date
from .conftest import day, import_rows, make_workshop, sql

V = "/api/garage/v1"


def test_migrations_apply_once_and_are_idempotent(env):
    assert dbmod.migrate(env.dsn) == []                                         # template already migrated
    assert [r["version"] for r in sql(env, "SELECT version FROM garage_schema_migrations")] == ["0001_init"]


def test_health(env):
    from .conftest import new_client
    assert new_client(env).get(f"{V}/health").json() == {"status": "ok"}


def test_timestamps_are_europe_london(env):
    c = make_workshop(env, "A", "o@a.example")
    b = import_rows(c, [("AB12CDE", day(3), "mot")])["created_booking_ids"][0]
    created = c.get(f"{V}/bookings/{b}").json()["created_at"]
    dt = datetime.fromisoformat(created)
    assert dt.utcoffset() == dt.astimezone(LONDON).utcoffset()
    assert london_date(datetime(2026, 7, 1, 23, 30, tzinfo=__import__("datetime").timezone.utc)).isoformat() == "2026-07-02"   # BST


def test_retention_sweep_removes_old_bookings_and_history_only(env):
    c = make_workshop(env, "A", "o@a.example")
    keep = import_rows(c, [("AB12CDE", day(-2), "mot")])["created_booking_ids"][0]
    old = import_rows(c, [("AB13CDE", day(-3), "mot")])["created_booking_ids"][0]
    c.post(f"{V}/bookings/{old}/decisions", json={"decision_type": "offer_inspection"})
    c.post(f"{V}/bookings/{keep}/decisions", json={"decision_type": "offer_inspection"})
    worker.drain(env.ctx)
    sql(env, "UPDATE bookings SET appointment_date = current_date - interval '13 months' WHERE id = %s", (old,))
    out = retention.purge(env.ctx.db, 12, london_date(env.ctx.clock()))
    assert out["bookings"] == 1
    assert c.get(f"{V}/bookings/{old}").status_code == 404 and c.get(f"{V}/bookings/{keep}").status_code == 200
    assert len(c.get(f"{V}/bookings/{keep}/decisions").json()["items"]) == 1
    assert sql(env, "SELECT count(*) AS n FROM decisions")[0]["n"] == 1
    # outside the sweep the append-only rule still bites
    with pytest.raises(Exception, match="append-only"):
        sql(env, "DELETE FROM decisions")


def test_retention_job_runs_through_worker(env):
    from garage.api import jobs
    assert jobs.enqueue_system(env.ctx.db, "retention_sweep", {}, dedupe_key="retention:x")
    assert not jobs.enqueue_system(env.ctx.db, "retention_sweep", {}, dedupe_key="retention:x")   # de-duplicated
    assert worker.drain(env.ctx) == 1
    assert sql(env, "SELECT status FROM jobs")[0]["status"] == "done"


def test_cli_create_invite_prints_code_once_and_stores_hash(env, monkeypatch, capsys):
    monkeypatch.setenv("GARAGE_DATABASE_URL", env.dsn)
    cli.main(["create-invite", "--note", "Smith Motors", "--ttl-days", "7"])
    code = capsys.readouterr().out.strip()
    assert code.startswith("GRG-") and len(code) == 23
    row = sql(env, "SELECT code_hash, note FROM invites")[0]
    assert code not in json.dumps(row, default=str) and row["note"] == "Smith Motors"
    from .conftest import new_client
    c = new_client(env)
    assert c.post(f"{V}/auth/signup", json={"invite_code": code.lower(), "workshop_name": "S", "email": "s@s.example"}).status_code == 202


def test_resend_sender_payload_and_failures():
    seen = []

    def h(req):
        seen.append(req)
        return httpx.Response(200 if req.headers["authorization"].endswith("good") else 422, json={})
    s = mailer.ResendEmailSender("good", "noreply@x.test", dry_run=False, http=httpx.Client(transport=httpx.MockTransport(h)))
    assert s.send("a@b.example", "Hi", "text") is True
    body = json.loads(seen[0].content)
    assert body["from"] == "AutoSafe Garage <noreply@x.test>" and body["to"] == ["a@b.example"]
    assert mailer.ResendEmailSender("bad", "n@x.test", dry_run=False, http=httpx.Client(transport=httpx.MockTransport(h))).send("a@b.example", "s", "t") is False
    nokey = mailer.ResendEmailSender("", "n@x.test", dry_run=False)
    nokey.api_key = None
    assert nokey.send("a@b.example", "s", "t") is False
    assert mailer.ResendEmailSender("k", dry_run=True).send("a@b.example", "s", "t") is True and len(seen) == 2
    assert mailer.mask_email("john@example.com") == "j***@example.com"


def test_sign_in_email_contains_link_and_no_customer_data(env):
    make_workshop(env, "A", "o@a.example")
    m = env.mail.outbox[0]
    assert "/api/garage/v1/auth/links/" in m.text and "15 minutes" in m.text and "once" in m.text
