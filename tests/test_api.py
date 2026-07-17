"""
AutoSafe API Tests
==================

Tests for the AutoSafe MOT Risk Prediction API endpoints.
Updated to match actual API contract (P0-2 fix).
"""
from fastapi.testclient import TestClient
import secrets
import unittest
import os
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as main_module  # noqa: E402
from main import app
from report_test_helpers import make_history

client = TestClient(app)


class TestAPI(unittest.TestCase):

    def test_read_root(self):
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/html"))

    def test_canonical_redirect_strips_identifier_queries(self):
        response = client.get(
            "/?campaign=summer&reg=AB12CDE&postcode=SW1A1AA&vrm=AB12CDE",
            headers={"host": "autosafe.one"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response.headers["location"],
            "https://www.autosafe.one/?campaign=summer",
        )

    def test_get_makes(self):
        """Test that /api/makes returns a list of makes."""
        response = client.get("/api/makes")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsInstance(data, list)
        # If we have data, verify it's strings
        if data:
            self.assertIsInstance(data[0], str)

    def test_get_models(self):
        """Test that /api/models returns a list of models for a make."""
        response = client.get("/api/models?make=FORD")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsInstance(data, list)

    def test_get_models_missing_make(self):
        """Test that /api/models requires make parameter."""
        response = client.get("/api/models")
        self.assertEqual(response.status_code, 422)

    def test_get_risk_missing_params(self):
        """Test that /api/risk requires all parameters."""
        response = client.get("/api/risk")
        self.assertEqual(response.status_code, 422)

    def test_get_risk_missing_year(self):
        """Test that /api/risk requires year parameter."""
        response = client.get("/api/risk?make=FORD&model=FIESTA")
        self.assertEqual(response.status_code, 422)

    def test_get_risk_valid(self):
        """Test successful risk calculation with valid parameters."""
        response = client.get("/api/risk?make=FORD&model=FIESTA&year=2018")
        # Should be 200 (success with real or default data)
        self.assertEqual(response.status_code, 200)

        data = response.json()
        # Verify required fields exist (lowercase keys per actual API)
        self.assertIn("failure_risk", data)
        self.assertIn("vehicle", data)
        self.assertIn("confidence_level", data)

        # Verify failure_risk is a valid probability
        failure_risk = data.get("failure_risk")
        self.assertIsNotNone(failure_risk)
        self.assertGreaterEqual(failure_risk, 0.0)
        self.assertLessEqual(failure_risk, 1.0)

    def test_get_risk_response_structure(self):
        """Test that risk response contains expected fields when successful."""
        response = client.get("/api/risk?make=FORD&model=FIESTA&year=2018")
        self.assertEqual(response.status_code, 200)

        data = response.json()

        # Check for standard response fields (lowercase per actual API)
        expected_fields = [
            "vehicle", "year", "failure_risk", "confidence_level",
            "risk_brakes", "risk_suspension", "risk_tyres"
        ]
        for field in expected_fields:
            self.assertIn(field, data, f"Missing field: {field}")

        # Verify component risks are valid
        for comp in ["risk_brakes", "risk_suspension", "risk_tyres",
                     "risk_steering", "risk_visibility", "risk_lamps", "risk_body"]:
            if comp in data:
                self.assertGreaterEqual(data[comp], 0.0)
                self.assertLessEqual(data[comp], 1.0)

    def test_get_risk_invalid_year_too_old(self):
        """Test that years before 1990 are rejected."""
        response = client.get("/api/risk?make=FORD&model=FIESTA&year=1950")
        self.assertEqual(response.status_code, 422)

    def test_get_risk_invalid_year_future(self):
        """Test that years too far in the future are rejected."""
        response = client.get("/api/risk?make=FORD&model=FIESTA&year=2050")
        self.assertEqual(response.status_code, 422)

    def test_health_endpoint(self):
        """Test that health check endpoint works."""
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("status", data)
        self.assertIn(data["status"], ("ok", "degraded"))


class TestV55API(unittest.TestCase):
    """Tests for V55 registration-based endpoint."""

    def test_v55_missing_registration(self):
        """Test that /api/risk/v55 requires registration."""
        response = client.get("/api/risk/v55")
        self.assertEqual(response.status_code, 422)

    def test_v55_invalid_registration(self):
        """Test that invalid registration format is rejected."""
        response = client.get("/api/risk/v55?registration=!!!")
        self.assertEqual(response.status_code, 400)

    def test_v55_valid_registration(self):
        """Test V55 with valid registration (may use demo/fallback)."""
        response = client.get("/api/risk/v55?registration=AB12CDE&postcode=SW1A1AA")
        # Should be 200 or 503 (if model not loaded)
        self.assertIn(response.status_code, [200, 503])

        if response.status_code == 200:
            data = response.json()
            self.assertIn("registration", data)
            self.assertIn("failure_risk", data)
            self.assertIn("model_version", data)

    def test_v55_response_structure(self):
        """Test V55 response has expected structure."""
        response = client.get("/api/risk/v55?registration=AB12CDE")

        if response.status_code == 200:
            data = response.json()
            # Check required fields
            self.assertIn("registration", data)
            self.assertIn("failure_risk", data)
            self.assertIn("confidence_level", data)
            self.assertIn("model_version", data)

            # Check risk_components if present. Values may be honestly None
            # (R1-T4: a population_global degrade -- no make/model known,
            # e.g. this DVSA-not-configured path -- carries no fabricated
            # per-component defaults) rather than always-numeric; only
            # bounds-check the ones that are actually populated.
            if "risk_components" in data and data["risk_components"]:
                components = data["risk_components"]
                self.assertIsInstance(components, dict)
                for key, value in components.items():
                    if value is not None:
                        self.assertGreaterEqual(value, 0.0)
                        self.assertLessEqual(value, 1.0)


class TestAPIErrorHandling(unittest.TestCase):
    """Tests for error handling and edge cases."""

    def test_empty_make(self):
        """Test that empty make parameter fails validation."""
        response = client.get("/api/models?make=")
        # FastAPI may return 422 or accept empty string
        self.assertIn(response.status_code, [200, 422])

    def test_special_characters_in_make(self):
        """Test handling of special characters in make parameter."""
        response = client.get("/api/models?make=FORD%20TEST")
        self.assertEqual(response.status_code, 200)
        # Should return empty list or valid list, not crash
        self.assertIsInstance(response.json(), list)

    def test_sql_injection_attempt(self):
        """Test that SQL injection attempts are handled safely."""
        # This shouldn't cause any SQL errors - parameterized queries should handle it
        response = client.get("/api/models?make=FORD'; DROP TABLE risks;--")
        # Should return 200 with empty list, not crash
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json(), list)

    def test_very_long_make(self):
        """Test that very long make parameter is rejected."""
        long_make = "A" * 100
        response = client.get(f"/api/risk?make={long_make}&model=TEST&year=2020")
        self.assertEqual(response.status_code, 422)

    def test_very_long_model(self):
        """Test that very long model parameter is rejected."""
        long_model = "B" * 100
        response = client.get(f"/api/risk?make=FORD&model={long_model}&year=2020")
        self.assertEqual(response.status_code, 422)


