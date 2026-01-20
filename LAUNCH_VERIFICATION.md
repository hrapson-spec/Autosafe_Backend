# AutoSafe Launch Verification Guide

**Document Version:** 1.0
**Last Updated:** 2026-01-20
**Classification:** Internal / Security Verification

This document provides step-by-step verification procedures for all security controls
before production launch. Each section includes test procedures and expected outcomes.

---

## Pre-Launch Checklist Overview

| # | Control | Priority | Status |
|---|---------|----------|--------|
| 1 | Admin IP Allowlist | CRITICAL | [ ] |
| 2 | API Key Rotation | CRITICAL | [ ] |
| 3 | Audit Logging | CRITICAL | [ ] |
| 4 | PII Redaction | CRITICAL | [ ] |
| 5 | Data Retention | HIGH | [ ] |
| 6 | GDPR Erasure | HIGH | [ ] |
| 7 | Cache Controls | HIGH | [ ] |
| 8 | SQLite Disabled | HIGH | [ ] |
| 9 | Email Minimization | HIGH | [ ] |
| 10 | Portal Security | HIGH | [ ] |
| 11 | CORS Configuration | MEDIUM | [ ] |
| 12 | Privacy Notice | MEDIUM | [ ] |
| 13 | Processor Register | MEDIUM | [ ] |
| 14 | Load Test Safety | LOW | [ ] |

---

## 1. Admin IP Allowlist Verification

### Configuration

```bash
# Production environment variables required:
ADMIN_ALLOWED_IPS=203.0.113.50,198.51.100.25  # Your office/VPN IPs
ADMIN_ALLOW_ALL_IPS=false  # MUST be false in production
```

### Test Procedure

1. **Set ADMIN_ALLOWED_IPS to your fixed office/VPN egress IPs only**
   ```bash
   # Verify no broad ranges (e.g., 0.0.0.0/0, 10.0.0.0/8)
   echo $ADMIN_ALLOWED_IPS
   # Should show specific IPs like: 203.0.113.50,198.51.100.25
   ```

2. **Test from allowlisted IP (should return 200)**
   ```bash
   curl -H "X-API-Key: $ADMIN_API_KEY" \
        https://your-app.railway.app/api/admin/retention/status
   # Expected: 200 OK with retention data
   ```

3. **Test from non-allowlisted IP (should return 403)**
   ```bash
   # From a different network (mobile hotspot, different VPN exit)
   curl -H "X-API-Key: $ADMIN_API_KEY" \
        https://your-app.railway.app/api/admin/retention/status
   # Expected: 403 Forbidden "Access denied: IP not in allowlist"
   ```

4. **Verify trusted proxy handling**
   ```bash
   # Check logs show correct client IP (not Railway proxy IP)
   # The logged IP should match your actual egress IP
   railway logs | grep "Admin access"
   ```

### Evidence to Capture
- [ ] Screenshot: Environment variables (redacted)
- [ ] Screenshot: 200 response from allowlisted IP
- [ ] Screenshot: 403 response from non-allowlisted IP
- [ ] Log entry showing correct client IP evaluation

---

## 2. API Key Rotation Verification

### Configuration

```bash
# Set both keys for rotation
ADMIN_API_KEY=primary-key-min-32-chars-here-abc123
ADMIN_API_KEY_SECONDARY=secondary-key-for-rotation-xyz789
```

### Test Procedure

1. **Verify primary key works**
   ```bash
   curl -H "X-API-Key: $ADMIN_API_KEY" \
        https://your-app.railway.app/api/admin/retention/status
   # Expected: 200 OK
   ```

2. **Verify secondary key works**
   ```bash
   curl -H "X-API-Key: $ADMIN_API_KEY_SECONDARY" \
        https://your-app.railway.app/api/admin/retention/status
   # Expected: 200 OK
   ```

3. **Remove old key and verify it no longer works**
   ```bash
   # After removing ADMIN_API_KEY_SECONDARY from env:
   curl -H "X-API-Key: old-secondary-key" \
        https://your-app.railway.app/api/admin/retention/status
   # Expected: 401 Unauthorized
   ```

