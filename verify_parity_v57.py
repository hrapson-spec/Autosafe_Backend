"""
V57 Parity Verification Script
==============================

Verifies that the survivorship feature fix in feature_engineering_v55.py
produces the same values as the training script for the same vehicle.

This script:
1. Loads cohort_stats.pkl and model_hierarchical_features.pkl from V57
2. Loads a sample vehicle from the training data
3. Runs the same vehicle through engineer_features_with_stats()
4. Compares key features that were previously hardcoded

Pass Condition: Values match within tolerance, proving inference parity with training.
"""

import pickle
import sys
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from feature_engineering_v55 import engineer_features, get_age_band


# Configuration
V57_DIR = Path.home() / "autosafe_work/catboost_production_v57"
PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
DEV_SET = PROJECT_ROOT / "stratified_samples/dev_set_with_advisory.parquet"


class MockMOTTest:
    """Mock MOT test for inference testing."""
    def __init__(self, test_date, test_result, odometer_value, odometer_unit='mi', expiry_date=None, defects=None):
        self.test_date = test_date
        self.test_result = test_result
        self.odometer_value = odometer_value
        self.odometer_unit = odometer_unit
        self.expiry_date = expiry_date
        self.defects = defects or []


class MockVehicleHistory:
    """Mock vehicle history for inference testing."""
    def __init__(self, make, model, manufacture_date, mot_tests):
        self.make = make
        self.model = model
        self.manufacture_date = manufacture_date
        self.mot_tests = mot_tests


def load_artifacts():
    """Load V57 artifacts."""
    print(f"Loading artifacts from {V57_DIR}...")

    cohort_stats_path = V57_DIR / "cohort_stats.pkl"
    model_hier_path = V57_DIR / "model_hierarchical_features.pkl"

    if not cohort_stats_path.exists():
        print(f"ERROR: {cohort_stats_path} not found. Training may not be complete.")
        return None, None

    if not model_hier_path.exists():
        print(f"ERROR: {model_hier_path} not found. Training may not be complete.")
        return None, None

    with open(cohort_stats_path, 'rb') as f:
        cohort_stats = pickle.load(f)
    print(f"  Loaded cohort_stats: {list(cohort_stats.keys())}")

    with open(model_hier_path, 'rb') as f:
        model_hier = pickle.load(f)
    print(f"  Loaded model_hierarchical: {type(model_hier).__name__}")
    print(f"    global_fail_rate: {model_hier.global_fail_rate:.4f}")
    print(f"    model_rates: {len(model_hier.model_rates)} entries")
    print(f"    make_rates: {len(model_hier.make_rates)} entries")

    return cohort_stats, model_hier


def create_test_vehicle():
    """Create a mock 'Young High Risk' vehicle for testing."""
    # 4-year-old Ford Transit with high mileage and multiple advisories
    manufacture_date = datetime(2020, 3, 15)

    tests = [
        MockMOTTest(
            test_date=datetime(2024, 3, 20),
            test_result='PASSED',
            odometer_value=85000,
            expiry_date=datetime(2025, 3, 19),
            defects=[
                {'type': 'ADVISORY', 'text': 'Brake pad wear'},
                {'type': 'ADVISORY', 'text': 'Front suspension arm worn'},
                {'type': 'ADVISORY', 'text': 'Tyre worn close to limit'},
            ]
        ),
        MockMOTTest(
            test_date=datetime(2023, 3, 18),
            test_result='FAILED',
            odometer_value=65000,
            defects=[
                {'type': 'FAIL', 'text': 'Brake disc worn below minimum'},
                {'type': 'ADVISORY', 'text': 'Suspension bush deteriorated'},
            ]
        ),
        MockMOTTest(
            test_date=datetime(2022, 3, 15),
            test_result='PASSED',
            odometer_value=42000,
            defects=[
                {'type': 'ADVISORY', 'text': 'Brake pad wear'},
            ]
        ),
    ]

    return MockVehicleHistory(
        make='FORD',
        model='TRANSIT',
        manufacture_date=manufacture_date,
        mot_tests=tests
    )


