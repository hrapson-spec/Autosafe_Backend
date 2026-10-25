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

  // Mock mailbox: read the token the mock "sent", then land on the confirm page.
  const token = await page.evaluate((e) => (window as any).__garageMock.lastTokenFor(e), EMAIL);
  expect(token).toBeTruthy();
  await page.evaluate(() => (window as any).__garageMock.fastAssessments());
  await page.goto(`/auth/confirm?token=${token}`);
  await page.getByRole('button', { name: /confirm and sign in/i }).click();
  await expect(page.getByRole('heading', { name: 'Your workshop is ready' })).toBeVisible();
}

export function csvFor(date: string) {
  return [
    'registration,date,type,customer name,phone',
    `QR56STU,${date},MOT,Jane Doe,07000 000000`,
    `LM34NOP,${date},MOT,,`,
    `AB12CDE,${date},MOT,,`,
    `KP21ABC,${date},MOT,,`,
    `SM11AAA,${date},Service + MOT,,`,
    `UNAV01X,${date},MOT,,`,
    `FAIL01X,${date},MOT,,`,
    `NOPR01X,${date},MOT,,`,
    `RT11AAA,${date},Retest,,`,
    `SV11AAA,${date},Service only,,`,
    `LM34NOP,${date},MOT,,`,
    `!!,${date},MOT,,`,
  ].join('\n');
}
