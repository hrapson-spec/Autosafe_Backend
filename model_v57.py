"""V57 model serving — self-describing bundle loader + prediction.

Loads ONLY the versioned bundle in ``models/v57/`` (model.cbm, pickle-free
calibrator.json, feature_contract.json, metrics.json, feature_artifacts/)
and refuses to serve if any piece is missing or if the contract disagrees
with either the code (``model_contract_v57``) or the trained model's own
feature names. No vocab shim: the v57 matrix was trained on serving
vocabularies (Stage-1 directive, 2026-06-12).
"""

import json
import logging
import math
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import numpy as np

import model_contract_v57
from dvsa_client import VehicleHistory
from feature_engineering_v57 import engineer_features, features_to_array
from model_bundle import load_contract

logger = logging.getLogger(__name__)

BUNDLE_DIR = Path(__file__).parent / "models" / "v57"
REQUIRED_FILES = ("model.cbm", "calibrator.json", "feature_contract.json",
                  "metrics.json")

_model = None
_calibrator: Optional[Dict[str, float]] = None
_contract = None
_cohort_stats: Optional[Dict[str, Any]] = None
_model_hier = None
_segment_hier = None
_model_age: Optional[Dict[str, Any]] = None


def _decode_tuple_keys(d: Dict[str, Any]) -> Dict[tuple, Any]:
    return {tuple(k.split("|")): v for k, v in d.items()}


def load_model() -> bool:
    """Load + validate the v57 bundle. Returns False (and logs why) on any
    missing or inconsistent piece — this model must never half-load."""
    global _model, _calibrator, _contract, _cohort_stats
    global _model_hier, _segment_hier, _model_age

    missing = [n for n in REQUIRED_FILES if not (BUNDLE_DIR / n).exists()]
    if missing:
        logger.error(f"v57 bundle incomplete, missing: {missing}")
        return False

    try:
        contract = load_contract(BUNDLE_DIR / "feature_contract.json")
        if contract.feature_names != model_contract_v57.FEATURE_NAMES:
            raise ValueError(
                "bundle contract disagrees with model_contract_v57 — "
                "code and artifact are from different lineages")

        from catboost import CatBoostClassifier
        model = CatBoostClassifier()
        model.load_model(str(BUNDLE_DIR / "model.cbm"))
        model_names = list(getattr(model, "feature_names_", []) or [])
        if model_names and model_names != contract.feature_names:
            raise ValueError(
                f"model.cbm feature names diverge from the contract "
                f"(first mismatch: "
                f"{next(((i, a, b) for i, (a, b) in enumerate(zip(model_names, contract.feature_names)) if a != b), None)})")

        calib = json.loads((BUNDLE_DIR / "calibrator.json").read_text())
        if calib.get("type") != "platt_sigmoid" or "A" not in calib or "B" not in calib:
            raise ValueError(f"unexpected calibrator format: {calib.get('type')!r}")

        cohort_stats = None
        model_hier = None
        segment_hier = None
        model_age = None
        art_dir = BUNDLE_DIR / "feature_artifacts"
        if (art_dir / "cohort_stats.json").exists():
            raw = json.loads((art_dir / "cohort_stats.json").read_text())
            cohort_stats = {
                "cohort_mileage": _decode_tuple_keys(raw["cohort_mileage"]),
                "cohort_advisory": _decode_tuple_keys(raw["cohort_advisory"]),
                "global_mileage_avg": raw["global_mileage_avg"],
                "global_advisory_avg": raw["global_advisory_avg"],
            }
        if (art_dir / "hierarchical_rates.json").exists():
            raw = json.loads((art_dir / "hierarchical_rates.json").read_text())
            model_hier = SimpleNamespace(
                model_rates=raw.get("model_rates", {}),
                make_rates=raw.get("make_rates", {}),
                global_fail_rate=raw.get("global_fail_rate", 0.28),
            )
            segment_hier = SimpleNamespace(
                segment_rates=_decode_tuple_keys(raw.get("segment_rates", {})),
                make_rates=raw.get("make_rates", {}),
                global_fail_rate=raw.get("global_fail_rate", 0.28),
            )
        if (art_dir / "model_age_rates.json").exists():
            raw = json.loads((art_dir / "model_age_rates.json").read_text())
            model_age = {
                "model_age_rates": _decode_tuple_keys(raw.get("model_age_rates", {})),
                "make_age_rates": _decode_tuple_keys(raw.get("make_age_rates", {})),
                "global_fail_rate": raw.get("global_fail_rate", 0.28),
            }

        for dep in contract.artifact_dependencies:
            if not (BUNDLE_DIR / dep).exists():
                raise ValueError(f"contract artifact dependency missing: {dep}")

    except Exception as e:  # noqa: BLE001 — refuse to serve on ANY defect
        logger.error(f"v57 bundle failed validation: {e}")
        return False

    _model, _contract = model, contract
    _calibrator = {"A": float(calib["A"]), "B": float(calib["B"])}
    _cohort_stats, _model_hier = cohort_stats, model_hier
    _segment_hier, _model_age = segment_hier, model_age
    logger.info(
        f"v57 bundle loaded: {len(contract.feature_names)} features, "
        f"window {contract.history_window_start}, calibrator A={calib['A']:.4f}")
    return True


