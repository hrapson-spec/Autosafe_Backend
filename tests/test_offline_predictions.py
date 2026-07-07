"""Offline regression tests for the V55 prediction pipeline — no DVSA API.

Locks the behaviour two shipped regressions broke:
  * the log-odds calibration bug (served risk collapsed toward 0)
  * the constant-feature bug (every car scored the same)
by pinning per-fixture golden predictions and the calibrator-domain invariant.
Fixtures and goldens are produced by offline_eval/persist_fixtures.py.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import model_v55  # noqa: E402
from dvsa_client import MOTTest, VehicleHistory  # noqa: E402
from offline_eval.harness import make_client, score_record  # noqa: E402

GOLDEN_DIR = REPO / "tests/fixtures/golden"
GOLDEN_PRED = json.loads((REPO / "tests/fixtures/golden_predictions.json").read_text())
MODEL_CBM = REPO / "catboost_production_v55/model.cbm"
TOL = 1e-6
PRED_DATE = datetime.fromisoformat(GOLDEN_PRED["_meta"]["prediction_date"])
GOLDEN_IDS = [k for k in GOLDEN_PRED if k != "_meta"]


@pytest.fixture(scope="module", autouse=True)
def _loaded():
    assert model_v55.load_model(), "model artefacts must load"


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def test_golden_model_unchanged():
    """If the model binary changed, golden predictions are stale by definition;
    fail loudly so they are regenerated deliberately (the regression tripwire)."""
    assert _sha256(MODEL_CBM) == GOLDEN_PRED["_meta"]["model_cbm_sha256"], (
        "model.cbm changed since goldens were generated — rerun "
        "offline_eval/persist_fixtures.py to refresh golden_predictions.json"
    )


@pytest.mark.parametrize("tid", GOLDEN_IDS)
def test_golden_prediction_stable(tid):
    """Each fixture must score exactly as recorded — catches any silent drift
    in feature engineering, the model, or calibration."""
    client = make_client()
    record = json.loads((GOLDEN_DIR / f"{tid}.json").read_text())
    pred = score_record(client, record, PRED_DATE)
    exp = GOLDEN_PRED[tid]
    assert pred["raw_probability"] == pytest.approx(exp["raw_probability"], abs=TOL), (
        f"{tid} ({exp['category']}) raw drifted: {pred['raw_probability']} "
        f"vs golden {exp['raw_probability']}"
    )
    assert pred["failure_risk"] == pytest.approx(exp["failure_risk"], abs=TOL)


@pytest.mark.parametrize("tid", GOLDEN_IDS)
def test_calibration_in_fitted_domain(tid):
    """Served risk must equal calibrator(raw_probability) — the exact invariant
    the log-odds bug violated."""
    client = make_client()
    record = json.loads((GOLDEN_DIR / f"{tid}.json").read_text())
    pred = score_record(client, record, PRED_DATE)
    expected = float(
        model_v55._calibrator.predict_proba([[pred["raw_probability"]]])[0][1])
    assert pred["failure_risk"] == pytest.approx(expected, abs=2e-4), (
        f"{tid}: failure_risk {pred['failure_risk']} != calibrator(raw) "
        f"{expected:.4f} — calibrator applied outside its fitted domain?"
    )


def test_predictions_not_degenerate():
    """The constant-feature era gave ~identical risk to every car. Real fixtures
    must span a meaningful range."""
    raws = [GOLDEN_PRED[t]["raw_probability"] for t in GOLDEN_IDS]
    assert max(raws) - min(raws) > 0.15, f"golden raws nearly constant: {raws}"


def _history(year, tests, make="FORD", model="FOCUS"):
    mot = [MOTTest(test_date=d, test_result=r, expiry_date=None,
                   odometer_value=m, odometer_unit="mi", test_number=f"T{i}",
                   defects=dfx or []) for i, (d, r, m, dfx) in enumerate(tests)]
    mot.sort(key=lambda t: t.test_date, reverse=True)
    return VehicleHistory(registration="SYN1", make=make, model=model,
                          fuel_type="PE", colour="BLUE",
                          registration_date=datetime(year, 6, 1),
                          manufacture_date=datetime(year, 6, 1),
                          engine_size=1600, mot_tests=mot)


def _raw(history):
    feats = model_v55.engineer_features_with_stats(history, "RG1 1AA", PRED_DATE)
    return model_v55.predict_risk(feats)["raw_probability"]


def test_monotonic_in_mileage():
    """Higher latest odometer must not reduce risk (synthetic, deployed path)."""
    D = datetime
    def hist(m):
        return _history(2015, [(D(2025, 5, 15), "PASSED", m, []),
                               (D(2024, 5, 10), "PASSED", m - 8000, []),
                               (D(2023, 5, 12), "PASSED", m - 16000, [])])
    scores = [_raw(hist(m)) for m in (30000, 70000, 120000, 180000)]
    assert all(b >= a - 1e-9 for a, b in zip(scores, scores[1:])), scores


def test_monotonic_in_advisories():
    """More advisories on the latest test must not reduce risk."""
    D = datetime
    adv = [{"type": "ADVISORY", "text": "Tyre worn", "dangerous": False}]
    def hist(k):
        return _history(2015, [(D(2025, 5, 15), "PASSED", 60000, adv * k),
                               (D(2024, 5, 10), "PASSED", 52000, []),
                               (D(2023, 5, 12), "PASSED", 44000, [])])
    scores = [_raw(hist(k)) for k in (0, 1, 3, 5)]
    assert all(b >= a - 1e-9 for a, b in zip(scores, scores[1:])), scores


def test_scrub_lossless():
    """Registration is not a feature: scrubbing the VRM must not change the
    prediction (the property that makes committing scrubbed fixtures safe)."""
    client = make_client()
    record = json.loads((GOLDEN_DIR / f"{GOLDEN_IDS[0]}.json").read_text())
    base = score_record(client, record, PRED_DATE)["raw_probability"]
    for vrm in ("AB12CDE", "ZZ99ZZZ", "TESTXXXX"):
        alt = dict(record, registration=vrm)
        assert score_record(client, alt, PRED_DATE)["raw_probability"] == base