class TestVehicleEndpoint(unittest.TestCase):
    """Tests for /api/vehicle endpoint."""

    def test_vehicle_missing_registration(self):
        """Test that /api/vehicle requires registration."""
        response = client.get("/api/vehicle")
        self.assertEqual(response.status_code, 422)

    def test_vehicle_valid_registration(self):
        """Test vehicle lookup with valid registration (demo mode)."""
        response = client.get("/api/vehicle?registration=AB12CDE")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("registration", data)
        self.assertIn("dvla", data)

    def test_vehicle_response_structure(self):
        """Test vehicle response has expected structure."""
        response = client.get("/api/vehicle?registration=AB12CDE")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Check DVLA data structure
        if "dvla" in data and data["dvla"]:
            dvla = data["dvla"]
            # Common fields that should be present
            expected_fields = ["make", "yearOfManufacture"]
            for field in expected_fields:
                self.assertIn(field, dvla, f"Missing DVLA field: {field}")

    def test_vehicle_response_has_typed_odometer(self):
        """DVSA path (history fetched): a dated miles reading resolves to a
        typed AVAILABLE odometer object, additive alongside the existing
        response keys (R1-T2)."""
        history = make_history([("2024-06-01", 42000, "mi")])
        fake_client = MagicMock()
        fake_client.is_configured = True
        fake_client.normalize_vrm = MagicMock(return_value="AB12CDE")
        fake_client.fetch_vehicle_history = AsyncMock(return_value=history)
        with patch("main.get_dvsa_client", return_value=fake_client):
            response = client.get("/api/vehicle?registration=AB12CDE")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Existing keys stay byte-identical alongside the new one.
        self.assertIn("registration", data)
        self.assertIn("dvla", data)
        self.assertEqual(data["source"], "dvsa")

        self.assertIn("odometer", data)
        odometer = data["odometer"]
        self.assertEqual(odometer["status"], "available")
        self.assertIsInstance(odometer["value_miles"], int)
        self.assertEqual(odometer["value_miles"], 42000)
        self.assertEqual(odometer["recorded_at"], datetime(2024, 6, 1).date().isoformat())
        self.assertEqual(odometer["original_unit"], "mi")

    def test_vehicle_demo_odometer_unavailable(self):
        """Demo/fallback path (DVSA not configured in this test env, so this
        naturally exercises the real demo path like the sibling tests
        above): an explicit UNAVAILABLE/no_reading odometer object, never a
        fabricated number (R1-T2)."""
        response = client.get("/api/vehicle?registration=AB12CDE")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertIn("odometer", data)
        odometer = data["odometer"]
        self.assertEqual(odometer["status"], "unavailable")
        self.assertEqual(odometer["unavailable_reason"], "no_reading")
        self.assertIsNone(odometer["value_miles"])


