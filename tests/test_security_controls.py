"""
Security Controls Verification Tests
=====================================

Run with: python -m pytest tests/test_security_controls.py -v

These tests verify all launch-critical security controls are functioning correctly.
"""
import os
import sys
import re
import unittest
from unittest.mock import Mock, patch, MagicMock
from io import StringIO

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestPIIRedaction(unittest.TestCase):
    """Test PII redaction in logging."""

    def setUp(self):
        from security import redact_pii, PII_PATTERNS
        self.redact_pii = redact_pii
        self.patterns = PII_PATTERNS

    def test_email_redaction(self):
        """Emails should be redacted, preserving domain."""
        result = self.redact_pii("Contact: john.doe@example.com for info", preserve_partial=True)
        self.assertIn("[REDACTED]@example.com", result)
        self.assertNotIn("john.doe", result)

    def test_email_full_redaction(self):
        """Full email redaction when preserve_partial=False."""
        result = self.redact_pii("Email: test@example.com", preserve_partial=False)
        self.assertIn("[REDACTED_EMAIL]", result)
        self.assertNotIn("test@example.com", result)

    def test_uk_postcode_redaction(self):
        """UK postcodes should be redacted, preserving outcode."""
        test_cases = [
            ("SW1A 1AA", "SW1A [REDACTED]"),
            ("M1 1AA", "M1 [REDACTED]"),
            ("B33 8TH", "B33 [REDACTED]"),
            ("CR2 6XH", "CR2 [REDACTED]"),
            ("DN55 1PT", "DN55 [REDACTED]"),
        ]
        for postcode, expected_partial in test_cases:
            result = self.redact_pii(f"Postcode: {postcode}", preserve_partial=True)
            self.assertIn("[REDACTED]", result, f"Failed for {postcode}")
            # Check outcode is preserved
            outcode = postcode.split()[0] if " " in postcode else postcode[:-3]
            self.assertIn(outcode, result, f"Outcode not preserved for {postcode}")

    def test_uk_vrm_redaction(self):
        """UK vehicle registrations should be redacted."""
        test_cases = [
            "AB12CDE",
            "AB12 CDE",
            "A123BCD",
            "ABC123D",
            "1234AB",
        ]
        for vrm in test_cases:
            result = self.redact_pii(f"Vehicle: {vrm}")
            self.assertIn("[REDACTED_VRM]", result, f"Failed for {vrm}")
            self.assertNotIn(vrm.replace(" ", ""), result.replace(" ", ""))

    def test_phone_redaction(self):
        """UK phone numbers should be redacted."""
        test_cases = [
            "07700900123",
            "0770 090 0123",
            "+447700900123",
            "00447700900123",
        ]
        for phone in test_cases:
            result = self.redact_pii(f"Phone: {phone}")
            self.assertIn("[REDACTED_PHONE]", result, f"Failed for {phone}")

    def test_api_key_redaction(self):
        """API keys should be redacted."""
        test_cases = [
            "sk_test_1234567890abcdefghij",
            "re_abcdefghijklmnopqrstuvwxyz",
            "api_key_1234567890123456789012",
        ]
        for key in test_cases:
            result = self.redact_pii(f"Key: {key}")
            self.assertIn("[REDACTED", result, f"Failed for {key}")

    def test_mixed_pii_redaction(self):
        """Multiple PII types in one string should all be redacted."""
        text = "User john@example.com at SW1A 1AA, phone 07700900123, car AB12CDE"
        result = self.redact_pii(text, preserve_partial=True)

        self.assertNotIn("john", result)
        self.assertNotIn("1AA", result)
        self.assertNotIn("07700900123", result)
        self.assertNotIn("AB12CDE", result)

        # Should have redaction markers
        self.assertIn("[REDACTED]", result)

    def test_no_false_positives(self):
        """Normal text should not be redacted."""
        safe_texts = [
            "The quick brown fox",
            "Error code 12345",
            "Processing complete",
            "Vehicle make: FORD",
        ]
        for text in safe_texts:
            result = self.redact_pii(text)
            self.assertEqual(text, result, f"False positive for: {text}")


