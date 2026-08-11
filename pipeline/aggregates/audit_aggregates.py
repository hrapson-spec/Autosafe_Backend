"""Aggregate audit gates -- adapted from audit_risk_model.py's tests, but
ENFORCED: every gate returns a CheckResult and the runner exits non-zero.
(The legacy audit logs FAIL and returns 0; printed-not-enforced gates are a
recorded defect class this program retires.)

Gates:
- schema: exactly the 13 artifact columns, exact order (build_db.py and
  upload_to_postgres.py both type by POSITION -- order is contract);
- boundaries: no hard zeros and nothing >= 1.0 in Failure_Risk/Risk_*
  (hard zeros imply impossibility; smoothing must prevent them);
- sparse smoothing: rows with <20 tests and 0 failures must still carry
  non-zero component risk mass (the legacy acceptance test, kept exactly);
- weighted Brier: sample-size-weighted (Failure_Risk vs observed)^2 below
  threshold -- shrinkage keeps high-volume cells near observed;
- reconciliation (decision D7 tripwire): dataset-wide failure rate within
  tolerance of an expected rate when one is supplied (run against the old
  artifact's 26.9139638817903% on an equivalent window BEFORE publishing
  any new totals);
- golden: optional regression vs a blessed previous frame.
"""
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from .build_aggregates import ARTIFACT_COLUMNS


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


RISK_COLUMNS = [c for c in ARTIFACT_COLUMNS if c.startswith("Risk_")]


def check_schema(frame: pd.DataFrame) -> CheckResult:
    ok = list(frame.columns) == ARTIFACT_COLUMNS
    return CheckResult(
        "schema", ok,
        "13 columns, exact artifact order" if ok
        else f"columns are {list(frame.columns)}; expected {ARTIFACT_COLUMNS}",
    )


def check_boundaries(frame: pd.DataFrame) -> CheckResult:
    cols = ["Failure_Risk"] + RISK_COLUMNS
    hard_zeros = int(sum((frame[c] == 0.0).sum() for c in cols))
    at_or_above_one = int(sum((frame[c] >= 1.0).sum() for c in cols))
    negatives = int(sum((frame[c] < 0.0).sum() for c in cols))
    ok = hard_zeros == 0 and at_or_above_one == 0 and negatives == 0
    return CheckResult(
        "boundaries", ok,
        f"hard_zeros={hard_zeros} >=1.0={at_or_above_one} negatives={negatives}",
    )


def check_sparse_smoothing(frame: pd.DataFrame) -> CheckResult:
    sparse = frame[(frame["Total_Tests"] > 0) & (frame["Total_Tests"] < 20)
                   & (frame["Total_Failures"] == 0)]
    if sparse.empty:
        return CheckResult("sparse_smoothing", True, "no sparse zero-failure rows to test")
    component_mass = sparse[RISK_COLUMNS].sum(axis=1)
    zeros = int((component_mass <= 0).sum())
    return CheckResult(
        "sparse_smoothing", zeros == 0,
        f"{len(sparse)} sparse zero-failure rows; {zeros} with zero component risk mass",
    )


def check_weighted_brier(frame: pd.DataFrame, max_brier: float = 0.01) -> CheckResult:
    observed = frame["Total_Failures"] / frame["Total_Tests"].clip(lower=1)
    weights = frame["Total_Tests"]
    brier = float((((frame["Failure_Risk"] - observed) ** 2) * weights).sum() / weights.sum())
    return CheckResult(
        "weighted_brier", brier < max_brier,
        f"weighted_brier={brier:.6f} (max {max_brier})",
    )


def check_reconciliation(frame: pd.DataFrame, expected_rate: Optional[float],
                         tolerance: float = 0.02) -> CheckResult:
    """D7 tripwire: |dataset rate - expected| <= tolerance (absolute). Run
    with the OLD artifact's exact rate on an equivalent window; a miss means
    the cycle/PRS semantics are wrong -- halt, do not publish totals."""
    rate = float(frame["Total_Failures"].sum() / frame["Total_Tests"].sum())
    if expected_rate is None:
        return CheckResult("reconciliation", True,
                           f"dataset_rate={rate:.6f} (no expected rate supplied; informational)")
    diff = abs(rate - expected_rate)
    return CheckResult(
        "reconciliation", diff <= tolerance,
        f"dataset_rate={rate:.6f} expected={expected_rate:.6f} "
        f"|diff|={diff:.6f} (tol {tolerance})",
    )


def check_golden(frame: pd.DataFrame, golden: Optional[pd.DataFrame],
                 max_mean_risk_diff: float = 0.01) -> CheckResult:
    if golden is None:
        return CheckResult("golden", True, "no golden frame supplied; skipped")
    diff = abs(float(frame["Failure_Risk"].mean()) - float(golden["Failure_Risk"].mean()))
    return CheckResult(
        "golden", diff <= max_mean_risk_diff,
        f"mean Failure_Risk diff vs golden = {diff:.6f} (max {max_mean_risk_diff})",
    )


def run_audit(frame: pd.DataFrame,
              expected_rate: Optional[float] = None,
              reconciliation_tolerance: float = 0.02,
              golden: Optional[pd.DataFrame] = None,
              max_brier: float = 0.01) -> List[CheckResult]:
    return [
        check_schema(frame),
        check_boundaries(frame),
        check_sparse_smoothing(frame),
        check_weighted_brier(frame, max_brier=max_brier),
        check_reconciliation(frame, expected_rate, reconciliation_tolerance),
        check_golden(frame, golden),
    ]
