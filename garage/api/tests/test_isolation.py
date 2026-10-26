"""Cross-workshop denial: every endpoint, the job worker, and the SQL itself."""
import contextlib
import pathlib
import re
import uuid

import pytest

from garage.api import app as app_mod, db as dbmod, worker
from garage.api.store import Scoped
from .conftest import add_staff_and_sign_in, day, history, import_rows, make_due, make_workshop, new_client, sql

V = "/api/garage/v1"
PREFIX = app_mod.PREFIX

# (method, path template, body) -- B's ids are substituted into the template; every one must be 404.
ID_ENDPOINTS = [
    ("DELETE", "/sessions/{session_id}", None),
    ("DELETE", "/staff/{membership_id}", None),
    ("POST", "/imports/{import_id}/commit", None),
    ("GET", "/imports/{import_id}", None),
    ("GET", "/bookings/{booking_id}", None),
    ("PATCH", "/bookings/{booking_id}", {"appointment_date": "2099-01-01"}),
    ("PATCH", "/bookings/{booking_id}", {"status": "cancelled"}),
    ("GET", "/bookings/{booking_id}/assessments", None),
    ("POST", "/bookings/{booking_id}/assessments", None),
    ("GET", "/bookings/{booking_id}/briefing", None),
    ("PUT", "/bookings/{booking_id}/feedback", {"value": "not_helpful", "reason_code": "other"}),
    ("GET", "/bookings/{booking_id}/decisions", None),
    ("POST", "/bookings/{booking_id}/decisions", {"decision_type": "proceed_as_booked", "note": "x"}),
    ("GET", "/bookings/{booking_id}/outcomes", None),
]
# endpoints with no id: they must return only the caller's own data
SELF_ENDPOINTS = [("GET", "/me"), ("GET", "/sessions"), ("GET", "/workshop"), ("GET", "/staff"), ("GET", "/usage"),
                  ("GET", "/bookings"), ("POST", "/imports/preview"), ("POST", "/bookings"), ("PATCH", "/workshop"),
                  ("POST", "/staff"), ("POST", "/auth/logout")]
OWNER_ONLY_SELF = {("PATCH", "/workshop"), ("POST", "/staff")}


@pytest.fixture()
def two(env):
    a = make_workshop(env, "Alpha", "oa@a.example")
    b = make_workshop(env, "Beta", "ob@b.example")
    sa = add_staff_and_sign_in(env, a, "sa@a.example")
    sb = add_staff_and_sign_in(env, b, "sb@b.example")
    for reg in ("AA19AAA", "BB20BBB"):
        env.dvsa.set_history(reg, history(reg, [("2022-01-01", "PASSED"), (str(day(-3)), "PASSED")]))
    a_ids = import_rows(a, [("AA19AAA", day(-5), "mot")])
    b_ids = import_rows(b, [("BB20BBB", day(-5), "mot")])
    worker.drain(env.ctx)
    make_due(env)
    worker.drain(env.ctx)
    bbid = b_ids["created_booking_ids"][0]
    b.post(f"{V}/bookings/{bbid}/decisions", json={"decision_type": "offer_inspection", "note": "b-note"})
    mem = lambda cl: next(m["membership_id"] for m in cl.get(f"{V}/staff").json()["items"] if m["role"] == "staff")
    ids = {"booking_id": bbid, "import_id": b_ids["import_id"], "membership_id": mem(b),
           "session_id": b.get(f"{V}/sessions").json()["items"][0]["id"]}
    return env, a, b, sa, sb, ids, a_ids["created_booking_ids"][0]


def b_state(b, ids):
    bid = ids["booking_id"]
    return {"booking": b.get(f"{V}/bookings/{bid}").json(), "dec": b.get(f"{V}/bookings/{bid}/decisions").json(),
            "ass": b.get(f"{V}/bookings/{bid}/assessments").json(), "out": b.get(f"{V}/bookings/{bid}/outcomes").json(),
            "imp": b.get(f"{V}/imports/{ids['import_id']}").json(), "staff": b.get(f"{V}/staff").json(),
            "sessions": b.get(f"{V}/sessions").json(), "usage": b.get(f"{V}/usage").json()}


