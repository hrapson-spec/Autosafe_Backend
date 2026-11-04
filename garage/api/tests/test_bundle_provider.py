"""INT-W3a: BundleProvider seam + shadow persistence. Hermetic: a tiny fake scorer script and manifest are
written to tmp; no model library, no network, no real data. Every history here is synthetic."""
import hashlib
import json
import logging
import pathlib
import sys
import textwrap
import time
from datetime import date, datetime, timezone

import psycopg2
import pytest

from garage.api import app as app_mod
from garage.api import worker
from garage.api.dvsa import DvsaHistory, DvsaTest
from garage.api.provider import AssessmentRequest, StubProvider
from garage.api.providers import BundleError, BundleProvider, ShadowProviderResult, provider_from_env
from garage.api.providers.bundle_provider import canonical_hash
from .conftest import day, history, import_rows, make_workshop, sql

V = "/api/garage/v1"
MARGIN, PROB = 0.123456789, 0.314159265

SCORER = textwrap.dedent('''
    import json, sys, time, pathlib
    bundle = pathlib.Path(sys.argv[sys.argv.index("--bundle") + 1])
    raw = sys.stdin.read()
    (bundle / "last_stdin.json").write_text(raw)
    (bundle / "last_argv.json").write_text(json.dumps(sys.argv))
    mode = (bundle / "mode.txt").read_text().strip()
    man = json.loads((bundle / "manifest.json").read_text())
    good = {"ok": True, "margin": %r, "probability": %r, "support_reasons": [],
            "bundle_manifest_sha256": man["manifest_content_sha256"], "model_version": man["model_version"]}
    if mode == "ok": print(json.dumps(good))
    elif mode == "sleep": time.sleep(30); print(json.dumps(good))
    elif mode == "exit1": sys.exit(1)
    elif mode == "garbage": print("this is not json")
    elif mode == "extra_stdout": print("hello"); print(json.dumps(good))
    elif mode == "okfalse": print(json.dumps({"ok": False, "error_code": "no_prior_episode", "support_reasons": ["x"]}))
    elif mode == "extra_key": print(json.dumps({**good, "percent": 25}))
    elif mode == "missing_key": good.pop("margin"); print(json.dumps(good))
    elif mode == "nan": print(json.dumps(good).replace(%s, "NaN"))
    elif mode == "prob_range": print(json.dumps({**good, "probability": 1.5}))
    elif mode == "wrong_bundle": print(json.dumps({**good, "bundle_manifest_sha256": "0" * 64}))
    elif mode == "wrong_version": print(json.dumps({**good, "model_version": "other"}))
    elif mode == "bool_margin": print(json.dumps({**good, "margin": True}))
''' % (MARGIN, PROB, repr(json.dumps(MARGIN))))


def write_bundle(root: pathlib.Path, mode="ok", **over) -> pathlib.Path:
    b = root / "bundle"
    b.mkdir()
    (b / "scorer.py").write_text(SCORER)
    (b / "mode.txt").write_text(mode)
    m = {"bundle_version": "BundleV1", "arm": "challenger/without40", "model_version": "synthetic-1",
         "policy_version": "garage-shadow-v1", "qualified": False, "display_state": "shadow",
         "integrated_freeze_sha256": "a" * 64, "dag_node_ids": ["n1"], "files": {},
         "runtime": {"stage": sys.executable}, "schema_sha256": "b" * 64, "synthetic_fixture": True,
         "scorer": {"path": "scorer.py", "sha256": hashlib.sha256(SCORER.encode()).hexdigest(),
                    "runtime_executable": sys.executable}}
    m.update(over)
    m["manifest_content_sha256"] = canonical_hash(m)
    (b / "manifest.json").write_text(json.dumps(m))
    return b


def rewrite_manifest(b: pathlib.Path, rehash=True, **over):
    m = json.loads((b / "manifest.json").read_text())
    m.update(over)
    if rehash:
        m.pop("manifest_content_sha256")
        m["manifest_content_sha256"] = canonical_hash(m)
    (b / "manifest.json").write_text(json.dumps(m))


