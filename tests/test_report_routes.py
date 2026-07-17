"""
Tests for the v2 report API HTTP layer (report_routes.py): request/error
handling on POST /api/v2/reports and GET /api/v2/reports/{token}, the
rate-limit dispatcher, and GET /api/version.

Async: this venv does not have pytest-asyncio installed, so the one class
that calls report_routes.rate_limit_exceeded_dispatcher directly (not
through TestClient) drives it with asyncio.run(...) inside a plain sync
test method -- the same pattern already used by
tests/test_report_service_banding.py and tests/test_database_v2.py in
this repo. Everything else goes through TestClient, which manages its own
event loop regardless.

DATABASE_URL and the DVSA_* credentials are unset in this test
environment (verified: no .env file, os.environ has none of them), so:
  - db.* calls that are NOT explicitly patched below would raise
    db.PostgresUnavailable for real (get_pool() returns None) -- the
    bad-token-shape test below deliberately exploits this to prove the
    shape guard runs before any DB access.
  - dvsa_client's real singleton has is_configured=False, so demo mode is
    the natural default for any test that doesn't patch
    report_routes.get_dvsa_client -- exploited by the demo-mode and
    persistence-happy-path tests below to exercise the real
    _build_demo_history/report_service.build_assessment pipeline instead
    of an all-mocked one.
"""
import asyncio
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import Request  # noqa: E402

import database as db  # noqa: E402
import report_routes  # noqa: E402
import report_service  # noqa: E402
from dvsa_client import DVSAAPIError, VehicleNotFoundError  # noqa: E402
from main import app  # noqa: E402
from report_contract import (  # noqa: E402
    REPORT_TTL_DAYS,
    ComponentRiskItem,
    ConfidenceLevel,
    MatchScope,
    MileageSource,
    PredictionSource,
    ReportComponents,
    ReportEvidence,
    ReportMileage,
    ReportMot,
    ReportPersistence,
    ReportRepairEstimate,
    ReportResponse,
    ReportRisk,
    ReportVehicle,
    VehicleDataSource,
)

client = TestClient(app)

# One of main.DEMO_VEHICLES' three curated entries -- gives a realistic
# make/model/year rather than the generic DEMO/VEHICLE/2020 fallback.
VALID_VRM = "AB12CDE"


def run(coro):
    return asyncio.run(coro)


def _naive_future():
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)


def _naive_past():
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)


def _fixture_assessment(**overrides):
    defaults = dict(
        vehicle=ReportVehicle(make="FORD", model="FIESTA", year=2018, fuel_type="PETROL", colour="BLUE"),
        mot=ReportMot(expiry_date="2026-01-01", last_test_date="2025-01-01", last_result="PASSED"),
        mileage=ReportMileage(
            effective_value=45000, source=MileageSource.OBSERVED_MOT,
            observed_at="2025-01-01", unit_converted=False, anomaly=False,
        ),
        evidence=ReportEvidence(
            match_scope=MatchScope.EXACT_BAND, age_band="6-10", mileage_band="30k-60k",
            total_tests=500, total_failures=100,
        ),
        risk=ReportRisk(failure_risk=0.2, confidence=ConfidenceLevel.HIGH),
        components=ReportComponents(available=True, items=[ComponentRiskItem(key="brakes", label="Brakes", risk=0.1)]),
        repair_estimate=ReportRepairEstimate(expected=250, range_low=100, range_high=500),
        prediction_source=PredictionSource.SQLITE,
        note=None,
    )
    defaults.update(overrides)
    return report_service.Assessment(**defaults)


def _fixture_response(token, **overrides):
    now = datetime.now(timezone.utc)
    defaults = dict(
        report_id="11111111-1111-1111-1111-111111111111",
        report_token=token,
        share_url=report_routes._share_url(token),
        created_at=now.isoformat(),
        expires_at=(now + timedelta(days=REPORT_TTL_DAYS)).isoformat(),
        registration="AB12CDE",
        vehicle=ReportVehicle(make="FORD", model="FIESTA", year=2018, fuel_type="PETROL", colour="BLUE"),
        mot=ReportMot(expiry_date="2026-01-01", last_test_date="2025-01-01", last_result="PASSED"),
        mileage=ReportMileage(
            effective_value=45000, source=MileageSource.OBSERVED_MOT,
            observed_at="2025-01-01", unit_converted=False, anomaly=False,
        ),
        evidence=ReportEvidence(
            match_scope=MatchScope.EXACT_BAND, age_band="6-10", mileage_band="30k-60k",
            total_tests=500, total_failures=100,
        ),
        risk=ReportRisk(failure_risk=0.2, confidence=ConfidenceLevel.HIGH),
        components=ReportComponents(available=True, items=[ComponentRiskItem(key="brakes", label="Brakes", risk=0.1)]),
        repair_estimate=ReportRepairEstimate(expected=250, range_low=100, range_high=500),
        persistence=ReportPersistence(saved=True, share_available=True),
        prediction_source=PredictionSource.SQLITE,
        vehicle_data_source=VehicleDataSource.DEMO,
        note=None,
    )
    defaults.update(overrides)
    return ReportResponse(**defaults).model_dump(mode='json')


