# AutoSafe Backend Security Audit Report

**Audit Date:** 2026-01-20
**Auditor:** Security Review
**Scope:** Full codebase security assessment
**Classification:** INTERNAL - CONFIDENTIAL

---

## Executive Summary

This security audit identified **4 Critical**, **6 High**, **8 Medium**, and **5 Low** severity findings. The most significant issues are:

1. **IDOR vulnerability** in outcome reporting endpoints (no authentication)
2. **No dependency lockfile** enabling supply chain attacks
3. **Missing security headers** (CSP, HSTS, X-Frame-Options)
4. **PII logged in plaintext** (GDPR compliance risk)

**Overall Security Posture:** MODERATE RISK - Requires remediation before production deployment.

---

## 1. Threat Model

### 1.1 Entry Points Inventory

| Endpoint | Method | Auth | Rate Limit | Risk Level |
|----------|--------|------|------------|------------|
| `/health` | GET | None | None | Low |
| `/api/makes` | GET | None | 100/min | Low |
| `/api/models` | GET | None | 100/min | Low |
| `/api/risk` | GET | None | 20/min | Medium |
| `/api/risk/v55` | GET | None | 20/min | Medium |
| `/api/leads` | POST | None | 10/min | High |
| `/api/leads` | GET | API Key | 30/min | High |
| `/api/admin/garages` | ALL | API Key | **None** | Critical |
| `/api/garage/outcome/{id}` | GET/POST | **None** | **None** | Critical |

### 1.2 Attack Paths

#### Path 1: IDOR → Data Manipulation → Business Impact
```
1. Attacker intercepts/guesses assignment_id (UUID in email links)
2. Calls GET /api/garage/outcome/{assignment_id} → Reads vehicle/garage info
3. Calls GET /api/garage/outcome/{assignment_id}?result=won → Manipulates metrics
4. Impact: Falsified conversion data, skewed business metrics, competitive intelligence
```

#### Path 2: Supply Chain → Dependency Hijack → RCE
```
1. Attacker compromises PyPI package (no lockfile = no integrity check)
2. pip install fetches malicious version
3. Code execution during build/runtime
4. Impact: Full system compromise, data exfiltration
```

#### Path 3: PII Exposure → GDPR Violation → Legal/Financial
```
1. Logs contain garage emails, customer postcodes, vehicle info
2. Log aggregation service compromise OR insider threat
3. PII exfiltration from logs
4. Impact: GDPR fines (4% revenue), reputational damage
```

#### Path 4: Admin Key Theft → Full Admin Access
```
1. Attacker obtains ADMIN_API_KEY (env var leak, logs, repo)
2. Full access to all admin endpoints
3. Can create/modify garages, access all leads with PII
4. Impact: Complete business data access, data manipulation
```

### 1.3 Security Invariants (MUST Always Hold)

1. **AuthZ-01:** Only authenticated admin users can access lead PII
2. **AuthZ-02:** Only the assigned garage can report outcomes for their assignments
3. **DATA-01:** Secrets (API keys, tokens) never appear in logs or responses
4. **DATA-02:** Customer PII is never logged in plaintext
5. **INT-01:** All dependencies have verified integrity (lockfile + hashes)
6. **AUDIT-01:** All admin actions are logged with actor identity

---

## 2. Findings

### CRITICAL SEVERITY

#### VULN-001: Broken Access Control (IDOR) on Outcome Endpoints
- **Component:** `main.py:983-1035`
- **CWE:** CWE-639 (Authorization Bypass Through User-Controlled Key)
- **CVSS:** 8.6 (High)

**Description:**
The `/api/garage/outcome/{assignment_id}` endpoints have NO authentication. Anyone with an assignment_id can:
- Read assignment details (vehicle info, garage name)
- Modify outcomes (won/lost/no_response)
- Manipulate garage conversion metrics

**Evidence:**
```python
@app.get("/api/garage/outcome/{assignment_id}")
async def get_outcome_page(assignment_id: str, result: Optional[str] = None):
    # NO AUTH CHECK
    assignment = await db.get_lead_assignment_by_id(assignment_id)
```

**Impact:** Data integrity compromise, falsified business metrics, potential competitive intelligence gathering.

**Remediation:**
1. Add authentication via signed tokens in outcome URLs
2. Implement garage-specific verification (e.g., garage must authenticate via email link token)
3. Rate limit outcome reporting
4. Add audit logging for all outcome changes