def request(reg="ZZ99TST"):
    tests = (DvsaTest(completed_at=datetime(2021, 5, 1, 10, tzinfo=timezone.utc), result="PASSED",
                      test_number="000000000001", odometer_value=40000, odometer_unit="mi",
                      expiry_date=date(2022, 5, 1), defects=({"type": "ADVISORY", "text": "tyre", "dangerous": False},)),
             DvsaTest(completed_at=datetime(2022, 5, 2, 10, tzinfo=timezone.utc), result="FAILED",
                      test_number="000000000002", odometer_value=50000, odometer_unit="mi"),
             # after the cutoff: must never reach the scorer even if a caller forgot to truncate
             DvsaTest(completed_at=datetime(2022, 9, 1, 10, tzinfo=timezone.utc), result="PASSED",
                      test_number="000000000003"))
    return AssessmentRequest(registration=reg, booking_type="mot", appointment_date=date(2022, 6, 1),
                             cutoff_date=date(2022, 6, 1), cutoff_at=datetime(2022, 6, 1, 6, tzinfo=timezone.utc),
                             history=DvsaHistory(registration=reg, make="FORD", model="FOCUS", tests=tests))


def assert_no_estimate(r):
    assert r.state == "unavailable"
    assert r.percent_rounded is None and r.band is None and r.rank_score is None
    assert r.display_policy.ranking_enabled is False and r.display_policy.operational_qualified is False
    assert r.display_policy.display_state in ("shadow", "unavailable")


# ------------------------------------------------------------------ provider unit tests
def test_valid_bundle_gives_shadow_result_with_margin_and_no_estimate(tmp_path):
    p = BundleProvider(write_bundle(tmp_path))
    r = p.assess(request())
    assert isinstance(r, ShadowProviderResult)
    assert_no_estimate(r)
    assert (r.reason, r.display_policy.display_state) == ("model_not_qualified", "shadow")
    assert (r.shadow_margin, r.shadow_probability) == (MARGIN, PROB)
    assert r.shadow_bundle_sha256 == p.manifest_sha256 and r.shadow_model_version == "synthetic-1"
    assert r.display_policy.model_version == "synthetic-1" and r.display_policy.policy_version == "garage-shadow-v1"
    assert r.display_policy.problem() is None


def test_scorer_stdin_is_dvsa_shaped_cutoff_truncated_and_has_no_plate(tmp_path):
    b = write_bundle(tmp_path)
    BundleProvider(b).assess(request("ZZ99TST"))
    raw = (b / "last_stdin.json").read_text()
    d = json.loads(raw)
    assert set(d) == {"history", "cutoff_at", "vehicle_id"}
    assert d["cutoff_at"] == "2022-06-01T06:00:00+00:00"
    assert d["vehicle_id"].isdigit() and str(int(d["vehicle_id"])) == d["vehicle_id"] and int(d["vehicle_id"]) < 2**64
    assert "ZZ99TST" not in raw and "registration" not in d["history"]
    tests = d["history"]["motTests"]
    assert [t["motTestNumber"] for t in tests] == ["000000000001", "000000000002"]     # post-cutoff test dropped
    assert tests[0]["testResult"] == "PASSED" and tests[0]["completedDate"] == "2021-05-01T10:00:00+00:00"
    assert tests[0]["defects"] == [{"type": "ADVISORY", "text": "tyre", "dangerous": False}]
    argv = json.loads((b / "last_argv.json").read_text())
    assert argv[1:] == ["--bundle", str(b.resolve())]


