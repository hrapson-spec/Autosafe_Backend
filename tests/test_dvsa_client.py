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
import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dvsa_client import DVSAClient  # noqa: E402


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


if __name__ == '__main__':
    unittest.main()
