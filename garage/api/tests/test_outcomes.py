from datetime import timedelta


from garage.api import matching, worker
from garage.api.dvsa import DvsaNotFound, DvsaTransientError
from .conftest import day, history, import_rows, make_due, make_workshop, sql

V = "/api/garage/v1"


def outcomes(c, bid):
    return c.get(f"{V}/bookings/{bid}/outcomes").json()["items"]


def setup(env, tests, btype="mot", appt=None, reg="AB12CDE"):
    appt = appt or day(-5)
    env.dvsa.set_history(reg, history(reg, tests))
    c = make_workshop(env, "A", "o@a.example")
    bid = import_rows(c, [(reg, appt, btype)])["created_booking_ids"][0]
    return c, bid, appt


def run_all(env):
    make_due(env)
    worker.drain(env.ctx)


def test_four_checks_scheduled_at_days_1_3_7_14_london_time(env):
    for appt in (day(30), day(200)):                       # different sides of a DST change at least once a year
        c = make_workshop(env, f"W{appt}", f"o{appt}@a.example")
        bid = import_rows(c, [("AB12CDE", appt, "mot")])["created_booking_ids"][0]
        rows = outcomes(c, bid)
        assert [r["check_day"] for r in rows] == [1, 3, 7, 14] and [r["status"] for r in rows] == ["scheduled"] * 4
        assert rows[3]["final"] and not rows[0]["final"]
        for r in rows:
            due = appt + timedelta(days=r["check_day"])
            assert r["due_at"].startswith(f"{due}T07:00:00")
        assert sql(env, "SELECT count(*) AS n FROM jobs WHERE kind='outcome_check' AND payload->>'booking_id' = %s", (bid,))[0]["n"] == 4


def test_no_subsequent_test(env):
    c, bid, appt = setup(env, [(str(day(-400)), "PASSED"), (str(day(-6)), "FAILED")])
    run_all(env)
    rows = outcomes(c, bid)
    assert [r["status"] for r in rows] == ["no_subsequent_test"] * 4 and rows[0]["candidates"] == []
    assert c.get(f"{V}/bookings/{bid}").json()["outcome"]["check_day"] == 14
    assert len(sql(env, "SELECT 1 FROM source_snapshots WHERE kind='outcome_check'")) == 4


def test_single_candidate_matches(env):
    c, bid, appt = setup(env, [(str(day(-400)), "PASSED"), (str(day(-3)), "PASSED", "999999999999")])
    run_all(env)
    r = outcomes(c, bid)[0]
    assert r["status"] == "matched"
    assert r["matched_test"] == {"test_date": str(day(-3)), "result": "PASSED", "test_number": "999999999999"}


def test_multiple_candidates_left_unresolved(env):
    c, bid, appt = setup(env, [(str(day(-4)), "FAILED"), (str(day(-1)), "PASSED")])
    run_all(env)
    for r in outcomes(c, bid):
        assert r["status"] == "ambiguous" and r["matched_test"] is None and len(r["candidates"]) == 2
        assert r["detail"] == "fail_then_later_pass"
    assert c.get(f"{V}/bookings/{bid}").json()["outcome"]["status"] == "ambiguous"


def test_two_passes_in_window_also_ambiguous():
    from datetime import date, datetime
    from garage.api.dvsa import DvsaTest
    mk = lambda d, r: DvsaTest(datetime.fromisoformat(d + "T10:00:00+00:00"), r)
    st, m, c, d = matching.match_outcome([mk("2026-03-01", "PASSED"), mk("2026-03-02", "PASSED")], date(2026, 3, 1), "mot")
    assert (st, m, d) == ("ambiguous", None, "multiple_candidates")


def test_tests_outside_window_are_not_candidates():
    from datetime import date, datetime
    from garage.api.dvsa import DvsaTest
    mk = lambda d, r: DvsaTest(datetime.fromisoformat(d + "T10:00:00+00:00"), r)
    appt = date(2026, 3, 10)
    st, m, c, _ = matching.match_outcome([mk("2026-03-09", "PASSED"), mk("2026-03-25", "PASSED")], appt, "mot")
    assert st == "no_subsequent_test" and c == []
    st, m, c, _ = matching.match_outcome([mk("2026-03-10", "FAILED"), mk("2026-03-24", "PASSED")], appt, "mot")
    assert st == "ambiguous" and len(c) == 2                         # day 0 and day 14 are both inside


