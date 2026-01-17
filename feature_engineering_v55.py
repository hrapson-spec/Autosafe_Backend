"""
Feature Engineering for V55 CatBoost Model
==========================================

Extracts and engineers 104 features from DVSA MOT history data
for the V55 CatBoost production model.

Features are derived from:
1. DVSA API response (test history, advisories, failures)
2. Regional defaults (corrosion index from postcode)
3. Default values for unavailable data

This module transforms raw DVSA data into the exact feature vector
expected by the trained CatBoost model.
"""

import math
import re
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple

import numpy as np

from dvsa_client import VehicleHistory, MOTTest
from regional_defaults import get_corrosion_index, get_station_strictness_bias


# Feature names in exact order expected by model
FEATURE_NAMES = [
    'prev_cycle_outcome_band', 'gap_band', 'make', 'advisory_trend',
    'test_mileage', 'prev_count_advisory', 'days_since_last_test',
    'prior_fail_rate_smoothed', 'n_prior_tests', 'make_fail_rate_smoothed',
    'segment_fail_rate_smoothed', 'model_fail_rate_smoothed', 'days_late',
    'prev_adv_brakes', 'prev_adv_suspension', 'prev_adv_steering', 'prev_adv_tyres',
    'mileage_cohort_ratio', 'advisory_cohort_delta', 'days_since_pass_ratio',
    'station_fail_rate_smoothed', 'station_x_prev_outcome_fail_rate',
    'multi_system_advisory_count', 'n_prior_fails', 'fails_last_365d',
    'fails_last_730d', 'fail_rate_trend', 'recent_fail_intensity', 'mdps_score',
    'front_end_advisory_intensity', 'brake_system_stress', 'commercial_wear_proxy',
    'usage_band_hybrid', 'has_prior_advisory_brakes', 'tests_since_last_advisory_brakes',
    'advisory_in_last_1_brakes', 'advisory_in_last_2_brakes', 'advisory_streak_len_brakes',
    'has_prior_advisory_tyres', 'miles_since_last_advisory_tyres',
    'tests_since_last_advisory_tyres', 'advisory_in_last_1_tyres',
    'advisory_in_last_2_tyres', 'advisory_streak_len_tyres',
    'has_prior_advisory_suspension', 'miles_since_last_advisory_suspension',
    'tests_since_last_advisory_suspension', 'advisory_in_last_1_suspension',
    'advisory_in_last_2_suspension', 'advisory_streak_len_suspension',
    'has_prior_failure_brakes', 'has_ever_failed_brakes', 'failure_streak_len_brakes',
    'tests_since_last_failure_brakes', 'has_prior_failure_tyres', 'has_ever_failed_tyres',
    'failure_streak_len_tyres', 'tests_since_last_failure_tyres',
    'has_prior_failure_suspension', 'has_ever_failed_suspension',
    'failure_streak_len_suspension', 'tests_since_last_failure_suspension',
    'station_strictness_bias', 'annualized_mileage_v2', 'mileage_anomaly_flag',
    'has_prev_mileage', 'mileage_plausible_flag', 'local_corrosion_index',
    'local_corrosion_delta', 'high_risk_model_flag', 'suspension_risk_profile',
    'model_age_fail_rate_eb', 'make_age_fail_rate_eb',
    'historic_negligence_ratio_smoothed', 'negligence_band', 'raw_behavioral_count',
    'eb_unified_prior', 'mech_decay_brake', 'mech_decay_suspension',
    'mech_decay_structure', 'mech_decay_steering', 'mech_decay_index',
    'mech_decay_index_normalized', 'mech_risk_driver', 'text_corrosion_index',
    'text_wear_index', 'text_leak_index', 'text_damage_index',
    'text_corrosion_index_log', 'text_wear_index_log', 'text_leak_index_log',
    'text_damage_index_log', 'has_corrosion_history', 'has_wear_history',
    'has_leak_history', 'has_damage_history', 'mechanism_count', 'max_severity_score',
    'severity_escalation_flag', 'has_advisory_history', 'dominant_mechanism',
    'test_month', 'is_winter_test', 'day_of_week'
]

# Categorical feature indices
CATEGORICAL_INDICES = [0, 1, 2, 3, 32, 74, 83, 100, 101, 103]

# Component categories for defect classification
COMPONENT_CATEGORIES = {
    'brakes': ['brake', 'braking', 'disc', 'pad', 'caliper', 'abs'],
    'suspension': ['suspension', 'shock', 'absorber', 'spring', 'wishbone', 'arm', 'bush', 'bearing'],
    'steering': ['steering', 'rack', 'tie rod', 'track rod', 'ball joint', 'power steering'],
    'tyres': ['tyre', 'tire', 'wheel', 'rim'],
    'structure': ['chassis', 'subframe', 'sill', 'floor', 'structural', 'body'],
}

