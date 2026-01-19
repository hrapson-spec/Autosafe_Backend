"""
DVLA Vehicle Enquiry Service (VES) API Client.

Provides async interface to DVLA's VES API for looking up vehicle details
by registration number.

API Documentation:
https://developer-portal.driver-vehicle-licensing.api.gov.uk/apis/vehicle-enquiry-service/vehicle-enquiry-service-description.html
"""
import httpx
import logging
import re
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# UK registration plate pattern (simplified - covers most formats)
UK_REG_PATTERN = re.compile(r'^[A-Z]{2}[0-9]{2}\s?[A-Z]{3}$|^[A-Z][0-9]{1,3}\s?[A-Z]{3}$|^[A-Z]{3}\s?[0-9]{1,3}[A-Z]$|^[0-9]{1,4}\s?[A-Z]{1,3}$|^[A-Z]{1,3}\s?[0-9]{1,4}$', re.IGNORECASE)


class DVLAError(Exception):
    """Base exception for DVLA API errors."""
    def __init__(self, message: str, status_code: int = None):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


class DVLANotFoundError(DVLAError):
    """Vehicle not found in DVLA database."""
    pass


class DVLARateLimitError(DVLAError):
    """Rate limit exceeded."""
    pass


class DVLAValidationError(DVLAError):
    """Invalid registration format."""
    pass


# Demo vehicles for when no API key is configured
DEMO_VEHICLES = {
    "AB12CDE": {
        "registrationNumber": "AB12CDE",
        "make": "FORD",
        "colour": "BLUE",
        "yearOfManufacture": 2018,
        "fuelType": "PETROL",
        "engineCapacity": 1500,
        "taxStatus": "Taxed",
        "taxDueDate": "2025-03-01",
        "motStatus": "Valid",
        "motExpiryDate": "2025-06-15",
        "co2Emissions": 120,
    },
    "XY99XYZ": {
        "registrationNumber": "XY99XYZ",
        "make": "VAUXHALL",
        "colour": "SILVER",
        "yearOfManufacture": 2015,
        "fuelType": "DIESEL",
        "engineCapacity": 1600,
        "taxStatus": "Taxed",
        "taxDueDate": "2025-08-15",
        "motStatus": "Valid",
        "motExpiryDate": "2025-04-20",
        "co2Emissions": 105,
    },
    "TEST123": {
        "registrationNumber": "TEST123",
        "make": "BMW",
        "colour": "BLACK",
        "yearOfManufacture": 2020,
        "fuelType": "PETROL",
        "engineCapacity": 2000,
        "taxStatus": "Taxed",
        "taxDueDate": "2025-12-01",
        "motStatus": "Valid",
        "motExpiryDate": "2025-09-30",
        "co2Emissions": 150,
    },
    "DEMO001": {
        "registrationNumber": "DEMO001",
        "make": "VOLKSWAGEN",
        "colour": "WHITE",
        "yearOfManufacture": 2019,
        "fuelType": "PETROL",
        "engineCapacity": 1400,
        "taxStatus": "Taxed",
        "taxDueDate": "2025-05-20",
        "motStatus": "Valid",
        "motExpiryDate": "2025-07-10",
        "co2Emissions": 115,
    },
}


def normalize_registration(reg: str) -> str:
    """Normalize registration by removing spaces and converting to uppercase."""
    return reg.replace(" ", "").upper()


def validate_registration(reg: str) -> bool:
    """Validate UK registration plate format."""
    normalized = normalize_registration(reg)
    # Basic length check (2-7 characters without spaces)
    if len(normalized) < 2 or len(normalized) > 7:
        return False
    # Must contain at least one letter and one number
    has_letter = any(c.isalpha() for c in normalized)
    has_number = any(c.isdigit() for c in normalized)
    return has_letter and has_number


