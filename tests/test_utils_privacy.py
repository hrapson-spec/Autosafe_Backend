"""
Privacy-hygiene tests for AutoSafe.

Covers the two log-safe redaction helpers in utils.py (hash_vrm,
mask_postcode) and a source-level regression guard confirming the
five plaintext-PII log lines fixed in the 2026-07 privacy-hygiene
batch (database.py) stay redacted.
"""
import os
import sys
import unittest

# Add parent directory to path to import utils/database
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import hash_vrm, mask_postcode

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestHashVrm(unittest.TestCase):

    def test_deterministic(self):
        """Same VRM hashes to the same value every time."""
        self.assertEqual(hash_vrm("AB12CDE"), hash_vrm("AB12CDE"))

    def test_output_format(self):
        """Output is exactly 8 lowercase hex characters
        (sha256 hexdigest truncated to 8, per the implementation)."""
        result = hash_vrm("AB12CDE")
        self.assertIsInstance(result, str)
        self.assertEqual(len(result), 8)
        self.assertRegex(result, r'^[0-9a-f]{8}$')

    def test_differs_for_different_vrms(self):
        """Different VRMs produce different hashes."""
        self.assertNotEqual(hash_vrm("AB12CDE"), hash_vrm("XY99ZZZ"))

    def test_differs_for_similar_vrms(self):
        """Near-identical VRMs (single changed character) still diverge."""
        self.assertNotEqual(hash_vrm("AB12CDE"), hash_vrm("AB12CDF"))

    def test_empty_string_does_not_crash(self):
        """Empty string is a valid str and must not raise."""
        result = hash_vrm("")
        self.assertEqual(len(result), 8)
        self.assertRegex(result, r'^[0-9a-f]{8}$')


class TestMaskPostcode(unittest.TestCase):

    def test_full_postcode_with_space(self):
        self.assertEqual(mask_postcode("SW1A 1AA"), "SW1A")

    def test_full_postcode_no_space(self):
        self.assertEqual(mask_postcode("SW1A1AA"), "SW1A")

    def test_full_postcode_short_outward_with_space(self):
        self.assertEqual(mask_postcode("M1 1AE"), "M1")

    def test_lowercase_no_space(self):
        self.assertEqual(mask_postcode("sw1a1aa"), "SW1A")

    def test_lowercase_with_space(self):
        self.assertEqual(mask_postcode("sw1a 1aa"), "SW1A")

    def test_outward_code_only_input(self):
        """A bare outward code with no inward code has its trailing 3
        characters stripped by the same mechanical rule (the function
        always drops the final 3 characters of a >3-char input -- it
        does not attempt to parse postcode structure). This pins the
        actual, documented behavior rather than an idealized one."""
        self.assertEqual(mask_postcode("SW1A"), "S")

    def test_short_input_returns_placeholder(self):
        """Inputs of <=3 characters have no safe outward code to return."""
        self.assertEqual(mask_postcode("M1"), "***")

    def test_exactly_three_chars_returns_placeholder(self):
        self.assertEqual(mask_postcode("ABC"), "***")

    def test_empty_string(self):
        self.assertEqual(mask_postcode(""), "***")

    def test_none(self):
        self.assertEqual(mask_postcode(None), "***")

    def test_non_str_input(self):
        self.assertEqual(mask_postcode(12345), "***")

    def test_whitespace_only(self):
        self.assertEqual(mask_postcode("   "), "***")


class TestDatabasePyLogRedaction(unittest.TestCase):
    """
    Source-level regression guard for the five plaintext-PII log lines
    fixed by the 2026-07 privacy-hygiene batch (database.py, originally
    around lines 343, 478, 867, 889, 957).

    Patterns are the exact original plaintext f-string fragments,
    scoped tightly (immediate surrounding characters included) so they
    only ever matched those five lines and won't false-positive on
    legitimate, unrelated uses of the same dict lookups elsewhere
    (e.g. DB write parameters, which never appear on a `logger.` line).
    """

    # Original plaintext fragments that must never reappear on a
    # logger.* line. Keep this list scoped to exactly the five lines
    # touched by the privacy-hygiene batch -- do not broaden it to bare
    # substrings like "lead_data.get('postcode')", which also
    # legitimately appears in the (unredacted, correct) DB INSERT calls.
    FORBIDDEN_LOG_PATTERNS = [
        "postcode={lead_data.get('postcode')}",   # was line ~343 (Lead saved)
        "({garage_data.get('postcode')})",         # was line ~478 (Garage saved)
        "postcode={risk_data.get('postcode')}",    # was line ~867 (Risk check logged)
        "file: {risk_data.get('registration')}",   # was line ~889 (Risk check backed up to file)
        "registration={registration}",             # was line ~957 (MOT reminder saved)
    ]

    @classmethod
    def setUpClass(cls):
        db_path = os.path.join(REPO_ROOT, "database.py")
        with open(db_path, "r") as f:
            cls.source_lines = f.readlines()
        cls.logger_lines = [line for line in cls.source_lines if "logger." in line]

    def test_database_module_imports_without_circular_import(self):
        """utils.hash_vrm/mask_postcode importing cleanly into database.py
        (and database.py importing cleanly at all) is the regression
        guard against a circular import between utils.py and database.py."""
        import database  # noqa: F401 -- successful import is the assertion
        self.assertTrue(hasattr(database, "save_lead"))

    def test_no_plaintext_postcode_or_registration_in_logger_lines(self):
        for pattern in self.FORBIDDEN_LOG_PATTERNS:
            offending = [line for line in self.logger_lines if pattern in line]
            self.assertEqual(
                offending, [],
                f"Found un-redacted PII pattern {pattern!r} in database.py logger line(s): {offending}"
            )

    def test_database_logger_lines_use_the_redaction_helpers(self):
        """Confirm mask_postcode/hash_vrm are actually wired into the
        five target lines (not just that the old plaintext is gone)."""
        joined = "".join(self.logger_lines)
        self.assertIn("mask_postcode(lead_data.get('postcode'))", joined)
        self.assertIn("mask_postcode(garage_data.get('postcode'))", joined)
        self.assertIn("mask_postcode(risk_data.get('postcode'))", joined)
        self.assertIn("hash_vrm(risk_data.get('registration'))", joined)
        self.assertIn("hash_vrm(registration)", joined)


if __name__ == '__main__':
    unittest.main()