def _legacy_fixture_response(token, **overrides):
    payload = _fixture_response(token, **overrides)
    payload.pop('result_kind')
    return payload


# ---------------------------------------------------------------------------
# POST /api/v2/reports -- error matrix
# ---------------------------------------------------------------------------

class TestCreateReportErrors(unittest.TestCase):

    def test_invalid_vrn_returns_400_envelope_with_correlation_id(self):
        resp = client.post("/api/v2/reports", json={"registration": "!!!!"})
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["error_code"], "invalid_registration")
        self.assertTrue(body.get("correlation_id"))

    def test_vehicle_not_found_returns_404(self):
        fake_client = MagicMock()
        fake_client.is_configured = True
        fake_client.fetch_vehicle_history = AsyncMock(side_effect=VehicleNotFoundError("nope"))
        with patch("report_routes.get_dvsa_client", return_value=fake_client):
            resp = client.post("/api/v2/reports", json={"registration": VALID_VRM})
        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assertEqual(body["error_code"], "vehicle_not_found")
        self.assertTrue(body.get("correlation_id"))

    def test_dvsa_unavailable_returns_503(self):
        fake_client = MagicMock()
        fake_client.is_configured = True
        fake_client.fetch_vehicle_history = AsyncMock(side_effect=DVSAAPIError("down"))
        with patch("report_routes.get_dvsa_client", return_value=fake_client):
            resp = client.post("/api/v2/reports", json={"registration": VALID_VRM})
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["error_code"], "dvsa_unavailable")

    def test_undeclared_query_param_on_post_returns_400(self):
        resp = client.post("/api/v2/reports?bogus=1", json={"registration": VALID_VRM})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error_code"], "undeclared_parameter")

    def test_undeclared_query_param_on_get_returns_400(self):
        resp = client.get("/api/v2/reports/someTokenValue123?bogus=1")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error_code"], "undeclared_parameter")

    def test_mileage_user_field_rejected_as_unknown(self):
        """mileage_user was deleted from ReportCreateRequest entirely
        (Release 1 removed the user-entered-mileage fabrication path); the
        model's extra="forbid" now rejects a client that still sends it
        with a generic 422, not the old ge/le boundary validation."""
        resp = client.post("/api/v2/reports", json={"registration": VALID_VRM, "mileage_user": 45000})
        self.assertEqual(resp.status_code, 422)

    def test_deep_unexpected_exception_returns_500_without_stack_trace(self):
        broken = AsyncMock(side_effect=RuntimeError("boom-secret-detail"))
        with patch("report_routes.report_service.build_assessment", new=broken):
            resp = client.post("/api/v2/reports", json={"registration": VALID_VRM})
        self.assertEqual(resp.status_code, 500)
        body = resp.json()
        self.assertEqual(body["error_code"], "internal_error")
        self.assertTrue(body.get("correlation_id"))
        raw = resp.text
        self.assertNotIn("boom-secret-detail", raw)
        self.assertNotIn("RuntimeError", raw)
        self.assertNotIn("Traceback", raw)


# ---------------------------------------------------------------------------
# POST /api/v2/reports -- demo-mode happy path
# ---------------------------------------------------------------------------

class TestCreateReportDemoHappyPath(unittest.TestCase):

    def test_demo_mode_source_and_observed_mileage_with_degraded_persistence(self):
        """DVSA not configured -> real demo history -> real
        report_service.build_assessment -> real resolve_mileage. Only the
        DB boundary is mocked (down), so this proves the demo-history
        builder actually produces an odometer reading observed_mot can
        resolve, end to end -- and that a persist failure degrades
        honestly (no token/id/share_url handed out)."""
        broken_save = AsyncMock(side_effect=db.PostgresUnavailable("down"))
        with patch("report_routes.db.save_report", new=broken_save):
            resp = client.post("/api/v2/reports", json={"registration": VALID_VRM})

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["result_kind"], "comparison")
        self.assertEqual(body["vehicle_data_source"], "demo")
        self.assertEqual(body["mileage"]["source"], "observed_mot")
        self.assertIsNotNone(body["mileage"]["effective_value"])
        self.assertFalse(body["persistence"]["saved"])
        self.assertFalse(body["persistence"]["share_available"])
        self.assertIsNone(body["report_token"])
        self.assertIsNone(body["report_id"])
        self.assertIsNone(body["share_url"])
        self.assertIsNone(body["expires_at"])


# ---------------------------------------------------------------------------
# POST /api/v2/reports -- persistence happy path
# ---------------------------------------------------------------------------

