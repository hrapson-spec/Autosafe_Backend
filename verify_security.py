#!/usr/bin/env python3
"""
Security Controls Verification Script
======================================

Run with: python verify_security.py

This script verifies all critical security controls are working correctly.
"""
import os
import sys
import re

# Test results
PASSED = []
FAILED = []

def test(name, condition, details=""):
    """Record test result."""
    if condition:
        PASSED.append(name)
        print(f"  PASS: {name}")
    else:
        FAILED.append((name, details))
        print(f"  FAIL: {name} - {details}")

def section(title):
    """Print section header."""
    print(f"\n{'='*60}")
    print(f" {title}")
    print('='*60)

# =============================================================================
# 1. PII Redaction Tests
# =============================================================================
section("1. PII REDACTION")

from security import redact_pii, PII_PATTERNS

# Email redaction
result = redact_pii("Contact: john.doe@example.com", preserve_partial=True)
test("Email partial redaction",
     "[REDACTED]@example.com" in result and "john.doe" not in result,
     f"Got: {result}")

result = redact_pii("Email: test@example.com", preserve_partial=False)
test("Email full redaction",
     "[REDACTED_EMAIL]" in result and "test@example.com" not in result,
     f"Got: {result}")

# UK Postcode redaction
for postcode in ["SW1A 1AA", "M1 1AA", "B33 8TH"]:
    result = redact_pii(f"Postcode: {postcode}", preserve_partial=True)
    outcode = postcode.split()[0]
    test(f"Postcode redaction ({postcode})",
         "[REDACTED]" in result and outcode in result,
         f"Got: {result}")

# UK VRM redaction
for vrm in ["AB12CDE", "AB12 CDE", "A123BCD"]:
    result = redact_pii(f"Vehicle: {vrm}")
    test(f"VRM redaction ({vrm})",
         "[REDACTED_VRM]" in result,
         f"Got: {result}")

# Phone redaction
for phone in ["07700900123", "+447700900123"]:
    result = redact_pii(f"Phone: {phone}")
    test(f"Phone redaction ({phone})",
         "[REDACTED_PHONE]" in result,
         f"Got: {result}")

# API key redaction
for key in ["sk_test_1234567890abcdefghij", "re_abcdefghijklmnopqrstuvwxyz"]:
    result = redact_pii(f"Key: {key}")
    test(f"API key redaction",
         "[REDACTED" in result and key not in result,
         f"Got: {result}")

# No false positives
safe_texts = ["The quick brown fox", "Error code 12345", "Vehicle make: FORD"]
for text in safe_texts:
    result = redact_pii(text)
    test(f"No false positive: '{text[:30]}'",
         result == text,
         f"Got: {result}")

# Mixed PII
text = "User john@example.com at SW1A 1AA, car AB12CDE"
result = redact_pii(text, preserve_partial=True)
test("Mixed PII redaction",
     "john" not in result and "1AA" not in result and "AB12CDE" not in result,
     f"Got: {result}")

# =============================================================================
# 2. CORS Configuration Tests
# =============================================================================
section("2. CORS CONFIGURATION")

from security import get_allowed_origins

# Test wildcard rejection
os.environ["CORS_ORIGINS"] = "*"
# Need to reload to pick up env change
import importlib
import security
importlib.reload(security)
origins = security.get_allowed_origins()
test("Wildcard CORS rejected",
     "*" not in origins,
     f"Got: {origins}")

# Test explicit origins
os.environ["CORS_ORIGINS"] = "https://example.com,https://app.example.com"
importlib.reload(security)
origins = security.get_allowed_origins()
test("Explicit CORS origins parsed",
     "https://example.com" in origins and "https://app.example.com" in origins,
     f"Got: {origins}")

# Test invalid origin rejection
os.environ["CORS_ORIGINS"] = "example.com,https://valid.com"
importlib.reload(security)
origins = security.get_allowed_origins()
test("Invalid origin rejected",
     "example.com" not in origins and "https://valid.com" in origins,
     f"Got: {origins}")

# Clean up
os.environ.pop("CORS_ORIGINS", None)

# =============================================================================
# 3. API Key Rotation Tests
# =============================================================================
section("3. API KEY ROTATION")

from security import APIKeyManager

# Test with mocked keys
manager = APIKeyManager()
manager.primary_key = "primary-key-12345"
manager.secondary_key = "secondary-key-67890"

test("Primary key validates",
     manager.validate_key("primary-key-12345"),
     "Primary key should work")

test("Secondary key validates",
     manager.validate_key("secondary-key-67890"),
     "Secondary key should work for rotation")

test("Wrong key rejected",
     not manager.validate_key("wrong-key"),
     "Wrong key should be rejected")

test("Empty key rejected",
     not manager.validate_key("") and not manager.validate_key(None),
     "Empty keys should be rejected")

# =============================================================================
# 4. Audit Logging Tests
# =============================================================================
section("4. AUDIT LOGGING")

from security import AuditLogger
import hashlib

logger = AuditLogger()

# Test key hashing
key_hash = logger._hash_key("test-api-key-12345")
test("Key hash is short (12 chars)",
     len(key_hash) == 12,
     f"Got length: {len(key_hash)}")

test("Key hash is consistent",
     key_hash == logger._hash_key("test-api-key-12345"),
     "Same key should produce same hash")

test("Different keys produce different hashes",
     logger._hash_key("key1") != logger._hash_key("key2"),
     "Different keys should have different hashes")

