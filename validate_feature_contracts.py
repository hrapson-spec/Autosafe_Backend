"""
Feature Contract Validator for AutoSafe MOT Prediction Model.

Validates that datasets conform to feature availability contracts and
applies transformations (defaults, missing indicators) for inference.

Contract Severity:
- HARD contracts: Violations raise HardContractViolationError (fail fast)
- SOFT contracts: Violations are logged as warnings (allows continuation)
"""

import json
import pickle
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from feature_contracts import (
    FEATURE_CONTRACTS,
    FeatureContract,
    ContractSeverity,
    get_categorical_features,
    get_missing_indicator_features,
)


class HardContractViolationError(Exception):
    """Raised when a HARD contract is violated. Blocks training/inference."""

    def __init__(self, violations: List['ContractViolation']):
        self.violations = violations
        messages = [f"{v.feature}: {v.message}" for v in violations]
        super().__init__(
            f"HARD contract violation(s) detected - cannot proceed:\n  " +
            "\n  ".join(messages)
        )


@dataclass
class ContractViolation:
    """Records a single contract violation."""
    feature: str
    violation_type: str  # 'missing_rate_drift', 'required_absent', 'type_mismatch'
    expected: Any
    actual: Any
    tolerance: float
    severity: str  # 'error', 'warning'
    message: str


@dataclass
class ValidationReport:
    """Complete validation report for a dataset."""
    dataset_name: str
    timestamp: str
    n_samples: int
    violations: List[ContractViolation] = field(default_factory=list)
    hard_violations: List[ContractViolation] = field(default_factory=list)
    soft_violations: List[ContractViolation] = field(default_factory=list)
    missing_rates: Dict[str, float] = field(default_factory=dict)
    passed: bool = True

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "dataset_name": self.dataset_name,
            "timestamp": self.timestamp,
            "n_samples": self.n_samples,
            "passed": self.passed,
            "n_hard_violations": len(self.hard_violations),
            "n_soft_violations": len(self.soft_violations),
            "hard_violations": [
                {
                    "feature": v.feature,
                    "type": v.violation_type,
                    "expected": str(v.expected),
                    "actual": str(v.actual),
                    "tolerance": v.tolerance,
                    "severity": v.severity,
                    "message": v.message,
                }
                for v in self.hard_violations
            ],
            "soft_violations": [
                {
                    "feature": v.feature,
                    "type": v.violation_type,
                    "expected": str(v.expected),
                    "actual": str(v.actual),
                    "tolerance": v.tolerance,
                    "severity": v.severity,
                    "message": v.message,
                }
                for v in self.soft_violations
            ],
            "missing_rates": self.missing_rates,
        }

    def print_summary(self):
        """Print human-readable summary."""
        status = "PASSED" if self.passed else "FAILED"
        print(f"\n{'=' * 70}")
        print(f"VALIDATION REPORT: {self.dataset_name}")
        print(f"{'=' * 70}")
        print(f"Status: {status}")
        print(f"Samples: {self.n_samples:,}")
        print(f"HARD violations: {len(self.hard_violations)}")
        print(f"SOFT violations: {len(self.soft_violations)}")

        if self.hard_violations:
            print(f"\n{'HARD Violations (blocking)':^70}")
            print("-" * 70)
            for v in self.hard_violations:
                print(f"  [HARD] {v.feature}: {v.message}")

        if self.soft_violations:
            print(f"\n{'SOFT Violations (warnings)':^70}")
            print("-" * 70)
            for v in self.soft_violations:
                print(f"  [SOFT] {v.feature}: {v.message}")

        print(f"\n{'Missing Rates':^70}")
        print("-" * 70)
        for feat, rate in sorted(self.missing_rates.items(), key=lambda x: -x[1]):
            if rate > 0:
                print(f"  {feat}: {rate:.1%}")


