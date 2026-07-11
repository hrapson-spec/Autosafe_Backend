"""
Tests for report_service.build_assessment: the postgres-then-sqlite
evidence ladder and its mapping onto ReportEvidence / ReportRisk /
ReportComponents / ReportRepairEstimate / note / prediction_source.

Async: this venv does not have pytest-asyncio installed, so async calls
are driven with asyncio.run(...) inside plain sync test functions -- the
same pattern already used by tests/test_dvla.py in this repo.
"""
import asyncio
import os
import sys
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import report_service  # noqa: E402
from report_contract import (  # noqa: E402
    ConfidenceLevel,
    MatchScope,
    POPULATION_DEFAULT_FAILURE_RISK,
    PredictionSource,
)
from report_service import (  # noqa: E402
    NOTE_AGE_BAND_ONLY,
    NOTE_MODEL_AVERAGE,
    NOTE_POPULATION_DEFAULT,
    NOTE_UNAVAILABLE,
    build_assessment,
)

from report_test_helpers import (  # noqa: E402
    SEEDED_RISKS_ROWS,
    install_postgres_unavailable,
    make_history,
    raising_sqlite_factory,
    seeded_sqlite,
    sqlite_factory,
)

FIXED_NOW = datetime(2026, 1, 1)


def run(coro):
    return asyncio.run(coro)


def _identity(make='TESTMAKE', model='TESTMODEL', year=2022, fuel_type='PETROL', colour='BLUE'):
    return {
        'registration': 'AB12CDE',
        'make': make,
        'model': model,
        'year': year,
        'fuel_type': fuel_type,
        'colour': colour,
    }


# ---------------------------------------------------------------------------
# Postgres branch: db.get_risk_v2_banded mocked directly (AsyncMock).
# Vehicle year=2022, now=2026-01-01 -> age 4 -> age_band '3-5'.
# ---------------------------------------------------------------------------

def _postgres_dict(match_scope, total_tests=500, total_failures=100, failure_risk=0.20,
                    components_available=True, risk_values=None):
    risk_values = risk_values or {
        'risk_brakes': 0.05, 'risk_suspension': 0.04, 'risk_tyres': 0.03,
        'risk_steering': 0.02, 'risk_visibility': 0.015, 'risk_lamps': 0.025, 'risk_body': 0.045,
    }
    if not components_available:
        risk_values = {k: None for k in risk_values}
    d = {
        'match_scope': match_scope,
        'total_tests': total_tests,
        'total_failures': total_failures,
        'failure_risk': failure_risk,
        'components_available': components_available,
    }
    d.update(risk_values)
    return d


def test_postgres_exact_band_field_mapping(monkeypatch):
    mock_dict = _postgres_dict('exact_band')
    monkeypatch.setattr(report_service.db, 'get_risk_v2_banded', AsyncMock(return_value=mock_dict), raising=False)

    history = make_history([], year=2022)
    assessment = run(build_assessment(_identity(), history, mileage_user=30000, now=FIXED_NOW))

    assert assessment.prediction_source == PredictionSource.POSTGRES
    assert assessment.evidence.match_scope == MatchScope.EXACT_BAND
    assert assessment.evidence.age_band == '3-5'
    assert assessment.evidence.mileage_band == '30k-60k'  # get_mileage_band(30000): first band not <30000
    assert assessment.evidence.total_tests == 500
    assert assessment.evidence.total_failures == 100
    assert assessment.risk.failure_risk == 0.20
    assert assessment.risk.confidence == ConfidenceLevel.MEDIUM  # classify_confidence(500) -> Medium (<1000)
    assert assessment.note is None

    assert assessment.components.available is True
    items = {item.key: (item.label, item.risk) for item in assessment.components.items}
    assert items['brakes'] == ('Brakes', 0.05)
    assert items['suspension'] == ('Suspension', 0.04)
    assert items['tyres'] == ('Tyres', 0.03)
    assert items['steering'] == ('Steering', 0.02)
    assert items['visibility'] == ('Visibility', 0.015)
    assert items['lamps'] == ('Lamps & Electrical', 0.025)
    assert items['body'] == ('Body & Chassis', 0.045)

    assert assessment.repair_estimate is not None
    assert assessment.repair_estimate.expected > 0
    assert assessment.repair_estimate.range_low <= assessment.repair_estimate.expected <= assessment.repair_estimate.range_high


