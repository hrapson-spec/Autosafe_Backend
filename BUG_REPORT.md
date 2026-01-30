# AutoSafe Bug Report

**Date:** 2026-01-30
**Reviewed by:** Claude Code Review
**Branch:** claude/review-autosafe-bugs-aBzOA

---

## Summary

| Severity | Count | Description |
|----------|-------|-------------|
| P1 High | 1 | API type mismatches |
| P2 Medium | 4 | Memory leaks, incomplete functions, format inconsistencies |
| P3 Low | 3 | Validation issues, minor bugs |

---

## P1 - High Priority Bugs

### 1. autosafeApi.ts - Response Type Mismatch with Backend

**File:** `services/autosafeApi.ts`
**Lines:** 29-54

```typescript
interface BackendRiskResponse {
  Failure_Risk: number;        // API returns: failure_risk
  Risk_Brakes?: number;        // API returns: risk_brakes (in risk_components)
  Total_Tests: number;         // API returns: different structure
}
```

**Issue:** The TypeScript interface expects `Title_Case` keys but the V55 API endpoint returns `snake_case` keys with a different structure:
```json
{
  "failure_risk": 0.25,
  "risk_components": {
    "brakes": 0.05,
    "suspension": 0.04
  }
}
```

**Impact:** Frontend will show undefined values for risk components.

**Fix:** Update interface to match actual API response:
```typescript
interface BackendRiskResponse {
  failure_risk: number;
  risk_components: {
    brakes: number;
    suspension: number;
    // ...
  };
}
```

---

## P2 - Medium Priority Bugs

### 2. HeroForm.tsx:11 - Registration Pattern Too Restrictive

**File:** `components/HeroForm.tsx`
**Line:** 11

```typescript
const UK_REG_PATTERN = /^[A-Z]{2}[0-9]{2}\s?[A-Z]{3}$/i;
```

**Issue:** Only matches current-style UK plates (AA00 AAA). Rejects valid older formats:
- Prefix format: A123 BCD
- Suffix format: ABC 123D
- Dateless: 1234 AB, AB 1234

**Impact:** Users with older or personalized plates cannot use the form.

**Fix:** Use more permissive pattern matching the backend's validation:
```typescript
const UK_REG_PATTERN = /^[A-Z0-9]{2,8}$/i;  // Allow 2-8 alphanumeric chars
```

---

### 3. postcode_service.py:15 - Unbounded Cache Growth

**File:** `postcode_service.py`
**Line:** 15

```python
_postcode_cache: Dict[str, Tuple[float, float]] = {}  # No size limit
```

**Issue:** The postcode cache is a plain dict with no max size limit or TTL. In high-traffic production, this could grow indefinitely.

**Impact:** Memory leak in long-running production deployments.

**Fix:** Use `cachetools.TTLCache` like other caches in the codebase:
```python
from cachetools import TTLCache
_postcode_cache: TTLCache = TTLCache(maxsize=10000, ttl=86400)  # 1 day TTL
```

---

### 4. lead_distributor.py:230-239 - Incomplete Function

**File:** `lead_distributor.py`
**Lines:** 230-239

```python
async def retry_failed_distributions() -> dict:
    """Retry distributing leads that failed previously."""
    pass  # Function body is empty!
```

**Issue:** Function is declared with a docstring but has no implementation (just `pass`).

**Impact:** Returns `None` instead of expected dict. Any caller will fail with AttributeError or TypeError.

**Fix:** Either implement the function or remove it and mark as TODO.

---

### 5. repair_costs.py vs main.py - Inconsistent Response Formats

**Files:** `repair_costs.py` and `main.py`

```python
# repair_costs.py calculate_expected_repair_cost() returns:
{"cost_min": 150, "cost_mid": 250, "cost_max": 400, "display": "..."}

# main.py _estimate_repair_cost() returns:
{"expected": 250, "range_low": 150, "range_high": 400}
```

**Issue:** Two different response formats for repair cost estimates.

**Impact:** Frontend may receive different formats depending on code path.

**Fix:** Standardize on one format across both functions.

---

## P3 - Lower Priority Issues

### 6. dvla_client.py:109 - Registration Length Validation

**File:** `dvla_client.py`
**Line:** 109

```python
if len(normalized) < 2 or len(normalized) > 7:  # Should be > 8
```

**Issue:** Standard UK plates can be up to 8 characters without spaces. This rejects valid 8-character registrations.

---

### 7. email_service.py:121 - Only Checks Status 200

**File:** `email_service.py`
**Line:** 121

```python
if response.status_code == 200:
```

**Issue:** Only checks for 200, but REST APIs commonly return 201 (Created) or 202 (Accepted) for successful POST requests.

---

### 8. utils.py vs feature_engineering_v55.py - Duplicate Functions with Different Logic

**Files:** `utils.py` and `feature_engineering_v55.py`

Both files define `get_age_band()` with different age band ranges:
- `utils.py`: Returns '0-3', '3-5', '6-10', '**10-15**', '15+'
- `feature_engineering_v55.py`: Returns '0-3', '3-5', '6-10', '**11-15**', '15+'

**Issue:** Inconsistent age band boundaries (10-15 vs 11-15) could cause data mismatches.

---

## Recommendations

### Short-term Actions (P1)
1. Update autosafeApi.ts interface to match backend response

### Medium-term Actions (P2)
2. Expand registration pattern in HeroForm.tsx
3. Add bounded cache for postcode lookups
4. Implement or remove retry_failed_distributions()
5. Standardize repair cost response format

### Housekeeping (P3)
6-8. Fix validation, status codes, and consolidate duplicate functions
