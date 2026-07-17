/**
 * Full happy path on `/`: submit -> land on the persisted report route ->
 * the final comparison result and its methodology are on the page ->
 * browser back returns to a working form.
 *
 * Methodology is intentionally collapsed in the final layout, so the test
 * expands it before checking its scope and sample-size copy. Expected copy
 * is computed by calling the app's own ReportCopy functions
 * against the exact fixture being mocked, rather than hand-transcribed
 * literals -- this exercises "does the rendered page actually show what
 * ReportCopy produces for this API response" (the real integration seam)
 * without this spec silently drifting from ReportCopy's own wording
 * (already covered in isolation by ReportCopy.test.tsx).
 */
import { test, expect } from './helpers/setup';
import { mockCreateReport, mockGetReport } from './helpers/mockApi';
import { registrationInput, postcodeInput } from './helpers/heroForm';
import { fixtureExactHigh } from '../fixtures/reportResponses';
import { buildScopeDisclosure, sampleSizeBadge, mileageHeaderValue } from '../components/ReportCopy';

test('happy path: submit -> final persisted report -> methodology -> back to a working form', async ({ page }) => {
  await mockCreateReport(page, fixtureExactHigh, 200);
  await mockGetReport(page, fixtureExactHigh.report_token as string, fixtureExactHigh, 200);

  await page.goto('/');
  await registrationInput(page).fill('AB12CDE');
  await postcodeInput(page).fill('SW1A 1AA');
  await page.getByRole('button', { name: /check this car/i }).click();

  await expect(page).toHaveURL(new RegExp(`/app/report/${fixtureExactHigh.report_token}$`));

  await expect(page.getByTestId('comparison-result')).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'This result isn’t a prediction for AB12 CDE' })
  ).toBeVisible();

  await expect(page.getByText(buildScopeDisclosure(fixtureExactHigh), { exact: true })).toBeHidden();
  await expect(page.getByText(sampleSizeBadge(fixtureExactHigh), { exact: true })).toBeHidden();
  await page.getByText('How this result was calculated').click();
  await expect(page.getByText(buildScopeDisclosure(fixtureExactHigh), { exact: true })).toBeVisible();
  await expect(page.getByText(sampleSizeBadge(fixtureExactHigh), { exact: true })).toBeVisible();

  // Mileage line, in the header's vehicle-identity paragraph. Built to
  // match ReportDashboard.tsx's renderHeader template exactly
  // ("{year? }{make} {model}{mileage? ` • ${mileage}` : ''}") rather than a
  // bare substring search, so the identity and mileage provenance remain
  // tied to the same header line.
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
