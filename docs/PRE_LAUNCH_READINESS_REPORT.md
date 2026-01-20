# AutoSafe Pre-Launch Software Readiness Report

**Assessment Date:** January 2026
**Prepared For:** AutoSafe Backend
**Environment:** Railway (Production)
**Revision:** 2.0 (with corrected assessments)

---

## Executive Summary

This report assesses the AutoSafe Backend against a comprehensive pre-launch checklist. The system is a **FastAPI-based MOT failure risk prediction engine** with ML integration, lead distribution, and garage network management.

### Overall Readiness: **NO-GO for Public Launch**

**Soft Launch (invited testers, no paid offer, close monitoring):** Conditional GO once release gates are met.

| Category | Status | Critical Issues |
|----------|--------|-----------------|
| Core User Journeys | **FAIL** | Lead flow untested; no integration tests |
| Input Validation | **PARTIAL** | Idempotency missing; stack trace exposure unknown |
| Data Integrity | **PARTIAL** | Cross-step consistency risks in lead distribution |
| Failure Resilience | **FAIL** | SQLite fallback is a launch blocker |
| Performance | **NOT TESTED** | No baseline; cannot distinguish normal from degraded |
| Security | **PARTIAL** | Admin not rate-limited; CORS too permissive |
| Environment Config | **PASS** | Production-ready |
| Monitoring | **FAIL** | No alerting; "manual checks" is not adequate |
| Deployment | **PARTIAL** | CI added but not gating deploys |
| Privacy/Compliance | **PARTIAL** | PII in logs is HIGH priority risk |

---

## Release Gates (Non-Negotiable Before Any Launch)

These must be completed before exposing the service to any real users:

### 1. CI Gating Exists and Runs Tests on Every Change
- [x] GitHub Actions runs pytest on PRs and main
- [ ] Railway deploys only from green builds (or manual deploy from green main)

### 2. PII Scrubbing in Logs + Exception Hygiene
- [ ] Stop logging raw emails; mask or hash
- [ ] Audit logs for VRM/postcode/phone/token exposure
- [ ] Exception handler returns sanitised errors and logs correlation ID only
- [ ] Do NOT log raw request bodies or exception payloads with user data

### 3. Disable or Constrain Production "DB Fallback" Behaviour
- [ ] If PostgreSQL is down, service should fail clearly (503) or degrade read-only
- [ ] SQLite fallback must NOT be used for lead persistence in production
- [ ] Silent semantic changes are unacceptable

### 4. Basic Uptime Monitoring and Alerting
- [ ] External uptime check on `/health` with email alerting
- [ ] At least one production metric: error rate (5xx) and request latency (p95)

### 5. Baseline Performance Evidence
- [ ] Load test (k6/locust) proving service handles expected traffic ×3
- [ ] Recorded p95 latency for critical endpoints

### 6. Admin Surface Hardening
- [ ] Rate limit admin endpoints
- [ ] CORS does not enable credentialed cross-origin access
- [ ] Consider rotating admin key / scoping per purpose

---

## 1. Core User Journeys

### Assessment: **FAIL**

| Requirement | Status | Evidence |
|-------------|--------|----------|
| End-to-end tests cover primary flows | **FAIL** | Endpoint tests only; no integration across DVSA → pipeline → model → response |
| Error paths tested | **PARTIAL** | Validation tested; network/integration failures not |
| Tests run automatically on deployment | **PARTIAL** | CI added but not gating Railway deploys |
| Failed critical tests block release | **MISSING** | No automated gating |

### Critical Gap: Lead Capture Flow is Untested

The business-critical flow `submit lead → save to DB → assign garages → send emails` has **no integration test coverage**. This flow involves:
- Database write
- Geographic matching
- External email service call
- Assignment record creation

**Risk:** Any regression in this flow silently breaks revenue generation.

### Primary User Flows

| Flow | Test Coverage | Risk |
|------|---------------|------|
| Risk Check (DVSA → ML → Response) | Endpoint only | **HIGH** - Integration untested |
| Lead Capture (Save → Distribute → Email) | **NONE** | **CRITICAL** - Business flow untested |
| Admin (Auth → CRUD) | Endpoint only | **MEDIUM** |

### Required Actions

