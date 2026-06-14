"""Tests for faithful_inject.py — candidate-FAITHFUL injection (review pt 11).

The promotion path must not use the dev `mot_tests=[]` shim. The reconstruction is a
single pure function (no drift) and the GOLDEN test pins the candidate value for a known
packet so an elaborate-but-wrong reconstruction is caught.
"""
import datetime as dt

import candidate_feature as cf
from faithful_inject import reconstruct_history_kwargs, compute_candidate_over_history


def _rows():
    fud = dt.datetime(2014, 1, 1)
    tgt = dt.datetime(2024, 1, 1)
    common = dict(tgt_id=1, tgt_date=tgt, tgt_fud=fud, tgt_make="FORD", tgt_model_id="FOCUS")
    return [
        dict(common, p_test_id=101, p_date=dt.datetime(2023, 1, 1), p_result="PASSED",
             p_miles=50000, p_fud=fud, p_make="FORD", p_model="FOCUS", p_fuel="PETROL",
             defects_json=None),
        dict(common, p_test_id=100, p_date=dt.datetime(2022, 1, 1), p_result="PASSED",
             p_miles=40000, p_fud=fud, p_make="FORD", p_model="FOCUS", p_fuel="PETROL",
             defects_json=None),
    ]


def test_reconstruct_populates_mot_tests_and_anchor():
    kw = reconstruct_history_kwargs(_rows())
    assert kw["registration_date"] == dt.datetime(2014, 1, 1)
    assert kw["make"] == "FORD" and kw["fuel_type"] == "PETROL"
    assert len(kw["mot_tests"]) == 2                    # populated, NOT the mot_tests=[] shim
    assert kw["mot_tests"][0]["odometer_value"] == 50000


def test_golden_vehicle_age_matches_hand_computed_value():
    feats = compute_candidate_over_history(
        _rows(), cf.compute_candidate_features,
        target_date=dt.datetime(2024, 1, 1), postcode="")
    expected = round((dt.datetime(2024, 1, 1) - dt.datetime(2014, 1, 1)).days / 365.25, 4)
    assert feats["vehicle_age_years"] == expected
    assert feats["vehicle_age_years_missing"] == 0
    assert feats["age_anchor_is_fallback"] == 0


def test_no_prior_target_has_empty_mot_tests_but_is_still_anchored():
    fud = dt.datetime(2020, 1, 1)
    rows = [dict(tgt_id=2, tgt_date=dt.datetime(2024, 1, 1), tgt_fud=fud, tgt_make="VW",
                 tgt_model_id="GOLF", p_test_id=None, p_date=None, p_result=None,
                 p_miles=None, p_fud=None, p_make=None, p_model=None, p_fuel=None,
                 defects_json=None)]
    kw = reconstruct_history_kwargs(rows)
    assert kw["mot_tests"] == []
    assert kw["registration_date"] == fud               # falls back to tgt_fud