@pytest.mark.parametrize("mode,reason", [
    ("exit1", "scorer_exit_nonzero"),
    ("garbage", "scorer_output_malformed"),
    ("extra_stdout", "scorer_output_malformed"),
    ("nan", "scorer_output_malformed"),
    ("okfalse", "no_prior_episode"),
    ("extra_key", "scorer_schema_mismatch"),
    ("missing_key", "scorer_schema_mismatch"),
    ("prob_range", "scorer_schema_mismatch"),
    ("bool_margin", "scorer_schema_mismatch"),
    ("wrong_bundle", "scorer_bundle_mismatch"),
    ("wrong_version", "scorer_bundle_mismatch"),
])
def test_scorer_failures_become_specific_unavailable_reasons(tmp_path, mode, reason):
    b = write_bundle(tmp_path)
    p = BundleProvider(b)
    (b / "mode.txt").write_text(mode)
    r = p.assess(request())
    assert_no_estimate(r)
    assert r.reason == reason and r.reason_text
    assert not isinstance(r, ShadowProviderResult) or r.shadow_margin is None
    assert r.display_policy.display_state == "unavailable"


def test_timeout_kills_the_scorer_and_reports_scorer_timeout(tmp_path):
    b = write_bundle(tmp_path)
    p = BundleProvider(b, timeout_s=0.5)
    (b / "mode.txt").write_text("sleep")
    t0 = time.monotonic()
    r = p.assess(request())
    assert r.reason == "scorer_timeout" and time.monotonic() - t0 < 10
    assert_no_estimate(r)


def test_okfalse_reason_text_carries_the_code_and_no_shadow_values(tmp_path):
    b = write_bundle(tmp_path)
    p = BundleProvider(b)
    (b / "mode.txt").write_text("okfalse")
    r = p.assess(request())
    assert r.reason == "no_prior_episode" and "no_prior_episode" in r.reason_text
    assert not isinstance(r, ShadowProviderResult)


def test_tampered_scorer_or_manifest_after_startup_is_unavailable_not_an_exception(tmp_path):
    b = write_bundle(tmp_path)
    p = BundleProvider(b)
    with open(b / "scorer.py", "a") as f:
        f.write("\n# tampered\n")
    assert p.assess(request()).reason == "scorer_hash_mismatch"
    (b / "scorer.py").write_text(SCORER)
    assert p.assess(request()).reason == "model_not_qualified"            # restored => verifies again
    m = json.loads((b / "manifest.json").read_text())
    m["model_version"] = "tampered"                                       # content hash left stale
    (b / "manifest.json").write_text(json.dumps(m))
    assert p.assess(request()).reason == "bundle_hash_mismatch"
    (b / "manifest.json").unlink()
    assert p.assess(request()).reason == "bundle_manifest_invalid"


def test_manifest_replaced_by_a_different_valid_bundle_is_refused(tmp_path):
    b = write_bundle(tmp_path)
    p = BundleProvider(b)
    rewrite_manifest(b, model_version="swapped")                          # internally consistent, different hash
    assert p.assess(request()).reason == "bundle_hash_mismatch"


def test_scorer_file_deleted_after_startup_is_unavailable(tmp_path):
    b = write_bundle(tmp_path)
    p = BundleProvider(b)
    (b / "scorer.py").unlink()
    assert p.assess(request()).reason == "bundle_manifest_invalid"


def test_naive_cutoff_is_scorer_input_invalid_not_an_exception(tmp_path):
    p = BundleProvider(write_bundle(tmp_path))
    req = request()
    naive = AssessmentRequest(**{**req.__dict__, "cutoff_at": datetime(2022, 6, 1, 6)})
    assert p.assess(naive).reason == "scorer_input_invalid"


@pytest.mark.parametrize("over,code", [
    ({"qualified": True}, "bundle_manifest_invalid"),
    ({"display_state": "bands"}, "bundle_manifest_invalid"),
    ({"bundle_version": "BundleV2"}, "bundle_manifest_invalid"),
    ({"model_version": ""}, "bundle_manifest_invalid"),
])
def test_invalid_manifests_are_refused_at_construction(tmp_path, over, code):
    with pytest.raises(BundleError) as e:
        BundleProvider(write_bundle(tmp_path, **over))
    assert e.value.code == code


