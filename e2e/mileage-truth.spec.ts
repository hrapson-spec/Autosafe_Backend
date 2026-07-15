/**
 * Mileage truth: the report's mileage figures and provenance disclosures
 * must never overstate, understate, or fabricate what the evidence actually
 * supports (see components/ReportCopy.tsx's module doc comment -- "no
 * fabrication, enforced here"). Four cells of the evidence-ladder /
 * mileage-resolution matrix, exercised end-to-end through the real customer
 * flow (registration -> submit -> rendered report) rather than only at the
 * ReportCopy unit-test level (already covered by ReportCopy.test.tsx):
 *
 *   1. A high, exact-band mileage reading renders its full dated
 *      last-recorded-mileage line and the population-average badge (the
 *      report never implies a vehicle-specific prediction).
 *   2. A rejected/anomalous reading with no usable mileage renders the
 *      honest "not used" disclosure and shows NO numeric mileage anywhere
 *      on the page -- the strongest form of the no-fabrication guarantee.
 *   3. A kilometre-recorded reading discloses its pre-conversion km figure
 *      rather than silently showing only the converted miles number.
 *   4. A DVSA outage at report-creation time renders the established error
 *      UX and never a fabricated report.
 *
 * Timezone: this suite pins the browser to Europe/London
 * (test.use({ timezoneId: 'Europe/London' }) below). That is load-bearing,
 * not cosmetic -- the DVSA "one day early" defect only reproduces for a
 * summer/BST date, so a UTC-executed run could never expose this regression
 * class. The backend now emits observed_at as a canonical date-only
 * 'YYYY-MM-DD' string (report_service.resolve_mileage), so these mocks use
 * the real fixture payloads directly -- no hand-built local wire overrides.
 *
 * Test 1 uses fixtureObservedHighMileage as-is (canonical date-only
 * observed_at '2025-11-02') and proves the canonical wire form renders
 * correctly end to end. Test 3 uses fixtureKmConverted, whose observed_at is
 * a LEGACY zone-less 'T00:00:00' timestamp ('2026-07-01T00:00:00', a summer
 * date) -- its full dated line is asserted here as the legacy-payload render
 * proof under the pinned BST timezone. Test 2's fixtureAnomalyMissing has a
 * null observed_at (no date to render) but exercises Option C's mileage-free
 * scope line. The zone-less formatDateGB four-class regression matrix is
 * proven at the unit level in components/formatDateGB.tzspec.ts.
 */
import type { Page } from '@playwright/test';
import { test, expect } from './helpers/setup';
import { mockCreateReport, mockGetReport } from './helpers/mockApi';
import { forceVariant } from './helpers/experiments';
import { registrationInput, postcodeInput } from './helpers/heroForm';
import {
  fixtureObservedHighMileage,
  fixtureAnomalyMissing,
  fixtureKmConverted,
  fixtureErrorEnvelopes,
} from '../fixtures/reportResponses';
import { lastRecordedMileageLine, populationBadge } from '../components/ReportCopy';
import { mapErrorToMessage } from '../services/errorMessages';

// Pin the browser timezone so the date-rendering assertions are meaningful:
// the DVSA one-day-early defect only manifests for a summer/BST date on a
// Europe/London host. A UTC-executed run (CI's default) could not expose it.
test.use({ timezoneId: 'Europe/London' });

/**
 * Fills both HeroForm fields and submits -- the real customer flow
 * (registration -> submit -> rendered report), mirroring
 * report-and-reset.spec.ts / share-flow.spec.ts's happy-path drive rather
 * than a direct token navigation.
 */
async function driveToReport(page: Page, registration: string, postcode: string): Promise<void> {
  await page.goto('/');
  await registrationInput(page).fill(registration);
  await postcodeInput(page).fill(postcode);
  await page.getByRole('button', { name: /check this car/i }).click();
}

test('high-mileage exact-band reading: dated last-recorded-mileage line and population badge render', async ({ page }) => {
  await forceVariant(page, 'control');
  // Canonical wire shape: fixtureObservedHighMileage's observed_at is already
  // the date-only 'YYYY-MM-DD' form the backend now emits, so the fixture is
  // used directly -- no hand-built local override. Renders under the pinned
  // Europe/London browser timezone (see file header).
  await mockCreateReport(page, fixtureObservedHighMileage, 200);
  await mockGetReport(page, fixtureObservedHighMileage.report_token as string, fixtureObservedHighMileage, 200);

  await driveToReport(page, fixtureObservedHighMileage.registration, 'SW1A 1AA');

  await expect(page).toHaveURL(new RegExp(`/app/report/${fixtureObservedHighMileage.report_token}$`));

  // Anchor: ReportCopy's real function must produce the exact acceptance
  // string for this fixture (regression guard against wording drift)...
  const expectedLine = 'Last recorded MOT mileage: 112,406 miles on 2 Nov 2025';
  expect(lastRecordedMileageLine(fixtureObservedHighMileage)).toBe(expectedLine);
  // ...then confirm the rendered page actually shows it. Rendered exactly
  // once (ReportDashboard.tsx's shared renderHeader(), called once per
  // variant branch), so no .first() is needed here.
  await expect(page.getByText(expectedLine, { exact: true })).toBeVisible();

  await expect(page.getByTestId('population-badge')).toHaveText(populationBadge().label);
});

