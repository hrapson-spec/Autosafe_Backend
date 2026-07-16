/**
 * Full happy path on `/`: submit -> land on the persisted report route ->
 * every evidence-ladder fact the report claims is actually on the page ->
 * browser back returns to a working form.
 *
 * Forced to the 'control' variant (see e2e/helpers/experiments.ts): the
 * scope-disclosure sentence this spec asserts on (ReportCopy.buildScopeDisclosure)
 * is only rendered by ReportDashboard's control branch (components/ReportDashboard.tsx
 * calls it once, inside the control-only "MOT Prediction Card") -- the
 * suite's no-experiment-randomness invariant and this spec's own assertion
 * list both point at the same variant, so control is not an arbitrary
 * choice here.
 *
 * Expected copy is computed by calling the app's own ReportCopy functions
 * against the exact fixture being mocked, rather than hand-transcribed
 * literals -- this exercises "does the rendered page actually show what
 * ReportCopy produces for this API response" (the real integration seam)
 * without this spec silently drifting from ReportCopy's own wording
 * (already covered in isolation by ReportCopy.test.tsx).
 */
import { test, expect } from './helpers/setup';
import { mockCreateReport, mockGetReport } from './helpers/mockApi';
import { forceVariant } from './helpers/experiments';
import { registrationInput, postcodeInput } from './helpers/heroForm';
import { fixtureExactHigh } from '../fixtures/reportResponses';
import { buildNarrative, buildScopeDisclosure, sampleSizeBadge, mileageHeaderValue } from '../components/ReportCopy';

test('happy path: submit -> persisted report -> evidence facts present -> back to a working form', async ({ page }) => {
  await forceVariant(page, 'control');
  await mockCreateReport(page, fixtureExactHigh, 200);
  await mockGetReport(page, fixtureExactHigh.report_token as string, fixtureExactHigh, 200);

  await page.goto('/');
  await registrationInput(page).fill('AB12CDE');
  await postcodeInput(page).fill('SW1A 1AA');
  await page.getByRole('button', { name: /check this car/i }).click();

  await expect(page).toHaveURL(new RegExp(`/app/report/${fixtureExactHigh.report_token}$`));

  // Narrative paragraph is rendered from the same comparison-copy function
  // wherever the control layout repeats it.
  const narrative = buildNarrative(fixtureExactHigh);
  await expect(page.getByText(narrative, { exact: true }).first()).toBeVisible();

  // Scope disclosure (control-only evidence-quality card).
  await expect(page.getByText(buildScopeDisclosure(fixtureExactHigh), { exact: true })).toBeVisible();

  // Sample size (evidence-meta badge).
  await expect(page.getByTestId('evidence-meta')).toContainText(sampleSizeBadge(fixtureExactHigh));

  // Mileage line, in the header's vehicle-identity paragraph. Built to
  // match ReportDashboard.tsx's renderHeader template exactly
  // ("{year? }{make} {model}{mileage? ` • ${mileage}` : ''}") rather than a
  // bare substring search -- "62,411 miles" alone is also a substring of
  // the narrative sentence (asserted above, twice in the control layout),
  // so a plain getByText(mileage) is a strict-mode violation (3 matches).
  const mileage = mileageHeaderValue(fixtureExactHigh);
  expect(mileage).not.toBeNull();
  const yearPrefix = fixtureExactHigh.vehicle.year ? `${fixtureExactHigh.vehicle.year} ` : '';
  const headerLine = `${yearPrefix}${fixtureExactHigh.vehicle.make} ${fixtureExactHigh.vehicle.model} • ${mileage}`;
  await expect(page.getByText(headerLine, { exact: true })).toBeVisible();

  await page.screenshot({ path: 'e2e-artifacts/report-happy-path.png', fullPage: true });

  await page.goBack();

  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole('heading', { name: 'Fix it before they find it.' })).toBeVisible();
  await expect(registrationInput(page)).toBeEditable();
  await expect(page.getByRole('button', { name: /check this car/i })).toBeVisible();
});
