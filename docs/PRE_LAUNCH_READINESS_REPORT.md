# AutoSafe Pre-Launch Software Readiness Report

**Assessment Date:** January 2026
**Prepared For:** AutoSafe Backend
**Environment:** Railway (Production)

---

## Executive Summary

This report assesses the AutoSafe Backend against a comprehensive pre-launch checklist covering core functionality, security, data integrity, monitoring, and compliance. The system is a **FastAPI-based MOT failure risk prediction engine** with ML integration.

### Overall Readiness: **CONDITIONAL PASS**

| Category | Status | Critical Issues |
|----------|--------|-----------------|
| Core User Journeys | **PARTIAL** | Automated deployment tests missing |
| Input Validation | **PASS** | Minor improvements needed |
| Data Integrity | **PASS** | Atomic operations implemented |
| Failure Resilience | **PASS** | Graceful fallbacks in place |
| Performance | **NOT TESTED** | Load testing required |
| Security | **PARTIAL** | Security headers missing |
| Environment Config | **PASS** | Production-ready |
| Monitoring | **PARTIAL** | Alerts not configured |
| Deployment | **PARTIAL** | CI/CD pipeline missing |
| Privacy/Compliance | **PASS** | Privacy policy complete |

---

## 1. Core User Journeys

### Assessment

| Requirement | Status | Evidence |
|-------------|--------|----------|
| End-to-end tests cover primary flows | **PARTIAL** | `tests/test_api.py` covers API endpoints but lacks E2E flows |
| Error paths tested | **PARTIAL** | Validation errors tested, network failures not |
| Tests run automatically on deployment | **MISSING** | No CI/CD pipeline configured |
| Failed critical tests block release | **MISSING** | No automated gating |

### Primary User Flows Identified

1. **Risk Check Flow:** User enters registration → API fetches DVSA data → ML prediction → Display results
2. **Lead Capture Flow:** User submits contact details → Save to DB → Distribute to garages → Email sent
3. **Admin Flow:** Authenticate → Manage garages → View leads

### Current Test Coverage

```
tests/
├── test_api.py         - 23 tests covering API endpoints
├── test_banding.py     - 13 tests for age/mileage bands
├── test_confidence.py  - Confidence interval calculations
├── test_defects.py     - Defect processing
└── test_dvla.py        - DVLA client integration
```

### Gaps

- [ ] **No CI/CD pipeline** - Tests exist but aren't run on every deployment
- [ ] **No E2E flow tests** - Individual endpoints tested, but not full user journeys
- [ ] **No lead distribution tests** - Critical business flow untested

### Recommendations

1. **CRITICAL:** Create `.github/workflows/test.yml` for automated test runs
2. Add end-to-end tests covering:
   - Full risk check with DVSA integration
   - Lead submission through to email delivery (mocked)
3. Configure Railway to only deploy on test success

---

## 2. Input Validation and Data Handling

### Assessment

| Requirement | Status | Evidence |
|-------------|--------|----------|
| All inputs validated server-side | **PASS** | Pydantic models + custom validators |
| Boundary conditions tested | **PARTIAL** | Some tests exist, needs expansion |
| Duplicate submissions handled | **PASS** | UUIDs prevent exact duplicates |
| No stack traces exposed | **PARTIAL** | Needs verification |
| Logs don't contain PII | **PARTIAL** | Emails logged in some places |

### Validation Implementation

**Registration/VRM:** `dvsa_client.py:214-256`
- Alphanumeric only (hard reject)
- Length 2-8 characters
- Normalized (uppercase, no spaces)

**Email:** `main.py:734-743`
- Basic format validation (@ with domain)
- Lowercased and trimmed

**Postcode:** `main.py:745-750`
- Minimum 3 characters
- Uppercased and trimmed

**Query Parameters:** `main.py:316-318`
- Year: 1990-2026 (ge/le constraints)
- Make/Model: min/max length

### Gaps

- [ ] Logs contain email addresses (`database.py:276-277`)
- [ ] Stack traces may leak on unhandled exceptions
- [ ] No explicit idempotency keys for lead submissions

### Recommendations

1. Add global exception handler to sanitize error responses:
   ```python
   @app.exception_handler(Exception)
   async def global_exception_handler(request, exc):
       logger.error(f"Unhandled error: {exc}")
       return JSONResponse(status_code=500, content={"detail": "Internal server error"})
   ```
2. Mask email in logs (show only domain)
3. Add idempotency key support for lead submissions

---

## 3. Data Integrity and Consistency