---

#### VULN-002: No Dependency Lockfile (Supply Chain Risk)
- **Component:** `requirements.txt`
- **CWE:** CWE-829 (Inclusion of Functionality from Untrusted Control Sphere)
- **CVSS:** 9.8 (Critical)

**Description:**
No `requirements.lock` or `Pipfile.lock` exists. Dependencies are specified with loose version constraints (e.g., `catboost>=1.2`). This allows:
- Non-reproducible builds
- Dependency confusion attacks
- Automatic installation of compromised package versions

**Evidence:**
```
$ ls *.lock
No lockfiles found

$ cat requirements.txt
catboost>=1.2  # No upper bound, no hash
```

**Impact:** Complete system compromise via malicious dependency injection.

**Remediation:**
1. Generate `requirements.lock` with pinned versions and hashes:
   ```bash
   pip-compile --generate-hashes requirements.txt -o requirements.lock
   ```
2. Use `pip install --require-hashes -r requirements.lock` in Dockerfile
3. Enable Dependabot/Renovate for automated security updates
4. Consider using `pip-audit` in CI pipeline

---

#### VULN-003: Missing Security Headers
- **Component:** `main.py` (CORS middleware only)
- **CWE:** CWE-693 (Protection Mechanism Failure)
- **CVSS:** 7.5 (High)

**Description:**
No security headers are configured:
- No Content-Security-Policy (XSS mitigation)
- No Strict-Transport-Security (HTTPS enforcement)
- No X-Frame-Options (Clickjacking prevention)
- No X-Content-Type-Options (MIME sniffing prevention)

**Evidence:**
```bash
$ grep -rn "Content-Security-Policy\|X-Frame-Options" *.py
No security headers found
```

**Impact:** Increased vulnerability to XSS, clickjacking, and downgrade attacks.

**Remediation:**
Add security headers middleware:
```python
from starlette.middleware import Middleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware

@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com"
    return response
```

---

#### VULN-004: No Rate Limiting on Admin Endpoints
- **Component:** `main.py:874-977`
- **CWE:** CWE-770 (Allocation of Resources Without Limits)
- **CVSS:** 7.5 (High)

**Description:**
Admin endpoints (`/api/admin/garages/*`) have no rate limiting, enabling:
- Brute force attacks on API key
- Resource exhaustion via rapid requests
- Data scraping if API key is compromised

**Evidence:**
```python
@app.post("/api/admin/garages", status_code=201)
async def create_garage(request: Request, garage: GarageSubmission):
    # NO @limiter.limit() decorator
```

**Impact:** API key brute force, DoS, data exfiltration.

**Remediation:**
Add rate limiting to all admin endpoints:
```python
@app.post("/api/admin/garages", status_code=201)
@limiter.limit("10/minute")  # Add this
async def create_garage(request: Request, garage: GarageSubmission):
```

---

### HIGH SEVERITY

#### VULN-005: PII Logged in Plaintext
- **Component:** `lead_distributor.py:123`, `database.py:276`
- **CWE:** CWE-532 (Insertion of Sensitive Information into Log File)
- **CVSS:** 6.5 (Medium)

**Description:**
Sensitive PII is logged in plaintext:
- Garage email addresses
- Customer postcodes
- Vehicle information

**Evidence:**
```python
# lead_distributor.py:123
logger.info(f"Lead {lead_id} sent to {garage.name} ({garage.email})")

# database.py:276
logger.info(f"Lead saved: {lead_data.get('postcode')} - {vehicle.get('make')} {vehicle.get('model')}")
```

**Impact:** GDPR violation, privacy breach if logs are accessed.

**Remediation:**
1. Redact PII from logs:
   ```python
   logger.info(f"Lead {lead_id} sent to garage {garage.garage_id}")
   ```
2. Implement structured logging with PII filtering
3. Ensure log retention policies comply with GDPR

---

#### VULN-006: Single Admin API Key (No RBAC)
- **Component:** `main.py:805-833`
- **CWE:** CWE-269 (Improper Privilege Management)
- **CVSS:** 6.8 (Medium)

**Description:**
Single `ADMIN_API_KEY` provides all-or-nothing admin access:
- No role differentiation (read-only vs. write)
- No per-user audit trail
- No key rotation mechanism
- No scope limitation

