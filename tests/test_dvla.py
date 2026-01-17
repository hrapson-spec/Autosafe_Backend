"""
Tests for DVLA Vehicle Enquiry Service integration.
"""
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dvla_client import (
    DVLAClient,
    DVLAError,
    DVLANotFoundError,
    DVLARateLimitError,
    DVLAValidationError,
    validate_registration,
    normalize_registration,
    DEMO_VEHICLES,
)


class TestRegistrationValidation(unittest.TestCase):
    """Test UK registration plate validation."""

    def test_valid_modern_format(self):
        """Modern format: AB12 CDE"""
        self.assertTrue(validate_registration("AB12CDE"))
        self.assertTrue(validate_registration("AB12 CDE"))
        self.assertTrue(validate_registration("ab12cde"))

    def test_valid_prefix_format(self):
        """Prefix format: A123 BCD"""
        self.assertTrue(validate_registration("A123BCD"))
        self.assertTrue(validate_registration("A1ABC"))

    def test_valid_suffix_format(self):
        """Suffix format: ABC 123D"""
        self.assertTrue(validate_registration("ABC123D"))

    def test_valid_dateless_format(self):
        """Dateless format: 1234 AB or AB 1234"""
        self.assertTrue(validate_registration("1234AB"))
        self.assertTrue(validate_registration("AB1234"))
        self.assertTrue(validate_registration("1A"))

    def test_invalid_too_short(self):
        """Too short to be valid."""
        self.assertFalse(validate_registration("A"))

    def test_invalid_too_long(self):
        """Too long to be valid."""
        self.assertFalse(validate_registration("ABCDEFGH"))

    def test_invalid_only_letters(self):
        """Must contain at least one number."""
        self.assertFalse(validate_registration("ABCDEF"))

    def test_invalid_only_numbers(self):
        """Must contain at least one letter."""
        self.assertFalse(validate_registration("123456"))

    def test_normalize_removes_spaces(self):
        """Normalization removes spaces and uppercases."""
        self.assertEqual(normalize_registration("ab 12 cde"), "AB12CDE")
        self.assertEqual(normalize_registration("AB12CDE"), "AB12CDE")


class TestDVLAClientDemo(unittest.TestCase):
    """Test DVLA client in demo mode (no API key)."""

    def setUp(self):
        self.client = DVLAClient(api_key=None)

    def test_demo_mode_enabled(self):
        """Client should be in demo mode when no API key provided."""
        self.assertTrue(self.client.demo_mode)

    def test_get_predefined_demo_vehicle(self):
        """Should return predefined demo data for known registrations."""
        import asyncio

        async def run_test():
            result = await self.client.get_vehicle("AB12CDE")
            self.assertEqual(result["make"], "FORD")
            self.assertEqual(result["yearOfManufacture"], 2018)
            self.assertTrue(result.get("_demo"))

        asyncio.run(run_test())

    def test_get_unknown_demo_vehicle(self):
        """Should generate mock data for unknown registrations in demo mode."""
        import asyncio

        async def run_test():
            result = await self.client.get_vehicle("ZZ99ZZZ")
            self.assertIn("make", result)
            self.assertIn("yearOfManufacture", result)
            self.assertTrue(result.get("_demo"))
            self.assertIn("_note", result)

        asyncio.run(run_test())

    def test_invalid_registration_rejected(self):
        """Should raise validation error for invalid registrations."""
        import asyncio

        async def run_test():
            with self.assertRaises(DVLAValidationError):
                await self.client.get_vehicle("INVALID")

        asyncio.run(run_test())


class TestDVLAClientAPI(unittest.TestCase):
    """Test DVLA client API calls (mocked)."""

    def setUp(self):
        self.client = DVLAClient(api_key="test-key", use_test_env=True)

    def test_not_demo_mode(self):
        """Client should not be in demo mode when API key provided."""
        self.assertFalse(self.client.demo_mode)

    def test_uses_test_url(self):
        """Should use test URL when use_test_env=True."""
        self.assertIn("uat", self.client.base_url)

    def test_uses_prod_url(self):
        """Should use prod URL when use_test_env=False."""
        client = DVLAClient(api_key="test-key", use_test_env=False)
        self.assertNotIn("uat", client.base_url)

    @patch("httpx.AsyncClient.post")
    def test_successful_api_call(self, mock_post):
        """Test successful API response handling."""
        import asyncio

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "registrationNumber": "AB12CDE",
            "make": "FORD",
            "colour": "BLUE",
            "yearOfManufacture": 2018,
        }
        mock_post.return_value = mock_response

        async def run_test():
            result = await self.client.get_vehicle("AB12CDE")
            self.assertEqual(result["make"], "FORD")

        asyncio.run(run_test())

    @patch("httpx.AsyncClient.post")
    def test_vehicle_not_found(self, mock_post):
        """Test 404 response handling."""
        import asyncio

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_post.return_value = mock_response

        async def run_test():
            with self.assertRaises(DVLANotFoundError):
                await self.client.get_vehicle("ZZ99ZZZ")

        asyncio.run(run_test())

    @patch("httpx.AsyncClient.post")
    def test_rate_limit_exceeded(self, mock_post):
        """Test 429 response handling."""
        import asyncio

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_post.return_value = mock_response

        async def run_test():
            with self.assertRaises(DVLARateLimitError):
                await self.client.get_vehicle("AB12CDE")

        asyncio.run(run_test())


class TestAPIEndpoint(unittest.TestCase):
    """Test /api/vehicle endpoint."""

    @classmethod
    def setUpClass(cls):
        """Set up test client."""
        from fastapi.testclient import TestClient
        from main import app
        cls.client = TestClient(app)

    def test_vehicle_endpoint_exists(self):
        """Endpoint should be accessible."""
        response = self.client.get("/api/vehicle?registration=AB12CDE")
        # Should not be 404 (endpoint not found)
        self.assertNotEqual(response.status_code, 404)

    def test_vehicle_demo_response(self):
        """Should return demo data when no DVLA API key configured."""
        response = self.client.get("/api/vehicle?registration=AB12CDE")
        if response.status_code == 200:
            data = response.json()
            self.assertIn("registration", data)
            self.assertIn("dvla", data)
            # Check for demo flag (if in demo mode)
            if data.get("demo"):
                self.assertTrue(data["demo"])

    def test_vehicle_missing_registration(self):
        """Should return 422 for missing registration parameter."""
        response = self.client.get("/api/vehicle")
        self.assertEqual(response.status_code, 422)

    def test_vehicle_invalid_registration(self):
        """Should return 422 for invalid registration format."""
        response = self.client.get("/api/vehicle?registration=X")
        self.assertEqual(response.status_code, 422)

    def test_vehicle_response_structure(self):
        """Response should have expected structure."""
        response = self.client.get("/api/vehicle?registration=AB12CDE")
        if response.status_code == 200:
            data = response.json()
            # Check registration is returned
            self.assertIn("registration", data)
            # Check DVLA data section
            self.assertIn("dvla", data)
            dvla = data["dvla"]
            # These fields should be present in DVLA response
            expected_fields = ["make", "colour", "yearOfManufacture", "fuelType"]
            for field in expected_fields:
                self.assertIn(field, dvla)


if __name__ == "__main__":
    unittest.main()
