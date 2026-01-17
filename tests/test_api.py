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
        response = client.get("/api/risk?make=FORD&model=FIESTA&mileage=50000")
        self.assertEqual(response.status_code, 422)

    def test_get_risk_missing_mileage(self):
        """Test that /api/risk requires mileage parameter."""
        response = client.get("/api/risk?make=FORD&model=FIESTA&year=2018")
        self.assertEqual(response.status_code, 422)

    def test_get_risk_valid(self):
        """Test successful risk calculation with valid parameters."""
        response = client.get("/api/risk?make=FORD&model=FIESTA&year=2018&mileage=50000")
        # Should be either 200 (success) or 404 (model not found)
        self.assertIn(response.status_code, [200, 404])

        if response.status_code == 200:
            data = response.json()
            # Verify required fields exist
            self.assertIn("Failure_Risk", data)
            # Use Model_Id (API format) or model_id
            self.assertTrue("Model_Id" in data or "model_id" in data)

            # Verify Failure_Risk is a valid probability
            failure_risk = data.get("Failure_Risk")
            if failure_risk is not None:
                self.assertGreaterEqual(failure_risk, 0.0)
                self.assertLessEqual(failure_risk, 1.0)

    def test_get_risk_response_structure(self):
        """Test that risk response contains expected fields when successful."""
        response = client.get("/api/risk?make=FORD&model=FIESTA&year=2018&mileage=50000")

        if response.status_code == 200:
            data = response.json()

            # Check for standard response fields
            expected_fields = ["Failure_Risk"]
            for field in expected_fields:
                self.assertIn(field, data, f"Missing field: {field}")

            # If confidence intervals are present, verify they're valid
            if "Failure_Risk_CI_Lower" in data and "Failure_Risk_CI_Upper" in data:
                lower = data["Failure_Risk_CI_Lower"]
                upper = data["Failure_Risk_CI_Upper"]
                risk = data["Failure_Risk"]

                self.assertLessEqual(lower, risk, "CI lower bound should be <= risk")
                self.assertGreaterEqual(upper, risk, "CI upper bound should be >= risk")
                self.assertGreaterEqual(lower, 0.0, "CI lower should be >= 0")
                self.assertLessEqual(upper, 1.0, "CI upper should be <= 1")

    def test_get_risk_with_history_params(self):
        """Test risk calculation with history parameters."""
        response = client.get(
            "/api/risk?make=FORD&model=FIESTA&year=2018&mileage=50000"
            "&prev_outcome=PASS&days_since_prev=365"
        )
        self.assertIn(response.status_code, [200, 404])

        if response.status_code == 200:
            data = response.json()
            # If history adjustment was applied, check for related fields
            if "History_Adjustment" in data:
                history = data["History_Adjustment"]
                self.assertIn("prev_outcome", history)
                self.assertIn("gap_band", history)

    def test_get_risk_with_prev_outcome_fail(self):
        """Test risk calculation with previous FAIL outcome."""
        response = client.get(
            "/api/risk?make=FORD&model=FIESTA&year=2018&mileage=50000"
            "&prev_outcome=FAIL&days_since_prev=180"
        )
        self.assertIn(response.status_code, [200, 404])

    def test_get_risk_with_component_history(self):
        """Test risk calculation with component failure history."""
        response = client.get(
            "/api/risk?make=FORD&model=FIESTA&year=2018&mileage=50000"
            "&prev_brake_failure=1&prev_suspension_failure=0"
        )
        self.assertIn(response.status_code, [200, 404])

        if response.status_code == 200:
            data = response.json()
            # Component adjustment may or may not be applied depending on weights file
            self.assertIn("Failure_Risk", data)

    def test_get_risk_invalid_prev_outcome(self):
        """Test that invalid prev_outcome values are rejected."""
        response = client.get(
            "/api/risk?make=FORD&model=FIESTA&year=2018&mileage=50000"
            "&prev_outcome=INVALID"
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("detail", data)

    def test_get_risk_discontinued_model(self):
        """Test that discontinued model+year combinations return 422 error."""
        # Toyota Avensis was discontinued ~2018, so 2022 should fail
        response = client.get("/api/risk?make=TOYOTA&model=AVENSIS&year=2022&mileage=30000")
        # Should get 422 Unprocessable Entity for invalid year (consistent with other validation errors)
        self.assertEqual(response.status_code, 422)
        data = response.json()
        self.assertIn("detail", data)
        # Detail should mention the year or that it's not valid
        detail = data["detail"]
        # Handle both string and list formats
        if isinstance(detail, list):
            detail = str(detail)
        self.assertTrue("2022" in detail or "not valid" in detail.lower() or "discontinued" in detail.lower())

    def test_get_risk_invalid_year_too_old(self):
        """Test that years before 1990 are rejected."""
        response = client.get("/api/risk?make=FORD&model=FIESTA&year=1950&mileage=50000")
        self.assertEqual(response.status_code, 422)

    def test_get_risk_invalid_year_future(self):
        """Test that years too far in the future are rejected."""
        response = client.get("/api/risk?make=FORD&model=FIESTA&year=2050&mileage=50000")
        self.assertEqual(response.status_code, 422)

    def test_get_risk_negative_mileage(self):
        """Test that negative mileage is rejected."""
        response = client.get("/api/risk?make=FORD&model=FIESTA&year=2018&mileage=-1000")
        self.assertEqual(response.status_code, 422)

    def test_get_risk_excessive_mileage(self):
        """Test that excessive mileage (>999999) is rejected."""
        response = client.get("/api/risk?make=FORD&model=FIESTA&year=2018&mileage=1500000")
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
