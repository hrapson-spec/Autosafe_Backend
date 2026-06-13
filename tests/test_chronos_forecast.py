"""Tests for the Chronos-2 forward-looking forecast feature integration.

Covers the parts that run without torch/Chronos or the external data substrate:
feature-name parity, the parity-critical age-band normaliser, the serving
fold-down fallback chain, the engineer_features integration, and the offline
job's pure rolling-origin / assembly helpers.
"""
import os
import sys
from datetime import datetime

import pandas as pd
import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import feature_engineering_v55 as fe
import forecast_segment_rates as fsr
from dvsa_client import VehicleHistory


# --- feature-name parity ----------------------------------------------------
def test_chronos_feature_names_shape():
    assert fe.CHRONOS_FEATURE_NAMES[0] == "chronos_seg_fail_forecast"
    assert len(fe.CHRONOS_FEATURE_NAMES) == 1 + len(fe.CHRONOS_COMPONENTS)
    assert len(fe.CHRONOS_FEATURE_NAMES) == 8  # overall + 7 components
    # Must match the trainer's expectation exactly (train script defines the
    # same list; this guards drift between the two).
    expected = ["chronos_seg_fail_forecast"] + [
        f"chronos_seg_{c}_forecast"
        for c in ["brakes", "suspension", "tyres", "steering", "visibility", "lamps", "body"]
    ]
    assert fe.CHRONOS_FEATURE_NAMES == expected


def test_base_feature_names_unchanged():
    # The deployed model is 104 features; the base contract must not grow until retrain.
    assert len(fe.FEATURE_NAMES) == 104
    assert not any(n.startswith("chronos_") for n in fe.FEATURE_NAMES)


# --- parity-critical age-band normaliser ------------------------------------
@pytest.mark.parametrize("age,expected", [
    (0, "0-2"), (2, "0-2"), (3, "0-2"),   # get_age_band -> '0-3' must normalise to '0-2'
    (4, "3-5"), (5, "3-5"),
    (7, "6-10"), (10, "6-10"),
    (12, "11-15"), (15, "11-15"),
    (20, "15+"),
])
def test_normalize_age_band_matches_training_canonical(age, expected):
    assert fe.normalize_age_band(fe.get_age_band(age)) == expected


def test_mileage_to_band():
    assert fe.mileage_to_band(0) == "0-30k"
    assert fe.mileage_to_band(45000) == "30k-60k"
    assert fe.mileage_to_band(80000) == "60k-100k"
    assert fe.mileage_to_band(150000) == "100k+"


# --- fold-down fallback chain (segment -> make -> global -> latest -> default)
def _forecast(overall=None, make=None, glob=None, latest=None):
    return {
        "default_rate": 0.28, "latest_asof_month": latest,
        "overall": overall or {}, "components": {},
        "make": make or {}, "make_components": {},
        "global": glob or {}, "global_components": {},
    }


def test_lookup_none_returns_base_rate():
    out = fe.lookup_chronos_forecasts(None, "FORD FOCUS", "FORD", "3-5", "0-30k", "2026-06-01")
    assert set(out) == set(fe.CHRONOS_FEATURE_NAMES)
    assert all(v == 0.28 for v in out.values())


def test_lookup_segment_hit():
    cf = _forecast(overall={("FORD FOCUS", "3-5", "0-30k", "2026-06-01"): 0.41})
    out = fe.lookup_chronos_forecasts(cf, "FORD FOCUS", "FORD", "3-5", "0-30k", "2026-06-01")
    assert out["chronos_seg_fail_forecast"] == pytest.approx(0.41)


def test_lookup_make_then_global_then_latest_fallback():
    # No segment -> make
    cf = _forecast(make={("FORD", "2026-06-01"): 0.33})
    out = fe.lookup_chronos_forecasts(cf, "FORD FOCUS", "FORD", "3-5", "0-30k", "2026-06-01")
    assert out["chronos_seg_fail_forecast"] == pytest.approx(0.33)
    # No segment/make for this month -> global
    cf = _forecast(glob={"2026-06-01": 0.30})
    out = fe.lookup_chronos_forecasts(cf, "X", "X", "3-5", "0-30k", "2026-06-01")
    assert out["chronos_seg_fail_forecast"] == pytest.approx(0.30)
    # Nothing for the requested month -> latest_asof_month fallback
    cf = _forecast(glob={"2026-05-01": 0.26}, latest="2026-05-01")
    out = fe.lookup_chronos_forecasts(cf, "X", "X", "3-5", "0-30k", "2026-12-01")
    assert out["chronos_seg_fail_forecast"] == pytest.approx(0.26)
    # Truly empty -> default_rate
    out = fe.lookup_chronos_forecasts(_forecast(), "X", "X", "3-5", "0-30k", "2026-06-01")
    assert out["chronos_seg_fail_forecast"] == pytest.approx(0.28)


