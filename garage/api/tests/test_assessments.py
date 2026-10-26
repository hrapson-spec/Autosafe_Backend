import threading
import time

import pytest

from garage.api import jobs, worker
from garage.api.dvsa import DbThrottle, DvsaNotFound, DvsaTransientError, ThrottledDvsa
from garage.api.provider import ProviderResult, StubProvider
from .conftest import available, day, history, import_rows, make_due, make_workshop, sql

V = "/api/garage/v1"


def booking(c, bid):
    return c.get(f"{V}/bookings/{bid}").json()


def ok(**kw):
    return available(25, **kw)


def test_stub_provider_says_model_not_packaged():
    r = StubProvider().assess(None)
    assert (r.state, r.reason) == ("unavailable", "model_not_packaged")


def test_stub_flow_stores_snapshot_and_evidence(env):
    env.dvsa.set_history("AB12CDE", history("AB12CDE", [("2022-05-01", "PASSED"), ("2023-05-03", "FAILED")]))
    c = make_workshop(env, "A", "o@a.example")
    bid = import_rows(c, [("AB12CDE", day(3), "mot")])["created_booking_ids"][0]
    assert booking(c, bid)["assessment"]["state"] == "pending"
    worker.drain(env.ctx)
    a = booking(c, bid)["assessment"]
    assert (a["state"], a["reason"]) == ("unavailable", "model_not_packaged")
    assert a["evidence"]["prior_test_count"] == 2 and a["evidence"]["latest_prior_test_date"] == "2023-05-03"
    assert a["result"] is None and a["completed_at"]
    snap = sql(env, "SELECT upstream_status, content_sha256 FROM source_snapshots WHERE kind='assessment_input'")
    assert len(snap) == 1 and snap[0]["upstream_status"] == "ok" and len(snap[0]["content_sha256"]) == 64


def test_inputs_frozen_at_min_of_import_and_appointment(env):
    """R10: provider never sees tests on/after min(import date, appointment date)."""
    appt = day(-5)
    env.dvsa.set_history("AB12CDE", history("AB12CDE", [
        (str(day(-400)), "PASSED"), (str(day(-6)), "FAILED"), (str(appt), "PASSED"), (str(day(-2)), "PASSED")]))
    env.provider.result = ok()
    c = make_workshop(env, "A", "o@a.example")
    bid = import_rows(c, [("AB12CDE", appt, "mot")])["created_booking_ids"][0]
    worker.drain(env.ctx)
    seen = env.provider.calls[0]
    assert [str(t.test_date) for t in seen.history.tests] == [str(day(-400)), str(day(-6))]
    assert seen.cutoff_date == appt
    a = booking(c, bid)["assessment"]
    assert a["input_cutoff_date"] == str(appt) and a["evidence"]["prior_test_count"] == 2
    assert a["state"] == "available" and a["result"] == {"percent_rounded": 25, "band": "medium"}
    # future appointment: cutoff is today (import date), so a test dated today is excluded too
    env.dvsa.set_history("ZZ19ZZZ", history("ZZ19ZZZ", [(str(day(-30)), "PASSED"), (str(day(0)), "PASSED")]))
    import_rows(c, [("ZZ19ZZZ", day(10), "mot")])
    worker.drain(env.ctx)
    assert [str(t.test_date) for t in env.provider.calls[1].history.tests] == [str(day(-30))]
    assert env.provider.calls[1].cutoff_date == day(0)


def test_deferred_start_does_not_move_cutoff(env):
    """Cut-off is fixed when the version is created, even if the job runs days later."""
    env.provider.result = ok()
    env.dvsa.set_history("AB12CDE", history("AB12CDE", [(str(day(-30)), "PASSED"), (str(day(1)), "PASSED")]))
    c = make_workshop(env, "A", "o@a.example")
    import_rows(c, [("AB12CDE", day(10), "mot")])
    env.clock.advance(days=3)             # worker only gets to it later
    worker.drain(env.ctx)
    assert [str(t.test_date) for t in env.provider.calls[0].history.tests] == [str(day(-30))]


