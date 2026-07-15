"""
Tests for _fallback_prediction (main.py) and the /api/risk/v55 display
mileage fields (R1-T4): the last fabrication sites in the backend.

_fallback_prediction is re-implemented over the exact v2 evidence-ladder
machinery Task 3 built (report_service.build_lookup / _query_evidence_ladder
/ _build_components_and_repair) -- no hardcoded 0.28, no 0.05/0.04/0.03/0.02
component defaults, no invented repair-cost fabrication. Mocking pattern
mirrors tests/test_api_lookup_v2.py: the Postgres rung is an AsyncMock over
db.get_risk_v2_banded (report_service and main share the same `database`
module object), driven with asyncio.run (no pytest-asyncio in this venv).

Tests 1-4 exercise _fallback_prediction directly as a coroutine rather than
through /api/risk/v55's HTTP layer: it is the same function Task 3's sibling
(/api/risk) tests exercise at the route layer, but _fallback_prediction
itself has no HTTP concerns (VRM validation, rate limiting) worth re-driving
through TestClient here -- calling it directly lets each test assert
precisely on its Dict return value with the same ladder/store mocks.

test_v55_display_mileage_from_resolve_odometer (item 5) is different: the
display-mileage fields it covers live inline in get_risk_v55's SUCCESS path
(after _get_display_mileage's deletion), so it drives the full HTTP route
with a mocked DVSA fetch + mocked V55 model call.
"""
import json
import os
import sys
from asyncio import run
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
import main as main_module  # noqa: E402
import model_v55  # noqa: E402
import report_service  # noqa: E402
from report_contract import (  # noqa: E402
    DATASET_TOTAL_FAILURES,
    DATASET_TOTAL_TESTS,
    POPULATION_DEFAULT_FAILURE_RISK,
)

from report_test_helpers import make_history  # noqa: E402

client = TestClient(main_module.app)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """/api/risk/v55 is limited to 20/minute per client IP and TestClient
    uses a single fixed IP for the whole pytest process; without a reset
    this file's HTTP-level test would compete for the shared budget with
    tests/test_api.py's TestV55API (see tests/test_api_lookup_v2.py's
    identical fixture)."""
    main_module.limiter.reset()
    yield


