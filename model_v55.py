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

import hashlib
import os
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

# Expected checksums for model artifacts (SHA256)
# These should be updated when model files are regenerated
# Set SKIP_MODEL_CHECKSUM=1 to bypass verification (development only)
MODEL_CHECKSUMS_FILE = MODEL_DIR / "checksums.sha256"


def _compute_file_checksum(filepath: Path) -> str:
    """Compute SHA256 checksum of a file."""
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def _load_expected_checksums() -> Dict[str, str]:
    """Load expected checksums from checksums file."""
    checksums = {}
    if MODEL_CHECKSUMS_FILE.exists():
        with open(MODEL_CHECKSUMS_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    parts = line.split()
                    if len(parts) >= 2:
                        checksum, filename = parts[0], parts[-1]
                        checksums[filename] = checksum
    return checksums


def _verify_file_integrity(filepath: Path, expected_checksums: Dict[str, str]) -> bool:
    """
    Verify file integrity using SHA256 checksum.

    Returns True if:
    - Checksum verification is disabled via SKIP_MODEL_CHECKSUM env var
    - No checksums file exists (allows first-time setup)
    - File checksum matches expected value

    Returns False if checksum mismatch (potential tampering).
    """
    # Allow bypassing for development
    if os.environ.get("SKIP_MODEL_CHECKSUM", "").lower() in ("1", "true", "yes"):
        logger.warning(f"Checksum verification SKIPPED for {filepath.name} (SKIP_MODEL_CHECKSUM set)")
        return True

    filename = filepath.name

    # If no checksums file or no entry for this file, allow loading with warning
    if not expected_checksums or filename not in expected_checksums:
        logger.warning(f"No checksum available for {filename} - loading without verification")
        return True

    # Verify checksum
    actual_checksum = _compute_file_checksum(filepath)
    expected_checksum = expected_checksums[filename]

    if actual_checksum != expected_checksum:
        logger.error(
            f"SECURITY: Checksum mismatch for {filename}! "
            f"Expected: {expected_checksum[:16]}..., Got: {actual_checksum[:16]}... "
            "File may have been tampered with. Refusing to load."
        )
        return False

    logger.info(f"Checksum verified for {filename}")
    return True


def load_model() -> bool:
    """
    Load V55 model and calibrator from disk.

    Should be called once at application startup.
    Verifies file integrity via SHA256 checksums before loading pickle files.

    Returns:
        True if loaded successfully, False otherwise
    """
    global _model, _calibrator, _cohort_stats

    try:
        # Load expected checksums for integrity verification
        expected_checksums = _load_expected_checksums()
        if expected_checksums:
            logger.info(f"Loaded {len(expected_checksums)} checksums for verification")

        # Load CatBoost model (binary format, not pickle - lower risk)
        model_path = MODEL_DIR / "model.cbm"
        if not model_path.exists():
            logger.error(f"Model file not found: {model_path}")
            return False

        # Verify model file integrity
        if not _verify_file_integrity(model_path, expected_checksums):
            return False

        _model = CatBoostClassifier()
        _model.load_model(str(model_path))
        logger.info(f"Loaded CatBoost model from {model_path}")
        logger.info(f"Model has {len(_model.feature_names_)} features")

        # Load Platt calibrator (pickle - verify integrity first)
        calibrator_path = MODEL_DIR / "platt_calibrator.pkl"
        if calibrator_path.exists():
            if not _verify_file_integrity(calibrator_path, expected_checksums):
                logger.error("Platt calibrator failed integrity check - skipping")
                _calibrator = None
            else:
                with open(calibrator_path, 'rb') as f:
                    _calibrator = pickle.load(f)
                logger.info("Loaded Platt calibrator")
        else:
            logger.warning("Platt calibrator not found - using raw probabilities")
            _calibrator = None

        # Load cohort stats (pickle - verify integrity first)
        cohort_path = MODEL_DIR / "cohort_stats.pkl"
        if cohort_path.exists():
            if not _verify_file_integrity(cohort_path, expected_checksums):
                logger.error("Cohort stats failed integrity check - skipping")
                _cohort_stats = None
            else:
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

    # Clamp raw probability to avoid log(0) or log(1) issues (P0-3 fix)
    # Using 1e-8 instead of 1e-10 for extra safety margin in log-odds calculation
    raw_prob = float(np.clip(raw_prob, 1e-8, 1 - 1e-8))

    # Apply Platt calibration if available
    if _calibrator is not None:
        try:
            # Platt calibrator expects log-odds transformed input
            log_odds = np.log(raw_prob / (1 - raw_prob))
            # Guard against NaN/Inf from calibrator
            if not np.isfinite(log_odds):
                logger.warning(f"Non-finite log-odds: {log_odds}, using raw probability")
                calibrated_prob = raw_prob
            else:
                calibrated_prob = _calibrator.predict_proba([[log_odds]])[0][1]
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