### Assessment

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Database writes are atomic | **PASS** | Single INSERT statements |
| No partial writes on failures | **PASS** | No multi-statement transactions |
| Referential integrity enforced | **PASS** | Foreign keys in schema |
| Persistence/retrieval accuracy verified | **PARTIAL** | Basic tests only |
| Backups configured | **UNKNOWN** | Railway-managed PostgreSQL |

### ML Model Consistency

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Feature pipelines validated | **PASS** | `feature_engineering_v55.py` documented |
| Deterministic outputs for identical inputs | **PASS** | Cached responses |
| Missing/null features handled | **PASS** | Defaults provided |

### Database Operations

- **Lead saves:** Atomic single INSERT (`database.py:253-275`)
- **Garage saves:** Atomic single INSERT (`database.py:390-416`)
- **Lead assignments:** Single INSERT with NOW() timestamps
- **Outcome updates:** Single UPDATE statement

### Recommendations

1. Verify Railway PostgreSQL backup schedule (usually daily)
2. Add database migration scripts for future schema changes
3. Document recovery procedure in runbook

---

## 4. Dependency and Failure-Mode Resilience

### Assessment

| Requirement | Status | Evidence |
|-------------|--------|----------|
| External service outages simulated | **NOT DONE** | No chaos testing |
| Timeouts and retries configured | **PASS** | httpx timeouts set |
| Graceful error responses | **PASS** | Fallbacks return default values |
| No cascading failures | **PASS** | Service isolation maintained |
| Jobs/queues fail safely | **N/A** | No background queue system |

### External Dependencies

| Service | Timeout | Fallback | Status |
|---------|---------|----------|--------|
| DVSA API | 30s | SQLite lookup | **PASS** |
| PostgreSQL | Connection pool | SQLite | **PASS** |
| Resend Email | 10s | Log warning, continue | **PASS** |
| Postcodes.io | Default | Return None | **PASS** |

### Fallback Chain

```
DVSA API (real-time)
    ↓ (on failure)
SQLite Lookup (pre-computed)
    ↓ (on failure)
Population Averages (hardcoded)
```

### Evidence

**DVSA fallback:** `main.py:483-520`
```python
except VehicleNotFoundError:
    return await _fallback_prediction(...)
except DVSAAPIError as e:
    return await _fallback_prediction(...)
```

**Database fallback:** `main.py:75-82`
```python
if os.path.exists(DB_FILE):
    DATABASE_URL = None
elif DATABASE_URL:
    await db.get_pool()
```

### Recommendations

1. Document all failure modes in runbook
2. Consider adding retry logic for transient DVSA errors

---

## 5. Performance and Load Sanity

### Assessment

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Load test at 3x expected traffic | **NOT DONE** | No load test results |
| Critical endpoints meet response targets | **UNKNOWN** | No benchmarks |
| Cold-start behaviour measured | **UNKNOWN** | Not documented |
| No memory/connection growth | **UNKNOWN** | Not monitored |

### Current Configuration

- **Workers:** 4 Uvicorn workers (`Dockerfile:29`)
- **DB Pool:** 5-20 connections (`database.py:35`)
- **Cache:** In-memory TTL caches (1 hour for makes/models)

### Rate Limits (slowapi)

| Endpoint | Limit |
|----------|-------|
| `/api/makes` | 100/minute |
| `/api/models` | 100/minute |
| `/api/risk` | 20/minute |
| `/api/risk/v55` | 20/minute |
| `/api/leads` | 10/minute |

### Recommendations

1. **CRITICAL:** Conduct load test with locust or k6
2. Target metrics:
   - P95 response time < 500ms for `/api/risk/v55`
   - P95 response time < 100ms for `/api/makes`
   - Support 100 concurrent users
3. Add performance monitoring (e.g., Railway metrics dashboard)

---

## 6. Security Controls

### Assessment

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Auth enforced on protected routes | **PASS** | Admin API key required |
| Rate limiting applied | **PASS** | slowapi configured |
| Secrets not exposed in frontend/logs | **PASS** | env vars, .gitignore |
| Debug endpoints disabled | **PASS** | No debug routes found |
| Security headers configured | **MISSING** | No CSP, HSTS, etc. |

### Authentication

**Admin Endpoints:** Require `X-API-Key` header
- `POST /api/admin/garages`
- `GET /api/admin/garages`
- `GET /api/leads`
- `PATCH /api/admin/garages/{id}`

**Validation:** `main.py:871-873`
```python
if not ADMIN_API_KEY or not api_key or api_key != ADMIN_API_KEY:
    raise HTTPException(status_code=401, detail="Invalid or missing API key")
```