def test_confidence_classification_matches_confidence_module():
    """Guard the exact boundary used above: classify_confidence(500) is
    'Medium' (>=100, <1000), not 'High' (needs >=1000)."""
    from confidence import classify_confidence
    assert classify_confidence(500) == 'Medium'


def test_postgres_age_band_only_field_mapping(monkeypatch):
    mock_dict = _postgres_dict('age_band_only', total_tests=150, total_failures=20, failure_risk=0.133)
    monkeypatch.setattr(report_service.db, 'get_risk_v2_banded', AsyncMock(return_value=mock_dict), raising=False)

    assessment = run(build_assessment(_identity(), None, mileage_user=15000, now=FIXED_NOW))

    assert assessment.prediction_source == PredictionSource.POSTGRES
    assert assessment.evidence.match_scope == MatchScope.AGE_BAND_ONLY
    assert assessment.note == NOTE_AGE_BAND_ONLY
    assert assessment.risk.failure_risk == 0.133
    assert assessment.risk.confidence == ConfidenceLevel.MEDIUM  # classify_confidence(150) -> Medium


def test_postgres_model_average_field_mapping(monkeypatch):
    mock_dict = _postgres_dict('model_average', total_tests=15, total_failures=3, failure_risk=0.2)
    monkeypatch.setattr(report_service.db, 'get_risk_v2_banded', AsyncMock(return_value=mock_dict), raising=False)

    assessment = run(build_assessment(_identity(), None, mileage_user=15000, now=FIXED_NOW))

    assert assessment.prediction_source == PredictionSource.POSTGRES
    assert assessment.evidence.match_scope == MatchScope.MODEL_AVERAGE
    assert assessment.note == NOTE_MODEL_AVERAGE
    assert assessment.risk.confidence == ConfidenceLevel.VERY_LOW  # classify_confidence(15) -> Very Low


def test_postgres_total_failures_none_stays_none(monkeypatch):
    """null-means-unknown: total_failures can legitimately be missing even
    when total_tests and failure_risk are known -- must never be coerced
    to 0."""
    mock_dict = _postgres_dict('exact_band', total_tests=500, total_failures=None, failure_risk=0.20)
    monkeypatch.setattr(report_service.db, 'get_risk_v2_banded', AsyncMock(return_value=mock_dict), raising=False)

    assessment = run(build_assessment(_identity(), None, mileage_user=15000, now=FIXED_NOW))

    assert assessment.evidence.total_tests == 500
    assert assessment.evidence.total_failures is None
    assert assessment.evidence.total_failures != 0


def test_postgres_components_unavailable_hides_items_and_repair_even_with_failure_risk(monkeypatch):
    """components_available False -> items None + repair None, even though
    failure_risk itself is present and the scope is otherwise a normal
    exact_band match. failure_risk must NOT be gated by component
    availability."""
    mock_dict = _postgres_dict('exact_band', total_tests=500, total_failures=100, failure_risk=0.20,
                                components_available=False)
    monkeypatch.setattr(report_service.db, 'get_risk_v2_banded', AsyncMock(return_value=mock_dict), raising=False)

    assessment = run(build_assessment(_identity(), None, mileage_user=15000, now=FIXED_NOW))

    assert assessment.risk.failure_risk == 0.20
    assert assessment.components.available is False
    assert assessment.components.items is None
    assert assessment.repair_estimate is None
    # scope-driven note is unaffected by components_available:
    assert assessment.note is None


