"""
Route-level tests for the rewritten GET /api/risk (R1-T3): the legacy
lookup rebuilt over the v2 Postgres-then-SQLite evidence ladder with
honest provenance (prediction_source / cohort / note) and dark-by-default
lookup analytics.

Every fabrication the legacy handler contained dies here: no hardcoded
0.28, no age*8000 mileage synthesis, no count-blind lookups. Missing
mileage means the ladder simply starts at the age rung; no backed
aggregate means a typed population_global answer pinned to the checked-in
dataset totals; unreachable stores mean a typed unavailable answer with
failure_risk null -- never a silently fabricated rate.

Mocking pattern mirrors tests/test_report_service_banding.py: the
Postgres rung is an AsyncMock over db.get_risk_v2_banded (report_service
and main share the same `database` module object), and the SQLite tier is
driven through the same report_test_helpers seeded fixtures. Async is
driven with asyncio.run where needed (no pytest-asyncio in this venv).
"""
import os
import sys
import time
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
import main as main_module  # noqa: E402
import report_service  # noqa: E402
from report_contract import (  # noqa: E402
    DATASET_TOTAL_FAILURES,
    DATASET_TOTAL_TESTS,
)
from utils import get_age_band  # noqa: E402

from report_test_helpers import seeded_sqlite, sqlite_factory  # noqa: E402

client = TestClient(main_module.app)

# The legacy 15-key response surface that must stay present on EVERY path,
# plus the additive truth fields.
LEGACY_15_KEYS = {
    "vehicle", "year", "mileage", "last_mot_date", "last_mot_result",
    "failure_risk", "confidence_level", "risk_brakes", "risk_suspension",
    "risk_tyres", "risk_steering", "risk_visibility", "risk_lamps",
    "risk_body", "repair_cost_estimate",
}
ADDITIVE_KEYS = {"prediction_source", "cohort", "note"}

UNAVAILABLE_NOTE = (
    "Comparison data is temporarily unavailable — no rate is shown. "
    "Try again shortly."
)

# Age-band inputs are computed from the CURRENT year (the handler pins
# `age = datetime.now().year - year`), so tests derive `year` from a fixed
# age instead of hardcoding a year that silently changes band over time.
YEAR_AGE_4 = datetime.now().year - 4  # get_age_band(4) == '3-5'


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """/api/risk is limited to 20/minute per client IP and TestClient uses
    a single fixed IP for the whole pytest process; without a reset this
    file's requests would consume the shared budget and 429 later tests
    (see tests/test_report_routes.py's isolated-app comment)."""
    main_module.limiter.reset()
    yield


@pytest.fixture(autouse=True)
def _analytics_dark(monkeypatch):
    """Analytics ship dark: unless a test opts in explicitly, the flag is
    ABSENT (not just falsy), exactly like a fresh deployment."""
    monkeypatch.delenv("LOOKUP_ANALYTICS_ENABLED", raising=False)
    yield


