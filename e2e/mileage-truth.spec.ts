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
 * Every mocked fixture used here for a dated assertion carries a date-only
 * observed_at ('YYYY-MM-DD'), per this wave's mock-authoring rule: a
 * zone-less 'T00:00:00' timestamp is parsed as local time by
 * ReportCopy.tsx's formatDateGB, which renders a day early on a BST host --
 * a known, pre-existing bug this suite works around by construction rather
 * than asserting around. fixtureObservedHighMileage's and
 * fixtureAnomalyMissing's own observed_at fields are already safe
 * (date-only / null); test 3 below deliberately asserts only
 * fixtureKmConverted's unit-conversion suffix (which never reads
 * observed_at), never the dated portion of its line, since that fixture's
 * own observed_at is a full zone-less timestamp.
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
  const textOutsideDisclosure = bodyText.split(disclosure).join(' ');
  expect(textOutsideDisclosure).not.toMatch(/\d[\d,]* miles/);
});

test('km-recorded reading: pre-conversion km figure is disclosed alongside the converted miles value', async ({ page }) => {
  await forceVariant(page, 'control');
  await mockCreateReport(page, fixtureKmConverted, 200);
  await mockGetReport(page, fixtureKmConverted.report_token as string, fixtureKmConverted, 200);

  await driveToReport(page, fixtureKmConverted.registration, 'SW1A 1AA');

  await expect(page).toHaveURL(new RegExp(`/app/report/${fixtureKmConverted.report_token}$`));

  // Only the unit-conversion suffix is asserted -- never the dated portion
  // of lastRecordedMileageLine's output, which this fixture's own
  // non-date-only observed_at ('2026-07-01T00:00:00') would render a day
  // early on this (BST) host via the pre-existing formatDateGB bug (see
  // this file's header comment). The numeric part is read from the fixture
  // itself, not hand-transcribed, so it can never drift from what
  // ReportCopy.tsx's identical toLocaleString('en-GB') call produces.
  const kmSuffix = ` — converted from ${fixtureKmConverted.mileage.original_value!.toLocaleString('en-GB')} km`;
  await expect(page.getByText(kmSuffix)).toBeVisible();
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