def test_postgres_model_absent_returns_population_default(monkeypatch):
    """get_risk_v2_banded returning None (no PostgresUnavailable raised)
    means Postgres was reached successfully and the model has zero rows
    -> population_default. The displayed number comes from the checked-in
    dataset reference, while the scope records that this was a no-model-data
    fallback; SQLite is never queried."""
    sqlite_calls = []
    monkeypatch.setattr(report_service.db, 'get_risk_v2_banded', AsyncMock(return_value=None), raising=False)

    def factory():
        sqlite_calls.append(1)
        raise AssertionError("sqlite fallback must not be attempted when postgres answers None")

    assessment = run(build_assessment(_identity(), None, mileage_user=15000,
                                       sqlite_conn_factory=factory, now=FIXED_NOW))

    assert not sqlite_calls
    assert assessment.prediction_source == PredictionSource.DATASET_REFERENCE
    assert assessment.evidence.match_scope == MatchScope.POPULATION_DEFAULT
    assert assessment.note == NOTE_POPULATION_DEFAULT
    assert assessment.risk.failure_risk == POPULATION_DEFAULT_FAILURE_RISK
    assert assessment.risk.confidence == ConfidenceLevel.VERY_LOW
    assert assessment.evidence.total_tests is None
    assert assessment.evidence.total_failures is None
    assert assessment.components.available is False
    assert assessment.components.items is None
    assert assessment.repair_estimate is None


# ---------------------------------------------------------------------------
# SQLite fallback branch: triggered only by PostgresUnavailable.
# ---------------------------------------------------------------------------

def _postgres_unavailable_mock(monkeypatch):
    exc_cls = install_postgres_unavailable(monkeypatch)
    monkeypatch.setattr(
        report_service.db, 'get_risk_v2_banded', AsyncMock(side_effect=exc_cls("db down")), raising=False
    )


def test_sqlite_fallback_exact_band_matches_postgres_weighted_aggregate(monkeypatch):
    """PostgresUnavailable -> sqlite ladder step 1: exact age+mileage band.
    Two rows match (500-test model row, 50-test 'VARIANT' row via the LIKE
    pattern). SQLite must use the same sample-size-weighted aggregate as
    Postgres, so falling back cannot change the evidence shown."""
    _postgres_unavailable_mock(monkeypatch)
    conn = seeded_sqlite()

    contributing = [
        row for row in SEEDED_RISKS_ROWS
        if (row[0] == 'TESTMAKE TESTMODEL' or row[0].startswith('TESTMAKE TESTMODEL '))
        and row[1] == '3-5'
        and row[2] == '30k-60k'
    ]
    total_tests = sum(row[3] for row in contributing)
    total_failures = sum(row[4] for row in contributing)

    identity = _identity(make='TESTMAKE', model='TESTMODEL', year=2022)  # age 4 -> '3-5'
    assessment = run(build_assessment(identity, None, mileage_user=45000,  # '30k-60k'
                                       sqlite_conn_factory=sqlite_factory(conn), now=FIXED_NOW))

    assert assessment.prediction_source == PredictionSource.SQLITE
    assert assessment.evidence.match_scope == MatchScope.EXACT_BAND
    assert assessment.evidence.age_band == '3-5'
    assert assessment.evidence.mileage_band == '30k-60k'
    assert assessment.evidence.total_tests == total_tests
    assert assessment.evidence.total_failures == total_failures
    assert assessment.risk.failure_risk == pytest.approx(total_failures / total_tests)
    assert assessment.note is None

    assert assessment.components.available is True
    items = {item.key: item.risk for item in assessment.components.items}
    component_columns = {
        'brakes': 6,
        'suspension': 7,
        'tyres': 8,
        'steering': 9,
        'visibility': 10,
        'lamps': 11,
        'body': 12,
    }
    for key, column in component_columns.items():
        expected = sum(row[column] * row[3] for row in contributing) / total_tests
        assert items[key] == pytest.approx(expected)
    assert assessment.repair_estimate is not None


def test_sqlite_fallback_age_band_only_matches_postgres_weighted_aggregate(monkeypatch):
    """Step 2: age_band '6-10' has two rows (90-test and 200-test) at
    different mileage bands; querying a THIRD mileage band ('60k-100k',
    which has no row at age 6-10) must skip step 1 and fall to step 2's
    sample-size-weighted aggregate, exactly as Postgres does."""
    _postgres_unavailable_mock(monkeypatch)
    conn = seeded_sqlite()

    identity = _identity(make='TESTMAKE', model='TESTMODEL', year=2016)  # age 10 -> '6-10'
    assessment = run(build_assessment(identity, None, mileage_user=70000,  # '60k-100k': no exact row
                                       sqlite_conn_factory=sqlite_factory(conn), now=FIXED_NOW))

    assert assessment.prediction_source == PredictionSource.SQLITE
    assert assessment.evidence.match_scope == MatchScope.AGE_BAND_ONLY
    assert assessment.evidence.age_band == '6-10'
    assert assessment.evidence.mileage_band is None
    assert assessment.evidence.total_tests == 290
    assert assessment.evidence.total_failures == 75
    assert assessment.risk.failure_risk == pytest.approx(75 / 290)
    assert assessment.note == NOTE_AGE_BAND_ONLY