def test_rescheduled_booking_matches_against_new_date_only(env):
    old, new = day(-5), day(-2)
    c, bid, _ = setup(env, [(str(old), "PASSED", "111111111111"), (str(day(-1)), "PASSED", "222222222222")], appt=old)
    r = c.patch(f"{V}/bookings/{bid}", json={"appointment_date": str(new)})
    assert r.status_code == 200 and r.json()["rescheduled"] and r.json()["original_appointment_date"] == str(old)
    # pending checks for the old date were cancelled and replaced
    assert sql(env, "SELECT count(*) AS n FROM outcomes WHERE status='cancelled'")[0]["n"] == 4
    assert sql(env, "SELECT count(*) AS n FROM jobs WHERE kind='outcome_check' AND status='cancelled'")[0]["n"] == 4
    assert [r["status"] for r in outcomes(c, bid)] == ["scheduled"] * 4
    run_all(env)
    rows = outcomes(c, bid)
    assert all(x["status"] == "matched" and x["matched_test"]["test_number"] == "222222222222" for x in rows)
    # a stale job from the old schedule is inert
    n = len(env.dvsa.calls)
    sql(env, "INSERT INTO jobs (workshop_id, kind, payload) SELECT workshop_id, 'outcome_check', "
             "jsonb_build_object('booking_id', id, 'check_day', 1, 'schedule_date', %s::text) FROM bookings", (str(old),))
    worker.drain(env.ctx)
    assert len(env.dvsa.calls) == n
    assert sql(env, "SELECT status FROM outcomes WHERE schedule_date = %s AND check_day = 1", (old,))[0]["status"] == "cancelled"
    # rescheduling back revives the schedule
    c.patch(f"{V}/bookings/{bid}", json={"appointment_date": str(old)})
    assert [r["status"] for r in outcomes(c, bid)] == ["scheduled"] * 4


def test_retest_booking_matches_follow_up_but_never_guesses(env):
    c, bid, _ = setup(env, [(str(day(-25)), "FAILED"), (str(day(-3)), "PASSED")], btype="retest")
    run_all(env)
    r = outcomes(c, bid)[0]
    assert r["status"] == "matched" and r["matched_test"]["result"] == "PASSED" and r["detail"] is None
    # no earlier failure on record: still matched on date, but flagged
    c2 = make_workshop(env, "B", "o@b.example")
    env.dvsa.set_history("ZZ19ZZZ", history("ZZ19ZZZ", [(str(day(-400)), "PASSED"), (str(day(-3)), "PASSED")]))
    b2 = import_rows(c2, [("ZZ19ZZZ", day(-5), "retest")])["created_booking_ids"][0]
    run_all(env)
    r2 = outcomes(c2, b2)[0]
    assert r2["status"] == "matched" and r2["detail"] == "no_preceding_fail_found"
    # a retest booking with an earlier failure plus two later tests stays ambiguous
    c3 = make_workshop(env, "C", "o@c.example")
    env.dvsa.set_history("YY19YYY", history("YY19YYY", [(str(day(-25)), "FAILED"), (str(day(-4)), "FAILED"), (str(day(-2)), "PASSED")]))
    b3 = import_rows(c3, [("YY19YYY", day(-5), "retest")])["created_booking_ids"][0]
    run_all(env)
    assert outcomes(c3, b3)[0]["status"] == "ambiguous"


def test_service_only_not_applicable_and_no_jobs(env):
    c, bid, _ = setup(env, [(str(day(-3)), "PASSED")], btype="service")
    rows = outcomes(c, bid)
    assert [r["status"] for r in rows] == ["not_applicable"] * 4
    assert sql(env, "SELECT count(*) AS n FROM jobs WHERE kind='outcome_check'")[0]["n"] == 0


def test_service_plus_mot_is_matched_like_mot(env):
    c, bid, _ = setup(env, [(str(day(-3)), "FAILED")], btype="service_mot")
    run_all(env)
    assert outcomes(c, bid)[0]["status"] == "matched" and outcomes(c, bid)[0]["matched_test"]["result"] == "FAILED"


def test_cancelled_booking_cancels_checks(env):
    c, bid, _ = setup(env, [(str(day(-3)), "PASSED")], appt=day(5))
    c.patch(f"{V}/bookings/{bid}", json={"status": "cancelled"})
    run_all(env)
    assert outcomes(c, bid) == []                                    # cancelled rows are not listed
    assert sql(env, "SELECT count(*) AS n FROM jobs WHERE kind='outcome_check' AND status='queued'")[0]["n"] == 0
    assert env.dvsa.calls == []                                         # nothing is looked up for a cancelled booking


def test_upstream_problems_in_outcome_checks(env):
    c = make_workshop(env, "A", "o@a.example")
    env.dvsa.set_history("OK19OKK", history("OK19OKK", [(str(day(-3)), "PASSED")]))
    env.dvsa.errors["NF19NFF"] = DvsaNotFound
    env.dvsa.errors["TR19TRR"] = DvsaTransientError
    ids = import_rows(c, [(r, day(-5), "mot") for r in ("OK19OKK", "NF19NFF", "TR19TRR")])["created_booking_ids"]
    run_all(env)
    by = {b["registration"]: b for b in c.get(f"{V}/bookings").json()["items"]}
    assert outcomes(c, by["OK19OKK"]["id"])[0]["status"] == "matched"
    nf = outcomes(c, by["NF19NFF"]["id"])[0]
    assert nf["status"] == "no_subsequent_test" and nf["detail"] == "vehicle_not_found"
    tr = outcomes(c, by["TR19TRR"]["id"])[0]
    assert tr["status"] == "failed" and tr["detail"] == "upstream_transient"
