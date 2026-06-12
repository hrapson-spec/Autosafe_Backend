"""v57 model bundle: feature contract loader + the Stage-1 decision table.

Single source of truth for the v57 lineage decisions (2026-06-12 directive):

- RC-3 drop-set: four features served as constants by v55 are removed.
- Window-bounded observed-history contract: history accumulation/recency
  features are renamed ``*_observed`` and five coverage features tell the
  model whether "zero" means clean observed history or unavailable history.
- Serving applies the SAME history window cap as training (full-depth
  serving returns with v58 after the results-lake re-ingest).

Both the trainer (matrix/contract emission) and serving (validation at load,
feature ordering at predict) import THIS module — no hand-maintained feature
lists. See models/v57/README.md and feature_contract.schema.json.
"""

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# History window (substrate coverage: repaired 2019+ item/history lakes).
# Training matrices are built from histories >= WINDOW_START; the v57 serving
# path MUST apply the same cap to DVSA API histories before feature
# engineering, even though the API returns 2005+.
# ---------------------------------------------------------------------------
WINDOW_START = date(2019, 1, 1)

# UK first MOT is due at vehicle age 3 — used by the truncation flags.
FIRST_MOT_AGE_YEARS = 3

# ---------------------------------------------------------------------------
# RC-3 drop-set (GF17_DEFECT_LEDGER.md): trained live, served as constants.
# Dropped from the v57 contract rather than reconstructed.
# ---------------------------------------------------------------------------
RC3_DROPPED_FEATURES = [
    "station_fail_rate_smoothed",
    "station_x_prev_outcome_fail_rate",
    "station_strictness_bias",
    "suspension_risk_profile",
]

# ---------------------------------------------------------------------------
# Observed-history renames: features that accumulate counts/recencies over
# the vehicle's OBSERVED test history. Under a bounded window, their zeros
# are ambiguous without the coverage features below — the rename makes the
# window-bounded semantics explicit at the name level.
# (Single-previous-test features such as days_since_last_test keep their
# names; their window semantics are documented per-feature in the contract.)
# ---------------------------------------------------------------------------
OBSERVED_RENAMES: Dict[str, str] = {
    # headline counts (directive examples)
    "n_prior_tests": "n_prior_tests_observed",
    "n_prior_fails": "n_prior_fails_observed",
    "prev_count_advisory": "prior_advisory_count_observed",
    "has_ever_failed_brakes": "has_observed_failure_brakes",
    "has_ever_failed_tyres": "has_observed_failure_tyres",
    "has_ever_failed_suspension": "has_observed_failure_suspension",
    # windowed fail counts (still bounded by the observation window)
    "fails_last_365d": "fails_last_365d_observed",
    "fails_last_730d": "fails_last_730d_observed",
    # honest estimator name (ledger RC-4: raw ratio, not smoothed) + window
    "prior_fail_rate_smoothed": "prior_fail_rate_observed",
    # advisory recency/streak family
    "has_prior_advisory_brakes": "has_prior_advisory_brakes_observed",
    "has_prior_advisory_tyres": "has_prior_advisory_tyres_observed",
    "has_prior_advisory_suspension": "has_prior_advisory_suspension_observed",
    "tests_since_last_advisory_brakes": "tests_since_last_advisory_brakes_observed",
    "tests_since_last_advisory_tyres": "tests_since_last_advisory_tyres_observed",
    "tests_since_last_advisory_suspension": "tests_since_last_advisory_suspension_observed",
    "advisory_in_last_1_brakes": "advisory_in_last_1_brakes_observed",
    "advisory_in_last_1_tyres": "advisory_in_last_1_tyres_observed",
    "advisory_in_last_1_suspension": "advisory_in_last_1_suspension_observed",
    "advisory_in_last_2_brakes": "advisory_in_last_2_brakes_observed",
    "advisory_in_last_2_tyres": "advisory_in_last_2_tyres_observed",
    "advisory_in_last_2_suspension": "advisory_in_last_2_suspension_observed",
    "advisory_streak_len_brakes": "advisory_streak_len_brakes_observed",
    "advisory_streak_len_tyres": "advisory_streak_len_tyres_observed",
    "advisory_streak_len_suspension": "advisory_streak_len_suspension_observed",
    "miles_since_last_advisory_tyres": "miles_since_last_advisory_tyres_observed",
    "miles_since_last_advisory_suspension": "miles_since_last_advisory_suspension_observed",
    # failure recency/streak family
    "has_prior_failure_brakes": "has_prior_failure_brakes_observed",
    "has_prior_failure_tyres": "has_prior_failure_tyres_observed",
    "has_prior_failure_suspension": "has_prior_failure_suspension_observed",
    "failure_streak_len_brakes": "failure_streak_len_brakes_observed",
    "failure_streak_len_tyres": "failure_streak_len_tyres_observed",
    "failure_streak_len_suspension": "failure_streak_len_suspension_observed",
    "tests_since_last_failure_brakes": "tests_since_last_failure_brakes_observed",
    "tests_since_last_failure_tyres": "tests_since_last_failure_tyres_observed",
    "tests_since_last_failure_suspension": "tests_since_last_failure_suspension_observed",
}