def test_lookup_component_resolution():
    cf = _forecast()
    cf["components"]["brakes"] = {("FORD FOCUS", "3-5", "0-30k", "2026-06-01"): 0.12}
    out = fe.lookup_chronos_forecasts(cf, "FORD FOCUS", "FORD", "3-5", "0-30k", "2026-06-01")
    assert out["chronos_seg_brakes_forecast"] == pytest.approx(0.12)
    assert out["chronos_seg_suspension_forecast"] == pytest.approx(0.28)  # untouched -> default


# --- engineer_features integration ------------------------------------------
def _history(make="FORD", model="FOCUS", manufacture_year=2015):
    return VehicleHistory(
        registration="TEST123", make=make, model=model, fuel_type="PE", colour="BLUE",
        registration_date=datetime(manufacture_year, 6, 1),
        manufacture_date=datetime(manufacture_year, 6, 1),
        engine_size=1600, mot_tests=[],
    )


def test_engineer_features_emits_chronos_keys_with_default_when_absent():
    feats = fe.engineer_features(_history(), "RG1 1AA", prediction_date=datetime(2026, 6, 13))
    for name in fe.CHRONOS_FEATURE_NAMES:
        assert name in feats
        assert feats[name] == pytest.approx(0.28)


def test_engineer_features_uses_chronos_lookup():
    # age 2026-2015 = 11 -> '11-15'; mileage 0 -> '0-30k'; month 2026-06-01
    cf = _forecast(overall={("FORD FOCUS", "11-15", "0-30k", "2026-06-01"): 0.47})
    feats = fe.engineer_features(_history(), "RG1 1AA",
                                 prediction_date=datetime(2026, 6, 13), chronos_forecast=cf)
    assert feats["chronos_seg_fail_forecast"] == pytest.approx(0.47)


def test_features_to_array_respects_explicit_order():
    feats = fe.engineer_features(_history(), "RG1 1AA", prediction_date=datetime(2026, 6, 13))
    order = fe.FEATURE_NAMES + ["chronos_seg_fail_forecast"]
    arr = fe.features_to_array(feats, feature_names=order)
    assert len(arr) == 105
    assert arr[-1] == pytest.approx(0.28)


# --- offline job pure helpers ------------------------------------------------
def test_next_month_and_rolling_origins():
    assert fsr.next_month(pd.Timestamp("2026-01-15")) == pd.Timestamp("2026-02-01")
    months = pd.date_range("2019-01-01", periods=14, freq="MS").tolist()
    origins = fsr.rolling_origins(months, min_history=12)
    # need >=12 months <= O: first eligible origin is the 12th month
    assert origins[0] == pd.Timestamp("2019-12-01")
    assert all(o <= pd.Timestamp("2020-02-01") for o in origins)


def test_asof_invariant_rejects_leak():
    # history up to March used for a forecast valid for March -> leak
    with pytest.raises(AssertionError):
        fsr.assert_asof_invariant(pd.Timestamp("2026-03-20"), pd.Timestamp("2026-03-01"))
    # history up to Feb for a March forecast -> ok
    fsr.assert_asof_invariant(pd.Timestamp("2026-02-28"), pd.Timestamp("2026-03-01"))


def test_future_covariates_winter_flag():
    assert fsr.build_future_covariates(pd.Timestamp("2026-01-01"))["is_winter"] == 1
    assert fsr.build_future_covariates(pd.Timestamp("2026-07-01"))["is_winter"] == 0


def test_assemble_serving_pkl_structure():
    seg = pd.DataFrame([{
        "asof_month": pd.Timestamp("2026-06-01"), "model_id": "FORD FOCUS",
        "age_band": "11-15", "mileage_band": "0-30k",
        **{c: 0.4 for c in fe.CHRONOS_FEATURE_NAMES},
    }])
    make = pd.DataFrame([{
        "asof_month": pd.Timestamp("2026-06-01"), "make": "FORD",
        **{c: 0.3 for c in fe.CHRONOS_FEATURE_NAMES},
    }])
    glob = pd.DataFrame([{
        "asof_month": pd.Timestamp("2026-06-01"),
        **{c: 0.28 for c in fe.CHRONOS_FEATURE_NAMES},
    }])
    pkl = fsr.assemble_serving_pkl(seg, make, glob)
    assert pkl["latest_asof_month"] == "2026-06-01"
    assert pkl["overall"][("FORD FOCUS", "11-15", "0-30k", "2026-06-01")] == pytest.approx(0.4)
    assert pkl["make"][("FORD", "2026-06-01")] == pytest.approx(0.3)
    assert pkl["global"]["2026-06-01"] == pytest.approx(0.28)
    # Round-trips through the serving lookup.
    out = fe.lookup_chronos_forecasts(pkl, "FORD FOCUS", "FORD", "11-15", "0-30k", "2026-06-01")
    assert out["chronos_seg_fail_forecast"] == pytest.approx(0.4)
