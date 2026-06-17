"""v57 serving adapter — emits the v57 feature contract from a VehicleHistory.

Decision #4 ("one model via coverage features") realised at serve time. The v57
path is intentionally a thin layer over the v55 feature engineering so the two
lineages cannot drift on the shared features:

  1. cap the DVSA history to the v57 window (>= WINDOW_START), so the
     ``*_observed`` history features carry their window-bounded meaning;
  2. run the existing v55 ``engineer_features`` on the capped history;
  3. apply the v57 transform (drop RC-3, rename observed, add the 5 coverage
     features) via the SAME ``v57_features`` module the training matrix uses.

Unlike v55 serving, NO vocab shim is applied: v57 is trained on the serving
vocabulary directly, so the categorical values the model sees at train and serve
are identical (the v55 shim, and the dead gap_band/usage_band_hybrid/
dominant_mechanism vocabularies, are retired here).

Artifacts (cohort/EB/hierarchical pkls) are loaded exactly as in model_v55 and
passed through to engineer_features; until the v57 retrain re-fits them the
bundle reuses the v55 fitted lookups (see models/v57/feature_contract.json
artifact_dependencies).

This adapter is the serving half of the v57 scaffold; the model binary it will
score against is produced by ``train_catboost_v57.py`` and is not yet committed.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
from typing import Any, Dict, List, Optional

from dvsa_client import VehicleHistory
from feature_engineering_v55 import engineer_features
from v57_features import (
    V57_CATEGORICAL,
    V57_FEATURE_NAMES,
    apply_v57_row_transform,
    cap_tests_to_window,
    compute_coverage_features,
)

logger = logging.getLogger(__name__)


def engineer_features_v57(
    history: VehicleHistory,
    postcode: str,
    prediction_date: Optional[datetime] = None,
    *,
    cohort_stats: Optional[Dict[str, Any]] = None,
    model_hierarchical: Optional[Any] = None,
    model_age_hierarchical: Optional[Dict[str, Any]] = None,
    segment_hierarchical: Optional[Any] = None,
) -> Dict[str, Any]:
    """Engineer the v57 feature dict for one vehicle.

    Returns a dict whose keys are exactly ``V57_FEATURE_NAMES``.
    """
    if prediction_date is None:
        prediction_date = datetime.now()

    # 1. Window-cap the history so observed-history features match training.
    capped = cap_tests_to_window(history.mot_tests)
    capped_history = replace(history, mot_tests=capped)

    # 2. Reuse the v55 feature engineering on the capped history.
    v55_features = engineer_features(
        capped_history,
        postcode,
        prediction_date,
        cohort_stats=cohort_stats,
        model_hierarchical=model_hierarchical,
        model_age_hierarchical=model_age_hierarchical,
        segment_hierarchical=segment_hierarchical,
    )

    # 3. Coverage features from point-in-time dates.
    first_observed = min((t.test_date for t in capped), default=None)
    first_use = history.registration_date or history.manufacture_date
    coverage = compute_coverage_features(prediction_date, first_use, first_observed)

    # 4. Apply the shared v57 transform (drop RC-3, rename observed, + coverage).
    return apply_v57_row_transform(v55_features, coverage)


def features_to_array_v57(features: Dict[str, Any], *, validate: bool = True) -> List[Any]:
    """Order the v57 feature dict to the contract for model scoring."""
    if validate:
        missing = [n for n in V57_FEATURE_NAMES if n not in features]
        if missing:
            raise ValueError(
                f"Missing {len(missing)} v57 features: {missing[:5]}"
                + (f"... and {len(missing) - 5} more" if len(missing) > 5 else "")
            )
    return [features.get(n, 0) for n in V57_FEATURE_NAMES]


def get_feature_names_v57() -> List[str]:
    return list(V57_FEATURE_NAMES)


def get_categorical_features_v57() -> List[str]:
    return list(V57_CATEGORICAL)
