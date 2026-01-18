"""
V55 CatBoost Model Inference
============================

Loads and runs the V55 CatBoost production model with Platt calibration.

Usage:
    from model_v55 import load_model, predict_risk

    # Load model once at startup
    load_model()

    # Predict risk for a vehicle
    features = engineer_features(history, postcode)
    prediction = predict_risk(features)
"""

import pickle
import hashlib
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

import numpy as np
from catboost import CatBoostClassifier

from feature_engineering_v55 import (
    features_to_array,
    get_feature_names,
    get_categorical_indices,
    FEATURE_NAMES
)

logger = logging.getLogger(__name__)

# Model artifacts directory
MODEL_DIR = Path(__file__).parent / "catboost_production_v55"

# Expected SHA256 hashes for model artifacts (security: prevents loading tampered files)
# Update these hashes when model is retrained
EXPECTED_HASHES = {
    "model.cbm": "025a0e68962299e1138ae16583b55f9e4e0027b1e4240187652bebd04e396c62",
    "platt_calibrator.pkl": "997f01489cd88795471457fb9f9d5fdb986a792f69faaa4ac99185040910b44c",
    "cohort_stats.pkl": "b8d208857946d920ab64a150e3b990a8848ad36a9e4f1aefb57892d285cfb7b3",
}

# Global model instances
_model: Optional[CatBoostClassifier] = None
_calibrator: Optional[Any] = None  # Platt calibrator (sklearn LogisticRegression)
_cohort_stats: Optional[Dict] = None


def _verify_file_integrity(file_path: Path, expected_hash: str) -> bool:
    """
    Verify file integrity using SHA256 hash.

    Args:
        file_path: Path to file to verify
        expected_hash: Expected SHA256 hash (lowercase hex)

    Returns:
        True if hash matches, False otherwise
    """
    try:
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256_hash.update(chunk)
        actual_hash = sha256_hash.hexdigest()

        if actual_hash != expected_hash:
            logger.error(
                f"INTEGRITY CHECK FAILED for {file_path.name}: "
                f"expected {expected_hash[:16]}..., got {actual_hash[:16]}..."
            )
            return False

        logger.info(f"Integrity verified for {file_path.name}")
        return True
    except Exception as e:
        logger.error(f"Failed to verify {file_path}: {e}")
        return False


def _safe_pickle_load(file_path: Path, expected_hash: str) -> Any:
    """
    Safely load a pickle file after verifying its integrity.

    Args:
        file_path: Path to pickle file
        expected_hash: Expected SHA256 hash

    Returns:
        Loaded object

    Raises:
        SecurityError: If integrity check fails
    """
    if not _verify_file_integrity(file_path, expected_hash):
        raise SecurityError(f"Integrity check failed for {file_path.name}. File may be corrupted or tampered.")

    with open(file_path, 'rb') as f:
        return pickle.load(f)


class SecurityError(Exception):
    """Raised when a security check fails."""
    pass


