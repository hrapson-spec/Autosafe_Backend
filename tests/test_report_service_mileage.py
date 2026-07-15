"""
Tests for report_service.resolve_odometer (the shared DVSA odometer
parser) and report_service.resolve_mileage (a thin adapter from
OdometerReading onto the report contract's ReportMileage shape).

Release 1 ("Truthful Population Report") removed two fabrication paths
that used to live in resolve_mileage: user-entered priority (mileage_user
no longer exists anywhere on this path) and the age*8000 "estimated"
fallback. resolve_mileage can now only ever produce OBSERVED_MOT (a real
recorded MOT odometer reading) or MISSING (honestly absent) -- there is
no third tier and the previous reading is never silently substituted for
an implausible one.
"""
import os
import sys
from datetime import datetime

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_contract import (  # noqa: E402
    MileageSource,
    OdometerStatus,
    OdometerUnavailableReason,
)
from report_service import resolve_mileage, resolve_odometer  # noqa: E402
from dvsa_client import DVSAClient, MOTTest, VehicleHistory  # noqa: E402

from report_test_helpers import make_history  # noqa: E402


# ---------------------------------------------------------------------------
# Regression (a): the canonical date-only wire contract, proven from a real
# DVSA timestamp string all the way through the actual dvsa_client._parse_date
# into resolve_mileage.
# ---------------------------------------------------------------------------

def test_real_dvsa_timestamp_through_parse_date_yields_date_only_wire_form():
    """A real DVSA completedDate timestamp string, run through the actual
    dvsa_client._parse_date and then resolve_mileage, must yield the
    documented canonical wire form 'YYYY-MM-DD' -- never a zone-less
    '...T00:00:00' timestamp (which a Europe/London browser renders a day
    early). _parse_date truncates to date_str[:10] and parses '%Y-%m-%d'
    (a midnight datetime); resolve_odometer emits test_date.date().isoformat()
    so the wire form stays a bare calendar date.
    """
    client = DVSAClient(
        client_id='id', client_secret='secret',
        token_url='https://example/token', api_key='key',
    )
    # A real DVSA MOT completedDate is a full timestamp; pick a July (BST)
    # instant -- the season the old zone-less-midnight bug actually bit.
    parsed = client._parse_date('2025-07-02T10:30:00.000Z')
    assert parsed == datetime(2025, 7, 2)  # truncated to the calendar date, midnight

    history = VehicleHistory(
        registration='AB12CDE', make='FORD', model='FIESTA',
        fuel_type='PETROL', colour='BLUE',
        registration_date=datetime(2018, 1, 1),
        manufacture_date=datetime(2018, 1, 1),
        engine_size=1200,
        mot_tests=[MOTTest(
            test_date=parsed, test_result='PASSED', expiry_date=None,
            odometer_value=45000, odometer_unit='mi',
            test_number='TEST0', defects=[],
        )],
    )
    result = resolve_mileage(history)

    assert result.observed_at == '2025-07-02'
    assert 'T' not in (result.observed_at or '')  # never a zone-less timestamp


# ---------------------------------------------------------------------------
# resolve_odometer: reading passthrough + unit conversion.
# ---------------------------------------------------------------------------

def test_miles_reading_passthrough():
    """A plain miles reading is echoed verbatim: value, date, and unit all
    round-trip unchanged."""
    history = make_history([('2025-06-01', 45000, 'mi')])
    result = resolve_odometer(history)

    assert result.status == OdometerStatus.AVAILABLE
    assert result.value_miles == 45000
    assert result.original_value == 45000
    assert result.original_unit == 'mi'
    assert result.recorded_at == datetime(2025, 6, 1).date().isoformat()
    assert result.source == MileageSource.OBSERVED_MOT
    assert result.unavailable_reason is None


def test_km_reading_converted_and_originals_preserved():
    """18,000 km -> round(18000 * 0.621371) = 11,185 mi, while the
    original verbatim reading + unit are preserved untouched."""
    history = make_history([('2025-06-01', 18000, 'km')])
    result = resolve_odometer(history)

    assert result.status == OdometerStatus.AVAILABLE
    assert result.value_miles == 11185
    assert result.original_value == 18000
    assert result.original_unit == 'km'


