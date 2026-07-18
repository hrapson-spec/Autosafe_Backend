"""
Tests for the feature contract system.

This test suite verifies:
1. Feature contract dataclass semantics (immutability, inference safety)
2. Feature registry completeness and correctness
3. The v57 derived contract (model_contract_v57 + model_bundle)

History: the original file also imported a `feature_validation` module
(validate_training_features / validate_inference_features / ...) that was
never committed to this repository, so the whole file failed at collection
time (GF-17 stale-test repair, 2026-06-12). Those test classes were removed
with their subject module; v57 contract validation replaces them. If a real
feature_validation module lands, restore the classes from git history
alongside it.
"""

import os
import sys

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feature_contracts import (  # noqa: E402
    FeatureContract,
    TimeAvailability,
    ValidContext,
)
from feature_registry import (  # noqa: E402
    FEATURE_REGISTRY,
    ALL_CONTRACTS,
    HISTORICAL_FEATURES,
    OUTCOME_FEATURES,
    get_inference_safe_features,
    get_outcome_features,
    get_contract,
)


class TestFeatureContract:
    """Tests for the FeatureContract dataclass."""

    def test_contract_immutable(self):
        """Contracts should be frozen/immutable."""
        contract = FeatureContract(
            name="test_feature",
            availability=TimeAvailability.STATIC
        )
        with pytest.raises(AttributeError):
            contract.name = "modified"

    def test_is_safe_for_inference_static(self):
        """Static features should be safe for inference."""
        contract = FeatureContract(
            name="make",
            availability=TimeAvailability.STATIC
        )
        assert contract.is_safe_for_inference() is True

    def test_is_safe_for_inference_outcome(self):
        """Outcome features should NOT be safe for inference."""
        contract = FeatureContract(
            name="is_failure",
            availability=TimeAvailability.OUTCOME,
            context=ValidContext.TRAINING_ONLY
        )
        assert contract.is_safe_for_inference() is False

    def test_is_label(self):
        """Outcome features should be identified as labels."""
        outcome = FeatureContract(
            name="test_result",
            availability=TimeAvailability.OUTCOME
        )
        static = FeatureContract(
            name="make",
            availability=TimeAvailability.STATIC
        )
        assert outcome.is_label() is True
        assert static.is_label() is False

    def test_is_historical(self):
        """Historical features should be identified correctly."""
        historical = FeatureContract(
            name="prev_outcome",
            availability=TimeAvailability.HISTORICAL,
            requires_lag=True
        )
        static = FeatureContract(
            name="make",
            availability=TimeAvailability.STATIC
        )
        assert historical.is_historical() is True
        assert static.is_historical() is False


class TestFeatureRegistry:
    """Tests for the feature registry."""

    def test_registry_not_empty(self):
        """Registry should contain features."""
        assert len(FEATURE_REGISTRY) > 0
        assert len(ALL_CONTRACTS) > 0

    def test_all_outcome_features_are_training_only(self):
        """All OUTCOME features should be marked TRAINING_ONLY."""
        for contract in OUTCOME_FEATURES:
            assert contract.context == ValidContext.TRAINING_ONLY, (
                f"Outcome feature '{contract.name}' should be TRAINING_ONLY, "
                f"got {contract.context}"
            )

    def test_historical_features_require_lag(self):
        """All HISTORICAL features should have requires_lag=True."""
        for contract in HISTORICAL_FEATURES:
            assert contract.requires_lag is True, (
                f"Historical feature '{contract.name}' should have requires_lag=True"
            )

    def test_inference_safe_excludes_outcomes(self):
        """Inference-safe set should not contain outcome features."""
        safe = get_inference_safe_features()
        outcomes = get_outcome_features()
        overlap = safe & outcomes
        assert not overlap, f"Outcome features in inference-safe set: {overlap}"

    def test_no_duplicate_names(self):
        """Feature names should be unique."""
        names = [c.name for c in ALL_CONTRACTS]
        assert len(names) == len(set(names)), "Duplicate feature names found"

    def test_get_contract_exists(self):
        """get_contract should return contract for known features."""
        contract = get_contract('make')
        assert contract is not None
        assert contract.name == 'make'

    def test_get_contract_unknown(self):
        """get_contract should return None for unknown features."""
        contract = get_contract('nonexistent_feature')
        assert contract is None

    def test_key_features_exist(self):
        """Critical features should be in the registry."""
        required_features = [
            # Static
            'make', 'model', 'model_id',
            # At decision
            'age_band', 'mileage_band',
            # Historical
            'prev_cycle_outcome_band', 'gap_band',
            # Outcome (labels)
            'test_result', 'is_failure',
            # Aggregate
            'Failure_Risk',
        ]
        for feature in required_features:
            assert feature in FEATURE_REGISTRY, f"Feature '{feature}' missing from registry"


class TestV57ContractValidation:
    """The v57 derived contract is internally consistent and round-trips
    through the scaffold loader (replaces the never-committed
    feature_validation test surface)."""

    def test_contract_emits_and_loads(self, tmp_path):
        from model_bundle import load_contract
        from model_contract_v57 import (
            CATEGORICAL_FEATURES, FEATURE_NAMES, contract_as_dict,
        )

        path = tmp_path / "feature_contract.json"
        contract_as_dict(out_path=path)
        loaded = load_contract(path)  # runs validate_decision_table
        assert loaded.feature_names == FEATURE_NAMES
        assert loaded.categorical_features == CATEGORICAL_FEATURES
        loaded.validate_feature_columns(FEATURE_NAMES)

    def test_contract_rejects_order_drift(self, tmp_path):
        from model_bundle import load_contract
        from model_contract_v57 import FEATURE_NAMES, contract_as_dict

        path = tmp_path / "feature_contract.json"
        contract_as_dict(out_path=path)
        loaded = load_contract(path)
        shuffled = list(FEATURE_NAMES)
        shuffled[0], shuffled[1] = shuffled[1], shuffled[0]
        with pytest.raises(ValueError, match="violate the v57 contract"):
            loaded.validate_feature_columns(shuffled)

    def test_every_feature_has_canonical_default_and_source(self):
        from model_contract_v57 import FEATURE_NAMES, build_feature_rows

        rows = build_feature_rows()
        assert [r["name"] for r in rows] == FEATURE_NAMES
        for r in rows:
            assert r["dtype"] in ("float", "int", "categorical")
            assert "default" in r and r["default"] is not None
            assert r["source"], f"{r['name']} missing source"
            assert r["prediction_time_available"] is True