4. **Verify keys are never logged**
   ```bash
   # Search logs for key values
   railway logs | grep -i "admin_api_key\|X-API-Key"
   # Should NOT find actual key values, only "[REDACTED]" or key hashes
   ```

### Evidence to Capture
- [ ] Screenshot: Primary key authentication success
- [ ] Screenshot: Secondary key authentication success
- [ ] Screenshot: Old key rejection after rotation
- [ ] Log search showing keys are not logged

---

## 3. Audit Logging Verification

### Test Procedure

1. **Perform admin actions and verify logging**
   ```bash
   # List leads
   curl -H "X-API-Key: $ADMIN_API_KEY" \
        https://your-app.railway.app/api/leads

   # Check logs
   railway logs | grep "AUDIT"
   ```

2. **Verify log entries contain required fields**
   ```
   Expected log format:
   {"timestamp": "...", "type": "AUDIT", "message": "{
     'action': 'list',
     'resource': 'leads',
     'client_ip': '203.0.113.50',
     'key_hash': 'a1b2c3d4e5f6',
     'path': '/api/leads',
     'method': 'GET'
   }"}
   ```

3. **Verify audit log retention is configured**
   ```bash
   echo $RETENTION_AUDIT_DAYS
   # Default: 365 days
   ```

### Required Fields in Audit Logs
- [ ] Timestamp
- [ ] Client IP (evaluated, not proxy)
- [ ] Key hash (not full key)
- [ ] Action (list, create, update, delete)
- [ ] Resource
- [ ] Outcome

### Evidence to Capture
- [ ] Sample audit log entries for each action type
- [ ] Verification that key hash (not full key) is logged

---

## 4. PII Redaction Verification

### Test Procedure

1. **Create a synthetic PII test request**
   ```bash
   # Submit a request containing VRM, email, postcode, phone
   # This will create a lead with test data
   curl -X POST https://your-app.railway.app/api/leads \
        -H "Content-Type: application/json" \
        -d '{
          "email": "test-pii-check@example.com",
          "postcode": "SW1A 1AA",
          "phone": "07700900123",
          "name": "Test User"
        }'
   ```

2. **Force an error to test exception logging**
   ```bash
   # Request with invalid data to trigger error logging
   curl -X POST https://your-app.railway.app/api/leads \
        -H "Content-Type: application/json" \
        -d '{"email": "invalid"}'
   ```

3. **Check logs for PII exposure**
   ```bash
   # Search for raw PII values
   railway logs | grep -E "test-pii-check@example.com|SW1A 1AA|07700900123"
   # Should return NO matches

   # Search for redacted values
   railway logs | grep -E "\[REDACTED\]|\[REDACTED_EMAIL\]|\[REDACTED_VRM\]"
   # Should show redacted versions
   ```

4. **Verify patterns are redacted**
   - [ ] Email addresses → `[REDACTED]@domain.com`
   - [ ] Full postcodes → `SW1A [REDACTED]`
   - [ ] Phone numbers → `[REDACTED_PHONE]`
   - [ ] Vehicle registrations → `[REDACTED_VRM]`

### Evidence to Capture
- [ ] Log search showing no raw PII
- [ ] Log samples showing redacted values
- [ ] Exception log showing PII redaction

---

## 5. Data Retention Verification

### Configuration

```bash
RETENTION_LEADS_DAYS=90
RETENTION_ASSIGNMENTS_DAYS=90
RETENTION_AUDIT_DAYS=365
RETENTION_INACTIVE_GARAGES_DAYS=365
```

### Test Procedure

1. **Configure a scheduled daily retention job**
   ```bash
   # Railway cron or equivalent - run daily at 3 AM UTC
   # POST /api/admin/retention/cleanup?dry_run=false
   ```

2. **Run manual dry run**
   ```bash
   curl -X POST \
        -H "X-API-Key: $ADMIN_API_KEY" \
        "https://your-app.railway.app/api/admin/retention/cleanup?dry_run=true"
   ```

3. **Review dry run results**
   ```json
   {
     "timestamp": "2026-01-20T...",
     "dry_run": true,
     "tasks": {
       "expired_leads": {"deleted_count": 5, "dry_run": true},
       "orphaned_assignments": {"deleted_count": 0, "dry_run": true},
       "inactive_garages": {"deleted_count": 0, "dry_run": true}
     }
   }
   ```

