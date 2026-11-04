import { expect, type Page } from '@playwright/test';

export const EMAIL = 'owner@example.test';

/** Mock-only hooks exposed by the MSW layer (window.__garageMock). Not part of the product. */
export async function signUp(page: Page, workshop = 'High Street MOT') {
  await page.goto('/sign-in');
  await page.getByRole('radio', { name: /Create a workshop/ }).check();
  await page.getByLabel('Your work email').fill(EMAIL);
  await page.getByLabel('Workshop name', { exact: true }).fill(workshop);
  await page.getByLabel('Invite code', { exact: true }).fill('PILOT-0001');
  await page.getByRole('button', { name: /email me a sign-in link/i }).click();
  await expect(page.getByRole('heading', { name: 'Check your email' })).toBeVisible();

  // Mock mailbox: read the token the mock "sent". The real link opens the SERVER-rendered confirm page, whose
  // button POSTs .../consume and redirects to /app; the mock layer cannot render that page, so the hook below
  // stands in for the POST, then we land where the redirect would.
  const token = await page.evaluate((e) => (window as any).__garageMock.lastTokenFor(e), EMAIL);
  expect(token).toBeTruthy();
  await page.evaluate(() => (window as any).__garageMock.fastAssessments());
  await page.evaluate((t) => (window as any).__garageMock.consumeToken(t), token);
  await page.goto('/app');
  await expect(page.getByRole('heading', { name: 'Your workshop is ready' })).toBeVisible();
}

/**
 * Registrations ZZnnFXT are the synthetic plates of the 50 raw-shape fixtures; the mock API serves the backend
 * engine's real briefing for them (src/mocks/briefingFixtures.json). The mock's estimate for each is derived from a
 * hash of the plate, so these five MOT plates all round to 55% and only the service's rank_key orders them:
 * ZZ14 > ZZ39 > ZZ04 > ZZ29 (alphabetical order would be ZZ04, ZZ14, ZZ29, ZZ39).
 */
export const RANKED_ORDER = ['ZZ14FXT', 'ZZ39FXT', 'ZZ04FXT', 'ZZ29FXT', 'ZZ02FXT', 'ZZ15FXT', 'ZZ01FXT', 'ZZ44FXT'];

export function csvFor(date: string) {
  return [
    'registration,date,type,customer name,phone,appointment time',
    `ZZ04FXT,${date},MOT,Jane Doe,07000 000000,10:30`,
    `ZZ14FXT,${date},MOT,,,8:15`,
    `ZZ29FXT,${date},MOT,,,`,
    `ZZ39FXT,${date},MOT,,,14:00`,
    `ZZ02FXT,${date},MOT,,,`,
    `ZZ15FXT,${date},Service + MOT,,,`,
    `ZZ44FXT,${date},MOT,,,`,
    `UNAV01X,${date},MOT,,,`,
    `FAIL01X,${date},MOT,,,`,
    `NOPR01X,${date},MOT,,,`,
    `RT11AAA,${date},Retest,,,`,
    `SV11AAA,${date},Service only,,,`,
    `ZZ14FXT,${date},MOT,,,`,
    `!!,${date},MOT,,,`,
  ].join('\n');
}
