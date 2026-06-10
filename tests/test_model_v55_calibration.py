"""Regression test: the Platt calibrator must be applied in the domain it was
fitted on (raw probabilities, per train_catboost_production_v55.py).

Fails on the pre-fix code, which transformed to log-odds before calling the
calibrator and thereby collapsed most served probabilities toward 0.
"""
from datetime import datetime

import numpy as np
import pytest

import model_v55
from dvsa_client import VehicleHistory


@pytest.fixture(scope="module")
def loaded_model():
    assert model_v55.load_model(), "model artefacts must load"
    return model_v55


def _expected_calibrated(raw_prob: float) -> float:
    """The calibrator's mapping applied in its fitted domain (raw prob)."""
    calibrator = model_v55._calibrator
    return float(calibrator.predict_proba([[raw_prob]])[0][1])


def _make_history(year: int, mot_tests=None) -> VehicleHistory:
    return VehicleHistory(
        registration="TEST123",
        make="FORD",
        model="FOCUS",
        fuel_type="PE",
        colour="BLUE",
        registration_date=datetime(year, 6, 1),
        manufacture_date=datetime(year, 6, 1),
        engine_size=1600,
        mot_tests=mot_tests or [],
    )


def test_calibrated_output_matches_calibrator_domain(loaded_model):
    """predict_risk must produce exactly calibrator(raw_prob), 4dp-rounded."""
    history = _make_history(2015)
    features = model_v55.engineer_features_with_stats(history, "RG1 1AA")
    result = model_v55.predict_risk(features)

    expected = _expected_calibrated(result["raw_probability"])
    assert result["failure_risk"] == pytest.approx(expected, abs=2e-4), (
        f"failure_risk {result['failure_risk']} != calibrator(raw="
        f"{result['raw_probability']}) = {expected:.4f} — calibrator applied "
        "outside its fitted domain?"
    )


def test_midrange_probability_not_collapsed(loaded_model):
    """The pre-fix log-odds bug mapped mid-range raw risks to ~0. A raw
    probability near the population base rate must calibrate to a mid-range
    probability, not collapse below 5%."""
    calibrator = model_v55._calibrator
    for raw in (0.20, 0.28, 0.40):
        served = float(calibrator.predict_proba([[raw]])[0][1])
        assert served > 0.05, (
            f"calibrator({raw}) = {served:.4f}: mid-range risk collapsed — "
            "input domain mismatch"
        )


def test_calibration_is_monotone(loaded_model):
    calibrator = model_v55._calibrator
    grid = np.linspace(0.02, 0.95, 20)
    out = [float(calibrator.predict_proba([[p]])[0][1]) for p in grid]
    assert all(b >= a for a, b in zip(out, out[1:])), "calibration not monotone"
