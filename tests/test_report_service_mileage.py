"""
Tests for report_service.resolve_mileage: mileage provenance resolution
and the anomaly rules mirrored from main.py._get_display_mileage.

Priority order under test: user_entered > observed_mot > estimated > missing.
"""
import os
import sys
from datetime import datetime

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_contract import MileageSource  # noqa: E402
from report_service import resolve_mileage  # noqa: E402
from utils import get_mileage_band  # noqa: E402

from report_test_helpers import make_history  # noqa: E402

FIXED_NOW = datetime(2026, 1, 1)


# ---------------------------------------------------------------------------
# 1. User-entered boundary matrix: user mileage always wins, is used as-is,
#    and bands exactly at utils.get_mileage_band's documented boundaries.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "user_mileage,expected_band",
    [
        (0, '0-30k'),
        (29999, '0-30k'),
        (30000, '30k-60k'),
        (59999, '30k-60k'),
        (60000, '60k-100k'),
        (99999, '60k-100k'),
        (100000, '100k+'),
        (500000, '100k+'),  # contract's upper bound (ge=0, le=500000); still a normal band, not 'Unknown'
    ],
)
def test_user_entered_boundary_matrix(user_mileage, expected_band):
    # history is present but must be entirely ignored once mileage_user is given.
    history = make_history([('2025-01-01', 999, 'mi')])
    result = resolve_mileage(mileage_user=user_mileage, history=history, vehicle_year=2018, now=FIXED_NOW)

    assert result.source == MileageSource.USER_ENTERED
    assert result.effective_value == user_mileage
    assert get_mileage_band(result.effective_value) == expected_band
    assert result.anomaly is False
    assert result.unit_converted is False
    assert result.observed_at is None


def test_user_entered_beats_observed():
    """User beats observed: an explicit mileage_user wins even when the
    history has a perfectly good observed reading."""
    history = make_history([('2025-06-01', 45000, 'mi')])
    result = resolve_mileage(mileage_user=12345, history=history, vehicle_year=2018, now=FIXED_NOW)

    assert result.source == MileageSource.USER_ENTERED
    assert result.effective_value == 12345
    assert result.observed_at is None


# ---------------------------------------------------------------------------
# 2. Observed MOT reading (no mileage_user given).
# ---------------------------------------------------------------------------

def test_observed_single_reading_no_previous():
    history = make_history([('2025-06-01', 45000, 'mi')])
    result = resolve_mileage(mileage_user=None, history=history, vehicle_year=2018, now=FIXED_NOW)

    assert result.source == MileageSource.OBSERVED_MOT
    assert result.effective_value == 45000
    assert get_mileage_band(result.effective_value) == '30k-60k'
    assert result.observed_at == datetime(2025, 6, 1).isoformat()
    assert result.unit_converted is False
    assert result.anomaly is False


def test_observed_beats_estimated():
    """Observed beats estimated: a real reading wins over the age-based
    estimate even though vehicle_year is also known."""
    history = make_history([('2025-06-01', 45000, 'mi')])
    result = resolve_mileage(mileage_user=None, history=history, vehicle_year=2018, now=FIXED_NOW)

    assert result.source == MileageSource.OBSERVED_MOT
    assert result.effective_value == 45000


def test_observed_km_reading_converted_to_miles():
    """km reading 100000km -> round(100000 * 0.621371) = 62137mi -> '60k-100k'."""
    history = make_history([('2025-06-01', 100000, 'km')])
    result = resolve_mileage(mileage_user=None, history=history, vehicle_year=2018, now=FIXED_NOW)

    assert result.source == MileageSource.OBSERVED_MOT
    assert result.effective_value == 62137
    assert get_mileage_band(result.effective_value) == '60k-100k'
    assert result.unit_converted is True
    assert result.anomaly is False


