# AutoSafe Backend - Security & Reliability Audit Report

**Date:** 2026-01-22
**Auditor:** Claude Code (Opus 4.5)
**Scope:** Full codebase audit for routine high-impact problems

## Executive Summary

This audit analyzed the AutoSafe Backend codebase (FastAPI + React) for common high-impact issues that commonly cause defects, outages, security issues, or maintenance burden. The codebase is **generally well-written** with several security best practices already in place. However, I identified **12 findings** across security, reliability, and maintenance categories that should be addressed.

---

## HIGH SEVERITY (Immediate Action Required)

### 1. Missing `pydantic` in requirements.txt
**Location:** `requirements.txt`
**Impact:** Build failures in clean environments

The application uses Pydantic (`BaseModel`, `EmailStr`, `field_validator`) extensively in `main.py:974-1018` but `pydantic` is not listed in `requirements.txt`. This works currently because FastAPI includes pydantic as a transitive dependency, but:
- Version mismatches can occur
- Explicit declaration is a best practice
- Future FastAPI versions may change pydantic handling

**Recommendation:** Add `pydantic>=2.0.0` to requirements.txt

---

### 2. Unbounded In-Memory Cache Growth
**Location:** `postcode_service.py:15`, `main.py:59-62`
**Impact:** Memory exhaustion, potential DoS

```python
# postcode_service.py:15
_postcode_cache: Dict[str, Tuple[float, float]] = {}  # No size limit!
```

The postcode cache grows indefinitely without any eviction policy. Under sustained load or attack, this can exhaust memory.

Similarly, in `main.py:59-62`:
```python
_cache = {
    "makes": {"data": None, "time": 0},
    "models": {}  # Keyed by make - grows unbounded
}
```

**Recommendation:** Use `cachetools.TTLCache` with a `maxsize` limit (already imported but not used here).

---

### 3. Pickle Deserialization of Model Artifacts
**Location:** `model_v55.py:70-81`
**Impact:** Remote code execution if model files are compromised

```python
with open(calibrator_path, 'rb') as f:
    _calibrator = pickle.load(f)  # Unsafe deserialization
```

Pickle files can execute arbitrary code when loaded. If an attacker can replace model artifacts (`platt_calibrator.pkl`, `cohort_stats.pkl`), they can achieve RCE.

**Recommendations:**
- Verify file integrity with checksums before loading
- Consider safer serialization formats (joblib with mmap, or ONNX for models)
- Ensure model files are read-only and properly secured in deployment

---

### 4. Email Address Logged in Plain Text
**Location:** `email_service.py:77`, `lead_distributor.py:132`
**Impact:** PII exposure in logs

```python
# email_service.py:77
logger.info(f"Email sent to {to_email}: {subject}")

# lead_distributor.py:132
logger.info(f"Lead {lead_id} sent to {garage.name} ({garage.email})")
```

While `main.py` has `mask_email()` function, it's not used in the email service or lead distributor modules.

**Recommendation:** Use `mask_email()` consistently across all modules or configure a logging filter.

---

## MEDIUM SEVERITY (Address Soon)

### 5. CI Security Checks Set to `continue-on-error: true`
**Location:** `.github/workflows/ci.yml:36-39, 57-64`
**Impact:** Security vulnerabilities can merge to production undetected

```yaml
- name: Run linting (optional)
  run: |
    pip install flake8
    flake8 main.py database.py --max-line-length=120 --ignore=E501,W503
  continue-on-error: true  # Failures ignored

- name: Check requirements for known vulnerabilities
  run: |
    pip install pip-audit
    pip-audit -r requirements.txt || echo "Some vulnerabilities found - review required"
  continue-on-error: true  # Vulnerabilities ignored
```

**Recommendation:** Remove `continue-on-error` and make security checks blocking.

---

### 6. Admin API Key Bypass When Not Configured
**Location:** `main.py:1103-1109`
**Impact:** Potential information disclosure if misconfigured

While requests are rejected when `ADMIN_API_KEY` is unset, error messages differ between "not configured" vs "wrong key". An attacker can distinguish configuration state.

**Recommendation:** Use consistent error messaging to prevent information leakage.

---

### 7. Missing Rate Limiting on Outcome Endpoints
**Location:** `main.py:1364-1435`
**Impact:** Enumeration attacks, spam