def test_construction_refuses_bad_hashes_missing_dir_and_escaping_scorer(tmp_path):
    b = write_bundle(tmp_path)
    (b / "scorer.py").write_text(SCORER + "\n#x\n")
    with pytest.raises(BundleError) as e:
        BundleProvider(b)
    assert e.value.code == "scorer_hash_mismatch"
    with pytest.raises(BundleError) as e:
        BundleProvider(tmp_path / "nope")
    assert e.value.code == "bundle_manifest_invalid"
    outside = tmp_path / "evil.py"
    outside.write_text(SCORER)
    b2 = tmp_path / "b2"
    b2.mkdir()
    (b2 / "mode.txt").write_text("ok")
    sc = {"path": "../evil.py", "sha256": hashlib.sha256(SCORER.encode()).hexdigest(), "runtime_executable": sys.executable}
    m = json.loads((b / "manifest.json").read_text())
    m.update(scorer=sc)
    m.pop("manifest_content_sha256")
    m["manifest_content_sha256"] = canonical_hash(m)
    (b2 / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(BundleError) as e:
        BundleProvider(b2)
    assert "escapes" in str(e.value)


def test_distinct_reasons_for_each_failure_class(tmp_path):
    """The five spec'd failure classes yield five different reasons."""
    out = {}
    b = write_bundle(tmp_path)
    p = BundleProvider(b, timeout_s=0.5)
    for mode in ("sleep", "garbage", "exit1", "okfalse"):
        (b / "mode.txt").write_text(mode)
        out[mode] = p.assess(request()).reason
    (b / "mode.txt").write_text("ok")
    with open(b / "scorer.py", "a") as f:
        f.write("#\n")
    out["bad_hash"] = p.assess(request()).reason
    assert len(set(out.values())) == 5, out


# ------------------------------------------------------------------ app seam
def test_env_unset_or_blank_gives_stub_and_default_context_is_stub(dsn, monkeypatch):
    assert isinstance(provider_from_env({}), StubProvider)
    assert isinstance(provider_from_env({"GARAGE_MODEL_BUNDLE": "  "}), StubProvider)
    for k, v in (("GARAGE_DATABASE_URL", dsn), ("GARAGE_DVSA_BACKEND", "fake"), ("GARAGE_EMAIL_BACKEND", "console")):
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("GARAGE_MODEL_BUNDLE", raising=False)
    ctx = app_mod.build_ctx_from_env()
    try:
        assert isinstance(ctx.provider, StubProvider)
    finally:
        ctx.db.close()


def test_valid_bundle_selected_at_startup_and_invalid_falls_back_to_stub_with_log(tmp_path, dsn, monkeypatch, caplog):
    good = write_bundle(tmp_path)
    for k, v in (("GARAGE_DATABASE_URL", dsn), ("GARAGE_DVSA_BACKEND", "fake"), ("GARAGE_EMAIL_BACKEND", "console"),
                 ("GARAGE_MODEL_BUNDLE", str(good))):
        monkeypatch.setenv(k, v)
    ctx = app_mod.build_ctx_from_env()
    try:
        assert isinstance(ctx.provider, BundleProvider)
    finally:
        ctx.db.close()
    with caplog.at_level(logging.ERROR, logger="garage.provider"):
        for bad_env in (str(tmp_path / "missing"), str(good / "mode.txt")):
            assert isinstance(provider_from_env({"GARAGE_MODEL_BUNDLE": bad_env}), StubProvider)
        rewrite_manifest(good, qualified=True)
        assert isinstance(provider_from_env({"GARAGE_MODEL_BUNDLE": str(good)}), StubProvider)
    assert caplog.text.count("GARAGE_MODEL_BUNDLE rejected") == 3
    monkeypatch.setenv("GARAGE_MODEL_BUNDLE", str(tmp_path / "missing"))
    ctx = app_mod.build_ctx_from_env()       # does not crash
    try:
        assert isinstance(ctx.provider, StubProvider)
    finally:
        ctx.db.close()


# ------------------------------------------------------------------ persistence end to end
def _run(env, c, provider, reg="ZZ00FXT"):
    env.ctx.provider = provider
    env.dvsa.set_history(reg, history(reg, [("2021-01-01", "PASSED"), ("2022-01-01", "FAILED")]))
    bid = import_rows(c, [(reg, day(2), "mot")])["created_booking_ids"][0]
    worker.drain(env.ctx)
    return bid


def _walk(o, keys, vals):
    if isinstance(o, dict):
        for k, v in o.items():
            keys.add(k)
            _walk(v, keys, vals)
    elif isinstance(o, list):
        for v in o:
            _walk(v, keys, vals)
    else:
        vals.append(o)


def test_shadow_result_is_persisted_with_margin_and_never_serialised(env, tmp_path):
    b = write_bundle(tmp_path)
    c = make_workshop(env, "A", "o@a.example")
    bid = _run(env, c, BundleProvider(b))
    row = sql(env, "SELECT * FROM assessments WHERE booking_id = %s", (bid,))[0]
    man = json.loads((b / "manifest.json").read_text())
    assert row["state"] == "unavailable" and row["reason"] == "model_not_qualified"
    assert (row["shadow_margin"], row["shadow_probability"]) == (MARGIN, PROB)
    assert row["shadow_bundle_sha256"] == man["manifest_content_sha256"] and row["shadow_model_version"] == "synthetic-1"
    assert row["result"] is None and row["rank_key"] is None and row["score_unrounded"] is None
    assert row["display_policy"]["display_state"] == "shadow" and row["display_policy"]["ranking_enabled"] is False
    # every client-reachable read: none of the shadow keys or values
    bodies = [c.get(f"{V}/bookings/{bid}"), c.get(f"{V}/bookings/{bid}/assessments"), c.get(f"{V}/bookings"),
              c.get(f"{V}/bookings/{bid}/briefing"), c.get(f"{V}/bookings/{bid}/decisions"),
              c.get(f"{V}/bookings/{bid}/outcomes")]
    for r in bodies:
        assert r.status_code == 200, r.text
        keys, vals = set(), []
        _walk(r.json(), keys, vals)
        assert not [k for k in keys if "shadow" in k.lower() or "margin" in k.lower() or "probab" in k.lower()], keys
        for needle in (str(MARGIN), str(PROB), "123456789", "314159265", man["manifest_content_sha256"]):
            assert needle not in r.text
        assert MARGIN not in vals and PROB not in vals
    a = c.get(f"{V}/bookings/{bid}").json()["assessment"]
    assert a["state"] == "unavailable" and a["result"] is None and a["rank_key"] is None
    assert a["display_policy"]["display_state"] == "shadow"


@pytest.mark.parametrize("mode,reason", [("exit1", "scorer_exit_nonzero"), ("garbage", "scorer_output_malformed"),
                                         ("okfalse", "no_prior_episode")])
def test_scorer_failure_end_to_end_stores_unavailable_and_no_shadow_values(env, tmp_path, mode, reason):
    b = write_bundle(tmp_path)
    p = BundleProvider(b)
    (b / "mode.txt").write_text(mode)
    c = make_workshop(env, "A", "o@a.example")
    bid = _run(env, c, p)
    row = sql(env, "SELECT * FROM assessments WHERE booking_id = %s", (bid,))[0]
    assert (row["state"], row["reason"]) == ("unavailable", reason)
    assert row["shadow_margin"] is None and row["shadow_probability"] is None
    assert row["shadow_bundle_sha256"] is None and row["shadow_model_version"] is None
    assert c.get(f"{V}/bookings/{bid}").json()["assessment"]["reason"] == reason


def test_stub_and_other_providers_store_no_shadow_values(env):
    c = make_workshop(env, "A", "o@a.example")
    bid = _run(env, c, StubProvider())
    row = sql(env, "SELECT * FROM assessments WHERE booking_id = %s", (bid,))[0]
    assert row["reason"] == "model_not_packaged" and row["shadow_margin"] is None


def test_database_refuses_shadow_values_on_available_or_ranked_rows(env):
    c = make_workshop(env, "A", "o@a.example")
    env.dvsa.set_history("ZZ00FXT", history("ZZ00FXT", [("2022-01-01", "PASSED")]))
    bid = import_rows(c, [("ZZ00FXT", day(2), "mot")])["created_booking_ids"][0]   # pending, not drained
    with pytest.raises(psycopg2.errors.CheckViolation):
        sql(env, "UPDATE assessments SET state='available', completed_at=now(), shadow_margin=0.1, "
                 "display_policy='{\"ranking_enabled\": false}' WHERE booking_id=%s", (bid,))
    with pytest.raises(psycopg2.errors.CheckViolation):
        sql(env, "UPDATE assessments SET state='unavailable', completed_at=now(), shadow_margin=0.1, "
                 "display_policy='{\"ranking_enabled\": true}' WHERE booking_id=%s", (bid,))


# ------------------------------------------------------------------ D-D: raw DVSA JSON + new fields
RAW_MARKER = "RAWONLY-7f3a91"
RAW = {"registration": "ZZ77RAW", "make": "EXAMPLE MOTORS", "model": "RAW MODEL", "fuelType": "Petrol",
       "engineSize": "1200", "primaryColour": "Silver", "firstUsedDate": "2016-01-01",
       "registrationDate": "2016-01-01", "manufactureDate": "2016-01-01", "x_marker": RAW_MARKER,
       "motTests": [
           {"completedDate": "2021-02-03T10:15:00Z", "testResult": "PASSED", "motTestNumber": "SYN-R1",
            "odometerValue": "12000", "odometerUnit": "MI", "odometerResultType": "READ", "dataSource": "DVSA",
            "registrationAtTimeOfTest": "ZZ77RAW", "expiryDate": "2022-02-03", "defects": []},
           {"completedDate": "2022-02-03T10:15:00Z", "testResult": "FAILED", "motTestNumber": "SYN-R2",
            "odometerValue": "13000", "odometerUnit": "MI", "odometerResultType": "UNREADABLE",
            "dataSource": "DVA", "registrationAtTimeOfTest": "ZZ77RAW", "defects": []},
           {"completedDate": "2022-02-04T10:15:00Z", "testResult": "PASSED", "motTestNumber": "SYN-R3",
            "odometerValue": "13001", "odometerUnit": "MI", "odometerResultType": "READ", "dataSource": "DVSA",
            "defects": []},
           {"completedDate": "2030-01-01T10:15:00Z", "testResult": "PASSED", "motTestNumber": "SYN-FUTURE",
            "x_leak": "FUTURE-LEAK", "defects": []}]}
RAW_BYTES = json.dumps(RAW, indent=1, sort_keys=True).encode()   # authored bytes; exact bytes must survive


def test_new_fields_parse_and_normalised_json_is_unchanged_by_them():
    from garage.api.dvsa import DvsaHistory, parse_api_response
    h = parse_api_response("ZZ77RAW", RAW, raw=RAW_BYTES)
    assert h.colour == "Silver" and h.raw == RAW_BYTES
    assert [(t.odometer_result_type, t.data_source) for t in h.tests[:3]] == \
        [("READ", "DVSA"), ("UNREADABLE", "DVA"), ("READ", "DVSA")]
    # the normalised snapshot form is byte-identical to the pre-D-D form (snapshot hashes / briefing versions stable)
    j = h.to_json()
    assert set(j) == {"registration", "make", "model", "fuel_type", "engine_size", "first_used_date",
                      "registration_date", "manufacture_date", "tests"}
    assert set(j["tests"][0]) == {"completed_at", "result", "test_number", "odometer_value", "odometer_unit",
                                  "expiry_date", "defects"}
    assert RAW_MARKER not in json.dumps(j) and "Silver" not in json.dumps(j)
    back = DvsaHistory.from_json(j)
    assert back.raw is None and back.tests[0].test_number == "SYN-R1"
    assert h.before(date(2022, 2, 4), datetime(2022, 2, 4, tzinfo=timezone.utc)).raw == RAW_BYTES


def test_http_client_attaches_the_exact_response_bytes():
    import httpx
    from garage.api.dvsa import HttpDvsaClient

    def handler(req):
        if req.url.host == "token.test":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(200, content=RAW_BYTES, headers={"content-type": "application/json"})
    c = HttpDvsaClient("c", "s", "https://token.test/o", "K", http=httpx.Client(transport=httpx.MockTransport(handler)))
    h = c.fetch_history("ZZ77RAW")
    assert h.raw == RAW_BYTES and h.colour == "Silver"


def _raw_env(env, tmp_path, reg="ZZ77RAW", raw=RAW_BYTES):
    d = tmp_path / "fx"
    d.mkdir(exist_ok=True)
    (d / f"{reg}.json").write_bytes(raw)
    env.dvsa.fixture_dir = d          # FakeDvsaClient reads raw-shaped fixtures from here


def test_raw_response_is_stored_hashed_verified_and_never_in_any_response(env, tmp_path):
    b = write_bundle(tmp_path)
    c = make_workshop(env, "A", "o@a.example")
    _raw_env(env, tmp_path)
    env.ctx.provider = BundleProvider(b)
    # the booking date must make the test at 2022-02-04 precede the cutoff only partially: cutoff = booking day - 1
    bid = import_rows(c, [("ZZ77RAW", day(2), "mot")])["created_booking_ids"][0]
    worker.drain(env.ctx)
    snap = sql(env, "SELECT raw_response, raw_sha256, content_sha256, history FROM source_snapshots WHERE booking_id=%s", (bid,))[0]
    assert bytes(snap["raw_response"]) == RAW_BYTES
    assert snap["raw_sha256"] == hashlib.sha256(RAW_BYTES).hexdigest()       # stable: sha of the authored bytes
    assert sql(env, "SELECT encode(sha256(raw_response),'hex') = raw_sha256 AS ok FROM source_snapshots "
                    "WHERE booking_id=%s", (bid,))[0]["ok"] is True          # independent in-database check
    assert RAW_MARKER not in json.dumps(snap["history"]) and "Silver" not in json.dumps(snap["history"])
    # the DB refuses a stored hash that does not match the stored bytes
    with pytest.raises(psycopg2.errors.CheckViolation):
        sql(env, "UPDATE source_snapshots SET raw_sha256 = repeat('0', 64) WHERE booking_id=%s", (bid,))
    # no client-reachable response mentions the raw JSON, its hash, or the new raw-only fields
    for r in (c.get(f"{V}/bookings/{bid}"), c.get(f"{V}/bookings/{bid}/assessments"), c.get(f"{V}/bookings"),
              c.get(f"{V}/bookings/{bid}/briefing"), c.get(f"{V}/bookings/{bid}/decisions"),
              c.get(f"{V}/bookings/{bid}/outcomes")):
        assert r.status_code == 200, r.text
        for needle in (RAW_MARKER, snap["raw_sha256"], "raw_response", "raw_sha256", "primaryColour",
                       "odometerResultType", "Silver", "FUTURE-LEAK"):
            assert needle not in r.text, (needle, r.request.url.path)


def test_scorer_stdin_comes_from_the_raw_response_cut_at_the_cutoff_without_plates(env, tmp_path):
    b = write_bundle(tmp_path)
    c = make_workshop(env, "A", "o@a.example")
    _raw_env(env, tmp_path)
    env.ctx.provider = BundleProvider(b)
    import_rows(c, [("ZZ77RAW", day(2), "mot")])
    worker.drain(env.ctx)
    raw_in = (b / "last_stdin.json").read_text()
    d = json.loads(raw_in)["history"]
    assert d["x_marker"] == RAW_MARKER and d["primaryColour"] == "Silver"           # raw-only fields reach the scorer
    assert [t["motTestNumber"] for t in d["motTests"]] == ["SYN-R1", "SYN-R2", "SYN-R3"]   # 2030 test cut off
    assert [t["odometerResultType"] for t in d["motTests"]] == ["READ", "UNREADABLE", "READ"]
    assert [t["dataSource"] for t in d["motTests"]] == ["DVSA", "DVA", "DVSA"]
    assert "ZZ77RAW" not in raw_in and "FUTURE-LEAK" not in raw_in and "registration" not in raw_in.replace(
        "registrationDate", "").replace("firstUsed", "")


def test_old_snapshot_without_raw_still_assesses_from_the_normalised_history(env, tmp_path):
    b = write_bundle(tmp_path)
    c = make_workshop(env, "A", "o@a.example")
    env.ctx.provider = BundleProvider(b)
    env.dvsa.set_history("ZZ00FXT", history("ZZ00FXT", [("2021-01-01", "PASSED"), ("2022-01-01", "FAILED")]))
    bid = import_rows(c, [("ZZ00FXT", day(2), "mot")])["created_booking_ids"][0]
    worker.drain(env.ctx)
    snap = sql(env, "SELECT raw_response, raw_sha256 FROM source_snapshots WHERE booking_id=%s", (bid,))[0]
    assert snap["raw_response"] is None and snap["raw_sha256"] is None
    row = sql(env, "SELECT state, reason, shadow_margin FROM assessments WHERE booking_id=%s", (bid,))[0]
    assert (row["state"], row["reason"], row["shadow_margin"]) == ("unavailable", "model_not_qualified", MARGIN)
    d = json.loads((b / "last_stdin.json").read_text())["history"]
    assert "x_marker" not in d
    assert len(d["motTests"]) == 2 and set(d["motTests"][0]) >= {"odometerResultType", "dataSource"}
    assert d["motTests"][0]["odometerResultType"] is None
    # a pre-D-D normalised snapshot JSON (no colour / new keys) still loads
    from garage.api.dvsa import DvsaHistory
    old = {"registration": "X", "make": None, "model": None, "fuel_type": None, "engine_size": None,
           "first_used_date": None, "registration_date": None, "manufacture_date": None,
           "tests": [{"completed_at": "2021-01-01T00:00:00+00:00", "result": "PASSED", "test_number": None,
                      "odometer_value": None, "odometer_unit": None, "expiry_date": None, "defects": []}]}
    h = DvsaHistory.from_json(old)
    assert h.colour is None and h.tests[0].data_source is None and h.to_json() == old


def test_malformed_raw_is_scorer_input_invalid_not_an_exception(tmp_path):
    p = BundleProvider(write_bundle(tmp_path))
    req = request()
    bad = DvsaHistory(**{**req.history.__dict__, "raw": b"{not json"})
    r = p.assess(AssessmentRequest(**{**req.__dict__, "history": bad}))
    assert r.reason == "scorer_input_invalid" and r.state == "unavailable"


def test_raw_path_applies_the_cutoff_itself_when_the_caller_did_not_truncate(tmp_path):
    from garage.api.dvsa import parse_api_response
    b = write_bundle(tmp_path)
    p = BundleProvider(b)
    full = parse_api_response("ZZ77RAW", RAW, raw=RAW_BYTES)            # untruncated history, raw attached
    req = AssessmentRequest(registration="ZZ77RAW", booking_type="mot", appointment_date=date(2022, 2, 4),
                            cutoff_date=date(2022, 2, 4), cutoff_at=datetime(2022, 2, 4, 0, tzinfo=timezone.utc),
                            history=full)
    assert isinstance(p.assess(req), ShadowProviderResult)
    d = json.loads((b / "last_stdin.json").read_text())["history"]
    assert [t["motTestNumber"] for t in d["motTests"]] == ["SYN-R1", "SYN-R2"]