def test_sqlite_fallback_model_average_weighted_aggregate(monkeypatch):
    """Step 3: age_band '0-2' has no rows for TESTMAKE TESTMODEL at any
    mileage band, so both step 1 and step 2 come back empty and the
    weighted aggregate across the whole model (rows sharing the model_id
    LIKE pattern -- the 6 TESTMAKE TESTMODEL[+ VARIANT] rows, NOT the
    unrelated NULLMODEL/OTHERMAKE rows) is used. Expected values are
    computed programmatically from SEEDED_RISKS_ROWS, not hand-transcribed."""
    _postgres_unavailable_mock(monkeypatch)
    conn = seeded_sqlite()

    contributing = [
        row for row in SEEDED_RISKS_ROWS
        if row[0] == 'TESTMAKE TESTMODEL' or row[0].startswith('TESTMAKE TESTMODEL ')
    ]
    assert len(contributing) == 6  # sanity-check the fixture matches what this test expects

    total_tests = sum(row[3] for row in contributing)
    total_failures = sum(row[4] for row in contributing)
    expected_failure_risk = total_failures / total_tests
    expected_risk_brakes = sum(row[6] * row[3] for row in contributing) / total_tests  # column 6 = Risk_Brakes

    identity = _identity(make='TESTMAKE', model='TESTMODEL', year=2025)  # age 1 -> '0-2': no data at all
    assessment = run(build_assessment(identity, None, mileage_user=15000,  # '0-30k': also no 0-2 row
                                       sqlite_conn_factory=sqlite_factory(conn), now=FIXED_NOW))

    assert assessment.prediction_source == PredictionSource.SQLITE
    assert assessment.evidence.match_scope == MatchScope.MODEL_AVERAGE
    assert assessment.evidence.age_band is None
    assert assessment.evidence.mileage_band is None
    assert assessment.evidence.total_tests == total_tests
    assert assessment.evidence.total_failures == total_failures
    assert abs(assessment.risk.failure_risk - expected_failure_risk) < 1e-9
    assert assessment.note == NOTE_MODEL_AVERAGE
    assert assessment.components.available is True

    items = {item.key: item.risk for item in assessment.components.items}
    assert abs(items['brakes'] - expected_risk_brakes) < 1e-9
    assert assessment.repair_estimate is not None


def test_sqlite_fallback_components_null_row(monkeypatch):
    """A row whose Risk_Brakes happens to be NULL (never produced by
    build_db.py in real data, but the defensive branch must actually work):
    components_available=False, items None, repair None -- even though
    match_scope is a normal exact_band and failure_risk is present."""
    _postgres_unavailable_mock(monkeypatch)
    conn = seeded_sqlite()

    identity = _identity(make='TESTMAKE', model='NULLMODEL', year=2022)  # age 4 -> '3-5'
    assessment = run(build_assessment(identity, None, mileage_user=45000,  # '30k-60k'
                                       sqlite_conn_factory=sqlite_factory(conn), now=FIXED_NOW))

    assert assessment.evidence.match_scope == MatchScope.EXACT_BAND
    assert assessment.risk.failure_risk == 0.25
    assert assessment.note is None
    assert assessment.components.available is False
    assert assessment.components.items is None
    assert assessment.repair_estimate is None


def test_sqlite_partial_component_coverage_is_not_presented_as_complete(monkeypatch):
    """One populated variant must not hide a NULL component in another
    contributing row: the contract has no component-specific denominator, so
    partial coverage must suppress the component section."""
    _postgres_unavailable_mock(monkeypatch)
    conn = seeded_sqlite()
    conn.execute(
        "INSERT INTO risks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            'TESTMAKE NULLMODEL VARIANT', '3-5', '30k-60k', 10, 2, 0.20,
            0.05, 0.10, 0.10, 0.10, 0.10, 0.10, 0.10,
        ),
    )
    conn.commit()

    identity = _identity(make='TESTMAKE', model='NULLMODEL', year=2022)
    assessment = run(build_assessment(
        identity,
        None,
        mileage_user=45000,
        sqlite_conn_factory=sqlite_factory(conn),
        now=FIXED_NOW,
    ))

    assert assessment.evidence.total_tests == 50
    assert assessment.components.available is False
    assert assessment.components.items is None
    assert assessment.repair_estimate is None


