"""v57 feature contract — derived, never hand-maintained.

Derives the ordered v57 feature list from the served v55 ground truth
(`feature_engineering_v55.FEATURE_NAMES`) plus the Stage-1 decision table
(`model_bundle`: RC-3 drops, observed-history renames, coverage features),
and carries the per-feature canonical defaults (ledger RC-1: one default per
feature, serving semantics). Both the v57 trainer and the v57 serving path
import THIS module; the on-disk `feature_contract.json` is emitted from it
via `model_bundle.emit_contract` at training time.

Width: 104 served − 4 RC-3 drops + 5 coverage = 105.
"""

from typing import Any, Dict, List, Optional

from feature_engineering_v55 import (
    CATEGORICAL_INDICES as _V55_CATEGORICAL_INDICES,
    FEATURE_NAMES as _V55_FEATURE_NAMES,
)
from model_bundle import (
    COVERAGE_FEATURES,
    OBSERVED_RENAMES,
    RC3_DROPPED_FEATURES,
    WINDOW_START,
    emit_contract,
)

# Window: single source is model_bundle.WINDOW_START (2019-01-01). Serving
# applies the same cap to DVSA API histories as training does to the lake.
HISTORY_WINDOW_START = WINDOW_START

# Last-resort fallbacks for features without a canonical override below.
CATEGORICAL_DEFAULT = "unknown"
NUMERIC_DEFAULT = 0.0

# Noisy ablation families: trainer-only in v55 (never served); guarded out.
NEGLECT_GUARDS = frozenset({
    "neglect_score_brakes",
    "neglect_score_tyres",
    "neglect_score_suspension",
})

EXCLUDED_V57_FEATURES = frozenset(RC3_DROPPED_FEATURES) | NEGLECT_GUARDS

# README order == insertion order of the model_bundle.COVERAGE_FEATURES dict.
COVERAGE_FEATURE_ORDER = tuple(COVERAGE_FEATURES)

FEATURE_NAMES: List[str] = [
    OBSERVED_RENAMES.get(name, name)
    for name in _V55_FEATURE_NAMES
    if name not in RC3_DROPPED_FEATURES
] + list(COVERAGE_FEATURE_ORDER)

# The 10 v55 categoricals survive drops/renames untouched (all drops and
# renames are numeric history features) — derived, not hand-listed.
CATEGORICAL_FEATURES: List[str] = [
    OBSERVED_RENAMES.get(_V55_FEATURE_NAMES[i], _V55_FEATURE_NAMES[i])
    for i in _V55_CATEGORICAL_INDICES
    if _V55_FEATURE_NAMES[i] not in RC3_DROPPED_FEATURES
]

_INDEX: Dict[str, int] = {name: i for i, name in enumerate(FEATURE_NAMES)}


def feature_index(name: str) -> int:
    return _INDEX[name]


# ---------------------------------------------------------------------------
# Canonical per-feature defaults (ledger RC-1 closure: serving semantics).
# Categorical defaults MUST be levels the serving path can emit — CatBoost
# has no OOV bucket, so an out-of-vocabulary default would be a dead value.
# ---------------------------------------------------------------------------
_CANONICAL_DEFAULT_OVERRIDES: Dict[str, Any] = {
    # RC-1 ledger rows (serving's missing-input values)
    "test_mileage": 0.0,
    "days_since_last_test": 0.0,
    "days_late": 0.0,                  # serving: max(0, ·)
    "annualized_mileage_v2": 10000.0,  # serving missing-annualized basis
    "negligence_band": "clean",        # serving computes 'clean', never fills 'unknown'
    # eb_unified_prior: canonical default is the FITTED GLOBAL emitted by the
    # trainer into feature_contract.json; module placeholder documented.
    "eb_unified_prior": 0.25,
    # serving emissions when no in-window prior exists
    "prev_cycle_outcome_band": "first_test",
    "gap_band": "first_test",
    "advisory_trend": "unknown",
    "usage_band_hybrid": "average",    # missing annualized -> 10000 -> 'average'
    "dominant_mechanism": "NO_HISTORY",
    "mech_risk_driver": "CLEAN",
    "make": "UNKNOWN",
    # always computable from the prediction date; in-vocab anchors
    "test_month": 1,
    "day_of_week": 0,
}

