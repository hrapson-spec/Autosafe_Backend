"""Contract tests for model_contract_v57 — the derived v57 feature contract.

Scaffold-governed (model_bundle.py, Stage-1 directive 2026-06-12): the v57
contract is DERIVED from feature_engineering_v55.FEATURE_NAMES plus the
decision table (RC-3 drops, observed renames, coverage features), never
hand-maintained. These tests bind the derivation to both sources.
"""

import os
import sys
from datetime import date

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feature_engineering_v55 import FEATURE_NAMES as V55_FEATURE_NAMES  # noqa: E402
from model_bundle import (  # noqa: E402
    OBSERVED_RENAMES,
    RC3_DROPPED_FEATURES,
    WINDOW_START,
)
from model_contract_v57 import (  # noqa: E402
    CATEGORICAL_FEATURES,
    COVERAGE_FEATURE_ORDER,
    EXCLUDED_V57_FEATURES,
    FEATURE_DEFAULTS,
    FEATURE_NAMES,
    HISTORY_WINDOW_START,
    feature_index,
)


def test_v57_history_window_matches_bundle_directive():
    assert HISTORY_WINDOW_START == date(2019, 1, 1)
    assert HISTORY_WINDOW_START == WINDOW_START


def test_v57_excludes_station_and_known_noisy_features():
    forbidden = {
        # RC-3 drop-set (served-as-constants in v55)
        "station_fail_rate_smoothed",
        "station_x_prev_outcome_fail_rate",
        "station_strictness_bias",
        "suspension_risk_profile",
        # noisy ablation guards (trainer-only in v55; must never enter v57)
        "neglect_score_brakes",
        "neglect_score_tyres",
        "neglect_score_suspension",
    }
    assert forbidden <= EXCLUDED_V57_FEATURES
    assert not (forbidden & set(FEATURE_NAMES))


def test_v57_contract_has_unique_feature_names_and_defaults():
    assert len(FEATURE_NAMES) == len(set(FEATURE_NAMES))
    assert set(FEATURE_NAMES) == set(FEATURE_DEFAULTS)


def test_v57_categorical_features_are_present_in_ordered_contract():
    missing = set(CATEGORICAL_FEATURES) - set(FEATURE_NAMES)
    assert missing == set()
    assert [FEATURE_NAMES[feature_index(name)] for name in CATEGORICAL_FEATURES] == CATEGORICAL_FEATURES


def test_v57_width_and_derivation_order():
    # 104 served v55 features - 4 RC-3 drops + 5 coverage features = 105,
    # in v55 order with renames applied in place and coverage appended last.
    assert len(FEATURE_NAMES) == 105
    derived = [
        OBSERVED_RENAMES.get(name, name)
        for name in V55_FEATURE_NAMES
        if name not in RC3_DROPPED_FEATURES
    ] + list(COVERAGE_FEATURE_ORDER)
    assert FEATURE_NAMES == derived


def test_v57_no_pre_rename_names_and_all_targets_present():
    names = set(FEATURE_NAMES)
    leaked_sources = sorted(set(OBSERVED_RENAMES) & names)
    assert leaked_sources == []
    missing_targets = sorted(set(OBSERVED_RENAMES.values()) - names)
    assert missing_targets == []


def test_v57_coverage_features_appended_in_readme_order():
    assert list(COVERAGE_FEATURE_ORDER) == [
        "window_days_available",
        "history_years_observed",
        "has_prior_test_observed",
        "has_left_truncated_history",
        "first_observed_test_is_not_true_first",
    ]
    assert FEATURE_NAMES[-5:] == list(COVERAGE_FEATURE_ORDER)


def test_v57_canonical_defaults_follow_serving_semantics():
    # Ledger RC-1 rows: one canonical default per feature = serving's value.
    assert FEATURE_DEFAULTS["test_mileage"] == 0.0
    assert FEATURE_DEFAULTS["days_since_last_test"] == 0.0
    assert FEATURE_DEFAULTS["days_late"] == 0.0
    assert FEATURE_DEFAULTS["annualized_mileage_v2"] == 10000.0
    assert FEATURE_DEFAULTS["negligence_band"] == "clean"
    # eb_unified_prior's canonical default is the fitted global emitted at
    # train time; the module only guarantees the key exists (checked above).
    assert "eb_unified_prior" in FEATURE_DEFAULTS
