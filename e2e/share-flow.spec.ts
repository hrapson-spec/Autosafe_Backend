/**
 * Sharing: the copy-link and WhatsApp buttons in ReportDashboard's header
 * (components/ReportDashboard.tsx's renderHeader, shared by both A/B
 * variants), plus the two edge cases around it -- reopening a share link
 * with no prior app state at all, and a persistence-degraded report where
 * sharing is honestly disabled rather than pointing at a link that doesn't
 * exist (report.persistence.share_available === false).
 *
 * The postcode-never-leaks assertions in (1) and (3) are only meaningful
 * run against the real submit flow (a postcode the user actually typed),
 * not a direct token navigation where no postcode was ever in play --
 * both tests fill and submit the form rather than jumping straight to
 * /app/report/:token.
 */
import type { Page } from '@playwright/test';
import { test, expect } from './helpers/setup';
import { seedConsent } from './helpers/consent';
import { blockExternalRequests } from './helpers/network';
import { mockCreateReport, mockGetReport } from './helpers/mockApi';
import { forceVariant } from './helpers/experiments';
import { registrationInput, postcodeInput } from './helpers/heroForm';
import { fixtureExactHigh, fixtureUnavailableDegraded } from '../fixtures/reportResponses';
import { buildNarrative, buildScopeDisclosure, sampleSizeBadge, buildWhatsAppMessage } from '../components/ReportCopy';

const POSTCODE = 'SW1A 1AA';

async function submitHappyPath(page: Page): Promise<void> {
  await page.goto('/');
  await registrationInput(page).fill('AB12CDE');
  await postcodeInput(page).fill(POSTCODE);
  await page.getByRole('button', { name: /check this car/i }).click();
  await expect(page).toHaveURL(new RegExp(`/app/report/${fixtureExactHigh.report_token}$`));
}

test('copy-link: clipboard gets exactly the share URL, and the postcode never reaches the URL bar', async ({
  page,
  context,
}) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await forceVariant(page, 'control');
  await mockCreateReport(page, fixtureExactHigh, 200);
  await mockGetReport(page, fixtureExactHigh.report_token as string, fixtureExactHigh, 200);

  await submitHappyPath(page);

  const copyLinkButton = page.getByRole('button', { name: 'Copy report link' });
  await expect(copyLinkButton).toBeEnabled();
  await copyLinkButton.click();
  await expect(page.getByText('Copied!')).toBeVisible();

  const clipboardText = await page.evaluate(() => navigator.clipboard.readText());
  expect(clipboardText).toBe(fixtureExactHigh.share_url);

  expect(page.url()).toContain('/app/report/');
  expect(page.url()).not.toContain('SW1A');
});

test('fresh context (no prior storage) reopening the share URL renders the same report, and survives a reload', async ({
  browser,
  baseURL,
}) => {
  const context = await browser.newContext({ baseURL });
  const page = await context.newPage();
  try {
    await seedConsent(page);
    await blockExternalRequests(page);
    await forceVariant(page, 'control');
    await mockGetReport(page, fixtureExactHigh.report_token as string, fixtureExactHigh, 200);

    // share_url is absolute (https://www.autosafe.one/...) exactly as the
    // backend mints it; a recipient clicking it lands on that path. The
    // test serves the same path from the preview server.
    await page.goto(new URL(fixtureExactHigh.share_url as string).pathname);

    const narrative = buildNarrative(fixtureExactHigh);
    await expect(page.getByText(narrative, { exact: true }).first()).toBeVisible();
    await expect(page.getByText(buildScopeDisclosure(fixtureExactHigh), { exact: true })).toBeVisible();
    await expect(page.getByTestId('evidence-meta')).toContainText(sampleSizeBadge(fixtureExactHigh));

    await page.screenshot({ path: 'e2e-artifacts/report-fresh-context.png', fullPage: true });

    await page.reload();
    await expect(page.getByText(narrative, { exact: true }).first()).toBeVisible();
  } finally {
    await context.close();
  }
});

test('whatsapp share message matches buildWhatsAppMessage and never contains the postcode', async ({ page }) => {
  await forceVariant(page, 'control');
  await mockCreateReport(page, fixtureExactHigh, 200);
  await mockGetReport(page, fixtureExactHigh.report_token as string, fixtureExactHigh, 200);

  await submitHappyPath(page);

  // blockExternalRequests (applied context-wide, see network.ts) aborts
  // wa.me by default -- correct for every other test, but an aborted
  // navigation lands the popup on Chrome's internal error interstitial
  // (observed empirically: popup.url() came back as
  // "chrome-error://chromewebdata/", not the requested wa.me URL),
  // destroying the one thing this test needs to inspect. Fulfilling
  // instead of aborting, for exactly this URL, keeps the popup's URL (and
  // therefore its `text` query param) intact without ever letting the
  // request reach the real wa.me over the network. Registered on the
  // context so it also covers the popup, which blockExternalRequests
  // already proved is necessary (see network.ts's doc comment) and takes
  // priority over it (Playwright tries the most-recently-registered
  // matching route first).
  await page.context().route('https://wa.me/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>wa.me stub</title>' });
  });

  const [popup] = await Promise.all([
    page.waitForEvent('popup'),
    page.getByRole('button', { name: 'Share on WhatsApp' }).click(),
  ]);
  await popup.waitForLoadState();
  const popupUrl = new URL(popup.url());
  expect(popupUrl.hostname).toBe('wa.me');
  const text = popupUrl.searchParams.get('text');

  expect(text).toBe(buildWhatsAppMessage(fixtureExactHigh));
  expect(text).toContain(fixtureExactHigh.vehicle.make);
  expect(text).toContain(fixtureExactHigh.vehicle.model);
  expect(text).toContain('12%');
  expect(text).toContain(fixtureExactHigh.share_url as string);
  expect(text).not.toContain(POSTCODE);

  await popup.close();
});

test('share-disabled: persistence-degraded report renders with share buttons disabled and microcopy visible', async ({
  page,
}) => {
  await forceVariant(page, 'control');
  await mockCreateReport(page, fixtureUnavailableDegraded, 200);

  await page.goto('/');
  await registrationInput(page).fill('ZZ99ZZZ');
  await postcodeInput(page).fill(POSTCODE);
  await page.getByRole('button', { name: /check this car/i }).click();

  await expect(page).toHaveURL(/\/app\/report\/unsaved$/);
  // Confirms the report actually rendered (as opposed to an error state) --
  // fixtureUnavailableDegraded.vehicle_data_source === 'demo'.
  await expect(page.getByTestId('demo-banner')).toBeVisible();

  // Both share buttons fall back to the same aria-label when disabled
  // (ReportDashboard.tsx's shareDisabledLabel) -- WhatsApp then Copy Link,
  // in DOM order.
  const disabledShareButtons = page.getByRole('button', { name: "Sharing isn't available for this report" });
  await expect(disabledShareButtons).toHaveCount(2);
  await expect(disabledShareButtons.first()).toBeDisabled();
  await expect(disabledShareButtons.last()).toBeDisabled();
  await expect(page.getByText("Sharing isn't available for this report")).toBeVisible();

  await page.screenshot({ path: 'e2e-artifacts/share-disabled.png', fullPage: true });
});