def _rung(match_scope, total_tests=300, total_failures=75, failure_risk=0.25,
          components_available=True):
    """A normalized ladder-result dict, shape-matched to
    database.get_risk_v2_banded's documented return (the same builder
    pattern as tests/test_api_lookup_v2.py's _rung -- deliberately
    different literal values so an assertion of "these exact numbers
    carried through" can't be satisfied by coincidence)."""
    risk_values = {
        'risk_brakes': 0.12, 'risk_suspension': 0.09, 'risk_tyres': 0.07,
        'risk_steering': 0.05, 'risk_visibility': 0.03, 'risk_lamps': 0.04,
        'risk_body': 0.06,
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


def _mock_save_risk_check(monkeypatch):
    mock = AsyncMock(return_value="row-id")
    monkeypatch.setattr(database, 'save_risk_check', mock)
    return mock


# ---------------------------------------------------------------------------
# _fallback_prediction: honest degradation, exercised directly.
# ---------------------------------------------------------------------------

def test_no_make_model_returns_population_global_not_028(monkeypatch):
    """No vehicle identity at all -> the honest population_global degrade
    (dataset-wide reference rate), never the legacy hardcoded 0.28 -- and
    no ladder query even happens (there is no model_id to query with)."""
    _mock_save_risk_check(monkeypatch)
    pg_mock = _mock_postgres(monkeypatch, return_value=_rung('exact_band'))

    result = run(main_module._fallback_prediction(
        registration="AB12CDE", make="", model="", year=None,
        postcode="SW1A1AA", note="", utm_data=None,
    ))

    assert result["failure_risk"] == pytest.approx(POPULATION_DEFAULT_FAILURE_RISK)
    assert result["failure_risk"] != 0.28
    assert result["prediction_source"] == "population_global"
    assert result["cohort"]["match_level"] == "dataset"
    assert result["cohort"]["total_tests"] == DATASET_TOTAL_TESTS
    assert result["cohort"]["total_failures"] == DATASET_TOTAL_FAILURES
    assert result["cohort"]["age_band"] is None
    assert result["cohort"]["mileage_band"] is None
    assert result["vehicle"] is None
    assert result["mileage"] is None
    for key in ("brakes", "suspension", "tyres", "steering", "visibility", "lamps", "body"):
        assert result["risk_components"][key] is None, key
    assert result["repair_cost_estimate"] is None
    pg_mock.assert_not_awaited()

    serialized = json.dumps(result)
    assert "0.28" not in serialized


def test_ladder_rung_carries_real_totals_and_provenance(monkeypatch):
    """Make/model/year known -> the real found rung's totals, rate, and
    per-component risks carry straight through untouched (mileage is
    always None from this call site, so the ladder can reach age_band_only
    or model_average but never exact_band -- age_band_only exercised
    here)."""
    _mock_save_risk_check(monkeypatch)
    _mock_postgres(monkeypatch, return_value=_rung('age_band_only'))

    result = run(main_module._fallback_prediction(
        registration="AB12CDE", make="Ford", model="Fiesta", year=2018,
        postcode="SW1A1AA", note="", utm_data=None,
    ))

    assert result["failure_risk"] == pytest.approx(0.25)
    assert result["prediction_source"] == "population_broad"
    assert result["cohort"]["match_level"] == "age_band_only"
    assert result["cohort"]["total_tests"] == 300
    assert result["cohort"]["total_failures"] == 75
    assert result["vehicle"] == {"make": "FORD", "model": "FIESTA", "year": 2018}
    assert result["risk_components"]["brakes"] == pytest.approx(0.12)
    assert result["risk_components"]["suspension"] == pytest.approx(0.09)
    assert result["risk_components"]["tyres"] == pytest.approx(0.07)
    assert result["risk_components"]["steering"] == pytest.approx(0.05)
    assert result["risk_components"]["visibility"] == pytest.approx(0.03)
    assert result["risk_components"]["lamps"] == pytest.approx(0.04)
    assert result["risk_components"]["body"] == pytest.approx(0.06)
    assert result["repair_cost_estimate"] is not None
    assert result["repair_cost_estimate"]["expected"] > 0


def test_components_null_without_full_rung(monkeypatch):
    """A found rung with a null component: all 7 components stay null and
    the repair estimate stays null, even though failure_risk itself is
    known -- and none of the legacy fabricated component defaults
    (0.05/0.04/0.03/0.02) appear anywhere in the serialized response."""
    _mock_save_risk_check(monkeypatch)
    _mock_postgres(
        monkeypatch, return_value=_rung('model_average', components_available=False)
    )

    result = run(main_module._fallback_prediction(
        registration="AB12CDE", make="TESTMAKE", model="TESTMODEL", year=2018,
        postcode="", note="", utm_data=None,
    ))

    assert result["failure_risk"] == pytest.approx(0.25)  # rate itself still known
    assert result["prediction_source"] == "population_broad"
    assert result["cohort"]["match_level"] == "model_average"
    for key in ("brakes", "suspension", "tyres", "steering", "visibility", "lamps", "body"):
        assert result["risk_components"][key] is None, key
    assert result["repair_cost_estimate"] is None

    serialized = json.dumps(result)
    for fabricated in ("0.05", "0.04", "0.03", "0.02"):
        assert fabricated not in serialized, fabricated


def test_stores_down_typed_unavailable(monkeypatch):
    """Both Postgres and SQLite unreachable -> the typed-unavailable shape
    (rate/components/repair estimate all null, honest note), matching
    /api/risk's (T3) semantics exactly -- never a silently fabricated
    rate."""
    _mock_save_risk_check(monkeypatch)
    _mock_stores_down(monkeypatch)

    result = run(main_module._fallback_prediction(
        registration="AB12CDE", make="TESTMAKE", model="TESTMODEL", year=2018,
        postcode="", note="", utm_data=None,
    ))

    assert result["failure_risk"] is None
    assert result["confidence_level"] is None
    assert result["repair_cost_estimate"] is None
    for key in ("brakes", "suspension", "tyres", "steering", "visibility", "lamps", "body"):
        assert result["risk_components"][key] is None, key
    assert result["prediction_source"] == "unavailable"
    assert result["cohort"] is None
    assert result["note"] == report_service.NOTE_LOOKUP_UNAVAILABLE


def test_year_unknown_with_make_model_serves_model_average(monkeypatch):
    """Make/model known but year unknown (e.g. DVSA returned no
    manufacture_date) must still reach the evidence ladder and can be
    served a real model_average rung -- NOT the population_global degrade.

    This is the Important-finding regression test: the old gate
    (`if make and model and year is not None`) routed this exact case to
    _population_global_fallback, discarding real, ladder-reachable
    model_average evidence. database.py's model_average rung is queried
    with age_band=None (_fetch_mot_risk_aggregate(conn, model_id, None,
    None), database.py:1218) -- it needs no age band at all, so a missing
    year is not a reason to skip the ladder."""
    _mock_save_risk_check(monkeypatch)
    pg_mock = _mock_postgres(monkeypatch, return_value=_rung('model_average'))

    result = run(main_module._fallback_prediction(
        registration="AB12CDE", make="Ford", model="Fiesta", year=None,
        postcode="SW1A1AA", note="", utm_data=None,
    ))

    pg_mock.assert_awaited()  # the ladder WAS queried, unlike the no-make/model case
    assert result["prediction_source"] == "population_broad"
    assert result["prediction_source"] != "population_global"
    assert result["cohort"]["match_level"] == "model_average"
    assert result["failure_risk"] == pytest.approx(0.25)
    assert result["cohort"]["total_tests"] == 300
    assert result["cohort"]["total_failures"] == 75


def test_year_unknown_ladder_empty_falls_to_population_global(monkeypatch):
    """Make/model known, year unknown, AND the ladder legitimately has no
    rows anywhere for this model -> population_global (the checked-in
    dataset-wide reference) -- the same honest degrade as the no-make/model
    case, but reached via a real (empty) ladder query this time, not
    skipped outright. Distinguishes "ladder queried, found nothing" from
    "no identity to query the ladder with at all"."""
    _mock_save_risk_check(monkeypatch)
    pg_mock = _mock_postgres(monkeypatch, return_value=None)

    result = run(main_module._fallback_prediction(
        registration="AB12CDE", make="NoSuchMake", model="NoSuchModel", year=None,
        postcode="SW1A1AA", note="", utm_data=None,
    ))

    pg_mock.assert_awaited()  # ladder WAS queried and legitimately came back empty
    assert result["prediction_source"] == "population_global"
    assert result["cohort"]["match_level"] == "dataset"
    assert result["cohort"]["total_tests"] == DATASET_TOTAL_TESTS
    assert result["cohort"]["total_failures"] == DATASET_TOTAL_FAILURES
    assert result["failure_risk"] == pytest.approx(POPULATION_DEFAULT_FAILURE_RISK)


# ---------------------------------------------------------------------------
# /api/risk/v55 display mileage: report_service.resolve_odometer, no
# previous-reading substitution.
# ---------------------------------------------------------------------------

def _predict_risk_stub(features):
    return {
        'failure_risk': 0.31,
        'raw_probability': 0.31,
        'confidence_level': 'Medium',
        'risk_components': {
            'brakes': 0.1, 'suspension': 0.08, 'tyres': 0.05,
            'steering': 0.04, 'visibility': 0.02, 'lamps': 0.03, 'body': 0.06,
        },
    }


def _mock_v55_success_path(monkeypatch, history):
    fake_client = MagicMock()
    fake_client.is_configured = True
    fake_client.normalize_vrm = MagicMock(side_effect=lambda reg: reg.strip().upper())
    fake_client.fetch_vehicle_history = AsyncMock(return_value=history)

    monkeypatch.setattr(main_module, 'get_dvsa_client', lambda: fake_client)
    monkeypatch.setattr(model_v55, 'is_model_loaded', lambda: True)
    monkeypatch.setattr(main_module, 'engineer_features_with_stats', lambda h, postcode: {})
    monkeypatch.setattr(model_v55, 'predict_risk', _predict_risk_stub)
    _mock_save_risk_check(monkeypatch)


def test_v55_display_mileage_from_resolve_odometer(monkeypatch):
    """An anomalous (rollback) MOT history must surface mileage: None +
    mileage_anomaly: true on /api/risk/v55's SUCCESS path -- never the
    previous reading _get_display_mileage used to substitute (that helper
    is deleted by this task)."""
    history = make_history([
        ('2026-01-01', 45000, 'mi'),  # latest: mileage went DOWN
        ('2025-01-01', 50000, 'mi'),  # previous
    ])
    _mock_v55_success_path(monkeypatch, history)

    r = client.get("/api/risk/v55", params={"registration": "AB12CDE"})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["mileage"] is None
    assert body["mileage_anomaly"] is True
    assert body["mileage"] != 50000  # never the previous reading


def test_v55_display_mileage_plausible_reading_not_flagged(monkeypatch):
    """Sanity companion: a plausible reading is shown verbatim and NOT
    flagged anomalous -- proves the anomaly assertion above isn't
    vacuously true."""
    history = make_history([
        ('2026-01-01', 55000, 'mi'),
        ('2025-01-01', 45000, 'mi'),
    ])
    _mock_v55_success_path(monkeypatch, history)

    r = client.get("/api/risk/v55", params={"registration": "AB12CDE"})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["mileage"] == 55000
    assert body["mileage_anomaly"] is False


# ---------------------------------------------------------------------------
# Enforcement: the literal fabricated rate is gone from main.py, full stop.
# ---------------------------------------------------------------------------

def test_no_028_literal_in_main():
    """Enforcement test for acceptance iii: every 0.28 fabrication in
    main.py (the old hardcoded UK-average failure rate returned by
    _fallback_prediction, and _estimate_repair_cost's scaling baseline) is
    gone, with no way to silently reintroduce it."""
    main_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    with open(main_path, "r") as f:
        source = f.read()
    assert "0.28" not in source
