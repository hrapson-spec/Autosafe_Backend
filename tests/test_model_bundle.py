"""Decision-table and contract loader tests for the v57 bundle scaffold."""

import os
import sys

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feature_engineering_v55 import FEATURE_NAMES as V55_FEATURE_NAMES  # noqa: E402
from model_bundle import (  # noqa: E402
    COVERAGE_FEATURES,
    OBSERVED_RENAMES,
    RC3_DROPPED_FEATURES,
    WINDOW_START,
    emit_contract,
    load_contract,
)


def test_decision_table_is_internally_consistent():
    targets = list(OBSERVED_RENAMES.values())
    assert len(targets) == len(set(targets)), "duplicate rename targets"
    assert not set(OBSERVED_RENAMES) & set(RC3_DROPPED_FEATURES), (
        "a feature cannot be both renamed and dropped"
    )
    assert not set(targets) & set(V55_FEATURE_NAMES), (
        "rename target collides with an existing v55 feature name"
    )
    assert not set(COVERAGE_FEATURES) & set(V55_FEATURE_NAMES), (
        "coverage feature collides with an existing v55 feature name"
    )
    assert all(t.endswith("_observed") or t.startswith("has_observed") or t == "prior_advisory_count_observed"
               for t in targets)


def test_decision_table_binds_to_real_v55_features():
    """Every rename source and drop-set member must exist in the served
    contract — catches typos against ground truth."""
    served = set(V55_FEATURE_NAMES)
    missing_renames = [f for f in OBSERVED_RENAMES if f not in served]
    missing_drops = [f for f in RC3_DROPPED_FEATURES if f not in served]
    assert not missing_renames, f"rename sources not in v55 contract: {missing_renames}"
    assert not missing_drops, f"drop-set not in v55 contract: {missing_drops}"


def test_expected_v57_feature_count():
    # 104 served - 4 dropped + 5 coverage = 105 model features
    assert len(V55_FEATURE_NAMES) == 104
    assert len(RC3_DROPPED_FEATURES) == 4
    assert len(COVERAGE_FEATURES) == 5
    assert len(OBSERVED_RENAMES) == 35


def _v57_feature_rows():
    """Synthesize the v57 feature list from the decision table (what the
    matrix builder will emit mechanically)."""
    rows = []
    for name in V55_FEATURE_NAMES:
        if name in RC3_DROPPED_FEATURES:
            continue
        new = OBSERVED_RENAMES.get(name, name)
        rows.append({
            "name": new,
            "dtype": "categorical" if name in (
                "prev_cycle_outcome_band", "gap_band", "make", "advisory_trend",
                "usage_band_hybrid", "negligence_band", "mech_risk_driver",
                "dominant_mechanism",
            ) else "float",
            "default": 0,
            "source": f"v55:{name}",
            "prediction_time_available": True,
            "window_bounded": name in OBSERVED_RENAMES,
        })
    for cov, desc in COVERAGE_FEATURES.items():
        rows.append({
            "name": cov, "dtype": "float", "default": 0,
            "source": "coverage", "prediction_time_available": True,
            "window_bounded": True, "description": desc,
        })
    return rows


def test_emit_and_load_roundtrip(tmp_path):
    out = tmp_path / "feature_contract.json"
    emit_contract(version="v57.0-test", features=_v57_feature_rows(), out_path=out)
    c = load_contract(out)
    assert c.version == "v57.0-test"
    assert len(c.feature_names) == 105
    assert c.history_window_start == WINDOW_START.isoformat()
    # decision table enforced on load
    assert "station_strictness_bias" not in c.feature_names
    assert "n_prior_tests" not in c.feature_names
    assert "n_prior_tests_observed" in c.feature_names
    assert all(cov in c.feature_names for cov in COVERAGE_FEATURES)
    # categorical indices resolve
    assert len(c.categorical_indices) == 8


def test_loader_rejects_contract_violating_decisions(tmp_path):
    rows = _v57_feature_rows()
    rows.append({"name": "suspension_risk_profile", "dtype": "float", "default": 0,
                 "source": "x", "prediction_time_available": True})
    out = tmp_path / "bad.json"
    emit_contract(version="v57.0-test", features=rows, out_path=out)
    with pytest.raises(ValueError, match="RC-3 dropped"):
        load_contract(out)


def test_validate_feature_columns_detects_order_drift(tmp_path):
    out = tmp_path / "feature_contract.json"
    emit_contract(version="v57.0-test", features=_v57_feature_rows(), out_path=out)
    c = load_contract(out)
    c.validate_feature_columns(list(c.feature_names))  # exact -> ok
    swapped = list(c.feature_names)
    swapped[0], swapped[1] = swapped[1], swapped[0]
    with pytest.raises(ValueError, match="first_order_mismatch"):
        c.validate_feature_columns(swapped)
