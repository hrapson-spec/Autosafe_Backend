"""GET /bookings/{id}/briefing and PUT /bookings/{id}/feedback through the real app, worker and database,
with the 50 raw-shape fixtures behind the fake DVSA client. Cross-workshop denial for both endpoints is in
test_isolation.py (every id endpoint) and again here for feedback rows."""
import json
import re

import pytest

from garage.api import briefing as B
from garage.api import retention, worker
from garage.api.dvsa import DvsaNotFound, DvsaTransientError
from garage.api.security import sha256_json
from .conftest import add_staff_and_sign_in, available, day, history, import_rows, make_workshop, sql

V = "/api/garage/v1"
REG = "ZZ03FXT"        # a real-shape fixture history (8 tests)


def brief(c, bid):
    r = c.get(f"{V}/bookings/{bid}/briefing")
    assert r.status_code == 200, r.text
    return r.json()


def setup(env, reg=REG, typ="mot", appt=None, provider_result=None):
    c = make_workshop(env, "A", "o@a.example")
    if provider_result is not None:
        env.provider.result = provider_result
    bid = import_rows(c, [(reg, appt or day(3), typ)])["created_booking_ids"][0]
    return c, bid


def keys(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from keys(v)
    elif isinstance(o, list):
        for v in o:
            yield from keys(v)


def test_pending_then_ready_from_the_stored_snapshot_with_the_stub_model(env):
    c, bid = setup(env)
    p = brief(c, bid)
    assert p["status"] == "pending" and p["source_records"] == [] and p["briefing_version"] is None
    worker.drain(env.ctx)
    assert c.get(f"{V}/bookings/{bid}").json()["assessment"]["state"] == "unavailable"     # no model estimate exists...
    b = brief(c, bid)                                                                      # ...and the briefing does
    assert b["status"] == "ready" and b["assessment_version"] == 1 and b["rule_table_version"] == B.RULE_TABLE_VERSION
    assert 1 <= len(b["source_records"]) <= 5 and b["summary"].startswith("Recorded before")
    snap = sql(env, "SELECT content_sha256, history FROM source_snapshots")[0]
    assert b["source"]["snapshot_sha256"] == snap["content_sha256"] and b["source"]["upstream_status"] == "ok"
    assert b["briefing_version"] == f"{snap['content_sha256'][:16]}.{B.RULE_TABLE_VERSION}"
    assert b["history_cutoff_date"] == str(day(0))                 # min(import day, appointment day 3 days ahead)
    assert all(r["test_date"] < b["history_cutoff_date"] for r in b["source_records"])


def test_get_never_calls_dvsa_and_reads_only_the_stored_snapshot(env):
    c, bid = setup(env)
    worker.drain(env.ctx)
    first = brief(c, bid)
    calls = len(env.dvsa.calls)
    env.dvsa.set_history(REG, history(REG, [("2000-01-01", "PASSED")]))           # upstream changes afterwards
    again = brief(c, bid)
    assert again == first and len(env.dvsa.calls) == calls


def test_briefing_is_independent_of_the_model_result(env):
    c, bid = setup(env, provider_result=available(35))
    worker.drain(env.ctx)
    with_model = brief(c, bid)
    assert c.get(f"{V}/bookings/{bid}").json()["assessment"]["result"]["percent_rounded"] == 35
    c2 = make_workshop(env, "B", "o@b.example")
    env.provider.result = RuntimeError("boom")                                     # provider crashes -> assessment failed
    bid2 = import_rows(c2, [(REG, day(3), "mot")])["created_booking_ids"][0]
    worker.drain(env.ctx)
    assert c2.get(f"{V}/bookings/{bid2}").json()["assessment"]["state"] == "failed"
    after_failure = brief(c2, bid2)
    assert after_failure["status"] == "ready"
    for field in ("summary", "source_records", "evidence_gaps", "follow_up_questions", "briefing_version"):
        assert after_failure[field] == with_model[field]


def test_no_score_or_explanation_fields_and_no_percent_signs(env):
    c, bid = setup(env, provider_result=available(35, score=0.3517))
    worker.drain(env.ctx)
    b = brief(c, bid)
    assert not ({"percent", "percent_rounded", "band", "score", "rank_key", "rank_score", "probability", "explanation",
                 "display_policy"} & set(keys(b)))
    blob = json.dumps(b)
    assert "0.3517" not in blob and "35%" not in blob
    text = " ".join([b["summary"]] + [g["text"] for g in b["evidence_gaps"]] + [q["text"] for q in b["follow_up_questions"]])
    assert not re.search(r"%|estimate|score|risk|probab|because", text, re.I)


@pytest.mark.parametrize("typ,reason", [("service", "service_only"), ("retest", "retest_booking")])
def test_unsupported_booking_types_have_no_briefing(env, typ, reason):
    c, bid = setup(env, typ=typ)
    b = brief(c, bid)
    assert (b["status"], b["reason"]) == ("unavailable", reason) and b["reason_text"]
    assert b["source_records"] == [] and b["briefing_version"] is None and b["feedback"] is None


def test_cancelled_before_the_assessment_ran_is_unavailable(env):
    c, bid = setup(env)
    assert c.patch(f"{V}/bookings/{bid}", json={"status": "cancelled"}).status_code == 200
    worker.drain(env.ctx)
    b = brief(c, bid)
    assert (b["status"], b["reason"]) == ("unavailable", "booking_cancelled")


def test_upstream_failure_is_unavailable_not_an_empty_ready_briefing(env):
    env.dvsa.errors["AB12CDE"] = DvsaTransientError
    c, bid = setup(env, reg="AB12CDE")
    worker.drain(env.ctx)
    worker.drain(env.ctx)
    b = brief(c, bid)
    assert b["status"] == "unavailable" and b["reason"] in ("upstream_unavailable", "upstream_retrying")


def test_vehicle_not_found_is_ready_with_an_explicit_gap(env):
    env.dvsa.errors["QQ99QQQ"] = DvsaNotFound
    c, bid = setup(env, reg="QQ99QQQ")
    worker.drain(env.ctx)
    b = brief(c, bid)
    assert b["status"] == "ready" and b["source_records"] == [] and b["source"]["upstream_status"] == "not_found"
    assert [g["rule_id"] for g in b["evidence_gaps"]] == ["G01_vehicle_not_found"]
    assert c.get(f"{V}/bookings/{bid}").json()["assessment"]["reason"] == "no_mot_history"


def test_history_only_after_the_cutoff_gives_g02_and_hides_the_later_tests(env):
    env.dvsa.set_history("AB12CDE", history("AB12CDE", [(str(day(0)), "PASSED"), (str(day(1)), "FAILED")]))
    c, bid = setup(env, reg="AB12CDE", appt=day(5))
    worker.drain(env.ctx)
    b = brief(c, bid)
    assert b["status"] == "ready" and b["source_records"] == []
    assert [g["rule_id"] for g in b["evidence_gaps"]] == ["G02_no_prior_test"]
    assert c.get(f"{V}/bookings/{bid}").json()["assessment"]["reason"] == "no_prior_mot"


def test_cutoff_is_the_assessments_not_the_viewing_time(env):
    """Import with an appointment 10 days ago: tests after that day must stay hidden however late it is viewed."""
    env.dvsa.set_history("AB12CDE", history("AB12CDE", [(str(day(-400)), "PASSED"), (str(day(-30)), "PASSED"),
                                                         (str(day(-8)), "FAILED"), (str(day(-2)), "PASSED")]))
    c, bid = setup(env, reg="AB12CDE", appt=day(-10))
    worker.drain(env.ctx)
    env.clock.advance(days=40)
    b = brief(c, bid)
    assert b["history_cutoff_date"] == str(day(-10))
    assert [r["test_date"] for r in b["source_records"]] == [str(day(-30)), str(day(-400))]


def test_refresh_makes_a_new_version_and_same_snapshot_means_same_briefing(env):
    c, bid = setup(env)
    worker.drain(env.ctx)
    v1 = brief(c, bid)
    assert c.post(f"{V}/bookings/{bid}/assessments").status_code == 202
    assert brief(c, bid)["assessment_version"] == 1                       # v2 still pending: v1's snapshot is shown
    worker.drain(env.ctx)
    v2 = brief(c, bid)
    assert v2["assessment_version"] == 2 and v2["assessment_id"] != v1["assessment_id"]
    same = sql(env, "SELECT count(DISTINCT content_sha256) AS n FROM source_snapshots")[0]["n"]
    assert same == 1                                                       # identical history -> identical snapshot hash
    for field in ("briefing_version", "summary", "source_records", "evidence_gaps", "follow_up_questions"):
        assert v2[field] == v1[field]
    # changed history -> new snapshot hash -> new briefing version
    env.dvsa.set_history(REG, history(REG, [("2020-01-01", "PASSED"), ("2021-01-01", "PASSED")]))
    c.post(f"{V}/bookings/{bid}/assessments")
    worker.drain(env.ctx)
    v3 = brief(c, bid)
    assert v3["assessment_version"] == 3 and v3["briefing_version"] != v2["briefing_version"]


def test_unknown_booking_is_404(env):
    c = make_workshop(env, "A", "o@a.example")
    assert c.get(f"{V}/bookings/00000000-0000-4000-8000-000000000000/briefing").status_code == 404
    r = c.put(f"{V}/bookings/00000000-0000-4000-8000-000000000000/feedback", json={"value": "helpful"})
    assert r.status_code == 404


# ------------------------------------------------------------------ feedback
def ready(env):
    c, bid = setup(env)
    worker.drain(env.ctx)
    return c, bid


def test_feedback_needs_a_briefing(env):
    c, bid = setup(env)
    r = c.put(f"{V}/bookings/{bid}/feedback", json={"value": "helpful"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "briefing_not_ready"
    assert sql(env, "SELECT count(*) AS n FROM briefing_feedback")[0]["n"] == 0


def test_feedback_roundtrip_replace_and_reason_rules(env):
    c, bid = ready(env)
    b = brief(c, bid)
    assert b["feedback"] is None
    r = c.put(f"{V}/bookings/{bid}/feedback", json={"value": "helpful", "briefing_version": b["briefing_version"]})
    assert r.status_code == 200
    assert (r.json()["value"], r.json()["reason_code"], r.json()["assessment_version"]) == ("helpful", None, 1)
    assert r.json()["briefing_version"] == b["briefing_version"]
    assert brief(c, bid)["feedback"]["value"] == "helpful"
    r = c.put(f"{V}/bookings/{bid}/feedback", json={"value": "not_helpful", "reason_code": "too_long"})
    assert r.status_code == 200 and r.json()["reason_code"] == "too_long"
    assert sql(env, "SELECT count(*) AS n FROM briefing_feedback")[0]["n"] == 1                 # replaced, not added
    r = c.put(f"{V}/bookings/{bid}/feedback", json={"value": "helpful"})
    assert r.status_code == 200 and r.json()["reason_code"] is None                              # reason cleared
    assert sql(env, "SELECT reason_code FROM briefing_feedback")[0]["reason_code"] is None
    for bad in ({"value": "helpful", "reason_code": "too_long"}, {"value": "meh"}, {"value": "not_helpful", "reason_code": "x"},
                {"value": "helpful", "extra": 1}, {}):
        assert c.put(f"{V}/bookings/{bid}/feedback", json=bad).status_code == 422, bad
    assert sql(env, "SELECT count(*) AS n FROM briefing_feedback")[0]["n"] == 1


def test_feedback_is_one_per_member_and_private_to_that_member(env):
    c, bid = ready(env)
    staff = add_staff_and_sign_in(env, c, "s@a.example")
    c.put(f"{V}/bookings/{bid}/feedback", json={"value": "helpful"})
    assert brief(staff, bid)["feedback"] is None                                   # not the owner's
    staff.put(f"{V}/bookings/{bid}/feedback", json={"value": "not_helpful", "reason_code": "not_relevant"})
    assert brief(c, bid)["feedback"]["value"] == "helpful"
    assert brief(staff, bid)["feedback"]["reason_code"] == "not_relevant"
    rows = sql(env, "SELECT membership_id, value FROM briefing_feedback ORDER BY value")
    assert len(rows) == 2 and len({r["membership_id"] for r in rows}) == 2


def test_feedback_attaches_to_the_booking_version_not_the_booking(env):
    c, bid = ready(env)
    c.put(f"{V}/bookings/{bid}/feedback", json={"value": "not_helpful"})
    c.post(f"{V}/bookings/{bid}/assessments")
    worker.drain(env.ctx)
    assert brief(c, bid)["assessment_version"] == 2 and brief(c, bid)["feedback"] is None
    c.put(f"{V}/bookings/{bid}/feedback", json={"value": "helpful"})
    rows = sql(env, "SELECT a.version, f.value FROM briefing_feedback f JOIN assessments a ON a.id = f.assessment_id "
                    "ORDER BY a.version")
    assert [(r["version"], r["value"]) for r in rows] == [(1, "not_helpful"), (2, "helpful")]


def test_feedback_for_a_stale_briefing_version_is_refused(env):
    c, bid = ready(env)
    r = c.put(f"{V}/bookings/{bid}/feedback", json={"value": "helpful", "briefing_version": "0" * 16 + ".briefing-rules-0"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "briefing_version_changed"
    assert sql(env, "SELECT count(*) AS n FROM briefing_feedback")[0]["n"] == 0


def test_other_workshop_cannot_read_or_write_feedback_rows(env):
    a, bid = ready(env)
    b = make_workshop(env, "B", "o@b.example")
    a.put(f"{V}/bookings/{bid}/feedback", json={"value": "helpful"})
    for r in (b.get(f"{V}/bookings/{bid}/briefing"), b.put(f"{V}/bookings/{bid}/feedback", json={"value": "not_helpful"})):
        assert r.status_code == 404
    assert [r["value"] for r in sql(env, "SELECT value FROM briefing_feedback")] == ["helpful"]
    # the database itself refuses a feedback row whose membership belongs to another workshop
    wb = b.get(f"{V}/me").json()
    mb = wb["membership_id"]
    row = sql(env, "SELECT workshop_id, booking_id, assessment_id FROM briefing_feedback")[0]
    with pytest.raises(Exception, match="foreign key"):
        sql(env, "INSERT INTO briefing_feedback (workshop_id, booking_id, assessment_id, membership_id, value, briefing_version) "
                 "VALUES (%s, %s, %s, %s, 'helpful', 'x')", (row["workshop_id"], row["booking_id"], row["assessment_id"], mb))


def test_retention_purge_removes_feedback_with_its_booking(env):
    c, bid = ready(env)
    c.put(f"{V}/bookings/{bid}/feedback", json={"value": "helpful"})
    retention.purge(env.ctx.db, 12, day(800))
    assert sql(env, "SELECT count(*) AS n FROM briefing_feedback")[0]["n"] == 0
    assert sql(env, "SELECT count(*) AS n FROM bookings")[0]["n"] == 0