def test_provider_output_validation_and_errors(env):
    c = make_workshop(env, "A", "o@a.example")
    cases = [
        (ProviderResult("available", percent_rounded=23, band="low"), "failed", "invalid_provider_output"),
        (ProviderResult("available", percent_rounded=25, band="extreme"), "failed", "invalid_provider_output"),
        (ProviderResult("unavailable"), "failed", "invalid_provider_output"),            # no reason
        (RuntimeError("boom"), "failed", "provider_error"),
        (ProviderResult("unsupported", reason="first_mot"), "unsupported", "first_mot"),
    ]
    for i, (res, state, reason) in enumerate(cases):
        reg = f"AB{i:02d}CDE"
        env.dvsa.set_history(reg, history(reg, [("2023-01-01", "PASSED")]))
        env.provider.result = res
        bid = import_rows(c, [(reg, day(2), "mot")])["created_booking_ids"][0]
        worker.drain(env.ctx)
        a = booking(c, bid)["assessment"]
        assert (a["state"], a["reason"]) == (state, reason), (i, a)
        assert a["result"] is None


def test_population_reasons_from_history(env):
    c = make_workshop(env, "A", "o@a.example")
    env.dvsa.set_history("CC21CCC", history("CC21CCC", []))                       # first MOT not yet taken
    env.dvsa.set_history("AB12CDE", history("AB12CDE", [(str(day(3)), "PASSED")]))  # only a test after the cut-off date
    out = import_rows(c, [("CC21CCC", day(2), "mot"), ("QQ99QQQ", day(2), "mot"), ("AB12CDE", day(1), "service_mot")])
    env.dvsa.errors["QQ99QQQ"] = DvsaNotFound
    worker.drain(env.ctx)
    by = {b["registration"]: b["assessment"] for b in c.get(f"{V}/bookings").json()["items"]}
    assert (by["CC21CCC"]["state"], by["CC21CCC"]["reason"]) == ("unsupported", "no_prior_mot")
    assert (by["QQ99QQQ"]["state"], by["QQ99QQQ"]["reason"]) == ("unsupported", "no_mot_history")
    assert (by["AB12CDE"]["state"], by["AB12CDE"]["reason"]) == ("unsupported", "no_prior_mot")
    assert env.provider.calls == []                                               # provider not consulted


def test_partial_upstream_failure_isolated_per_booking(env):
    """Fixtures: AA19AAA ok, BB20BBB ok, DD22DDD always transient, EE23EEE permanent, CC21CCC no tests."""
    c = make_workshop(env, "A", "o@a.example")
    regs = ["AA19AAA", "DD22DDD", "BB20BBB", "EE23EEE", "CC21CCC"]
    out = import_rows(c, [(r, day(3), "mot") for r in regs])
    assert len(out["created_booking_ids"]) == 5                      # import itself never fails on upstream trouble
    worker.drain(env.ctx)
    by = {b["registration"]: b["assessment"] for b in c.get(f"{V}/bookings").json()["items"]}
    assert by["AA19AAA"]["state"] == by["BB20BBB"]["state"] == "unavailable"          # stub provider
    assert by["AA19AAA"]["reason"] == "model_not_packaged"
    assert (by["DD22DDD"]["state"], by["DD22DDD"]["reason"]) == ("failed", "upstream_unavailable")
    assert (by["EE23EEE"]["state"], by["EE23EEE"]["reason"]) == ("failed", "upstream_error")
    assert by["CC21CCC"]["reason"] == "no_prior_mot"
    assert env.dvsa.calls.count("DD22DDD") == 3 and env.dvsa.calls.count("EE23EEE") == 1   # retried vs not
    jobs_ = {r["status"] for r in sql(env, "SELECT status FROM jobs WHERE kind='assess_booking'")}
    assert jobs_ == {"done", "failed"}