def load_model() -> bool:
    """
    Load V55 model and calibrator from disk.

    Should be called once at application startup.

    Returns:
        True if loaded successfully, False otherwise
    """
    global _model, _calibrator, _cohort_stats

    try:
        # Load CatBoost model with integrity verification
        model_path = MODEL_DIR / "model.cbm"
        if not model_path.exists():
            logger.error(f"Model file not found: {model_path}")
            return False

        # Verify model file integrity before loading
        if "model.cbm" in EXPECTED_HASHES:
            if not _verify_file_integrity(model_path, EXPECTED_HASHES["model.cbm"]):
                logger.error("Model integrity check failed - refusing to load potentially tampered model")
                return False

        _model = CatBoostClassifier()
        _model.load_model(str(model_path))
        logger.info(f"Loaded CatBoost model from {model_path}")
        logger.info(f"Model has {len(_model.feature_names_)} features")

        # CRITICAL: Validate feature count matches what we expect
        expected_feature_count = len(FEATURE_NAMES)
        actual_feature_count = len(_model.feature_names_)
        if actual_feature_count != expected_feature_count:
            logger.error(
                f"FEATURE MISMATCH: Model expects {actual_feature_count} features, "
                f"but FEATURE_NAMES has {expected_feature_count}. Predictions may be wrong!"
            )
            # This is a critical error - model was likely trained with different features
            raise ValueError(
                f"Feature count mismatch: model={actual_feature_count}, expected={expected_feature_count}"
            )

        # Load Platt calibrator with integrity verification
        calibrator_path = MODEL_DIR / "platt_calibrator.pkl"
        if calibrator_path.exists():
            if "platt_calibrator.pkl" in EXPECTED_HASHES:
                _calibrator = _safe_pickle_load(calibrator_path, EXPECTED_HASHES["platt_calibrator.pkl"])
            else:
                logger.warning("No hash for platt_calibrator.pkl - loading without verification")
                with open(calibrator_path, 'rb') as f:
                    _calibrator = pickle.load(f)
            logger.info("Loaded Platt calibrator")
        else:
            logger.warning("Platt calibrator not found - using raw probabilities")
            _calibrator = None

        # Load cohort stats with integrity verification
        cohort_path = MODEL_DIR / "cohort_stats.pkl"
        if cohort_path.exists():
            if "cohort_stats.pkl" in EXPECTED_HASHES:
                _cohort_stats = _safe_pickle_load(cohort_path, EXPECTED_HASHES["cohort_stats.pkl"])
            else:
                logger.warning("No hash for cohort_stats.pkl - loading without verification")
                with open(cohort_path, 'rb') as f:
                    _cohort_stats = pickle.load(f)
            logger.info("Loaded cohort statistics")

        return True

    except Exception as e:
        logger.error(f"Failed to load V55 model: {e}")
        return False


def is_model_loaded() -> bool:
    """Check if the model is loaded."""
    return _model is not None


def predict_risk(features: Dict[str, Any]) -> Dict[str, Any]:
    """
    Predict MOT failure risk using V55 model.

    Args:
        features: Dict of engineered features from feature_engineering_v55

    Returns:
        Dict with prediction results including:
        - failure_risk: Calibrated failure probability (0-1)
        - raw_probability: Uncalibrated model output
        - confidence_level: High/Medium/Low based on input completeness
        - risk_components: Component-specific risk estimates
    """
    if _model is None:
        raise RuntimeError("V55 model not loaded. Call load_model() first.")

    # Convert features to array
    feature_array = features_to_array(features)

    # Get raw prediction
    raw_prob = _model.predict_proba([feature_array])[0][1]  # Probability of class 1 (failure)

    # Apply Platt calibration if available
    if _calibrator is not None:
        try:
            # Platt calibrator expects log-odds transformed input
            # Clamp raw_prob to avoid log(0) or division by zero
            clamped_prob = np.clip(raw_prob, 1e-10, 1 - 1e-10)
            log_odds = np.log(clamped_prob / (1 - clamped_prob))
            calibrated_prob = _calibrator.predict_proba([[log_odds]])[0][1]
        except Exception as e:
            logger.warning(f"Calibration failed, using raw probability: {e}")
            calibrated_prob = raw_prob
    else:
        calibrated_prob = raw_prob

    # Determine confidence level
    confidence = _calculate_confidence(features)

    # Calculate component-specific risks
    component_risks = _estimate_component_risks(features, calibrated_prob)

    return {
        'failure_risk': round(calibrated_prob, 4),
        'raw_probability': round(raw_prob, 4),
        'confidence_level': confidence,
        'risk_components': component_risks,
        'component_disclaimer': "Component risk estimates are derived approximations based on advisory history, not direct model predictions.",
    }