@pytest.mark.parametrize("who", ["owner", "staff"])
@pytest.mark.parametrize("method,tmpl,body", ID_ENDPOINTS, ids=lambda x: str(x)[:40])
def test_other_workshops_ids_are_404_on_every_id_endpoint(two, who, method, tmpl, body):
    env, a, b, sa, sb, ids, _ = two
    attacker = a if who == "owner" else sa
    before = b_state(b, ids)
    n_dec = sql(env, "SELECT count(*) AS n FROM decisions")[0]["n"]
    n_ass = sql(env, "SELECT count(*) AS n FROM assessments")[0]["n"]
    r = attacker.request(method, V + tmpl.format(**ids), json=body)
    if who == "staff" and (method, tmpl) in {("DELETE", "/staff/{membership_id}")}:
        assert r.status_code == 403                                     # owner-only: role check comes first
        return
    assert r.status_code == 404, (method, tmpl, r.status_code, r.text)
    assert r.json()["error"]["code"] == "not_found"
    assert b_state(b, ids) == before                                   # nothing of B's changed
    assert sql(env, "SELECT count(*) AS n FROM decisions")[0]["n"] == n_dec
    assert sql(env, "SELECT count(*) AS n FROM assessments")[0]["n"] == n_ass
    # identical to a nonexistent id: no existence oracle
    ghost = {k: str(uuid.uuid4()) for k in ids}
    r2 = attacker.request(method, V + tmpl.format(**ghost), json=body)
    assert (r2.status_code, r2.json()) == (r.status_code, r.json())


def test_self_endpoints_never_show_other_workshop_data(two):
    env, a, b, sa, sb, ids, a_bid = two
    for cl in (a, sa):
        bookings = cl.get(f"{V}/bookings").json()["items"]
        assert [x["id"] for x in bookings] == [a_bid]
        assert {s["email"] for s in cl.get(f"{V}/sessions").json()["items"]} <= {"oa@a.example", "sa@a.example"}
        assert {m["email"] for m in cl.get(f"{V}/staff").json()["items"]} == {"oa@a.example", "sa@a.example"}
        assert cl.get(f"{V}/workshop").json()["name"] == "Alpha"
        assert cl.get(f"{V}/me").json()["workshop"]["name"] == "Alpha"
        blob = " ".join(str(cl.get(f"{V}{p}").json()) for p in ("/bookings", "/sessions", "/staff", "/workshop", "/usage"))
        assert ids["booking_id"] not in blob and "BB20BBB" not in blob and "b-note" not in blob and "Beta" not in blob
        # filters cannot reach across workshops either
        assert cl.get(f"{V}/bookings", params={"limit": 200, "from": "2000-01-01", "to": "2099-01-01"}).json()["items"][0]["registration"] == "AA19AAA"


def test_spoofed_workshop_hints_are_ignored(two):
    env, a, b, sa, sb, ids, a_bid = two
    r = a.get(f"{V}/bookings", headers={"X-Workshop-Id": b.get(f"{V}/me").json()["workshop"]["id"]})
    assert [x["id"] for x in r.json()["items"]] == [a_bid]
    assert a.post(f"{V}/bookings", json={"registration": "ZZ19ZZZ", "date": str(day(2)), "type": "mot",
                                           "workshop_id": ids["booking_id"]}).status_code == 422


def test_decision_and_booking_creation_land_in_callers_workshop_only(two):
    env, a, b, sa, sb, ids, a_bid = two
    wa = a.get(f"{V}/me").json()["workshop"]["id"]
    r = a.post(f"{V}/bookings/{a_bid}/decisions", json={"decision_type": "offer_inspection"})
    assert r.status_code == 201
    assert {x["workshop_id"] for x in sql(env, "SELECT workshop_id FROM decisions WHERE membership_id = %s",
                                         (r.json()["actor"]["membership_id"],))} == {wa}


def test_same_registration_in_two_workshops_is_independent(env):
    a, b = make_workshop(env, "A", "o@a.example"), make_workshop(env, "B", "o@b.example")
    ia = import_rows(a, [("AB12CDE", day(3), "mot")])
    ib = import_rows(b, [("AB12CDE", day(3), "mot")])
    assert ia["created_booking_ids"] and ib["created_booking_ids"] and ia["created_booking_ids"] != ib["created_booking_ids"]
    assert a.get(f"{V}/bookings/{ib['created_booking_ids'][0]}").status_code == 404


def test_unknown_or_forged_cookie_is_401_on_every_private_route(env):
    c = new_client(env)
    count = 0
    for route in c.app.state.garage_router.routes:
        if route.path in app_mod.PUBLIC_PATHS:
            continue
        for m in route.methods - {"HEAD", "OPTIONS"}:
            url = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), route.path)
            for cl in (c, ):
                assert cl.request(m, url, json={}).status_code == 401, (m, route.path)
                cl.cookies.set("__Host-garage_session", "forged-" + "x" * 40)
                assert cl.request(m, url, json={}).status_code == 401, (m, route.path)
                cl.cookies.clear()
            count += 1
    assert count == 24