class TestCreateReportPersistence(unittest.TestCase):

    def test_persistence_happy_path_record_and_response(self):
        fixture = _fixture_assessment()
        fake_save = AsyncMock(return_value="row-id-123")
        with patch("report_routes.report_service.build_assessment", new=AsyncMock(return_value=fixture)), \
             patch("report_routes.db.save_report", new=fake_save):
            resp = client.post(
                "/api/v2/reports",
                json={"registration": VALID_VRM, "postcode": "SW1A 1AA"},
                headers={
                    "referer": (
                        "https://www.autosafe.one/app/report/secret-token"
                        "?reg=AB12CDE&postcode=SW1A1AA"
                    )
                },
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()

        fake_save.assert_called_once()
        record = fake_save.call_args[0][0]

        expected_keys = {
            'id',
            'registration', 'postcode', 'vehicle_make', 'vehicle_model', 'vehicle_year',
            'vehicle_fuel_type', 'mileage', 'last_mot_date', 'last_mot_result',
            'failure_risk', 'confidence_level', 'risk_components', 'repair_cost_estimate',
            'model_version', 'prediction_source', 'is_dvsa_data', 'referrer',
            'contract_version', 'report_token', 'report_payload', 'mileage_source',
            'effective_mileage', 'match_scope', 'total_tests', 'total_failures',
            'idempotency_key', 'expires_at',
        }
        self.assertEqual(set(record.keys()), expected_keys)

        self.assertEqual(record['registration'], VALID_VRM)
        self.assertEqual(record['postcode'], 'SW1A 1AA')  # DB row keeps it (LIA); response never has it
        self.assertNotIn('postcode', body)
        self.assertEqual(record['vehicle_make'], 'FORD')
        self.assertEqual(record['vehicle_model'], 'FIESTA')
        self.assertEqual(record['mileage'], 45000)
        self.assertEqual(record['failure_risk'], 0.2)
        self.assertEqual(record['confidence_level'], 'High')
        self.assertEqual(record['risk_components'], {'brakes': 0.1})
        self.assertEqual(record['repair_cost_estimate'], {'expected': 250, 'range_low': 100, 'range_high': 500})
        self.assertEqual(record['model_version'], 'lookup_v2')
        self.assertEqual(record['prediction_source'], 'sqlite')
        self.assertFalse(record['is_dvsa_data'])  # demo mode, not DVSA
        self.assertEqual(
            record['referrer'],
            "https://www.autosafe.one",
        )
        self.assertEqual(record['contract_version'], '2.0')
        self.assertEqual(record['report_token'], body['report_token'])
        self.assertEqual(record['mileage_source'], 'observed_mot')
        self.assertEqual(record['effective_mileage'], 45000)
        self.assertEqual(record['match_scope'], 'exact_band')
        self.assertEqual(record['total_tests'], 500)
        self.assertEqual(record['total_failures'], 100)
        self.assertIsNone(record['idempotency_key'])
        self.assertIsInstance(record['expires_at'], datetime)
        self.assertIsNone(record['expires_at'].tzinfo, "must be naive for asyncpg's TIMESTAMP column")
        self.assertIsInstance(record['last_mot_date'], datetime)  # real datetime, not the ISO string

        # persisted payload == returned payload, byte-for-byte INCLUDING
        # report_id: the route mints the uuid pre-insert and passes it as
        # the explicit row id (record['id']), so POST and any later GET
        # replay agree on every field (staging acceptance check 4f).
        stored_payload = record['report_payload']
        self.assertEqual(body, stored_payload)
        self.assertEqual(body['report_id'], str(record['id']))
        self.assertRegex(body['report_id'], r'^[0-9a-f-]{36}$')

        # token format + share_url composition
        self.assertRegex(body['report_token'], r'^[A-Za-z0-9_-]{10,64}$')
        self.assertEqual(body['share_url'], 'https://www.autosafe.one/app/report/' + body['report_token'])
        self.assertTrue(body['persistence']['saved'])
        self.assertTrue(body['persistence']['share_available'])

        # expires_at = created_at + 90d
        created = datetime.fromisoformat(body['created_at'])
        expires = datetime.fromisoformat(body['expires_at'])
        self.assertEqual(expires - created, timedelta(days=REPORT_TTL_DAYS))


# ---------------------------------------------------------------------------
# POST /api/v2/reports -- idempotency
# ---------------------------------------------------------------------------

class TestCreateReportIdempotency(unittest.TestCase):

    def test_idempotent_replay_returns_stored_payload_without_recomputing(self):
        stored = _fixture_response(token="already-stored-token-1")
        lookup = AsyncMock(return_value={
            'id': 'row-1', 'registration': VALID_VRM, 'report_payload': stored,
        })
        build_mock = AsyncMock(side_effect=AssertionError("build_assessment should not be called on replay"))
        dvsa_mock = MagicMock(side_effect=AssertionError("get_dvsa_client should not be called on replay"))
        save_mock = AsyncMock(side_effect=AssertionError("save_report should not be called on replay"))

        with patch("report_routes.db.get_report_by_idempotency_key", new=lookup), \
             patch("report_routes.report_service.build_assessment", new=build_mock), \
             patch("report_routes.get_dvsa_client", new=dvsa_mock), \
             patch("report_routes.db.save_report", new=save_mock):
            resp = client.post(
                "/api/v2/reports",
                json={"registration": VALID_VRM, "idempotency_key": "idem-key-1"},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), stored)
        lookup.assert_awaited_once_with("idem-key-1")
        build_mock.assert_not_called()
        dvsa_mock.assert_not_called()
        save_mock.assert_not_called()

    def test_idempotent_replay_defaults_legacy_payload_to_comparison(self):
        stored = _legacy_fixture_response(token="legacy-idempotent-token")
        lookup = AsyncMock(return_value={
            'id': 'row-legacy-idempotent',
            'registration': VALID_VRM,
            'report_payload': stored,
        })
        build_mock = AsyncMock(side_effect=AssertionError("build_assessment should not be called on replay"))
        dvsa_mock = MagicMock(side_effect=AssertionError("get_dvsa_client should not be called on replay"))
        save_mock = AsyncMock(side_effect=AssertionError("save_report should not be called on replay"))

        with patch("report_routes.db.get_report_by_idempotency_key", new=lookup), \
             patch("report_routes.report_service.build_assessment", new=build_mock), \
             patch("report_routes.get_dvsa_client", new=dvsa_mock), \
             patch("report_routes.db.save_report", new=save_mock):
            resp = client.post(
                "/api/v2/reports",
                json={"registration": VALID_VRM, "idempotency_key": "legacy-idem-key"},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {**stored, "result_kind": "comparison"})
        build_mock.assert_not_called()
        dvsa_mock.assert_not_called()
        save_mock.assert_not_called()

    def test_unique_violation_race_refetches_and_returns_winner(self):
        fixture = _fixture_assessment()
        winner_payload = _fixture_response(token="winner-token-1")
        # First call (idempotency-first check): no row yet. Second call
        # (post-UniqueViolationError race resolution): the concurrent
        # request that won the insert.
        lookup = AsyncMock(side_effect=[
            None,
            {'id': 'row-2', 'registration': VALID_VRM, 'report_payload': winner_payload},
        ])
        save_mock = AsyncMock(side_effect=asyncpg.exceptions.UniqueViolationError("duplicate key"))

        with patch("report_routes.db.get_report_by_idempotency_key", new=lookup), \
             patch("report_routes.report_service.build_assessment", new=AsyncMock(return_value=fixture)), \
             patch("report_routes.db.save_report", new=save_mock):
            resp = client.post(
                "/api/v2/reports",
                json={"registration": VALID_VRM, "idempotency_key": "idem-key-2"},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), winner_payload)
        self.assertEqual(lookup.await_count, 2)
        save_mock.assert_called_once()

    def test_idempotency_key_cannot_replay_a_different_registration(self):
        """An idempotency key scopes one normalized VRN, not every caller
        who happens to know/reuse the key."""
        other_vrm = "XY99ZZZ"
        stored = _fixture_response(token="other-vrm-token-1", registration=other_vrm)
        lookup = AsyncMock(return_value={
            'id': 'row-other', 'registration': other_vrm, 'report_payload': stored,
        })

        with patch("report_routes.db.get_report_by_idempotency_key", new=lookup), \
             patch("report_routes.report_service.build_assessment") as build_mock, \
             patch("report_routes.db.save_report") as save_mock:
            resp = client.post(
                "/api/v2/reports",
                json={"registration": VALID_VRM, "idempotency_key": "idem-key-cross-vrm-123"},
            )

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error_code"], "idempotency_conflict")
        build_mock.assert_not_called()
        save_mock.assert_not_called()

    def test_idempotency_key_cannot_resurrect_an_expired_report(self):
        stored = _fixture_response(token="expired-idempotent-token")
        lookup = AsyncMock(return_value={
            'id': 'row-expired',
            'registration': VALID_VRM,
            'report_payload': stored,
            'expires_at': _naive_past(),
            'pseudonymised_at': None,
        })

        with patch("report_routes.db.get_report_by_idempotency_key", new=lookup), \
             patch("report_routes.report_service.build_assessment") as build_mock, \
             patch("report_routes.db.save_report") as save_mock:
            resp = client.post(
                "/api/v2/reports",
                json={"registration": VALID_VRM, "idempotency_key": "expired-idem-key"},
            )

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error_code"], "idempotency_conflict")
        build_mock.assert_not_called()
        save_mock.assert_not_called()

    def test_idempotency_key_with_invalid_stored_payload_requires_a_new_key(self):
        lookup = AsyncMock(return_value={
            'id': 'row-invalid-payload',
            'registration': VALID_VRM,
            'report_payload': {'not': 'a report'},
            'expires_at': _naive_future(),
            'pseudonymised_at': None,
        })

        with patch("report_routes.db.get_report_by_idempotency_key", new=lookup), \
             patch("report_routes.report_service.build_assessment") as build_mock, \
             patch("report_routes.db.save_report") as save_mock:
            resp = client.post(
                "/api/v2/reports",
                json={"registration": VALID_VRM, "idempotency_key": "invalid-payload-key"},
            )

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error_code"], "idempotency_conflict")
        build_mock.assert_not_called()
        save_mock.assert_not_called()

    def test_report_error_log_redacts_share_token(self):
        token = "secretBearerToken12345"
        with patch("report_routes.db.get_report_by_token", new=AsyncMock(return_value=None)), \
             self.assertLogs(report_routes.logger, level="WARNING") as captured:
            resp = client.get(f"/api/v2/reports/{token}")

        self.assertEqual(resp.status_code, 404)
        combined = "\n".join(captured.output)
        self.assertNotIn(token, combined)
        self.assertIn("/api/v2/reports/{token}", combined)