# Text mining keywords
TEXT_KEYWORDS = {
    'corrosion': ['corroded', 'corrosion', 'rust', 'rusted', 'oxidation', 'pitting'],
    'wear': ['worn', 'wear', 'excessive wear', 'deteriorated', 'perished'],
    'leak': ['leak', 'leaking', 'fluid', 'oil', 'hydraulic'],
    'damage': ['damaged', 'cracked', 'broken', 'fractured', 'bent', 'distorted'],
}


def classify_defect_component(defect_text: str) -> Optional[str]:
    """Classify a defect into a component category."""
    text_lower = defect_text.lower()
    for component, keywords in COMPONENT_CATEGORIES.items():
        if any(kw in text_lower for kw in keywords):
            return component
    return None


def extract_text_signals(defect_text: str) -> Dict[str, bool]:
    """Extract text mining signals from defect description."""
    text_lower = defect_text.lower()
    signals = {}
    for signal_type, keywords in TEXT_KEYWORDS.items():
        signals[signal_type] = any(kw in text_lower for kw in keywords)
    return signals


def get_gap_band(days: int) -> str:
    """Convert days since last test to gap band."""
    if days < 300:
        return 'early'
    elif days < 365:
        return 'on_time'
    elif days < 400:
        return 'slightly_late'
    elif days < 500:
        return 'late'
    else:
        return 'very_late'


def get_usage_band(annualized_mileage: float) -> str:
    """Convert annualized mileage to usage band."""
    if annualized_mileage < 5000:
        return 'low'
    elif annualized_mileage < 10000:
        return 'average'
    elif annualized_mileage < 15000:
        return 'high'
    else:
        return 'very_high'