The `/api/garage/outcome/{assignment_id}` GET and POST endpoints have no rate limiting. An attacker could enumerate assignment IDs to discover garage/vehicle information.

**Recommendation:** Add rate limiting to outcome endpoints.

---

### 8. SQLite Connection Not Thread-Safe
**Location:** `main.py:309-319`
**Impact:** Potential data corruption under concurrent access

```python
conn = sqlite3.connect(DB_FILE)  # Default mode, not thread-safe
```

With 4 Uvicorn workers (Dockerfile line 29), concurrent SQLite access without proper isolation can cause issues.

**Recommendation:** Use `check_same_thread=False` or implement connection pooling for SQLite, or ensure SQLite is only used in read-only mode.

---

## LOW SEVERITY (Improve When Possible)

### 9. Missing Input Validation Order Optimization
**Location:** `main.py:1377-1378`
**Impact:** Wasted database query on invalid input

The result parameter validation happens after fetching the assignment from database.

**Recommendation:** Validate result parameter before database query.

---

### 10. Hardcoded Demo Data Contains Stale Dates
**Location:** `dvla_client.py:44-98`
**Impact:** Poor demo experience, confusion

```python
DEMO_VEHICLES = {
    "AB12CDE": {
        "taxDueDate": "2025-03-01",   # These dates become stale
        "motExpiryDate": "2025-06-15",
```

**Recommendation:** Generate dates dynamically relative to current date.

---

### 11. Inconsistent Column Name Handling
**Location:** `main.py:605-606` vs `main.py:846-847`
**Impact:** Potential missing data in responses

```python
# main.py:605-606 (correct names)
"risk_lamps": result.get('Risk_Lamps_Reflectors_And_Electrical_Equipment', 0.03),

# main.py:846-847 (different names - missing "And")
"lamps": result.get('Risk_Lamps_Reflectors_Electrical_Equipment', 0.03),
```

**Recommendation:** Define column name constants to ensure consistency.

---

### 12. No Retry Mechanism for Email Sending
**Location:** `email_service.py:64-88`
**Impact:** Lost lead notifications on transient failures

Email failures are logged but not retried. The `retry_failed_distributions()` function in `lead_distributor.py:148-157` is a placeholder.

**Recommendation:** Implement retry logic with exponential backoff, or use a job queue.

---

## Notable Positive Findings

The codebase demonstrates several security best practices:

1. **Constant-time API key comparison** (`main.py:1086`) - prevents timing attacks
2. **PII masking in logs** (`main.py:162-176`) - though inconsistently applied
3. **Parameterized SQL queries** - no SQL injection vulnerabilities found
4. **PostgreSQL-only writes** - prevents silent data loss to SQLite
5. **VRM hashing for logging** (`main.py:212-214`) - privacy protection
6. **Rate limiting on sensitive endpoints** - prevents abuse
7. **Global exception handler** (`main.py:178-190`) - prevents stack trace leakage
8. **Security headers** (CSP, HSTS, X-Frame-Options) - defense in depth
9. **Input validation** via Pydantic and FastAPI Query parameters
10. **Defense-in-depth SQL field validation** (`database.py:568-579`)

---

## Remediation Priority

| Priority | Finding | Effort |
|----------|---------|--------|
| P0 | #1 Missing pydantic dependency | 5 min |
| P0 | #3 Pickle deserialization risk | 2 hrs |
| P1 | #2 Unbounded cache growth | 30 min |
| P1 | #4 Email PII in logs | 30 min |
| P1 | #5 CI security checks bypassed | 15 min |
| P2 | #6 Admin key error message inconsistency | 15 min |
| P2 | #7 Missing rate limiting on outcomes | 15 min |
| P2 | #8 SQLite thread safety | 30 min |
| P3 | #9 Validation order optimization | 10 min |
| P3 | #10 Stale demo dates | 15 min |
| P3 | #11 Inconsistent column names | 20 min |
| P3 | #12 Email retry mechanism | 2 hrs |

---

## Conclusion

The AutoSafe Backend codebase is well-architected with good separation of concerns and many security best practices. The most critical issues to address are:

1. Add explicit `pydantic` dependency to requirements.txt
2. Implement bounded caches with eviction policies
3. Add integrity verification for pickle-loaded model files
4. Consistently mask PII in all logging statements

Addressing these items will significantly improve the security posture and operational reliability of the application.