# ---------------------------------------------------------------------------
# GET /api/v2/reports/{token} -- matrix
# ---------------------------------------------------------------------------

class TestGetReport(unittest.TestCase):

    def test_200_returns_stored_payload_verbatim(self):
        payload = _fixture_response(token="get-token-0001")
        row = {'id': 'row-3', 'report_payload': payload, 'expires_at': _naive_future(), 'pseudonymised_at': None}
        with patch("report_routes.db.get_report_by_token", new=AsyncMock(return_value=row)):
            resp = client.get("/api/v2/reports/get-token-0001")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["result_kind"], "comparison")
        self.assertEqual(resp.json(), payload)

    def test_200_defaults_legacy_payload_to_comparison(self):
        payload = _legacy_fixture_response(token="legacy-get-token-01")
        row = {
            'id': 'row-legacy-get',
            'report_payload': payload,
            'expires_at': _naive_future(),
            'pseudonymised_at': None,
        }
        with patch("report_routes.db.get_report_by_token", new=AsyncMock(return_value=row)):
            resp = client.get("/api/v2/reports/legacy-get-token-01")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {**payload, "result_kind": "comparison"})

    def test_404_when_token_unknown(self):
        with patch("report_routes.db.get_report_by_token", new=AsyncMock(return_value=None)):
            resp = client.get("/api/v2/reports/unknown-token-000001")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error_code"], "report_not_found")

    def test_410_when_expired(self):
        past = datetime(2020, 1, 1)  # naive, matches what asyncpg actually returns
        payload = _fixture_response(token="expired-token-001")
        row = {'id': 'row-4', 'report_payload': payload, 'expires_at': past, 'pseudonymised_at': None}
        with patch("report_routes.db.get_report_by_token", new=AsyncMock(return_value=row)):
            resp = client.get("/api/v2/reports/expired-token-001")
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.json()["error_code"], "report_expired")

    def test_410_when_expires_at_is_tz_aware_and_past(self):
        """Defensive aware/naive normalization: the write path only ever
        produces naive datetimes (Postgres TIMESTAMP, verified against
        asyncpg's codec), but the comparison must not raise TypeError even
        if a tz-aware value ever appears here (e.g. a future TIMESTAMPTZ
        migration)."""
        past_aware = datetime(2020, 1, 1, tzinfo=timezone.utc)
        payload = _fixture_response(token="expired-aware-tok1")
        row = {'id': 'row-4b', 'report_payload': payload, 'expires_at': past_aware, 'pseudonymised_at': None}
        with patch("report_routes.db.get_report_by_token", new=AsyncMock(return_value=row)):
            resp = client.get("/api/v2/reports/expired-aware-tok1")
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.json()["error_code"], "report_expired")

    def test_410_when_pseudonymised(self):
        payload = _fixture_response(token="pseudo-token-00001")
        row = {
            'id': 'row-5', 'report_payload': payload,
            'expires_at': _naive_future(), 'pseudonymised_at': datetime(2026, 1, 1),
        }
        with patch("report_routes.db.get_report_by_token", new=AsyncMock(return_value=row)):
            resp = client.get("/api/v2/reports/pseudo-token-00001")
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.json()["error_code"], "report_expired")

    def test_410_when_payload_none(self):
        row = {'id': 'row-6', 'report_payload': None, 'expires_at': _naive_future(), 'pseudonymised_at': None}
        with patch("report_routes.db.get_report_by_token", new=AsyncMock(return_value=row)):
            resp = client.get("/api/v2/reports/nullpayload-token001")
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.json()["error_code"], "report_expired")

    def test_503_when_postgres_unavailable(self):
        broken = AsyncMock(side_effect=db.PostgresUnavailable("down"))
        with patch("report_routes.db.get_report_by_token", new=broken):
            resp = client.get("/api/v2/reports/anytoken0000000001")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["error_code"], "storage_unavailable")

    def test_bad_token_shape_returns_404_without_hitting_db(self):
        # db.get_report_by_token deliberately left unpatched: DATABASE_URL
        # is unset in this test env, so a real call would raise
        # PostgresUnavailable -> 503, not 404. Getting 404 is itself proof
        # the shape guard runs before any DB access.
        resp = client.get("/api/v2/reports/short")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error_code"], "report_not_found")


