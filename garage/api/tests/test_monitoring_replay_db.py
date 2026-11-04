"""monitor-g6-replay end to end on a migrated fixture PostgreSQL, plus migrations 0005 (score provenance) and 0006
(monitoring_runs). Synthetic data and a FAKE features-mode scorer only (see test_monitoring_replay.py). The policy values are
TEST values: open decisions are not answered by them."""
import copy
import dataclasses
import hashlib
import json
from datetime import timedelta

import psycopg2
import pytest

from garage.api import cli, worker
from garage.api.dvsa import parse_api_response
from garage.api.monitoring import dumps_canonical
from garage.api.monitoring.reader import SCORE_COLUMNS, g7_sql, replay_sql
from garage.api.monitoring.runs import FORBIDDEN_KEYS, assert_aggregate_only, record_run
from garage.api.provider import STUB_DISPLAY_POLICY, ProviderResult
from garage.api.providers import ShadowProviderResult
from garage.api.timeutil import utcnow
from .conftest import day, import_rows, make_workshop, sql
from .test_monitoring_replay import (NAMES, SCHEMA_SHA, MAKES, _ref, dev_rows, raw_response, write_bundle)  # noqa: F401

SHADOW_POL = dataclasses.replace(STUB_DISPLAY_POLICY, display_state="shadow", suppression_reason="model_not_qualified",
                                 ranking_enabled=False)


def replay_policy(manifest_sha, ref_sha, **over):
    now = utcnow()
    p = {"kind": "monitor_g6_replay", "score_column": "shadow_probability",
         "g6": {"min_supported": 500, "psi_threshold": 0.10, "max_unsupported_share": 0.30, "smoothing": 0.5},
         "bootstrap": {"n_boot": 0, "seed": 1, "alpha": 0.05}, "reference_sha256": ref_sha,
         "outcome_access_start_utc": "NOT_STARTED", "population": "fixture",
         "window": {"start_utc": (now - timedelta(days=1)).isoformat(), "end_utc": (now + timedelta(days=1)).isoformat()},
         "scope": {"booking_types": ["mot"]},
         "replay": {"timeout_s": 30, "max_output_bytes": 65536, "max_bookings": 100,
                    "unsupported_error_codes": ["row_unsupported"], "bundle_manifest_sha256": manifest_sha,
                    "feature_schema_sha256": SCHEMA_SHA}}
    for k, v in over.items():
        p[k] = v
    return p


def put(tmp_path, name, obj):
    f = tmp_path / name
    f.write_text(json.dumps(obj, indent=1))
    return str(f)


@pytest.fixture()
def ref_file(tmp_path):
    raw = dumps_canonical(_ref().raw)
    f = tmp_path / "ref.json"
    f.write_bytes(raw)
    return str(f), hashlib.sha256(raw).hexdigest()


def seed(env, c, tmp_path, bundle_sha, extra=()):
    """Four scored bookings, one the scorer refuses (row_unsupported), one vehicle DVSA does not know. Returns {reg: score}."""
    appt = day(5)
    specs = [("AA11AAA", "FORD", 3, "PASSED", 40000, 0.21), ("BB22BBB", "VW", 2, "FAILED", 52000, 0.34),
             ("CC33CCC", "BMW", 4, "PASSED", 61000, 0.15), ("DD44DDD", "FORD", 5, "FAILED", 88000, 0.44), *extra]
    scores = {}
    for reg, make, n, last, odo, p in specs:
        raw = raw_response(n, last, make, odo, plate=reg)
        env.dvsa.set_history(reg, parse_api_response(reg, json.loads(raw), raw=raw))
        scores[reg] = p
    raw = raw_response(2, "PASSED", "REFUSE", 30000, plate="EE55EEE")
    env.dvsa.set_history("EE55EEE", parse_api_response("EE55EEE", json.loads(raw), raw=raw))
    regs = [s[0] for s in specs] + ["EE55EEE", "FF66FFF"]            # FF66FFF: not set => DVSA not found => unsupported

    def provider(req):
        if req.registration == "EE55EEE":
            return ProviderResult(state="unavailable", reason="row_unsupported", reason_text="x", provider_name="t",
                                  provider_version="1", display_policy=dataclasses.replace(STUB_DISPLAY_POLICY,
                                                                                        display_state="unavailable"))
        p = scores[req.registration]
        return ShadowProviderResult(state="unavailable", reason="model_not_qualified", provider_name="t", provider_version="1",
                                    display_policy=SHADOW_POL, shadow_margin=0.0, shadow_probability=p,
                                    shadow_bundle_sha256=bundle_sha, shadow_model_version="synthetic-1")
    env.provider.result = provider
    import_rows(c, [(r, appt, "mot") for r in regs])
    worker.drain(env.ctx, kinds=("assess_booking",))
    return scores