**Evidence:**
```python
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY")

if not api_key or api_key != ADMIN_API_KEY:
    raise HTTPException(status_code=401, detail="Invalid or missing API key")
```

**Impact:** Compromised key = full admin access, no accountability.

**Remediation:**
1. Implement proper authentication (JWT, OAuth)
2. Add role-based access control (RBAC)
3. Implement API key rotation
4. Add audit logging with user identity

---

#### VULN-007: BASE_URL Used in Emails Without Validation
- **Component:** `email_templates.py:9, 244-256`
- **CWE:** CWE-601 (URL Redirection to Untrusted Site)
- **CVSS:** 5.4 (Medium)

**Description:**
`BASE_URL` from environment variable is used directly in email links without validation. Misconfiguration could lead to phishing links in legitimate emails.

**Evidence:**
```python
BASE_URL = os.environ.get("BASE_URL", "https://autosafe.co.uk")
# Used in email templates:
href="{BASE_URL}/api/garage/outcome/{assignment_id}?result=won"
```

**Impact:** Phishing attacks if BASE_URL is misconfigured.

**Remediation:**
1. Validate BASE_URL format on startup
2. Enforce HTTPS scheme
3. Consider allowlist of valid domains

---

#### VULN-008: SQL Table Names in f-strings
- **Component:** `build_db.py:55, 78, 86, 113-116`
- **CWE:** CWE-89 (SQL Injection)
- **CVSS:** 4.0 (Medium) - mitigated by constant usage

**Description:**
SQL table names are constructed using f-strings. While currently using constants, this pattern is dangerous if ever modified to use user input.

**Evidence:**
```python
TABLE_NAME = 'risks'  # Constant - safe
cursor.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")
cursor.execute(f'CREATE INDEX IF NOT EXISTS idx_model_id ON {TABLE_NAME} (model_id)')
```

**Impact:** Currently mitigated, but creates dangerous pattern.

**Remediation:**
1. Use constant table names or allowlist validation
2. Add comment documenting that TABLE_NAME must never be user-controlled

---

#### VULN-009: DVSA/Resend API Secrets in Memory
- **Component:** `dvsa_client.py:151-155`, `email_service.py:13-16`
- **CWE:** CWE-316 (Cleartext Storage of Sensitive Information in Memory)
- **CVSS:** 4.4 (Medium)

**Description:**
OAuth client secrets and API keys are stored in module-level variables, persisting in memory for the application lifetime.

**Impact:** Memory dump could expose secrets.

**Remediation:**
1. Consider using a secrets manager (AWS Secrets Manager, HashiCorp Vault)
2. Minimize secret lifetime in memory where possible

---

#### VULN-010: No Input Sanitization on Lead Form
- **Component:** `main.py:725-750`
- **CWE:** CWE-20 (Improper Input Validation)
- **CVSS:** 4.3 (Medium)

**Description:**
Lead submission accepts `name` and `phone` fields with minimal validation. While stored safely (parameterized queries), they're included in emails sent to garages.

**Evidence:**
```python
class LeadSubmission(BaseModel):
    name: Optional[str] = None  # No length limit, no sanitization
    phone: Optional[str] = None  # No format validation
```

**Impact:** Potential for abuse (spam, inappropriate content in emails).

**Remediation:**
1. Add length limits to name/phone fields
2. Add phone number format validation
3. Consider content filtering for name field

---

### MEDIUM SEVERITY

#### VULN-011: Unbounded DVSA Cache in main.py
- **Component:** `main.py:51-54`
- **CWE:** CWE-400 (Uncontrolled Resource Consumption)

**Description:**
The `_cache` dict for makes/models has no size limit.

**Remediation:** Use `cachetools.TTLCache` with maxsize.

---

#### VULN-012: Missing Audit Logging
- **Component:** All admin endpoints
- **CWE:** CWE-778 (Insufficient Logging)

**Description:**
No audit trail for admin actions (garage create/update, lead access).

**Remediation:** Implement structured audit logging with actor, action, timestamp.

---

#### VULN-013: Demo Mode in Production Risk
- **Component:** `main.py:164-177`
- **CWE:** CWE-489 (Active Debug Code)

**Description:**
Demo/mock data is returned if DATABASE_URL not set. Could leak into production.

**Remediation:** Fail fast if critical config missing in production.

---

#### VULN-014: Race Condition in Cache
- **Component:** `main.py:216`
- **CWE:** CWE-362 (Race Condition)

