"""INT-W3b: the app provider against the REAL synthetic fixture BundleV1 (real scorer subprocess, bundle's recorded runtime).

Skipped unless GARAGE_TEST_MODEL_BUNDLE points at a BundleV1 directory. Optional GARAGE_TEST_MODEL_BUNDLE_SHA256 pins the
expected manifest_content_sha256. Histories are authored synthetic data (never T1b/raw); the bundle is a synthetic fixture
(`synthetic_fixture:true`, tiny trees), so the scores mean nothing: this tests the seam, not the model.
Per-call latencies (MEASURED, wall clock around BundleProvider.assess) are printed as `LATENCY ...` lines (run with -s).
"""
import json
import os
import pathlib
import time
from datetime import date, datetime, timezone

import pytest

from garage.api import worker
from garage.api.dvsa import parse_api_response
from garage.api.provider import AssessmentRequest
from garage.api.providers import BundleProvider, ShadowProviderResult
from .conftest import day, import_rows, make_workshop, sql

BUNDLE = os.environ.get("GARAGE_TEST_MODEL_BUNDLE")
pytestmark = pytest.mark.skipif(not BUNDLE, reason="GARAGE_TEST_MODEL_BUNDLE not set (needs the INT-W2b fixture BundleV1 directory)")

V = "/api/garage/v1"
BRAKE = "Nearside front brake pad(s) worn to the wear indicator (1.1.13 (a) (i))"   # text the fixture bundle's mapper accepts
MARKER = "RAWONLY-w3b-5c1d"
LATENCY: list = []


def _t(d, res, odo, unit, defs=(), rtype="READ"):
    return {"completedDate": d + "T10:00:00.000Z", "testResult": res, "motTestNumber": "SYN" + d.replace("-", ""),
            "odometerValue": None if odo is None else str(odo), "odometerUnit": unit, "odometerResultType": rtype,
            "dataSource": "DVSA", "defects": [{"type": x, "text": BRAKE, "dangerous": x == "DANGEROUS"} for x in defs]}


def _vehicle(tests):
    return {"make": "FORD", "model": "FOCUS", "fuelType": "Petrol", "engineSize": "1600", "primaryColour": "Blue",
            "firstUsedDate": "2012-03-01", "registrationDate": "2012-03-01", "manufactureDate": "2012-01-01",
            "x_marker": MARKER, "motTests": tests}


# <=2022-style: no test after 2021 (a 2022-target row would be built from these)
OLD = [_t("2018-03-05", "PASSED", 42000, "MI", ["ADVISORY"]), _t("2019-03-04", "FAILED", 52000, "MI", ["MAJOR", "ADVISORY"]),
       _t("2019-03-12", "PASSED", 52010, "MI", ["ADVISORY"]), _t("2020-03-09", "PASSED", 64000, "MI", ["MINOR"]),
       _t("2021-03-10", "PASSED", 75000, "MI", ["ADVISORY"])]
# live-shaped 2023-2025 history with KM READ readings
LIVE = [_t("2020-03-09", "PASSED", 100000, "KM", ["ADVISORY"]), _t("2021-03-10", "PASSED", 120000, "KM", ["ADVISORY"]),
        _t("2022-03-08", "PASSED", 140000, "KM", ["ADVISORY"]), _t("2023-03-07", "FAILED", 150000, "KM", ["MAJOR", "ADVISORY"]),
        _t("2024-03-05", "PASSED", 160000, "KM", ["ADVISORY"]), _t("2025-03-04", "PASSED", 170000, "KM", ["ADVISORY"])]
# out of design: a test already in the cutoff year (booking day is in 2026) -> scorer error_code test_already_this_year
OOD = OLD + [_t("2026-01-10", "PASSED", 80000, "MI", ["ADVISORY"])]