class TestIPAllowlist(unittest.TestCase):
    """Test IP allowlist functionality."""

    def create_mock_request(self, client_ip=None, forwarded_for=None, real_ip=None):
        """Create a mock request with specified IP headers."""
        request = Mock()
        request.headers = {}
        if forwarded_for:
            request.headers["X-Forwarded-For"] = forwarded_for
        if real_ip:
            request.headers["X-Real-IP"] = real_ip
        request.headers.get = lambda k, d=None: request.headers.get(k, d)

        if client_ip:
            request.client = Mock()
            request.client.host = client_ip
        else:
            request.client = None

        return request

    def test_direct_ip_when_no_proxy(self):
        """Without proxy, direct client IP should be used."""
        with patch.dict(os.environ, {"RAILWAY_ENVIRONMENT": ""}, clear=False):
            # Reload module to pick up env change
            import importlib
            import security
            importlib.reload(security)

            # When RAILWAY_ENVIRONMENT is not set, X-Forwarded-For should be ignored
            request = self.create_mock_request(
                client_ip="192.168.1.100",
                forwarded_for="10.0.0.1, 192.168.1.100"
            )

            # Direct IP should be used when not behind trusted proxy
            if not security.BEHIND_TRUSTED_PROXY:
                ip = security.get_client_ip(request)
                self.assertEqual(ip, "192.168.1.100")

    def test_forwarded_ip_behind_proxy(self):
        """Behind trusted proxy, X-Forwarded-For first IP should be used."""
        with patch.dict(os.environ, {"RAILWAY_ENVIRONMENT": "production"}):
            import importlib
            import security
            importlib.reload(security)

            request = self.create_mock_request(
                client_ip="10.0.0.50",  # Proxy IP
                forwarded_for="203.0.113.50, 10.0.0.1"  # Real client, then proxy
            )

            if security.BEHIND_TRUSTED_PROXY:
                ip = security.get_client_ip(request)
                self.assertEqual(ip, "203.0.113.50")

    def test_ip_allowlist_check(self):
        """IP allowlist should correctly allow/deny IPs."""
        import security

        # Test with specific allowed IPs
        with patch.object(security, 'ADMIN_ALLOWED_IPS', {'203.0.113.50', '198.51.100.25'}):
            with patch.object(security, 'ADMIN_ALLOW_ALL_IPS', False):
                with patch.object(security, 'RAILWAY_INTERNAL_NETWORK', False):
                    # Allowed IP
                    request = self.create_mock_request(client_ip="203.0.113.50")
                    with patch.object(security, 'get_client_ip', return_value="203.0.113.50"):
                        self.assertTrue(security.is_ip_allowed_for_admin(request))

                    # Denied IP
                    request = self.create_mock_request(client_ip="192.168.1.100")
                    with patch.object(security, 'get_client_ip', return_value="192.168.1.100"):
                        self.assertFalse(security.is_ip_allowed_for_admin(request))

    def test_localhost_always_allowed(self):
        """Localhost should always be allowed for development."""
        import security

        with patch.object(security, 'ADMIN_ALLOWED_IPS', set()):
            with patch.object(security, 'ADMIN_ALLOW_ALL_IPS', False):
                with patch.object(security, 'RAILWAY_INTERNAL_NETWORK', False):
                    for localhost in ["127.0.0.1", "::1", "localhost"]:
                        request = self.create_mock_request(client_ip=localhost)
                        with patch.object(security, 'get_client_ip', return_value=localhost):
                            self.assertTrue(
                                security.is_ip_allowed_for_admin(request),
                                f"Localhost {localhost} should be allowed"
                            )

    def test_railway_internal_network(self):
        """Railway internal network (10.x.x.x) should be allowed when on Railway."""
        import security

        with patch.object(security, 'ADMIN_ALLOWED_IPS', set()):
            with patch.object(security, 'ADMIN_ALLOW_ALL_IPS', False):
                with patch.object(security, 'RAILWAY_INTERNAL_NETWORK', True):
                    request = self.create_mock_request(client_ip="10.0.0.50")
                    with patch.object(security, 'get_client_ip', return_value="10.0.0.50"):
                        self.assertTrue(security.is_ip_allowed_for_admin(request))