### SQL Injection Prevention

- Uses parameterized queries throughout (`$1`, `$2` placeholders)
- Tested in `test_api.py:207-213`

### Secrets Management

| Secret | Storage | Exposure Risk |
|--------|---------|---------------|
| DATABASE_URL | Railway env | **LOW** |
| ADMIN_API_KEY | Railway env | **LOW** |
| DVSA_CLIENT_SECRET | Railway env | **LOW** |
| RESEND_API_KEY | Railway env | **LOW** |

### Gaps

- [ ] No security headers (CSP, HSTS, X-Frame-Options)
- [ ] CORS allows all origins (`allow_origins=["*"]`)

### Recommendations

1. **IMPORTANT:** Add security headers middleware:
   ```python
   from starlette.middleware import Middleware
   from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware

   # Add security headers
   @app.middleware("http")
   async def add_security_headers(request, call_next):
       response = await call_next(request)
       response.headers["X-Content-Type-Options"] = "nosniff"
       response.headers["X-Frame-Options"] = "DENY"
       response.headers["X-XSS-Protection"] = "1; mode=block"
       response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
       return response
   ```
2. Restrict CORS to specific domains when known
3. Add rate limiting to admin endpoints

---

## 7. Environment and Configuration

### Assessment

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Production config reviewed | **PASS** | Railway env vars |
| Debug modes disabled | **PASS** | No DEBUG flags found |
| Test credentials removed | **PASS** | No hardcoded creds |
| Feature flags reviewed | **N/A** | No feature flags used |
| Config stored securely | **PASS** | Railway secrets |

### Environment Variables

| Variable | Required | Default | Set |
|----------|----------|---------|-----|
| DATABASE_URL | Yes | - | Railway auto |
| PORT | Yes | 8000 | Railway auto |
| ADMIN_API_KEY | Yes | - | Manual |
| DVSA_CLIENT_ID | Yes | - | Manual |
| DVSA_CLIENT_SECRET | Yes | - | Manual |
| DVSA_TOKEN_URL | Yes | - | Manual |
| RESEND_API_KEY | Optional | - | Manual |
| EMAIL_FROM | Optional | onboarding@resend.dev | Manual |

### Configuration Files

- `.env` files are gitignored (`.gitignore:13-15`)
- No `.env.example` file found (should be created)

### Recommendations

1. Create `.env.example` with placeholder values
2. Document all required environment variables in README

---

## 8. Monitoring, Logging, and Alerting

### Assessment

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Error rates monitored | **PARTIAL** | Railway logs only |
| Latency metrics captured | **MISSING** | Not implemented |
| Job/queue failures tracked | **N/A** | No queue system |
| Alerts configured | **MISSING** | No alerts |
| Logs searchable/retained | **PASS** | Railway dashboard |

### Current Logging

**Format:** JSON structured (`main.py:43-47`)
```python
format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}'
```

**Key Events Logged:**
- Model loading
- Database connections
- DVSA API calls
- Lead submissions
- Email send results
- Errors

### Health Endpoint

`GET /health` returns:
```json
{
  "status": "ok",
  "timestamp": "2026-01-20T...",
  "database": "connected|disconnected|error"
}
```

### Gaps

- [ ] No uptime monitoring configured
- [ ] No alerting for errors
- [ ] No latency metrics
- [ ] No business metrics (leads/day, predictions/day)

### Recommendations

1. **CRITICAL:** Set up UptimeRobot for `/health` monitoring
2. Add email alerts for:
   - Health check failures
   - Error rate spikes
   - DVSA API failures
3. Consider adding Plausible/Umami for analytics (per `docs/MONITORING.md`)

---

## 9. Deployment and Rollback

### Assessment

| Requirement | Status | Evidence |
|-------------|--------|----------|
| CI pipeline is deterministic | **MISSING** | No CI/CD |
| Database migrations tested | **N/A** | No migrations |
| Rollback procedure tested | **PARTIAL** | Documented, not tested |
| Health checks gate traffic | **MISSING** | Not configured |
| Previous version redeployable | **PASS** | Railway supports this |

### Current Deployment

- **Trigger:** GitHub push to `main`
- **Platform:** Railway auto-deploy
- **Container:** Python 3.9-slim, 4 workers
- **Health:** `/health` endpoint exists

### Rollback Procedure

Documented in `docs/RUNBOOK.md:46-53`:
1. Go to Railway Dashboard → Deployments
2. Find last working deployment
3. Click Rollback

