"""v57 feature transform — the SINGLE source of truth shared by training and
serving (audit item #4: "one model via coverage features").

The v55 lineage drifted because training and serving each hand-maintained their
feature definitions. v57 fixes that: this module mechanically derives the v57
feature set from the v55 set + the ``model_bundle`` decision table, and provides
BOTH the row-level transform (serving) and the DataFrame-level transform
(training matrix builder). They are the same logic, so the two paths cannot
diverge.

What v57 changes vs v55 (all encoded in model_bundle.py):
  * RC-3 drop-set (4) — features v55 served as constants are removed.
  * observed renames (35) — windowed history features get ``*_observed`` names.
  * coverage features (5) — make "zero prior failures" unambiguous between a
    genuinely clean observed history and a history the window cannot see. This
    is what lets ONE model serve both first-test and returning vehicles
    (decision #4): first-test rows are kept in train+eval, and the coverage
    features tell the model which is which.

The v55 base feature list is read from ``feature_engineering_v55.py`` via the
``ast`` module (NOT imported), so this module stays dependency-light (stdlib +
model_bundle) and unit-testable without numpy/pandas. The DataFrame transform
imports pandas lazily, only when called by the trainer.
"""

from __future__ import annotations

import ast
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from model_bundle import (
    COVERAGE_FEATURES,
    FIRST_MOT_AGE_YEARS,
    OBSERVED_RENAMES,
    RC3_DROPPED_FEATURES,
    WINDOW_START,
)

_FE_SOURCE = Path(__file__).parent / "feature_engineering_v55.py"