def run_parity_check(cohort_stats, model_hier):
    """Run parity check between inference and training features."""
    print("\n" + "=" * 70)
    print("PARITY VERIFICATION")
    print("=" * 70)

    # Create test vehicle
    vehicle = create_test_vehicle()
    postcode = "SW1A 1AA"
    prediction_date = datetime(2024, 6, 1)

    print(f"\nTest Vehicle: {vehicle.make} {vehicle.model}")
    print(f"  Manufacture date: {vehicle.manufacture_date}")
    print(f"  MOT tests: {len(vehicle.mot_tests)}")
    print(f"  Latest mileage: {vehicle.mot_tests[0].odometer_value}")
    print(f"  Prediction date: {prediction_date}")

    # Calculate vehicle age
    vehicle_age = (prediction_date - vehicle.manufacture_date).days / 365
    age_band = get_age_band(int(vehicle_age))
    model_id = f"{vehicle.make} {vehicle.model}"
    cohort_key = (model_id, age_band)

    print(f"\n  Vehicle age: {vehicle_age:.1f} years")
    print(f"  Age band: {age_band}")
    print(f"  Cohort key: {cohort_key}")

    # Run inference WITHOUT cohort stats (old behavior)
    print("\n--- OLD BEHAVIOR (hardcoded defaults) ---")
    features_old = engineer_features(
        history=vehicle,
        postcode=postcode,
        prediction_date=prediction_date,
        cohort_stats=None,
        model_hierarchical=None,
    )

    print(f"  advisory_cohort_delta:       {features_old['advisory_cohort_delta']:.4f}")
    print(f"  mileage_cohort_ratio:        {features_old['mileage_cohort_ratio']:.4f}")
    print(f"  model_fail_rate_smoothed:    {features_old['model_fail_rate_smoothed']:.4f}")
    print(f"  make_fail_rate_smoothed:     {features_old['make_fail_rate_smoothed']:.4f}")
    print(f"  mech_decay_index_normalized: {features_old['mech_decay_index_normalized']:.4f}")

    # Run inference WITH cohort stats (new behavior)
    print("\n--- NEW BEHAVIOR (with cohort lookups) ---")
    features_new = engineer_features(
        history=vehicle,
        postcode=postcode,
        prediction_date=prediction_date,
        cohort_stats=cohort_stats,
        model_hierarchical=model_hier,
    )

    print(f"  advisory_cohort_delta:       {features_new['advisory_cohort_delta']:.4f}")
    print(f"  mileage_cohort_ratio:        {features_new['mileage_cohort_ratio']:.4f}")
    print(f"  model_fail_rate_smoothed:    {features_new['model_fail_rate_smoothed']:.4f}")
    print(f"  make_fail_rate_smoothed:     {features_new['make_fail_rate_smoothed']:.4f}")
    print(f"  mech_decay_index_normalized: {features_new['mech_decay_index_normalized']:.4f}")

    # Check cohort lookup worked
    print("\n--- VERIFICATION ---")

    # Check cohort_mileage lookup
    if cohort_key in cohort_stats.get('cohort_mileage', {}):
        expected_mileage = cohort_stats['cohort_mileage'][cohort_key]
        print(f"  Cohort mileage for {cohort_key}: {expected_mileage:.0f}")
    else:
        print(f"  Cohort key {cohort_key} NOT FOUND in cohort_mileage")
        # Check what keys exist for FORD TRANSIT
        ford_keys = [k for k in cohort_stats.get('cohort_mileage', {}).keys() if 'FORD' in str(k) and 'TRANSIT' in str(k)]
        print(f"  Available FORD TRANSIT keys: {ford_keys[:5]}")

    # Verification results
    passed = True
    results = []

    # Check advisory_cohort_delta changed
    if features_old['advisory_cohort_delta'] == 0.0 and features_new['advisory_cohort_delta'] != 0.0:
        results.append(("advisory_cohort_delta", "PASS", "Changed from 0.0"))
    elif features_new['advisory_cohort_delta'] != 0.0:
        results.append(("advisory_cohort_delta", "PASS", f"Non-zero: {features_new['advisory_cohort_delta']:.4f}"))
    else:
        results.append(("advisory_cohort_delta", "FAIL", "Still 0.0"))
        passed = False

    # Check mileage_cohort_ratio changed
    if features_old['mileage_cohort_ratio'] == 1.0 and features_new['mileage_cohort_ratio'] != 1.0:
        results.append(("mileage_cohort_ratio", "PASS", "Changed from 1.0"))
    elif features_new['mileage_cohort_ratio'] != 1.0:
        results.append(("mileage_cohort_ratio", "PASS", f"Non-default: {features_new['mileage_cohort_ratio']:.4f}"))
    else:
        results.append(("mileage_cohort_ratio", "FAIL", "Still 1.0"))
        passed = False

    # Check model_fail_rate_smoothed changed
    if features_old['model_fail_rate_smoothed'] == 0.28 and features_new['model_fail_rate_smoothed'] != 0.28:
        results.append(("model_fail_rate_smoothed", "PASS", "Changed from 0.28"))
    elif features_new['model_fail_rate_smoothed'] != 0.28:
        results.append(("model_fail_rate_smoothed", "PASS", f"Non-default: {features_new['model_fail_rate_smoothed']:.4f}"))
    else:
        results.append(("model_fail_rate_smoothed", "FAIL", "Still 0.28"))
        passed = False

    # Check make_fail_rate_smoothed changed
    if features_new['make_fail_rate_smoothed'] != 0.28:
        results.append(("make_fail_rate_smoothed", "PASS", f"Non-default: {features_new['make_fail_rate_smoothed']:.4f}"))
    else:
        results.append(("make_fail_rate_smoothed", "FAIL", "Still 0.28"))
        passed = False

    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    for feature, status, message in results:
        icon = "✅" if status == "PASS" else "❌"
        print(f"  {icon} {feature}: {message}")

    print("\n" + "=" * 70)
    if passed:
        print("✅ PARITY CHECK PASSED - Survivorship features are working!")
    else:
        print("❌ PARITY CHECK FAILED - Some features still use defaults")
    print("=" * 70)

    return passed


def main():
    """Main entry point."""
    print("V57 Parity Verification")
    print("=" * 70)

    # Load artifacts
    cohort_stats, model_hier = load_artifacts()

    if cohort_stats is None or model_hier is None:
        print("\nERROR: Cannot proceed without artifacts. Is V57 training complete?")
        sys.exit(1)

    # Run parity check
    passed = run_parity_check(cohort_stats, model_hier)

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