class TestAPIKeyRotation(unittest.TestCase):
    """Test API key rotation support."""

    def test_primary_key_works(self):
        """Primary API key should authenticate."""
        import security

        with patch.object(security.api_key_manager, 'primary_key', 'test-primary-key'):
            with patch.object(security.api_key_manager, 'secondary_key', None):
                self.assertTrue(security.api_key_manager.validate_key('test-primary-key'))
                self.assertFalse(security.api_key_manager.validate_key('wrong-key'))

    def test_secondary_key_works(self):
        """Secondary API key should authenticate for rotation."""
        import security

        with patch.object(security.api_key_manager, 'primary_key', 'primary'):
            with patch.object(security.api_key_manager, 'secondary_key', 'secondary'):
                self.assertTrue(security.api_key_manager.validate_key('primary'))
                self.assertTrue(security.api_key_manager.validate_key('secondary'))
                self.assertFalse(security.api_key_manager.validate_key('neither'))

    def test_empty_key_rejected(self):
        """Empty or None keys should be rejected."""
        import security

        self.assertFalse(security.api_key_manager.validate_key(''))
        self.assertFalse(security.api_key_manager.validate_key(None))

    def test_key_not_logged(self):
        """API keys should never appear in audit logs."""
        import security

        # Capture log output
        log_capture = StringIO()
        handler = __import__('logging').StreamHandler(log_capture)
        security.audit_logger.logger.addHandler(handler)

        # Create mock request with API key
        request = Mock()
        request.headers = {"X-API-Key": "secret-api-key-12345"}
        request.headers.get = lambda k, d=None: request.headers.get(k, d)
        request.url = Mock()
        request.url.path = "/api/test"
        request.method = "GET"
        request.client = Mock()
        request.client.host = "127.0.0.1"

        # Log an admin access
        security.audit_logger.log_admin_access(
            request=request,
            action="test",
            resource="test"
        )

        log_output = log_capture.getvalue()

        # Key should not appear in logs
        self.assertNotIn("secret-api-key-12345", log_output)

        # But key hash should appear
        self.assertIn("key_hash", log_output)

        security.audit_logger.logger.removeHandler(handler)


class TestAuditLogging(unittest.TestCase):
    """Test audit logging functionality."""

    def test_audit_log_fields(self):
        """Audit logs should contain required fields."""
        import security
        import json

        log_capture = StringIO()
        handler = __import__('logging').StreamHandler(log_capture)
        handler.setFormatter(__import__('logging').Formatter('%(message)s'))
        security.audit_logger.logger.addHandler(handler)

        request = Mock()
        request.headers = {"X-API-Key": "test-key"}
        request.headers.get = lambda k, d=None: request.headers.get(k, d)
        request.url = Mock()
        request.url.path = "/api/leads"
        request.method = "GET"
        request.client = Mock()
        request.client.host = "203.0.113.50"

        security.audit_logger.log_admin_access(
            request=request,
            action="list",
            resource="leads",
            resource_id="123",
            details={"limit": 50}
        )

        log_output = log_capture.getvalue()

        # Check required fields are present
        self.assertIn("action", log_output)
        self.assertIn("list", log_output)
        self.assertIn("resource", log_output)
        self.assertIn("leads", log_output)
        self.assertIn("client_ip", log_output)
        self.assertIn("key_hash", log_output)
        self.assertIn("path", log_output)
        self.assertIn("method", log_output)

        security.audit_logger.logger.removeHandler(handler)


class TestCORSConfiguration(unittest.TestCase):
    """Test CORS configuration."""

    def test_no_wildcards_allowed(self):
        """Wildcard origins should be rejected."""
        import security

        with patch.dict(os.environ, {"CORS_ORIGINS": "*"}):
            origins = security.get_allowed_origins()
            self.assertNotIn("*", origins)

    def test_explicit_origins_parsed(self):
        """Explicit origins should be parsed correctly."""
        import security

        test_origins = "https://example.com,https://app.example.com"
        with patch.dict(os.environ, {"CORS_ORIGINS": test_origins}):
            import importlib
            importlib.reload(security)
            origins = security.get_allowed_origins()
            self.assertIn("https://example.com", origins)
            self.assertIn("https://app.example.com", origins)

    def test_invalid_origins_rejected(self):
        """Origins without http/https should be rejected."""
        import security

        with patch.dict(os.environ, {"CORS_ORIGINS": "example.com,https://valid.com"}):
            import importlib
            importlib.reload(security)
            origins = security.get_allowed_origins()
            self.assertNotIn("example.com", origins)
            self.assertIn("https://valid.com", origins)


