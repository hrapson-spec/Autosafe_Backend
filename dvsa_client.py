"""
DVSA MOT History API Client
===========================

Fetches vehicle MOT history from the DVSA API with:
- OAuth 2.0 authentication (client credentials flow)
- VRM (registration) validation and normalization
- 24-hour response caching
- Graceful error handling for fallback triggering
"""

import os
import re
import logging
import time
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from dataclasses import dataclass

import httpx
from cachetools import TTLCache

logger = logging.getLogger(__name__)


class OAuthToken:
    """Manages OAuth 2.0 token with automatic refresh."""

    def __init__(self):
        self.access_token: Optional[str] = None
        self.expires_at: float = 0

    def is_valid(self) -> bool:
        """Check if token is valid (exists and not expired)."""
        return self.access_token is not None and time.time() < self.expires_at - 60  # 60s buffer

    def set_token(self, access_token: str, expires_in: int):
        """Set token with expiry."""
        self.access_token = access_token
        self.expires_at = time.time() + expires_in


class VRMValidationError(Exception):
    """Raised when VRM validation fails hard rules."""
    pass


class DVSAAPIError(Exception):
    """Raised when DVSA API returns an error."""
    pass


class VehicleNotFoundError(Exception):
    """Raised when vehicle not found in DVSA database."""
    pass


@dataclass
class MOTTest:
    """Represents a single MOT test record."""
    test_date: datetime
    test_result: str  # 'PASSED' or 'FAILED'
    expiry_date: Optional[datetime]
    odometer_value: Optional[int]
    odometer_unit: str  # 'mi' or 'km'
    test_number: str
    defects: List[Dict[str, Any]]  # Advisory/failure items


@dataclass
class VehicleHistory:
    """Complete vehicle MOT history from DVSA."""
    registration: str
    make: str
    model: str
    fuel_type: Optional[str]
    colour: Optional[str]
    registration_date: Optional[datetime]
    manufacture_date: Optional[datetime]
    engine_size: Optional[int]
    mot_tests: List[MOTTest]

    @property
    def latest_test(self) -> Optional[MOTTest]:
        """Get the most recent MOT test."""
        if self.mot_tests:
            return self.mot_tests[0]  # Tests are returned newest-first
        return None

    @property
    def has_mot_history(self) -> bool:
        """Check if vehicle has any MOT history."""
        return len(self.mot_tests) > 0