def test_observed_reading_over_500k_stays_as_observed():
    """An implausibly high observed reading is not clamped or rejected by
    the resolver itself -- it is passed through honestly as observed_mot;
    it's the mileage_band computation (get_mileage_band -> 'Unknown') and
    the evidence-ladder's step-1 skip that react to it downstream (see
    test_report_service_banding.py). The 500,000 contract ceiling only
    applies to mileage_user, which is validated upstream of this module."""
    history = make_history([('2025-06-01', 600000, 'mi')])
    result = resolve_mileage(mileage_user=None, history=history, vehicle_year=2018, now=FIXED_NOW)

    assert result.source == MileageSource.OBSERVED_MOT
    assert result.effective_value == 600000
    assert get_mileage_band(result.effective_value) == 'Unknown'


def test_latest_test_missing_odometer_scans_to_next_reading():
    """Generalisation beyond legacy _get_display_mileage: legacy only ever
    looks at tests[0]/tests[1] and gives up entirely if tests[0] lacks a
    reading. Here we use real evidence when it exists: tests[0] has no
    odometer, so tests[1] (the next real reading) is used as the "latest
    test with a non-None odometer" -- and since there is no test *after*
    that one to compare against, no anomaly check applies."""
    history = make_history([
        ('2026-01-01', None, 'mi'),
        ('2025-01-01', 45000, 'mi'),
    ])
    result = resolve_mileage(mileage_user=None, history=history, vehicle_year=2018, now=FIXED_NOW)

    assert result.source == MileageSource.OBSERVED_MOT
    assert result.effective_value == 45000
    assert result.observed_at == datetime(2025, 1, 1).isoformat()
    assert result.anomaly is False


# ---------------------------------------------------------------------------
# 3. Anomaly rules mirrored exactly from main.py._get_display_mileage:
#
#     if days_diff > 0:
#         if mileage_diff < 0:
#             return prev_mileage, True  # negative mileage anomaly
#         if mileage_diff > 0:
#             annualized = (mileage_diff / days_diff) * 365
#             if annualized > 50000:  # physically implausible
#                 return prev_mileage, True
# ---------------------------------------------------------------------------

def test_anomaly_negative_delta_uses_previous_reading():
    """Latest reading is LOWER than the previous one (odometer rollback /
    data-entry error) -> flagged anomaly, previous reading shown instead."""
    history = make_history([
        ('2026-01-01', 45000, 'mi'),  # latest: mileage went DOWN
        ('2025-01-01', 50000, 'mi'),  # previous
    ])
    result = resolve_mileage(mileage_user=None, history=history, vehicle_year=2018, now=FIXED_NOW)

    assert result.source == MileageSource.OBSERVED_MOT
    assert result.anomaly is True
    assert result.effective_value == 50000  # the corrected (previous) reading wins
    assert result.observed_at == datetime(2025, 1, 1).isoformat()  # date the shown value was actually recorded


def test_anomaly_implausible_annualized_rate_uses_previous_reading():
    """10,000 miles in 30 days -> annualized = 10000/30*365 ~= 121,667 mi/yr,
    comfortably over the 50,000 mi/yr implausibility threshold."""
    history = make_history([
        ('2023-07-01', 20000, 'mi'),  # latest
        ('2023-06-01', 10000, 'mi'),  # previous, 30 days earlier
    ])
    result = resolve_mileage(mileage_user=None, history=history, vehicle_year=2018, now=FIXED_NOW)

    days_diff = (datetime(2023, 7, 1) - datetime(2023, 6, 1)).days
    annualized = ((20000 - 10000) / days_diff) * 365
    assert annualized > 50000  # sanity-check the fixture actually exercises the threshold

    assert result.source == MileageSource.OBSERVED_MOT
    assert result.anomaly is True
    assert result.effective_value == 10000
    assert result.observed_at == datetime(2023, 6, 1).isoformat()


def test_anomaly_not_triggered_for_plausible_increase():
    """A plausible mileage increase must NOT be flagged as an anomaly."""
    history = make_history([
        ('2026-01-01', 55000, 'mi'),  # latest: +10,000 miles over 1 year
        ('2025-01-01', 45000, 'mi'),  # previous
    ])
    result = resolve_mileage(mileage_user=None, history=history, vehicle_year=2018, now=FIXED_NOW)

    assert result.anomaly is False
    assert result.effective_value == 55000


