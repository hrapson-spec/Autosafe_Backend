import { expect, test, type Page } from '../../web/node_modules/@playwright/test/index.mjs';

const V = '/api/garage/v1';
async function createWorkshop(page: Page, invite: string, email: string) {
  await page.goto('/sign-in');
  await expect(page.getByText(/LOCAL FULL-STACK FIXTURE REVIEW/)).toBeVisible();
  await page.getByRole('radio', { name: /Create a workshop/ }).check();
  await page.getByLabel('Your work email').fill(email);
  await page.getByLabel('Workshop name', { exact: true }).fill('Synthetic integration garage');
  await page.getByLabel('Invite code', { exact: true }).fill(invite);
  await page.getByRole('button', { name: /email me a sign-in link/i }).click();
  await expect(page.getByRole('heading', { name: 'Check your email' })).toBeVisible();
  await page.goto('/review/mailbox');
  const mail = page.locator('article').filter({ hasText: email });
  await expect(mail).toBeVisible();
  await mail.getByRole('link', { name: 'Open fixture sign-in link' }).click();
  await expect(page.getByRole('heading', { name: 'Sign in to AutoSafe Garage' })).toBeVisible();
  await expect(page.locator('meta[name=referrer]')).toHaveAttribute('content', 'same-origin');
  const response = await page.request.get(page.url());
  expect(response.headers()['referrer-policy']).toBe('same-origin');
  for (const Origin of ['null', 'https://outside.invalid']) {
    const denied = await page.request.post(`${page.url()}/consume`, { headers: { Origin } });
    expect(denied.status()).toBe(403);
  }
  // A reload is another real GET and must not burn the one-use link.
  await page.reload();
  const [confirmationPost] = await Promise.all([
    page.waitForRequest(r => r.url().endsWith('/consume') && r.method() === 'POST'),
    page.getByRole('button', { name: 'Sign in', exact: true }).click(),
  ]);
  expect(confirmationPost.headers()['origin']).toBe(new URL(page.url()).origin);
  await expect(page.getByRole('heading', { name: 'Your workshop is ready' })).toBeVisible();
}

test('real API/Postgres import, secure session, CSRF, feedback, decisions and tenant isolation', async ({ page, browser }) => {
  const info = await (await page.request.get('/review/info')).json();
  const origin = info.origin;
  const blocked: string[] = [];
  page.on('request', req => { if (!req.url().startsWith(origin)) blocked.push(req.url()); });
  await createWorkshop(page, info.invites[0], `owner-${Date.now()}@example.test`);
  expect(await page.evaluate(() => (window as any).__garageMock)).toBeUndefined();
  const cookies = await page.context().cookies();
  expect(cookies.some(c => c.name === '__Host-garage_session' && c.secure && c.httpOnly)).toBe(true);
  const csrf = await page.request.post(`${V}/imports/preview`, { headers: { Origin: 'https://outside.invalid' }, data: { format: 'manual', rows: [] } });
  expect(csrf.status()).toBe(403);
  await page.getByRole('button', { name: /import first bookings/i }).click();
  await page.locator('#paste').fill('ZZ02FXT\nZZ04FXT\nZZ02FXT');
  await page.getByRole('button', { name: 'Preview', exact: true }).click();
  await expect(page.getByTestId('preview-counts')).toContainText('2 ready, 1 flagged');
  const [commit] = await Promise.all([page.waitForResponse(r => r.url().endsWith('/commit') && r.request().method() === 'POST'), page.getByRole('button', { name: 'Import 2 rows' }).click()]);
  expect(commit.status()).toBe(202);
  await expect(page.getByTestId('import-summary')).toHaveText('Imported 2 of 3 rows.');
  const again = await page.request.post(commit.url());
  expect(again.status()).toBe(200); expect((await again.json()).already_committed).toBe(true);
  await page.getByRole('link', { name: 'View bookings' }).click();
  const ranked = page.getByRole('region', { name: /highest estimate first/i });
  await expect(ranked.getByRole('link', { name: 'ZZ02FXT' })).toBeVisible({ timeout: 15_000 });
  const all = await (await page.request.get(`${V}/bookings`)).json();
  const booking = all.items.find((b: any) => b.registration === 'ZZ02FXT');
  expect(booking.assessment.provider.name).toBe('synthetic-fullstack-fixture');
  expect(booking.assessment.display_policy.display_state).toBe('simulated');
  expect(booking.assessment.rank_key).toBeGreaterThan(0);
  await ranked.getByRole('link', { name: 'ZZ02FXT' }).click();
  await expect(page.getByTestId('source-records')).toContainText('Example washer reservoir replenished');
  await page.getByRole('radio', { name: /Offer an inspection/ }).check();
  await page.getByLabel(/Short note/).fill('Synthetic review note only');
  await page.getByRole('button', { name: 'Save decision' }).click();
  await expect(page.getByTestId('decision-item')).toContainText('Synthetic review note only');
  await page.getByRole('button', { name: 'Helpful', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Thanks, saved.' })).toBeVisible();
  await page.reload();
  await expect(page.getByTestId('decision-item')).toContainText('Synthetic review note only');
  await expect(page.getByRole('button', { name: 'Helpful', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.screenshot({ path: '../review/full-stack-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: '../review/full-stack-phone.png', fullPage: true });
  const otherContext = await browser.newContext({ baseURL: origin, serviceWorkers: 'block' });
  const other = await otherContext.newPage();
  await other.goto(origin);
  await createWorkshop(other, info.invites[1], `other-${Date.now()}@example.test`);
  const denied = await other.request.get(`${origin}${V}/bookings/${booking.id}`);
  expect(denied.status()).toBe(404);
  expect((await (await other.request.get(`${origin}${V}/bookings`)).json()).items).toEqual([]);
  await otherContext.close();
  const health = await (await page.request.get('/review/health')).json();
  expect(health.msw).toBe(false); expect(health.real_model_enabled).toBe(false);
  expect(health.dvsa_calls).toBeGreaterThanOrEqual(2); expect(health.fake_emails).toBeGreaterThanOrEqual(2);
  expect(blocked).toEqual([]);
});