class DVSAClient:
    """
    Client for DVSA MOT History API.

    Uses OAuth 2.0 client credentials flow for authentication.

    Usage:
        client = DVSAClient()
        history = await client.fetch_vehicle_history("AB12CDE")

    Required environment variables:
        DVSA_CLIENT_ID: OAuth client ID
        DVSA_CLIENT_SECRET: OAuth client secret
        DVSA_TOKEN_URL: OAuth token endpoint
        DVSA_SCOPE: OAuth scope (usually https://tapi.dvsa.gov.uk/.default)
    """

    # DVSA API endpoint - try legacy beta API
    BASE_URL = "https://beta.check-mot.service.gov.uk"

    # Cache TTL: 24 hours
    CACHE_TTL_SECONDS = 24 * 60 * 60

    # VRM validation patterns
    # Basic rules: alphanumeric, 2-8 characters
    VRM_BASIC_PATTERN = re.compile(r'^[A-Z0-9]{2,8}$')

    # Common UK patterns for user feedback (not hard rejection)
    UK_PATTERNS = [
        re.compile(r'^[A-Z]{2}[0-9]{2}[A-Z]{3}$'),  # Current: AB12CDE
        re.compile(r'^[A-Z][0-9]{1,3}[A-Z]{3}$'),    # Prefix: A123BCD
        re.compile(r'^[A-Z]{3}[0-9]{1,3}[A-Z]$'),    # Suffix: ABC123D
        re.compile(r'^[0-9]{1,4}[A-Z]{1,3}$'),       # Dateless: 1234AB
        re.compile(r'^[A-Z]{1,3}[0-9]{1,4}$'),       # Dateless: AB1234
    ]

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        token_url: Optional[str] = None,
        scope: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        """
        Initialize DVSA client with OAuth 2.0 credentials.

        Args:
            client_id: OAuth client ID (or set DVSA_CLIENT_ID env var)
            client_secret: OAuth client secret (or set DVSA_CLIENT_SECRET env var)
            token_url: OAuth token endpoint (or set DVSA_TOKEN_URL env var)
            scope: OAuth scope (or set DVSA_SCOPE env var)
            api_key: API key (or set DVSA_API_KEY env var)
        """
        # Check both naming conventions for Railway compatibility
        self.client_id = client_id or os.environ.get("DVSA_CLIENT_ID") or os.environ.get("DVSA_Client_ID")
        self.client_secret = client_secret or os.environ.get("DVSA_CLIENT_SECRET") or os.environ.get("DVSA_Client_Secret")
        self.token_url = token_url or os.environ.get("DVSA_TOKEN_URL") or os.environ.get("DVSA_Token_URL") or os.environ.get("DVSA_Token_ID")
        self.scope = scope or os.environ.get("DVSA_SCOPE") or os.environ.get("DVSA_Scope") or "https://tapi.dvsa.gov.uk/.default"
        self.api_key = api_key or os.environ.get("DVSA_API_KEY") or os.environ.get("DVSA_Api_Key")

        # Check if credentials are configured
        self.is_configured = all([self.client_id, self.client_secret, self.token_url])
        if not self.is_configured:
            logger.warning("DVSA OAuth credentials not fully configured - API calls will fail")
            logger.warning(f"  CLIENT_ID: {'set' if self.client_id else 'MISSING'}")
            logger.warning(f"  CLIENT_SECRET: {'set' if self.client_secret else 'MISSING'}")
            logger.warning(f"  TOKEN_URL: {'set' if self.token_url else 'MISSING'}")

        # OAuth token management
        self._token = OAuthToken()

        # 24-hour cache (max 10000 entries)
        self._cache: TTLCache = TTLCache(maxsize=10000, ttl=self.CACHE_TTL_SECONDS)

        # HTTP client with timeout
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    async def _get_access_token(self) -> str:
        """Get valid OAuth access token, refreshing if needed."""
        if self._token.is_valid():
            return self._token.access_token

        if not self.is_configured:
            raise DVSAAPIError("DVSA OAuth credentials not configured")

        logger.info("Fetching new OAuth token from DVSA...")

        try:
            response = await self._client.post(
                self.token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": self.scope,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            if response.status_code != 200:
                logger.error(f"OAuth token request failed: {response.status_code} - {response.text}")
                raise DVSAAPIError(f"OAuth authentication failed: {response.status_code}")

            token_data = response.json()
            self._token.set_token(
                access_token=token_data["access_token"],
                expires_in=token_data.get("expires_in", 3600)
            )

            logger.info("OAuth token obtained successfully")
            return self._token.access_token

        except httpx.RequestError as e:
            raise DVSAAPIError(f"OAuth token request failed: {str(e)}")

    def normalize_vrm(self, registration: str) -> str:
        """
        Normalize a vehicle registration mark (VRM).

        Steps:
        1. Strip whitespace and convert to uppercase
        2. Remove spaces
        3. Hard-block if not alphanumeric or wrong length

        Args:
            registration: Raw registration input

        Returns:
            Normalized VRM

        Raises:
            VRMValidationError: If VRM fails basic validation
        """
        # Trim and uppercase
        vrm = registration.strip().upper()

        # Remove spaces
        vrm = vrm.replace(" ", "")

        # Hard-block: must be alphanumeric
        if not vrm.isalnum():
            raise VRMValidationError(
                f"Invalid registration format: must contain only letters and numbers"
            )

        # Hard-block: length 2-8
        if len(vrm) < 2 or len(vrm) > 8:
            raise VRMValidationError(
                f"Invalid registration format: must be 2-8 characters"
            )

        # Basic pattern check
        if not self.VRM_BASIC_PATTERN.match(vrm):
            raise VRMValidationError(
                f"Invalid registration format: contains invalid characters"
            )

        return vrm

    def validate_vrm_pattern(self, vrm: str) -> Dict[str, Any]:
        """
        Check VRM against common UK patterns for user feedback.

        This does NOT reject non-matching VRMs (older/personalised plates
        may not match standard patterns).

        Args:
            vrm: Normalized VRM

        Returns:
            Dict with 'matches_known_pattern' and optional 'pattern_type'
        """
        for i, pattern in enumerate(self.UK_PATTERNS):
            if pattern.match(vrm):
                pattern_names = ['current', 'prefix', 'suffix', 'dateless_numeric', 'dateless_alpha']
                return {
                    'matches_known_pattern': True,
                    'pattern_type': pattern_names[i] if i < len(pattern_names) else 'unknown'
                }

        return {
            'matches_known_pattern': False,
            'pattern_type': None,
            'note': 'Non-standard format - may be personalised or older plate'
        }

    async def fetch_vehicle_history(self, registration: str) -> VehicleHistory:
        """
        Fetch MOT history for a vehicle from DVSA API.

        Args:
            registration: Vehicle registration (will be normalized)

        Returns:
            VehicleHistory object with all MOT tests

        Raises:
            VRMValidationError: If registration fails validation
            VehicleNotFoundError: If vehicle not in DVSA database
            DVSAAPIError: If API returns an error
        """
        # Normalize VRM
        vrm = self.normalize_vrm(registration)

        # P1-10 fix: Hash VRM for logging
        import hashlib
        vrm_hash = hashlib.sha256(vrm.encode()).hexdigest()[:8]

        # Check cache first
        if vrm in self._cache:
            logger.info(f"Cache hit for {vrm_hash}")
            return self._cache[vrm]

        # P2-6 fix: Add retry logic for transient failures
        max_retries = 3
        last_error = None

        for attempt in range(max_retries):
            try:
                # Call DVSA API
                if attempt == 0:
                    logger.info(f"Fetching MOT history for {vrm_hash} from DVSA API")
                else:
                    logger.info(f"Retry {attempt}/{max_retries} for {vrm_hash}")

                # Get OAuth access token
                access_token = await self._get_access_token()

                # Build headers - OAuth bearer token required, API key optional
                headers = {"Authorization": f"Bearer {access_token}"}
                if self.api_key:
                    headers["X-API-Key"] = self.api_key

                response = await self._client.get(
                    f"{self.BASE_URL}/trade/vehicles/mot-tests",
                    params={"registration": vrm},
                    headers=headers
                )

                if response.status_code == 404:
                    raise VehicleNotFoundError(f"Vehicle not found in DVSA database")

                if response.status_code == 403:
                    raise DVSAAPIError("DVSA API key invalid or expired")

                if response.status_code == 429:
                    # Rate limit - retry after delay
                    if attempt < max_retries - 1:
                        import asyncio
                        await asyncio.sleep(2 ** attempt)  # Exponential backoff
                        continue
                    raise DVSAAPIError("DVSA API rate limit exceeded")

                if response.status_code != 200:
                    raise DVSAAPIError(f"DVSA API error: {response.status_code}")

                data = response.json()

                # Parse response
                history = self._parse_response(vrm, data)

                # P1-5 fix: Validate response before caching
                if history and history.registration:
                    self._cache[vrm] = history
                    logger.info(f"Cached MOT history for {vrm_hash} ({len(history.mot_tests)} tests)")
                else:
                    logger.warning(f"Invalid response for {vrm_hash}, not caching")

                return history

            except httpx.TimeoutException:
                last_error = DVSAAPIError("DVSA API request timed out")
                if attempt < max_retries - 1:
                    import asyncio
                    await asyncio.sleep(2 ** attempt)
                    continue
            except httpx.RequestError as e:
                last_error = DVSAAPIError(f"DVSA API connection error: {str(e)}")
                if attempt < max_retries - 1:
                    import asyncio
                    await asyncio.sleep(2 ** attempt)
                    continue

        # All retries exhausted
        raise last_error or DVSAAPIError("DVSA API request failed after retries")

    def _parse_response(self, vrm: str, data: Dict[str, Any]) -> VehicleHistory:
        """Parse DVSA API response into VehicleHistory object."""
        # Handle array response (API returns array of vehicles)
        if isinstance(data, list):
            if not data:
                raise VehicleNotFoundError(f"No data returned for {vrm}")
            vehicle_data = data[0]
        else:
            vehicle_data = data

        # Fix: Validate essential fields
        make = vehicle_data.get('make')
        model = vehicle_data.get('model')
        if not make or not model:
            logger.warning(f"DVSA response missing make/model for VRM, using UNKNOWN")
            make = make or 'UNKNOWN'
            model = model or 'UNKNOWN'

        # Parse MOT tests
        mot_tests = []
        for test_data in vehicle_data.get('motTests', []):
            # Fix: Properly handle defects vs rfrAndComments
            # If defects is explicitly None, use rfrAndComments; if defects is [] use []
            defects = test_data.get('defects')
            if defects is None:
                defects = test_data.get('rfrAndComments', [])

            test = MOTTest(
                test_date=self._parse_date(test_data.get('completedDate')),
                test_result=test_data.get('testResult', 'UNKNOWN'),
                expiry_date=self._parse_date(test_data.get('expiryDate')),
                odometer_value=test_data.get('odometerValue'),
                odometer_unit=test_data.get('odometerUnit', 'mi'),
                test_number=test_data.get('motTestNumber', ''),
                defects=defects or []
            )
            mot_tests.append(test)

        return VehicleHistory(
            registration=vrm,
            make=make,
            model=model,
            fuel_type=vehicle_data.get('fuelType'),
            colour=vehicle_data.get('primaryColour'),
            registration_date=self._parse_date(vehicle_data.get('registrationDate')),
            manufacture_date=self._parse_date(vehicle_data.get('manufactureDate')),
            engine_size=vehicle_data.get('engineSize'),
            mot_tests=mot_tests
        )

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse date string from DVSA API."""
        if not date_str:
            return None
        try:
            # DVSA uses ISO format: 2024-01-15 or 2024.01.15
            date_str = date_str.replace('.', '-')[:10]
            return datetime.strptime(date_str, '%Y-%m-%d')
        except (ValueError, TypeError):
            return None

    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()

    def clear_cache(self):
        """Clear the response cache."""
        self._cache.clear()
        logger.info("DVSA cache cleared")


# Singleton instance for app-wide use
_dvsa_client: Optional[DVSAClient] = None


def get_dvsa_client() -> DVSAClient:
    """Get or create the singleton DVSA client."""
    global _dvsa_client
    if _dvsa_client is None:
        _dvsa_client = DVSAClient()
    return _dvsa_client


async def close_dvsa_client():
    """Close the singleton DVSA client."""
    global _dvsa_client
    if _dvsa_client is not None:
        await _dvsa_client.close()
        _dvsa_client = None