def _rung(match_scope, total_tests=500, total_failures=100, failure_risk=0.20,
          components_available=True):
    """A normalized ladder-result dict, shape-matched to
    database.get_risk_v2_banded's documented return (the same builder
    pattern as tests/test_report_service_banding.py's _postgres_dict)."""
    risk_values = {
        'risk_brakes': 0.05, 'risk_suspension': 0.04, 'risk_tyres': 0.03,
        'risk_steering': 0.02, 'risk_visibility': 0.015, 'risk_lamps': 0.025,
        'risk_body': 0.045,
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


def _mock_postgres(monkeypatch, return_value=None, side_effect=None):
    mock = AsyncMock(return_value=return_value, side_effect=side_effect)
    monkeypatch.setattr(database, 'get_risk_v2_banded', mock)
    return mock


def _mock_stores_down(monkeypatch):
    mock = _mock_postgres(
        monkeypatch, side_effect=database.PostgresUnavailable("staged outage")
    )
    monkeypatch.setattr(main_module, 'get_sqlite_connection', None)
    return mock


def _drain_analytics_tasks(timeout=2.0):
    """Wait for the handler's fire-and-forget analytics task(s) to finish,
    so both called and NOT-called assertions are made after the task has
    actually run (never vacuously against a task that hasn't started)."""
    deadline = time.time() + timeout
    while main_module._LOOKUP_ANALYTICS_TASKS and time.time() < deadline:
        time.sleep(0.01)
    assert not main_module._LOOKUP_ANALYTICS_TASKS, (
        "lookup analytics task did not complete within {}s".format(timeout)
    )


def _ladder_call_bands(mock):
    """(age_band, mileage_band) the Postgres rung was queried with,
    tolerant of positional or keyword call style."""
    assert mock.await_count >= 1, "get_risk_v2_banded was never awaited"
    call = mock.await_args
    args = list(call.args) + [None] * 3
    age_band = call.kwargs.get('age_band', args[1])
    mileage_band = call.kwargs.get('mileage_band', args[2])
    return age_band, mileage_band


# ---------------------------------------------------------------------------
# Tier mapping: ladder outcome -> (prediction_source, cohort).
# ---------------------------------------------------------------------------

def test_mileage_drives_exact_band(monkeypatch):
    mock = _mock_postgres(monkeypatch, return_value=_rung('exact_band'))

    r = client.get("/api/risk", params={
        "make": "FORD", "model": "FIESTA", "year": YEAR_AGE_4, "mileage": 125000,
    })
    assert r.status_code == 200, r.text
    body = r.json()

    _, queried_mileage_band = _ladder_call_bands(mock)
    assert queried_mileage_band == '100k+'

    assert body["prediction_source"] == "population_exact"
    assert body["cohort"]["match_level"] == "exact_band"
    assert body["cohort"]["mileage_band"] == "100k+"
    assert body["cohort"]["age_band"] == get_age_band(4)
    assert body["cohort"]["total_tests"] == 500
    assert body["cohort"]["total_failures"] == 100
    assert body["failure_risk"] == pytest.approx(0.20)
    assert body["mileage"] == 125000  # validated echo, never a "reading"


def test_missing_mileage_starts_at_age_rung_no_estimate(monkeypatch):
    mock = _mock_postgres(monkeypatch, return_value=_rung('age_band_only'))

    r = client.get("/api/risk", params={
        "make": "FORD", "model": "FIESTA", "year": YEAR_AGE_4,
    })
    assert r.status_code == 200, r.text
    body = r.json()

    # The exact rung is skipped: the ladder call carries the skip signal
    # (mileage_band None or its 'Unknown' equivalent), never an estimate.
    _, queried_mileage_band = _ladder_call_bands(mock)
    assert queried_mileage_band in (None, 'Unknown')

    assert body["prediction_source"] == "population_broad"
    assert body["cohort"]["match_level"] == "age_band_only"
    assert body["cohort"]["age_band"] == get_age_band(4)
    assert body["cohort"]["mileage_band"] is None
    assert body["mileage"] is None

    # No fabricated mileage anywhere in the serialized response.
    assert "estimated" not in r.text
    assert "50000" not in r.text


def test_model_absent_returns_population_global_not_028(monkeypatch):
    _mock_postgres(monkeypatch, return_value=None)

    r = client.get("/api/risk", params={
        "make": "NOSUCHMAKE", "model": "NOSUCHMODEL", "year": YEAR_AGE_4,
    })
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["prediction_source"] == "population_global"
    assert body["cohort"]["match_level"] == "dataset"
    assert body["cohort"]["total_tests"] == 148509908
    assert body["cohort"]["total_tests"] == DATASET_TOTAL_TESTS
    assert body["cohort"]["total_failures"] == DATASET_TOTAL_FAILURES
    assert body["cohort"]["age_band"] is None
    assert body["cohort"]["mileage_band"] is None
    assert body["failure_risk"] == pytest.approx(39969903 / 148509908)
    assert body["failure_risk"] != 0.28


def test_stores_down_returns_typed_unavailable(monkeypatch):
    _mock_stores_down(monkeypatch)

    r = client.get("/api/risk", params={
        "make": "FORD", "model": "FIESTA", "year": YEAR_AGE_4, "mileage": 45000,
    })
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["prediction_source"] == "unavailable"
    assert body["failure_risk"] is None
    assert body["cohort"] is None
    assert body["confidence_level"] is None
    assert body["repair_cost_estimate"] is None
    for key in ("risk_brakes", "risk_suspension", "risk_tyres", "risk_steering",
                "risk_visibility", "risk_lamps", "risk_body"):
        assert body[key] is None, key
    assert body["note"] == UNAVAILABLE_NOTE


# ---------------------------------------------------------------------------
# The legacy 15-key surface survives on every path.
# ---------------------------------------------------------------------------

def _response_for_tier(monkeypatch, tier):
    if tier == "population_exact":
        _mock_postgres(monkeypatch, return_value=_rung('exact_band'))
        params = {"make": "FORD", "model": "FIESTA", "year": YEAR_AGE_4, "mileage": 45000}
    elif tier == "population_broad":
        _mock_postgres(monkeypatch, return_value=_rung('age_band_only'))
        params = {"make": "FORD", "model": "FIESTA", "year": YEAR_AGE_4}
    elif tier == "population_global":
        _mock_postgres(monkeypatch, return_value=None)
        params = {"make": "NOSUCHMAKE", "model": "NOSUCHMODEL", "year": YEAR_AGE_4}
    elif tier == "unavailable":
        _mock_stores_down(monkeypatch)
        params = {"make": "FORD", "model": "FIESTA", "year": YEAR_AGE_4}
    else:  # pragma: no cover - test bug guard
        raise AssertionError(tier)
    r = client.get("/api/risk", params=params)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["prediction_source"] == tier
    return r


ALL_TIERS = ["population_exact", "population_broad", "population_global", "unavailable"]


@pytest.mark.parametrize("tier", ALL_TIERS)
def test_legacy_15_keys_always_present(monkeypatch, tier):
    body = _response_for_tier(monkeypatch, tier).json()
    assert set(body.keys()) == LEGACY_15_KEYS | ADDITIVE_KEYS, sorted(body.keys())


# ---------------------------------------------------------------------------
# Component / repair honesty.
# ---------------------------------------------------------------------------

def test_component_nulls_stay_null(monkeypatch):
    """A found rung with a null component: all 7 components stay null and
    the repair estimate stays null, even though failure_risk itself is
    known -- partial component evidence is never presented as complete."""
    _mock_postgres(
        monkeypatch, return_value=_rung('exact_band', components_available=False)
    )

    r = client.get("/api/risk", params={
        "make": "FORD", "model": "FIESTA", "year": YEAR_AGE_4, "mileage": 45000,
    })
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["prediction_source"] == "population_exact"
    assert body["failure_risk"] == pytest.approx(0.20)
    for key in ("risk_brakes", "risk_suspension", "risk_tyres", "risk_steering",
                "risk_visibility", "risk_lamps", "risk_body"):
        assert body[key] is None, key
    assert body["repair_cost_estimate"] is None


def test_repair_estimate_null_without_components(monkeypatch):
    """Same invariant proven through the OTHER store: the seeded SQLite
    NULLMODEL row (one NULL Risk_Brakes) reached via the PostgresUnavailable
    fallback -- repair_cost_estimate must be null whenever the component
    breakdown is, because the estimate is computed FROM the components."""
    _mock_postgres(
        monkeypatch, side_effect=database.PostgresUnavailable("staged outage")
    )
    monkeypatch.setattr(
        main_module, 'get_sqlite_connection', sqlite_factory(seeded_sqlite())
    )

    r = client.get("/api/risk", params={
        "make": "TESTMAKE", "model": "NULLMODEL", "year": YEAR_AGE_4, "mileage": 45000,
    })
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["failure_risk"] == pytest.approx(0.25)  # rung found, rate known
    assert body["repair_cost_estimate"] is None
    for key in ("risk_brakes", "risk_suspension", "risk_tyres", "risk_steering",
                "risk_visibility", "risk_lamps", "risk_body"):
        assert body[key] is None, key


# ---------------------------------------------------------------------------
# Analytics logging: DARK by default, opt-in, never latency-bearing.
# ---------------------------------------------------------------------------

def test_lookup_logging_dark_by_default(monkeypatch):
    _mock_postgres(monkeypatch, return_value=_rung('exact_band'))
    save_mock = AsyncMock(return_value="row-id")
    monkeypatch.setattr(database, 'save_risk_check', save_mock)

    r = client.get("/api/risk", params={
        "make": "FORD", "model": "FIESTA", "year": YEAR_AGE_4, "mileage": 45000,
        "registration": "AB12CDE",
    })
    assert r.status_code == 200, r.text

    _drain_analytics_tasks()
    save_mock.assert_not_awaited()


def test_lookup_logging_with_registration_postcode(monkeypatch):
    monkeypatch.setenv("LOOKUP_ANALYTICS_ENABLED", "1")
    _mock_postgres(monkeypatch, return_value=_rung('exact_band'))
    save_mock = AsyncMock(return_value="row-id")
    monkeypatch.setattr(database, 'save_risk_check', save_mock)

    r = client.get("/api/risk", params={
        "make": "FORD", "model": "FIESTA", "year": YEAR_AGE_4, "mileage": 45000,
        "registration": "ab12 cde", "postcode": "SW1A 1AA",
    })
    assert r.status_code == 200, r.text
    body = r.json()

    _drain_analytics_tasks()
    assert save_mock.await_count == 1
    payload = save_mock.await_args.args[0]
    assert payload["model_version"] == "lookup_v2"
    assert payload["registration"] == "AB12CDE"  # normalized, never raw
    assert payload["prediction_source"] == body["prediction_source"]
    assert payload["is_dvsa_data"] is False


def test_lookup_logging_skipped_without_identifiers(monkeypatch):
    monkeypatch.setenv("LOOKUP_ANALYTICS_ENABLED", "1")
    _mock_postgres(monkeypatch, return_value=_rung('exact_band'))
    save_mock = AsyncMock(return_value="row-id")
    monkeypatch.setattr(database, 'save_risk_check', save_mock)

    r = client.get("/api/risk", params={
        "make": "FORD", "model": "FIESTA", "year": YEAR_AGE_4, "mileage": 45000,
    })
    assert r.status_code == 200, r.text

    _drain_analytics_tasks()
    save_mock.assert_not_awaited()


def test_invalid_registration_format_400(monkeypatch):
    _mock_postgres(monkeypatch, return_value=_rung('exact_band'))

    r = client.get("/api/risk", params={
        "make": "FORD", "model": "FIESTA", "year": YEAR_AGE_4,
        "registration": "!!!",
    })
    assert r.status_code == 400, r.text


# ---------------------------------------------------------------------------
# No fabricated numbers, ever -- across every tier.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tier", ALL_TIERS)
def test_response_never_contains_028_or_zero_test_counts(monkeypatch, tier):
    r = _response_for_tier(monkeypatch, tier)
    body = r.json()

    # The legacy fabricated rate must never appear as a serialized literal.
    # population_global's computed dataset rate is ~0.2691, which is fine --
    # the assertion is on the exact string "0.28".
    assert "0.28" not in r.text

    # Zero test counts are not evidence and must never be presented.
    assert '"total_tests": 0' not in r.text
    assert '"total_tests":0' not in r.text
    cohort = body.get("cohort")
    if cohort is not None:
        assert cohort["total_tests"] > 0
