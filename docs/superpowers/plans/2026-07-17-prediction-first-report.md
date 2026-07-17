# Prediction-First Vehicle Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the approved AutoSafe result design with an explicit, contract-enforced distinction between a genuine vehicle prediction and today’s comparison fallback.

**Architecture:** Add a backwards-compatible `result_kind` discriminator to the Python and TypeScript v2 report contracts, defaulting all existing reports to `comparison` and permitting `vehicle_prediction` only with `prediction_source: model_v55`. Create one focused result component for the two visual states, then simplify `ReportDashboard` into a single final layout while preserving share, reminder, email, garage and analytics behaviour.

**Tech Stack:** Python 3, Pydantic v2, FastAPI, React 19, TypeScript, Tailwind CSS, Vitest, Testing Library, Playwright, Vite, Railway.

## Global Constraints

- Existing and persisted v2 reports must default to `result_kind: comparison`.
- `vehicle_prediction` is valid only with `prediction_source: model_v55`.
- No current report-service path may emit `vehicle_prediction`.
- Current comparison copy must say the result is not a prediction for the submitted registration.
- Vehicle-prediction copy must render only inside the `vehicle_prediction` branch.
- Do not relabel cohort component rates as priorities for this car.
- Remove the standalone Evidence Quality card and methodology-led headline treatment.
- Preserve share, reminder, email, garage, reset and analytics behaviour.
- Retire the `results_page_v1` layout experiment rather than serving two designs.
- Keep contract version `2.0`; the change is additive and backwards-compatible.
- Base and release from `c5f83fdf58ad81344ecfaa83a0b1e857ad17ea1f`.

---

### Task 1: Add the result-semantic contract

**Files:**
- Modify: `tests/test_report_contract.py`
- Modify: `tests/test_report_routes.py`
- Modify: `services/reportValidation.test.ts`
- Modify: `report_contract.py`
- Modify: `types.ts`
- Modify: `services/reportValidation.ts`
- Modify: `fixtures/reportResponses.ts`
- Modify: `openapi.json`

**Interfaces:**
- Produces: Python `ResultKind` enum with `COMPARISON` and `VEHICLE_PREDICTION`.
- Produces: TypeScript `ResultKind = 'comparison' | 'vehicle_prediction'`.
- Produces: `ReportResponse.result_kind` and `ReportV2.result_kind`.
- Extends: `PredictionSource` with `MODEL_V55` / `'model_v55'`.

- [ ] **Step 1: Write failing backend contract tests**

Add tests that prove an omitted field defaults to comparison, valid model output is accepted, and invalid source pairings are rejected:

```python
def test_result_kind_defaults_to_comparison(self, valid_report_dict):
    valid_report_dict.pop("result_kind", None)
    report = ReportResponse.model_validate(valid_report_dict)
    assert report.result_kind == ResultKind.COMPARISON

def test_vehicle_prediction_requires_model_source(self, valid_report_dict):
    valid_report_dict["result_kind"] = "vehicle_prediction"
    with pytest.raises(ValidationError, match="vehicle prediction requires model_v55"):
        ReportResponse.model_validate(valid_report_dict)

def test_vehicle_prediction_accepts_model_v55(self, valid_report_dict):
    valid_report_dict.update(result_kind="vehicle_prediction", prediction_source="model_v55")
    report = ReportResponse.model_validate(valid_report_dict)
    assert report.result_kind == ResultKind.VEHICLE_PREDICTION
```

- [ ] **Step 2: Verify backend RED**

Run:

```bash
pytest -q tests/test_report_contract.py -k "result_kind or prediction_source"
```

Expected: FAIL because `ResultKind`, `result_kind`, and `model_v55` do not exist.

- [ ] **Step 3: Write failing frontend validation tests**

Add these expectations:

```ts
expect(isReportV2({ ...fixtureExactHigh, result_kind: 'comparison' })).toBe(true);
expect(isReportV2({
  ...fixtureExactHigh,
  result_kind: 'vehicle_prediction',
  prediction_source: 'model_v55',
})).toBe(true);
expect(isReportV2({
  ...fixtureExactHigh,
  result_kind: 'vehicle_prediction',
  prediction_source: 'postgres',
})).toBe(false);
```

- [ ] **Step 4: Verify frontend RED**

Run:

```bash
npm test -- services/reportValidation.test.ts
```

Expected: FAIL because the validator rejects the new enum values and has no pairing rule.