def test_sqlite_fallback_step1_skipped_for_unknown_mileage_band(monkeypatch):
    """An observed reading over 500,000 miles bands to 'Unknown'
    (utils.get_mileage_band); the sqlite ladder must skip step 1 entirely
    for an 'Unknown' mileage band and fall straight to step 2 (age-only),
    landing on the same 550-test weighted age aggregate as the exact-band
    test above."""
    _postgres_unavailable_mock(monkeypatch)
    conn = seeded_sqlite()

    history = make_history([('2025-06-01', 600000, 'mi')], make='TESTMAKE', model='TESTMODEL', year=2022)
    identity = _identity(make='TESTMAKE', model='TESTMODEL', year=2022)  # age 4 -> '3-5'
    assessment = run(build_assessment(identity, history, mileage_user=None,
                                       sqlite_conn_factory=sqlite_factory(conn), now=FIXED_NOW))

    assert assessment.mileage.effective_value == 600000
    assert assessment.evidence.match_scope == MatchScope.AGE_BAND_ONLY  # step 1 skipped, step 2 answered
    assert assessment.evidence.age_band == '3-5'
    assert assessment.evidence.mileage_band is None
    assert assessment.evidence.total_tests == 550
    assert assessment.evidence.total_failures == 140
    assert assessment.risk.failure_risk == pytest.approx(140 / 550)


def test_sqlite_fallback_model_absent_is_population_default_not_unavailable(monkeypatch):
    """The model has zero rows anywhere in the seeded table: sqlite WAS
    reached successfully (no exception, factory not None), so this is a
    "queried, no data" outcome -> population_default. The displayed number's
    source is the checked-in dataset reference; match_scope preserves the
    distinction from store unavailability."""
    _postgres_unavailable_mock(monkeypatch)
    conn = seeded_sqlite()

    identity = _identity(make='NOPE', model='NOPE', year=2022)
    assessment = run(build_assessment(identity, None, mileage_user=45000,
                                       sqlite_conn_factory=sqlite_factory(conn), now=FIXED_NOW))

    assert assessment.prediction_source == PredictionSource.DATASET_REFERENCE
    assert assessment.evidence.match_scope == MatchScope.POPULATION_DEFAULT
    assert assessment.note == NOTE_POPULATION_DEFAULT
    assert assessment.risk.failure_risk == POPULATION_DEFAULT_FAILURE_RISK
    assert assessment.risk.confidence == ConfidenceLevel.VERY_LOW
    assert assessment.evidence.total_tests is None
    assert assessment.evidence.total_failures is None
    assert assessment.components.available is False
    assert assessment.repair_estimate is None


# ---------------------------------------------------------------------------
# Unavailable: factory None, or a real sqlite3 exception.
# ---------------------------------------------------------------------------

def test_sqlite_factory_none_is_unavailable(monkeypatch):
    _postgres_unavailable_mock(monkeypatch)

    identity = _identity(year=None)
    assessment = run(build_assessment(identity, None, mileage_user=None,
                                       sqlite_conn_factory=None, now=FIXED_NOW))

    assert assessment.prediction_source == PredictionSource.DATASET_REFERENCE
    assert assessment.evidence.match_scope == MatchScope.UNAVAILABLE
    assert assessment.note == NOTE_UNAVAILABLE
    assert assessment.risk.failure_risk == POPULATION_DEFAULT_FAILURE_RISK
    assert assessment.risk.confidence == ConfidenceLevel.VERY_LOW
    assert assessment.evidence.total_tests is None
    assert assessment.evidence.total_failures is None
    assert assessment.components.available is False
    assert assessment.components.items is None
    assert assessment.repair_estimate is None
    # Unavailable evidence cannot claim that any age or mileage band matched.
    assert assessment.evidence.age_band is None
    assert assessment.evidence.mileage_band is None