1. **GATE:** Add integration test for lead distribution (mocked email)
2. **GATE:** Add integration test for DVSA → feature pipeline → model flow
3. Configure Railway to deploy only on CI success

---

## 2. Input Validation and Data Handling

### Assessment: **PARTIAL**

| Requirement | Status | Evidence |
|-------------|--------|----------|
| All inputs validated server-side | **PASS** | Pydantic + custom validators |
| Boundary conditions tested | **PARTIAL** | Some tests exist |
| Duplicate submissions handled | **PARTIAL** | No idempotency key; UUID per-request doesn't prevent duplicates |
| No stack traces exposed | **UNKNOWN** | Not verified; assume risk |
| Logs don't contain PII | **FAIL** | Emails logged; HIGH priority breach risk |

### Idempotency Gap

Current implementation generates a new UUID per request. This does NOT prevent duplicate submissions. A user refreshing after submit, or network retry, creates duplicate leads.

**Required:** Idempotency key (client-provided or payload hash) to deduplicate within a time window.

### PII in Logs - HIGH PRIORITY

**Current exposure points:**
- `database.py:276-277` - Email logged on lead save
- Exception payloads may include request data
- VRM, postcode, phone potentially logged

**Risk:** PII in logs is a breach-impact multiplier. If logs are accessed (breach, insider, misconfigured access), all user data is exposed.

### Required Actions

1. **GATE:** Mask emails in logs (e.g., `j***@example.com`)
2. **GATE:** Audit all log statements for PII (VRM, postcode, phone, tokens)
3. **GATE:** Exception handler must NOT log raw request/exception data
4. Add idempotency key support for lead submissions

---

## 3. Data Integrity and Consistency

### Assessment: **PARTIAL**

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Database writes are atomic | **PASS** | Single INSERT statements |
| No partial writes on failures | **PARTIAL** | Single statements OK; cross-step consistency at risk |
| Referential integrity enforced | **PASS** | Foreign keys in schema |
| Persistence/retrieval accuracy verified | **PARTIAL** | Basic tests only |
| Backups configured | **UNKNOWN** | Railway-managed; verify schedule |

### Cross-Step Consistency Risk: Lead Distribution

The lead distribution flow has multiple steps without transaction/outbox:

```
1. INSERT lead → 2. Find garages → 3. INSERT assignments → 4. Send emails → 5. UPDATE lead status
```

**Failure scenarios:**
- Lead saved but emails not sent ("saved but not sent")
- Email sent but assignment not recorded ("sent but not saved")
- Partial assignment creation on failure
- Retries cause duplicate emails

**Required:** Either:
- Wrap in transaction with outbox pattern, OR
- Design for idempotent retry with explicit state machine

### ML Model Consistency

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Feature pipelines validated | **PASS** | Documented in `feature_engineering_v55.py` |
| Deterministic outputs for identical inputs | **PARTIAL** | Caching hides non-determinism; not tested |
| Missing/null features handled | **PASS** | Defaults provided |

**Note:** Caching does not prove determinism. Need test asserting identical outputs with cache disabled.

### Required Actions

1. Document lead distribution failure modes
2. Add determinism test for ML model (cache disabled)
3. Verify Railway PostgreSQL backup schedule

---

## 4. Dependency and Failure-Mode Resilience

### Assessment: **FAIL** (due to SQLite fallback)

| Requirement | Status | Evidence |
|-------------|--------|----------|
| External service outages simulated | **NOT DONE** | No chaos testing |
| Timeouts and retries configured | **PASS** | httpx timeouts set |
| Graceful error responses | **PARTIAL** | See SQLite issue below |
| No cascading failures | **PASS** | Service isolation maintained |

### LAUNCH BLOCKER: SQLite Fallback in Production

**Current behaviour:** If PostgreSQL is unavailable, the service silently falls back to SQLite for ALL operations including lead persistence.

**Problems:**
1. SQLite file is ephemeral in container - data loss on redeploy
2. Silent semantic change - no indication to operators
3. Leads saved to SQLite are lost when PostgreSQL recovers
4. Inconsistent data state between databases

**Required:** In production, if PostgreSQL is down:
- Return 503 Service Unavailable for write operations
- OR degrade to read-only mode with clear indication
- NEVER silently persist to ephemeral SQLite