def engineer_features(
    history: VehicleHistory,
    postcode: str,
    prediction_date: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Engineer all 104 features from DVSA vehicle history.

    Args:
        history: VehicleHistory from DVSA API
        postcode: UK postcode for corrosion index
        prediction_date: Date for prediction (defaults to now)

    Returns:
        Dict mapping feature names to values
    """
    if prediction_date is None:
        prediction_date = datetime.now()

    features = {}
    tests = history.mot_tests

    # Sort tests by date (newest first)
    tests = sorted(tests, key=lambda t: t.test_date, reverse=True)

    # Get latest test for temporal features
    latest_test = tests[0] if tests else None

    # =========================================================================
    # TEMPORAL FEATURES (V55)
    # =========================================================================
    if latest_test:
        features['test_month'] = str(latest_test.test_date.month)
        features['is_winter_test'] = 1 if latest_test.test_date.month in [10, 11, 12, 1, 2, 3] else 0
        features['day_of_week'] = str(latest_test.test_date.weekday())
    else:
        features['test_month'] = str(prediction_date.month)
        features['is_winter_test'] = 1 if prediction_date.month in [10, 11, 12, 1, 2, 3] else 0
        features['day_of_week'] = str(prediction_date.weekday())

    # =========================================================================
    # BASIC FEATURES
    # =========================================================================
    features['make'] = history.make

    # Previous cycle outcome
    if len(tests) >= 1:
        prev_result = tests[0].test_result
        if prev_result == 'PASSED':
            features['prev_cycle_outcome_band'] = 'pass'
        elif prev_result == 'FAILED':
            features['prev_cycle_outcome_band'] = 'fail'
        else:
            features['prev_cycle_outcome_band'] = 'unknown'
    else:
        features['prev_cycle_outcome_band'] = 'first_test'

    # Gap band (days since last test)
    if len(tests) >= 2:
        days_since = (tests[0].test_date - tests[1].test_date).days
        features['gap_band'] = get_gap_band(days_since)
        features['days_since_last_test'] = days_since
    else:
        features['gap_band'] = 'first_test'
        features['days_since_last_test'] = 0

    # Test mileage
    if latest_test and latest_test.odometer_value:
        mileage = latest_test.odometer_value
        if latest_test.odometer_unit == 'km':
            mileage = int(mileage * 0.621371)
        features['test_mileage'] = mileage
        features['has_prev_mileage'] = 1
        features['mileage_plausible_flag'] = 1
        features['mileage_anomaly_flag'] = 0
    else:
        features['test_mileage'] = 0
        features['has_prev_mileage'] = 0
        features['mileage_plausible_flag'] = 0
        features['mileage_anomaly_flag'] = 0

    # =========================================================================
    # ADVISORY FEATURES
    # =========================================================================
    # Count advisories from all tests
    all_advisories = []
    component_advisories = {comp: [] for comp in COMPONENT_CATEGORIES.keys()}
    text_signals_total = {k: 0 for k in TEXT_KEYWORDS.keys()}

    for test in tests:
        for defect in test.defects:
            defect_text = defect.get('text', '') or defect.get('type', '')
            if defect.get('type') == 'ADVISORY' or 'advisory' in str(defect).lower():
                all_advisories.append({'test': test, 'text': defect_text})

                # Classify by component
                component = classify_defect_component(defect_text)
                if component:
                    component_advisories[component].append({'test': test, 'text': defect_text})

                # Extract text signals
                signals = extract_text_signals(defect_text)
                for signal_type, present in signals.items():
                    if present:
                        text_signals_total[signal_type] += 1

    # Previous advisory count
    features['prev_count_advisory'] = len(all_advisories)

    # Advisory trend
    if len(tests) >= 2:
        recent_adv = sum(1 for a in all_advisories if a['test'] == tests[0])
        older_adv = sum(1 for a in all_advisories if a['test'] == tests[1])
        if recent_adv > older_adv:
            features['advisory_trend'] = 'increasing'
        elif recent_adv < older_adv:
            features['advisory_trend'] = 'decreasing'
        else:
            features['advisory_trend'] = 'stable'
    else:
        features['advisory_trend'] = 'unknown'

    # Component-specific advisory counts
    features['prev_adv_brakes'] = len(component_advisories['brakes'])
    features['prev_adv_suspension'] = len(component_advisories['suspension'])
    features['prev_adv_steering'] = len(component_advisories['steering'])
    features['prev_adv_tyres'] = len(component_advisories['tyres'])

    # Multi-system advisory count
    systems_with_advisories = sum(1 for comp, advs in component_advisories.items() if len(advs) > 0)
    features['multi_system_advisory_count'] = systems_with_advisories

    # =========================================================================
    # COMPONENT-SPECIFIC ADVISORY FEATURES (V32)
    # =========================================================================
    for component in ['brakes', 'tyres', 'suspension']:
        advs = component_advisories[component]
        features[f'has_prior_advisory_{component}'] = 1 if len(advs) > 0 else 0

        # Tests since last advisory
        if len(advs) > 0:
            last_adv_test_idx = next((i for i, t in enumerate(tests) if any(a['test'] == t for a in advs)), len(tests))
            features[f'tests_since_last_advisory_{component}'] = last_adv_test_idx
        else:
            features[f'tests_since_last_advisory_{component}'] = 999

        # Advisory in last 1/2 tests
        recent_advs = [a for a in advs if tests and a['test'] in tests[:1]]
        features[f'advisory_in_last_1_{component}'] = 1 if len(recent_advs) > 0 else 0

        recent_advs_2 = [a for a in advs if tests and a['test'] in tests[:2]]
        features[f'advisory_in_last_2_{component}'] = 1 if len(recent_advs_2) > 0 else 0

        # Advisory streak length
        streak = 0
        for test in tests:
            if any(a['test'] == test for a in advs):
                streak += 1
            else:
                break
        features[f'advisory_streak_len_{component}'] = streak

    # Miles since last advisory for tyres/suspension
    for component in ['tyres', 'suspension']:
        advs = component_advisories[component]
        if len(advs) > 0 and features['has_prev_mileage']:
            features[f'miles_since_last_advisory_{component}'] = 5000  # Default estimate
        else:
            features[f'miles_since_last_advisory_{component}'] = 0

    # =========================================================================
    # FAILURE FEATURES
    # =========================================================================
    failed_tests = [t for t in tests if t.test_result == 'FAILED']
    features['n_prior_fails'] = len(failed_tests)
    features['n_prior_tests'] = len(tests)

    # Prior fail rate
    if len(tests) > 0:
        features['prior_fail_rate_smoothed'] = len(failed_tests) / len(tests)
    else:
        features['prior_fail_rate_smoothed'] = 0.0

    # Fails in last 365/730 days
    now = prediction_date
    features['fails_last_365d'] = sum(1 for t in failed_tests if (now - t.test_date).days <= 365)
    features['fails_last_730d'] = sum(1 for t in failed_tests if (now - t.test_date).days <= 730)

    # Fail rate trend
    if len(tests) >= 4:
        recent_fails = sum(1 for t in tests[:2] if t.test_result == 'FAILED')
        older_fails = sum(1 for t in tests[2:4] if t.test_result == 'FAILED')
        features['fail_rate_trend'] = recent_fails - older_fails
    else:
        features['fail_rate_trend'] = 0

    # Recent fail intensity
    features['recent_fail_intensity'] = features['fails_last_365d']

    # =========================================================================
    # COMPONENT-SPECIFIC FAILURE FEATURES (V32)
    # =========================================================================
    # Classify failures by component
    component_failures = {comp: [] for comp in ['brakes', 'tyres', 'suspension']}

    for test in failed_tests:
        for defect in test.defects:
            defect_text = defect.get('text', '') or defect.get('type', '')
            if defect.get('type') == 'FAIL' or 'fail' in str(defect).lower():
                component = classify_defect_component(defect_text)
                if component in component_failures:
                    component_failures[component].append(test)

    for component in ['brakes', 'tyres', 'suspension']:
        fails = component_failures[component]
        features[f'has_prior_failure_{component}'] = 1 if len(fails) > 0 else 0
        features[f'has_ever_failed_{component}'] = 1 if len(fails) > 0 else 0

        # Failure streak
        streak = 0
        for test in tests:
            if test in fails:
                streak += 1
            else:
                break
        features[f'failure_streak_len_{component}'] = streak

        # Tests since last failure
        if len(fails) > 0:
            last_fail_idx = next((i for i, t in enumerate(tests) if t in fails), len(tests))
            features[f'tests_since_last_failure_{component}'] = last_fail_idx
        else:
            features[f'tests_since_last_failure_{component}'] = 999

    # =========================================================================
    # TEXT MINING FEATURES (V52)
    # =========================================================================
    for signal_type in ['corrosion', 'wear', 'leak', 'damage']:
        count = text_signals_total[signal_type]
        features[f'text_{signal_type}_index'] = count
        features[f'text_{signal_type}_index_log'] = math.log1p(count)
        features[f'has_{signal_type}_history'] = 1 if count > 0 else 0

    # Mechanism count and dominant
    mechanisms_present = [k for k, v in text_signals_total.items() if v > 0]
    features['mechanism_count'] = len(mechanisms_present)
    features['has_advisory_history'] = 1 if len(all_advisories) > 0 else 0

    if len(mechanisms_present) == 0:
        features['dominant_mechanism'] = 'CLEAN' if len(tests) > 0 else 'NO_HISTORY'
    elif len(mechanisms_present) == 1:
        features['dominant_mechanism'] = mechanisms_present[0].upper()
    else:
        # Find most common
        max_count = max(text_signals_total.values())
        dominant = [k for k, v in text_signals_total.items() if v == max_count][0]
        features['dominant_mechanism'] = dominant.upper()

    # Severity features
    features['max_severity_score'] = 2 if len(failed_tests) > 0 else (1 if len(all_advisories) > 0 else 0)
    features['severity_escalation_flag'] = 1 if features['fail_rate_trend'] > 0 else 0

    # =========================================================================
    # MECHANICAL DECAY FEATURES (V51)
    # =========================================================================
    # Simplified decay indices based on advisory counts
    features['mech_decay_brake'] = min(len(component_advisories['brakes']) * 0.2, 1.0)
    features['mech_decay_suspension'] = min(len(component_advisories['suspension']) * 0.2, 1.0)
    features['mech_decay_structure'] = min(len(component_advisories.get('structure', [])) * 0.2, 1.0)
    features['mech_decay_steering'] = min(len(component_advisories['steering']) * 0.2, 1.0)

    # Composite decay index
    decay_values = [features['mech_decay_brake'], features['mech_decay_suspension'],
                    features['mech_decay_structure'], features['mech_decay_steering']]
    features['mech_decay_index'] = max(decay_values)
    features['mech_decay_index_normalized'] = features['mech_decay_index']

    # Mechanical risk driver
    if features['mech_decay_index'] == 0:
        features['mech_risk_driver'] = 'CLEAN'
    elif max(decay_values) == features['mech_decay_brake']:
        features['mech_risk_driver'] = 'BRAKE'
    elif max(decay_values) == features['mech_decay_suspension']:
        features['mech_risk_driver'] = 'SUSP'
    elif max(decay_values) == features['mech_decay_steering']:
        features['mech_risk_driver'] = 'STEER'
    else:
        features['mech_risk_driver'] = 'STRUCT'

    # =========================================================================
    # REGIONAL/STATION FEATURES
    # =========================================================================
    features['local_corrosion_index'] = get_corrosion_index(postcode)
    features['local_corrosion_delta'] = features['local_corrosion_index'] - 0.5  # Delta from neutral
    features['station_strictness_bias'] = get_station_strictness_bias()
    features['station_fail_rate_smoothed'] = 0.25  # Default
    features['station_x_prev_outcome_fail_rate'] = 0.25  # Default

    # =========================================================================
    # USAGE/BEHAVIORAL FEATURES
    # =========================================================================
    # Annualized mileage
    if len(tests) >= 2 and features['has_prev_mileage']:
        mileage_diff = (tests[0].odometer_value or 0) - (tests[1].odometer_value or 0)
        days_diff = (tests[0].test_date - tests[1].test_date).days
        if days_diff > 0 and mileage_diff > 0:
            annualized = (mileage_diff / days_diff) * 365
            features['annualized_mileage_v2'] = annualized
            features['usage_band_hybrid'] = get_usage_band(annualized)
        else:
            features['annualized_mileage_v2'] = 10000  # Default
            features['usage_band_hybrid'] = 'average'
    else:
        features['annualized_mileage_v2'] = 10000  # Default
        features['usage_band_hybrid'] = 'average'

    # Days late (after expiry)
    if latest_test and latest_test.expiry_date:
        days_late = (prediction_date - latest_test.expiry_date).days
        features['days_late'] = max(0, days_late)
    else:
        features['days_late'] = 0

    # =========================================================================
    # COHORT/COMPARATIVE FEATURES (defaults)
    # =========================================================================
    features['mileage_cohort_ratio'] = 1.0  # Neutral
    features['advisory_cohort_delta'] = 0.0  # Neutral
    features['days_since_pass_ratio'] = 1.0  # Neutral

    # =========================================================================
    # HIERARCHICAL/EB FEATURES (defaults)
    # =========================================================================
    # These would normally come from pre-computed population statistics
    base_rate = 0.28  # UK average MOT fail rate

    features['make_fail_rate_smoothed'] = base_rate
    features['segment_fail_rate_smoothed'] = base_rate
    features['model_fail_rate_smoothed'] = base_rate
    features['model_age_fail_rate_eb'] = base_rate
    features['make_age_fail_rate_eb'] = base_rate
    features['eb_unified_prior'] = base_rate

    # =========================================================================
    # DERIVED FEATURES
    # =========================================================================
    # Front end advisory intensity
    features['front_end_advisory_intensity'] = (
        features['prev_adv_steering'] +
        features['prev_adv_suspension'] +
        features['prev_adv_tyres']
    )

    # Brake system stress
    vehicle_age = 5  # Default estimate
    if history.manufacture_date:
        vehicle_age = (prediction_date - history.manufacture_date).days / 365
    features['brake_system_stress'] = features['prev_adv_brakes'] + (vehicle_age * 0.1)

    # Commercial wear proxy
    features['commercial_wear_proxy'] = vehicle_age * 0.1

    # MDPS score (maintenance deferral propensity)
    features['mdps_score'] = features['days_late'] / 365 if features['days_late'] > 0 else 0

    # =========================================================================
    # NEGLIGENCE FEATURES
    # =========================================================================
    # Simplified negligence ratio
    if len(tests) > 0 and features['prev_count_advisory'] > 0:
        neglect_ratio = features['prev_count_advisory'] / len(tests)
        features['historic_negligence_ratio_smoothed'] = min(neglect_ratio, 1.0)
    else:
        features['historic_negligence_ratio_smoothed'] = 0.0

    if features['historic_negligence_ratio_smoothed'] < 0.2:
        features['negligence_band'] = 'low'
    elif features['historic_negligence_ratio_smoothed'] < 0.5:
        features['negligence_band'] = 'medium'
    else:
        features['negligence_band'] = 'high'

    features['raw_behavioral_count'] = features['prev_count_advisory']

    # =========================================================================
    # MODEL-SPECIFIC FLAGS
    # =========================================================================
    features['high_risk_model_flag'] = 0  # Default
    features['suspension_risk_profile'] = 0.0  # Default

    return features


def features_to_array(features: Dict[str, Any]) -> List[Any]:
    """
    Convert features dict to array in exact order expected by model.

    Args:
        features: Dict mapping feature names to values

    Returns:
        List of feature values in model's expected order
    """
    return [features.get(name, 0) for name in FEATURE_NAMES]


def get_feature_names() -> List[str]:
    """Get list of feature names in model order."""
    return FEATURE_NAMES.copy()


def get_categorical_indices() -> List[int]:
    """Get indices of categorical features."""
    return CATEGORICAL_INDICES.copy()