def _calculate_confidence(features: Dict[str, Any]) -> str:
    """
    Calculate confidence level based on data completeness and quality.

    NOTE: This reflects data availability, NOT model prediction uncertainty.
    A "High" confidence means we have good input data, but the model's
    actual prediction uncertainty may still vary.

    Factors considered:
    - Mileage data availability (important for usage patterns)
    - Number of prior MOT tests (more history = better patterns)
    - Recency of test history (stale data = less relevant)
    - Consistency of test patterns

    Returns:
        'High', 'Medium', or 'Low'
    """
    # Score based on data availability and quality
    score = 0
    n_tests = features.get('n_prior_tests', 0)

    # Has previous mileage (+2) - critical for usage pattern features
    if features.get('has_prev_mileage', 0) == 1:
        score += 2

    # Has prior tests - scaled by number
    if n_tests >= 5:
        score += 3  # Extensive history
    elif n_tests >= 3:
        score += 2  # Good history
    elif n_tests >= 1:
        score += 1  # Minimal history

    # Has recent test history (+1) - data is current
    if features.get('days_since_last_test', 999) < 400:
        score += 1

    # Has consistent mileage data (not anomalous)
    if features.get('mileage_plausible_flag', 0) == 1:
        score += 1

    # Determine level (adjusted thresholds)
    if score >= 6:
        return 'High'
    elif score >= 3:
        return 'Medium'
    else:
        return 'Low'


def _estimate_component_risks(
    features: Dict[str, Any],
    overall_risk: float
) -> Dict[str, float]:
    """
    Estimate component-specific failure risks.

    IMPORTANT: These are DERIVED APPROXIMATIONS, not direct model outputs.
    The CatBoost model predicts overall failure probability only. Component
    breakdowns are estimated using heuristics based on advisory history,
    mechanical decay indices, and population statistics.

    Uses a combination of:
    - Advisory history (indicates wear/issues)
    - Mechanical decay indices
    - Overall failure risk as baseline

    Args:
        features: Engineered features
        overall_risk: Overall failure probability

    Returns:
        Dict mapping component names to estimated risk values
    """
    # Base component risks (empirical averages from MOT data)
    base_risks = {
        'brakes': 0.05,
        'suspension': 0.04,
        'tyres': 0.03,
        'steering': 0.02,
        'visibility': 0.02,
        'lamps': 0.03,
        'body': 0.02,
    }

    component_risks = {}

    for component, base_risk in base_risks.items():
        # Adjust based on advisory history
        advisory_key = f'prev_adv_{component}' if component in ['brakes', 'suspension', 'steering', 'tyres'] else None
        decay_key = f'mech_decay_{component}' if component in ['brakes', 'suspension', 'steering'] else None

        multiplier = 1.0

        # Advisory history increases risk
        if advisory_key and features.get(advisory_key, 0) > 0:
            multiplier += 0.5 * min(features.get(advisory_key, 0), 3)

        # Mechanical decay increases risk
        if decay_key:
            decay = features.get(decay_key, 0)
            multiplier += decay * 2

        # Prior failure increases risk significantly
        failure_key = f'has_prior_failure_{component}'
        if features.get(failure_key, 0) == 1:
            multiplier += 1.0

        # Scale by overall risk
        risk_ratio = overall_risk / 0.28  # 0.28 is average fail rate
        adjusted_risk = base_risk * multiplier * risk_ratio

        # Clamp to reasonable range
        component_risks[component] = round(min(max(adjusted_risk, 0.01), 0.5), 4)

    return component_risks


def get_model_info() -> Dict[str, Any]:
    """Get information about the loaded model."""
    if _model is None:
        return {'loaded': False}

    return {
        'loaded': True,
        'feature_count': len(_model.feature_names_),
        'feature_names': _model.feature_names_,
        'has_calibrator': _calibrator is not None,
        'model_path': str(MODEL_DIR / "model.cbm"),
    }


def get_feature_importance(top_n: int = 20) -> List[Dict[str, Any]]:
    """
    Get top feature importances from the model.

    Args:
        top_n: Number of top features to return

    Returns:
        List of dicts with 'feature' and 'importance'
    """
    if _model is None:
        return []

    importances = _model.get_feature_importance()
    feature_names = _model.feature_names_

    # Sort by importance
    sorted_features = sorted(
        zip(feature_names, importances),
        key=lambda x: x[1],
        reverse=True
    )

    return [
        {'feature': name, 'importance': round(imp, 4)}
        for name, imp in sorted_features[:top_n]
    ]