class Recording(BundleProvider):
    """Records each call's exact scorer stdout and wall-clock latency."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.stdouts = []

    def _parse(self, vb, out):
        self.stdouts.append(out)
        return super()._parse(vb, out)

    def assess(self, req):
        t0 = time.monotonic()
        r = super().assess(req)
        LATENCY.append((req.registration, round(time.monotonic() - t0, 3), r.reason))
        return r


@pytest.fixture(scope="module")
def bundle_dir():
    d = pathlib.Path(BUNDLE)
    m = json.loads((d / "manifest.json").read_text())
    pin = os.environ.get("GARAGE_TEST_MODEL_BUNDLE_SHA256")
    if pin:
        assert m["manifest_content_sha256"] == pin
    assert m["synthetic_fixture"] is True and m["qualified"] is False
    return d


def _fixture_dir(env, tmp_path, vehicles: dict):
    d = tmp_path / "fx"
    d.mkdir()
    for reg, tests in vehicles.items():
        (d / f"{reg}.json").write_bytes(json.dumps(_vehicle(tests), indent=1, sort_keys=True).encode())
    env.dvsa.fixture_dir = d


def _walk(o, keys):
    if isinstance(o, dict):
        for k, v in o.items():
            keys.add(k)
            _walk(v, keys)
    elif isinstance(o, list):
        for v in o:
            _walk(v, keys)


def test_real_bundle_through_the_handler_path(env, tmp_path, bundle_dir):
    prov = Recording(bundle_dir, timeout_s=60)
    manifest_sha = json.loads((bundle_dir / "manifest.json").read_text())["manifest_content_sha256"]
    assert prov.manifest_sha256 == manifest_sha
    c = make_workshop(env, "A", "o@a.example")
    env.ctx.provider = prov
    _fixture_dir(env, tmp_path, {"ZZ81OLD": OLD, "ZZ82LIV": LIVE, "ZZ83OOD": OOD})
    ids = {}
    for reg in ("ZZ81OLD", "ZZ82LIV", "ZZ83OOD"):
        ids[reg] = import_rows(c, [(reg, day(2), "mot")])["created_booking_ids"][0]
    worker.drain(env.ctx)
    assert len(prov.stdouts) == 3
    outs = {}
    for reg, raw in zip(("ZZ81OLD", "ZZ82LIV", "ZZ83OOD"), prov.stdouts):   # drain order = import order
        outs[reg] = json.loads(raw)
    rows = {reg: sql(env, "SELECT * FROM assessments WHERE booking_id=%s", (bid,))[0] for reg, bid in ids.items()}

    for reg in ("ZZ81OLD", "ZZ82LIV"):
        out, row = outs[reg], rows[reg]
        assert out["ok"] is True and out["bundle_manifest_sha256"] == manifest_sha
        assert (row["state"], row["reason"]) == ("unavailable", "model_not_qualified")
        assert row["display_policy"]["display_state"] == "shadow" and row["display_policy"]["ranking_enabled"] is False
        assert row["display_policy"]["operational_qualified"] is False and row["display_policy"]["model_version"] == out["model_version"]
        assert row["shadow_margin"] == out["margin"] and row["shadow_probability"] == out["probability"]   # exact scorer stdout
        assert row["shadow_bundle_sha256"] == manifest_sha and row["shadow_model_version"] == out["model_version"]
        assert row["result"] is None and row["rank_key"] is None and row["score_unrounded"] is None
        snap = sql(env, "SELECT raw_response, raw_sha256 FROM source_snapshots WHERE id=%s", (row["snapshot_id"],))[0]
        assert MARKER.encode() in bytes(snap["raw_response"]) and snap["raw_sha256"]
    # out-of-design: scorer's own error_code is the reason; no shadow values
    ood = rows["ZZ83OOD"]
    assert outs["ZZ83OOD"] == {"error_code": "test_already_this_year", "ok": False, "support_reasons": []}
    assert (ood["state"], ood["reason"]) == ("unavailable", "test_already_this_year")
    assert ood["shadow_margin"] is None and ood["shadow_probability"] is None and ood["display_policy"]["display_state"] == "unavailable"

    # nothing from the scorer or the raw JSON reaches any response
    needles = [MARKER, "RAWONLY", "primaryColour", "odometerResultType", "raw_response", "raw_sha256", manifest_sha]
    for reg in ("ZZ81OLD", "ZZ82LIV"):
        needles += [repr(outs[reg]["margin"]), repr(outs[reg]["probability"]), str(outs[reg]["margin"])[:9]]
    listing = c.get(f"{V}/bookings")
    assert all(i["assessment"]["rank_key"] is None for i in listing.json()["items"])
    for bid in ids.values():
        for r in (c.get(f"{V}/bookings/{bid}"), c.get(f"{V}/bookings/{bid}/assessments"), c.get(f"{V}/bookings/{bid}/briefing"),
                  c.get(f"{V}/bookings/{bid}/decisions"), c.get(f"{V}/bookings/{bid}/outcomes"), listing):
            assert r.status_code == 200, r.text
            keys = set()
            _walk(r.json(), keys)
            assert not [k for k in keys if k.startswith("shadow") or k in ("margin", "probability", "score_unrounded")], keys
            for n in needles:
                assert n not in r.text, (n, r.request.url.path)
            if r.request.url.path.endswith(bid):
                a = r.json()["assessment"]
                assert a["result"] is None and a["rank_key"] is None
    for line in LATENCY:
        print("LATENCY", line)


def test_real_scorer_with_a_2022_target_cutoff_and_stdout_equality(tmp_path, bundle_dir):
    """True <=2022-target-style call: cutoff in 2022 on the old history (provider unit path, no DB)."""
    prov = Recording(bundle_dir, timeout_s=60)
    h = parse_api_response("ZZ81OLD", _vehicle(OLD), raw=json.dumps(_vehicle(OLD)).encode())
    req = AssessmentRequest(registration="ZZ81OLD", booking_type="mot", appointment_date=date(2022, 3, 20),
                            cutoff_date=date(2022, 3, 20), cutoff_at=datetime(2022, 3, 20, 6, tzinfo=timezone.utc), history=h)
    r = prov.assess(req)
    assert isinstance(r, ShadowProviderResult) and r.display_policy.display_state == "shadow"
    out = json.loads(prov.stdouts[0])
    assert (r.shadow_margin, r.shadow_probability) == (out["margin"], out["probability"])
    assert r.percent_rounded is None and r.band is None and r.rank_score is None
    again = prov.assess(req)                       # deterministic: second subprocess gives the same bytes
    assert prov.stdouts[1] == prov.stdouts[0] and again.shadow_margin == r.shadow_margin
    print("LATENCY", LATENCY[-2:])