def test_every_private_route_depends_on_get_scope_and_is_covered_here(env):
    router = new_client(env).app.state.garage_router

    def has_scope(dep):
        return dep.call is app_mod.get_scope or any(has_scope(d) for d in dep.dependencies)
    private = set()
    for route in router.routes:
        if route.path in app_mod.PUBLIC_PATHS:
            continue
        for m in route.methods - {"HEAD", "OPTIONS"}:
            assert has_scope(route.dependant), f"{m} {route.path} does not depend on get_scope"
            private.add((m, route.path.removeprefix(PREFIX)))
    covered = {(m, t) for m, t, _ in ID_ENDPOINTS} | set(SELF_ENDPOINTS)
    assert private == covered, f"endpoints missing from cross-workshop tests: {private ^ covered}"
    # and the public list is exactly the sign-in surface
    assert app_mod.PUBLIC_PATHS == {f"{PREFIX}{p}" for p in (
        "/health", "/auth/signup", "/auth/login-links", "/auth/links/{token}", "/auth/links/{token}/consume")}


def test_scoped_store_cannot_be_built_without_a_scope(env):
    with env.ctx.db.tx() as cur:
        with pytest.raises(TypeError):
            Scoped(cur, "11111111-1111-1111-1111-111111111111")
        with pytest.raises(TypeError):
            Scoped(cur, {"workshop_id": "x"})


# ------------------------------------------------------------------ the SQL itself
PRIVATE_TABLES = ("bookings", "booking_events", "imports", "source_snapshots", "assessments", "decisions",
                  "outcomes", "usage_counters", "briefing_feedback")
GARAGE_DIR = pathlib.Path(__file__).resolve().parents[1]


def test_only_the_store_names_private_tables():
    allowed = {"store.py", "retention.py"}
    rx = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE|DELETE\s+FROM)\s+(" + "|".join(PRIVATE_TABLES) + r")\b", re.I)
    offenders = []
    for f in GARAGE_DIR.glob("*.py"):
        if f.name not in allowed and rx.search(f.read_text()):
            offenders.append(f.name)
    assert offenders == []
    # the system sweep is the one place that runs unscoped, and it says so
    assert "not workshop-scoped by design" in (GARAGE_DIR / "retention.py").read_text()


def test_every_executed_statement_on_private_tables_is_workshop_filtered(two, monkeypatch):
    env, a, b, sa, sb, ids, a_bid = two
    log = []

    class Rec:
        def __init__(self, cur):
            self._c = cur

        def execute(self, q, args=None):
            log.append(q)
            return self._c.execute(q, args)

        def __getattr__(self, n):
            return getattr(self._c, n)

    orig = dbmod.Database.tx

    @contextlib.contextmanager
    def tx(self):
        with orig(self) as cur:
            yield Rec(cur)
    monkeypatch.setattr(dbmod.Database, "tx", tx)
    # a representative sweep of every code path
    for cl in (a, sa):
        for method, tmpl, body in ID_ENDPOINTS:
            cl.request(method, V + tmpl.format(**ids), json=body)
        cl.get(f"{V}/bookings"); cl.get(f"{V}/usage"); cl.get(f"{V}/workshop")
        cl.get(f"{V}/bookings/{a_bid}"); cl.get(f"{V}/bookings/{a_bid}/outcomes")
        cl.post(f"{V}/bookings/{a_bid}/decisions", json={"decision_type": "offer_inspection"})
        cl.post(f"{V}/bookings/{a_bid}/assessments")
        import_rows(cl, [("XX19XXX", day(-2), "mot")])
    a.patch(f"{V}/bookings/{a_bid}", json={"appointment_date": str(day(-3))})
    make_due(env)
    worker.drain(env.ctx)
    assert len(log) > 150
    bad = []
    for q in log:
        names = [t for t in PRIVATE_TABLES if re.search(rf"\b{t}\b", q)]
        if not names or re.match(r"\s*SET LOCAL", q):
            continue
        if re.match(r"\s*INSERT", q, re.I) and "workshop_id" in q.split("VALUES")[0] and "SELECT" not in q.split("VALUES")[0].upper():
            continue
        if not re.search(r"workshop_id\s*=\s*%s", q):
            bad.append(" ".join(q.split())[:120])
    assert bad == []