def test_transient_failure_then_success(env):
    env.dvsa.set_history("AB12CDE", history("AB12CDE", [("2023-01-01", "PASSED")]))
    env.dvsa.errors["AB12CDE"] = [DvsaTransientError("t"), DvsaTransientError("t"), None]
    c = make_workshop(env, "A", "o@a.example")
    bid = import_rows(c, [("AB12CDE", day(2), "mot")])["created_booking_ids"][0]
    assert worker.run_once(env.ctx)
    a = booking(c, bid)["assessment"]
    assert (a["state"], a["reason"]) == ("pending", "upstream_retrying")
    worker.drain(env.ctx)
    assert booking(c, bid)["assessment"]["state"] == "unavailable"
    # the three attempts consumed ONE unit of fair use
    assert c.get(f"{V}/usage").json()["assessments_used"] == 1


def test_fair_use_50_per_workshop_per_day_defers_then_resumes(make_env):
    env = make_env(fair_use_assessments_per_day=3)
    for i in range(5):
        env.dvsa.set_history(f"AB{i:02d}CDE", history(f"AB{i:02d}CDE", [("2023-01-01", "PASSED")]))
    c = make_workshop(env, "A", "o@a.example")
    other = make_workshop(env, "B", "o@b.example")
    import_rows(c, [(f"AB{i:02d}CDE", day(2), "mot") for i in range(5)])
    import_rows(other, [("AB00CDE", day(2), "mot")])
    worker.drain(env.ctx)
    states = [b["assessment"]["state"] for b in c.get(f"{V}/bookings").json()["items"]]
    assert sorted(states) == ["pending", "pending", "unavailable", "unavailable", "unavailable"]
    pend = [b["assessment"] for b in c.get(f"{V}/bookings").json()["items"] if b["assessment"]["state"] == "pending"]
    assert all(p["reason"] == "fair_use_deferred" and p["retry_at"] for p in pend)
    u = c.get(f"{V}/usage").json()
    assert (u["assessments_used"], u["assessments_limit"], u["assessments_remaining"]) == (3, 3, 0)
    assert other.get(f"{V}/usage").json()["assessments_used"] == 1                  # per workshop
    assert other.get(f"{V}/bookings").json()["items"][0]["assessment"]["state"] == "unavailable"
    bid = next(b["id"] for b in c.get(f"{V}/bookings").json()["items"] if b["assessment"]["state"] == "unavailable")
    r = c.post(f"{V}/bookings/{bid}/assessments")
    assert r.status_code == 429 and r.json()["error"]["code"] == "fair_use_limit"
    calls_before = len(env.dvsa.calls)
    env.clock.advance(days=1)
    make_due(env, ("assess_booking",))
    worker.drain(env.ctx)
    assert {b["assessment"]["state"] for b in c.get(f"{V}/bookings").json()["items"]} == {"unavailable"}
    assert len(env.dvsa.calls) - calls_before == 2                                   # deferred ones ran exactly once
    assert c.get(f"{V}/usage").json()["assessments_used"] == 2                       # new London day


def test_default_fair_use_limit_is_50():
    from garage.api.config import Settings
    assert Settings().fair_use_assessments_per_day == 50


def test_refresh_creates_new_frozen_version(env):
    env.dvsa.set_history("AB12CDE", history("AB12CDE", [("2023-01-01", "PASSED")]))
    c = make_workshop(env, "A", "o@a.example")
    bid = import_rows(c, [("AB12CDE", day(3), "mot")])["created_booking_ids"][0]
    worker.drain(env.ctx)
    v1 = booking(c, bid)["assessment"]
    env.provider.result = ok()
    r = c.post(f"{V}/bookings/{bid}/assessments")
    assert r.status_code == 202 and r.json()["version"] == 2 and r.json()["trigger"] == "refresh"
    assert c.post(f"{V}/bookings/{bid}/assessments").status_code == 409                 # one pending at a time
    worker.drain(env.ctx)
    items = c.get(f"{V}/bookings/{bid}/assessments").json()["items"]
    assert [i["version"] for i in items] == [2, 1] and items[0]["state"] == "available"
    assert items[1] == v1                                                              # v1 untouched
    with pytest.raises(Exception, match="frozen"):
        sql(env, "UPDATE assessments SET state = 'failed' WHERE version = 1")
    with pytest.raises(Exception, match="cannot be deleted"):
        sql(env, "DELETE FROM assessments WHERE version = 1")