### Gaps

- [ ] No CI/CD pipeline (`.github/workflows/` is empty)
- [ ] No automated testing before deploy
- [ ] Health check doesn't gate traffic

### Recommendations

1. **CRITICAL:** Create `.github/workflows/ci.yml`:
   ```yaml
   name: CI
   on: [push, pull_request]
   jobs:
     test:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
         - uses: actions/setup-python@v5
           with:
             python-version: '3.9'
         - run: pip install -r requirements.txt
         - run: python -m pytest tests/ -v
   ```
2. Configure Railway to require passing checks
3. Test rollback procedure before launch

---

## 10. Privacy, Compliance, and User Harm Controls

### Assessment

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Privacy policy matches practices | **PASS** | `static/privacy.html` |
| Data retention/deletion verified | **PARTIAL** | Retention documented |
| Consent flows tested | **N/A** | No explicit consent needed |
| Export/access requests supported | **PASS** | Email contact provided |
| Outputs not misrepresented | **PASS** | Disclaimers present |
| Disclaimers visible | **PASS** | Multiple locations |

### Privacy Implementation

**Data Collection (per privacy.html):**
- VRM: Not stored (processed in real-time)
- Postcode: Not stored
- IP address: Retained 30 days (Railway logs)
- Leads (email/name/phone): Stored until deletion requested

**Data Sharing:**
- Lead data shared with matched garages (by design)
- No third-party marketing sharing

### User Harm Controls

**Disclaimers Present:**

1. Privacy page (`static/privacy.html:241-249`):
   > "These predictions are for information only"
   > "They do not guarantee any particular MOT outcome"
   > "They have no legal effect on you"

2. Footer (`static/index.html:170`):
   > "Data from UK DVSA • Not official government advice or endorsement"

3. Terms page available at `/static/terms.html`

### Automated Decision-Making

ML predictions are disclosed and explicitly non-binding:
- Confidence levels shown to users
- "Population average" fallback clearly labelled
- No automated actions taken on predictions

### Recommendations

1. Add a visible disclaimer on the results page above the risk score
2. Document data deletion process in runbook
3. Consider adding GDPR data export functionality

---

## Launch Readiness Declaration

### Critical Items Status

| Item | Status | Owner | Mitigation |
|------|--------|-------|------------|
| CI/CD Pipeline | **NOT COMPLETE** | - | Manual testing before deploy |
| Load Testing | **NOT COMPLETE** | - | Monitor closely post-launch |
| Security Headers | **NOT COMPLETE** | - | Accept risk for soft launch |
| Uptime Monitoring | **NOT COMPLETE** | - | Manual checks initially |

### Known Issues

| Issue | Severity | Mitigation |
|-------|----------|------------|
| No automated tests on deploy | High | Run tests manually pre-deploy |
| Security headers missing | Medium | Add in first post-launch update |
| Logs contain email addresses | Low | Non-critical for MVP |
| CORS allows all origins | Low | Acceptable for public API |

### Pre-Launch Checklist

- [ ] All environment variables set in Railway
- [ ] DVSA OAuth credentials tested
- [ ] Resend email configuration verified
- [ ] Admin API key set and secured
- [ ] Manual smoke test of all endpoints
- [ ] Rollback procedure understood
- [ ] Incident response contact identified

### Recommended Pre-Launch Actions

1. **Run full test suite manually:**
   ```bash
   python -m pytest tests/ -v
   ```

2. **Verify health endpoint:**
   ```bash
   curl https://autosafebackend-production.up.railway.app/health
   ```

3. **Test primary user flow:**
   - Enter valid VRM
   - Verify prediction displayed
   - Submit lead form
   - Confirm email received by test garage

4. **Set up UptimeRobot** (free tier)

### Sign-off

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Technical Lead | | | |
| QA/SDET | | | |
| Product Owner | | | |

---

## Appendix A: Action Item Summary

### Critical (Block Launch)

1. Manual test execution before any production deploy
2. Verify all environment variables are set

### High Priority (First Week Post-Launch)

1. Add `.github/workflows/ci.yml` for automated testing
2. Configure UptimeRobot monitoring
3. Add security headers middleware
4. Conduct basic load test

### Medium Priority (First Month)

1. Add E2E tests for full user journeys
2. Set up error alerting
3. Create `.env.example` file
4. Add latency metrics

### Low Priority (Ongoing)

1. Implement log sanitization for PII
2. Add business metrics dashboard
3. Document data deletion process
4. Consider GDPR export functionality
