# AutoSafe Bug Report

**Date:** 2026-01-30
**Reviewed by:** Claude Code Review
**Branch:** claude/review-autosafe-bugs-aBzOA

---

## Summary

| Severity | Count | Description |
|----------|-------|-------------|
| P0 Critical | 2 | Core functionality broken |
| P1 High | 2 | Startup failures, data type mismatches |
| P2 Medium | 4 | Memory leaks, incomplete functions, format inconsistencies |
| P3 Low | 3 | Validation issues, minor bugs |

---

## P0 - Critical Bugs

### 1. App.tsx:29 - User Registration Input Completely Ignored

**File:** `App.tsx`
**Line:** 29

```typescript
const result = await getReportBySelection('FORD', 'FIESTA', 2018);
```

**Issue:** The `handleCarCheck` function ignores the `data.registration` parameter entirely. Users enter their vehicle registration, but the app always returns a Ford Fiesta 2018 report regardless of input.

**Impact:** Core functionality is broken - users cannot check their actual vehicle.

**Fix:** Use the actual registration data:
```typescript
// Should call getReportByRegistration(data.registration) instead
// Or parse vehicle info from DVLA/DVSA and use actual make/model/year
```

---

### 2. feature_engineering_v55.py - Missing Features in FEATURE_NAMES

**File:** `feature_engineering_v55.py`
**Lines:** 415-428 (computation) vs 32-70 (FEATURE_NAMES list)

**Issue:** The code computes `neglect_score_brakes`, `neglect_score_tyres`, and `neglect_score_suspension` features (lines 415-428), but these features are NOT included in the `FEATURE_NAMES` list.

The docstring explicitly states:
```
V55+neglect: Added 3 neglect_score features (brakes, tyres, suspension)
from V33 with optimized weights for +2.21pp AUC lift.
```

This suggests these features should be used, but they're not in the list.

**Impact:** Features are computed but never passed to the model, potentially reducing prediction accuracy by 2.21pp AUC.

**Fix:** Add to `FEATURE_NAMES`:
```python
'neglect_score_brakes', 'neglect_score_tyres', 'neglect_score_suspension',
```

---

## P1 - High Priority Bugs

### 3. main.py:87-95 - Variable Used Before Definition

**File:** `main.py`
**Lines:** 87-95 (usage) vs 200-201 (definition)

```python
# Line 87-95 in lifespan() function:
if os.path.exists(DB_FILE):  # DB_FILE defined at line 200!
    ...
elif DATABASE_URL:           # DATABASE_URL defined at line 201!
```

**Issue:** `DATABASE_URL` and `DB_FILE` are referenced in the `lifespan()` function but defined 100+ lines later in the module.

**Impact:** Python modules execute top-to-bottom, so globals defined later are available. However, this is fragile code that could break if imports or reordering occurs.

**Fix:** Move definitions before the `lifespan()` function or use explicit imports.

---

### 4. autosafeApi.ts - Response Type Mismatch with Backend

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

### 5. HeroForm.tsx:11 - Registration Pattern Too Restrictive

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

### 6. postcode_service.py:15 - Unbounded Cache Growth

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

### 7. lead_distributor.py:230-239 - Incomplete Function

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

### 8. repair_costs.py vs main.py - Inconsistent Response Formats

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

### 9. dvla_client.py:109 - Registration Length Validation

**File:** `dvla_client.py`
**Line:** 109

```python
if len(normalized) < 2 or len(normalized) > 7:  # Should be > 8
```

**Issue:** Standard UK plates can be up to 8 characters without spaces. This rejects valid 8-character registrations.

---

### 10. email_service.py:121 - Only Checks Status 200

**File:** `email_service.py`
**Line:** 121

```python
if response.status_code == 200:
```

**Issue:** Only checks for 200, but REST APIs commonly return 201 (Created) or 202 (Accepted) for successful POST requests.

---

### 11. utils.py vs feature_engineering_v55.py - Duplicate Functions with Different Logic

**Files:** `utils.py` and `feature_engineering_v55.py`

Both files define `get_age_band()` with different age band ranges:
- `utils.py`: Returns '0-3', '3-5', '6-10', '**10-15**', '15+'
- `feature_engineering_v55.py`: Returns '0-3', '3-5', '6-10', '**11-15**', '15+'

**Issue:** Inconsistent age band boundaries (10-15 vs 11-15) could cause data mismatches.

---

## Recommendations

### Immediate Actions (P0)
1. Fix App.tsx to use actual user registration input
2. Add neglect_score features to FEATURE_NAMES or remove dead code

### Short-term Actions (P1)
3. Reorder variable definitions in main.py
4. Update autosafeApi.ts interface to match backend response

### Medium-term Actions (P2)
5. Expand registration pattern in HeroForm.tsx
6. Add bounded cache for postcode lookups
7. Implement or remove retry_failed_distributions()
8. Standardize repair cost response format

### Housekeeping (P3)
9-11. Fix validation, status codes, and consolidate duplicate functions