class FeatureContractValidator:
    """
    Validates datasets against feature availability contracts.

    Usage:
        validator = FeatureContractValidator(FEATURE_CONTRACTS)
        validator.fit(train_df)  # Learn baseline missing rates
        report = validator.validate(oot_df, 'OOT_2024')  # Check for drift
        df_transformed = validator.transform(oot_df)  # Apply defaults + indicators
    """

    def __init__(self, contracts: Dict[str, FeatureContract]):
        """
        Initialize with feature contracts.

        Args:
            contracts: Dictionary mapping feature names to FeatureContract objects
        """
        self.contracts = contracts
        self.training_missing_rates: Dict[str, float] = {}
        self.training_n_samples: int = 0
        self.fitted: bool = False

    def fit(self, train_df: pd.DataFrame) -> 'FeatureContractValidator':
        """
        Learn baseline missing rates from training data.

        Args:
            train_df: Training dataframe

        Returns:
            self for method chaining
        """
        self.training_n_samples = len(train_df)
        self.training_missing_rates = {}

        for name, contract in self.contracts.items():
            if name in train_df.columns:
                missing_rate = train_df[name].isna().mean()
            else:
                # Feature not in training data - will be created by transforms
                missing_rate = 1.0 if contract.min_history_depth > 0 else 0.0

            self.training_missing_rates[name] = missing_rate

        self.fitted = True
        return self

    def validate(
        self,
        df: pd.DataFrame,
        dataset_name: str,
        strict: bool = False,
        fail_hard: bool = False
    ) -> ValidationReport:
        """
        Validate a dataset against contracts.

        Args:
            df: DataFrame to validate
            dataset_name: Name for reporting
            strict: If True, treat all violations as errors (legacy behavior)
            fail_hard: If True, raise HardContractViolationError on HARD violations

        Returns:
            ValidationReport with all violations

        Raises:
            HardContractViolationError: If fail_hard=True and HARD violations exist
        """
        report = ValidationReport(
            dataset_name=dataset_name,
            timestamp=datetime.now().isoformat(),
            n_samples=len(df),
        )

        for name, contract in self.contracts.items():
            # Check if feature exists
            if name not in df.columns:
                if contract.required and contract.min_history_depth == 0:
                    # Required base feature is missing entirely
                    violation = ContractViolation(
                        feature=name,
                        violation_type='required_absent',
                        expected='present',
                        actual='absent',
                        tolerance=0,
                        severity='hard' if contract.severity == ContractSeverity.HARD else 'soft',
                        message=f"Required feature '{name}' is not in dataset"
                    )
                    report.violations.append(violation)
                    if contract.severity == ContractSeverity.HARD:
                        report.hard_violations.append(violation)
                    else:
                        report.soft_violations.append(violation)
                # Features created by transforms (hierarchical, etc) won't exist yet
                continue

            # Compute missing rate
            missing_rate = df[name].isna().mean()
            report.missing_rates[name] = missing_rate

            # Check against training baseline (if fitted)
            if self.fitted:
                expected_rate = self.training_missing_rates.get(name, 0)
                drift = abs(missing_rate - expected_rate)

                if drift > contract.tolerance:
                    # Use contract severity to determine violation type
                    severity_str = 'hard' if contract.severity == ContractSeverity.HARD else 'soft'

                    violation = ContractViolation(
                        feature=name,
                        violation_type='missing_rate_drift',
                        expected=expected_rate,
                        actual=missing_rate,
                        tolerance=contract.tolerance,
                        severity=severity_str,
                        message=(
                            f"Missing rate {missing_rate:.1%} vs training {expected_rate:.1%} "
                            f"(drift {drift:.1%} > tolerance {contract.tolerance:.1%})"
                        )
                    )
                    report.violations.append(violation)
                    if contract.severity == ContractSeverity.HARD:
                        report.hard_violations.append(violation)
                    else:
                        report.soft_violations.append(violation)

        # Determine overall pass/fail
        # HARD violations always fail, SOFT violations only warn
        if strict:
            report.passed = len(report.violations) == 0
        else:
            # Pass if no HARD violations (SOFT violations are warnings only)
            report.passed = len(report.hard_violations) == 0

        # Optionally raise on HARD violations
        if fail_hard and report.hard_violations:
            raise HardContractViolationError(report.hard_violations)

        return report

    def transform(
        self,
        df: pd.DataFrame,
        create_missing_indicators: bool = True,
        inplace: bool = False
    ) -> pd.DataFrame:
        """
        Apply contract transformations: fill defaults and create missing indicators.

        Args:
            df: DataFrame to transform
            create_missing_indicators: Whether to create is_missing_* columns
            inplace: Whether to modify df in place

        Returns:
            Transformed DataFrame
        """
        if not inplace:
            df = df.copy()

        for name, contract in self.contracts.items():
            if name not in df.columns:
                # Feature will be created by upstream transforms (hierarchical, etc)
                # Create placeholder with default value
                df[name] = contract.default_value
                continue

            # Create missing indicator BEFORE filling (captures true missingness)
            # Only create if not already present (for idempotency)
            if create_missing_indicators and contract.create_missing_indicator:
                indicator_name = f"is_missing_{name}"
                if indicator_name not in df.columns:
                    df[indicator_name] = df[name].isna().astype(int)

            # Fill missing values with contract default
            df[name] = df[name].fillna(contract.default_value)

            # Ensure categorical features are strings
            if contract.is_categorical:
                df[name] = df[name].astype(str)

        return df

    def get_categorical_indices(self, feature_names: List[str]) -> List[int]:
        """
        Get indices of categorical features for CatBoost Pool.

        Args:
            feature_names: Ordered list of feature names

        Returns:
            List of indices for categorical features
        """
        categorical = get_categorical_features()
        return [i for i, name in enumerate(feature_names) if name in categorical]

    def save(self, path: Path):
        """Save fitted validator to disk."""
        with open(path, 'wb') as f:
            pickle.dump({
                'training_missing_rates': self.training_missing_rates,
                'training_n_samples': self.training_n_samples,
                'fitted': self.fitted,
            }, f)

    @classmethod
    def load(cls, path: Path, contracts: Dict[str, FeatureContract] = None) -> 'FeatureContractValidator':
        """Load fitted validator from disk."""
        if contracts is None:
            contracts = FEATURE_CONTRACTS

        validator = cls(contracts)
        with open(path, 'rb') as f:
            state = pickle.load(f)
            validator.training_missing_rates = state['training_missing_rates']
            validator.training_n_samples = state['training_n_samples']
            validator.fitted = state['fitted']

        return validator