test('anomalous/missing mileage: "not used" disclosure renders and no numeric mileage appears anywhere', async ({ page }) => {
  await forceVariant(page, 'control');
  await mockCreateReport(page, fixtureAnomalyMissing, 200);
  await mockGetReport(page, fixtureAnomalyMissing.report_token as string, fixtureAnomalyMissing, 200);

  await driveToReport(page, fixtureAnomalyMissing.registration, 'SW1A 1AA');

  await expect(page).toHaveURL(new RegExp(`/app/report/${fixtureAnomalyMissing.report_token}$`));

  const disclosure = "Recorded mileage was not used: the vehicle's MOT mileage history is inconsistent, so no reading could be trusted.";
  // buildNarrative(report) repeats identically in two places in the control
  // layout (pie-card paragraph + Evidence Summary card) -- .first() avoids a
  // strict-mode violation, exactly as report-and-reset.spec.ts's narrative
  // check does for the same reason.
  await expect(page.getByText(disclosure).first()).toBeVisible();

  // No numeric mileage anywhere on the page -- source: 'missing' means
  // ReportCopy.tsx's mileage-phrase functions (buildMileagePhrase,
  // mileageHeaderValue, lastRecordedMileageLine) all return null for this
  // fixture, so nothing should ever interpolate a number in front of
  // "miles". Checked against the whole rendered body, with the disclosure
  // sentence itself excluded first -- it contains no such pattern, but this
  // keeps the check honestly scoped to "outside the disclosure copy" per
  // the brief.
  const bodyText = (await page.textContent('body')) ?? '';
  expect(bodyText).toContain(disclosure);

  // Option C (acceptance-ii page copy): with the anomalous reading rejected,
  // W4 (the disclosure above) already states mileage was not used and why, so
  // the scope line (W5) must carry NO second mileage statement -- it names
  // only the comparison scope. Assert the mileage-free scope sentence is on
  // the page and the pre-Option-C duplicate clause is gone.
  expect(bodyText).toContain('This comparison uses TESTMAKE ANOMALYMODEL records in the matched age band.');
  expect(bodyText).not.toContain('mileage was not used because no reliable recorded mileage was available');

  const textOutsideDisclosure = bodyText.split(disclosure).join(' ');
  expect(textOutsideDisclosure).not.toMatch(/\d[\d,]* miles/);
});

test('km-recorded reading: full dated last-recorded-mileage line, incl. the pre-conversion km figure, renders', async ({ page }) => {
  await forceVariant(page, 'control');
  await mockCreateReport(page, fixtureKmConverted, 200);
  await mockGetReport(page, fixtureKmConverted.report_token as string, fixtureKmConverted, 200);

  await driveToReport(page, fixtureKmConverted.registration, 'SW1A 1AA');

  await expect(page).toHaveURL(new RegExp(`/app/report/${fixtureKmConverted.report_token}$`));

  // fixtureKmConverted's observed_at is a LEGACY zone-less 'T00:00:00'
  // timestamp for a July (BST) date ('2026-07-01T00:00:00') -- the exact
  // shape that rendered a day early pre-fix. Under the pinned Europe/London
  // browser timezone (file header) this asserts the FULL dated line -- the
  // date AND the km-conversion suffix -- end to end: the legacy-payload
  // render proof. Rendered once, as a single <p> in ReportDashboard's
  // renderHeader(), so { exact: true } (mirrors test 1).
  const expectedLine = 'Last recorded MOT mileage: 111,847 miles on 1 Jul 2026 — converted from 180,000 km';
  expect(lastRecordedMileageLine(fixtureKmConverted)).toBe(expectedLine);
  await expect(page.getByText(expectedLine, { exact: true })).toBeVisible();
});

test('DVSA outage at report creation: established error UX renders, no fabricated report', async ({ page }) => {
  await mockCreateReport(page, fixtureErrorEnvelopes.dvsa_unavailable, 503);
  await page.goto('/app');

  await registrationInput(page).fill('ZZ99ZZZ');
  await postcodeInput(page).fill('SW1A 1AA');
  await page.getByRole('button', { name: /check this car/i }).click();

  const banner = page.getByRole('alert');
  await expect(banner).toBeVisible();
  await expect(banner).toHaveText(mapErrorToMessage('dvsa_unavailable'));

  // No fabricated report: the app never navigates off the form on error
  // (App.tsx's catch block only calls setError, never navigate()), and no
  // report-only DOM (population badge, evidence meta) is ever mounted.
  await expect(page).toHaveURL(/\/app$/);
  await expect(page.getByTestId('population-badge')).toHaveCount(0);
  await expect(page.getByTestId('evidence-meta')).toHaveCount(0);
});
