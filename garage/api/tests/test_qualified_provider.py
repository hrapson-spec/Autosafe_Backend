"""Route-to-live step 9: the app-side reader that turns a verified QualifiedBundle v2 into a qualified DisplayPolicy.
FIXTURE / SYNTHETIC ONLY: the two checked-in synthetic sample bundles from T2c/qual_v2 and their SAMPLE_PINS.json. The scorer subprocess is faked
(canned stdout), so the numbers mean nothing: this tests the seam (qualification gate, rounding, fail-closed fallbacks), not a model."""
import json
import logging
import pathlib
import shutil
import subprocess

import pytest

from garage.api import worker
from garage.api.handlers import _validate_provider
from garage.api.provider import StubProvider
from garage.api.providers import BundleProvider, ShadowProviderResult, bundle_provider, provider_from_env, qualification
from garage.api.display_values import READINGS
from garage.api.qual_v2 import bundle_v2
from .conftest import day, history, import_rows, make_workshop, sql
from .test_bundle_provider import request as make_request

FIX = pathlib.Path(__file__).resolve().parent / "qual_v2_fixtures"
PINS = json.loads((FIX / "SAMPLE_PINS.json").read_text())
BANDS, PCT = "sample_synthetic_bundle_bands", "sample_synthetic_bundle_percentage_ranking"
V = "/api/garage/v1"
EXE_OK = pytest.mark.skipif(not __import__("os").access("/usr/bin/python3", __import__("os").X_OK), reason="sample bundles name /usr/bin/python3")
pytestmark = EXE_OK


@pytest.fixture()
def bundle(tmp_path):
    """A writable copy of a sample bundle: bundle('bands') -> Path."""
    def _copy(name=BANDS):
        dst = tmp_path / name
        shutil.copytree(FIX / name, dst)
        return dst
    return _copy


class FakeScorer:
    def __init__(self, monkeypatch, bundle_dir, p=0.325, margin=-0.7):
        m = json.loads((bundle_dir / "manifest.json").read_text())
        self.out = json.dumps({"ok": True, "margin": margin, "probability": p, "support_reasons": [],
                               "bundle_manifest_sha256": m["manifest_content_sha256"], "model_version": m["model_version"]}).encode()
        self.calls = 0
        outer = self

        class _P:
            returncode = 0
            pid = 1

            def __init__(self, cmd, **kw):
                outer.calls += 1

            def communicate(self, stdin=None, timeout=None):
                return outer.out, None
        monkeypatch.setattr(bundle_provider.subprocess, "Popen", _P)


def qualified(bundle_dir, *, pin=None, reading="float_expression", synthetic=True, **kw):
    return BundleProvider(bundle_dir, pin=PINS[bundle_dir.name] if pin is None else pin, rounding_reading=reading,
                          allow_synthetic=synthetic, **kw)


def assert_shadow(r):
    assert isinstance(r, ShadowProviderResult)
    assert r.state == "unavailable" and r.reason == "model_not_qualified"
    assert r.percent_rounded is None and r.band is None and r.rank_score is None
    pol = r.display_policy
    assert (pol.display_state, pol.operational_qualified, pol.ranking_enabled, pol.provisional) == ("shadow", False, False, True)
    assert pol.problem() is None and r.shadow_probability is not None


# ------------------------------------------------------------------ qualified happy paths
def test_bands_bundle_is_qualified_with_the_bundles_display_policy(bundle, monkeypatch):
    b = bundle(BANDS)
    FakeScorer(monkeypatch, b, p=0.325)
    prov = qualified(b, reading="binary_exact")
    assert prov.qualified is True and prov.qualification_code is None
    r = prov.assess(make_request())
    assert r.state == "available" and not isinstance(r, ShadowProviderResult)
    pol = r.display_policy
    assert (pol.display_state, pol.operational_qualified, pol.provisional, pol.ranking_enabled) == ("bands", True, False, False)
    assert pol.suppression_reason is None and pol.model_version == "garage-synth-v1" and pol.policy_version == "garage-display-synth-v1"
    assert pol.band_edges == (20, 35) and pol.rounding_pp == 5 and pol.problem() is None
    assert (r.percent_rounded, r.band, r.rank_score) == (35, "high", 0.325)           # binary_exact reading of .325
    assert _validate_provider(r) is None
    assert pol.to_json() == json.loads((b / "QUALIFIED_V2.json").read_text())["display_policy"]