### External Dependencies

| Service | Timeout | Current Fallback | Required Change |
|---------|---------|------------------|-----------------|
| PostgreSQL | Pool | SQLite (UNSAFE) | Fail with 503 |
| DVSA API | 30s | SQLite lookup | OK for reads |
| Resend Email | 10s | Log + continue | OK |
| Postcodes.io | Default | Return None | OK |

### Required Actions

1. **GATE:** Disable SQLite fallback for writes in production
2. **GATE:** Return 503 when PostgreSQL unavailable for lead operations
3. Add environment flag to control fallback behaviour

---

## 5. Performance and Load Sanity

### Assessment: **NOT TESTED** - Launch Blocker

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Load test at 3x expected traffic | **NOT DONE** | No baseline exists |
| Critical endpoints meet response targets | **UNKNOWN** | No benchmarks |
| Cold-start behaviour measured | **UNKNOWN** | Not documented |
| No memory/connection growth | **UNKNOWN** | Not monitored |

**Why this is a gate:** Without a baseline, you cannot distinguish "normal" from "degrading". You will not know if the service is struggling until users complain.

### Current Configuration

- **Workers:** 4 Uvicorn workers
- **DB Pool:** 5-20 connections
- **Cache:** In-memory TTL (1 hour for makes/models)

### Required Actions

1. **GATE:** Run load test with k6/locust at 3× expected traffic
2. **GATE:** Record p95 latency for `/api/risk/v55` and `/api/leads`
3. Define acceptable thresholds (e.g., p95 < 500ms)

---

## 6. Security Controls

### Assessment: **PARTIAL**

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Auth enforced on protected routes | **PASS** | Admin API key required |
| Rate limiting applied | **PARTIAL** | Public endpoints only; admin unprotected |
| Secrets not exposed in frontend/logs | **PASS** | env vars, .gitignore |
| Debug endpoints disabled | **PASS** | None found |
| Security headers configured | **PARTIAL** | Added but needs review |

### Admin Surface Risks

**Current state:**
- Single shared API key for all admin operations
- No rate limiting on admin endpoints
- CORS `allow_origins=["*"]` enables any site to make requests

**Attack surface:**
- Credential stuffing against admin endpoints (no rate limit)
- Any malicious site can drive browser traffic to API
- Single key compromise = full admin access

### Security Headers