# ---------------------------------------------------------------------------
# Rate-limit dispatcher
# ---------------------------------------------------------------------------

class TestRateLimitDispatcher(unittest.TestCase):

    def test_v2_path_returns_error_envelope(self):
        fake_request = MagicMock()
        fake_request.url.path = "/api/v2/reports"
        fake_exc = MagicMock()
        resp = run(report_routes.rate_limit_exceeded_dispatcher(fake_request, fake_exc))
        self.assertEqual(resp.status_code, 429)
        body = json.loads(resp.body)
        self.assertEqual(body["error_code"], "rate_limited")
        self.assertTrue(body.get("correlation_id"))

    def test_legacy_path_delegates_to_slowapi_default(self):
        fake_request = MagicMock()
        fake_request.url.path = "/api/risk"
        fake_exc = MagicMock()
        sentinel = object()
        with patch("report_routes._rate_limit_exceeded_handler", return_value=sentinel) as mocked:
            result = run(report_routes.rate_limit_exceeded_dispatcher(fake_request, fake_exc))
        mocked.assert_called_once_with(fake_request, fake_exc)
        self.assertIs(result, sentinel)

    def test_legacy_shape_end_to_end_via_isolated_app(self):
        """One real TestClient integration proving the legacy branch's
        byte-shape end to end, using a throwaway app + Limiter isolated
        from main.app's process-lifetime shared rate-limit state (hitting
        a real main.app route hard enough to trip its limiter would
        permanently consume part of that route's budget for every other
        test in the same pytest process)."""
        from slowapi import Limiter
        from slowapi.errors import RateLimitExceeded as _RLE

        isolated_app = FastAPI()
        isolated_limiter = Limiter(key_func=lambda request: "fixed-test-key")
        isolated_app.state.limiter = isolated_limiter
        isolated_app.add_exception_handler(_RLE, report_routes.rate_limit_exceeded_dispatcher)

        @isolated_app.get("/legacy-style-route")
        @isolated_limiter.limit("1/minute")
        async def _dummy(request: Request):
            return {"ok": True}

        isolated_client = TestClient(isolated_app)
        first = isolated_client.get("/legacy-style-route")
        self.assertEqual(first.status_code, 200)
        second = isolated_client.get("/legacy-style-route")
        self.assertEqual(second.status_code, 429)
        body = second.json()
        self.assertEqual(set(body.keys()), {"error"})
        self.assertTrue(body["error"].startswith("Rate limit exceeded"))