def test_percentage_ranking_bundle(bundle, monkeypatch):
    b = bundle(PCT)
    FakeScorer(monkeypatch, b, p=0.575)
    r = qualified(b, reading="decimal_repr").assess(make_request())
    assert (r.display_policy.display_state, r.display_policy.ranking_enabled, r.display_policy.operational_qualified) == ("percentage", True, True)
    assert (r.percent_rounded, r.band, r.rank_score) == (60, "high", 0.575)
    assert _validate_provider(r) is None


@pytest.mark.parametrize("p,reading,pct,band", [
    (0.175, "float_expression", 20, "medium"), (0.175, "decimal_repr", 20, "medium"), (0.175, "binary_exact", 15, "low"),
    (0.325, "float_expression", 30, "medium"), (0.325, "decimal_repr", 30, "medium"), (0.325, "binary_exact", 35, "high"),
    (0.575, "float_expression", 55, "high"), (0.575, "decimal_repr", 60, "high"), (0.575, "binary_exact", 55, "high")])
def test_each_rounding_reading_through_the_provider(bundle, monkeypatch, p, reading, pct, band):
    b = bundle(BANDS)
    FakeScorer(monkeypatch, b, p=p)
    r = qualified(b, reading=reading).assess(make_request())
    assert (r.percent_rounded, r.band, r.rank_score) == (pct, band, p)
    assert _validate_provider(r) is None


def test_scorer_refusal_stays_unavailable_under_a_qualified_bundle(bundle, monkeypatch):
    b = bundle(BANDS)
    sc = FakeScorer(monkeypatch, b)
    sc.out = json.dumps({"ok": False, "error_code": "no_prior_episode", "support_reasons": ["x"]}).encode()
    r = qualified(b).assess(make_request())
    assert r.state == "unavailable" and r.reason == "no_prior_episode" and r.percent_rounded is None and r.rank_score is None


# ------------------------------------------------------------------ fail closed: everything else is shadow
def _tamper_qualification_file(b):
    f = b / "qualification" / "display_approval.json"
    f.write_bytes(f.read_bytes() + b" ")


def _corrupt_sidecar(b):
    (b / "QUALIFIED_V2.json").write_text("{not json")


def _drop_sidecar(b):
    (b / "QUALIFIED_V2.json").unlink()


CASES = [  # id, mutate(b), ctor kwargs, expected qualification_code
    ("no_pin", None, {"pin": ""}, "pin_missing"),
    ("wrong_pin", None, {"pin": "0" * 64}, "pin_mismatch"),
    ("synthetic_refused", None, {"synthetic": False}, "synthetic_refused"),
    ("no_sidecar", _drop_sidecar, {}, "no_sidecar"),
    ("tampered_evidence", _tamper_qualification_file, {}, "qualification_file_hash_mismatch"),
    ("rounding_reading_missing", None, {"reading": None}, "rounding_reading_missing"),
    ("rounding_reading_invalid", None, {"reading": "banker"}, "rounding_reading_invalid"),
]


@pytest.mark.parametrize("cid,mutate,kw,code", CASES, ids=[c[0] for c in CASES])
def test_anything_unqualified_is_shadow_with_the_code(bundle, monkeypatch, caplog, cid, mutate, kw, code):
    b = bundle(BANDS)
    if mutate:
        mutate(b)
    FakeScorer(monkeypatch, b, p=0.325)
    with caplog.at_level(logging.INFO, logger="garage.provider.bundle"):
        prov = qualified(b, **kw) if cid != "no_pin" else BundleProvider(b, pin=None, rounding_reading="float_expression", allow_synthetic=True)
    assert prov.qualified is False and prov.qualification_code == code
    assert_shadow(prov.assess(make_request()))
    errs = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    if code == "no_sidecar":
        assert errs == []                                                    # a plain shadow bundle is the normal case, not an error
    else:
        assert errs and all(code in m for m in errs)
        text = " ".join(r.getMessage() for r in caplog.records)
        assert PINS[BANDS] not in text and "0.325" not in text and str(b) not in text    # code only: no pin, no value, no path


def test_corrupt_sidecar_is_shadow(bundle, monkeypatch):
    b = bundle(BANDS)
    _corrupt_sidecar(b)
    FakeScorer(monkeypatch, b)
    prov = qualified(b)
    assert prov.qualified is False and prov.qualification_code
    assert_shadow(prov.assess(make_request()))


def test_unexpected_exception_in_qual_v2_is_shadow(bundle, monkeypatch):
    b = bundle(BANDS)
    FakeScorer(monkeypatch, b)

    def boom(*a, **k):
        raise RuntimeError("secret detail 0.325")
    monkeypatch.setattr(bundle_v2, "read_bundle_v2", boom)
    prov = qualified(b)
    assert prov.qualified is False and prov.qualification_code == "unexpected_RuntimeError"
    assert_shadow(prov.assess(make_request()))


