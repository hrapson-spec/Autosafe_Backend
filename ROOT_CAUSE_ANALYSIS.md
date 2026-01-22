# Root Cause Analysis: AutoSafe UX Issues

**Date**: 2026-01-22
**Analyst**: Claude
**Scope**: User experience issues identified during testing

---

## Executive Summary

Five critical UX issues were identified in the AutoSafe application. This document analyzes the root cause of each issue using the **5 Whys** technique and provides recommendations for systemic improvements.

---

## Issue 1: Demo Mode - Hardcoded Ford Fiesta

### Symptom
All vehicle lookups returned Ford Fiesta 2018 data regardless of the registration entered.

### Location
`App.tsx:27-29`

```tsx
// DEMO MODE: Use backend demo data until DVLA API is configured
// This simulates a Ford Fiesta lookup for any registration
const result = await getReportBySelection('FORD', 'FIESTA', 2018);
```

### 5 Whys Analysis

1. **Why was Ford Fiesta hardcoded?**
   The code was intentionally written as "demo mode" while waiting for DVSA API configuration.

2. **Why wasn't the real API used?**
   The DVSA API requires OAuth 2.0 credentials (client ID, client secret, token URL) which weren't configured.

3. **Why weren't credentials available?**
   DVSA API access requires a formal application and approval process from the UK government.

4. **Why wasn't there automatic detection and fallback?**
   The `getReportByRegistration` function existed but the frontend never attempted to use it first.

5. **Why wasn't this flagged during code review?**
   The comment indicated it was temporary ("until DVLA API is configured"), but there was no ticket, TODO tracking, or feature flag to ensure it was addressed.

### Root Cause
**Missing feature flag and graceful degradation pattern.** The developer created a reasonable temporary solution but:
- No detection mechanism to check if DVSA API was configured
- No automatic switch between demo and production modes
- No visible indicator to users that they were seeing demo data
- No tracking/alerting when demo mode was active in production

### Contributing Factors
- **Technical debt not tracked**: "DEMO MODE" comment was informal
- **No integration tests**: Tests didn't verify actual registration lookup flow
- **Decoupled development**: Frontend was developed independently of backend API availability

### Recommendations
1. Implement feature flags with environment-based auto-detection
2. Add health check that verifies DVSA connectivity on startup
3. Show "demo mode" badge in UI when external APIs unavailable
4. Add monitoring alert when demo mode is active in production
5. Create integration test that verifies real vehicle lookup

---

## Issue 2: API Response Type Mismatch

### Symptom
Frontend TypeScript types expected `Failure_Risk` (PascalCase), but backend returned `failure_risk` (lowercase).

### Location
- **Frontend types**: `types.ts`, `services/autosafeApi.ts`
- **Backend response**: `main.py:591-608`

### 5 Whys Analysis

1. **Why were the types different?**
   Frontend types were modeled after database column names (PascalCase), while backend API used Python/REST conventions (snake_case).

2. **Why didn't frontend types match the actual API?**
   Frontend and backend were developed in parallel or by different contributors without a shared API contract.

3. **Why wasn't there a shared API schema?**
   No OpenAPI/Swagger documentation was enforced as the source of truth.

4. **Why wasn't this caught during testing?**
   The demo mode (Issue 1) meant `getReportByRegistration` was never actually called, so the type mismatch was never exercised.

5. **Why wasn't there type validation?**
   TypeScript types are compile-time only; no runtime validation (e.g., Zod, io-ts) was used.

### Root Cause
**Missing API contract and type synchronization mechanism.** The frontend types were written based on assumptions about the backend, not actual responses.

### Contributing Factors
- **SQLite column names leaked to types**: Types like `Total_Tests`, `Failure_Risk` matched database schema, not API
- **Backend transformation layer**: Backend reads DB columns (`Failure_Risk`) and transforms to API format (`failure_risk`), but frontend wasn't updated
- **Demo mode masked the bug**: Real API calls never happened

### Recommendations
1. Generate TypeScript types from OpenAPI schema (e.g., `openapi-typescript`)
2. Add runtime response validation with Zod or io-ts
3. Create contract tests that verify frontend types match backend responses
4. Document API response format in OpenAPI/Swagger
5. Use consistent naming convention across stack (recommend snake_case for API)

---

## Issue 3: Registration Validation Too Restrictive

### Symptom
Form only accepted post-2001 registration format (`AB12 CDE`), rejecting valid older formats.

### Location
`components/HeroForm.tsx:11`

```tsx
const UK_REG_PATTERN = /^[A-Z]{2}[0-9]{2}\s?[A-Z]{3}$/i;
```

### 5 Whys Analysis

1. **Why was only one format supported?**
   The regex was written for the most common modern format without considering historical formats.

2. **Why weren't other formats researched?**
   The developer likely wasn't aware of the complexity of UK registration history (formats changed in 1963, 1983, and 2001).

3. **Why wasn't this validated against real-world data?**
   No user research or dataset analysis was performed to understand the distribution of registration formats.

4. **Why wasn't server-side validation used?**
   The DVSA API already handles VRM normalization (`dvsa_client.normalize_vrm()`), but frontend added redundant validation.

5. **Why duplicate frontend validation?**
   Common pattern for UX (immediate feedback), but became overly restrictive without aligning with backend capabilities.

### Root Cause
**Frontend validation duplicated backend logic with incomplete domain knowledge.** The developer implemented strict validation without understanding the full UK registration format history.

### Contributing Factors
- **Lack of domain expertise**: UK registration format history is complex
- **No validation parity**: Frontend and backend validation weren't synchronized
- **Missing test data**: No test cases for older registration formats