# ---------------------------------------------------------------------------
# GET /api/version
# ---------------------------------------------------------------------------

class TestVersionEndpoint(unittest.TestCase):

    def test_version_shape(self):
        resp = client.get("/api/version")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(
            set(body.keys()),
            {
                "backend_sha", "frontend_sha", "frontend_bundle_hash",
                "contract_version", "app_version", "build_timestamp", "started_at",
            },
        )
        self.assertEqual(body["app_version"], "2.0.0")
        self.assertEqual(body["contract_version"], "2.0")
        self.assertIsInstance(body["backend_sha"], str)
        self.assertIsInstance(body["frontend_sha"], str)
        fbh = body["frontend_bundle_hash"]
        self.assertTrue(
            fbh is None
            or (
                isinstance(fbh, str)
                and len(fbh) == 64
                and all(char in "0123456789abcdef" for char in fbh)
            )
        )
        if body["build_timestamp"] is not None:
            datetime.fromisoformat(body["build_timestamp"].replace("Z", "+00:00"))
        datetime.fromisoformat(body["started_at"])  # parses as ISO8601


# ---------------------------------------------------------------------------
# Legacy route smoke test
# ---------------------------------------------------------------------------

class TestLegacyRouteUnaffected(unittest.TestCase):

    def test_legacy_risk_endpoint_still_works(self):
        resp = client.get("/api/risk?make=FORD&model=FIESTA&year=2018")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for key in ("failure_risk", "vehicle", "confidence_level"):
            self.assertIn(key, body)


if __name__ == '__main__':
    unittest.main()


# ---------------------------------------------------------------------------
# V55 prediction integration (POST /api/v2/reports)
# ---------------------------------------------------------------------------

def _dvsa_history():
    from report_test_helpers import make_history
    return make_history([('2025-03-01', 41000, 'mi'), ('2024-03-01', 36000, 'mi')])


def _fake_dvsa_client(history):
    fake = MagicMock()
    fake.is_configured = True
    fake.fetch_vehicle_history = AsyncMock(return_value=history)
    return fake


