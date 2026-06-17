"""v57 serving adapter ↔ contract parity (audit item #4 / gate #2 closure).

Needs numpy (model_v57 -> feature_engineering_v55); skipped where unavailable.
Proves the v57 serving adapter emits exactly the v57 contract over a diverse
fixture panel, with RC-3 gone and coverage features present.
"""

import os
import sys

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("numpy")  # model_v57 -> feature_engineering_v55 -> numpy

from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402

from gf17_train_serve_parity import build_fixtures, run_v57_serving_parity  # noqa: E402
from model_bundle import COVERAGE_FEATURES, RC3_DROPPED_FEATURES  # noqa: E402
from model_v57 import engineer_features_v57, features_to_array_v57  # noqa: E402
from v57_features import V57_FEATURE_NAMES  # noqa: E402

CONTRACT = Path(__file__).resolve().parent.parent / "models" / "v57" / "feature_contract.json"


def test_v57_serving_matches_contract():
    rep = run_v57_serving_parity(CONTRACT)
    assert rep["ok"], rep["violations"]
    assert rep["missing_from_serving"] == []
    assert rep["rc3_present"] == []
    assert rep["coverage_missing"] == []
    assert rep["pre_rename_present"] == []


def test_v57_adapter_emits_full_orderable_vector():
    # Every fixture must produce all 105 features so the model can be scored.
    pred_date = datetime(2026, 6, 1)
    for _name, history, postcode in build_fixtures():
        feats = engineer_features_v57(history, postcode, prediction_date=pred_date)
        arr = features_to_array_v57(feats, validate=True)  # raises if any missing
        assert len(arr) == len(V57_FEATURE_NAMES) == 105


def test_v57_first_test_cohort_gets_coverage_signal():
    # The no-history fixture is exactly the cohort decision #4 keeps in-model.
    pred_date = datetime(2026, 6, 1)
    no_hist = next(h for n, h, _ in build_fixtures() if n == "no_history")
    feats = engineer_features_v57(no_hist, "SW1A 1AA", prediction_date=pred_date)
    assert feats["has_prior_test_observed"] == 0
    for f in RC3_DROPPED_FEATURES:
        assert f not in feats
    for c in COVERAGE_FEATURES:
        assert c in feats


def test_v57_window_cap_excludes_pre_2019_tests():
    # old_high_mileage carries a 2017 test; the v57 path must cap it out, so the
    # earliest observed test is 2022 -> history_years_observed ~4, not ~9.
    pred_date = datetime(2026, 6, 1)
    veh = next(h for n, h, _ in build_fixtures() if n == "old_high_mileage")
    feats = engineer_features_v57(veh, "", prediction_date=pred_date)
    assert feats["has_prior_test_observed"] == 1
    assert 3.0 < feats["history_years_observed"] < 5.0, feats["history_years_observed"]
    # Old car, MOT-liable before the window, with an observed test inside it.
    assert feats["has_left_truncated_history"] == 1
    assert feats["first_observed_test_is_not_true_first"] == 1
