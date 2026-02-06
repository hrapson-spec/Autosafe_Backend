"""
Feature Engineering for V55 CatBoost Model
==========================================

Extracts and engineers 104 features from DVSA MOT history data
for the V55 CatBoost production model.

V55+neglect: Added 3 neglect_score features (brakes, tyres, suspension)
from V33 with optimized weights for +2.21pp AUC lift.

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

# V44: Top 20 failing models (fail rate > 28%, from training data analysis)
# Must match HIGH_RISK_MODELS in train_catboost_production_v55.py
HIGH_RISK_MODELS_SET = {
    'ROVER 75', 'RENAULT LAGUNA', 'CITROEN XSARA', 'PEUGEOT 307 SW',
    'VAUXHALL VECTRA', 'PEUGEOT 206', 'CHEVROLET MATIZ', 'PEUGEOT 307',
    'VAUXHALL TIGRA', 'RENAULT MODUS', 'NISSAN PRIMASTAR', 'CITROEN C2',
    'RENAULT GRAND SCENIC', 'FORD FOCUS C-MAX', 'VOLKSWAGEN BORA',
    'JAGUAR X TYPE', 'FIAT SCUDO', 'HYUNDAI COUPE',
    'MITSUBISHI L200 DOUBLE CAB', 'MAZDA 5',
}


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


def get_age_band(vehicle_age: int) -> str:
    """Convert vehicle age to age band for cohort lookups."""
    if vehicle_age <= 3:
        return '0-3'
    elif vehicle_age <= 5:
        return '3-5'
    elif vehicle_age <= 10:
        return '6-10'
    elif vehicle_age <= 15:
        return '11-15'
    else:
        return '15+'


def engineer_features(
    history: VehicleHistory,
    postcode: str,
    prediction_date: Optional[datetime] = None,
    cohort_stats: Optional[Dict[str, Any]] = None,
    model_hierarchical: Optional[Any] = None,
    model_age_hierarchical: Optional[Dict[str, Any]] = None,
    segment_hierarchical: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Engineer all 104 features from DVSA vehicle history.

    Args:
        history: VehicleHistory from DVSA API
        postcode: UK postcode for corrosion index
        prediction_date: Date for prediction (defaults to now)
        cohort_stats: Optional cohort statistics for survivorship adjustments
            Expected keys: 'cohort_mileage', 'cohort_advisory', 'global_mileage_avg', 'global_advisory_avg'
        model_hierarchical: Optional ModelHierarchicalFeatures for EB priors
            Expected attributes: 'model_rates', 'make_rates', 'global_fail_rate'
        model_age_hierarchical: Optional dict for model-age EB rates (13.4% importance)
            Expected keys: 'model_age_rates', 'make_age_rates', 'global_fail_rate'
        segment_hierarchical: Optional HierarchicalFeatures for segment-level rates
            Expected attributes: 'segment_rates', 'make_rates', 'global_fail_rate'

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

    # Test mileage (fix: use round instead of int for precision)
    if latest_test and latest_test.odometer_value is not None:
        mileage = latest_test.odometer_value
        # Fix: Handle None unit, default to miles
        unit = (latest_test.odometer_unit or 'mi').lower()
        if unit == 'km':
            mileage = round(mileage * 0.621371)  # Fix: round instead of truncate
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
    # Fix: Include 'structure' in component_advisories for mech_decay_structure
    component_advisories = {comp: [] for comp in list(COMPONENT_CATEGORIES.keys())}
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

        # Fix: Build set first for O(n) instead of O(n²) lookups
        adv_test_set = {id(a['test']) for a in advs}

        # Tests since last advisory
        if len(advs) > 0:
            last_adv_test_idx = next((i for i, t in enumerate(tests) if id(t) in adv_test_set), len(tests))
            features[f'tests_since_last_advisory_{component}'] = last_adv_test_idx
        else:
            features[f'tests_since_last_advisory_{component}'] = 999

        # Advisory in last 1/2 tests
        recent_test_ids = {id(t) for t in tests[:1]} if tests else set()
        features[f'advisory_in_last_1_{component}'] = 1 if adv_test_set & recent_test_ids else 0

        recent_test_ids_2 = {id(t) for t in tests[:2]} if tests else set()
        features[f'advisory_in_last_2_{component}'] = 1 if adv_test_set & recent_test_ids_2 else 0

        # Advisory streak length
        streak = 0
        for test in tests:
            if id(test) in adv_test_set:
                streak += 1
            else:
                break
        features[f'advisory_streak_len_{component}'] = streak

    # Miles since last advisory for tyres/suspension
    # Fix: Calculate actual estimate instead of hardcoded 5000
    for component in ['tyres', 'suspension']:
        advs = component_advisories[component]
        if len(advs) > 0 and features['has_prev_mileage'] and features['test_mileage'] > 0:
            # Estimate based on annualized mileage and time since last advisory
            # Default to average UK annual mileage (~8000) divided by test frequency
            est_annual_miles = 8000
            features[f'miles_since_last_advisory_{component}'] = min(est_annual_miles, features['test_mileage'] // max(len(tests), 1))
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
    # NEGLECT SCORES (V33+ optimized weights)
    # =========================================================================
    # Weights learned via logistic regression on DEV set, validated on OOT
    # Result: +2.21pp AUC improvement over hand-picked weights
    NEGLECT_WEIGHTS = {
        'brakes': {'adv': 0.19, 'fail': 0.55, 'tsf': -0.02},
        'tyres': {'adv': 0.20, 'fail': 0.35, 'tsf': -0.03},
        'suspension': {'adv': 0.36, 'fail': 0.53, 'tsf': 0.06},
    }

    for component in ['brakes', 'tyres', 'suspension']:
        w = NEGLECT_WEIGHTS[component]
        adv_streak = features.get(f'advisory_streak_len_{component}', 0)
        fail_streak = features.get(f'failure_streak_len_{component}', 0)
        tests_since_repair = features.get(f'tests_since_last_failure_{component}', 0)
        # Cap tests_since_repair at reasonable value (999 = never failed)
        if tests_since_repair == 999:
            tests_since_repair = 0  # No prior failure means no repair signal

        features[f'neglect_score_{component}'] = (
            (adv_streak * w['adv']) +
            (fail_streak * w['fail']) +
            (tests_since_repair * w['tsf'])
        )

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

    # Age-normalized decay: divide by mean decay for vehicle's age band
    # This identifies young vehicles with unusually high decay (survivor anomaly A)
    # and genuine survivors with lower-than-expected decay (survivor adjustment B)
    if cohort_stats and 'age_decay_means' in cohort_stats:
        vehicle_age = 5  # Default
        if history.manufacture_date:
            vehicle_age = int((prediction_date - history.manufacture_date).days / 365)
        age_mean = cohort_stats['age_decay_means'].get(vehicle_age, None)
        if age_mean is not None and age_mean > 0:
            features['mech_decay_index_normalized'] = features['mech_decay_index'] / age_mean
        else:
            # Fallback to raw index if age mean not found
            features['mech_decay_index_normalized'] = features['mech_decay_index']
    else:
        # No cohort stats - use raw index
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
    # Fix: Distinguish between 0 miles (valid) and None (missing data)
    if len(tests) >= 2 and features['has_prev_mileage']:
        # Use None-aware comparison: 0 is a valid odometer reading
        val0 = tests[0].odometer_value if tests[0].odometer_value is not None else None
        val1 = tests[1].odometer_value if tests[1].odometer_value is not None else None

        if val0 is not None and val1 is not None:
            mileage_diff = val0 - val1
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
    # COHORT/COMPARATIVE FEATURES
    # =========================================================================
    # Compute vehicle age for cohort lookups
    vehicle_age_for_cohort = 5  # Default estimate
    if history.manufacture_date:
        vehicle_age_for_cohort = int((prediction_date - history.manufacture_date).days / 365)

    # Try to look up cohort stats if provided
    if cohort_stats:
        model_id = f"{history.make} {history.model}" if history.model else history.make
        age_band = get_age_band(vehicle_age_for_cohort)
        cohort_key = (model_id, age_band)

        # Mileage cohort ratio
        cohort_mileage = cohort_stats.get('cohort_mileage', {})
        if cohort_key in cohort_mileage and cohort_mileage[cohort_key] > 0:
            features['mileage_cohort_ratio'] = features['test_mileage'] / cohort_mileage[cohort_key]
        else:
            # Fallback to global average
            global_avg = cohort_stats.get('global_mileage_avg', features['test_mileage'])
            if global_avg > 0:
                features['mileage_cohort_ratio'] = features['test_mileage'] / global_avg
            else:
                features['mileage_cohort_ratio'] = 1.0

        # Advisory cohort delta
        cohort_advisory = cohort_stats.get('cohort_advisory', {})
        if cohort_key in cohort_advisory:
            features['advisory_cohort_delta'] = features['prev_count_advisory'] - cohort_advisory[cohort_key]
        else:
            # Fallback to global average
            global_adv_avg = cohort_stats.get('global_advisory_avg', features['prev_count_advisory'])
            features['advisory_cohort_delta'] = features['prev_count_advisory'] - global_adv_avg
    else:
        # No cohort stats available - use neutral defaults
        features['mileage_cohort_ratio'] = 1.0
        features['advisory_cohort_delta'] = 0.0

    features['days_since_pass_ratio'] = 1.0  # Neutral (not in cohort_stats)

    # =========================================================================
    # HIERARCHICAL/EB FEATURES
    # =========================================================================
    # Base rate for fallbacks
    base_rate = 0.28  # UK average MOT fail rate

    # Try to look up EB priors from model_hierarchical if provided
    if model_hierarchical:
        model_id = f"{history.make} {history.model}" if history.model else history.make
        age_band = get_age_band(vehicle_age_for_cohort)

        # Model-level fail rate
        model_rates = getattr(model_hierarchical, 'model_rates', {})
        if isinstance(model_rates, dict) and model_id in model_rates:
            features['model_fail_rate_smoothed'] = model_rates[model_id]
        else:
            features['model_fail_rate_smoothed'] = base_rate

        # Make-level fail rate
        make_rates = getattr(model_hierarchical, 'make_rates', {})
        if isinstance(make_rates, dict) and history.make in make_rates:
            features['make_fail_rate_smoothed'] = make_rates[history.make]
        else:
            features['make_fail_rate_smoothed'] = base_rate

        # Model-age EB rate (3-level hierarchy: global -> make+age -> model+age)
        # Use separate model_age_hierarchical dict if provided (13.4% importance feature!)
        if model_age_hierarchical:
            model_age_rates = model_age_hierarchical.get('model_age_rates', {})
            make_age_rates = model_age_hierarchical.get('make_age_rates', {})
            global_age_rate = model_age_hierarchical.get('global_fail_rate', base_rate)
        else:
            # Fallback to model_hierarchical attributes (likely empty)
            model_age_rates = getattr(model_hierarchical, 'model_age_rates', {})
            make_age_rates = getattr(model_hierarchical, 'make_age_rates', {})
            global_age_rate = getattr(model_hierarchical, 'global_fail_rate', base_rate)

        model_age_key = (model_id, age_band)
        make_age_key = (history.make, age_band)

        if isinstance(model_age_rates, dict) and model_age_key in model_age_rates:
            features['model_age_fail_rate_eb'] = model_age_rates[model_age_key]
        elif isinstance(make_age_rates, dict) and make_age_key in make_age_rates:
            features['model_age_fail_rate_eb'] = make_age_rates[make_age_key]
        else:
            features['model_age_fail_rate_eb'] = global_age_rate

        # Make-age EB rate
        if isinstance(make_age_rates, dict) and make_age_key in make_age_rates:
            features['make_age_fail_rate_eb'] = make_age_rates[make_age_key]
        else:
            features['make_age_fail_rate_eb'] = global_age_rate

        # Unified EB prior (use model-age as primary)
        features['eb_unified_prior'] = features['model_age_fail_rate_eb']
    else:
        # No model_hierarchical - use base rate defaults for model/make rates
        features['make_fail_rate_smoothed'] = base_rate
        features['model_fail_rate_smoothed'] = base_rate

        # But still try model_age_hierarchical for model-age rates (13.4% importance!)
        if model_age_hierarchical:
            model_id = f"{history.make} {history.model}" if history.model else history.make
            age_band = get_age_band(vehicle_age_for_cohort)

            model_age_rates = model_age_hierarchical.get('model_age_rates', {})
            make_age_rates = model_age_hierarchical.get('make_age_rates', {})
            global_age_rate = model_age_hierarchical.get('global_fail_rate', base_rate)

            model_age_key = (model_id, age_band)
            make_age_key = (history.make, age_band)

            if model_age_key in model_age_rates:
                features['model_age_fail_rate_eb'] = model_age_rates[model_age_key]
            elif make_age_key in make_age_rates:
                features['model_age_fail_rate_eb'] = make_age_rates[make_age_key]
            else:
                features['model_age_fail_rate_eb'] = global_age_rate

            if make_age_key in make_age_rates:
                features['make_age_fail_rate_eb'] = make_age_rates[make_age_key]
            else:
                features['make_age_fail_rate_eb'] = global_age_rate

            features['eb_unified_prior'] = features['model_age_fail_rate_eb']
        else:
            features['model_age_fail_rate_eb'] = base_rate
            features['make_age_fail_rate_eb'] = base_rate
            features['eb_unified_prior'] = base_rate

    # Segment-level fail rate from segment_hierarchical (make, age_band, mileage_band)
    if segment_hierarchical and hasattr(segment_hierarchical, 'segment_rates'):
        age_band_seg = get_age_band(vehicle_age_for_cohort)
        mileage = features.get('test_mileage', 0)
        if mileage < 30000:
            mileage_band = '0-30k'
        elif mileage < 60000:
            mileage_band = '30k-60k'
        elif mileage < 100000:
            mileage_band = '60k-100k'
        else:
            mileage_band = '100k+'
        seg_key = (history.make, age_band_seg, mileage_band)
        seg_rates = segment_hierarchical.segment_rates
        if isinstance(seg_rates, dict) and seg_key in seg_rates:
            features['segment_fail_rate_smoothed'] = seg_rates[seg_key]
        else:
            make_rates_seg = getattr(segment_hierarchical, 'make_rates', {})
            features['segment_fail_rate_smoothed'] = make_rates_seg.get(history.make, base_rate)
    else:
        features['segment_fail_rate_smoothed'] = base_rate

    # =========================================================================
    # DERIVED FEATURES
    # =========================================================================
    # Front end advisory intensity
    features['front_end_advisory_intensity'] = (
        features['prev_adv_steering'] +
        features['prev_adv_suspension'] +
        features['prev_adv_tyres']
    )

    # Brake system stress — must match training formula (train_catboost_production_v55.py:556-564)
    # Training: brakes + np.log1p(n_prior_tests) / 3.0
    n_prior = features.get('n_prior_tests', 0)
    age_factor = np.log1p(n_prior) / 3.0
    features['brake_system_stress'] = features['prev_adv_brakes'] + age_factor

    # Commercial wear proxy — must match training formula (train_catboost_production_v55.py:577-593)
    # Training: log1p(annual_miles/10000) + days_overdue + age_factor
    annual_miles = features.get('annualized_mileage_v2', 8000)
    high_mileage_factor = np.log1p(annual_miles / 10000)
    days_overdue = max(0, features.get('days_since_last_test', 365) - 365) / 365.0
    features['commercial_wear_proxy'] = high_mileage_factor + days_overdue + age_factor

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

    # Negligence bands must match training categories: clean/low/high/chronic/unknown
    # Training uses cohort-relative thresholds; approximate with absolute thresholds
    neg_ratio = features['historic_negligence_ratio_smoothed']
    if neg_ratio == 0:
        features['negligence_band'] = 'clean'
    elif neg_ratio < 0.3:
        features['negligence_band'] = 'low'
    elif neg_ratio < 0.6:
        features['negligence_band'] = 'high'
    else:
        features['negligence_band'] = 'chronic'

    features['raw_behavioral_count'] = features['prev_count_advisory']

    # =========================================================================
    # MODEL-SPECIFIC FLAGS
    # =========================================================================
    model_id = f"{history.make} {history.model}".upper().strip() if history.model else history.make.upper().strip()
    features['high_risk_model_flag'] = 1 if model_id in HIGH_RISK_MODELS_SET else 0
    features['suspension_risk_profile'] = 0.0  # TODO: load from model artifacts

    return features


def features_to_array(features: Dict[str, Any], validate: bool = True) -> List[Any]:
    """
    Convert features dict to array in exact order expected by model.

    Args:
        features: Dict mapping feature names to values
        validate: If True, raise error for missing features (P0-4 fix)

    Returns:
        List of feature values in model's expected order

    Raises:
        ValueError: If validate=True and required features are missing
    """
    if validate:
        missing = [name for name in FEATURE_NAMES if name not in features]
        if missing:
            raise ValueError(
                f"Missing {len(missing)} required features: {missing[:5]}"
                + (f"... and {len(missing)-5} more" if len(missing) > 5 else "")
            )

    # Validate expected feature count matches model expectations
    if len(FEATURE_NAMES) != 104:
        raise ValueError(f"FEATURE_NAMES has {len(FEATURE_NAMES)} entries, expected 104")

    return [features.get(name, 0) for name in FEATURE_NAMES]


def get_feature_names() -> List[str]:
    """Get list of feature names in model order."""
    return FEATURE_NAMES.copy()


def get_categorical_indices() -> List[int]:
    """Get indices of categorical features."""
    return CATEGORICAL_INDICES.copy()