V55_PREDICTION = {
    'failure_risk': 0.31,
    'raw_probability': 0.29,
    'confidence_level': 'Medium',
    'risk_components': {
        'brakes': 0.18, 'suspension': 0.07, 'tyres': 0.09, 'steering': 0.02,
        'visibility': 0.03, 'lamps': 0.05, 'body': 0.02,
    },
}


class PredictionRouteCase(unittest.TestCase):
    """Base: configured DVSA client, model mocked healthy, Postgres save
    mocked. Runs the REAL prediction_service between route and model."""

    def setUp(self):
        # This file's POST volume now exceeds the route's 20/minute limit;
        # reset the shared limiter per-test (same pattern as
        # tests/test_fallback_v55.py::_reset_rate_limiter).
        app.state.limiter.reset()
        self.history = _dvsa_history()
        self.fake_client = _fake_dvsa_client(self.history)

        patchers = [
            patch.dict(os.environ, {'PREDICTIONS_ENABLED': 'true'}),
            patch('report_routes.get_dvsa_client', return_value=self.fake_client),
            patch('prediction_service.model_v55.is_model_loaded', return_value=True),
            patch('prediction_service.model_v55.engineer_features_with_stats',
                  return_value={'feature': 1}),
            patch('prediction_service.model_v55.predict_risk',
                  return_value=dict(V55_PREDICTION)),
            patch('report_routes.db.save_report', new=AsyncMock(return_value='row-id-123')),
        ]
        mocks = [p.start() for p in patchers]
        for p in patchers:
            self.addCleanup(p.stop)
        self.mock_engineer = mocks[3]
        self.mock_predict = mocks[4]
        self.mock_save = mocks[5]

    def _post(self):
        return client.post(
            '/api/v2/reports',
            json={'registration': VALID_VRM, 'postcode': 'SW1A 1AA'},
        )


class TestCreateReportPredictionSuccess(PredictionRouteCase):

    def test_success_semantics_and_single_calls(self):
        comparison_spy = AsyncMock(side_effect=AssertionError('comparison path must not run'))
        with patch('report_routes.report_service.build_assessment', new=comparison_spy):
            resp = self._post()

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['result_kind'], 'vehicle_prediction')
        self.assertEqual(body['prediction_source'], 'model_v55')
        self.assertEqual(body['evidence']['match_scope'], 'model_prediction')
        self.assertIsNone(body['evidence']['total_tests'])
        self.assertIsNone(body['evidence']['age_band'])
        self.assertIsNone(body['note'])
        self.assertEqual(body['risk']['failure_risk'], 0.31)

        # Exactly one DVSA fetch, one feature build, one inference.
        self.assertEqual(self.fake_client.fetch_vehicle_history.await_count, 1)
        self.assertEqual(self.mock_engineer.call_count, 1)
        self.assertEqual(self.mock_predict.call_count, 1)
        comparison_spy.assert_not_awaited()
        # The route's normalized postcode reaches feature engineering.
        self.assertEqual(self.mock_engineer.call_args.args[1], 'SW1A 1AA')

    def test_persisted_record_and_payload_identity(self):
        resp = self._post()
        body = resp.json()

        record = self.mock_save.call_args[0][0]
        self.assertEqual(record['model_version'], 'v55')
        self.assertEqual(record['prediction_source'], 'model_v55')
        self.assertEqual(record['match_scope'], 'model_prediction')
        self.assertIsNone(record['total_tests'])
        self.assertIsNone(record['total_failures'])
        self.assertTrue(record['is_dvsa_data'])
        self.assertEqual(record['failure_risk'], 0.31)
        self.assertEqual(record['risk_components']['brakes'], 0.18)
        # Byte-identity invariant holds for predictions too.
        self.assertEqual(body, record['report_payload'])
        self.assertEqual(body['report_id'], str(record['id']))

    def test_degraded_persistence_keeps_prediction_semantics(self):
        with patch('report_routes.db.save_report',
                   new=AsyncMock(side_effect=db.PostgresUnavailable('down'))):
            resp = self._post()
        body = resp.json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(body['result_kind'], 'vehicle_prediction')
        self.assertFalse(body['persistence']['saved'])
        self.assertIsNone(body['report_token'])