def test_reading_date_is_the_displayed_readings_date():
    """recorded_at is the ISO date of the specific reading being
    displayed -- not the date of the older comparison reading used only
    for the plausibility check."""
    history = make_history([
        ('2026-01-01', 55000, 'mi'),  # displayed reading
        ('2025-01-01', 45000, 'mi'),  # comparison prior -- NOT recorded_at
    ])
    result = resolve_odometer(history)

    assert result.status == OdometerStatus.AVAILABLE
    assert result.recorded_at == datetime(2026, 1, 1).date().isoformat()
    assert result.recorded_at != datetime(2025, 1, 1).date().isoformat()


# ---------------------------------------------------------------------------
# resolve_odometer: the newest-first displayability scan.
# ---------------------------------------------------------------------------

def test_reading_without_date_is_skipped_in_scan():
    """The newest test has an odometer reading but no recorded test_date
    -- not displayable -- so the scan must move on to the next test that
    has both a reading and a date."""
    history = make_history([
        (None, 99999, 'mi'),          # newest: reading present, no date -- skipped
        ('2025-01-01', 45000, 'mi'),  # next: this one is displayed
    ])
    result = resolve_odometer(history)

    assert result.status == OdometerStatus.AVAILABLE
    assert result.value_miles == 45000
    assert result.recorded_at == datetime(2025, 1, 1).date().isoformat()


def test_scan_skips_missing_latest_odometer():
    """The newest test has no odometer_value at all -- the scan moves on
    to the next test that has one (generalisation beyond the legacy
    tests[0]/tests[1]-only logic)."""
    history = make_history([
        ('2026-01-01', None, 'mi'),
        ('2025-01-01', 45000, 'mi'),
    ])
    result = resolve_odometer(history)

    assert result.status == OdometerStatus.AVAILABLE
    assert result.value_miles == 45000
    assert result.recorded_at == datetime(2025, 1, 1).date().isoformat()


def test_no_reading_anywhere_is_unavailable_no_reading():
    history = make_history([])
    result = resolve_odometer(history)

    assert result.status == OdometerStatus.UNAVAILABLE
    assert result.unavailable_reason == OdometerUnavailableReason.NO_READING
    assert result.value_miles is None


# ---------------------------------------------------------------------------
# resolve_odometer: unit validation.
# ---------------------------------------------------------------------------

def test_unknown_unit_is_unavailable():
    history = make_history([('2025-06-01', 45000, 'furlongs')])
    result = resolve_odometer(history)

    assert result.status == OdometerStatus.UNAVAILABLE
    assert result.unavailable_reason == OdometerUnavailableReason.UNKNOWN_UNIT
    assert result.value_miles is None


def test_absent_unit_is_unavailable():
    history = make_history([('2025-06-01', 45000, None)])
    result = resolve_odometer(history)

    assert result.status == OdometerStatus.UNAVAILABLE
    assert result.unavailable_reason == OdometerUnavailableReason.UNKNOWN_UNIT
    assert result.value_miles is None


# ---------------------------------------------------------------------------
# resolve_odometer: plausibility vs the adjacent next-older reading.
# ---------------------------------------------------------------------------

def test_rollback_is_unavailable_not_previous_reading():
    """Latest reading is LOWER than the previous one (odometer rollback /
    data-entry error) -> UNAVAILABLE/ROLLBACK. The old (pre-Release-1)
    behaviour returned the previous reading instead; that must never
    recur -- value_miles is None, full stop."""
    history = make_history([
        ('2026-01-01', 45000, 'mi'),  # latest: mileage went DOWN
        ('2025-01-01', 50000, 'mi'),  # previous
    ])
    result = resolve_odometer(history)

    assert result.status == OdometerStatus.UNAVAILABLE
    assert result.unavailable_reason == OdometerUnavailableReason.ROLLBACK
    assert result.value_miles is None
    assert result.value_miles != 50000


def test_implausible_annualised_rate_is_unavailable():
    """10,000 miles in 30 days -> annualized ~= 121,667 mi/yr, comfortably
    over the 50,000 mi/yr implausibility threshold -> UNAVAILABLE, not the
    previous reading."""
    history = make_history([
        ('2023-07-01', 20000, 'mi'),  # latest
        ('2023-06-01', 10000, 'mi'),  # previous, 30 days earlier
    ])
    days_diff = (datetime(2023, 7, 1) - datetime(2023, 6, 1)).days
    annualized = ((20000 - 10000) / days_diff) * 365
    assert annualized > 50000  # sanity-check the fixture actually exercises the threshold

    result = resolve_odometer(history)

    assert result.status == OdometerStatus.UNAVAILABLE
    assert result.unavailable_reason == OdometerUnavailableReason.IMPLAUSIBLE_INCREASE
    assert result.value_miles is None
    assert result.value_miles != 10000