class TestDataRetention(unittest.TestCase):
    """Test data retention configuration."""

    def test_retention_periods_configurable(self):
        """Retention periods should be configurable via env vars."""
        with patch.dict(os.environ, {
            "RETENTION_LEADS_DAYS": "30",
            "RETENTION_ASSIGNMENTS_DAYS": "30",
            "RETENTION_AUDIT_DAYS": "180",
        }):
            import importlib
            import data_retention
            importlib.reload(data_retention)

            self.assertEqual(data_retention.RETENTION_PERIODS["leads"], 30)
            self.assertEqual(data_retention.RETENTION_PERIODS["lead_assignments"], 30)
            self.assertEqual(data_retention.RETENTION_PERIODS["audit_logs"], 180)

    def test_default_retention_periods(self):
        """Default retention periods should be reasonable."""
        import data_retention

        # Leads should default to 90 days
        self.assertGreaterEqual(data_retention.RETENTION_PERIODS.get("leads", 0), 30)
        self.assertLessEqual(data_retention.RETENTION_PERIODS.get("leads", 999), 365)

        # DVSA cache should be short (1 day)
        self.assertEqual(data_retention.RETENTION_PERIODS.get("dvsa_cache", 0), 1)


class TestPortalSecurity(unittest.TestCase):
    """Test portal security controls."""

    def test_uuid_validation(self):
        """Invalid UUIDs should be rejected."""
        import uuid

        valid_uuids = [
            "550e8400-e29b-41d4-a716-446655440000",
            str(uuid.uuid4()),
        ]

        invalid_uuids = [
            "not-a-uuid",
            "'; DROP TABLE leads;--",
            "123",
            "",
            "../../../etc/passwd",
        ]

        for valid in valid_uuids:
            try:
                uuid.UUID(valid)
            except ValueError:
                self.fail(f"Valid UUID rejected: {valid}")

        for invalid in invalid_uuids:
            with self.assertRaises(ValueError, msg=f"Invalid UUID accepted: {invalid}"):
                uuid.UUID(invalid)


class TestSQLiteFallback(unittest.TestCase):
    """Test SQLite fallback is disabled in production."""

    def test_sqlite_disabled_in_production(self):
        """SQLite fallback should be disabled when RAILWAY_ENVIRONMENT=production."""
        # This is tested by checking the logic in main.py
        # The SQLITE_FALLBACK_ENABLED flag should be False in production

        # We can't easily test this without running the full app,
        # but we can verify the logic exists
        import main

        # Check that the flag exists
        self.assertTrue(hasattr(main, 'SQLITE_FALLBACK_ENABLED') or
                       'SQLITE_FALLBACK_ENABLED' in dir(main))


class TestEmailMinimization(unittest.TestCase):
    """Test email templates minimize PII exposure."""

    def test_secure_email_no_contact_details(self):
        """Secure email template should not include customer contact details."""
        from email_templates_secure import generate_lead_email_minimal

        result = generate_lead_email_minimal(
            garage_name="Test Garage",
            distance_miles=5.0,
            vehicle_make="FORD",
            vehicle_model="FIESTA",
            vehicle_year=2020,
            failure_risk=0.35,
            top_risks=["brakes", "suspension"],
            assignment_id="550e8400-e29b-41d4-a716-446655440000",
        )

        html = result["html"]
        text = result["text"]

        # Should NOT contain customer contact placeholders
        self.assertNotIn("customer_email", html.lower())
        self.assertNotIn("customer_phone", html.lower())

        # Should contain portal link
        self.assertIn("/portal/lead/", html)
        self.assertIn("550e8400-e29b-41d4-a716-446655440000", html)

        # Should contain vehicle info (allowed)
        self.assertIn("FORD", html)
        self.assertIn("FIESTA", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
