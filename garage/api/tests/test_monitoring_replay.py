"""G6 production input-PSI by replay (monitoring/replay.py): subprocess hardening, features-mode contract handling, refusal to
mix schemas/bundles, counted refusals without data values, request parity with the serving provider, and G6 on replayed rows.

Everything is synthetic. The scorer is a FAKE script that implements the fixed FEATURES-MODE contract
(`<python> -I scorer.py --bundle DIR --emit-features`); no real bundle, data or model is read. The G6 policy values used by
the tests are the adopted ones; nothing here decides an open question."""
import copy
import hashlib
import inspect
import json
import pathlib
import random
import subprocess
import sys
import textwrap
from datetime import date, datetime, timezone

import pytest

from garage.api.dvsa import parse_api_response
from garage.api.monitoring import MonitoringInputError, ReferenceArtefact, build_reference_dict, dumps_canonical
from garage.api.monitoring import replay as R
from garage.api.provider import AssessmentRequest
from garage.api.providers.bundle_provider import _scorer_input, canonical_hash, verify_bundle

CUTOFF_DATE = date(2022, 6, 1)
CUTOFF_AT = datetime(2022, 6, 1, 6, tzinfo=timezone.utc)
NAMES = ["n_prior", "last_failed", "make_code", "odometer"]
TYPES = [["n_prior", "numeric"], ["last_failed", "numeric"], ["make_code", "categorical"], ["odometer", "numeric"]]
SCHEMA_SHA = hashlib.sha256(json.dumps(TYPES, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
OTHER_SCHEMA_SHA = "e" * 64
MAKES = {"FORD": 1.0, "VW": 2.0, "BMW": 3.0}


def fake_features(req, manifest_sha, schema_sha):
    """The fake scorer's whole behaviour as a pure function of the request (also run in-process by `fake_runner`).
    The `make` field selects special behaviour so a test can plant any row-level failure through the real request path."""
    h = req["history"]
    make = h.get("make")
    tests = h.get("motTests") or []
    special = {"REFUSE": {"ok": False, "error": "row_unsupported"}, "ODD": {"ok": False, "error": "history_invalid"},
               "LEAK": {"ok": False, "error": "AB12 CDE leaked registration"},
               "WRONGBUNDLE": "wrong_bundle", "SCHEMA2": "schema2", "NAMES2": "names2"}.get(make)
    if isinstance(special, dict):
        return special
    last = tests[-1] if tests else {}
    odo = last.get("odometerValue")
    values = [float(len(tests)), 1.0 if last.get("testResult") == "FAILED" else 0.0,
              MAKES.get(make, 9.0), float(odo) if odo is not None else None]
    out = {"ok": True, "names": list(NAMES), "values": values, "feature_schema_sha256": schema_sha,
           "bundle_manifest_sha256": manifest_sha}
    if special == "wrong_bundle":
        out["bundle_manifest_sha256"] = "0" * 64
    elif special == "schema2":
        out["feature_schema_sha256"] = OTHER_SCHEMA_SHA
    elif special == "names2":
        out["names"] = [NAMES[1], NAMES[0], NAMES[2], NAMES[3]]
    return out


def fake_runner(manifest_sha, schema_sha):
    """In-process stand-in for run_features_subprocess (same (bytes|None, code|None) interface)."""
    def run(stdin_bytes):
        return json.dumps(fake_features(json.loads(stdin_bytes), manifest_sha, schema_sha), sort_keys=True).encode(), None
    return run


SCORER = textwrap.dedent('''
    import json, os, sys, time, pathlib
    bundle = pathlib.Path(sys.argv[sys.argv.index("--bundle") + 1])
    man = json.loads((bundle / "manifest.json").read_text())
    mode = (bundle / "mode.txt").read_text().strip()
    SCHEMA_SHA = %r
    NAMES = %r
    MAKES = %r
    OTHER_SCHEMA_SHA = %r
''') % (SCHEMA_SHA, NAMES, MAKES, OTHER_SCHEMA_SHA) + inspect.getsource(fake_features) + textwrap.dedent('''
    raw = sys.stdin.read()
    (bundle / "last_argv.json").write_text(json.dumps(sys.argv))
    (bundle / "last_env.json").write_text(json.dumps(sorted(os.environ)))
    (bundle / "last_stdin.json").write_text(raw)
    if "--emit-features" not in sys.argv:
        print(json.dumps({"ok": False, "error": "not_features_mode"})); sys.exit(0)
    req = json.loads(raw)
    if mode == "sleep": time.sleep(30)
    elif mode == "exit1": sys.exit(1)
    elif mode == "garbage": print("this is not json"); sys.exit(0)
    elif mode == "flood":
        sys.stdout.write("x" * 5_000_000); sys.stdout.flush(); sys.exit(0)
    elif mode == "nan": print('{"ok": true, "names": ["a"], "values": [NaN], "feature_schema_sha256": "%s", "bundle_manifest_sha256": "%s"}' % ("a" * 64, "b" * 64)); sys.exit(0)
    print(json.dumps(fake_features(req, man["manifest_content_sha256"], SCHEMA_SHA), sort_keys=True))
''')


def write_bundle(root: pathlib.Path, mode="ok") -> pathlib.Path:
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
    m["manifest_content_sha256"] = canonical_hash(m)
    (b / "manifest.json").write_text(json.dumps(m))
    return b


def raw_response(n_prior=3, last="PASSED", make="FORD", odometer=40000, plate="AB12CDE", extra_after_cutoff=False) -> bytes:
    tests = []
    for i in range(n_prior):
        res = last if i == n_prior - 1 else "PASSED"
        tests.append({"completedDate": f"{2016 + i}-0{1 + i}-15T10:00:00.000Z", "testResult": res,
                      "motTestNumber": f"{i:012d}", "odometerValue": str(odometer) if odometer is not None else None,
                      "odometerUnit": "MI", "registrationAtTimeOfTest": plate, "defects": []})
    if extra_after_cutoff:
        tests.append({"completedDate": "2022-09-01T10:00:00.000Z", "testResult": "FAILED", "motTestNumber": "999999999999",
                      "odometerValue": "99999", "odometerUnit": "MI", "defects": []})
    return json.dumps({"registration": plate, "make": make, "model": "FOCUS", "primaryColour": "Blue",
                       "motTests": tests}, sort_keys=True).encode()


def cand(i, raw, *, score=0.2, state="available", bundle_sha="a" * 64, reg="AB12CDE"):
    return {"booking_id": f"b{i}", "workshop_id": "w1", "vehicle_id": hashlib.sha256(f"{reg}{i}".encode()).hexdigest(),
            "state": state, "score": score, "score_bundle_sha256": bundle_sha if score is not None else None,
            "raw_response": raw, "vehicle_sha_prefix_hex": hashlib.sha256(f"{reg}{i}".encode()).hexdigest()[:16],
            "cutoff_date": CUTOFF_DATE, "cutoff_at": CUTOFF_AT}


# ------------------------------------------------------------------------------------- request parity with serving
@pytest.mark.parametrize("after", [False, True])
def test_replayed_request_is_byte_identical_to_the_serving_provider_request(after):
    reg = "ZZ99TST"
    raw = raw_response(extra_after_cutoff=after, plate=reg)
    hist = parse_api_response(reg, json.loads(raw), raw=raw)
    served = _scorer_input(AssessmentRequest(registration=reg, booking_type="mot", appointment_date=CUTOFF_DATE,
                                             cutoff_date=CUTOFF_DATE, cutoff_at=CUTOFF_AT, history=hist))
    prefix = hashlib.sha256(reg.encode()).hexdigest()[:16]          # what the SQL computes in the database
    replayed = R.scorer_request_bytes(raw, prefix, CUTOFF_DATE, CUTOFF_AT)
    assert replayed == served
    body = json.loads(replayed)
    assert "AB12CDE" not in replayed.decode() and reg not in replayed.decode()      # plates never reach the scorer
    assert all(t["completedDate"] < "2022-06-01" for t in body["history"]["motTests"])   # nothing at/after the cutoff
    assert body["vehicle_id"] == str(int(prefix, 16))


def test_request_builder_refuses_a_naive_cutoff():
    with pytest.raises(ValueError):
        R.scorer_request_bytes(raw_response(), "00" * 8, CUTOFF_DATE, datetime(2022, 6, 1, 6))


# ----------------------------------------------------------------------------------------- output validation
GOOD = {"ok": True, "names": ["a", "b"], "values": [1.0, None], "feature_schema_sha256": "a" * 64,
        "bundle_manifest_sha256": "b" * 64}


def enc(o):
    return json.dumps(o).encode()


def test_parse_accepts_the_contract_shape_and_null_values():
    d, code = R.parse_features_output(enc(GOOD))
    assert code is None and d["values"] == [1.0, None]


@pytest.mark.parametrize("mut", [
    lambda d: d.update(extra=1), lambda d: d.pop("names"), lambda d: d.update(names=["a", "a"]),
    lambda d: d.update(names=[]), lambda d: d.update(names=["a", 2]), lambda d: d.update(values=[1.0]),
    lambda d: d.update(values=[True, 1]), lambda d: d.update(values=["1", 2]), lambda d: d.update(values=[1e999, 1]),
    lambda d: d.update(feature_schema_sha256="A" * 64), lambda d: d.update(bundle_manifest_sha256="x"),
    lambda d: d.update(ok="yes")])
def test_parse_rejects_every_deviation_from_the_contract(mut):
    d = copy.deepcopy(GOOD)
    mut(d)
    parsed, code = R.parse_features_output(json.dumps(d).encode())
    assert parsed is None and code in ("schema_mismatch", "output_malformed")


def test_parse_refusal_codes_and_malformed_output():
    assert R.parse_features_output(enc({"ok": False, "error": "row_unsupported"})) == (None, "scorer:row_unsupported")
    assert R.parse_features_output(enc({"ok": False, "error": "AB12 CDE"}))[1] == "scorer:error_code_invalid"
    assert R.parse_features_output(enc({"ok": False, "error": 5}))[1] == "scorer:error_code_invalid"
    assert R.parse_features_output(enc({"ok": False, "error": "x", "more": 1}))[1] == "schema_mismatch"
    for bad in (b"", b"not json", b"\xff\xfe", b'{"ok": true, "values": [NaN]}', b"[1]"):
        assert R.parse_features_output(bad)[1] in ("output_malformed", "schema_mismatch")


# ------------------------------------------------------------------------------------------- the subprocess
def run_one(b, raw=None, **kw):
    vb = verify_bundle(b)
    stdin = R.scorer_request_bytes(raw or raw_response(), "00" * 8, CUTOFF_DATE, CUTOFF_AT)
    return R.run_features_subprocess(R.features_argv(vb), stdin, cwd=str(vb.directory),
                                     timeout_s=kw.pop("timeout_s", 20.0), max_output_bytes=kw.pop("max_output_bytes", 65536)), vb


def test_subprocess_runs_features_mode_without_a_shell_with_a_minimal_environment(tmp_path, monkeypatch):
    b = write_bundle(tmp_path)
    monkeypatch.setenv("GARAGE_SENTINEL", "must-not-reach-the-child")
    seen = {}
    real = subprocess.Popen

    def spy(*a, **kw):
        seen["args"], seen["kw"] = a, kw
        return real(*a, **kw)
    monkeypatch.setattr(R.subprocess, "Popen", spy)
    (out, code), vb = run_one(b)
    assert code is None
    assert seen["kw"]["shell"] is False and isinstance(seen["args"][0], list)
    assert seen["kw"]["env"] == {"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"}
    assert seen["kw"]["cwd"] == str(vb.directory) and seen["kw"]["start_new_session"] is True
    argv = seen["args"][0]
    assert argv[1] == "-I" and argv[-1] == "--emit-features" and argv[argv.index("--bundle") + 1] == str(vb.directory)
    child_env = set(json.loads((b / "last_env.json").read_text()))
    # macOS / the interpreter add a few of their own; nothing the parent had (sentinel, DSN, HOME, PYTHONPATH) is inherited
    assert child_env - {"__CF_USER_TEXT_ENCODING", "__PYVENV_LAUNCHER__", "LC_CTYPE"} == {"PATH", "LC_ALL"}
    assert "GARAGE_SENTINEL" not in child_env
    parsed, c2 = R.parse_features_output(out)
    assert c2 is None and parsed["names"] == NAMES and parsed["bundle_manifest_sha256"] == vb.manifest_sha256
    assert parsed["feature_schema_sha256"] == SCHEMA_SHA and parsed["values"][0] == 3.0


@pytest.mark.parametrize("mode,code", [("sleep", "timeout"), ("exit1", "exit_nonzero"), ("flood", "output_too_large")])
def test_subprocess_timeout_exit_and_output_cap(tmp_path, mode, code):
    b = write_bundle(tmp_path, mode)
    import time
    t0 = time.monotonic()
    (out, got), _ = run_one(b, timeout_s=1.0, max_output_bytes=65536)
    assert (out, got) == (None, code)
    assert time.monotonic() - t0 < 15                  # the sleeping child was killed, not waited for


def test_subprocess_launch_failure_is_a_code_not_an_exception(tmp_path):
    out, code = R.run_features_subprocess(["/nonexistent/python", "x"], b"{}", cwd=str(tmp_path), timeout_s=2,
                                          max_output_bytes=1024)
    assert (out, code) == (None, "launch_failed")


# -------------------------------------------------------------------------------------- the run: mixing, counting
def run_over(rows, *, schema=SCHEMA_SHA, manifest="a" * 64, unsupported=("row_unsupported",)):
    run = R.ReplayRun(fake_runner(manifest, SCHEMA_SHA), bundle_manifest_sha256=manifest, feature_schema_sha256=schema,
                      unsupported_error_codes=unsupported)
    return run, R.build_g6_rows(rows, run, _ref(), expected_bundle_sha256=manifest)


def dev_rows(n=1200, seed=3, shift=False):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        n_prior = rng.choice([1, 2, 3, 4, 5]) if not shift else rng.choice([4, 5, 5, 5, 5])
        make = rng.choices(["FORD", "VW", "BMW", "OTHER"], [5, 3, 2, 1] if not shift else [1, 1, 1, 8])[0]
        failed = rng.random() < (0.3 if not shift else 0.8)
        odo = rng.randint(20000, 120000)
        out.append(dict(n_prior=n_prior, last="FAILED" if failed else "PASSED", make=make, odometer=odo,
                        score=round(min(0.99, 0.05 + 0.04 * n_prior + (0.1 if failed else 0) + rng.random() * 0.05), 4)))
    return out


def _flat(r):
    return {"n_prior": float(r["n_prior"]), "last_failed": 1.0 if r["last"] == "FAILED" else 0.0,
            "make_code": MAKES.get(r["make"], 9.0), "odometer": float(r["odometer"]), "score": r["score"]}


_REF = {}


def _ref() -> ReferenceArtefact:
    if "r" not in _REF:
        rows = [dict(_flat(r), make_code=int(_flat(r)["make_code"])) for r in dev_rows()]
        d = build_reference_dict(rows, {"n_prior": {"kind": "continuous"}, "last_failed": {"kind": "binary"},
                                        "make_code": {"kind": "categorical", "top_n": 5},
                                        "odometer": {"kind": "continuous"}},
                                 artefact_id="replay-test", reference_source="fixture", bins_frozen_utc="2026-01-01T00:00:00Z")
        raw = dumps_canonical(d)
        _REF["r"] = ReferenceArtefact.from_bytes(raw, hashlib.sha256(raw).hexdigest())
    return _REF["r"]


def cands_from(rows, start=0, **kw):
    return [cand(start + i, raw_response(r["n_prior"], r["last"], r["make"], r["odometer"]), score=r["score"], **kw)
            for i, r in enumerate(rows)]


def test_categorical_codes_are_written_as_integers_for_the_reference_binner():
    assert R._for_reference("categorical", 3.0) == 3 and R._for_reference("categorical", 3.5) == 3.5
    assert R._for_reference("continuous", 3.0) == 3.0 and R._for_reference("binary", 1.0) == 1.0
    _, (rows, counts) = run_over(cands_from(dev_rows(5)))
    assert all(isinstance(r["features"]["make_code"], int) for r in rows)
    assert counts["n_replayed"] == 5 and counts["excluded_by_reason"] == {} and counts["refusals_by_code"] == {}


def test_row_level_refusals_are_counted_by_code_and_exclusions_by_reason_without_any_data():
    rows = dev_rows(8)
    c = cands_from(rows)
    c[0]["raw_response"] = raw_response(make="REFUSE")                     # scorer: row_unsupported, no stored score ...
    c[0]["score"], c[0]["score_bundle_sha256"] = None, None                # ... so it is an unsupported booking
    c[1]["raw_response"] = raw_response(make="ODD")                        # scorer: history_invalid (not an unsupported code)
    c[2]["raw_response"] = raw_response(make="LEAK", plate="ZY98XWV")      # a scorer error code that is not a token
    c[3]["raw_response"] = None                                            # snapshot predates migration 0004
    c[4]["score"], c[4]["score_bundle_sha256"] = None, None                # scored OK now, nothing stored then
    c[5]["score_bundle_sha256"] = "9" * 64                                 # stored score came from another bundle
    c[6]["raw_response"] = raw_response(make="REFUSE")                     # refusal now, but a score was stored: disagreement
    c.append(cand(99, None, score=None, state="unsupported"))              # the app itself said unsupported
    run, (out, counts) = run_over(c)
    assert counts["refusals_by_code"] == {"scorer:error_code_invalid": 1, "scorer:history_invalid": 1, "scorer:row_unsupported": 2}
    assert counts["excluded_by_reason"] == {
        "no_raw_snapshot": 1, "no_stored_score": 1, "score_from_other_bundle": 1, "replay_refused": 2,
        "replay_disagrees_with_stored_score": 1}
    assert [r["supported"] for r in out] == [False, True, False]     # c[0] unsupported, c[7] supported, state=unsupported
    blob = json.dumps(counts)
    for secret in ("ZY98XWV", "AB12", "b1", "leaked", "LEAK", "REFUSE"):
        assert secret not in blob


@pytest.mark.parametrize("make,code", [("WRONGBUNDLE", "bundle_manifest_mismatch"), ("SCHEMA2", "feature_schema_mismatch"),
                                      ("NAMES2", "feature_names_mismatch")])
def test_a_run_never_mixes_bundles_schemas_or_feature_names(make, code):
    c = cands_from(dev_rows(6))
    c[4]["raw_response"] = raw_response(make=make)
    with pytest.raises(R.ReplayRefused) as e:
        run_over(c)
    assert e.value.code == code


def test_the_pinned_schema_hash_must_match_what_the_scorer_reports():
    with pytest.raises(R.ReplayRefused) as e:
        run_over(cands_from(dev_rows(3)), schema="d" * 64)
    assert e.value.code == "feature_schema_mismatch"


def test_pins_must_be_64_hex():
    with pytest.raises(R.ReplayRefused):
        R.ReplayRun(fake_runner("a" * 64, SCHEMA_SHA), bundle_manifest_sha256="short", feature_schema_sha256=SCHEMA_SHA,
                    unsupported_error_codes=[])


def test_reference_inputs_must_equal_the_scorer_input_names():
    d = copy.deepcopy(_ref().raw)
    del d["inputs"]["odometer"]
    raw = dumps_canonical(d)
    ref = ReferenceArtefact.from_bytes(raw, hashlib.sha256(raw).hexdigest())
    run = R.ReplayRun(fake_runner("a" * 64, SCHEMA_SHA), bundle_manifest_sha256="a" * 64, feature_schema_sha256=SCHEMA_SHA,
                      unsupported_error_codes=[])
    with pytest.raises(R.ReplayRefused) as e:
        R.build_g6_rows(cands_from(dev_rows(3)), run, ref, expected_bundle_sha256="a" * 64)
    assert e.value.code == "reference_inputs_differ_from_scorer_schema"


# ------------------------------------------------------------------------------------------------ G6 on replayed rows
def evaluate(c, population="prospective_pilot", **kw):
    ref = _ref()
    # a prospective-pilot population against a FIXTURE reference can only ever be 'fixture_only' (psi.py); the numeric criteria
    # are what these tests look at (`criteria_met`, per-input PSI).
    return R.evaluate_replay(c, fake_runner("a" * 64, SCHEMA_SHA), reference=ref, bundle_manifest_sha256="a" * 64,
                             feature_schema_sha256=SCHEMA_SHA, unsupported_error_codes=["row_unsupported"],
                             outcome_access_start_utc="NOT_STARTED", population=population, n_boot=0, seed=1, alpha=0.05,
                             replay_info={"window": {"start_utc": "x", "end_utc": "y"}, "score_column": "shadow_probability"},
                             **kw)


def test_same_distribution_meets_the_numeric_criteria_and_the_replay_block_is_aggregate_only():
    res = evaluate(cands_from(dev_rows(800, seed=11)))
    assert res["n_supported"] == 800 and res["criteria_met"] is True and res["max_psi"] <= 0.10
    assert set(res["psi"]) == set(NAMES) | {"score"}        # every input and the score
    rp = res["replay"]
    assert rp["n_candidates"] == 800 and rp["n_replayed"] == 800 and rp["bundle_manifest_sha256"] == "a" * 64
    assert rp["feature_schema_sha256"] == SCHEMA_SHA and rp["refusals_by_code"] == {} and rp["n_input_names"] == 4
    assert res["status"] == "fixture_only"                   # fixture reference: can never be a pass (AMENDMENT_DRAFT.md:49)
    from garage.api.monitoring.runs import assert_aggregate_only
    assert_aggregate_only(res)


def test_a_shifted_input_population_fails_g6():
    res = evaluate(cands_from(dev_rows(800, seed=12, shift=True)))
    assert res["criteria_met"] is False and res["status"] == "fail" and res["max_psi"] > 0.10
    assert res["psi"]["n_prior"]["breach"] and res["psi"]["make_code"]["breach"]


def test_fewer_than_500_supported_bookings_is_insufficient_n_even_when_every_psi_is_small():
    res = evaluate(cands_from(dev_rows(499, seed=13)))
    assert res["n_supported"] == 499 and res["status"] == "insufficient_n"


def test_unsupported_rows_count_toward_the_guard_but_not_the_bins():
    c = cands_from(dev_rows(700, seed=14))
    c += [cand(10_000 + i, None, score=None, state="unsupported") for i in range(300)]
    res = evaluate(c)
    assert res["n_total"] == 1000 and res["n_supported"] == 700 and res["unsupported_guard_ok"] is True
    c += [cand(20_000 + i, None, score=None, state="unsupported") for i in range(200)]
    assert evaluate(c)["unsupported_guard_ok"] is False


def test_the_score_distribution_enters_g6():
    rows = dev_rows(700, seed=15)
    for r in rows:
        r["score"] = 0.9                                     # inputs unchanged, score collapsed
    res = evaluate(cands_from(rows))
    assert res["criteria_met"] is False and res["max_psi_input"] == "score" and res["psi"]["score"]["breach"]


def test_an_end_to_end_run_through_the_real_subprocess_matches_the_in_process_run(tmp_path):
    b = write_bundle(tmp_path)
    vb = verify_bundle(b)
    argv = R.features_argv(vb)
    sub = lambda stdin: R.run_features_subprocess(argv, stdin, cwd=str(b), timeout_s=30, max_output_bytes=65536)  # noqa: E731
    rows = dev_rows(12, seed=16)
    c = cands_from(rows, bundle_sha=vb.manifest_sha256)
    run = R.ReplayRun(sub, bundle_manifest_sha256=vb.manifest_sha256, feature_schema_sha256=SCHEMA_SHA, unsupported_error_codes=[])
    got, counts = R.build_g6_rows(c, run, _ref(), expected_bundle_sha256=vb.manifest_sha256)
    run2 = R.ReplayRun(fake_runner(vb.manifest_sha256, SCHEMA_SHA), bundle_manifest_sha256=vb.manifest_sha256,
                       feature_schema_sha256=SCHEMA_SHA, unsupported_error_codes=[])
    want, _ = R.build_g6_rows(c, run2, _ref(), expected_bundle_sha256=vb.manifest_sha256)
    assert got == want and counts["n_replayed"] == 12 and counts["refusals_by_code"] == {}


@pytest.mark.parametrize("mode,code", [("garbage", "output_malformed"), ("nan", "output_malformed"), ("exit1", "exit_nonzero")])
def test_scorer_misbehaviour_is_counted_and_never_logged(tmp_path, mode, code, caplog):
    b = write_bundle(tmp_path, mode)
    vb = verify_bundle(b)
    argv = R.features_argv(vb)
    sub = lambda stdin: R.run_features_subprocess(argv, stdin, cwd=str(b), timeout_s=30, max_output_bytes=65536)  # noqa: E731
    run = R.ReplayRun(sub, bundle_manifest_sha256=vb.manifest_sha256, feature_schema_sha256=SCHEMA_SHA, unsupported_error_codes=[])
    rows, counts = R.build_g6_rows(cands_from(dev_rows(3, seed=17), bundle_sha=vb.manifest_sha256), run, _ref(),
                                   expected_bundle_sha256=vb.manifest_sha256)
    assert rows == [] and counts["refusals_by_code"] == {code: 3} and counts["excluded_by_reason"] == {"replay_refused": 3}
    assert caplog.records == []