test("Key hash doesn't contain original key",
     "test-api-key" not in key_hash,
     f"Got: {key_hash}")

# =============================================================================
# 5. Data Retention Configuration Tests
# =============================================================================
section("5. DATA RETENTION")

from data_retention import RETENTION_PERIODS

test("Leads retention configured",
     "leads" in RETENTION_PERIODS and RETENTION_PERIODS["leads"] > 0,
     f"Got: {RETENTION_PERIODS.get('leads')}")

test("Leads retention reasonable (30-365 days)",
     30 <= RETENTION_PERIODS.get("leads", 0) <= 365,
     f"Got: {RETENTION_PERIODS.get('leads')}")

test("DVSA cache TTL is 1 day",
     RETENTION_PERIODS.get("dvsa_cache") == 1,
     f"Got: {RETENTION_PERIODS.get('dvsa_cache')}")

test("Audit logs retained longer than leads",
     RETENTION_PERIODS.get("audit_logs", 0) >= RETENTION_PERIODS.get("leads", 999),
     f"Audit: {RETENTION_PERIODS.get('audit_logs')}, Leads: {RETENTION_PERIODS.get('leads')}")

# =============================================================================
# 6. Email Template Tests
# =============================================================================
section("6. EMAIL MINIMIZATION")

from email_templates_secure import generate_lead_email_minimal

test_garage_id = "660e8400-f29c-51e5-b827-557766551111"
result = generate_lead_email_minimal(
    garage_name="Test Garage",
    distance_miles=5.0,
    vehicle_make="FORD",
    vehicle_model="FIESTA",
    vehicle_year=2020,
    failure_risk=0.35,
    top_risks=["brakes", "suspension"],
    assignment_id="550e8400-e29b-41d4-a716-446655440000",
    garage_id=test_garage_id,
)

html = result["html"]

test("Email contains portal link",
     "/portal/lead/" in html,
     "Portal link should be present")

test("Email contains assignment ID in link",
     "550e8400-e29b-41d4-a716-446655440000" in html,
     "Assignment ID should be in portal link")

test("Email contains vehicle info",
     "FORD" in html and "FIESTA" in html,
     "Vehicle info should be present")

test("Email does NOT contain customer_email placeholder",
     "customer_email" not in html.lower() and "${email}" not in html.lower(),
     "Should not have email placeholders")

test("Email contains garage unsubscribe link",
     f"/api/garage/unsubscribe/{test_garage_id}" in html,
     "Unsubscribe link should use garage ID")

# =============================================================================
# 7. UUID Validation Tests
# =============================================================================
section("7. PORTAL SECURITY (UUID Validation)")

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
        test(f"Valid UUID accepted: {valid[:20]}...", True, "")
    except ValueError:
        test(f"Valid UUID accepted: {valid[:20]}...", False, "UUID rejected")

for invalid in invalid_uuids:
    try:
        uuid.UUID(invalid)
        test(f"Invalid UUID rejected: {invalid[:20]}", False, "UUID was accepted")
    except ValueError:
        test(f"Invalid UUID rejected: {invalid[:20]}", True, "")

# =============================================================================
# 8. IP Allowlist Logic Tests
# =============================================================================
section("8. IP ALLOWLIST LOGIC")

# Test the logic directly
from security import ADMIN_ALLOWED_IPS, ADMIN_ALLOW_ALL_IPS

test("ADMIN_ALLOW_ALL_IPS is False by default",
     not ADMIN_ALLOW_ALL_IPS,
     f"Got: {ADMIN_ALLOW_ALL_IPS}")

# Test IP allowlist parsing
os.environ["ADMIN_ALLOWED_IPS"] = "203.0.113.50,198.51.100.25"
importlib.reload(security)
test("IP allowlist parsed correctly",
     "203.0.113.50" in security.ADMIN_ALLOWED_IPS and
     "198.51.100.25" in security.ADMIN_ALLOWED_IPS,
     f"Got: {security.ADMIN_ALLOWED_IPS}")

# Clean up
os.environ.pop("ADMIN_ALLOWED_IPS", None)

# =============================================================================
# 9. Production Config Verification
# =============================================================================
section("9. PRODUCTION CONFIG CHECKS")

# Check if production-unsafe settings are disabled
test("ADMIN_ALLOW_ALL_IPS not set in env",
     os.environ.get("ADMIN_ALLOW_ALL_IPS", "").lower() != "true",
     f"Got: {os.environ.get('ADMIN_ALLOW_ALL_IPS')}")

test("ENABLE_SQLITE_FALLBACK not set in env",
     os.environ.get("ENABLE_SQLITE_FALLBACK", "").lower() != "true",
     f"Got: {os.environ.get('ENABLE_SQLITE_FALLBACK')}")

test("DEBUG not enabled",
     not os.environ.get("DEBUG"),
     f"Got: {os.environ.get('DEBUG')}")

# =============================================================================
# Summary
# =============================================================================
section("SUMMARY")

print(f"\n  Total: {len(PASSED) + len(FAILED)}")
print(f"  Passed: {len(PASSED)}")
print(f"  Failed: {len(FAILED)}")

if FAILED:
    print("\n  FAILURES:")
    for name, details in FAILED:
        print(f"    - {name}: {details}")
    sys.exit(1)
else:
    print("\n  ALL TESTS PASSED!")
    sys.exit(0)