FEATURE_DEFAULTS: Dict[str, Any] = {
    name: _CANONICAL_DEFAULT_OVERRIDES.get(
        name,
        CATEGORICAL_DEFAULT if name in CATEGORICAL_FEATURES else NUMERIC_DEFAULT,
    )
    for name in FEATURE_NAMES
}

# Features whose value is computed over (or references) the capped
# observation window — informative `window_bounded` flag in the contract.
_PREV_CYCLE_REFERENCED = frozenset({
    "days_since_last_test", "days_late", "days_since_pass_ratio",
    "prev_cycle_outcome_band", "gap_band", "advisory_trend",
    "prev_adv_brakes", "prev_adv_suspension", "prev_adv_steering",
    "prev_adv_tyres",
})

WINDOW_BOUNDED_FEATURES = (
    frozenset(OBSERVED_RENAMES.values())
    | frozenset(COVERAGE_FEATURE_ORDER)
    | _PREV_CYCLE_REFERENCED
)

_RENAME_TARGETS = frozenset(OBSERVED_RENAMES.values())
_PRECOMPUTED_PREFIXES = (
    "text_", "mech_decay_", "has_corrosion", "has_wear", "has_leak",
    "has_damage", "mechanism_count", "max_severity", "severity_escalation",
    "has_advisory_history",
)


def _source_for(name: str) -> str:
    if name in COVERAGE_FEATURE_ORDER:
        return f"coverage: {COVERAGE_FEATURES[name]}"
    if name in _RENAME_TARGETS:
        return ("window-bounded observed history: in-window recompute over "
                "cycle_first_with_history (train) / capped DVSA API history (serve)")
    if name in _PREV_CYCLE_REFERENCED:
        return "latest in-window prior cycle (train SQL / capped API history)"
    if name.startswith(_PRECOMPUTED_PREFIXES) or name == "dominant_mechanism":
        return "precomputed v52 text-mining / v49 mech-decay artifact join (train) / keyword FE (serve)"
    if name in ("model_age_fail_rate_eb", "make_age_fail_rate_eb",
                "eb_unified_prior", "make_fail_rate_smoothed",
                "segment_fail_rate_smoothed", "model_fail_rate_smoothed",
                "historic_negligence_ratio_smoothed", "negligence_band",
                "raw_behavioral_count"):
        return "fitted lookup artifact (models/v57/feature_artifacts)"
    return "trainer SQL / feature_engineering_v57"


def build_feature_rows(
    default_overrides: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Ordered per-feature rows for `model_bundle.emit_contract`.

    `default_overrides` lets the trainer replace placeholder defaults with
    fitted values (e.g. eb_unified_prior's fitted global) at emission time.
    """
    overrides = default_overrides or {}
    rows: List[Dict[str, Any]] = []
    for name in FEATURE_NAMES:
        rows.append({
            "name": name,
            "dtype": "categorical" if name in CATEGORICAL_FEATURES else "float",
            "default": overrides.get(name, FEATURE_DEFAULTS[name]),
            "source": _source_for(name),
            "prediction_time_available": True,
            "window_bounded": name in WINDOW_BOUNDED_FEATURES,
        })
    return rows


def contract_as_dict(
    *,
    default_overrides: Optional[Dict[str, Any]] = None,
    artifact_dependencies: Optional[List[str]] = None,
    out_path=None,
) -> Dict[str, Any]:
    """The v57 contract document (optionally written to disk by the trainer)."""
    return emit_contract(
        version="v57",
        features=build_feature_rows(default_overrides),
        artifact_dependencies=artifact_dependencies,
        out_path=out_path,
    )
