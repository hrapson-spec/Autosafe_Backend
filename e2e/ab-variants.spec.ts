/**
 * results_page_v1 A/B variants (control vs treatment), each rendered
 * against two fixtures: the "everything worked" exact-match evidence
 * ladder cell, and the fully-degraded-evidence population_default cell
 * (report_service.py's furthest fallback rung that still has a real vehicle
 * identity). Both variants must tell a consistent story on both fixtures.
 *
 * The headline percentage is one disclosed historical failure rate in both
 * layouts. The suite checks the dedicated value slot, label, narrative and
 * (where present) progress bar, and rejects the old arithmetic-complement
 * labels that implied separate reliability/pass models.
 */
import type { Page } from '@playwright/test';
import type { ReportV2 } from '../types';
import { test, expect } from './helpers/setup';
import { mockGetReport } from './helpers/mockApi';
import { forceVariant, type ResultsPageVariant } from './helpers/experiments';
import { fixtureExactHigh, fixturePopulationDefault } from '../fixtures/reportResponses';
import { reportRateDisplay, buildNarrative, failureRateLabel } from '../components/ReportCopy';

async function assertHeadlineRiskConsistency(page: Page, variant: ResultsPageVariant, report: ReportV2): Promise<void> {
  const risk = reportRateDisplay(report);
  const value = variant === 'control'
    ? page.getByTestId('score-value')
    : page.getByTestId('failure-rate-value');
  await expect(value).toHaveText(`${risk.value}%`);
  await expect(page.getByRole('heading', { name: failureRateLabel(report) })).toBeVisible();

  if (variant === 'treatment') {
    await expect(page.getByRole('progressbar')).toHaveAttribute('aria-valuenow', String(risk.value));
  } else {
    await expect(page.getByRole('img', { name: `${failureRateLabel(report)}: ${risk.value}%` })).toBeVisible();
  }

  // The narrative sentence (ReportCopy.buildNarrative) embeds the same
  // headline number as prose, in both variants.
  await expect(page.getByText(buildNarrative(report), { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/reliability score/i)).toHaveCount(0);
  await expect(page.getByText(/pass probability/i)).toHaveCount(0);
  await expect(page.getByText(/MOT prediction/i)).toHaveCount(0);
}

async function assertShareEnabled(page: Page): Promise<void> {
  const whatsapp = page.getByRole('button', { name: 'Share on WhatsApp' });
  const copyLink = page.getByRole('button', { name: 'Copy report link' });
  await expect(whatsapp).toBeVisible();
  await expect(whatsapp).toBeEnabled();
  await expect(copyLink).toBeVisible();
  await expect(copyLink).toBeEnabled();
}

async function assertComponentsVisible(page: Page, variant: ResultsPageVariant): Promise<void> {
  if (variant === 'treatment') {
    // Components (+ Expert Analysis) live behind a collapsed <details>
    // accordion in the treatment layout (ReportDashboard.tsx). Chrome
    // exposes the <details>/<summary> pair as role "group"/"generic" (no
    // implicit button role for a bare <summary> -- confirmed against the
    // real accessibility snapshot, not assumed), so this locates the
    // summary by its visible text instead of by role. Expand it first,
    // exactly as a real user would, before asserting the section is
    // visible: this is correct, intentional treatment-layout behaviour,
    // not a bug to work around.
    await page.getByText('Component Comparison & Evidence Summary').click();
  }
  await expect(page.getByRole('heading', { name: 'Comparable-Vehicle Component Patterns' })).toBeVisible();
}

async function assertFallbackRender(page: Page, report: ReportV2): Promise<void> {
  // Positive signals the dashboard actually rendered (not the loading
  // spinner or an error state) come first, so the negative check below
  // can't pass vacuously against a page that hasn't finished loading --
  // both the loading state and an error state would also have zero
  // matching headings.
  //
  // Sample size honestly unavailable, never fabricated as a count.
  await expect(page.getByTestId('evidence-meta')).toContainText('Vehicle-matched sample unavailable');
  // No mileage slot: the header's vehicle line has no " • ... miles" suffix
  // (mileage.source === 'missing') and no year prefix (vehicle.year === null)
  // -- exact match on just "make model" proves nothing was appended.
  await expect(page.getByText(`${report.vehicle.make} ${report.vehicle.model}`, { exact: true })).toBeVisible();
  // No components card at all (report.components.available === false).
  await expect(page.getByRole('heading', { name: 'Comparable-Vehicle Component Patterns' })).toHaveCount(0);
}

for (const variant of ['control', 'treatment'] as const) {
  test.describe(`results_page_v1 = ${variant}`, () => {
    test(`exact_band fixture: consistent risk %, share enabled, components visible`, async ({ page }) => {
      await forceVariant(page, variant);
      await mockGetReport(page, fixtureExactHigh.report_token as string, fixtureExactHigh, 200);
      await page.goto(`/app/report/${fixtureExactHigh.report_token}`);

      await assertHeadlineRiskConsistency(page, variant, fixtureExactHigh);
      await assertShareEnabled(page);
      await assertComponentsVisible(page, variant);

      await page.screenshot({ path: `e2e-artifacts/report-${variant}-exact.png`, fullPage: true });
    });

    test(`population_default fixture: no components, unavailable sample size, no mileage slot`, async ({ page }) => {
      await forceVariant(page, variant);
      await mockGetReport(page, fixturePopulationDefault.report_token as string, fixturePopulationDefault, 200);
      await page.goto(`/app/report/${fixturePopulationDefault.report_token}`);

      await assertFallbackRender(page, fixturePopulationDefault);

      await page.screenshot({ path: `e2e-artifacts/report-${variant}-fallback.png`, fullPage: true });
    });
  });
}
