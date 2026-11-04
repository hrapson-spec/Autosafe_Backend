"""Migrations 0003 onwards on a NON-EMPTY database.

The PR 57 commit report flagged that the CHECK constraints in 0003 had only been exercised on empty tables. This
test builds a database in the state before 0003 (migrations 0001 and 0002 applied), fills it with one row for each
state the pre-0003 application could write, then applies every later migration through the application's own
`migrate()` and asserts success, row preservation, NULL in the new columns, validated constraints, and that the
constraints bite on new rows afterwards. Synthetic data only.

How the rows are written: through the application's own `Scoped` store methods where the SQL they run exists
before 0003 (workshops/users via auth_store.signup, bookings, events, create_assessment, outcomes, jobs,
decisions). `Scoped.complete_assessment` names the shadow_* columns and `Scoped.insert_snapshot` names the
raw_response columns, so they cannot run before 0003/0004; the two helpers below are verbatim copies of the
pre-0003 SQL of those methods (git: 1efd040^ store.py).
"""
import dataclasses
import itertools
import uuid
from datetime import date, datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
import pytest
from psycopg2.extras import Json

from garage.api import auth_store, db as dbmod
from garage.api.provider import STUB_DISPLAY_POLICY
from garage.api.scope import Scope
from garage.api.store import Scoped
from .conftest import _with_db

_n = itertools.count()
PRE = "0003_"        # first migration under test


def _complete_assessment_pre_0003(s, assessment_id, state, reason=None, reason_text=None, result=None, evidence=None,
                                  snapshot_id=None, provider_name=None, provider_version=None, display_policy=None,
                                  rank_key=None, score_unrounded=None):
    return s._one(
        """UPDATE assessments SET state = %s, reason = %s, reason_text = %s, result = %s, evidence = %s,
                  snapshot_id = %s, provider_name = %s, provider_version = %s, retry_at = NULL,
                  display_policy = %s, rank_key = %s, score_unrounded = %s, completed_at = now()
            WHERE workshop_id = %s AND id = %s AND state = 'pending' RETURNING *""",
        (state, reason, reason_text, Json(result) if result is not None else None,
         Json(evidence) if evidence is not None else None, snapshot_id, provider_name, provider_version,
         Json(display_policy) if display_policy is not None else None, rank_key, score_unrounded,
         s.wid, assessment_id))