def is_model_loaded() -> bool:
    return _model is not None


def engineer_features_with_stats(
    history: VehicleHistory,
    postcode: str,
    prediction_date: Optional[datetime] = None,
) -> Dict[str, Any]:
    return engineer_features(
        history,
        postcode,
        prediction_date=prediction_date,
        cohort_stats=_cohort_stats,
        model_hierarchical=_model_hier,
        model_age_hierarchical=_model_age,
        segment_hierarchical=_segment_hier,
    )


def predict_risk(features: Dict[str, Any]) -> Dict[str, Any]:
    """Calibrated failure-risk prediction from a v57 feature dict."""
    if _model is None or _calibrator is None:
        raise RuntimeError("v57 model not loaded — call load_model() first")

    array = features_to_array(features, validate=True)
    raw_prob = float(_model.predict_proba([array])[0][1])
    raw_prob = float(np.clip(raw_prob, 1e-8, 1 - 1e-8))

    # pickle-free Platt: p_cal = sigmoid(A * p_raw + B), fitted on raw probs
    z = _calibrator["A"] * raw_prob + _calibrator["B"]
    calibrated = 1.0 / (1.0 + math.exp(-z))
    calibrated = float(np.clip(calibrated, 0.0, 1.0))

    return {
        "failure_risk": round(calibrated, 4),
        "raw_probability": round(raw_prob, 4),
        "confidence_level": _calculate_confidence(features),
        "risk_components": _estimate_component_risks(features, calibrated),
        "model_version": "v57",
        "history_window_start": str(_contract.history_window_start),
        "history_left_truncated": int(features.get("has_left_truncated_history", 0)),
    }


def _calculate_confidence(features: Dict[str, Any]) -> str:
    """v55's completeness heuristic on the renamed v57 feature names."""
    score = 0
    if features.get("has_prev_mileage", 0) == 1:
        score += 2
    if features.get("n_prior_tests_observed", 0) > 0:
        score += 2
    if features.get("n_prior_tests_observed", 0) >= 3:
        score += 1
    if features.get("days_since_last_test", 999) and features.get("days_since_last_test", 999) < 400:
        score += 1
    # bounded observation window: cap confidence when true history is
    # known to extend beyond what the model can observe
    if features.get("has_left_truncated_history", 0) == 1 and score >= 5:
        score = 4
    if score >= 5:
        return "High"
    if score >= 3:
        return "Medium"
    return "Low"


def _estimate_component_risks(
    features: Dict[str, Any], overall_risk: float
) -> Dict[str, float]:
    """Indicative component risks (v55 heuristic, v57 feature names and
    the corrected mech_decay_* key spellings)."""
    base_risks = {
        "brakes": 0.05, "suspension": 0.04, "tyres": 0.03, "steering": 0.02,
        "visibility": 0.02, "lamps": 0.03, "body": 0.02,
    }
    advisory_keys = {
        "brakes": "prev_adv_brakes", "suspension": "prev_adv_suspension",
        "steering": "prev_adv_steering", "tyres": "prev_adv_tyres",
    }
    decay_keys = {
        "brakes": "mech_decay_brake", "suspension": "mech_decay_suspension",
        "steering": "mech_decay_steering",
    }
    scale = overall_risk / 0.28  # vs UK average failure rate
    out = {}
    for component, base in base_risks.items():
        multiplier = 1.0
        adv = features.get(advisory_keys.get(component, ""), 0) or 0
        if adv > 0:
            multiplier += 0.5 * min(adv, 3)
        decay = features.get(decay_keys.get(component, ""), 0) or 0
        if decay > 0:
            multiplier += 0.5 * min(decay, 1.0)
        out[component] = round(float(min(0.95, base * multiplier * scale)), 4)
    return out


def get_model_info() -> Dict[str, Any]:
    if _model is None:
        return {"loaded": False}
    metrics = json.loads((BUNDLE_DIR / "metrics.json").read_text())
    return {
        "loaded": True,
        "version": "v57",
        "n_features": len(_contract.feature_names),
        "history_window_start": str(_contract.history_window_start),
        "oot_roc_auc": metrics.get("roc_auc"),
        "calibrator": "platt_sigmoid (pickle-free)",
    }


def get_feature_names() -> List[str]:
    return list(model_contract_v57.FEATURE_NAMES)
