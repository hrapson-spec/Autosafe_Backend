"""
V55 CatBoost Model Inference
============================

Loads and runs the V55 CatBoost production model with Platt calibration.

Usage:
    from model_v55 import load_model, engineer_features_with_stats, predict_risk

    # Load model once at startup (also loads cohort stats and EB priors)
    load_model()

    # Predict risk for a vehicle using full survivorship adjustments
    features = engineer_features_with_stats(history, postcode)
    prediction = predict_risk(features)

Note: engineer_features_with_stats() uses the loaded cohort stats and EB priors
to properly calculate survivorship features like advisory_cohort_delta,
mileage_cohort_ratio, model_age_fail_rate_eb, and mech_decay_index_normalized.
"""

import pickle
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

import numpy as np
from catboost import CatBoostClassifier

from calibrator import PlattCalibrator

from feature_engineering_v55 import (
    features_to_array,
    get_feature_names,
    get_categorical_indices,
    engineer_features,
    FEATURE_NAMES
)
from dvsa_client import VehicleHistory
from vocab_shim import apply_vocab_shim

logger = logging.getLogger(__name__)

# Model artifacts directory
MODEL_DIR = Path(__file__).parent / "catboost_production_v55"

# Global model instances
_model: Optional[CatBoostClassifier] = None
_calibrator: Optional[Any] = None  # Platt calibrator (pickle-free PlattCalibrator)
_cohort_stats: Optional[Dict] = None
_model_hierarchical: Optional[Any] = None  # ModelHierarchicalFeatures for EB priors
_segment_hierarchical: Optional[Any] = None  # Segment-level rates (make, age_band, mileage_band)
_model_age_hierarchical: Optional[Dict] = None  # V45: Model-age EB rates (13.4% importance)
_chronos_forecast: Optional[Dict] = None  # Chronos-2 forward-looking segment-rate forecasts


def load_model() -> bool:
    """
    Load V55 model and calibrator from disk.

    Should be called once at application startup.

    Returns:
        True if loaded successfully, False otherwise
    """
    global _model, _calibrator, _cohort_stats, _model_hierarchical, _segment_hierarchical, _model_age_hierarchical, _chronos_forecast

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

        # Load Platt calibrator (pickle-free: sigmoid constants from JSON).
        # The legacy platt_calibrator.pkl is kept on disk for provenance only —
        # it was pickled under scikit-learn 1.8.0 and raises at predict time on
        # the sklearn <=1.6 that python:3.9 production can install, which made
        # the calibrator silently inert (audit P0 gf2b). The JSON artifact is
        # REQUIRED: failing to load it fails load_model() loudly rather than
        # silently serving raw probabilities.
        calibrator_path = MODEL_DIR / "calibrator.json"
        _calibrator = PlattCalibrator.from_json(calibrator_path)
        _calibrator.self_check()
        logger.info(
            "Loaded pickle-free Platt calibrator (A=%.6f, B=%.6f) and passed self-check",
            _calibrator.A, _calibrator.B,
        )

        # Load cohort stats (for survivorship adjustments)
        cohort_path = MODEL_DIR / "cohort_stats.pkl"
        if cohort_path.exists():
            with open(cohort_path, 'rb') as f:
                _cohort_stats = pickle.load(f)
            logger.info("Loaded cohort statistics for survivorship features")
        else:
            logger.warning("Cohort stats not found - survivorship features will use defaults")
            _cohort_stats = None

        # Load model hierarchical features (for EB priors)
        hierarchical_path = MODEL_DIR / "model_hierarchical_features.pkl"
        if hierarchical_path.exists():
            with open(hierarchical_path, 'rb') as f:
                _model_hierarchical = pickle.load(f)
            logger.info("Loaded model hierarchical features for EB priors")
        else:
            logger.warning("Model hierarchical features not found - EB priors will use defaults")
            _model_hierarchical = None

        # Load segment hierarchical features (make, age_band, mileage_band rates)
        segment_path = MODEL_DIR / "segment_hierarchical_features.pkl"
        if segment_path.exists():
            with open(segment_path, 'rb') as f:
                _segment_hierarchical = pickle.load(f)
            logger.info("Loaded segment hierarchical features")
        else:
            _segment_hierarchical = None

        # Load model-age hierarchical features (13.4% importance - critical for inference)
        model_age_path = MODEL_DIR / "model_age_hierarchical.pkl"
        if model_age_path.exists():
            with open(model_age_path, 'rb') as f:
                _model_age_hierarchical = pickle.load(f)
            n_rates = len(_model_age_hierarchical.get('model_age_rates', {}))
            logger.info(f"Loaded model-age hierarchical features ({n_rates} model-age rates)")
        else:
            logger.warning("Model-age hierarchical not found - model_age_fail_rate_eb will use defaults")
            _model_age_hierarchical = None

        # Load Chronos-2 forward-looking forecast lookup (offline-built; pkl so it
        # ships through .railwayignore). Pure pickle.load of a dict — NO torch import
        # here, so the serving image stays torch-free. Absent until the offline job
        # has run + a retrained model expects the features; serving degrades to the
        # base-rate fallback when missing (graceful, never fatal).
        chronos_path = MODEL_DIR / "chronos_segment_forecast.pkl"
        if chronos_path.exists():
            with open(chronos_path, 'rb') as f:
                _chronos_forecast = pickle.load(f)
            logger.info(
                "Loaded Chronos-2 forecast lookup (%d segment entries, latest_asof_month=%s)",
                len(_chronos_forecast.get('overall', {})),
                _chronos_forecast.get('latest_asof_month'),
            )
        else:
            logger.warning("Chronos forecast lookup not found - chronos_* features will use base-rate defaults")
            _chronos_forecast = None

        return True

    except Exception as e:
        logger.error(f"Failed to load V55 model: {e}")
        return False