Current implementation includes:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Strict-Transport-Security` (HSTS)
- `Content-Security-Policy`
- `Referrer-Policy`

**Note:** `X-XSS-Protection` is largely obsolete; modern baseline relies on CSP.

### Required Actions

1. **GATE:** Add rate limiting to admin endpoints (e.g., 10/minute)
2. **GATE:** Restrict CORS once frontend domain is known
3. Consider scoped API keys or short-lived tokens
4. Review CSP policy for static pages

---

## 7. Environment and Configuration

### Assessment: **PASS**

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Production config reviewed | **PASS** | Railway env vars |
| Debug modes disabled | **PASS** | No DEBUG flags |
| Test credentials removed | **PASS** | No hardcoded creds |
| Feature flags reviewed | **N/A** | None used |
| Config stored securely | **PASS** | Railway secrets |

### Environment Variables

| Variable | Required | Status |
|----------|----------|--------|
| DATABASE_URL | Yes | Railway auto |
| PORT | Yes | Railway auto |
| ADMIN_API_KEY | Yes | Manual |
| DVSA_CLIENT_ID | Yes | Manual |
| DVSA_CLIENT_SECRET | Yes | Manual |
| DVSA_TOKEN_URL | Yes | Manual |
| RESEND_API_KEY | Optional | Manual |

`.env.example` created for documentation.

---

## 8. Monitoring, Logging, and Alerting

### Assessment: **FAIL**

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Error rates monitored | **FAIL** | Railway logs only; no metrics |
| Latency metrics captured | **MISSING** | Not implemented |
| Alerts configured | **MISSING** | No alerting |
| Logs searchable/retained | **PASS** | Railway dashboard |

**"Manual checks initially" is not an adequate mitigation** once any real users exist. You need to know the service is broken before users tell you.

### Health Endpoint

`GET /health` returns:
```json
{
  "status": "ok",
  "timestamp": "...",
  "database": "connected|disconnected|error"
}
```

### Required Actions

1. **GATE:** Set up UptimeRobot or equivalent for `/health`
2. **GATE:** Configure email alerts for downtime
3. **GATE:** Add error rate metric (5xx count) visibility
4. Add p95 latency tracking for critical endpoints

---

## 9. Deployment and Rollback

### Assessment: **PARTIAL**

| Requirement | Status | Evidence |
|-------------|--------|----------|
| CI pipeline is deterministic | **PASS** | GitHub Actions added |
| Database migrations tested | **N/A** | No migrations |
| Rollback procedure tested | **PARTIAL** | Documented, not tested |
| Health checks gate traffic | **MISSING** | Not configured |
| Previous version redeployable | **PASS** | Railway supports |

### CI Pipeline

`.github/workflows/ci.yml` includes:
- Test execution on push/PR
- Security check for hardcoded secrets
- Dependency vulnerability scan
- Docker build verification

**Gap:** Railway auto-deploys on push regardless of CI status.

### Required Actions

1. **GATE:** Configure Railway to deploy only on CI success
2. Test rollback procedure before launch
3. Document rollback in runbook with specific steps

---

## 10. Privacy, Compliance, and User Harm Controls

### Assessment: **PARTIAL**

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Privacy policy matches practices | **PASS** | `static/privacy.html` |
| Data retention/deletion verified | **PARTIAL** | Retention documented |
| Consent flows tested | **N/A** | No explicit consent |
| Outputs not misrepresented | **PASS** | Disclaimers present |
| Logs don't contain PII | **FAIL** | See Section 2 |

### Disclaimers

Present in multiple locations:
- Privacy page: "predictions are for information only"
- Footer: "Not official government advice"
- Terms page available

### PII Risk (Elevated to HIGH)

Logging PII (emails, VRMs, postcodes) creates:
- Increased breach impact
- Compliance exposure
- Inconsistency with privacy policy

**Required:** See Section 2 for PII scrubbing requirements.

---

## Launch Decision Matrix

| Launch Type | Decision | Conditions |
|-------------|----------|------------|
| **Public Launch** (real users, organic traffic, payments, external garages) | **NO-GO** | Until ALL release gates met |
| **Soft Launch** (invited testers, no payments, close monitoring) | **Conditional GO** | Once release gates met |

---

## Release Gate Checklist

All items must be checked before any launch:

### CI/CD
- [x] GitHub Actions runs tests on PR/push
- [ ] Railway deploys only from green builds

### PII & Logging
- [ ] Emails masked in all logs
- [ ] VRM/postcode/phone audit complete
- [ ] Exception handler logs correlation ID only, not request data

### Database Resilience
- [ ] SQLite fallback disabled for writes in production
- [ ] Service returns 503 when PostgreSQL unavailable for writes

### Monitoring
- [ ] UptimeRobot (or equivalent) monitoring `/health`
- [ ] Email alerts configured for downtime
- [ ] Error rate visibility exists

### Performance
- [ ] Load test completed at 3× expected traffic
- [ ] P95 latency recorded and acceptable

### Security
- [ ] Admin endpoints rate limited
- [ ] CORS restricted to known domains (or documented as acceptable risk)

---

## Sign-off

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Technical Lead | | | |
| QA/SDET | | | |
| Product Owner | | | |

**By signing, you confirm all release gates have been met or explicitly accepted as documented risk.**

---

## Appendix: Risk Register

| Risk | Severity | Likelihood | Impact | Mitigation |
|------|----------|------------|--------|------------|
| Lead distribution flow regression | **CRITICAL** | Medium | Revenue loss | Add integration tests |
| PII breach via logs | **HIGH** | Low | Regulatory, reputation | Scrub PII from logs |
| SQLite data loss | **HIGH** | Low | Data loss | Disable write fallback |
| Service down undetected | **HIGH** | Medium | User impact | Add monitoring |
| Duplicate lead submissions | **MEDIUM** | Medium | Bad UX, garage spam | Add idempotency |
| Admin credential compromise | **MEDIUM** | Low | Full access | Rate limit, scope keys |
| Performance degradation undetected | **MEDIUM** | Medium | User churn | Load test, metrics |