def run_cli(args, monkeypatch, env, capsys):
    monkeypatch.setenv("GARAGE_DATABASE_URL", env.dsn)
    cli.main(args)
    cap = capsys.readouterr()
    return json.loads(cap.out), cap.err


def test_replay_end_to_end_counts_records_and_stores_aggregates_only(env, tmp_path, monkeypatch, capsys, ref_file):
    ref_path, ref_sha = ref_file
    b = write_bundle(tmp_path)
    man = json.loads((b / "manifest.json").read_text())["manifest_content_sha256"]
    c = make_workshop(env, "A", "o@a.example")
    seed(env, c, tmp_path, man)
    states = {r["registration"]: (r["state"], r["reason"]) for r in sql(
        env, "SELECT b.registration, a.state, a.reason FROM assessments a JOIN bookings b ON b.id = a.booking_id")}
    assert states["EE55EEE"] == ("unavailable", "row_unsupported") and states["FF66FFF"][0] == "unsupported"
    assert states["AA11AAA"] == ("unavailable", "model_not_qualified")
    assert all(r["raw_response"] for r in sql(env, "SELECT raw_response FROM source_snapshots WHERE upstream_status='ok'"))

    pol = replay_policy(man, ref_sha)
    pp = put(tmp_path, "p.json", pol)
    out, err = run_cli(["monitor-g6-replay", "--policy", pp, "--reference", ref_path, "--bundle", str(b), "--record"],
                       monkeypatch, env, capsys)
    sha = hashlib.sha256((tmp_path / "p.json").read_bytes()).hexdigest()
    assert f"policy_sha256={sha}" in err and out["policy"]["sha256"] == sha
    # 4 scored + 1 refused by the scorer + 1 unknown to DVSA: 6 bookings; 4 supported
    assert (out["n_total"], out["n_supported"], out["n_unsupported"]) == (6, 4, 2)
    assert out["status"] == "insufficient_n" and out["population"] == "fixture"
    rp = out["replay"]
    assert rp["n_candidates"] == 6 and rp["n_replayed"] == 5 and rp["refusals_by_code"] == {"scorer:row_unsupported": 1}
    assert rp["excluded_by_reason"] == {} and rp["bundle_manifest_sha256"] == man and rp["feature_schema_sha256"] == SCHEMA_SHA
    assert rp["score_column"] == "shadow_probability" and rp["subprocess"]["shell"] is False
    assert set(out["psi"]) == set(NAMES) | {"score"}
    # the scorer saw a plate-free request and was run in features mode with the bundle directory as cwd
    assert json.loads((b / "last_argv.json").read_text())[-1] == "--emit-features"
    last = (b / "last_stdin.json").read_text()
    assert "AA11AAA" not in last and "registration" not in last.lower().replace('"registrationdate"', "")

    # nothing but the aggregate row was written
    runs = sql(env, "SELECT * FROM monitoring_runs")
    assert len(runs) == 1 and out["monitoring_run_id"] == str(runs[0]["id"])
    r = runs[0]
    assert (r["kind"], r["policy_sha256"], r["reference_sha256"], r["bundle_manifest_sha256"]) == ("g6_replay", sha, ref_sha, man)
    assert r["window_start"] < r["window_end"]
    stored = json.dumps(r["result"])
    for reg in ("AA11AAA", "BB22BBB", "CC33CCC", "DD44DDD", "EE55EEE", "FF66FFF"):
        assert reg not in stored
    for bid in sql(env, "SELECT id::text AS i, encode(sha256(convert_to(registration,'UTF8')),'hex') AS h FROM bookings"):
        assert bid["i"] not in stored and bid["h"] not in stored
    assert_aggregate_only(r["result"])
    assert r["result"]["n_supported"] == 4 and r["result"]["replay"]["n_candidates"] == 6

    # the stored row is append-only
    with pytest.raises(psycopg2.errors.RaiseException):
        sql(env, "UPDATE monitoring_runs SET kind = 'g7' WHERE id = %s", (r["id"],))
    with pytest.raises(psycopg2.errors.RaiseException):
        sql(env, "DELETE FROM monitoring_runs WHERE id = %s", (r["id"],))

    # without --record nothing is written; the application tables are never changed by the command
    before = sql(env, "SELECT (SELECT count(*) FROM assessments) a, (SELECT count(*) FROM source_snapshots) s, "
                      "(SELECT count(*) FROM bookings) b")[0]
    out2, _ = run_cli(["monitor-g6-replay", "--policy", pp, "--reference", ref_path, "--bundle", str(b)], monkeypatch, env, capsys)
    assert "monitoring_run_id" not in out2 and len(sql(env, "SELECT 1 FROM monitoring_runs")) == 1
    assert sql(env, "SELECT (SELECT count(*) FROM assessments) a, (SELECT count(*) FROM source_snapshots) s, "
                    "(SELECT count(*) FROM bookings) b")[0] == before