4. **Run actual cleanup (dry_run=false)**
   ```bash
   curl -X POST \
        -H "X-API-Key: $ADMIN_API_KEY" \
        "https://your-app.railway.app/api/admin/retention/cleanup?dry_run=false"
   ```

5. **Set up alerting on failure**
   - Configure Railway/monitoring to alert if cleanup job fails
   - Test alert delivery

### Evidence to Capture
- [ ] Dry run output with deletion counts
- [ ] Actual run output with deletion counts
- [ ] Scheduled job configuration
- [ ] Alert configuration

---

## 6. GDPR Erasure Verification

### Test Procedure

1. **Create a test lead**
   ```bash
   curl -X POST https://your-app.railway.app/api/leads \
        -H "Content-Type: application/json" \
        -d '{
          "email": "gdpr-test-delete@example.com",
          "postcode": "SW1A 1AA",
          "name": "GDPR Test User"
        }'
   # Note the lead_id returned
   ```

2. **Run erasure dry run**
   ```bash
   curl -X POST \
        -H "X-API-Key: $ADMIN_API_KEY" \
        -H "Content-Type: application/json" \
        "https://your-app.railway.app/api/admin/retention/delete-subject" \
        -d '{"email": "gdpr-test-delete@example.com", "dry_run": true}'
   ```

3. **Execute actual erasure**
   ```bash
   curl -X POST \
        -H "X-API-Key: $ADMIN_API_KEY" \
        -H "Content-Type: application/json" \
        "https://your-app.railway.app/api/admin/retention/delete-subject" \
        -d '{"email": "gdpr-test-delete@example.com", "dry_run": false}'
   ```

4. **Verify complete removal**
   - [ ] Lead no longer in `leads` table
   - [ ] Assignments no longer in `lead_assignments` table
   - [ ] Portal returns 404 for any associated assignments
   - [ ] Email NOT logged in audit log (only action logged)

5. **Verify caches are cleared**
   - DVSA cache is cleared after erasure
   - Postcode cache does not contain PII

### Evidence to Capture
- [ ] Dry run erasure output
- [ ] Actual erasure output
- [ ] Verification of removal from all tables
- [ ] Audit log showing action without email

---

## 7. Cache Controls Verification

### Test Procedure

1. **Verify DVSA cache TTL**
   ```bash
   # Check cache stats
   curl -H "X-API-Key: $ADMIN_API_KEY" \
        https://your-app.railway.app/api/admin/retention/status
   # Look for dvsa_cache section showing TTL and non-exportable status
   ```

2. **Confirm cache cannot be exported**
   - No endpoint exposes raw cache contents
   - Cache stats show `"exportable": false`

3. **Verify cache keys don't expose PII**
   - Cache keys should be hashed or use non-identifiable keys

4. **Confirm cache clears on shutdown**
   - Check shutdown logs for "DVSA cache cleared" message

### Evidence to Capture
- [ ] Cache stats showing TTL enforcement
- [ ] Confirmation no export endpoint exists
- [ ] Shutdown log showing cache cleared

---

## 8. SQLite Fallback Verification

### Test Procedure

1. **Confirm disabled in production**
   ```bash
   # Check environment
   echo $RAILWAY_ENVIRONMENT
   # Should be: production

   echo $ENABLE_SQLITE_FALLBACK
   # Should be: unset or false

   # Check startup logs
   railway logs | grep -i sqlite
   # Should show: "SQLite fallback DISABLED in production"
   ```

2. **Verify no local file persistence**
   ```bash
   # Check for any local data files
   ls -la *.db *.sqlite *.json 2>/dev/null
   # Should show only expected files (not data dumps)
   ```

3. **Check for debug dumps or temp files**
   - No `/tmp` data exports
   - No debug data files
   - No export endpoints

### Evidence to Capture
- [ ] Log showing SQLite disabled
- [ ] File listing showing no unexpected data files

---

## 9. Email Minimization Verification

### Test Procedure

1. **Create a test lead and trigger email**
   ```bash
   # Create lead (requires garage in system)
   curl -X POST https://your-app.railway.app/api/leads \
        -H "Content-Type: application/json" \
        -d '{
          "email": "email-test@example.com",
          "postcode": "SW1A 1AA",
          "name": "Email Test",
          "phone": "07700900123"
        }'
   ```

