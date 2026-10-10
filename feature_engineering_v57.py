"""Feature Engineering for the V57 CatBoost model (Stage-1, 2026-06-12).

v57 serving features = v55 serving features computed on a WINDOW-CAPPED
history, with the Stage-1 decision table applied on top:

1. The DVSA history is capped at ``model_bundle.WINDOW_START`` (2019-01-01)
   BEFORE feature engineering — the training matrices are built from the
   same window, so "zero prior X" means the same thing on both paths.
   Full-depth serving returns with v58 after the results-lake re-ingest.
2. The RC-3 drop-set (features v55 served as constants) is never emitted.
3. The 35 observed-history renames are applied as a single pass over the
   authoritative ``model_bundle.OBSERVED_RENAMES`` map.
4. The five coverage features are appended so the model can distinguish
   clean observed history from unobservable history.

Delegating to ``feature_engineering_v55.engineer_features`` (rather than
forking its 600 lines) keeps v55's serving semantics — including the GF-17
Phase A1 ``days_since_pass_ratio`` fix — by construction.
"""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from dvsa_client import VehicleHistory
from feature_engineering_v55 import engineer_features as _engineer_features_v55
from model_bundle import FIRST_MOT_AGE_YEARS, OBSERVED_RENAMES, RC3_DROPPED_FEATURES
from model_contract_v57 import (
    COVERAGE_FEATURE_ORDER,
    FEATURE_DEFAULTS,
    FEATURE_NAMES,
    HISTORY_WINDOW_START,
)

_WINDOW_START_DT = datetime(
    HISTORY_WINDOW_START.year, HISTORY_WINDOW_START.month, HISTORY_WINDOW_START.day
)


def _first_mot_due(history: VehicleHistory) -> Optional[datetime]:
    """UK first MOT is due FIRST_MOT_AGE_YEARS after first use.

    Uses registration_date (closest available proxy for first use), falling
    back to manufacture_date; None when neither is known.
    """
    first_use = history.registration_date or history.manufacture_date
    if first_use is None:
        return None
    # +3 calendar years, robust to Feb 29
    try:
        return first_use.replace(year=first_use.year + FIRST_MOT_AGE_YEARS)
    except ValueError:
        return first_use + timedelta(days=365 * FIRST_MOT_AGE_YEARS + 1)


def engineer_features(
    history: VehicleHistory,
    postcode: str,
    prediction_date: Optional[datetime] = None,
    cohort_stats: Optional[Dict[str, Any]] = None,
    model_hierarchical: Optional[Any] = None,
    model_age_hierarchical: Optional[Dict[str, Any]] = None,
    segment_hierarchical: Optional[Any] = None,
) -> Dict[str, Any]:
    """Engineer the 105 v57 features (plus auxiliary keys) from DVSA history."""
    if prediction_date is None:
        prediction_date = datetime.now()

    in_window_tests = [
        t for t in history.mot_tests if t.test_date >= _WINDOW_START_DT
    ]
    capped_history = replace(history, mot_tests=in_window_tests)

    features = _engineer_features_v55(
        capped_history,
        postcode,
        prediction_date=prediction_date,
        cohort_stats=cohort_stats,
        model_hierarchical=model_hierarchical,
        model_age_hierarchical=model_age_hierarchical,
        segment_hierarchical=segment_hierarchical,
    )

    # 2. RC-3 drop-set: never emitted by v57.
    for dropped in RC3_DROPPED_FEATURES:
        features.pop(dropped, None)

    # 3. Observed-history renames (authoritative map, single pass).
    for old_name, new_name in OBSERVED_RENAMES.items():
        if old_name in features:
            features[new_name] = features.pop(old_name)

    # 4. Coverage features (model_bundle.COVERAGE_FEATURES definitions).
    features["window_days_available"] = max(
        0, (prediction_date - _WINDOW_START_DT).days
    )

    if in_window_tests:
        first_observed = min(t.test_date for t in in_window_tests)
        features["history_years_observed"] = (
            (prediction_date - first_observed).days / 365.25
        )
        features["has_prior_test_observed"] = 1
    else:
        features["history_years_observed"] = 0.0
        features["has_prior_test_observed"] = 0

    first_mot_due = _first_mot_due(history)
    features["has_left_truncated_history"] = (
        1 if (first_mot_due is not None and first_mot_due < _WINDOW_START_DT) else 0
    )
    features["first_observed_test_is_not_true_first"] = (
        1
        if features["has_prior_test_observed"] and features["has_left_truncated_history"]
        else 0
    )

    return features


def features_to_array(features: Dict[str, Any], validate: bool = True) -> List[Any]:
    """Feature dict -> list in exact v57 contract order.

    With ``validate=True`` (the serving default) a missing contract feature
    raises; with ``validate=False`` missing keys take the contract's
    canonical per-feature defaults.
    """
    if validate:
        missing = [name for name in FEATURE_NAMES if name not in features]
        if missing:
            raise ValueError(
                f"Missing {len(missing)} required features: {missing[:5]}"
                + (f"... and {len(missing) - 5} more" if len(missing) > 5 else "")
            )

    return [features.get(name, FEATURE_DEFAULTS[name]) for name in FEATURE_NAMES]


def get_feature_names() -> List[str]:
    return FEATURE_NAMES.copy()


__all__ = [
    "engineer_features",
    "features_to_array",
    "get_feature_names",
    "COVERAGE_FEATURE_ORDER",
    "HISTORY_WINDOW_START",
]