def test_the_score_column_is_a_named_choice_and_calibrated_is_empty_until_the_writer_is_applied(env, tmp_path, monkeypatch,
                                                                                                capsys, ref_file):
    ref_path, ref_sha = ref_file
    b = write_bundle(tmp_path)
    man = json.loads((b / "manifest.json").read_text())["manifest_content_sha256"]
    c = make_workshop(env, "A", "o@a.example")
    seed(env, c, tmp_path, man)
    n_cal = sql(env, "SELECT count(*) n FROM assessments WHERE calibrated_probability IS NOT NULL")[0]["n"]
    pp = put(tmp_path, "p.json", replay_policy(man, ref_sha, score_column="calibrated_probability"))
    out, _ = run_cli(["monitor-g6-replay", "--policy", pp, "--reference", ref_path, "--bundle", str(b)], monkeypatch, env, capsys)
    # the writer (REPORT_T1b.md patch) is not part of this tree unless applied: then every scored row has no stored score
    if n_cal == 0:
        assert out["n_supported"] == 0 and out["replay"]["excluded_by_reason"] == {"no_stored_score": 4}
    else:
        assert out["n_supported"] == n_cal


def test_a_stored_score_from_another_bundle_is_never_paired_with_replayed_inputs(env, tmp_path, monkeypatch, capsys, ref_file):
    ref_path, ref_sha = ref_file
    b = write_bundle(tmp_path)
    man = json.loads((b / "manifest.json").read_text())["manifest_content_sha256"]
    c = make_workshop(env, "A", "o@a.example")
    seed(env, c, tmp_path, "9" * 64)                      # scores were produced by a different bundle than the one replayed
    pp = put(tmp_path, "p.json", replay_policy(man, ref_sha))
    out, _ = run_cli(["monitor-g6-replay", "--policy", pp, "--reference", ref_path, "--bundle", str(b)], monkeypatch, env, capsys)
    assert out["n_supported"] == 0 and out["replay"]["excluded_by_reason"] == {"score_from_other_bundle": 4}


@pytest.mark.parametrize("what", ["missing_key", "unknown_key", "wrong_bundle_pin", "wrong_schema_pin", "bad_column",
                                  "changed_gate", "max_bookings", "mixed_schema"])
def test_refusals_exit_2_print_nothing_and_record_nothing(env, tmp_path, monkeypatch, capsys, ref_file, what):
    ref_path, ref_sha = ref_file
    b = write_bundle(tmp_path)
    man = json.loads((b / "manifest.json").read_text())["manifest_content_sha256"]
    c = make_workshop(env, "A", "o@a.example")
    extra = (("GG77GGG", "SCHEMA2", 2, "PASSED", 1000, 0.3),) if what == "mixed_schema" else ()
    seed(env, c, tmp_path, man, extra=extra)
    pol = replay_policy(man, ref_sha)
    if what == "missing_key":
        del pol["replay"]["timeout_s"]
    elif what == "unknown_key":
        pol["replay"]["shell"] = True
    elif what == "wrong_bundle_pin":
        pol["replay"]["bundle_manifest_sha256"] = "1" * 64
    elif what == "wrong_schema_pin":
        pol["replay"]["feature_schema_sha256"] = "1" * 64
    elif what == "bad_column":
        pol["score_column"] = "shadow_probability; DROP TABLE assessments"
    elif what == "changed_gate":
        pol["g6"]["psi_threshold"] = 0.2
    elif what == "max_bookings":
        pol["replay"]["max_bookings"] = 3
    pp = put(tmp_path, "p.json", pol)
    monkeypatch.setenv("GARAGE_DATABASE_URL", env.dsn)
    with pytest.raises(SystemExit) as e:
        cli.main(["monitor-g6-replay", "--policy", pp, "--reference", ref_path, "--bundle", str(b), "--record"])
    cap = capsys.readouterr()
    assert e.value.code == 2 and cap.out == "" and "REFUSED" in cap.err
    assert sql(env, "SELECT 1 FROM monitoring_runs") == []
    for reg in ("AA11AAA", "EE55EEE"):
        assert reg not in cap.err
    assert len(sql(env, "SELECT 1 FROM assessments")) >= 6                        # tables untouched