2. **Check Resend dashboard for email content**
   - Customer email address: NOT in body
   - Customer phone: NOT in body
   - Customer name: NOT in body (or minimal)
   - Portal link: Present
   - Vehicle/risk info: Present (minimal)

3. **Verify only minimum fields sent**
   ```
   Allowed in email:
   - Vehicle make, model, year
   - Failure risk percentage
   - Top risk areas
   - Estimated job value
   - Distance
   - Portal link (with assignment ID)

   NOT allowed in email:
   - Customer email
   - Customer phone
   - Customer name
   - Full postcode
   ```

4. **Confirm Resend DPA on file**
   - [ ] Resend Data Processing Agreement signed
   - [ ] Recorded in vendor register

### Evidence to Capture
- [ ] Screenshot of email content (no PII)
- [ ] Resend DPA document reference

---

## 10. Portal Security Verification

### Test Procedure

1. **Test rate limiting**
   ```bash
   # Make 31 requests in 1 minute
   for i in {1..31}; do
     curl -s -o /dev/null -w "%{http_code}\n" \
       "https://your-app.railway.app/portal/lead/invalid-uuid"
   done
   # Last request should return 429 (rate limited)
   ```

2. **Test UUID validation**
   ```bash
   # Invalid format
   curl "https://your-app.railway.app/portal/lead/not-a-uuid"
   # Expected: 404

   # SQL injection attempt
   curl "https://your-app.railway.app/portal/lead/'; DROP TABLE leads;--"
   # Expected: 404 (invalid UUID format)
   ```

3. **Test enumeration resistance**
   ```bash
   # Random UUID should return 404 with no info leakage
   curl "https://your-app.railway.app/portal/lead/550e8400-e29b-41d4-a716-446655440000"
   # Expected: 404 "Lead not found" (generic message)
   ```

4. **Verify access logging**
   ```bash
   # Access a valid portal link
   railway logs | grep "Portal access"
   # Should show: assignment ID (truncated), garage ID (truncated), IP
   ```

5. **Verify garage cannot access another garage's lead**
   - Create two garages
   - Assign lead to garage A
   - Attempt to access with garage B's assignment ID (not possible - IDs are unique)
   - Note: Current design uses UUID secrecy; future: add garage auth tokens

### Evidence to Capture
- [ ] Rate limit test results
- [ ] UUID validation test results
- [ ] Portal access log samples

---

## 11. CORS Configuration Verification

### Test Procedure

1. **Verify explicit origins**
   ```bash
   echo $CORS_ORIGINS
   # Should show: https://autosafe.co.uk,https://www.autosafe.co.uk
   # NO wildcards (*)
   ```

2. **Test CORS preflight from allowed origin**
   ```bash
   curl -X OPTIONS \
        -H "Origin: https://autosafe.co.uk" \
        -H "Access-Control-Request-Method: POST" \
        https://your-app.railway.app/api/leads \
        -v 2>&1 | grep "Access-Control"
   # Expected: Access-Control-Allow-Origin: https://autosafe.co.uk
   ```

3. **Test CORS from disallowed origin**
   ```bash
   curl -X OPTIONS \
        -H "Origin: https://evil.com" \
        -H "Access-Control-Request-Method: POST" \
        https://your-app.railway.app/api/leads \
        -v 2>&1 | grep "Access-Control"
   # Expected: No Access-Control-Allow-Origin header
   ```

4. **Verify credentials disabled**
   ```bash
   # Check response headers
   curl -I -H "Origin: https://autosafe.co.uk" \
        https://your-app.railway.app/api/leads
   # Should NOT include: Access-Control-Allow-Credentials: true
   ```

### Evidence to Capture
- [ ] CORS_ORIGINS environment variable
- [ ] Preflight response from allowed origin
- [ ] Preflight response from disallowed origin

---

## 12. Privacy Notice Verification

### Required Updates

Update `static/privacy.html` to include:

1. **Data Collected**
   - [ ] Email address
   - [ ] Postcode
   - [ ] Name (optional)
   - [ ] Phone (optional)
   - [ ] Vehicle registration
   - [ ] IP address (for rate limiting and security)