def validate_dataset(
    df: pd.DataFrame,
    dataset_name: str,
    training_baseline: Optional[pd.DataFrame] = None,
    output_path: Optional[Path] = None
) -> ValidationReport:
    """
    Convenience function to validate a dataset against contracts.

    Args:
        df: Dataset to validate
        dataset_name: Name for reporting
        training_baseline: Optional training data for baseline comparison
        output_path: Optional path to save JSON report

    Returns:
        ValidationReport
    """
    validator = FeatureContractValidator(FEATURE_CONTRACTS)

    if training_baseline is not None:
        validator.fit(training_baseline)

    report = validator.validate(df, dataset_name)
    report.print_summary()

    if output_path:
        with open(output_path, 'w') as f:
            json.dump(report.to_dict(), f, indent=2)
        print(f"\nReport saved to: {output_path}")

    return report


if __name__ == "__main__":
    # Example usage / self-test
    print("Feature Contract Validator - Self Test")
    print("=" * 50)

    # Create test data
    test_df = pd.DataFrame({
        'prev_cycle_outcome_band': ['PASS', 'FAIL', None, 'PASS'],
        'gap_band': ['0-30', '30-60', '60-90', None],
        'make': ['FORD', 'BMW', 'TOYOTA', 'UNKNOWN'],
        'test_mileage': [50000, 80000, None, 120000],
        'advisory_trend': [None, None, None, None],  # 100% missing
        'n_prior_tests': [3, 5, 0, 2],
    })

    validator = FeatureContractValidator(FEATURE_CONTRACTS)

    # Validate without baseline
    print("\n1. Validation without training baseline:")
    report = validator.validate(test_df, 'TEST_DATA')
    report.print_summary()

    # Transform
    print("\n2. After transformation:")
    transformed = validator.transform(test_df)
    print(f"Columns: {list(transformed.columns)}")
    print(f"\nadvisory_trend after fill:\n{transformed['advisory_trend'].tolist()}")
    print(f"\nis_missing_advisory_trend:\n{transformed['is_missing_advisory_trend'].tolist()}")