# ---------------------------------------------------------------------------
# Derive the v57 feature set from the v55 source + decision table.
# ---------------------------------------------------------------------------
def _extract_list_literal(source_path: Path, var_name: str):
    """Read a top-level list-literal assignment without importing the module."""
    tree = ast.parse(source_path.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == var_name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise KeyError(f"{var_name} not found in {source_path}")


V55_FEATURE_NAMES: List[str] = _extract_list_literal(_FE_SOURCE, "FEATURE_NAMES")
_V55_CATEGORICAL_INDICES: List[int] = _extract_list_literal(_FE_SOURCE, "CATEGORICAL_INDICES")
V55_CATEGORICAL: List[str] = [V55_FEATURE_NAMES[i] for i in _V55_CATEGORICAL_INDICES]

_RC3 = set(RC3_DROPPED_FEATURES)


def _rename(name: str) -> str:
    return OBSERVED_RENAMES.get(name, name)


# Kept v55 features (RC-3 dropped), renamed in place, then coverage appended.
V57_FEATURE_NAMES: List[str] = (
    [_rename(n) for n in V55_FEATURE_NAMES if n not in _RC3]
    + list(COVERAGE_FEATURES.keys())
)
# Categoricals: none of the v55 categoricals are dropped or renamed, so the set
# is unchanged (coverage features are all numeric).
V57_CATEGORICAL: List[str] = [
    _rename(n) for n in V55_CATEGORICAL if n not in _RC3
]
COVERAGE_FEATURE_NAMES: List[str] = list(COVERAGE_FEATURES.keys())


# ---------------------------------------------------------------------------
# Coverage features — the decision-#4 mechanism. Pure date arithmetic.
# ---------------------------------------------------------------------------
def _as_date(d: Any) -> Optional[date]:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    # numpy datetime64 / pandas Timestamp expose .to_pydatetime or astype; try
    # the common conversions without importing those libraries.
    if hasattr(d, "date"):
        try:
            return d.date()
        except Exception:
            pass
    return datetime.fromisoformat(str(d)[:19]).date()


def compute_coverage_features(
    target_date: Any,
    first_use_date: Any,
    first_observed_test_date: Any,
    *,
    window_start: date = WINDOW_START,
) -> Dict[str, Any]:
    """Compute the 5 coverage features for one prediction.

    Args:
        target_date: the date the prediction is for (the test being predicted).
        first_use_date: vehicle first-use / registration / manufacture date
            (used to tell whether real history exists before the window).
        first_observed_test_date: earliest test OBSERVED inside the window for
            this vehicle as of ``target_date``, or None if none observed.

    Returns:
        dict with exactly the COVERAGE_FEATURES keys.
    """
    td = _as_date(target_date)
    fud = _as_date(first_use_date)
    fotd = _as_date(first_observed_test_date)

    window_days_available = max(0, (td - window_start).days) if td else 0

    if fotd is not None and td is not None:
        history_years_observed = max(0.0, (td - fotd).days / 365.25)
        has_prior_test_observed = 1
    else:
        history_years_observed = 0.0
        has_prior_test_observed = 0

    has_left_truncated_history = 0
    if fud is not None:
        first_mot_due = fud + timedelta(days=round(365.25 * FIRST_MOT_AGE_YEARS))
        if first_mot_due < window_start:
            has_left_truncated_history = 1

    first_observed_test_is_not_true_first = int(
        has_prior_test_observed == 1 and has_left_truncated_history == 1
    )

    return {
        "window_days_available": window_days_available,
        "history_years_observed": round(history_years_observed, 4),
        "has_prior_test_observed": has_prior_test_observed,
        "has_left_truncated_history": has_left_truncated_history,
        "first_observed_test_is_not_true_first": first_observed_test_is_not_true_first,
    }


# ---------------------------------------------------------------------------
# Serving-side helpers.
# ---------------------------------------------------------------------------
def cap_tests_to_window(tests: Sequence, *, window_start: date = WINDOW_START) -> List:
    """Drop tests dated before the history window (serving must match training).

    Works on any object exposing ``.test_date`` (e.g. dvsa_client.MOTTest).
    """
    out = []
    for t in tests:
        td = _as_date(getattr(t, "test_date"))
        if td is not None and td >= window_start:
            out.append(t)
    return out


def apply_v57_row_transform(
    v55_features: Dict[str, Any],
    coverage: Dict[str, Any],
) -> Dict[str, Any]:
    """Turn a v55 feature dict into a v57 feature dict.

    Drops RC-3 features, applies the observed renames, and merges the coverage
    features. The result's keys equal ``V57_FEATURE_NAMES``.
    """
    out: Dict[str, Any] = {}
    for k, v in v55_features.items():
        if k in _RC3:
            continue
        out[_rename(k)] = v
    out.update(coverage)
    return out


# ---------------------------------------------------------------------------
# Training-side helper: the same transform over a DataFrame.
# ---------------------------------------------------------------------------
def add_v57_columns(
    df,
    *,
    target_date_col: str,
    first_use_date_col: str,
    first_observed_test_date_col: str,
    window_start: date = WINDOW_START,
):
    """Return a copy of ``df`` carrying the v57 columns (renames + drops + coverage).

    Mirrors ``apply_v57_row_transform`` so the training matrix and the serving
    emission share one definition. The three date columns must already be in the
    frame (the spine / results-lake join supplies them).
    """
    import numpy as np
    import pandas as pd

    out = df.copy()
    # Drop RC-3.
    out = out.drop(columns=[c for c in RC3_DROPPED_FEATURES if c in out.columns])
    # Observed renames.
    out = out.rename(columns={k: v for k, v in OBSERVED_RENAMES.items() if k in out.columns})

    td = pd.to_datetime(out[target_date_col])
    fud = pd.to_datetime(out[first_use_date_col])
    fotd = pd.to_datetime(out[first_observed_test_date_col])
    ws = pd.Timestamp(window_start)

    out["window_days_available"] = (td - ws).dt.days.clip(lower=0).fillna(0).astype(int)
    has_prior = fotd.notna()
    out["has_prior_test_observed"] = has_prior.astype(int)
    out["history_years_observed"] = (
        ((td - fotd).dt.days / 365.25).clip(lower=0).where(has_prior, 0.0).round(4)
    )
    first_mot_due = fud + pd.to_timedelta(round(365.25 * FIRST_MOT_AGE_YEARS), unit="D")
    out["has_left_truncated_history"] = (
        (first_mot_due < ws).where(fud.notna(), False).astype(int)
    )
    out["first_observed_test_is_not_true_first"] = (
        (out["has_prior_test_observed"].eq(1) & out["has_left_truncated_history"].eq(1)).astype(int)
    )
    return out


def v57_feature_rows() -> List[Dict[str, Any]]:
    """Per-feature contract rows for emit_contract (names, dtype, default, source)."""
    cat = set(V57_CATEGORICAL)
    # Reverse rename map to record provenance back to the v55 source column.
    inverse = {v: k for k, v in OBSERVED_RENAMES.items()}
    rows: List[Dict[str, Any]] = []
    for name in V57_FEATURE_NAMES:
        if name in COVERAGE_FEATURES:
            rows.append({
                "name": name, "dtype": "float", "default": 0,
                "source": "coverage", "prediction_time_available": True,
                "window_bounded": True, "description": COVERAGE_FEATURES[name],
            })
            continue
        src_v55 = inverse.get(name, name)
        rows.append({
            "name": name,
            "dtype": "categorical" if name in cat else "float",
            "default": None if name in cat else 0,
            "source": f"v55:{src_v55}",
            "prediction_time_available": True,
            "window_bounded": name in OBSERVED_RENAMES.values(),
        })
    return rows