2. **Purpose**
   - [ ] MOT risk prediction
   - [ ] Lead matching with local garages
   - [ ] Service improvement

3. **Retention Periods**
   - [ ] Leads: 90 days
   - [ ] Vehicle data cache: 24 hours

4. **Third-Party Sharing**
   - [ ] What data is shared with garages
   - [ ] What happens after sharing (garage responsibility)

5. **Legal Basis**
   - [ ] Consent for lead submission
   - [ ] Legitimate interest for predictions

### Evidence to Capture
- [ ] Updated privacy notice URL
- [ ] Just-in-time consent text on submission form

---

## 13. Processor/DPA Register

### Required Documentation

| Processor | Service | Data Processed | DPA Status | International Transfer |
|-----------|---------|----------------|------------|----------------------|
| Railway | Hosting | All app data | [ ] On file | US - check safeguards |
| Resend | Email | Lead notifications (minimal) | [ ] On file | US - check safeguards |
| DVSA | API | Vehicle registration | Gov API terms | UK only |
| Postcodes.io | API | Postcodes (non-PII) | N/A | UK only |

### Required Actions
- [ ] Railway DPA/Terms reviewed and on file
- [ ] Resend DPA signed and on file
- [ ] Document international transfer safeguards (UK Addendum/IDTA)
- [ ] Maintain vendor register with all processors

### Evidence to Capture
- [ ] Supplier register spreadsheet/document
- [ ] DPA document references for each processor

---

## 14. Load Test Safety

### Requirements

1. **Use synthetic data only**
   - [ ] No real customer data in tests
   - [ ] Use @example.com emails
   - [ ] Use test postcodes (ZZ99 9ZZ)

2. **Verify test data doesn't leak**
   ```bash
   # After load test, check logs
   railway logs | grep -E "real-email|real-postcode"
   # Should return no matches
   ```

3. **No unexpected third-party calls**
   - [ ] DVSA API: Use mock/sandbox if available
   - [ ] Resend: Use test mode or mock
   - [ ] Postcodes.io: Acceptable (public, non-PII)

### Evidence to Capture
- [ ] Load test configuration showing synthetic data
- [ ] Log sample showing no real PII

---

## Launch Evidence Pack

Compile the following before launch:

### Security Configuration
- [ ] Production env vars screenshot (secrets redacted)
- [ ] ADMIN_ALLOWED_IPS configuration

### Access Control Tests
- [ ] Admin 403 test from non-allowlisted IP
- [ ] Admin 200 test from allowlisted IP
- [ ] API key rotation test results

### Data Protection Tests
- [ ] PII redaction test logs
- [ ] Retention cleanup run logs
- [ ] GDPR erasure test proof
- [ ] Portal access test proof

### External Verification
- [ ] CORS preflight test results
- [ ] Resend email content screenshot (no PII)

### Compliance Documentation
- [ ] Supplier register with DPAs
- [ ] Published privacy notice URL
- [ ] Consent mechanism screenshots

---

## Post-Launch Monitoring

### Daily Checks
- [ ] Retention job ran successfully
- [ ] No PII in error logs
- [ ] Admin access audit log reviewed

### Weekly Checks
- [ ] Retention statistics reviewed
- [ ] Portal access patterns reviewed
- [ ] Failed authentication attempts reviewed

### Monthly Checks
- [ ] Full audit log review
- [ ] Processor/DPA register updated
- [ ] Privacy notice accuracy verified

---

## Emergency Procedures

### Data Breach Response
1. Identify scope of breach
2. Notify DPO within 24 hours
3. Document in breach register
4. Notify ICO within 72 hours if required
5. Notify affected individuals if high risk

### GDPR Erasure Emergency
```bash
# Immediate erasure (no dry run)
curl -X POST \
     -H "X-API-Key: $ADMIN_API_KEY" \
     -H "Content-Type: application/json" \
     "https://your-app.railway.app/api/admin/retention/delete-subject" \
     -d '{"email": "affected@example.com", "dry_run": false}'
```

### Cache Emergency Clear
```bash
# Trigger application restart to clear all caches
railway restart
```

---

*Document maintained by: Security Team*
*Review schedule: Before each deployment*