def is_model_loaded() -> bool:
    """Check if the model is loaded."""
    return _model is not None


def calibrator_state() -> Dict[str, Any]:
    """Operational calibrator state for /health diagnostics (audit gf2b rec).

    Surfaces whether predictions are being calibrated and with which constants,
    so a silently-inert calibrator can never again go unnoticed.
    """
    if _calibrator is None:
        return {"status": "not_loaded", "serving": "raw_probabilities"}
    return {
        "status": "loaded",
        "type": "platt_sigmoid_pickle_free",
        "A": _calibrator.A,
        "B": _calibrator.B,
    }


def get_cohort_stats() -> Optional[Dict]:
    """Get loaded cohort statistics (for advanced use cases)."""
    return _cohort_stats


def get_model_hierarchical() -> Optional[Any]:
    """Get loaded model hierarchical features (for advanced use cases)."""
    return _model_hierarchical


def get_model_age_hierarchical() -> Optional[Dict]:
    """Get loaded model-age hierarchical features (for model_age_fail_rate_eb lookups)."""
    return _model_age_hierarchical


def engineer_features_with_stats(
    history: VehicleHistory,
    postcode: str,
    prediction_date: Optional['datetime'] = None,
) -> Dict[str, Any]:
    """
    Engineer features using loaded cohort stats and EB priors.

    This is the preferred way to engineer features for prediction,
    as it uses the survivorship adjustment data loaded from model artifacts.

    Args:
        history: VehicleHistory from DVSA API
        postcode: UK postcode for corrosion index
        prediction_date: Date for prediction (defaults to now)

    Returns:
        Dict mapping feature names to values, with proper survivorship adjustments
    """
    from datetime import datetime as dt
    if prediction_date is None:
        prediction_date = dt.now()

    return engineer_features(
        history=history,
        postcode=postcode,
        prediction_date=prediction_date,
        cohort_stats=_cohort_stats,
        model_hierarchical=_model_hierarchical,
        model_age_hierarchical=_model_age_hierarchical,
        segment_hierarchical=_segment_hierarchical,
        chronos_forecast=_chronos_forecast,
    )


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

    # GF-17 Phase A2: map serving categorical emissions onto the deployed
    # model's training vocabulary at the model boundary only (vocab_shim.py).
    # `features` itself is left unshimmed for confidence/component consumers.
    #
    # Serialise in the loaded model's OWN feature order (feature_names_) rather
    # than the static FEATURE_NAMES. For the current 104-feature model this is
    # byte-identical to FEATURE_NAMES; for a retrained model that adds the
    # chronos_* features it activates them with no edit here (they are already
    # present in `features`). This is the dormant-until-retrain mechanism.
    feature_array = features_to_array(
        apply_vocab_shim(features), feature_names=list(_model.feature_names_)
    )

    # Get raw prediction
    raw_prob = _model.predict_proba([feature_array])[0][1]  # Probability of class 1 (failure)

    # Clamp raw probability to avoid log(0) or log(1) issues (P0-3 fix)
    # Using 1e-8 instead of 1e-10 for extra safety margin in log-odds calculation
    raw_prob = float(np.clip(raw_prob, 1e-8, 1 - 1e-8))

    # Apply Platt calibration if available
    if _calibrator is not None:
        try:
            # The calibrator was fitted on raw probabilities
            # (train_catboost_production_v55.py:
            #  calibrator.fit(y_pred_train.reshape(-1, 1), y_train)),
            # so it must receive raw probabilities here. Transforming to
            # log-odds first puts the input outside the fitted domain and
            # collapses the calibrated output toward 0/1.
            calibrated_prob = _calibrator.predict_proba([[raw_prob]])[0][1]
            # Ensure calibrated output is valid
            if not np.isfinite(calibrated_prob):
                logger.warning("Non-finite calibrated prob, using raw probability")
                calibrated_prob = raw_prob
        except Exception as e:
            logger.warning(f"Calibration failed, using raw probability: {e}")
            calibrated_prob = raw_prob
    else:
        calibrated_prob = raw_prob

    # Final safety clamp to valid probability range
    calibrated_prob = float(np.clip(calibrated_prob, 0.0, 1.0))

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