**Description:**
Cache TTL check has time-of-check-to-time-of-use (TOCTOU) issue.

**Remediation:** Use atomic cache operations or locking.

---

#### VULN-015: No Email Verification
- **Component:** `main.py:753-790`
- **CWE:** CWE-287 (Improper Authentication)

**Description:**
Lead emails are not verified before distributing to garages.

**Impact:** Fake leads, email harvesting.

**Remediation:** Implement email verification flow.

---

#### VULN-016: Pagination Without Maximum
- **Component:** `main.py:801-802`
- **CWE:** CWE-770 (Resource Exhaustion)

**Description:**
`limit` parameter allows up to 500 records per request.

**Remediation:** Reduce maximum limit, implement cursor-based pagination.

---

#### VULN-017: No HTTPS Redirect
- **Component:** `main.py`
- **CWE:** CWE-319 (Cleartext Transmission)

**Description:**
No explicit HTTPS redirect in application code.

**Remediation:** Add HTTPSRedirectMiddleware or ensure Railway handles this.

---

#### VULN-018: innerHTML Usage (Low Risk)
- **Component:** `static/script.js:195`
- **CWE:** CWE-79 (XSS)

**Description:**
`innerHTML = ''` is used to clear content. While safe, pattern exists.

**Remediation:** Use `textContent` or DOM manipulation instead.

---

### LOW SEVERITY

#### VULN-019: Verbose Error Messages
- **Component:** Various
- **CWE:** CWE-209 (Information Exposure)

**Description:** Error messages may reveal internal details.

---

#### VULN-020: No Request ID for Tracing
- **Component:** All endpoints
- **CWE:** CWE-778 (Insufficient Logging)

**Description:** No correlation IDs for request tracing.

---

#### VULN-021: Missing .env.example
- **Component:** Repository root
- **CWE:** N/A (Best Practice)

**Description:** No example environment file for developers.

---

#### VULN-022: Global Singleton Pattern
- **Component:** `dvsa_client.py:414-423`
- **CWE:** N/A (Code Quality)

**Description:** Global mutable state complicates testing.

---

#### VULN-023: Incomplete Feature: retry_failed_distributions
- **Component:** `lead_distributor.py`
- **CWE:** N/A (Functionality)

**Description:** Function implemented but not exposed via endpoint.

---

## 3. Baseline & Environment

### 3.1 Build Reproducibility

| Check | Status | Notes |
|-------|--------|-------|
| Lockfile present | FAIL | No requirements.lock |
| Pinned versions | PARTIAL | Some use `>=`, no upper bounds |
| Deterministic builds | FAIL | Dependencies can change between builds |
| Dockerfile best practices | PARTIAL | No multi-stage, runs as root |

### 3.2 Environment Configuration

| Variable | Required | Validated | Notes |
|----------|----------|-----------|-------|
| DATABASE_URL | Yes | No | Falls back to SQLite |
| ADMIN_API_KEY | Yes | No | No format validation |
| DVSA_CLIENT_ID | Yes | No | Warnings if missing |
| DVSA_CLIENT_SECRET | Yes | No | Warnings if missing |
| RESEND_API_KEY | Yes | No | Silent failure if missing |
| CORS_ORIGINS | No | Partial | Defaults to localhost |
| BASE_URL | No | No | Used in emails |

---

## 4. Remediation Priority

### Immediate (Before Production)

1. **VULN-001:** Add authentication to outcome endpoints
2. **VULN-002:** Generate lockfile with hashes
3. **VULN-003:** Add security headers middleware
4. **VULN-004:** Add rate limiting to admin endpoints

### Short-term (Within 30 days)

5. **VULN-005:** Redact PII from logs
6. **VULN-006:** Implement proper admin authentication
7. **VULN-007:** Validate BASE_URL
8. **VULN-010:** Add input validation to lead form

### Medium-term (Within 90 days)

9. **VULN-012:** Implement audit logging
10. **VULN-015:** Add email verification
11. Implement RBAC for admin access
12. Add automated security scanning to CI

---

## 5. Security Posture Summary

| Category | Score | Notes |
|----------|-------|-------|
| Authentication | 4/10 | Single API key, no user auth |
| Authorization | 3/10 | IDOR vulnerability, no RBAC |
| Input Validation | 6/10 | Pydantic models, but gaps exist |
| Data Protection | 5/10 | PII in logs, no encryption at rest |
| Supply Chain | 2/10 | No lockfile, no SCA |
| Logging/Monitoring | 4/10 | Basic logging, no audit trail |
| Infrastructure | 6/10 | Railway handles HTTPS, but no headers |