def test_plausible_increase_stays_available():
    """A plausible mileage increase must NOT be flagged implausible."""
    history = make_history([
        ('2026-01-01', 55000, 'mi'),  # latest: +10,000 miles over 1 year
        ('2025-01-01', 45000, 'mi'),  # previous
    ])
    result = resolve_odometer(history)

    assert result.status == OdometerStatus.AVAILABLE
    assert result.value_miles == 55000


def test_mixed_unit_pair_compared_in_miles():
    """Prior reading in km, latest in mi -- plausibility must be computed
    AFTER converting both to miles. Naively comparing the raw numbers
    (70000 vs 55000) would look like a rollback; correctly converted
    (70,000 km =~ 43,496 mi vs 55,000 mi) it is a plausible increase."""
    history = make_history([
        ('2026-01-01', 55000, 'mi'),  # latest
        ('2025-01-01', 70000, 'km'),  # previous, ~43,496 mi once converted
    ])
    result = resolve_odometer(history)

    assert result.status == OdometerStatus.AVAILABLE
    assert result.value_miles == 55000


def test_prior_without_reading_skips_plausibility():
    """Edge pin (T1 review): the adjacent prior test exists but carries no
    parseable odometer reading -- there is nothing to compare against, so
    the plausibility check is skipped entirely and the latest reading
    stands, AVAILABLE."""
    history = make_history([
        ('2026-01-01', 55000, 'mi'),   # latest: displayable
        ('2025-01-01', None, 'mi'),    # adjacent prior: no reading at all
    ])
    result = resolve_odometer(history)

    assert result.status == OdometerStatus.AVAILABLE
    assert result.value_miles == 55000
    assert result.recorded_at == datetime(2026, 1, 1).date().isoformat()
    assert result.unavailable_reason is None


def test_same_day_pair_reading_stands():
    """Edge pin (T1 review): the adjacent prior reading shares the same
    test_date -> days_diff <= 0 -> the plausibility comparison must not
    fire (no rollback verdict, no rate division), and the latest reading
    stands -- even though the raw same-day delta happens to be negative."""
    history = make_history([
        ('2025-06-01', 45000, 'mi'),   # latest entry for the day
        ('2025-06-01', 50000, 'mi'),   # same-day prior entry, higher reading
    ])
    result = resolve_odometer(history)

    assert result.status == OdometerStatus.AVAILABLE
    assert result.value_miles == 45000
    assert result.unavailable_reason is None


# ---------------------------------------------------------------------------
# resolve_odometer: no fabricated numbers, ever -- property-style.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "history",
    [
        make_history([]),
        None,
        make_history([('2025-01-01', None, 'mi'), ('2024-01-01', None, 'km')]),
    ],
    ids=["empty_list", "none_history", "all_unparseable"],
)
def test_never_returns_50000_or_age_times_8000_for_any_empty_history(history):
    result = resolve_odometer(history)

    assert result.status == OdometerStatus.UNAVAILABLE
    assert result.value_miles is None  # None categorically excludes any fabricated number
    assert result.value_miles != 50000
    assert result.source is None


# ---------------------------------------------------------------------------
# resolve_mileage: thin adapter over resolve_odometer.
# ---------------------------------------------------------------------------

def test_available_maps_to_observed_mot_with_originals():
    history = make_history([('2025-06-01', 18000, 'km')])
    result = resolve_mileage(history)

    assert result.source == MileageSource.OBSERVED_MOT
    assert result.effective_value == 11185
    assert result.observed_at == datetime(2025, 6, 1).date().isoformat()
    assert result.unit_converted is True
    assert result.anomaly is False
    assert result.original_value == 18000
    assert result.original_unit == 'km'


def test_rollback_maps_to_missing_with_anomaly_true():
    history = make_history([
        ('2026-01-01', 45000, 'mi'),
        ('2025-01-01', 50000, 'mi'),
    ])
    result = resolve_mileage(history)

    assert result.source == MileageSource.MISSING
    assert result.effective_value is None
    assert result.anomaly is True
    assert result.observed_at is None
    assert result.unit_converted is False
    assert result.original_value is None
    assert result.original_unit is None


def test_missing_maps_to_missing_anomaly_false():
    result = resolve_mileage(None)

    assert result.source == MileageSource.MISSING
    assert result.effective_value is None
    assert result.anomaly is False
    assert result.observed_at is None
    assert result.original_value is None
    assert result.original_unit is None
