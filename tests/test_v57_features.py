"""v57 shared feature transform + emitted contract (audit item #4).

Stdlib-only (no numpy/pandas) so it runs anywhere: v57_features reads the v55
base list via ast, and model_bundle is pure stdlib.
"""

import os
import sys
from datetime import date

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model_bundle import (  # noqa: E402
    COVERAGE_FEATURES,
    OBSERVED_RENAMES,
    RC3_DROPPED_FEATURES,
    WINDOW_START,
    load_contract,
)
import v57_features as v  # noqa: E402

CONTRACT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models", "v57", "feature_contract.json",
)


def test_v57_name_set_derives_from_decision_table():
    names = v.V57_FEATURE_NAMES
    assert len(names) == 105  # 104 - 4 dropped + 5 coverage
    assert len(names) == len(set(names))
    assert not (set(RC3_DROPPED_FEATURES) & set(names)), "RC-3 leaked into v57"
    assert not (set(OBSERVED_RENAMES) & set(names)), "pre-rename name in v57"
    for old, new in OBSERVED_RENAMES.items():
        assert new in names, f"renamed target missing: {new}"
    for cov in COVERAGE_FEATURES:
        assert cov in names
    assert len(v.V57_CATEGORICAL) == 10  # v55 categoricals unchanged


def test_coverage_first_test_young_car():
    cov = v.compute_coverage_features(date(2026, 6, 1),
                                      first_use_date=date(2024, 1, 1),
                                      first_observed_test_date=None)
    assert cov["has_prior_test_observed"] == 0
    assert cov["history_years_observed"] == 0.0
    assert cov["has_left_truncated_history"] == 0
    assert cov["first_observed_test_is_not_true_first"] == 0


def test_coverage_old_left_truncated_car():
    # MOT-liable (2008 + 3y) well before the 2019 window, but an observed test
    # exists inside it -> earliest observed test cannot be the true first MOT.
    cov = v.compute_coverage_features(date(2026, 6, 1),
                                      first_use_date=date(2008, 5, 1),
                                      first_observed_test_date=date(2020, 6, 1))
    assert cov["has_prior_test_observed"] == 1
    assert cov["has_left_truncated_history"] == 1
    assert cov["first_observed_test_is_not_true_first"] == 1
    assert cov["history_years_observed"] == round((date(2026, 6, 1) - date(2020, 6, 1)).days / 365.25, 4)


def test_coverage_post_window_car_not_truncated():
    cov = v.compute_coverage_features(date(2026, 6, 1),
                                      first_use_date=date(2019, 2, 1),
                                      first_observed_test_date=date(2022, 3, 1))
    assert cov["has_left_truncated_history"] == 0
    assert cov["first_observed_test_is_not_true_first"] == 0


def test_window_days_available_floors_at_zero():
    cov = v.compute_coverage_features(date(2018, 1, 1), None, None)
    assert cov["window_days_available"] == 0


def test_row_transform_yields_exactly_contract_keys():
    fake_v55 = {n: 1 for n in v.V55_FEATURE_NAMES}
    cov = v.compute_coverage_features(date(2026, 6, 1), date(2010, 1, 1), date(2020, 1, 1))
    row = v.apply_v57_row_transform(fake_v55, cov)
    assert set(row) == set(v.V57_FEATURE_NAMES)
    # RC-3 dropped, renames applied.
    for f in RC3_DROPPED_FEATURES:
        assert f not in row
    assert "n_prior_tests" not in row and "n_prior_tests_observed" in row


def test_emitted_contract_matches_shared_definition():
    # Locks the committed models/v57/feature_contract.json against the code: if
    # the decision table changes, regenerate via models/v57/build_contract.py.
    c = load_contract(CONTRACT_PATH)
    assert c.feature_names == v.V57_FEATURE_NAMES
    assert c.categorical_features == v.V57_CATEGORICAL
    assert c.history_window_start == WINDOW_START.isoformat()
    assert len(c.feature_names) == 105