def test_unexpected_exception_in_the_loader_is_shadow(bundle, monkeypatch):
    b = bundle(BANDS)
    FakeScorer(monkeypatch, b)
    monkeypatch.setattr(qualification, "try_read_bundle_v2", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    prov = qualified(b)
    assert prov.qualified is False and prov.qualification_code == "unexpected_loader_error"
    assert_shadow(prov.assess(make_request()))


def test_qualification_lost_after_load_degrades_to_shadow(bundle, monkeypatch, caplog):
    b = bundle(PCT)
    FakeScorer(monkeypatch, b, p=0.3)
    prov = qualified(b)
    assert prov.assess(make_request()).state == "available"
    _tamper_qualification_file(b)
    with caplog.at_level(logging.ERROR, logger="garage.provider.bundle"):
        assert_shadow(prov.assess(make_request()))
    assert any("qualification_file_hash_mismatch" in r.getMessage() for r in caplog.records)
    shutil.rmtree(b / "qualification")
    assert_shadow(prov.assess(make_request()))


def test_pin_is_never_taken_from_the_bundle(bundle, monkeypatch):
    b = bundle(BANDS)
    FakeScorer(monkeypatch, b)
    receipt_hash = json.loads((b / "QUALIFIED_V2.json").read_text())["qualification"]["receipt"]["content_sha256"]
    assert receipt_hash == PINS[BANDS]                                        # the bundle does contain its own hash...
    prov = BundleProvider(b, rounding_reading="float_expression", allow_synthetic=True)       # ...but without an external pin it is not used
    assert prov.qualified is False and prov.qualification_code == "pin_missing"


# ------------------------------------------------------------------ pure policy checks
def _good_policy():
    return json.loads((FIX / BANDS / "QUALIFIED_V2.json").read_text())["display_policy"]


def test_policy_conversion_accepts_the_bundle_policy_and_refuses_each_mutation():
    assert qualification._policy_from(_good_policy(), "garage-synth-v1") is not None
    muts = [{"provisional": True}, {"display_state": "shadow"}, {"display_state": "simulated"}, {"operational_qualified": False},
            {"suppression_reason": "x"}, {"band_edges": [35, 20]}, {"band_edges": [20, 37]}, {"rounding_pp": 10},
            {"model_version": "other"}, {"band_labels": {"low": "L"}}, {"ranking_enabled": "yes"}]
    for m in muts:
        assert qualification._policy_from({**_good_policy(), **m}, "garage-synth-v1") is None, m
    extra = {**_good_policy(), "extra": 1}
    assert qualification._policy_from(extra, "garage-synth-v1") is None
    assert qualification._policy_from(_good_policy(), "different-model") is None
    assert qualification._policy_from(None, "garage-synth-v1") is None


# ------------------------------------------------------------------ provider_from_env seam
def _env(b, **over):
    e = {"GARAGE_MODEL_BUNDLE": str(b), "GARAGE_MODEL_BUNDLE_PIN": PINS[b.name], "GARAGE_PERCENT_ROUNDING_READING": "float_expression"}
    e.update(over)
    return {k: v for k, v in e.items() if v is not None}


def test_provider_from_env_qualifies_only_with_pin_and_reading_and_code_injected_synthetic(bundle):
    b = bundle(BANDS)
    assert provider_from_env(_env(b), allow_synthetic=True).qualified is True
    p = provider_from_env(_env(b))                                                   # the env path never allows a synthetic bundle
    assert isinstance(p, BundleProvider) and p.qualified is False and p.qualification_code == "synthetic_refused"
    assert provider_from_env(_env(b, GARAGE_MODEL_BUNDLE_SYNTHETIC="1", GARAGE_ALLOW_SYNTHETIC="1")).qualified is False
    p = provider_from_env(_env(b, GARAGE_MODEL_BUNDLE_PIN=None), allow_synthetic=True)
    assert p.qualified is False and p.qualification_code == "pin_missing"
    p = provider_from_env(_env(b, GARAGE_PERCENT_ROUNDING_READING=None), allow_synthetic=True)
    assert p.qualified is False and p.qualification_code == "rounding_reading_missing"
    p = provider_from_env(_env(b, GARAGE_PERCENT_ROUNDING_READING=" "), allow_synthetic=True)
    assert p.qualified is False and p.qualification_code == "rounding_reading_missing"


def test_provider_from_env_keeps_the_stub_fallback_and_plain_shadow_bundles(tmp_path):
    assert isinstance(provider_from_env({}), StubProvider)
    assert isinstance(provider_from_env({"GARAGE_MODEL_BUNDLE": str(tmp_path / "missing")}), StubProvider)
    assert isinstance(provider_from_env({"GARAGE_MODEL_BUNDLE": str(tmp_path / "missing"), "GARAGE_MODEL_BUNDLE_PIN": "a" * 64,
                                         "GARAGE_PERCENT_ROUNDING_READING": "binary_exact"}, allow_synthetic=True), StubProvider)
    from .test_bundle_provider import write_bundle
    p = provider_from_env({"GARAGE_MODEL_BUNDLE": str(write_bundle(tmp_path)), "GARAGE_MODEL_BUNDLE_PIN": "a" * 64,
                           "GARAGE_PERCENT_ROUNDING_READING": "binary_exact"})
    assert isinstance(p, BundleProvider) and p.qualified is False and p.qualification_code == "no_sidecar"


def test_all_readings_are_accepted_by_the_seam(bundle):
    b = bundle(BANDS)
    for r in READINGS:
        assert provider_from_env(_env(b, GARAGE_PERCENT_ROUNDING_READING=r), allow_synthetic=True).qualified is True


# ------------------------------------------------------------------ through the worker / API
def test_qualified_bands_bundle_through_the_handler_path(env, bundle, monkeypatch):
    b = bundle(BANDS)
    FakeScorer(monkeypatch, b, p=0.2537)
    env.ctx.provider = qualified(b)
    c = make_workshop(env, "A", "o@a.example")
    env.dvsa.set_history("AB12CDE", history("AB12CDE", [("2023-01-01", "PASSED")]))
    bid = import_rows(c, [("AB12CDE", day(2), "mot")])["created_booking_ids"][0]
    worker.drain(env.ctx)
    a = c.get(f"{V}/bookings/{bid}").json()["assessment"]
    assert a["state"] == "available" and a["result"] == {"percent_rounded": None, "band": "medium"}      # bands: the percentage is not shown
    assert a["display_policy"]["operational_qualified"] is True and a["display_policy"]["display_state"] == "bands"
    assert a["display_policy"]["provisional"] is False
    row = sql(env, "SELECT score_unrounded FROM assessments WHERE id = %s", (a["id"],))[0]
    assert row["score_unrounded"] is None                                                               # ranking disabled
    blob = c.get(f"{V}/bookings/{bid}").text + c.get(f"{V}/bookings").text
    assert "0.2537" not in blob and "2537" not in blob


def test_qualified_percentage_bundle_ranks_but_never_exposes_the_score(env, bundle, monkeypatch):
    b = bundle(PCT)
    FakeScorer(monkeypatch, b, p=0.2537)
    env.ctx.provider = qualified(b)
    c = make_workshop(env, "A", "o@a.example")
    env.dvsa.set_history("AB12CDE", history("AB12CDE", [("2023-01-01", "PASSED")]))
    bid = import_rows(c, [("AB12CDE", day(2), "mot")])["created_booking_ids"][0]
    worker.drain(env.ctx)
    a = c.get(f"{V}/bookings/{bid}").json()["assessment"]
    assert a["state"] == "available" and a["result"] == {"percent_rounded": 25, "band": "medium"} and a["rank_key"] is None
    assert a["display_policy"]["display_state"] == "percentage" and a["display_policy"]["ranking_enabled"] is True
    assert sql(env, "SELECT score_unrounded FROM assessments WHERE id = %s", (a["id"],))[0]["score_unrounded"] == 0.2537
    assert c.get(f"{V}/bookings").json()["items"][0]["assessment"]["rank_key"] == 1
    assert "2537" not in c.get(f"{V}/bookings/{bid}").text + c.get(f"{V}/bookings").text


def test_unqualified_bundle_through_the_handler_path_is_still_shadow(env, bundle, monkeypatch):
    b = bundle(BANDS)
    FakeScorer(monkeypatch, b, p=0.2537)
    env.ctx.provider = BundleProvider(b, rounding_reading="float_expression", allow_synthetic=True)       # no pin
    c = make_workshop(env, "A", "o@a.example")
    env.dvsa.set_history("AB12CDE", history("AB12CDE", [("2023-01-01", "PASSED")]))
    bid = import_rows(c, [("AB12CDE", day(2), "mot")])["created_booking_ids"][0]
    worker.drain(env.ctx)
    a = c.get(f"{V}/bookings/{bid}").json()["assessment"]
    assert a["state"] == "unavailable" and a["reason"] == "model_not_qualified" and a["result"] is None
    assert a["display_policy"]["operational_qualified"] is False and a["display_policy"]["display_state"] == "shadow"