### Recommendations
1. Defer complex validation to backend (single source of truth)
2. Use permissive frontend validation (length + alphanumeric only)
3. Add test cases covering all UK registration formats
4. Document supported formats in user-facing help text
5. Consider using DVLA/DVSA validation libraries if available

---

## Issue 4: Error Handling Deficiencies

### Symptom
Errors appeared without dismiss button, didn't auto-clear, and showed technical messages.

### Location
`App.tsx:145-153`

### 5 Whys Analysis

1. **Why no dismiss button?**
   Error display was implemented as a simple conditional render without interactive controls.

2. **Why no auto-dismiss?**
   No `useEffect` timer was added to clear errors after a timeout.

3. **Why weren't these patterns considered?**
   Error handling was treated as secondary concern, implemented minimally to "show something."

4. **Why was it deprioritized?**
   Focus was on happy path functionality; error UX often gets less attention in early development.

5. **Why wasn't there a design system for errors?**
   No Toast/Notification component library was adopted, leading to ad-hoc implementations.

### Root Cause
**Ad-hoc error handling without UX patterns.** Errors were implemented as an afterthought rather than a first-class UX concern.

### Contributing Factors
- **No design system**: Custom error display instead of component library
- **Happy path focus**: Demo mode meant errors rarely occurred during development
- **No error taxonomy**: Different error types (user error, network, server) weren't distinguished

### Recommendations
1. Create reusable Toast/Notification component
2. Implement error taxonomy (user, network, server, rate-limit)
3. Add auto-dismiss with duration based on severity
4. Include dismiss button and keyboard support (Escape)
5. Add retry mechanism for transient errors

---

## Issue 5: Hardcoded Mileage (50,000 miles)

### Symptom
All predictions used 50,000 miles regardless of actual vehicle mileage.

### Location
- `services/autosafeApi.ts:114` - Function default
- `services/autosafeApi.ts:299` - Comment "Default mileage estimate"
- `services/autosafeApi.ts:307` - Selection object

### 5 Whys Analysis

1. **Why was mileage hardcoded?**
   The DVSA API returns last MOT odometer reading, but DVLA API doesn't provide current mileage.

2. **Why wasn't user input collected?**
   The form (`HeroForm`) was designed for minimal friction - only registration and postcode required.

3. **Why was 50,000 chosen?**
   It's approximately the UK average mileage for a 5-year-old car. Developer chose a "reasonable default."

4. **Why wasn't this flagged as a data quality issue?**
   Mileage significantly impacts MOT risk prediction, but the impact of using defaults wasn't quantified.

5. **Why no optional mileage field?**
   UX decision prioritized simplicity over accuracy, without offering an "advanced" option.

### Root Cause
**Product decision to minimize form friction without quantifying accuracy trade-off.** The hardcoded value was a conscious choice, but the impact on prediction quality wasn't communicated.

### Contributing Factors
- **Data availability gap**: DVLA doesn't provide current mileage
- **UX simplicity bias**: Fewer fields = higher conversion (assumed, not validated)
- **No accuracy indicator**: Users don't know predictions use estimated mileage
- **Missing user research**: No data on whether users would provide mileage if asked

### Recommendations
1. Add optional mileage field: "Know your mileage? (optional)"
2. Show data sources: "Mileage: 50,000 mi (estimated)" vs "Mileage: 67,234 mi (from MOT)"
3. Quantify accuracy impact: Compare predictions with estimated vs. actual mileage
4. A/B test mileage field: Measure conversion impact
5. Fetch from DVSA: If MOT history available, use last recorded odometer

---

## Systemic Issues Identified

### 1. Integration Testing Gap
Demo mode and type mismatches weren't caught because integration tests didn't cover end-to-end flows with real API calls.

**Recommendation**: Add contract tests and integration tests against actual API responses.

### 2. API Contract Ownership
No single source of truth for API response format led to frontend/backend divergence.

**Recommendation**: Adopt OpenAPI with type generation and contract testing.

### 3. Feature Flag Discipline
Temporary code ("demo mode") became permanent because there was no tracking mechanism.

**Recommendation**: Use formal feature flags with environment detection and expiration tracking.

### 4. Domain Knowledge Gaps
UK registration formats and MOT data nuances weren't well understood by developers.

**Recommendation**: Document domain knowledge, include edge case test data, consult domain experts.

### 5. Error UX Patterns
Ad-hoc error handling led to inconsistent UX across the application.

**Recommendation**: Create component library with standardized error handling patterns.

---

## Action Items

| Priority | Item | Owner | Effort |
|----------|------|-------|--------|
| P0 | Add integration tests for vehicle lookup flow | Backend | 2 days |
| P0 | Generate TypeScript types from OpenAPI | Full-stack | 1 day |
| P1 | Implement feature flag for DVSA availability | Backend | 0.5 days |
| P1 | Create Toast/Notification component | Frontend | 1 day |
| P2 | Add optional mileage field | Frontend | 0.5 days |
| P2 | Document UK registration format history | Docs | 0.5 days |
| P3 | A/B test mileage field impact | Product | 2 weeks |

---

## Conclusion

The root causes of these UX issues stem from:
1. **Demo mode that became permanent** - temporary code without tracking
2. **Disconnected frontend/backend development** - no API contract
3. **Incomplete domain knowledge** - UK vehicle data is complex
4. **Happy-path focus** - error handling deprioritized
5. **UX trade-offs without data** - simplicity assumed better without validation

The fixes implemented address the immediate symptoms. The recommendations above will prevent similar issues in the future.
