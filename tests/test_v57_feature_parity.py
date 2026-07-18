"""Serving-path tests for feature_engineering_v57.

Scaffold-governed semantics (model_bundle, Stage-1 directive 2026-06-12):
- history window cap 2019-01-01 applied to the DVSA history BEFORE feature
  engineering (training applies the same cap to the lake),
- RC-3 features absent, observed-history renames applied, five coverage
  features appended,
- v55 serving semantics otherwise inherited (fe_v57 delegates to fe_v55 on
  the capped history).
"""

import os
import sys
from datetime import datetime

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dvsa_client import MOTTest, VehicleHistory  # noqa: E402
from feature_engineering_v57 import engineer_features, features_to_array  # noqa: E402
from model_contract_v57 import (  # noqa: E402
    FEATURE_NAMES,
    HISTORY_WINDOW_START,
)

PREDICTION_DATE = datetime(2024, 1, 1)


def _test(date_text, result="PASSED", mileage=50000, defects=None):
    return MOTTest(
        test_date=datetime.fromisoformat(date_text),
        test_result=result,
        expiry_date=None,
        odometer_value=mileage,
        odometer_unit="mi",
        test_number="",
        defects=defects or [],
    )


def _history(mot_tests, manufacture="2014-01-01", registration="2014-03-01"):
    return VehicleHistory(
        registration="AB12CDE",
        make="FORD",
        model="FOCUS",
        fuel_type=None,
        colour=None,
        registration_date=datetime.fromisoformat(registration) if registration else None,
        manufacture_date=datetime.fromisoformat(manufacture) if manufacture else None,
        engine_size=1600,
        mot_tests=mot_tests,
    )


def test_v57_ignores_pre_window_history_for_history_counts():
    # Old vehicle (liable since 2017) with one pre-window test and two
    # in-window tests. Pre-window test must not reach any history feature.
    history = _history([
        _test("2017-05-01T00:00:00", "PASSED", 20000),
        _test("2020-06-01T00:00:00", "FAILED", 30000),
        _test("2022-06-01T00:00:00", "PASSED", 42000),
    ])

    features = engineer_features(history, postcode="SW1A 1AA", prediction_date=PREDICTION_DATE)

    assert HISTORY_WINDOW_START.isoformat() == "2019-01-01"
    assert features["n_prior_tests_observed"] == 2
    assert features["n_prior_fails_observed"] == 1
    assert features["prior_fail_rate_observed"] == pytest.approx(0.5)

    # Coverage features (scaffold definitions)
    assert features["window_days_available"] == 1826  # 2019-01-01 -> 2024-01-01
    assert features["has_prior_test_observed"] == 1
    assert features["history_years_observed"] == pytest.approx(1309 / 365.25, abs=1e-6)
    assert features["has_left_truncated_history"] == 1  # liable from 2017-03-01
    assert features["first_observed_test_is_not_true_first"] == 1

    # v55 serving frames inherited on the capped list
    assert features["prev_cycle_outcome_band"] == "pass"      # latest in-window
    assert features["days_since_last_test"] == 730            # gap between two latest priors
    assert features["days_since_pass_ratio"] == pytest.approx(730 / 365.0)
    assert features["gap_band"] == "very_late"
    assert features["advisory_trend"] == "stable"             # 0 vs 0 advisories
    assert features["test_month"] == "6"
    assert features["day_of_week"] == "2"                     # 2022-06-01 is a Wednesday
    assert features["test_mileage"] == 42000
    assert features["annualized_mileage_v2"] == pytest.approx(12000 / 730 * 365)
    assert features["usage_band_hybrid"] == "average"


def test_v57_young_vehicle_is_not_left_truncated():
    history = _history(
        [_test("2022-06-01T00:00:00", "PASSED", 42000)],
        manufacture="2018-01-01",
        registration="2018-03-01",
    )

    features = engineer_features(history, postcode="SW1A 1AA", prediction_date=PREDICTION_DATE)

    # Liable from 2021-03-01 >= window start: no truncated history possible.
    assert features["has_left_truncated_history"] == 0
    assert features["first_observed_test_is_not_true_first"] == 0
    assert features["n_prior_tests_observed"] == 1
    assert features["n_prior_fails_observed"] == 0
    assert features["has_prior_test_observed"] == 1
    assert features["history_years_observed"] == pytest.approx(579 / 365.25, abs=1e-6)
    # Single in-window test: serving emits the no-pair defaults.
    assert features["days_since_last_test"] == 0
    assert features["days_since_pass_ratio"] == 0.0
    assert features["gap_band"] == "first_test"
    assert features["advisory_trend"] == "unknown"


def test_v57_rookie_with_no_tests():
    history = _history([], manufacture="2021-06-01", registration="2021-08-01")

    features = engineer_features(history, postcode="SW1A 1AA", prediction_date=PREDICTION_DATE)

    assert features["has_prior_test_observed"] == 0
    assert features["history_years_observed"] == 0.0
    assert features["n_prior_tests_observed"] == 0
    assert features["prev_cycle_outcome_band"] == "first_test"
    assert features["dominant_mechanism"] == "NO_HISTORY"
    assert features["has_left_truncated_history"] == 0
    assert features["window_days_available"] == 1826


def test_v57_outputs_exact_contract_order_without_dropped_or_old_names():
    history = _history([_test("2022-06-01T00:00:00", "PASSED", 42000)])
    features = engineer_features(history, postcode="SW1A 1AA", prediction_date=PREDICTION_DATE)
    values = features_to_array(features)

    assert len(values) == len(FEATURE_NAMES) == 105

    # RC-3 drop-set never emitted
    for dropped in (
        "station_fail_rate_smoothed",
        "station_x_prev_outcome_fail_rate",
        "station_strictness_bias",
        "suspension_risk_profile",
    ):
        assert dropped not in features

    # Renames applied: old accumulation names gone, observed names present
    assert "n_prior_tests" not in features
    assert "prior_fail_rate_smoothed" not in features
    assert "n_prior_tests_observed" in features
    assert "fails_last_365d_observed" in features

    # Array order is the contract order
    assert values[FEATURE_NAMES.index("test_mileage")] == features["test_mileage"]
    assert values[-5:] == [
        features["window_days_available"],
        features["history_years_observed"],
        features["has_prior_test_observed"],
        features["has_left_truncated_history"],
        features["first_observed_test_is_not_true_first"],
    ]


def test_v57_features_to_array_validates_missing_keys():
    with pytest.raises(ValueError, match="Missing"):
        features_to_array({"test_mileage": 1}, validate=True)
