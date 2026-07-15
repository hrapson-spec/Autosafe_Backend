"""
Privacy-hygiene tests for AutoSafe.

Covers the two log-safe redaction helpers in utils.py (hash_vrm,
mask_postcode) and a source-level regression guard confirming the
five plaintext-PII log lines fixed in the 2026-07 privacy-hygiene
batch (database.py) stay redacted.
"""
import hashlib
import os
import sys
import unittest

# Add parent directory to path to import utils/database
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import hash_vrm, mask_postcode, safe_log_path, safe_referrer

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestHashVrm(unittest.TestCase):

    def test_deterministic(self):
        """Same VRM hashes to the same value every time."""
        self.assertEqual(hash_vrm("AB12CDE"), hash_vrm("AB12CDE"))

    def test_output_format(self):
        """Output is exactly 8 lowercase hex characters
        for compact within-process log correlation."""
        result = hash_vrm("AB12CDE")
        self.assertIsInstance(result, str)
        self.assertEqual(len(result), 8)
        self.assertRegex(result, r'^[0-9a-f]{8}$')

    def test_not_an_unkeyed_enumerable_vrm_digest(self):
        """A small VRN keyspace must not be exposed as plain SHA-256."""
        plain_digest = hashlib.sha256(b"AB12CDE").hexdigest()[:8]
        self.assertNotEqual(hash_vrm("AB12CDE"), plain_digest)

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


class TestSafeLogPath(unittest.TestCase):

    def test_redacts_api_share_token(self):
        self.assertEqual(
            safe_log_path("/api/v2/reports/secretBearerToken123"),
            "/api/v2/reports/{token}",
        )

    def test_redacts_spa_share_token(self):
        self.assertEqual(
            safe_log_path("/app/report/secretBearerToken123"),
            "/app/report/{token}",
        )

    def test_leaves_non_share_path_unchanged(self):
        self.assertEqual(safe_log_path("/api/v2/reports"), "/api/v2/reports")


class TestSafeReferrer(unittest.TestCase):

    def test_drops_query_and_fragment_that_can_contain_identifiers(self):
        self.assertEqual(
            safe_referrer(
                "https://www.autosafe.one/app?reg=AB12CDE&postcode=SW1A1AA#result"
            ),
            "https://www.autosafe.one",
        )

    def test_redacts_share_bearer_token_from_path(self):
        self.assertEqual(
            safe_referrer("https://www.autosafe.one/app/report/secret-token"),
            "https://www.autosafe.one",
        )

    def test_drops_arbitrary_referrer_paths_that_can_embed_identifiers(self):
        self.assertEqual(
            safe_referrer("https://campaign.example/customer/owner%40example.com"),
            "https://campaign.example",
        )

    def test_rejects_credentials_and_non_http_schemes(self):
        self.assertIsNone(safe_referrer("https://user:password@example.com/path"))
        self.assertIsNone(safe_referrer("javascript:alert(1)"))

    def test_none_or_malformed_input_returns_none(self):
        self.assertIsNone(safe_referrer(None))
        self.assertIsNone(safe_referrer("not a url"))


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

    def test_database_logger_lines_do_not_emit_postcode_or_registration_derivatives(self):
        """Operational ids and coarse vehicle type are enough for these logs."""
        joined = "".join(self.logger_lines)
        self.assertNotIn("mask_postcode(", joined)
        self.assertNotIn("hash_vrm(risk_data.get('registration'))", joined)
        self.assertNotIn("hash_vrm(registration)", joined)

    def test_legacy_disk_backup_uses_an_explicit_non_identifier_allowlist(self):
        import database

        entry = database._redact_risk_check_backup({
            "registration": "AB12CDE",
            "postcode": "SW1A1AA",
            "report_token": "secret-token",
            "report_payload": {"registration": "AB12CDE"},
            "referrer": "https://autosafe.one/app?reg=AB12CDE",
            "vehicle_make": "FORD",
            "vehicle_model": "FIESTA",
            "failure_risk": 0.27,
        })

        serialised = str(entry)
        self.assertNotIn("AB12CDE", serialised)
        self.assertNotIn("SW1A1AA", serialised)
        self.assertNotIn("secret-token", serialised)
        self.assertNotIn("registration", entry)
        self.assertNotIn("postcode", entry)
        self.assertNotIn("report_payload", entry)
        self.assertEqual(entry["vehicle_make"], "FORD")
        self.assertEqual(entry["failure_risk"], 0.27)


if __name__ == '__main__':
    unittest.main()
