import { expect, test } from '@playwright/test';
import { csvFor, signUp } from './helpers';

const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/London' }).format(new Date());

test('sign in -> create workshop -> import -> list -> briefing -> decision', async ({ page }) => {
  await signUp(page);
  await page.getByRole('button', { name: /import first bookings/i }).click();

  // CSV upload: a customer-style column must be reported ignored and never appear in the preview.
  await expect(page.getByRole('heading', { name: 'Import bookings' })).toBeVisible();
  await page.getByText('Upload a CSV', { exact: true }).click();
  await page.getByLabel(/CSV file/).setInputFiles({ name: 'bookings.csv', mimeType: 'text/csv', buffer: Buffer.from(csvFor(today())) });
  await expect(page.getByText(/Ignored and not uploaded: customer name, phone/)).toBeVisible();
  await expect(page.locator('body')).not.toContainText('Jane Doe');

  const rows = page.getByTestId('preview-row');
  await expect(rows).toHaveCount(12);
  await expect(rows.nth(10)).toContainText('Duplicate');
  await expect(rows.nth(11)).toContainText('Invalid');
  await expect(page.getByTestId('preview-counts')).toContainText('10 ready, 2 flagged');

  // Fix the invalid row by editing it, then import.
  await page.getByLabel('Registration, row 12').fill('TT70ZZZ');
  await expect(page.getByTestId('preview-counts')).toContainText('11 ready, 1 flagged');
  await page.getByRole('button', { name: 'Import 11 rows' }).click();
  await expect(page.getByTestId('import-summary')).toHaveText('Imported 11 of 12 rows.');
  await expect(page.getByTestId('result-row').filter({ hasText: 'Skipped, duplicate' })).toHaveCount(1);

  await page.getByRole('link', { name: 'View bookings' }).click();
  const ranked = page.getByRole('region', { name: /highest estimate first/i });
  // Assessments start pending, then resolve without a reload. Order is by the service's rounded percentage.
  await expect(ranked.getByTestId('booking-row')).toHaveCount(6, { timeout: 15_000 });
  const order = await ranked.getByTestId('booking-row').evaluateAll((els) => els.map((e) => e.getAttribute('data-reg')));
  expect(order).toEqual(['TT70ZZZ', 'QR56STU', 'LM34NOP', 'AB12CDE', 'KP21ABC', 'SM11AAA']);
  await expect(ranked.getByTestId('booking-row').first().getByTestId('estimate')).toContainText('55%');
  await expect(page.getByRole('region', { name: /not ranked yet/i })).toContainText('UNAV01X');
  await expect(page.getByRole('region', { name: /not ranked yet/i })).toContainText('FAIL01X');
  await expect(page.getByRole('region', { name: /^Unsupported vehicles/ })).toContainText('NOPR01X');
  await expect(page.getByRole('region', { name: /^Retests/ })).toContainText('RT11AAA');
  await expect(page.getByRole('region', { name: /^Service only/ })).toContainText('SV11AAA');

  await ranked.getByRole('link', { name: 'LM34NOP' }).click();
  await expect(page.getByRole('heading', { name: 'Briefing for LM34NOP' })).toBeVisible();
  await expect(page.getByText('estimated chance of failing or needing rectification during the MOT')).toBeVisible();
  await expect(page.getByTestId('follow-ups').getByRole('listitem')).toHaveCount(3);
  await expect(page.getByTestId('source-records')).toContainText('historical');
  await expect(page.getByTestId('staff-only')).toHaveText('For workshop staff only. Not a defect finding.');
  await expect(page.getByText("Don't include customer names, phone numbers or other customer details.")).toBeVisible();

  await page.getByRole('radio', { name: /Adjust the booking plan/ }).check();
  await page.getByLabel(/Short note/).fill('Bay 2, early slot');
  await page.getByRole('button', { name: 'Save decision' }).click();
  const item = page.getByTestId('decision-item').first();
  await expect(item).toContainText('Adjust the booking plan');
  await expect(item).toContainText('By owner@example.test');

  await page.getByRole('button', { name: 'Helpful', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Helpful', exact: true })).toHaveAttribute('aria-pressed', 'true');

  // Session survives a reload and the decision is still there.
  await page.reload();
  await expect(page.getByTestId('decision-item')).toHaveCount(1);
});

test('keyboard: skip link first, and the sign-in form works without a mouse', async ({ page }) => {
  await page.goto('/sign-in');
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Skip to main content' })).toBeFocused();
  await page.getByLabel('Your work email').focus();
  await page.keyboard.type('someone@example.test');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: /email me a sign-in link/i })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: 'Check your email' })).toBeVisible();
});

test('no horizontal page scroll at phone width on the public screens', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  for (const path of ['/', '/demo', '/demo/briefing/demo-1', '/sign-in']) {
    await page.goto(path);
    await page.waitForLoadState('networkidle');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `horizontal overflow on ${path}`).toBeLessThanOrEqual(0);
  }
});

test('no horizontal page scroll at phone width in the signed-in workspace', async ({ page }) => {
  await signUp(page);
  await page.evaluate(() => (window as any).__garageMock.seedDemoBookings());
  await page.setViewportSize({ width: 375, height: 800 });
  for (const path of ['/app/bookings', '/app/import', '/app/settings']) {
    await page.goto(path);
    await page.getByRole('heading', { level: 1 }).waitFor();
    await page.waitForLoadState('networkidle');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `horizontal overflow on ${path}`).toBeLessThanOrEqual(0);
  }
});
