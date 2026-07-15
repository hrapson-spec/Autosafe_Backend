"""
Tests for dvsa_client.DVSAClient's odometer parsing: the shared
_parse_odometer staticmethod, and _parse_response's handling of a missing
odometerUnit field.

These are exercised directly against DVSAClient (not through
report_service), because report_service.resolve_odometer's own test
suite builds MOTTest fixtures with already-typed odometer_value ints
(tests/report_test_helpers.make_history) and so never exercises DVSA's
raw string-parsing behaviour.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from dvsa_client import DVSAClient, DVSAAPIError  # noqa: E402


def _client():
    # Credentials are irrelevant to parsing; supplied so is_configured
    # doesn't matter either way for these pure-function tests.
    return DVSAClient(client_id='id', client_secret='secret', token_url='https://example/token', api_key='key')


class TestParseOdometer(unittest.TestCase):
    """DVSAClient._parse_odometer(raw) -> Optional[int]."""

    def test_none_is_none(self):
        self.assertIsNone(DVSAClient._parse_odometer(None))

    def test_empty_string_is_none(self):
        self.assertIsNone(DVSAClient._parse_odometer(''))

    def test_int_zero_is_none(self):
        # Mirrors the pre-existing `if odometer_raw else None` falsy check.
        self.assertIsNone(DVSAClient._parse_odometer(0))

    def test_plain_digit_string(self):
        self.assertEqual(DVSAClient._parse_odometer('45000'), 45000)

    def test_string_zero_is_parsed_not_none(self):
        # '0' is a non-empty (truthy) string, distinct from the falsy int 0.
        self.assertEqual(DVSAClient._parse_odometer('0'), 0)

    def test_strips_whitespace(self):
        self.assertEqual(DVSAClient._parse_odometer('  45000  '), 45000)

    def test_removes_commas(self):
        self.assertEqual(DVSAClient._parse_odometer('45,000'), 45000)

    def test_whitespace_and_commas_combined(self):
        self.assertEqual(DVSAClient._parse_odometer(' 45,000 '), 45000)

    def test_non_numeric_string_is_none(self):
        self.assertIsNone(DVSAClient._parse_odometer('abc'))

    def test_accepts_int_input(self):
        self.assertEqual(DVSAClient._parse_odometer(45000), 45000)


class TestParseResponseOdometerUnit(unittest.TestCase):
    """dvsa_client.DVSAClient._parse_response's odometerUnit handling: a
    missing key must surface as None, never a silent 'mi' default."""

    def _payload(self, mot_test_overrides):
        mot_test = {
            'completedDate': '2025-01-01',
            'testResult': 'PASSED',
            'expiryDate': '2026-01-01',
            'odometerValue': '45000',
            'odometerUnit': 'mi',
            'motTestNumber': 'T123',
            'defects': [],
        }
        mot_test.update(mot_test_overrides)
        return {
            'make': 'FORD',
            'model': 'FIESTA',
            'fuelType': 'PETROL',
            'primaryColour': 'BLUE',
            'registrationDate': '2018-01-01',
            'manufactureDate': '2018-01-01',
            'engineSize': 1200,
            'motTests': [mot_test],
        }

    def test_missing_odometer_unit_is_none_not_mi(self):
        mot_test = {
            'completedDate': '2025-01-01', 'testResult': 'PASSED', 'expiryDate': '2026-01-01',
            'odometerValue': '45000', 'motTestNumber': 'T123', 'defects': [],
            # deliberately no 'odometerUnit' key at all
        }
        data = self._payload({})
        data['motTests'] = [mot_test]

        history = _client()._parse_response('AB12CDE', data)

        self.assertIsNone(history.mot_tests[0].odometer_unit)
        self.assertEqual(history.mot_tests[0].odometer_value, 45000)

    def test_present_odometer_unit_is_preserved_verbatim(self):
        data = self._payload({'odometerUnit': 'km'})

        history = _client()._parse_response('AB12CDE', data)

        self.assertEqual(history.mot_tests[0].odometer_unit, 'km')

    def test_odometer_value_with_commas_parses_via_shared_helper(self):
        data = self._payload({'odometerValue': '45,000'})

        history = _client()._parse_response('AB12CDE', data)

        self.assertEqual(history.mot_tests[0].odometer_value, 45000)


class TestFetchRetryConfig(unittest.TestCase):
    """fetch_vehicle_history's retry loop must honor the module-level
    DVSA_MAX_RETRIES / DVSA_RETRY_BACKOFF constants (B12) instead of the
    hardcoded max_retries=3 / unscaled 2**attempt backoff, while leaving
    behaviour unchanged when the constants sit at their (env-absent)
    defaults.

    _client.get is mocked to always raise httpx.RequestError, the
    cheapest way to drive the loop's generic retry branch on every
    attempt; _get_access_token is mocked out so no real OAuth call is
    attempted; asyncio.sleep is mocked so the tests don't actually wait
    and so the exact backoff durations passed can be asserted.
    """

    def _failing_client(self):
        client = _client()
        client._get_access_token = AsyncMock(return_value='token')
        client._client.get = AsyncMock(side_effect=httpx.RequestError('boom'))
        return client

    def test_defaults_unchanged_when_env_absent(self):
        """No env vars set (the CI/dev default): must still make exactly
        3 attempts with 2**attempt-second backoff -- the prior hardcoded
        behaviour -- so shipping this port changes nothing by default."""
        client = self._failing_client()
        sleep_mock = AsyncMock()

        async def run_test():
            with patch('asyncio.sleep', new=sleep_mock):
                with self.assertRaises(DVSAAPIError):
                    await client.fetch_vehicle_history('AB12CDE')

        asyncio.run(run_test())

        self.assertEqual(client._client.get.call_count, 3)
        self.assertEqual(sleep_mock.call_count, 2)
        sleep_mock.assert_any_call(1.0 * (2 ** 0))
        sleep_mock.assert_any_call(1.0 * (2 ** 1))

    def test_retry_count_honors_env(self):
        """DVSA_MAX_RETRIES=5 (simulating the env var being set, which is
        how this module-level constant is populated) must drive 5
        attempts, not the old hardcoded 3."""
        client = self._failing_client()
        sleep_mock = AsyncMock()

        async def run_test():
            with patch('dvsa_client.DVSA_MAX_RETRIES', 5), \
                 patch('asyncio.sleep', new=sleep_mock):
                with self.assertRaises(DVSAAPIError):
                    await client.fetch_vehicle_history('AB12CDE')

        asyncio.run(run_test())

        self.assertEqual(client._client.get.call_count, 5)
        self.assertEqual(sleep_mock.call_count, 4)

    def test_backoff_scales_with_env(self):
        """DVSA_RETRY_BACKOFF=2.5 must scale every sleep duration by 2.5x
        relative to the unscaled 2**attempt sequence."""
        client = self._failing_client()
        sleep_mock = AsyncMock()

        async def run_test():
            with patch('dvsa_client.DVSA_MAX_RETRIES', 3), \
                 patch('dvsa_client.DVSA_RETRY_BACKOFF', 2.5), \
                 patch('asyncio.sleep', new=sleep_mock):
                with self.assertRaises(DVSAAPIError):
                    await client.fetch_vehicle_history('AB12CDE')

        asyncio.run(run_test())

        self.assertEqual(sleep_mock.call_count, 2)
        sleep_mock.assert_any_call(2.5 * (2 ** 0))
        sleep_mock.assert_any_call(2.5 * (2 ** 1))


if __name__ == '__main__':
    unittest.main()