**Overall: 4.3/10 - MODERATE RISK**

---

## 6. Operational Security Requirements

The following items require **operational/infrastructure changes** beyond code fixes:

### 6.1 Admin Identity & Authentication (VULN-006 Full Remediation)

The code fix (AuditLogger) provides visibility but doesn't fully address the "single admin key" risk.

**Required operational changes:**

| Requirement | Priority | Implementation |
|-------------|----------|----------------|
| Unique admin identities | Critical | SSO integration (Auth0, Okta, etc.) |
| MFA enforcement | High | Require TOTP/WebAuthn for admin access |
| Short-lived sessions | High | JWT with 15-minute expiry + refresh tokens |
| Key rotation | Medium | Automated quarterly rotation, revocation support |
| Least privilege roles | Medium | RBAC: `admin:read`, `admin:write`, `admin:super` |
| Emergency access procedure | Low | Break-glass accounts with separate audit |

### 6.2 CI/CD Security (VULN-002 Enforcement)

The `requirements.lock` file exists but requires CI enforcement:

```yaml
# Example GitHub Actions enforcement
- name: Verify lockfile is used
  run: |
    if [ ! -f requirements.lock ]; then
      echo "ERROR: requirements.lock missing"
      exit 1
    fi
    pip install --require-hashes -r requirements.lock

- name: Run pip-audit
  run: pip-audit --strict -r requirements.lock
```

**Recommended update cadence:** Weekly automated Dependabot PRs, monthly security review.

### 6.3 HTTPS Infrastructure (VULN-017 Full Remediation)

Code includes:
- HSTS header (1 year, includeSubDomains)
- HTTP→HTTPS redirect middleware (checks X-Forwarded-Proto)

**Railway/infrastructure requirements:**

| Requirement | Status | Action Needed |
|-------------|--------|---------------|
| TLS termination | ✓ Railway handles | Verify cert is valid |
| HTTP redirect at edge | ? | Confirm Railway config redirects HTTP→HTTPS |
| No HTTP-only endpoints | Check | Audit all subdomains |
| HSTS preload (optional) | Not set | Consider adding if all subdomains support HTTPS |

### 6.4 Input Validation Schema (VULN-010 Enhancement)

Current code has Pydantic validators. Full remediation requires:

1. **Request schema enforcement** at API gateway level (not just application)
2. **Normalization rules** for international characters, Unicode normalization
3. **Rate limiting per email/phone** to prevent enumeration
4. **Defense-in-depth**: Output encoding verified in email templates

### 6.5 Secrets Management

Current: Environment variables with fallback defaults.

**Recommended migration path:**

```
Phase 1: Remove all fallback defaults that could be insecure
Phase 2: Validate required secrets at startup (fail fast)
Phase 3: Migrate to secrets manager (Railway secrets, AWS Secrets Manager)
Phase 4: Implement secret rotation without downtime
```

### 6.6 Cache Thread Safety Verification

Code includes `threading.Lock` for TTLCache access. Verify in deployment:

- **Uvicorn workers=4**: Each worker is a separate process (cache not shared)
- **Within each worker**: Async tasks share the cache (lock protects)
- **Monitoring**: Add cache hit/miss metrics to verify behavior

---

## 7. Remediation Status & Sign-Off

### 7.1 VULN-001 Token Security Properties (CRITICAL → MITIGATED)

The email-link token implementation has the following explicit security properties:

| Property | Implementation | Status |
|----------|----------------|--------|
| **Short-lived** | 48-hour expiry (`TOKEN_EXPIRY_SECONDS = 48 * 60 * 60`) | ✓ Implemented |
| **Scoped** | Token contains assignment_id, verified against URL | ✓ Implemented |
| **Unguessable** | HMAC-SHA256 signed with SECRET_KEY (32-byte signature) | ✓ Implemented |
| **Single-use** | Outcomes cannot be overwritten once recorded | ✓ Implemented |
| **Not logged** | Tokens not in server logs (generic errors only) | ✓ Implemented |
| **Referrer protection** | `Referrer-Policy: no-referrer` for outcome endpoints | ✓ Implemented |
| **Timing-safe** | `hmac.compare_digest()` for constant-time comparison | ✓ Implemented |
| **Generic errors** | "Authentication required" for all auth failures | ✓ Implemented |

