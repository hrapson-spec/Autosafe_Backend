"""
prediction_service -- the typed V55 prediction boundary for v2 reports.
=======================================================================

Turns resolved vehicle identity + DVSA history into a vehicle_prediction
Assessment (report_service.Assessment), calling model_v55.predict_risk
exactly once. Operational failures are raised as subclasses of
PredictionUnavailable -- the ONLY exception type the report route may
catch before falling back to the comparison assessment. Anything else is
a contract/programming error and must propagate.

The kill-switch (PREDICTIONS_ENABLED) is read from the environment at
call time rather than imported from main.py: main imports report_routes,
which imports this module, so importing main here would be circular.
main.py keeps its own import-time copy for the legacy /api/risk/v55
route and /health.
"""
import logging
import math
import os
from datetime import datetime
from typing import Any, Dict, Optional

import model_v55
import repair_costs
from report_contract import (
    ComponentRiskItem,
    ConfidenceLevel,
    MatchScope,
    PredictionSource,
    ReportComponents,
    ReportEvidence,
    ReportRepairEstimate,
    ReportRisk,
    ResultKind,
)
from report_service import (
    Assessment,
    _COMPONENT_FIELDS,
    _REPAIR_COST_KEY_MAP,
    build_vehicle_context,
)

logger = logging.getLogger(__name__)


class PredictionUnavailable(Exception):
    """Base for every expected operational prediction failure. The report
    route catches exactly this type and falls back to the comparison
    assessment; subclasses exist so the fallback reason can be logged."""


class PredictionDisabled(PredictionUnavailable):
    """PREDICTIONS_ENABLED kill-switch is off."""


class ModelUnavailable(PredictionUnavailable):
    """The V55 model artifacts are not loaded."""


class FeatureEngineeringFailed(PredictionUnavailable):
    """Feature engineering raised on this vehicle's history."""


class InferenceFailed(PredictionUnavailable):
    """Model inference raised, or returned an unusable overall result."""


# component key -> calculate_expected_repair_cost() input key, joined from
# report_service's two tables on their shared normalized risk_* field so
# there is a single source of truth for component identity.
_RISK_FIELD_TO_REPAIR_KEY = {field: key for key, field in _REPAIR_COST_KEY_MAP}
COMPONENT_REPAIR_KEYS = {
    key: _RISK_FIELD_TO_REPAIR_KEY[field] for key, _label, field in _COMPONENT_FIELDS
}


def predictions_enabled() -> bool:
    """Call-time read of the PREDICTIONS_ENABLED kill-switch (same parsing
    as main.py's import-time copy)."""
    return os.environ.get("PREDICTIONS_ENABLED", "true").lower() == "true"


def _valid_probability(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0.0 <= value <= 1.0
    )


def _build_components(risk_components: Any) -> ReportComponents:
    """The seven-component vector, mapped onto the shared component keys and
    labels -- or honestly unavailable if any entry is missing or invalid.
    Suppression never discards the overall prediction."""
    if not isinstance(risk_components, dict):
        return ReportComponents(available=False)
    items = []
    for key, label, _field in _COMPONENT_FIELDS:
        value = risk_components.get(key)
        if not _valid_probability(value):
            return ReportComponents(available=False)
        items.append(ComponentRiskItem(key=key, label=label, risk=float(value)))
    return ReportComponents(available=True, items=items)


def _build_repair_estimate(
    failure_risk: float, components: ReportComponents
) -> Optional[ReportRepairEstimate]:
    if not components.available:
        return None
    risk_data: Dict[str, float] = {"Failure_Risk": failure_risk}
    for item in components.items:
        risk_data[COMPONENT_REPAIR_KEYS[item.key]] = item.risk
    cost = repair_costs.calculate_expected_repair_cost(risk_data)
    if not cost:
        return None
    return ReportRepairEstimate(
        expected=cost["cost_mid"],
        range_low=cost["cost_min"],
        range_high=cost["cost_max"],
    )


def build_v55_assessment(
    identity: Dict[str, Any],
    history: Any,
    postcode: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Assessment:
    """A vehicle_prediction Assessment for this vehicle, or a typed
    PredictionUnavailable subclass.

    Calls model_v55.predict_risk exactly once. Does not log (the route
    owns privacy-safe attempt logging) and never includes registration,
    postcode, or feature values in exception messages.
    """
    if not predictions_enabled():
        raise PredictionDisabled("predictions are disabled by kill-switch")
    if not model_v55.is_model_loaded():
        raise ModelUnavailable("V55 model is not loaded")

    try:
        # A missing postcode must not forfeit the prediction: empty input
        # resolves to the default corrosion index downstream.
        features = model_v55.engineer_features_with_stats(
            history, (postcode or "").strip(), prediction_date=now
        )
    except Exception as exc:
        raise FeatureEngineeringFailed(
            f"feature engineering raised {type(exc).__name__}"
        ) from exc

    try:
        prediction = model_v55.predict_risk(features)
    except Exception as exc:
        raise InferenceFailed(f"inference raised {type(exc).__name__}") from exc

    failure_risk = prediction.get("failure_risk")
    if not _valid_probability(failure_risk):
        raise InferenceFailed("model returned an invalid overall probability")

    try:
        confidence = ConfidenceLevel(prediction.get("confidence_level"))
    except ValueError as exc:
        raise InferenceFailed("model returned an unknown confidence level") from exc

    components = _build_components(prediction.get("risk_components"))
    repair_estimate = _build_repair_estimate(float(failure_risk), components)

    vehicle, mot, mileage = build_vehicle_context(identity, history)

    return Assessment(
        vehicle=vehicle,
        mot=mot,
        mileage=mileage,
        evidence=ReportEvidence(
            match_scope=MatchScope.MODEL_PREDICTION,
            age_band=None,
            mileage_band=None,
            total_tests=None,
            total_failures=None,
        ),
        risk=ReportRisk(failure_risk=float(failure_risk), confidence=confidence),
        components=components,
        repair_estimate=repair_estimate,
        prediction_source=PredictionSource.MODEL_V55,
        note=None,
        result_kind=ResultKind.VEHICLE_PREDICTION,
    )