- [ ] **Step 5: Implement the minimal contract**

In `report_contract.py` add:

```python
class ResultKind(str, Enum):
    COMPARISON = "comparison"
    VEHICLE_PREDICTION = "vehicle_prediction"

class PredictionSource(str, Enum):
    POSTGRES = "postgres"
    SQLITE = "sqlite"
    DATASET_REFERENCE = "dataset_reference"
    MODEL_V55 = "model_v55"
    UNAVAILABLE = "unavailable"
```

Add to `ReportResponse`:

```python
result_kind: ResultKind = ResultKind.COMPARISON
```

Add to `validate_report_consistency`:

```python
if self.result_kind == ResultKind.VEHICLE_PREDICTION:
    if self.prediction_source != PredictionSource.MODEL_V55:
        raise ValueError("vehicle prediction requires model_v55 prediction source")
elif self.prediction_source == PredictionSource.MODEL_V55:
    raise ValueError("model_v55 prediction source requires vehicle_prediction result kind")
```

Mirror the enums, required field and cross-field validation in `types.ts` and `services/reportValidation.ts`. Add `result_kind: 'comparison'` to every checked-in report fixture and export a `fixtureVehiclePrediction` clone with `result_kind: 'vehicle_prediction'` and `prediction_source: 'model_v55'`.

- [ ] **Step 6: Verify GREEN and route defaults**

Run:

```bash
pytest -q tests/test_report_contract.py tests/test_report_routes.py
npm test -- services/reportValidation.test.ts
```

Expected: all selected tests pass and current route responses contain `result_kind: comparison`.

- [ ] **Step 7: Commit the contract**

```bash
git add report_contract.py types.ts services/reportValidation.ts services/reportValidation.test.ts fixtures/reportResponses.ts tests/test_report_contract.py tests/test_report_routes.py
git commit -m "feat: distinguish predictions from comparisons"
```

---

### Task 2: Build the two-state result component

**Files:**
- Create: `components/ReportResult.tsx`
- Create: `components/ReportResult.test.tsx`
- Modify: `components/Icons.tsx`
- Reference: `components/ui/Card.tsx`
- Reference: `components/ui/Button.tsx`

**Interfaces:**
- Consumes: `report: ReportV2`, `onReminder: () => void`, `onGarage: () => void`.
- Produces: a responsive result section with `data-testid="vehicle-prediction-result"` or `data-testid="comparison-result"`.

- [ ] **Step 1: Write failing component tests**

Cover the two result branches:

```tsx
it('renders a vehicle prediction only for the explicit vehicle_prediction state', () => {
  render(<ReportResult report={fixtureVehiclePrediction} onReminder={vi.fn()} onGarage={vi.fn()} />);
  expect(screen.getByTestId('vehicle-prediction-result')).toBeInTheDocument();
  expect(screen.getByText('AutoSafe prediction for AB12 CDE')).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Your car’s predicted chance of failing its next MOT' })).toBeInTheDocument();
  expect(screen.getByText('12%')).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Check these first' })).toBeInTheDocument();
});

it('renders the honest comparison fallback for current reports', () => {
  render(<ReportResult report={fixtureModelAverageLow} onReminder={vi.fn()} onGarage={vi.fn()} />);
  expect(screen.getByTestId('comparison-result')).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'This result isn’t a prediction for EF56 HIJ' })).toBeInTheDocument();
  expect(screen.getByText(/TESTMAKE RAREMODEL comparison: about 1 in 3 failed their MOT/i)).toBeInTheDocument();
  expect(screen.queryByText(/AutoSafe prediction for/i)).not.toBeInTheDocument();
});
```

Also test that the reminder button calls `onReminder`, the garage button calls `onGarage`, the checklist link points to `/app/guides/mot-checklist`, and prediction priorities are sorted by component value while comparison checks use the fixed universal list.

- [ ] **Step 2: Verify component RED**

Run:

```bash
npm test -- components/ReportResult.test.tsx
```

Expected: FAIL because `ReportResult.tsx` does not exist.

- [ ] **Step 3: Implement `ReportResult`**

Use one branch discriminator:

```tsx
const isVehiclePrediction = report.result_kind === 'vehicle_prediction';
```

Render AutoSafe’s production card system:

```tsx
<section data-testid={isVehiclePrediction ? 'vehicle-prediction-result' : 'comparison-result'} className="space-y-5">
  <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.45fr)_minmax(17rem,.75fr)] gap-5">
    <Card padding="lg">...</Card>
    <Card>...</Card>
  </div>
  <Card variant="dark" className="grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_auto] gap-5 items-center">...</Card>
</section>
```

Use `reportRateDisplay(report)` for the single percentage. Derive a finite plain-language frequency for every contract-valid risk value (including `0`), use the existing risk colour thresholds, and use a proper `role="progressbar"`. Validate MOT dates by calendar round-trip so malformed and impossible values fall back to generic preparation copy. Do not display sample counts, evidence confidence, repair estimates, or component percentages in this primary section.

- [ ] **Step 4: Verify component GREEN**

Run:

```bash
npm test -- components/ReportResult.test.tsx
```

Expected: all result-state and action tests pass.

- [ ] **Step 5: Run accessibility and type checks for the new component**

```bash
npm run typecheck
npm run lint
```

Expected: exit code 0.

- [ ] **Step 6: Commit the result component**

```bash
git add components/ReportResult.tsx components/ReportResult.test.tsx components/Icons.tsx
git commit -m "feat: add prediction-first report result"
```

---

### Task 3: Integrate the final report layout and retire the A/B split

**Files:**
- Modify: `components/ReportDashboard.tsx`
- Modify: `components/ReportDashboard.test.tsx`
- Modify: `utils/experiments.ts`
- Modify: `e2e/ab-variants.spec.ts`
- Modify: `e2e/helpers/experiments.ts`
- Modify: `e2e/report-and-reset.spec.ts`
- Modify: `e2e/share-flow.spec.ts`
- Modify: `e2e/mileage-truth.spec.ts`
- Modify: `e2e/form-lifecycle.spec.ts`
- Modify: `e2e/README.md`

**Interfaces:**
- Consumes: `ReportResult` callbacks.
- Preserves: `ReportDashboardProps`, report-share actions, reminder/email capture, garage modal, sticky CTA and conversion analytics.

- [ ] **Step 1: Replace the dashboard expectations first**

Update `ReportDashboard.test.tsx` so current comparison fixtures require the new fallback, reject `Evidence Quality`, `Evidence Summary`, `Population average`, and visible sample-count copy, and continue to exercise WhatsApp/copy-link, reset, email and modal actions. Add one render using `fixtureVehiclePrediction` to prove the prediction branch reaches the dashboard.

- [ ] **Step 2: Verify dashboard RED**

```bash
npm test -- components/ReportDashboard.test.tsx
```

Expected: FAIL because the old control/treatment layouts still render methodology-led cards and no new result component.

- [ ] **Step 3: Simplify `ReportDashboard`**

Remove the control/treatment render branches, chart, motivator card, visible evidence summary, visible cohort components and repair context. Keep one layout:

```tsx
return (
  <div className="w-full max-w-5xl mx-auto p-4 md:p-8 space-y-8 animate-fade-in">
    {renderHeader()}
    <ReportResult report={report} onReminder={focusReminder} onGarage={openGarage} />
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4" ref={motReminderRef}>...</div>
    <details>...</details>
    <GarageFinderModal ... />
    <StickyCta ... />
  </div>
);
```

Keep only the share controls in the header’s secondary row; remove `Based on 148M+ recorded DVSA MOT tests`. Put `buildScopeDisclosure(report)` and `sampleSizeBadge(report)` inside the collapsed `How this result was calculated` disclosure.

Point the sticky-CTA visibility ref and reminder-focus ref at the reminder/email block. Let `StickyCta` own its click event so it is not double-counted. Preserve garage-success confirmation in the final layout. Remove branch-only analytics that belonged to the retired recommendation, motivator, and secondary CTA treatments.

- [ ] **Step 4: Retire `results_page_v1`**

Change `EXPERIMENTS` in `utils/experiments.ts` to an empty record so old stored assignments are filtered out of `getAllVariants()`. Remove `getVariant` usage from `ReportDashboard`. Rewrite the A/B Playwright spec as a single final-layout report test and remove experiment forcing that no longer represents a real product variant.

Remove the same stale experiment forcing and retired visible-evidence assertions from `report-and-reset`, `share-flow`, `mileage-truth`, and `form-lifecycle`; update the E2E README. Expand the new methodology disclosure before checking hidden scope/sample copy. Seed a stale `results_page_v1` assignment in a dashboard unit test and prove it is omitted from submitted lead metadata.

