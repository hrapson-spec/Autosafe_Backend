# AutoSafe User Experience Issues Report

**Date**: 2026-01-22
**Severity Levels**: Critical (breaks core functionality), High (major UX degradation), Medium (noticeable issues), Low (minor improvements)

---

## CRITICAL ISSUES

### 1. DEMO MODE - All Registrations Return Ford Fiesta
**File**: `App.tsx:27-29`
**Severity**: CRITICAL

**Problem**: The `handleCarCheck` function is hardcoded to always return Ford Fiesta 2018 data, regardless of what registration the user enters.

```tsx
// DEMO MODE: Use backend demo data until DVLA API is configured
// This simulates a Ford Fiesta lookup for any registration
const result = await getReportBySelection('FORD', 'FIESTA', 2018);
```

**Impact**: Users believe they are checking their actual vehicle, but receive completely wrong data. This fundamentally breaks the core value proposition of the application.

**Fix Required**: Use `getReportByRegistration(registration)` to actually look up the vehicle.

---

### 2. API Response Type Mismatch
**Files**: `main.py`, `autosafeApi.ts`
**Severity**: CRITICAL

**Problem**: Backend returns lowercase field names (`failure_risk`, `risk_brakes`), but frontend TypeScript types expect PascalCase (`Failure_Risk`, `Risk_Brakes`).

Backend returns:
```json
{
  "failure_risk": 0.28,
  "risk_brakes": 0.05
}
```

Frontend expects:
```typescript
{
  Failure_Risk: number;
  Risk_Brakes?: number;
}
```

**Impact**: `data.Failure_Risk` returns `undefined`, causing `NaN` values in calculations and broken displays.

**Fix Required**: Either update backend to return PascalCase, or update frontend types to match lowercase.

---

### 3. Mileage Always Hardcoded to 50,000 miles
**Files**: `autosafeApi.ts:114,299,307,333`, `HeroForm.tsx`
**Severity**: HIGH

**Problem**: Mileage is a critical factor in MOT risk prediction, yet users cannot input their actual mileage. It's always hardcoded to 50,000 miles.

**Impact**: Predictions are inaccurate for vehicles with significantly different mileage. A 150,000-mile car will get the same risk as a 20,000-mile car.

**Fix Required**: Add mileage input field to HeroForm and pass it through the API chain.

---

## HIGH PRIORITY ISSUES

### 4. Registration Pattern Too Restrictive
**File**: `HeroForm.tsx:11`
**Severity**: HIGH

**Problem**: The regex `^[A-Z]{2}[0-9]{2}\s?[A-Z]{3}$/i` only accepts post-2001 format (AB12 CDE).

**Rejected valid formats**:
- Older suffix: `ABC 123D`
- Older prefix: `A123 ABC`
- Northern Ireland: `ABC 1234`
- Personalized plates with different structures

**Impact**: Many users with valid UK registrations cannot use the service.

**Fix Required**: Use a more permissive regex or validate on the backend.

---

### 5. No Error Dismissal or Auto-clear
**File**: `App.tsx:145-153`
**Severity**: MEDIUM

**Problem**: Error notifications have no close button and don't auto-dismiss.

```tsx
{error && (
  <div className="fixed bottom-8 ...">
    <AlertCircle ... />
    {error}
  </div>
)}
```

**Impact**: Errors persist on screen, potentially confusing users on subsequent successful actions.

**Fix Required**: Add dismiss button and/or auto-dismiss after 5-10 seconds.

---

### 6. Generic "Loading..." Button Text
**File**: `Button.tsx:54-57`
**Severity**: LOW

**Problem**: Submit button shows generic "Loading..." during API calls.

**Impact**: Users don't know what's happening (checking vehicle? calculating risk? connecting to DVSA?).

**Fix Required**: Allow customizable loading text via prop.

---

### 7. No Data Source Indication
**Severity**: MEDIUM

**Problem**: Users don't know if they're receiving:
- Real DVSA/DVLA data
- Fallback lookup table data
- Demo/mock data
- Population averages

**Impact**: Users may trust inaccurate fallback data as if it were real vehicle-specific data.

**Fix Required**: Show data source badge (e.g., "Based on DVSA MOT history" vs "Based on similar vehicles").

---

### 8. Rate Limiting UX
**File**: `main.py` (various endpoints)
**Severity**: MEDIUM

**Problem**: When rate limited, users get generic 429 errors with no guidance on when they can retry.

**Impact**: Power users or those refreshing may hit limits without understanding why.

**Fix Required**: Include `Retry-After` header and user-friendly message.

---

### 9. No Offline/Network Error Handling
**File**: `autosafeApi.ts`
**Severity**: MEDIUM

**Problem**: No detection of offline state or network failures. API calls just fail with cryptic errors.

**Impact**: Poor experience on mobile networks or during connectivity issues.

**Fix Required**: Add network detection and meaningful error messages.

---

### 10. Garage Finder Modal Loading State
**File**: `GarageFinderModal.tsx`
**Severity**: LOW

**Problem**: During form submission, button shows "Loading..." but no indication of what's happening.

**Impact**: Users may think it's frozen on slow networks.

**Fix Required**: Show "Finding garages..." or similar contextual message.

---

## ACCESSIBILITY ISSUES

### 11. Good: Skip Link Present
**File**: `index.html:70`
The skip link for keyboard navigation is correctly implemented.

### 12. Good: ARIA Labels
Many components have proper `aria-label`, `aria-describedby`, and `role` attributes.

### 13. Issue: Focus Management in Modal
**File**: `GarageFinderModal.tsx`
Focus is set to email input, but focus trap is not implemented. Users can tab outside the modal.

---

## PERFORMANCE ISSUES

### 14. Tailwind CSS CDN in Production
**File**: `index.html:30`
```html
<script src="https://cdn.tailwindcss.com"></script>
```

**Impact**: CDN Tailwind processes styles at runtime, adding latency. Should use compiled CSS in production.

### 15. Import Maps for React
**File**: `index.html:56-67`
Using ESM imports from esm.sh adds network dependency. Should bundle for production.

---

## SECURITY NOTES

### 16. Good: Security Headers
CSP, HSTS, X-Frame-Options all properly configured.

### 17. Good: Rate Limiting
Appropriate rate limits on sensitive endpoints.

### 18. Good: Admin API Key Verification
Uses constant-time comparison to prevent timing attacks.

---

## RECOMMENDED PRIORITY ORDER

1. **CRITICAL**: Fix demo mode - use actual vehicle lookup
2. **CRITICAL**: Fix API response type mismatch
3. **HIGH**: Add mileage input field
4. **HIGH**: Fix registration regex to accept more formats
5. **MEDIUM**: Add error dismissal
6. **MEDIUM**: Show data source indicators
7. **MEDIUM**: Improve rate limit UX
8. **LOW**: Contextual loading messages
9. **LOW**: Focus trap in modal
