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

# Global model instances
_model: Optional[CatBoostClassifier] = None
_calibrator: Optional[Any] = None  # Platt calibrator (sklearn LogisticRegression)
_cohort_stats: Optional[Dict] = None


def load_model() -> bool:
    """
    Load V55 model and calibrator from disk.

    Should be called once at application startup.

    Returns:
        True if loaded successfully, False otherwise
    """
    global _model, _calibrator, _cohort_stats

    try:
        # Load CatBoost model
        model_path = MODEL_DIR / "model.cbm"
        if not model_path.exists():
            logger.error(f"Model file not found: {model_path}")
            return False

        _model = CatBoostClassifier()
        _model.load_model(str(model_path))
        logger.info(f"Loaded CatBoost model from {model_path}")
        logger.info(f"Model has {len(_model.feature_names_)} features")

        # Load Platt calibrator
        calibrator_path = MODEL_DIR / "platt_calibrator.pkl"
        if calibrator_path.exists():
            with open(calibrator_path, 'rb') as f:
                _calibrator = pickle.load(f)
            logger.info("Loaded Platt calibrator")
        else:
            logger.warning("Platt calibrator not found - using raw probabilities")
            _calibrator = None

        # Load cohort stats (for reference)
        cohort_path = MODEL_DIR / "cohort_stats.pkl"
        if cohort_path.exists():
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
    }


def _calculate_confidence(features: Dict[str, Any]) -> str:
    """
    Calculate confidence level based on feature completeness.

    Returns:
        'High', 'Medium', or 'Low'
    """
    # Score based on data availability
    score = 0

    # Has previous mileage (+2)
    if features.get('has_prev_mileage', 0) == 1:
        score += 2

    # Has prior tests (+2)
    if features.get('n_prior_tests', 0) > 0:
        score += 2

    # Has multiple tests (+1)
    if features.get('n_prior_tests', 0) >= 3:
        score += 1

    # Has recent test history (+1)
    if features.get('days_since_last_test', 999) < 400:
        score += 1

    # Determine level
    if score >= 5:
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

    Uses a combination of:
    - Advisory history (indicates wear/issues)
    - Mechanical decay indices
    - Overall failure risk as baseline

    Args:
        features: Engineered features
        overall_risk: Overall failure probability

    Returns:
        Dict mapping component names to risk values
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
