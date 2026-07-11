/**
 * Direct navigation to /app/report/:token for a token that can't resolve to
 * a report, across the three ways that can happen: the token is well-formed
 * but unknown (404 report_not_found), it once resolved but has since
 * expired (410 report_expired), and the request never reached a server at
 * all (aborted, mirroring offline/DNS/CORS-block).
 *
 * ReportUnavailable.tsx is structurally incapable of rendering vehicle data
 * (its only prop is the `reason` enum) -- the "no vehicle-ish strings"
 * assertion is the regression guard for that guarantee holding in practice,
 * not just in the component's own doc comment.
 *
 * No experiment forcing here: ReportUnavailable never calls getVariant --
 * only ReportDashboard (the success path) does, which none of these three
 * cases ever reach.
 */
import type { Page } from '@playwright/test';
import { test, expect } from './helpers/setup';
import { mockGetReport, abortGetReport } from './helpers/mockApi';
import { fixtureErrorEnvelopes } from '../fixtures/reportResponses';

const BOGUS_TOKEN = 'BOGUSTOKEN123';

// Strings from fixtures/reportResponses.ts's vehicle identities -- none of
// these may ever appear on a screen that has no report to show.
const VEHICLE_ISH_STRINGS = ['TESTMAKE', 'TESTMODEL', 'AGEMODEL', 'RAREMODEL', 'OBSCUREMODEL', 'DEMO'];

async function assertNoVehicleStrings(page: Page): Promise<void> {
  const bodyText = (await page.textContent('body')) ?? '';
  for (const needle of VEHICLE_ISH_STRINGS) {
    expect(bodyText).not.toContain(needle);
  }
}

test('404 report_not_found -> "isn\'t valid" screen, no vehicle data', async ({ page }) => {
  await mockGetReport(page, BOGUS_TOKEN, fixtureErrorEnvelopes.report_not_found, 404);
  await page.goto(`/app/report/${BOGUS_TOKEN}`);

  await expect(page.getByRole('heading', { name: "That report link isn't valid" })).toBeVisible();
  await assertNoVehicleStrings(page);
});

test('410 report_expired -> expired screen with a working re-check CTA, no vehicle data', async ({ page }) => {
  await mockGetReport(page, BOGUS_TOKEN, fixtureErrorEnvelopes.report_expired, 410);
  await page.goto(`/app/report/${BOGUS_TOKEN}`);

  await expect(page.getByRole('heading', { name: 'This report link has expired' })).toBeVisible();
  await assertNoVehicleStrings(page);

  await page.screenshot({ path: 'e2e-artifacts/token-expired.png', fullPage: true });

  await page.getByRole('button', { name: 'Check a vehicle' }).click();
  await expect(page).toHaveURL(/\/app$/);
  await expect(page.getByText('See what the MOT evidence says.')).toBeVisible();
});

test('network abort -> generic error screen, no vehicle data', async ({ page }) => {
  await abortGetReport(page, BOGUS_TOKEN);
  await page.goto(`/app/report/${BOGUS_TOKEN}`);

  await expect(page.getByRole('heading', { name: "We couldn't load this report" })).toBeVisible();
  await assertNoVehicleStrings(page);
});