class TestCreateReportPredictionFallback(PredictionRouteCase):

    def _assert_comparison_fallback(self, resp, expected_reason):
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['result_kind'], 'comparison')
        self.assertNotEqual(body['prediction_source'], 'model_v55')
        self.assertNotEqual(body['evidence']['match_scope'], 'model_prediction')
        record = self.mock_save.call_args[0][0]
        self.assertEqual(record['model_version'], 'lookup_v2')
        return body

    def test_kill_switch_falls_back_to_comparison(self):
        with patch.dict(os.environ, {'PREDICTIONS_ENABLED': 'false'}):
            with self.assertLogs('report_routes', level='WARNING') as logs:
                resp = self._post()
        self._assert_comparison_fallback(resp, 'PredictionDisabled')
        joined = '\n'.join(logs.output)
        self.assertIn('reason=PredictionDisabled', joined)
        self.assertNotIn(VALID_VRM, joined)
        self.assertNotIn('SW1A', joined)

    def test_model_unloaded_falls_back(self):
        with patch('prediction_service.model_v55.is_model_loaded', return_value=False):
            with self.assertLogs('report_routes', level='WARNING') as logs:
                resp = self._post()
        self._assert_comparison_fallback(resp, 'ModelUnavailable')
        self.assertIn('reason=ModelUnavailable', '\n'.join(logs.output))

    def test_feature_failure_falls_back(self):
        self.mock_engineer.side_effect = KeyError('boom')
        with self.assertLogs('report_routes', level='WARNING') as logs:
            resp = self._post()
        self._assert_comparison_fallback(resp, 'FeatureEngineeringFailed')
        self.assertIn('reason=FeatureEngineeringFailed', '\n'.join(logs.output))

    def test_inference_failure_falls_back(self):
        self.mock_predict.side_effect = RuntimeError('boom')
        with self.assertLogs('report_routes', level='WARNING') as logs:
            resp = self._post()
        self._assert_comparison_fallback(resp, 'InferenceFailed')
        self.assertIn('reason=InferenceFailed', '\n'.join(logs.output))

    def test_fallback_report_is_still_saved_and_shareable(self):
        self.mock_predict.side_effect = RuntimeError('boom')
        resp = self._post()
        body = resp.json()
        self.assertTrue(body['persistence']['saved'])
        self.assertTrue(body['persistence']['share_available'])
        self.assertIsNotNone(body['report_token'])

    def test_unexpected_programming_error_is_a_500_not_a_fallback(self):
        comparison_spy = AsyncMock(side_effect=AssertionError('must not fall back'))
        with patch('report_routes.prediction_service.build_v55_assessment',
                   side_effect=TypeError('contract bug')), \
             patch('report_routes.report_service.build_assessment', new=comparison_spy):
            resp = self._post()
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()['error_code'], 'internal_error')
        comparison_spy.assert_not_awaited()


class TestCreateReportPredictionGates(unittest.TestCase):

    def setUp(self):
        app.state.limiter.reset()

    def test_demo_source_never_attempts_prediction(self):
        # No DVSA client configured -> demo history. Prediction must not run.
        prediction_spy = MagicMock(side_effect=AssertionError('demo must not predict'))
        with patch('report_routes.prediction_service.build_v55_assessment', prediction_spy), \
             patch('report_routes.db.save_report', new=AsyncMock(return_value='row-id-123')):
            resp = client.post('/api/v2/reports', json={'registration': VALID_VRM})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['result_kind'], 'comparison')
        self.assertEqual(body['vehicle_data_source'], 'demo')
        prediction_spy.assert_not_called()

    def test_zero_history_vehicle_never_attempts_prediction(self):
        # Real DVSA data but an empty mot_tests list: the prediction copy
        # claims recorded-history provenance, so a zero-history vehicle must
        # take the comparison path instead.
        from report_test_helpers import make_history
        empty_history = make_history([])
        fake_client = _fake_dvsa_client(empty_history)
        prediction_spy = MagicMock(side_effect=AssertionError('zero history must not predict'))
        with patch('report_routes.get_dvsa_client', return_value=fake_client), \
             patch('report_routes.prediction_service.build_v55_assessment', prediction_spy), \
             patch('report_routes.db.save_report', new=AsyncMock(return_value='row-id-123')):
            resp = client.post('/api/v2/reports', json={'registration': VALID_VRM})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['result_kind'], 'comparison')
        self.assertEqual(body['vehicle_data_source'], 'dvsa')
        prediction_spy.assert_not_called()

    def test_idempotent_replay_of_prediction_never_reruns_the_model(self):
        token = 'replay-token-1234'
        stored = _fixture_response(
            token,
            result_kind='vehicle_prediction',
            prediction_source=PredictionSource.MODEL_V55,
            evidence=ReportEvidence(
                match_scope=MatchScope.MODEL_PREDICTION,
                age_band=None, mileage_band=None,
                total_tests=None, total_failures=None,
            ),
        )
        fake_get = AsyncMock(return_value={
            'report_payload': stored,
            'registration': VALID_VRM,
            'expires_at': _naive_future(),
            'pseudonymised_at': None,
        })
        prediction_spy = MagicMock(side_effect=AssertionError('replay must not predict'))
        with patch('report_routes.db.get_report_by_idempotency_key', new=fake_get), \
             patch('report_routes.prediction_service.build_v55_assessment', prediction_spy):
            resp = client.post(
                '/api/v2/reports',
                json={'registration': VALID_VRM, 'idempotency_key': 'idem-key-1'},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['result_kind'], 'vehicle_prediction')
        self.assertEqual(body['evidence']['match_scope'], 'model_prediction')
        prediction_spy.assert_not_called()