def test_cancelled_booking_never_calls_dvsa(env):
    c = make_workshop(env, "A", "o@a.example")
    bid = import_rows(c, [("AB12CDE", day(3), "mot")])["created_booking_ids"][0]
    assert c.patch(f"{V}/bookings/{bid}", json={"status": "cancelled"}).json()["status"] == "cancelled"
    worker.drain(env.ctx)
    assert env.dvsa.calls == []
    assert booking(c, bid)["assessment"]["reason"] == "booking_cancelled"
    assert c.post(f"{V}/bookings/{bid}/assessments").status_code == 409
    assert c.patch(f"{V}/bookings/{bid}", json={"appointment_date": str(day(5))}).status_code == 409


# ------------------------------------------------------------------ queue + throttle
def _insert_jobs(env, n):
    wid = make_workshop(env, "Q", "q@q.example").get(f"{V}/me").json()["workshop"]["id"]
    sql(env, "INSERT INTO jobs (workshop_id, kind, payload) SELECT %s, 'assess_booking', '{}'::jsonb FROM generate_series(1, %s)", (wid, n))


def test_skip_locked_each_job_claimed_exactly_once(env):
    _insert_jobs(env, 60)
    claimed, lock = [], threading.Lock()

    def worker_loop(i):
        while True:
            j = jobs.claim_one(env.ctx.db, f"w{i}", 600)
            if j is None:
                return
            with lock:
                claimed.append(j["id"])
    ts = [threading.Thread(target=worker_loop, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(claimed) == 60 and len(set(claimed)) == 60
    assert sql(env, "SELECT count(*) AS n FROM jobs WHERE status='running' AND attempts=1")[0]["n"] == 60


def test_claim_skips_future_jobs_and_recovers_dead_workers(env):
    _insert_jobs(env, 2)
    sql(env, "UPDATE jobs SET run_at = now() + interval '1 hour' WHERE id = (SELECT min(id) FROM jobs)")
    j = jobs.claim_one(env.ctx.db, "w", 600)
    assert j and jobs.claim_one(env.ctx.db, "w", 600) is None
    sql(env, "UPDATE jobs SET locked_at = now() - interval '20 minutes' WHERE id = %s", (j["id"],))
    again = jobs.claim_one(env.ctx.db, "w2", 600)                                      # visibility timeout expired
    assert again["id"] == j["id"] and again["attempts"] == 2


def test_dvsa_throttle_never_exceeds_5_per_second_across_workers(env):
    th = DbThrottle(env.ctx.db, rps=5, sleep=lambda s: None)
    slots, lock = [], threading.Lock()

    def go():
        for _ in range(4):
            _, slot = th.reserve_slot()
            with lock:
                slots.append(slot)
    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    slots.sort()
    assert len(slots) == 32
    for i in range(len(slots) - 5):
        assert (slots[i + 5] - slots[i]).total_seconds() > 1.0, "6 slots inside one second"


def test_throttled_client_really_waits(env):
    th = DbThrottle(env.ctx.db, rps=20)                                                # 55 ms spacing
    env.dvsa.set_history("AB12CDE", history("AB12CDE", []))
    t = ThrottledDvsa(env.dvsa, th)
    t0 = time.monotonic()
    for _ in range(8):
        t.fetch_history("AB12CDE")
    assert time.monotonic() - t0 >= 7 * 0.05
    assert len(env.dvsa.calls) == 8


def test_every_attempt_goes_through_the_throttle(env):
    """Retries are separate fetches, each paying a slot (the worker's dvsa is the throttled client)."""
    th = DbThrottle(env.ctx.db, rps=1000, sleep=lambda s: None)
    reserved = []
    orig = th.reserve_slot
    th.reserve_slot = lambda: (reserved.append(1), orig())[1]
    env.ctx.dvsa = ThrottledDvsa(env.dvsa, th)
    env.dvsa.errors["DD22DDD"] = DvsaTransientError
    c = make_workshop(env, "A", "o@a.example")
    import_rows(c, [("DD22DDD", day(2), "mot")])
    worker.drain(env.ctx)
    assert env.dvsa.calls.count("DD22DDD") == 3 and len(reserved) == 3