def test_the_replay_command_refuses_a_policy_of_the_wrong_kind(env, tmp_path, monkeypatch, capsys, ref_file):
    ref_path, ref_sha = ref_file
    b = write_bundle(tmp_path)
    man = json.loads((b / "manifest.json").read_text())["manifest_content_sha256"]
    pp = put(tmp_path, "p.json", replay_policy(man, ref_sha, kind="monitor_g6"))
    monkeypatch.setenv("GARAGE_DATABASE_URL", env.dsn)
    with pytest.raises(SystemExit) as e:
        cli.main(["monitor-g6-replay", "--policy", pp, "--reference", ref_path, "--bundle", str(b)])
    assert e.value.code == 2 and capsys.readouterr().out == ""


# --------------------------------------------------------------------------------------------- SQL / column naming
def test_only_two_literal_score_columns_exist_and_the_policy_selects_one():
    assert set(SCORE_COLUMNS) == {"shadow_probability", "calibrated_probability"}
    for col in SCORE_COLUMNS:
        s = g7_sql(col)
        other = ({"shadow_probability", "calibrated_probability"} - {col}).pop()
        assert f"a.{col} IS NOT NULL" in s and other not in s
        r = replay_sql(col)
        assert f"a.{col} AS score" in r and other not in r
    for bad in ("margin", "a.shadow_probability", "shadow_probability; --", None, ""):
        with pytest.raises(Exception):
            g7_sql(bad)
        with pytest.raises(Exception):
            replay_sql(bad)


@pytest.mark.parametrize("col", ["shadow_probability", "calibrated_probability"])
def test_both_reader_queries_run_on_the_migrated_schema(env, col):
    now = utcnow()
    with env.ctx.db.tx() as cur:
        cur.execute(g7_sql(col), {"check_day": 14, "statuses": ["matched"], "types": ["mot"]})
        assert cur.fetchall() == []
        cur.execute(replay_sql(col), {"start": now - timedelta(days=1), "end": now, "types": ["mot"], "limit": 5})
        assert cur.fetchall() == []


# -------------------------------------------------------------------------------- migration 0005 and 0006 constraints
def _pending(env):
    c = make_workshop(env, "A", "o@a.example")
    from .conftest import history
    env.dvsa.set_history("ZZ00FXT", history("ZZ00FXT", [("2022-01-01", "PASSED")]))
    return import_rows(c, [("ZZ00FXT", day(2), "mot")])["created_booking_ids"][0], c


def test_migration_0005_constraints(env):
    bid, _ = _pending(env)
    done = "state='unavailable', reason='x', completed_at=now()"
    for bad in ("calibrated_probability=1.5, calibrated_bundle_sha256=repeat('a',64)",
                "calibrated_probability=-0.1, calibrated_bundle_sha256=repeat('a',64)",
                "calibrated_probability='NaN', calibrated_bundle_sha256=repeat('a',64)",
                "calibrated_probability=0.3",                                              # probability without bundle hash
                "calibrated_bundle_sha256=repeat('a',64)",                                  # hash without probability
                "calibrated_probability=0.3, calibrated_bundle_sha256='XYZ'"):
        with pytest.raises(psycopg2.errors.CheckViolation):
            sql(env, f"UPDATE assessments SET {done}, {bad} WHERE booking_id=%s", (bid,))
    with pytest.raises(psycopg2.errors.CheckViolation):                       # not for pending / unsupported / failed rows
        sql(env, "UPDATE assessments SET state='unsupported', reason='x', completed_at=now(), calibrated_probability=0.3, "
                 "calibrated_bundle_sha256=repeat('a',64) WHERE booking_id=%s", (bid,))
    sql(env, f"UPDATE assessments SET {done}, calibrated_probability=0.3, calibrated_bundle_sha256=repeat('a',64) "
             "WHERE booking_id=%s", (bid,))
    r = sql(env, "SELECT calibrated_probability p, calibrated_bundle_sha256 h FROM assessments WHERE booking_id=%s", (bid,))[0]
    assert (r["p"], r["h"]) == (0.3, "a" * 64)
    with pytest.raises(psycopg2.errors.RaiseException):                       # frozen once completed (0001), so no back-fill
        sql(env, "UPDATE assessments SET calibrated_probability=0.4 WHERE booking_id=%s", (bid,))