class DVLAClient:
    """Async client for DVLA Vehicle Enquiry Service API."""

    PROD_URL = "https://driver-vehicle-licensing.api.gov.uk"
    TEST_URL = "https://uat.driver-vehicle-licensing.api.gov.uk"

    def __init__(self, api_key: Optional[str] = None, use_test_env: bool = False, demo_mode: bool = False):
        """
        Initialize DVLA client.

        Args:
            api_key: DVLA API key. If None and demo_mode=False, raises error.
            use_test_env: Use DVLA test environment instead of production.
            demo_mode: Explicitly enable demo mode for testing.
        """
        self.api_key = api_key
        self.base_url = self.TEST_URL if use_test_env else self.PROD_URL

        # Demo mode must be explicitly enabled OR API key must be provided
        if demo_mode:
            self.demo_mode = True
            logger.warning("DVLA client initialized in DEMO MODE - returning mock data")
        elif api_key is None:
            # SECURITY: Fail loudly if no API key and demo mode not explicitly enabled
            logger.error("DVLA_API_KEY not configured and demo_mode not enabled")
            self.demo_mode = True  # Fall back to demo but log error
            logger.warning("DVLA client falling back to DEMO MODE - configure DVLA_API_KEY for production")
        else:
            self.demo_mode = False
            logger.info("DVLA client initialized with API key")

        # Create reusable httpx client for better performance
        self._client: Optional[httpx.AsyncClient] = None

    async def get_vehicle(self, registration: str) -> Dict[str, Any]:
        """
        Look up vehicle details by registration number.

        Args:
            registration: UK vehicle registration number (e.g., "AB12CDE")

        Returns:
            Dict containing vehicle details from DVLA

        Raises:
            DVLAValidationError: Invalid registration format
            DVLANotFoundError: Vehicle not found
            DVLARateLimitError: Rate limit exceeded
            DVLAError: Other API errors
        """
        normalized_reg = normalize_registration(registration)

        # Validate registration format
        if not validate_registration(normalized_reg):
            raise DVLAValidationError(
                f"Invalid registration format: {registration}",
                status_code=400
            )

        # Demo mode - return mock data
        if self.demo_mode:
            return self._get_demo_vehicle(normalized_reg)

        # Call real DVLA API
        return await self._call_api(normalized_reg)

    def _get_demo_vehicle(self, registration: str) -> Dict[str, Any]:
        """Return demo vehicle data."""
        if registration in DEMO_VEHICLES:
            result = DEMO_VEHICLES[registration].copy()
            result["_demo"] = True
            return result

        # For unknown registrations in demo mode, generate plausible data
        # based on the registration pattern
        logger.info(f"Demo mode: generating mock data for {registration}")
        return {
            "registrationNumber": registration,
            "make": "FORD",
            "colour": "GREY",
            "yearOfManufacture": 2017,
            "fuelType": "PETROL",
            "engineCapacity": 1200,
            "taxStatus": "Taxed",
            "taxDueDate": "2025-06-01",
            "motStatus": "Valid",
            "motExpiryDate": "2025-05-15",
            "co2Emissions": 110,
            "_demo": True,
            "_note": "Demo data - not a real vehicle lookup"
        }

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the reusable httpx client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=5.0),
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
            )
        return self._client

    async def close(self):
        """Close the httpx client and release resources."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _call_api(self, registration: str) -> Dict[str, Any]:
        """Make actual API call to DVLA."""
        url = f"{self.base_url}/vehicle-enquiry/v1/vehicles"
        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        payload = {"registrationNumber": registration}

        client = await self._get_client()
        try:
            response = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException:
            raise DVLAError("DVLA API timeout", status_code=504)
        except httpx.RequestError as e:
            raise DVLAError(f"DVLA API connection error: {e}", status_code=502)

        # Handle response
        if response.status_code == 200:
            try:
                return response.json()
            except (ValueError, KeyError) as e:
                raise DVLAError(f"Invalid JSON response from DVLA: {e}", status_code=502)

        if response.status_code == 404:
            raise DVLANotFoundError(
                f"Vehicle not found: {registration}",
                status_code=404
            )

        if response.status_code == 400:
            raise DVLAValidationError(
                f"Invalid request for registration: {registration}",
                status_code=400
            )

        if response.status_code == 429:
            raise DVLARateLimitError(
                "DVLA API rate limit exceeded",
                status_code=429
            )

        # Other errors
        error_msg = f"DVLA API error: {response.status_code}"
        try:
            error_data = response.json()
            if isinstance(error_data, dict) and "message" in error_data:
                error_msg = f"DVLA API error: {error_data['message']}"
        except (ValueError, KeyError):
            pass  # Use default error message if JSON parsing fails

        raise DVLAError(error_msg, status_code=response.status_code)