class TestVerifyAdminApiKey(unittest.TestCase):
    """_verify_admin_api_key must run secrets.compare_digest() on every
    call, even when ADMIN_API_KEY is unconfigured, so an attacker cannot
    distinguish "unconfigured" from "wrong key" by timing (B4)."""

    def test_admin_key_compare_runs_when_unconfigured(self):
        with patch.object(main_module, "ADMIN_API_KEY", None), \
             patch("secrets.compare_digest", wraps=secrets.compare_digest) as spy:
            result = main_module._verify_admin_api_key("some-provided-key")

        self.assertFalse(result)
        spy.assert_called_once()

    def test_admin_key_compare_runs_when_unconfigured_and_key_also_missing(self):
        """Both sides absent must still run the comparison, not short-circuit."""
        with patch.object(main_module, "ADMIN_API_KEY", None), \
             patch("secrets.compare_digest", wraps=secrets.compare_digest) as spy:
            result = main_module._verify_admin_api_key(None)

        self.assertFalse(result)
        spy.assert_called_once()

    def test_admin_key_valid_still_passes(self):
        with patch.object(main_module, "ADMIN_API_KEY", "secret-key-123"):
            result = main_module._verify_admin_api_key("secret-key-123")

        self.assertTrue(result)

    def test_admin_key_wrong_value_still_fails(self):
        with patch.object(main_module, "ADMIN_API_KEY", "secret-key-123"):
            result = main_module._verify_admin_api_key("wrong-key")

        self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()


class TestModelPredictionMatchScopeAccepted(unittest.TestCase):
    """Downstream submissions from a vehicle_prediction report carry
    match_scope='model_prediction'; all three shared-scope validators must
    accept it (and still reject unknown scopes)."""

    def test_risk_data_accepts_model_prediction(self):
        risk = main_module.RiskData(match_scope='model_prediction')
        self.assertEqual(risk.match_scope, 'model_prediction')

    def test_mot_reminder_accepts_model_prediction(self):
        sub = main_module.MotReminderSubmission(
            email='user@example.com',
            registration='AB12CDE',
            match_scope='model_prediction',
        )
        self.assertEqual(sub.match_scope, 'model_prediction')

    def test_report_email_accepts_model_prediction(self):
        sub = main_module.ReportEmailSubmission(
            email='user@example.com',
            registration='AB12CDE',
            failure_risk=0.31,
            match_scope='model_prediction',
        )
        self.assertEqual(sub.match_scope, 'model_prediction')

    def test_unknown_scope_still_rejected(self):
        with self.assertRaises(Exception):
            main_module.RiskData(match_scope='cohort_prediction')