def test_a_scored_value_is_never_reachable_through_the_api(env):
    bid, c = _pending(env)
    p = 0.271828182845
    sql(env, "UPDATE assessments SET state='unavailable', reason='model_not_qualified', completed_at=now(), "
             "display_policy=%s, calibrated_probability=%s, calibrated_bundle_sha256=repeat('c',64) WHERE booking_id=%s",
        (json.dumps(SHADOW_POL.to_json()), p, bid))
    V = "/api/garage/v1"
    for r in (c.get(f"{V}/bookings/{bid}"), c.get(f"{V}/bookings/{bid}/assessments"), c.get(f"{V}/bookings"),
              c.get(f"{V}/bookings/{bid}/briefing"), c.get(f"{V}/bookings/{bid}/decisions"), c.get(f"{V}/bookings/{bid}/outcomes")):
        assert r.status_code == 200, r.text
        low = r.text.lower()
        assert "calibrated" not in low and "271828" not in r.text and "c" * 64 not in r.text


def test_migration_0006_constraints_and_append_only(env):
    base = dict(kind="g7", policy_sha256="a" * 64)
    with env.ctx.db.tx() as cur:
        rid = record_run(cur, result={"n": 1}, **base)
    assert len(sql(env, "SELECT 1 FROM monitoring_runs WHERE id = %s", (rid,))) == 1
    bad_rows = [
        ("INSERT INTO monitoring_runs (kind, policy_sha256, result) VALUES ('other', repeat('a',64), '{}')"),
        ("INSERT INTO monitoring_runs (kind, policy_sha256, result) VALUES ('g7', 'short', '{}')"),
        ("INSERT INTO monitoring_runs (kind, policy_sha256, result) VALUES ('g7', repeat('a',64), '[]')"),
        ("INSERT INTO monitoring_runs (kind, policy_sha256, result, window_start) VALUES ('g7', repeat('a',64), '{}', now())"),
        ("INSERT INTO monitoring_runs (kind, policy_sha256, result, window_start, window_end) "
         "VALUES ('g7', repeat('a',64), '{}', now(), now() - interval '1 day')"),
        ("INSERT INTO monitoring_runs (kind, policy_sha256, result, bundle_manifest_sha256) "
         "VALUES ('g7', repeat('a',64), '{}', 'zz')"),
        ("INSERT INTO monitoring_runs (kind, policy_sha256, result) VALUES ('g6_replay', repeat('a',64), '{}')")]
    for q in bad_rows:
        with pytest.raises(psycopg2.errors.CheckViolation):
            sql(env, q)
    with pytest.raises(psycopg2.errors.RaiseException):
        sql(env, "UPDATE monitoring_runs SET result = '{}' WHERE id = %s", (rid,))
    with pytest.raises(psycopg2.errors.RaiseException):
        sql(env, "DELETE FROM monitoring_runs WHERE id = %s", (rid,))
    with env.ctx.db.tx() as cur:                                        # an explicit retention sweep may delete (convention of 0001)
        cur.execute("SELECT set_config('garage.retention_sweep', 'on', true)")
        cur.execute("DELETE FROM monitoring_runs WHERE id = %s", (rid,))
    assert sql(env, "SELECT 1 FROM monitoring_runs") == []
    cols = {r["column_name"] for r in sql(env, "SELECT column_name FROM information_schema.columns WHERE table_name='monitoring_runs'")}
    assert cols == {"id", "kind", "window_start", "window_end", "policy_sha256", "reference_sha256", "bundle_manifest_sha256",
                    "result", "created_at"}


def test_record_run_refuses_row_level_keys_at_any_depth():
    class Cur:
        def execute(self, *a):
            raise AssertionError("must not reach the database")
    for k in sorted(FORBIDDEN_KEYS):
        for res in ({k: 1}, {"a": {"b": [{k: 1}]}}):
            with pytest.raises(Exception, match="row-level key"):
                record_run(Cur(), kind="g7", result=res, policy_sha256="a" * 64)
    with pytest.raises(Exception):
        record_run(Cur(), kind="nope", result={}, policy_sha256="a" * 64)