- [ ] **Step 5: Verify dashboard GREEN**

```bash
npm test -- components/ReportDashboard.test.tsx components/ReportResult.test.tsx components/ReportScreen.test.tsx
```

Expected: all selected tests pass.

- [ ] **Step 6: Run frontend regression checks**

```bash
npm test
npm run typecheck
npm run lint
npm run build
python3 scripts/claim_sweep.py
```

Expected: all  frontend tests pass, typecheck/lint/build succeed, and claim sweep reports no unsupported capability claims.

- [ ] **Step 7: Commit the integration**

```bash
git add components/ReportDashboard.tsx components/ReportDashboard.test.tsx utils/experiments.ts e2e/ab-variants.spec.ts e2e/helpers/experiments.ts e2e/report-and-reset.spec.ts e2e/share-flow.spec.ts e2e/mileage-truth.spec.ts e2e/form-lifecycle.spec.ts e2e/README.md
git commit -m "feat: ship final AutoSafe report layout"
```

---

### Task 4: Verify the release candidate visually and end-to-end

**Files:**
- Modify only if verification finds a defect in an already-owned file.

**Interfaces:**
- Verifies: browser → report API → React renderer → actions → responsive layout.

- [ ] **Step 1: Run backend contract and route verification**

```bash
pytest -q tests/test_report_contract.py tests/test_report_routes.py tests/test_report_service_banding.py
python3 scripts/check_openapi_drift.py
```

Expected: all selected tests pass and the committed OpenAPI snapshot matches the additive contract.

- [ ] **Step 2: Run Playwright**

```bash
npm run test:e2e
```

Expected: every end-to-end test passes, including report/reset, token states and the new final-layout test.

- [ ] **Step 3: Inspect desktop and mobile rendering**

Start the local app, load a current `model_average` fixture/report at desktop and 390px widths, and verify:

- no horizontal overflow;
- no Evidence Quality card;
- comparison fallback is the visible state;
- checklist, reminder, share and garage controls work;
- keyboard focus remains visible;
- no console errors.

- [ ] **Step 4: Run the complete release gate**

```bash
npm test
npm run typecheck
npm run lint
npm run build
pytest -q tests/
python3 scripts/claim_sweep.py
python3 scripts/check_openapi_drift.py
git diff --check
git status --short
```

Expected: every command exits 0 and status contains only the intentional documentation/source/test changes before the final commit.

- [ ] **Step 5: Review and commit any verification-only adjustments**

Stage only explicit changed paths and commit with:

```bash
git commit -m "test: verify prediction-first report release"
```

Skip this commit when verification required no source adjustment.

---

### Task 5: Publish and verify production

**Files:**
- No source edits unless a production-only defect is reproduced test-first.

**Interfaces:**
- Produces: a pull request to `main`, merged production SHA, two successful Railway deployments, and public verification evidence.

- [ ] **Step 1: Push the exact branch and open a pull request**

```bash
git push -u origin feat/prediction-first-report
```

Open a PR titled `Ship prediction-first AutoSafe report` and include the two-state truth contract, test evidence, screenshots, and the explicit note that current production reports render the comparison fallback.

- [ ] **Step 2: Require all seven CI jobs on the exact head SHA**

Verify success for `test`, `contract-drift`, `security-check`, `frontend-build`, `e2e`, `staging-evidence`, and `build-check`. Do not merge a different SHA than the one reviewed.

- [ ] **Step 3: Merge under the user’s explicit production approval**

Merge the PR to `main`. Record the merge SHA. This immediately authorises Railway’s two automatic production deployments because the user requested the change live.

- [ ] **Step 4: Wait for both Railway deployments**

Require success for the canonical `www.autosafe.one` service and the apex `autosafe.one` service. Verify `/health` and `/ready` return 200.

- [ ] **Step 5: Verify the public product directly**

Check:

```text
https://www.autosafe.one/api/version
https://www.autosafe.one/app/report/OC_1Jaa7ATdxJibHgJKu4g
https://www.autosafe.one/api/v2/reports/OC_1Jaa7ATdxJibHgJKu4g
```

The live JSON must include `result_kind: comparison`; the rendered report must show the new comparison fallback and omit the old Evidence Quality card; share/reset/checklist/reminder controls must work. Report the backend/frontend SHA fields separately if the existing identity mismatch persists.