# ---------------------------------------------------------------------------
# Coverage features (directive): the model must be able to distinguish
# "clean observed history" from "history unavailable before the window".
# Computed identically by the matrix builder and the v57 serving path.
# ---------------------------------------------------------------------------
COVERAGE_FEATURES: Dict[str, str] = {
    "window_days_available": (
        "max(0, (target_date - WINDOW_START).days) — how much observation "
        "window could exist for this prediction, regardless of the vehicle"
    ),
    "history_years_observed": (
        "(target_date - first_observed_test_date).days / 365.25, or 0.0 when "
        "no prior test is observed"
    ),
    "has_prior_test_observed": (
        "1 if at least one prior test is observed inside the window, else 0"
    ),
    "has_left_truncated_history": (
        "1 if the vehicle was MOT-liable before WINDOW_START "
        "(first_use_date + FIRST_MOT_AGE_YEARS < WINDOW_START), i.e. true "
        "history plausibly exists that the window cannot observe"
    ),
    "first_observed_test_is_not_true_first": (
        "1 if a prior test is observed AND has_left_truncated_history — the "
        "earliest observed test cannot be the vehicle's true first MOT"
    ),
}


@dataclass
class Contract:
    """Loaded, validated v57 feature contract."""

    version: str
    feature_names: List[str]
    dtypes: Dict[str, str]
    categorical_features: List[str]
    defaults: Dict[str, Any]
    history_window_start: str
    parity_tolerance: float
    source_columns: Dict[str, str] = field(default_factory=dict)
    artifact_dependencies: List[str] = field(default_factory=list)
    point_in_time_rule: str = ""

    @property
    def categorical_indices(self) -> List[int]:
        return [self.feature_names.index(n) for n in self.categorical_features]

    def validate_feature_columns(self, columns: List[str]) -> None:
        """Exact name+order equality — the schema gate. Raises on any drift."""
        if list(columns) != self.feature_names:
            missing = [c for c in self.feature_names if c not in columns]
            extra = [c for c in columns if c not in self.feature_names]
            first_mismatch = next(
                (
                    (i, a, b)
                    for i, (a, b) in enumerate(zip(columns, self.feature_names))
                    if a != b
                ),
                None,
            )
            raise ValueError(
                "Feature columns violate the v57 contract: "
                f"missing={missing[:5]} extra={extra[:5]} "
                f"first_order_mismatch={first_mismatch}"
            )

    def validate_decision_table(self) -> None:
        """The contract must embody the Stage-1 decisions."""
        for f in RC3_DROPPED_FEATURES:
            if f in self.feature_names:
                raise ValueError(f"RC-3 dropped feature present in contract: {f}")
        for old in OBSERVED_RENAMES:
            if old in self.feature_names:
                raise ValueError(f"Pre-rename feature name in contract: {old}")
        for cov in COVERAGE_FEATURES:
            if cov not in self.feature_names:
                raise ValueError(f"Coverage feature missing from contract: {cov}")


def load_contract(path: Path) -> Contract:
    with open(path) as f:
        spec = json.load(f)
    if spec.get("contract_type") != "autosafe_feature_contract":
        raise ValueError(f"Not a feature contract: {spec.get('contract_type')!r}")
    features = spec["features"]  # ordered list of per-feature dicts
    names = [f["name"] for f in features]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate feature names in contract")
    contract = Contract(
        version=spec["version"],
        feature_names=names,
        dtypes={f["name"]: f["dtype"] for f in features},
        categorical_features=[f["name"] for f in features if f["dtype"] == "categorical"],
        defaults={f["name"]: f.get("default") for f in features},
        history_window_start=spec["history_window"]["start"],
        parity_tolerance=spec["parity"]["tolerance"],
        source_columns={f["name"]: f.get("source", "") for f in features},
        artifact_dependencies=spec.get("artifact_dependencies", []),
        point_in_time_rule=spec.get("point_in_time_rule", ""),
    )
    contract.validate_decision_table()
    return contract


def emit_contract(
    *,
    version: str,
    features: List[Dict[str, Any]],
    artifact_dependencies: Optional[List[str]] = None,
    point_in_time_rule: str = (
        "Every feature uses only data available strictly before the target "
        "test date, and only tests dated >= history_window.start"
    ),
    parity_tolerance: float = 1e-9,
    out_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build (and optionally write) a contract dict from per-feature rows.

    Used by the matrix builder / trainer so the contract is always EMITTED
    from the artifact that exists, never hand-maintained.
    """
    spec = {
        "contract_type": "autosafe_feature_contract",
        "version": version,
        "history_window": {
            "start": WINDOW_START.isoformat(),
            "rule": (
                "Training matrices and the serving path both discard MOT "
                "tests before this date; full-depth serving is deferred to "
                "v58 after the results-lake re-ingest."
            ),
        },
        "parity": {"tolerance": parity_tolerance},
        "point_in_time_rule": point_in_time_rule,
        "artifact_dependencies": artifact_dependencies or [],
        "decision_provenance": {
            "rc3_dropped": RC3_DROPPED_FEATURES,
            "observed_renames": OBSERVED_RENAMES,
            "coverage_features": sorted(COVERAGE_FEATURES),
            "directive": "v57 Stage-1, 2026-06-12",
        },
        "features": features,
    }
    if out_path is not None:
        with open(out_path, "w") as f:
            json.dump(spec, f, indent=2)
    return spec
