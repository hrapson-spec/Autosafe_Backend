/**
 * Final report layout. The old results_page_v1 A/B split is retired: every
 * report now renders the same prediction-first shell, with current API
 * responses shown as an explicit comparison rather than a car prediction.
 */
import { test, expect } from './helpers/setup';
import { mockGetReport } from './helpers/mockApi';
import { fixtureExactHigh, fixtureVehiclePrediction } from '../fixtures/reportResponses';
import {
  buildScopeDisclosure,
  buildWhatsAppMessage,
  reportRateDisplay,
  sampleSizeBadge,
} from '../components/ReportCopy';

test('final report layout: honest comparison, actions, and collapsed methodology', async ({ page }) => {
  await mockGetReport(page, fixtureExactHigh.report_token as string, fixtureExactHigh, 200);
  await page.goto(`/app/report/${fixtureExactHigh.report_token}`);

  const result = page.getByTestId('comparison-result');
  await expect(result).toBeVisible();
  await expect(
    result.getByRole('heading', { name: 'This result isn’t a prediction for AB12 CDE' })
  ).toBeVisible();

  const risk = reportRateDisplay(fixtureExactHigh);
  await expect(result.getByText(risk.text, { exact: true })).toBeVisible();
  await expect(result.getByRole('progressbar', { name: 'Comparison failure rate' })).toHaveAttribute(
    'aria-valuenow',
    String(risk.value)
  );
  await expect(page.getByTestId('vehicle-prediction-result')).toHaveCount(0);

  await expect(page.getByRole('button', { name: 'Share on WhatsApp' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Copy report link' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Set an MOT reminder' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Find a local garage' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Open the 10-minute checklist' })).toHaveAttribute(
    'href',
    '/app/guides/mot-checklist'
  );

  await expect(page.getByRole('heading', { name: 'Evidence Quality' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Evidence Summary' })).toHaveCount(0);
  await expect(page.getByText('Population average')).toHaveCount(0);
  await expect(page.getByText(/Based on 148M\+ recorded DVSA MOT tests/i)).toHaveCount(0);
  await expect(result.getByText(/1,842 recorded MOT tests/i)).toHaveCount(0);
  await expect(result.getByText(/18%|9%|7%/)).toHaveCount(0);

  const methodology = page.getByText('How this result was calculated');
  await expect(page.getByText(buildScopeDisclosure(fixtureExactHigh), { exact: true })).toBeHidden();
  await expect(page.getByText(sampleSizeBadge(fixtureExactHigh), { exact: true })).toBeHidden();
  await methodology.click();
  await expect(page.getByText(buildScopeDisclosure(fixtureExactHigh), { exact: true })).toBeVisible();
  await expect(page.getByText(sampleSizeBadge(fixtureExactHigh), { exact: true })).toBeVisible();

  await page.screenshot({ path: 'e2e-artifacts/report-final-layout.png', fullPage: true });
});

test('final report layout: prediction language requires the explicit prediction fixture', async ({ page }) => {
  await mockGetReport(
    page,
    fixtureVehiclePrediction.report_token as string,
    fixtureVehiclePrediction,
    200
  );
  await page.goto(`/app/report/${fixtureVehiclePrediction.report_token}`);

  const result = page.getByTestId('vehicle-prediction-result');
  await expect(result).toBeVisible();
  await expect(result.getByText('AutoSafe prediction for AB12 CDE')).toBeVisible();
  await expect(
    result.getByRole('heading', { name: 'Your car’s predicted chance of failing its next MOT' })
  ).toBeVisible();
  await expect(result.getByRole('heading', { name: 'Check these first' })).toBeVisible();
  await expect(page.getByTestId('comparison-result')).toHaveCount(0);

  // Methodology: vehicle-history disclosure, never cohort sample sizes.
  const methodology = page.getByText('How this result was calculated');
  await methodology.click();
  await expect(
    page.getByText(buildScopeDisclosure(fixtureVehiclePrediction), { exact: true })
  ).toBeVisible();
  await expect(page.getByText('Vehicle-specific result', { exact: true })).toBeVisible();
  await expect(page.getByText(/\d[\d,]* tests\b/)).toHaveCount(0);

  await page.screenshot({ path: 'e2e-artifacts/report-prediction-layout.png', fullPage: true });
});

test('prediction report: WhatsApp share says AutoSafe predicts with a non-guarantee', async ({ page }) => {
  await mockGetReport(
    page,
    fixtureVehiclePrediction.report_token as string,
    fixtureVehiclePrediction,
    200
  );
  await page.goto(`/app/report/${fixtureVehiclePrediction.report_token}`);
  await expect(page.getByTestId('vehicle-prediction-result')).toBeVisible();

  await page.context().route('https://wa.me/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>wa.me stub</title>' });
  });

  const [popup] = await Promise.all([
    page.waitForEvent('popup'),
    page.getByRole('button', { name: 'Share on WhatsApp' }).click(),
  ]);
  await popup.waitForLoadState();
  const text = new URL(popup.url()).searchParams.get('text');
  expect(text).toBe(buildWhatsAppMessage(fixtureVehiclePrediction));
  expect(text).toMatch(/^AutoSafe predicts/);
  expect(text).toContain('not a guarantee');
  expect(text).not.toContain('Comparable');
  await popup.close();
});

test('prediction report: garage modal frames priorities as inspection items', async ({ page }) => {
  await mockGetReport(
    page,
    fixtureVehiclePrediction.report_token as string,
    fixtureVehiclePrediction,
    200
  );
  await page.goto(`/app/report/${fixtureVehiclePrediction.report_token}`);
  await page.getByRole('button', { name: 'Find a local garage' }).click();
  await expect(page.getByText('Inspection priorities, not diagnosed faults')).toBeVisible();
  await expect(page.getByText('Comparison patterns, not diagnosed faults')).toHaveCount(0);
});

test('prediction report renders without horizontal overflow on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockGetReport(
    page,
    fixtureVehiclePrediction.report_token as string,
    fixtureVehiclePrediction,
    200
  );
  await page.goto(`/app/report/${fixtureVehiclePrediction.report_token}`);
  await expect(page.getByTestId('vehicle-prediction-result')).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Your car’s predicted chance of failing its next MOT' })
  ).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(overflow).toBeLessThanOrEqual(0);
  await page.screenshot({ path: 'e2e-artifacts/report-prediction-mobile.png', fullPage: true });
});

test('comparison report renders without horizontal overflow on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockGetReport(page, fixtureExactHigh.report_token as string, fixtureExactHigh, 200);
  await page.goto(`/app/report/${fixtureExactHigh.report_token}`);
  await expect(page.getByTestId('comparison-result')).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(overflow).toBeLessThanOrEqual(0);
});
