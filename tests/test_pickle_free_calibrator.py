"""Tests for the pickle-free Platt calibrator (audit P0 gf2b remediation).

The legacy platt_calibrator.pkl was pickled under scikit-learn 1.8.0 and
raises at predict time on sklearn <=1.6 (the ceiling on python:3.9), which is
exactly how production served raw probabilities silently. These tests pin the
replacement to the constants extracted from that pickle and prove the module
needs no sklearn at all.
"""

import json
import math
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from calibrator import PlattCalibrator  # noqa: E402

ARTIFACT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "catboost_production_v55",
    "calibrator.json",
)

# Extracted from platt_calibrator.pkl coef_/intercept_; equivalence to
# predict_proba verified exactly (max abs diff 0.0) before the swap.
GOLDEN_A = 6.781078410783767
GOLDEN_B = -3.0256923848070487


def test_artifact_exists_and_matches_golden_constants():
    with open(ARTIFACT) as f:
        spec = json.load(f)
    assert spec["type"] == "platt_sigmoid"
    assert spec["A"] == GOLDEN_A
    assert spec["B"] == GOLDEN_B


def test_from_json_reproduces_pickle_mapping():
    cal = PlattCalibrator.from_json(ARTIFACT)
    # Golden values computed as sigmoid(A*x + B) with the extracted constants —
    # byte-equivalent to the pickle's predict_proba output.
    for raw in (0.05, 0.25, 0.5, 0.75, 0.95):
        expected = 1.0 / (1.0 + math.exp(-(GOLDEN_A * raw + GOLDEN_B)))
        assert abs(cal.calibrate(raw) - expected) < 1e-15
        p0, p1 = cal.predict_proba([[raw]])[0]
        assert abs(p1 - expected) < 1e-15
        assert abs(p0 + p1 - 1.0) < 1e-15


def test_self_check_passes_on_production_constants():
    cal = PlattCalibrator.from_json(ARTIFACT)
    cal.self_check()  # must not raise


def test_self_check_rejects_degenerate_calibrator():
    import pytest

    flat = PlattCalibrator(A=0.0, B=0.0)  # constant 0.5 output, non-monotone
    with pytest.raises(ValueError):
        flat.self_check()


def test_module_is_sklearn_free():
    import calibrator as calibrator_module

    assert "sklearn" not in calibrator_module.__dict__
    with open(calibrator_module.__file__.replace(".pyc", ".py")) as f:
        src = f.read()
    assert "import sklearn" not in src and "from sklearn" not in src
