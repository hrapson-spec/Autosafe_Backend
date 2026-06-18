"""Honest, point-in-time mileage representations (shared by train and serve).

Why this module exists
----------------------
The v57 feature contract's point-in-time rule (see ``model_bundle.emit_contract``)
requires every feature to use *only data available strictly before the target
test date*. Two long-standing mileage signals break that rule on the TRAINING
side, where each row is the test being scored:

  * ``test_mileage`` is ``d.test_mileage`` — the odometer recorded **at** the
    scored test (``train_catboost_production_v55.py`` base SELECT), i.e.
    contemporaneous with the label.
  * the training ``annualized_mileage_v2`` differences that same scored-test
    odometer over the prior interval
    (``(d.test_mileage - m_prev.test_mileage) * 365 / days``), so it also reads
    the target test.

At *serving* time those same names resolve to the most recent **completed**
test (``feature_engineering_v55.py`` uses ``tests[0]``), because the test being
predicted has not happened yet. So the trained feature and the served feature
are different estimands (train/serve skew) *and* the trained one is not
point-in-time honest. The contemporaneous level is also heavily collinear with
vehicle age, which the model already encodes — so most of its apparent strength
is an age proxy plus same-event leakage.

The honest representations
--------------------------
This module is the single definition of the leakage-free mileage features, so
the trainer and the serving path cannot drift:

  * ``prior_odometer``  — the honest mileage LEVEL: the odometer at the most
    recent test **strictly before** the target. In serving this already equals
    ``test_mileage`` (= ``tests[0]`` odometer); in training it is reconstructed
    as ``test_mileage - miles_since_last_test``.
  * ``projected_odometer`` — ``prior_odometer`` carried forward to the target
    date at the honest usage rate; the best point-in-time estimate of the
    odometer the next test will record.
  * ``cohort_ratio`` — the safe-divide normaliser used for BOTH the level
    cohort ratio and the rate cohort ratio. The cohort-normalised usage *rate*
    (``annualized_mileage_v2 / cohort_rate``) is the age- and model-decontaminated
    "driven hard for what it is" signal: the strongest *valid* mileage
    representation, because the rate is largely orthogonal to age whereas the
    raw level is not.

All functions are pure, dependency-free and NaN/None-safe so they can be used
scalar-wise in the serving path and via ``DataFrame.apply`` in the trainer.
"""

from __future__ import annotations

import math
from typing import Optional

# UK average annual mileage; matches the serving/training fallback for
# annualized_mileage_v2 when no valid inter-test rate is available.
DEFAULT_ANNUAL_MILES: float = 10000.0

# Neutral cohort ratio (this car is exactly average for its cohort).
NEUTRAL_RATIO: float = 1.0


def _as_finite_float(value: object) -> Optional[float]:
    """Coerce to a finite float, or return None for None/NaN/inf/non-numeric."""
    if value is None:
        return None
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def prior_odometer(current_odometer: object, miles_since_last_test: object) -> float:
    """Honest mileage LEVEL from a *training* row.

    The scored row carries ``current_odometer`` (the odometer read at the test
    being scored) and ``miles_since_last_test`` (= current minus the previous
    test's reading). The point-in-time-honest level is the *previous* test's
    odometer, which is exactly ``current - miles_since_last_test``.

    Falls back to ``current_odometer`` when the delta is missing or implausible
    (negative) — e.g. first-test vehicles or clocking anomalies — which mirrors
    the serving path, where the most recent completed reading is all there is.
    """
    current = _as_finite_float(current_odometer)
    if current is None:
        return 0.0
    delta = _as_finite_float(miles_since_last_test)
    if delta is None or delta < 0:
        return current
    return current - delta


def projected_odometer(
    last_completed_odometer: object,
    annual_rate: object,
    days_ahead: object,
    default_rate: float = DEFAULT_ANNUAL_MILES,
) -> float:
    """Project the last completed odometer forward to the target date.

    ``last_completed_odometer + rate * days_ahead / 365`` — the point-in-time
    estimate of the odometer the upcoming test will record, using only the last
    completed reading and the honest usage rate. ``days_ahead`` is clamped to be
    non-negative; a missing/non-positive ``annual_rate`` falls back to
    ``default_rate`` so the projection never moves the odometer backwards.
    """
    last = _as_finite_float(last_completed_odometer)
    if last is None:
        return 0.0
    rate = _as_finite_float(annual_rate)
    if rate is None or rate <= 0:
        rate = default_rate
    days = _as_finite_float(days_ahead)
    if days is None or days < 0:
        days = 0.0
    return last + rate * (days / 365.0)


def cohort_ratio(
    value: object,
    cohort_mean: object,
    global_mean: object = None,
    neutral: float = NEUTRAL_RATIO,
) -> float:
    """Normalise ``value`` by its cohort mean (with a global-mean fallback).

    Used for both ``mileage_cohort_ratio`` (level / cohort level) and
    ``annualized_mileage_cohort_ratio`` (rate / cohort rate). Returns ``neutral``
    when the numerator is missing or no positive denominator is available, so an
    absent cohort table degrades gracefully to "average for cohort" rather than
    NaN/inf — matching the existing serving default.
    """
    v = _as_finite_float(value)
    if v is None:
        return neutral
    denom = _as_finite_float(cohort_mean)
    if denom is None or denom <= 0:
        denom = _as_finite_float(global_mean)
    if denom is None or denom <= 0:
        return neutral
    return v / denom