def test_anomaly_check_skipped_when_days_diff_not_positive():
    """days_diff > 0 guard: same-day (or out-of-order) test dates skip the
    anomaly check entirely and the latest reading is used as-is, exactly
    mirroring legacy's `if days_diff > 0:` guard."""
    history = make_history([
        ('2026-01-01', 45000, 'mi'),  # latest
        ('2026-01-01', 50000, 'mi'),  # "previous" on the SAME date; mileage went down but days_diff == 0
    ])
    result = resolve_mileage(mileage_user=None, history=history, vehicle_year=2018, now=FIXED_NOW)

    assert result.anomaly is False
    assert result.effective_value == 45000


def test_anomaly_check_skipped_when_previous_test_has_no_odometer():
    """Legacy only ever checks tests[1] for the "previous" comparison; if
    that one test lacks a reading, no anomaly check happens at all (no
    further scanning) -- mirrored here exactly."""
    history = make_history([
        ('2026-01-01', 45000, 'mi'),
        ('2025-06-01', None, 'mi'),
        ('2025-01-01', 999999, 'mi'),  # would be wildly anomalous if compared, but must never be reached
    ])
    result = resolve_mileage(mileage_user=None, history=history, vehicle_year=2018, now=FIXED_NOW)

    assert result.anomaly is False
    assert result.effective_value == 45000


# ---------------------------------------------------------------------------
# 4. Estimated (no odometer reading anywhere, vehicle_year known).
# ---------------------------------------------------------------------------

def test_estimated_from_age_when_no_odometer():
    """No odometer + year -> estimated: age 8 * 8000 = 64000 -> '60k-100k'."""
    history = make_history([])
    result = resolve_mileage(mileage_user=None, history=history, vehicle_year=2018, now=FIXED_NOW)

    assert result.source == MileageSource.ESTIMATED
    assert result.effective_value == 64000
    assert get_mileage_band(result.effective_value) == '60k-100k'
    assert result.observed_at is None
    assert result.anomaly is False
    assert result.unit_converted is False


def test_estimated_none_odometer_value_also_falls_through():
    """A test record that exists but has no odometer reading at all must
    also fall through to the estimate, not be treated as observed."""
    history = make_history([('2025-01-01', None, 'mi')])
    result = resolve_mileage(mileage_user=None, history=history, vehicle_year=2020, now=FIXED_NOW)

    assert result.source == MileageSource.ESTIMATED
    assert result.effective_value == (2026 - 2020) * 8000


def test_estimated_age_zero_is_a_legitimate_zero_estimate():
    """A brand-new car (age 0) legitimately estimates to 0 miles, not
    'missing' -- 0 is a real, honest estimate here, not a fabricated
    placeholder."""
    history = make_history([])
    result = resolve_mileage(mileage_user=None, history=history, vehicle_year=2026, now=FIXED_NOW)

    assert result.source == MileageSource.ESTIMATED
    assert result.effective_value == 0


def test_estimated_floors_at_zero_for_future_year():
    """A vehicle_year after `now` (data error, or a forward-dated model
    year) must floor at age 0 rather than go negative."""
    history = make_history([])
    result = resolve_mileage(mileage_user=None, history=history, vehicle_year=2030, now=FIXED_NOW)

    assert result.source == MileageSource.ESTIMATED
    assert result.effective_value == 0


# ---------------------------------------------------------------------------
# 5. Missing (no odometer anywhere, no vehicle_year).
# ---------------------------------------------------------------------------

def test_missing_when_no_odometer_and_no_year():
    history = make_history([])
    result = resolve_mileage(mileage_user=None, history=history, vehicle_year=None, now=FIXED_NOW)

    assert result.source == MileageSource.MISSING
    assert result.effective_value is None
    assert result.observed_at is None
    assert result.anomaly is False
    assert result.unit_converted is False
    # Band computation is a downstream concern (build_assessment), but the
    # None-propagation rule is exactly: no effective value -> no band.
    band = get_mileage_band(result.effective_value) if result.effective_value is not None else None
    assert band is None


def test_missing_when_history_is_none_and_no_year():
    """history itself may be None (e.g. DVSA lookup failed entirely)."""
    result = resolve_mileage(mileage_user=None, history=None, vehicle_year=None, now=FIXED_NOW)

    assert result.source == MileageSource.MISSING
    assert result.effective_value is None
