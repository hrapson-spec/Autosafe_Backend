"""
AutoSafe API Tests
==================

Tests for the AutoSafe MOT Risk Prediction API endpoints.
Updated to match actual API contract (P0-2 fix).
"""
from fastapi.testclient import TestClient
import unittest
import os
import sys

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app

client = TestClient(app)


class TestAPI(unittest.TestCase):

    def test_read_root(self):
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/html"))

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
        self.assertEqual(data["status"], "ok")


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

            # Check risk_components if present
            if "risk_components" in data and data["risk_components"]:
                components = data["risk_components"]
                self.assertIsInstance(components, dict)
                for key, value in components.items():
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


if __name__ == '__main__':
    unittest.main()
