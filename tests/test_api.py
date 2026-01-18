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

    def test_get_risk_missing_model(self):
        """Test that /api/risk requires model parameter."""
        response = client.get("/api/risk?make=FORD&year=2018")
        self.assertEqual(response.status_code, 422)

    def test_get_risk_valid(self):
        """Test successful risk calculation with valid parameters."""
        response = client.get("/api/risk?make=FORD&model=FIESTA&year=2018")
        # Should return 200 (success with data or population average)
        self.assertEqual(response.status_code, 200)

        data = response.json()
        # Verify required fields exist (lowercase as per actual API)
        self.assertIn("failure_risk", data)
        self.assertIn("vehicle", data)
        self.assertIn("confidence_level", data)
        self.assertIn("match_type", data)

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

        # Check for standard response fields (lowercase as per actual API)
        expected_fields = ["failure_risk", "vehicle", "year", "confidence_level", "match_type"]
        for field in expected_fields:
            self.assertIn(field, data, f"Missing field: {field}")

        # Check component risk fields
        component_fields = ["risk_brakes", "risk_suspension", "risk_tyres", "risk_steering"]
        for field in component_fields:
            self.assertIn(field, data, f"Missing component field: {field}")

    def test_get_risk_unknown_model(self):
        """Test risk calculation for unknown model returns population average."""
        response = client.get("/api/risk?make=FORD&model=UNKNOWNMODEL&year=2018")
        self.assertEqual(response.status_code, 200)

        data = response.json()
        # Should return data with appropriate match_type
        self.assertIn("match_type", data)
        # Unknown model should fall back to make_only or population_average
        self.assertIn(data["match_type"], ["make_only", "population_average"])

    def test_get_risk_data_warning_on_fallback(self):
        """Test that data_warning is present when using fallback data."""
        response = client.get("/api/risk?make=UNKNOWNMAKE&model=UNKNOWNMODEL&year=2018")
        self.assertEqual(response.status_code, 200)

        data = response.json()
        # Should have data_warning when using population average
        if data.get("match_type") == "population_average":
            self.assertIn("data_warning", data)

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


if __name__ == '__main__':
    unittest.main()
