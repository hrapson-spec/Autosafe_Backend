/**
 * OA-005: the landing handoff parameters are removed from the address bar on
 * load (before any analytics page view), and with collection OFF the app
 * never talks to the first-party collector, even through a full journey.
 */
import { test, expect } from './helpers/setup';
import { mockCreateReport, mockGetReport } from './helpers/mockApi';
import { registrationInput, postcodeInput } from './helpers/heroForm';
import { fixtureExactHigh } from '../fixtures/reportResponses';

const AL = '5b1d7a52-5d5c-4f55-9a52-3c6e1f1c8a11';

test.describe('acquisition landing handoff (collection OFF)', () => {
  for (const path of ['/app', '/']) {
    test(`al/src are stripped from ${path} and nothing is sent to the collector`, async ({ page }) => {
      const collectorRequests: string[] = [];
      page.on('request', (req) => {
        if (req.url().includes('/api/acquisition')) collectorRequests.push(req.url());
      });
      await page.goto(`${path}?al=${AL}&src=google_organic&keep=1`);
      await expect(registrationInput(page)).toBeVisible();
      await expect(page).toHaveURL(new RegExp(`${path === '/' ? '/' : path}\\?keep=1$`));
      expect(page.url()).not.toContain(AL);
      expect(page.url()).not.toContain('src=');
      expect(collectorRequests).toEqual([]);
    });
  }

  test('a full check journey sends no collector request while the flag is off', async ({ page }) => {
    const collectorRequests: string[] = [];
    page.on('request', (req) => {
      if (req.url().includes('/api/acquisition')) collectorRequests.push(req.url());
    });
    await mockCreateReport(page, fixtureExactHigh, 200);
    await mockGetReport(page, fixtureExactHigh.report_token as string, fixtureExactHigh, 200);
    await page.goto(`/app?al=${AL}&src=google_organic`);
    await registrationInput(page).fill('AB12CDE');
    await postcodeInput(page).fill('SW1A 1AA');
    await page.getByRole('button', { name: /check this car/i }).click();
    await expect(page.getByTestId('comparison-result')).toBeVisible();
    expect(collectorRequests).toEqual([]);
    expect(page.url()).not.toContain(AL);
  });
});