def _insert_snapshot_pre_0004(s, booking_id, kind, upstream_status, history, sha):
    return s._one(
        """INSERT INTO source_snapshots (workshop_id, booking_id, kind, upstream_status, history, content_sha256)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (s.wid, booking_id, kind, upstream_status, Json(history) if history is not None else None, sha))


@pytest.fixture()
def pre_0003_db(pg_admin):
    """(dsn, admin cursor): an empty database migrated to the state just before 0003."""
    uri, admin = pg_admin
    name = f"mig_{next(_n)}_{uuid.uuid4().hex[:6]}"
    cur = admin.cursor()
    cur.execute(f'CREATE DATABASE "{name}"')
    dsn = _with_db(uri, name)
    conn = psycopg2.connect(dsn)
    try:
        c = conn.cursor()
        c.execute("CREATE TABLE garage_schema_migrations (version text PRIMARY KEY, "
                  "applied_at timestamptz NOT NULL DEFAULT now())")
        for f in dbmod.migration_files():
            if f.name >= PRE:
                break
            c.execute(f.read_text())
            c.execute("INSERT INTO garage_schema_migrations (version) VALUES (%s)", (f.stem,))
        conn.commit()
    finally:
        conn.close()
    yield dsn
    cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _populate(dsn):
    """One row per pre-0003 state. Returns the ids needed by the assertions."""
    db = dbmod.Database(dsn, 1, 2)
    ids = {}
    try:
        with db.tx() as cur:
            code = auth_store.create_invite(cur, "t", 14)
            assert auth_store.signup(cur, code, "Mig Motors", "o@mig.example", 30)
            cur.execute("SELECT m.id AS mid, m.workshop_id AS wid, m.user_id AS uid FROM memberships m")
            m = cur.fetchone()
            scope = Scope(workshop_id=str(m["wid"]), membership_id=str(m["mid"]), user_id=str(m["uid"]),
                          email="o@mig.example", role="owner")
            s = Scoped(cur, scope)
            appt = date(2026, 11, 20)
            now = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
            plans = {}
            # bookings: every type, one cancelled, one timed
            for i, btype in enumerate(("mot", "service", "retest", "service_mot", "mot")):
                b = s.insert_booking(f"MG{i}ABC", appt + timedelta(days=i), btype, "manual" if i % 2 else "import",
                                     appointment_time=None if i != 4 else datetime(2026, 1, 1, 9, 30).time())
                plans[i] = str(b["id"])
                s.add_event(plans[i], "created", {"i": i})
            s.add_event(plans[1], "rescheduled", {"from": "2026-11-20"})
            cur.execute("UPDATE bookings SET status='cancelled' WHERE id = %s", (plans[3],))
            s.add_event(plans[3], "cancelled", {})
            # source snapshots: both kinds, both upstream statuses
            hist = {"registration": "MG0ABC", "tests": []}
            snaps = [_insert_snapshot_pre_0004(s, plans[0], "assessment_input", "ok", hist, "a" * 64),
                     _insert_snapshot_pre_0004(s, plans[0], "outcome_check", "ok", hist, "b" * 64),
                     _insert_snapshot_pre_0004(s, plans[1], "assessment_input", "not_found", None, "c" * 64)]
            ids["snaps"] = [str(x["id"]) for x in snaps]
            # assessments: every state, with the display-policy shapes the pre-0003 app wrote
            sim = dataclasses.replace(STUB_DISPLAY_POLICY, display_state="simulated", suppression_reason=None,
                                      ranking_enabled=True).to_json()
            bands = dataclasses.replace(STUB_DISPLAY_POLICY, display_state="bands", suppression_reason=None,
                                        ranking_enabled=False).to_json()
            pend = s.create_assessment(plans[0], "import", appt, now)                       # stays pending
            pend2 = s.create_assessment(plans[1], "import", appt, now, reason="provider_retry",
                                        reason_text="retrying", retry_at=now + timedelta(minutes=5))
            avail = s.create_assessment(plans[2], "import", appt, now)
            _complete_assessment_pre_0003(s, avail["id"], "available", result={"percent_rounded": 25, "band": "medium"},
                                          evidence={"e": 1}, snapshot_id=snaps[0]["id"], provider_name="t",
                                          provider_version="1", display_policy=sim, rank_key=3, score_unrounded=0.25)
            avail_bands = s.create_assessment(plans[4], "manual_entry", appt, now)
            _complete_assessment_pre_0003(s, avail_bands["id"], "available", result={"percent_rounded": None, "band": "low"},
                                          snapshot_id=snaps[0]["id"], provider_name="t", provider_version="1",
                                          display_policy=bands, score_unrounded=0.1)
            for bid, state, why in ((plans[0], "unavailable", "model_not_packaged"),
                                    (plans[0], "unsupported", "no_prior_test"), (plans[0], "failed", "provider_error")):
                a = s.create_assessment(bid, "refresh", appt, now)
                _complete_assessment_pre_0003(s, a["id"], state, why, "text", snapshot_id=snaps[0]["id"],
                                              provider_name="stub", provider_version="0",
                                              display_policy=STUB_DISPLAY_POLICY.to_json())
            ids["assessments"] = 7
            # decisions (append-only table) referencing an available assessment, and a superseding one
            d1 = s.insert_decision(plans[2], "check_repair_history", "n", str(avail["id"]), None)
            s.insert_decision(plans[2], "proceed_as_booked", None, None, str(d1["id"]))
            # outcomes: every status
            statuses = ("scheduled", "matched", "no_subsequent_test", "ambiguous", "not_applicable", "cancelled",
                        "failed")
            for k, (day_, st) in enumerate(zip((1, 3, 7, 14, 1, 3, 7), statuses)):
                bid = plans[0] if k < 4 else plans[1]
                o = s.schedule_outcome(bid, day_, appt, now)
                if st != "scheduled":
                    s.complete_outcome(o["id"], st, {"result": "PASSED"} if st == "matched" else None,
                                       [{"result": "PASSED"}] if st in ("matched", "ambiguous") else None, "d",
                                       snaps[1]["id"])
            # jobs: every kind and status the worker writes
            s.enqueue("assess_booking", {"booking_id": plans[0]}, dedupe_key="k1")
            s.enqueue("outcome_check", {"booking_id": plans[0]}, dedupe_key="k2")
            cur.execute("UPDATE jobs SET status='done', finished_at=now() WHERE dedupe_key='k2'")
            cur.execute("INSERT INTO jobs (workshop_id, kind, payload) VALUES (NULL, 'retention_sweep', '{}')")
            ids["bookings"] = plans
    finally:
        db.close()
    return ids


TABLES = ("workshops", "users", "memberships", "invites", "login_tokens", "bookings", "booking_events",
          "source_snapshots", "assessments", "decisions", "outcomes", "jobs")


def _snapshot(dsn, cols_skip=()):
    """{table: sorted list of row dicts as text}, excluding columns added by the migrations under test."""
    conn = psycopg2.connect(dsn)
    out = {}
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        for t in TABLES:
            cur.execute(f"SELECT * FROM {t}")
            rows = [{k: repr(v) for k, v in r.items() if (t, k) not in cols_skip} for r in cur.fetchall()]
            out[t] = sorted(rows, key=lambda r: sorted(r.items()))
    finally:
        conn.close()
    return out


NEW_COLS = {("assessments", "shadow_margin"), ("assessments", "shadow_probability"),
            ("assessments", "shadow_bundle_sha256"), ("assessments", "shadow_model_version"),
            ("source_snapshots", "raw_response"), ("source_snapshots", "raw_sha256"),
            ("assessments", "calibrated_probability"), ("assessments", "calibrated_bundle_sha256")}      # 0005


def test_0003_and_later_apply_to_every_pre_0003_state_and_preserve_rows(pre_0003_db):
    dsn = pre_0003_db
    ids = _populate(dsn)
    before = _snapshot(dsn)
    assert len(before["assessments"]) == ids["assessments"] and len(before["outcomes"]) == 7
    assert {r["state"] for r in [dict(x) for x in before["assessments"]]} == {
        repr(s) for s in ("pending", "available", "unavailable", "unsupported", "failed")}
    assert len(before["bookings"]) == 5 and len(before["source_snapshots"]) == 3

    expected = [f.stem for f in dbmod.migration_files() if f.name >= PRE]
    assert expected[:2] == ["0003_shadow_assessment_columns", "0004_source_snapshot_raw_response"]
    assert dbmod.migrate(dsn) == expected              # success: no exception, exactly the later migrations
    assert dbmod.migrate(dsn) == []                    # and idempotent

    after = _snapshot(dsn, cols_skip=NEW_COLS)
    assert after == before                             # every pre-existing row, every pre-existing column, unchanged

    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT count(*) AS n, count(shadow_margin) + count(shadow_probability) + "
                    "count(shadow_bundle_sha256) + count(shadow_model_version) AS shadow FROM assessments")
        r = cur.fetchone()
        assert r["n"] == ids["assessments"] and r["shadow"] == 0
        cur.execute("SELECT count(*) AS n, count(raw_response) + count(raw_sha256) AS raw FROM source_snapshots")
        r = cur.fetchone()
        assert r["n"] == 3 and r["raw"] == 0
        cur.execute("""SELECT conname, convalidated FROM pg_constraint
                        WHERE conname IN ('assessments_shadow_not_when_available', 'assessments_shadow_ranking_disabled',
                                          'source_snapshots_raw_hash_matches')""")
        got = {x["conname"]: x["convalidated"] for x in cur.fetchall()}
        assert got == {"assessments_shadow_not_when_available": True, "assessments_shadow_ranking_disabled": True,
                       "source_snapshots_raw_hash_matches": True}
        # 0005 (T1b): new columns NULL on every pre-existing row, constraints validated; 0006: empty monitoring_runs table
        cur.execute("SELECT count(calibrated_probability) + count(calibrated_bundle_sha256) AS c FROM assessments")
        assert cur.fetchone()["c"] == 0
        cur.execute("""SELECT conname, convalidated FROM pg_constraint WHERE conname LIKE 'assessments_calibrated_%%'""")
        assert {x["conname"]: x["convalidated"] for x in cur.fetchall()} == {
            "assessments_calibrated_in_range": True, "assessments_calibrated_pair": True,
            "assessments_calibrated_bundle_hash": True, "assessments_calibrated_only_when_scored": True}
        cur.execute("SELECT count(*) AS n FROM monitoring_runs")
        assert cur.fetchone()["n"] == 0
    finally:
        conn.close()


def test_constraints_bite_on_new_rows_after_upgrade_and_pre_existing_rows_stay_readable(pre_0003_db):
    dsn = pre_0003_db
    ids = _populate(dsn)
    dbmod.migrate(dsn)
    db = dbmod.Database(dsn, 1, 2)
    try:
        with db.tx() as cur:
            cur.execute("SELECT m.id AS mid, m.workshop_id AS wid FROM memberships m")
            m = cur.fetchone()
            s = Scoped(cur, Scope(workshop_id=str(m["wid"]), membership_id=str(m["mid"]), user_id=None, email=None,
                                  role="owner"))
            bid = ids["bookings"][0]
            # the current store functions work on the upgraded, non-empty database
            snap = s.insert_snapshot(bid, "assessment_input", "ok", {"tests": []}, "d" * 64, raw=b"{}")
            a = s.create_assessment(bid, "refresh", date(2026, 11, 20), datetime(2026, 11, 1, tzinfo=timezone.utc))
            assert a["version"] == 5          # booking 0 already has four versions
            shadow_pol = dataclasses.replace(STUB_DISPLAY_POLICY, display_state="shadow",
                                             suppression_reason="model_not_qualified", ranking_enabled=False)
            done = s.complete_assessment(a["id"], "unavailable", "model_not_qualified", snapshot_id=snap["id"],
                                         provider_name="t", provider_version="1", display_policy=shadow_pol.to_json(),
                                         shadow={"margin": 0.1, "probability": 0.3, "bundle_sha256": "e" * 64,
                                                 "model_version": "v"})
            assert done["shadow_probability"] == 0.3
        # the new constraints reject what they exist to reject
        for stmt, args in (
            ("UPDATE assessments SET shadow_margin = 0.1 WHERE id = (SELECT id FROM assessments WHERE state='pending' LIMIT 1)", ()),
            ("INSERT INTO source_snapshots (workshop_id, booking_id, kind, upstream_status, content_sha256, "
             "raw_response, raw_sha256) SELECT workshop_id, id, 'outcome_check', 'ok', 'x', '\\x00', 'bad' FROM bookings LIMIT 1", ()),
        ):
            with pytest.raises(psycopg2.errors.CheckViolation):
                with db.tx() as cur:
                    cur.execute(stmt, args)
    finally:
        db.close()