**Remaining considerations:**
- Token appears in email body (acceptable - email is the auth channel)
- Token appears in URL query string (acceptable for email links, but users should not share URLs)
- Browser history will contain tokens (mitigated by 48h expiry)

**Verdict:** MITIGATED for intended threat model. Token provides capability-based auth for garage outcome reporting.

### 7.2 VULN-005 PII Logging Compensating Controls (HIGH → ACCEPTED WITH CONTROLS)

Per business requirement, customer details remain logged. Compensating controls:

| Control | Implementation | Owner |
|---------|----------------|-------|
| **Structured JSON logging** | All logs use JSON format for parsing | ✓ Code |
| **Log retention policy** | Must comply with GDPR 6-year limit | ⚠ Ops |
| **Access control on logs** | Railway/log provider must restrict access | ⚠ Ops |
| **Audit trail for log access** | Log aggregator must track who views logs | ⚠ Ops |
| **No PII in error responses** | `sanitize_error_message()` strips paths/SQL | ✓ Code |
| **Secure log transmission** | HTTPS to log aggregator | ⚠ Ops |

**Required operational actions:**
1. Configure Railway/log provider access controls
2. Enable audit logging on log viewer access
3. Set log retention to comply with GDPR (max 6 years, prefer shorter)
4. Document lawful basis for processing in privacy policy

**Verdict:** ACCEPTED RISK with documented compensating controls.

### 7.3 VULN-006 Single Admin Key (HIGH → PARTIALLY MITIGATED)

| Mitigation | Status | Notes |
|------------|--------|-------|
| Audit logging | ✓ Code | All admin actions logged with actor, timestamp, IP |
| Rate limiting | ✓ Code | 20-30/min on admin endpoints |
| Unique identities | ⚠ Ops | Requires SSO integration |
| MFA | ⚠ Ops | Requires auth provider |
| Key rotation | ⚠ Ops | Manual process currently |

**Verdict:** PARTIALLY MITIGATED. Visibility improved via audit logging, but full remediation requires operational changes (SSO/MFA).

### 7.4 Overall Sign-Off Status

| Category | Before | After | Notes |
|----------|--------|-------|-------|
| IDOR (VULN-001) | Critical | **Mitigated** | Token auth with explicit properties |
| Supply Chain (VULN-002) | Critical | **Mitigated** | Lockfile exists, CI enforcement needed |
| Security Headers (VULN-003) | Critical | **Resolved** | CSP, HSTS, X-Frame-Options added |
| Admin Rate Limit (VULN-004) | Critical | **Resolved** | Rate limiting on all admin endpoints |
| PII Logging (VULN-005) | High | **Accepted** | Business requirement with controls |
| Single Admin Key (VULN-006) | High | **Partial** | Audit logging only, needs SSO |
| BASE_URL (VULN-007) | High | **Resolved** | Strict allowlist validation |
| SQL Safety (VULN-008) | High | **Resolved** | Documented, static allowlist |

**Items NOT addressed (out of scope for code changes):**
- CSRF/cookie security (no cookie-based auth used)
- SSRF (no user-controlled URL fetching)
- File uploads (not implemented)
- Authentication hardening (requires auth provider)

**Sign-off recommendation:**
> Suitable for **early-stage/low-traffic production** with the documented operational controls in place.
> NOT recommended for high-value targets or regulated environments without completing Section 6 operational requirements.

---

## 8. Appendix

### 8.1 Files Reviewed

- `main.py` (1055 lines)
- `database.py` (722 lines)
- `dvsa_client.py` (432 lines)
- `lead_distributor.py` (219 lines)
- `email_service.py` (94 lines)
- `email_templates.py` (366 lines)
- `postcode_service.py` (155 lines)
- `build_db.py` (198 lines)
- `tests/test_api.py` (217 lines)
- `static/script.js` (323 lines)
- `Dockerfile`
- `requirements.txt`
- `.gitignore`

### 8.2 Tools Used

- Manual code review
- Grep pattern matching
- Git history analysis
- Dependency analysis

### 8.3 References

- OWASP Top 10 2021
- CWE/SANS Top 25
- GDPR Article 32 (Security of Processing)
- UK ICO Data Protection Guidelines