# ------------------------------------------------------------------ the job worker
def test_worker_refuses_payloads_pointing_at_another_workshop(two):
    env, a, b, sa, sb, ids, a_bid = two
    wa = a.get(f"{V}/me").json()["workshop"]["id"]
    wb = b.get(f"{V}/me").json()["workshop"]["id"]
    # B has a pending assessment (fresh refresh) and a scheduled outcome
    env.dvsa.set_history("BB20BBB", history("BB20BBB", [("2022-01-01", "PASSED")]))
    pend = b.post(f"{V}/bookings/{ids['booking_id']}/assessments").json()
    sql(env, "UPDATE jobs SET status='cancelled' WHERE workshop_id = %s", (wb,))        # take B's own jobs out of play
    outcome_row = sql(env, "SELECT check_day, schedule_date FROM outcomes WHERE booking_id = %s LIMIT 1", (ids["booking_id"],))[0]
    before_b = sql(env, "SELECT (SELECT count(*) FROM source_snapshots WHERE workshop_id = %s) AS snaps, "
                        "(SELECT string_agg(status, ',' ORDER BY id) FROM outcomes WHERE workshop_id = %s) AS outs", (wb, wb))
    # jobs owned by A, but whose payloads name B's rows
    sql(env, "INSERT INTO jobs (workshop_id, kind, payload) VALUES (%s, 'assess_booking', jsonb_build_object('assessment_id', %s::text))",
        (wa, pend["id"]))
    sql(env, "INSERT INTO jobs (workshop_id, kind, payload) VALUES (%s, 'outcome_check', "
             "jsonb_build_object('booking_id', %s::text, 'check_day', %s, 'schedule_date', %s::text))",
        (wa, ids["booking_id"], outcome_row["check_day"], str(outcome_row["schedule_date"])))
    # a payload that also smuggles a workshop id is still evaluated in A's scope
    sql(env, "INSERT INTO jobs (workshop_id, kind, payload) VALUES (%s, 'assess_booking', jsonb_build_object("
             "'assessment_id', %s::text, 'workshop_id', %s::text))", (wa, pend["id"], wb))
    # an import_commit job owned by A that names B's import
    sql(env, "INSERT INTO jobs (workshop_id, kind, payload) VALUES (%s, 'import_commit', jsonb_build_object('import_id', %s::text))",
        (wa, ids["import_id"]))
    calls = len(env.dvsa.calls)
    imp_before = sql(env, "SELECT status, progress_index FROM imports WHERE id = %s", (ids["import_id"],))
    worker.drain(env.ctx)
    failed = [r for r in sql(env, "SELECT status, last_error FROM jobs WHERE workshop_id = %s AND last_error LIKE 'scope_violation%%'", (wa,))]
    assert len(failed) == 4
    assert sql(env, "SELECT status, progress_index FROM imports WHERE id = %s", (ids["import_id"],)) == imp_before
    assert len(env.dvsa.calls) == calls                                                  # no upstream call was even made
    after_b = sql(env, "SELECT (SELECT count(*) FROM source_snapshots WHERE workshop_id = %s) AS snaps, "
                       "(SELECT string_agg(status, ',' ORDER BY id) FROM outcomes WHERE workshop_id = %s) AS outs", (wb, wb))
    assert after_b == before_b
    assert sql(env, "SELECT state FROM assessments WHERE id = %s", (pend["id"],))[0]["state"] == "pending"


def test_worker_jobs_run_only_for_their_own_workshop(env):
    a, b = make_workshop(env, "A", "o@a.example"), make_workshop(env, "B", "o@b.example")
    env.dvsa.set_history("AB12CDE", history("AB12CDE", [("2023-01-01", "PASSED")]))
    ia = import_rows(a, [("AB12CDE", day(3), "mot")])["created_booking_ids"][0]
    ib = import_rows(b, [("AB12CDE", day(3), "mot")])["created_booking_ids"][0]
    worker.drain(env.ctx)
    for cl, bid in ((a, ia), (b, ib)):
        assert cl.get(f"{V}/bookings/{bid}").json()["assessment"]["state"] == "unavailable"
    snaps = sql(env, "SELECT workshop_id, booking_id FROM source_snapshots")
    assert len(snaps) == 2 and len({s["workshop_id"] for s in snaps}) == 2
    # each workshop's snapshot hangs off its own booking (composite FK guarantees this at the DB level)
    with pytest.raises(Exception):
        sql(env, "INSERT INTO source_snapshots (workshop_id, booking_id, kind, upstream_status, content_sha256) "
                 "SELECT w.id, %s, 'assessment_input', 'ok', 'x' FROM workshops w WHERE w.name = 'A'", (ib,))


def test_database_rejects_cross_workshop_child_rows(env):
    a, b = make_workshop(env, "A", "o@a.example"), make_workshop(env, "B", "o@b.example")
    ib = import_rows(b, [("AB12CDE", day(3), "mot")])["created_booking_ids"][0]
    wa = a.get(f"{V}/me").json()["workshop"]["id"]
    ma = sql(env, "SELECT id FROM memberships WHERE workshop_id = %s", (wa,))[0]["id"]
    with pytest.raises(Exception, match="foreign key"):
        sql(env, "INSERT INTO decisions (workshop_id, booking_id, decision_type, membership_id) VALUES (%s, %s, 'offer_inspection', %s)",
            (wa, ib, ma))