def test_sqlite_exception_is_unavailable(monkeypatch):
    """A real sqlite3 error raised while acquiring/using the connection
    (not just a None factory) must also degrade to unavailable, per
    report_service.py's documented "SQLite factory None or sqlite
    exception -> scope 'unavailable'" behaviour."""
    import sqlite3
    _postgres_unavailable_mock(monkeypatch)

    identity = _identity(year=2022)
    assessment = run(build_assessment(
        identity, None, mileage_user=15000,
        sqlite_conn_factory=raising_sqlite_factory(sqlite3.OperationalError("disk I/O error")),
        now=FIXED_NOW,
    ))

    assert assessment.prediction_source == PredictionSource.DATASET_REFERENCE
    assert assessment.evidence.match_scope == MatchScope.UNAVAILABLE
    assert assessment.note == NOTE_UNAVAILABLE


def test_sqlite_pool_exhaustion_yields_none_is_unavailable(monkeypatch):
    """Mirrors main.py's get_sqlite_connection(): a factory whose context
    manager yields None (e.g. pool exhausted) rather than raising."""
    from contextlib import contextmanager

    _postgres_unavailable_mock(monkeypatch)

    @contextmanager
    def empty_pool_factory():
        yield None

    identity = _identity(year=2022)
    assessment = run(build_assessment(identity, None, mileage_user=15000,
                                       sqlite_conn_factory=empty_pool_factory, now=FIXED_NOW))

    assert assessment.prediction_source == PredictionSource.DATASET_REFERENCE
    assert assessment.evidence.match_scope == MatchScope.UNAVAILABLE


# ---------------------------------------------------------------------------
# Vehicle / mot / evidence band fields independent of ladder outcome.
# ---------------------------------------------------------------------------

def test_mot_is_none_safe_when_history_is_none(monkeypatch):
    _postgres_unavailable_mock(monkeypatch)
    identity = _identity(year=None)
    assessment = run(build_assessment(identity, None, mileage_user=None,
                                       sqlite_conn_factory=None, now=FIXED_NOW))

    assert assessment.mot.expiry_date is None
    assert assessment.mot.last_test_date is None
    assessment_result = assessment.mot.last_result
    assert assessment_result is None


def test_mot_is_none_safe_when_history_has_no_tests(monkeypatch):
    _postgres_unavailable_mock(monkeypatch)
    history = make_history([], year=2020)
    identity = _identity(year=2020)
    assessment = run(build_assessment(identity, history, mileage_user=None,
                                       sqlite_conn_factory=None, now=FIXED_NOW))

    assert assessment.mot.expiry_date is None
    assert assessment.mot.last_test_date is None
    assert assessment.mot.last_result is None


def test_mot_reflects_latest_test(monkeypatch):
    mock_dict = _postgres_dict('exact_band')
    monkeypatch.setattr(report_service.db, 'get_risk_v2_banded', AsyncMock(return_value=mock_dict), raising=False)

    history = make_history([('2025-06-01', 45000, 'mi')], results=['PASSED'], year=2022)
    identity = _identity(year=2022)
    assessment = run(build_assessment(identity, history, mileage_user=None, now=FIXED_NOW))

    assert assessment.mot.last_test_date == datetime(2025, 6, 1).isoformat()
    assert assessment.mot.last_result == 'PASSED'


def test_age_band_unknown_when_year_missing(monkeypatch):
    mock_dict = _postgres_dict('model_average')
    monkeypatch.setattr(report_service.db, 'get_risk_v2_banded', AsyncMock(return_value=mock_dict), raising=False)

    assessment = run(build_assessment(_identity(year=None), None, mileage_user=15000, now=FIXED_NOW))
    assert assessment.evidence.age_band is None
    assert assessment.vehicle.year is None


def test_mileage_band_none_when_effective_value_none(monkeypatch):
    mock_dict = _postgres_dict('model_average')
    monkeypatch.setattr(report_service.db, 'get_risk_v2_banded', AsyncMock(return_value=mock_dict), raising=False)

    assessment = run(build_assessment(_identity(year=None), None, mileage_user=None, now=FIXED_NOW))
    assert assessment.mileage.effective_value is None
    assert assessment.evidence.age_band is None
    assert assessment.evidence.mileage_band is None
