import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { RANKED_ORDER, csvFor, signUp } from './helpers';

/** The backend engine's real output for the fixture vehicles (the same file the mock API serves). */
const FX = JSON.parse(readFileSync('src/mocks/briefingFixtures.json', 'utf8'));

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
  await expect(rows).toHaveCount(14);
  await expect(rows.nth(12)).toContainText('Duplicate');
  await expect(rows.nth(13)).toContainText('Invalid');
  await expect(page.getByLabel('Time, row 2')).toHaveValue('08:15');                     // "8:15" normalised
  await expect(page.getByTestId('preview-counts')).toContainText('12 ready, 2 flagged');

  // Fix the invalid row by editing it, then import (a background job: progress shows N of M).
  await page.getByLabel('Registration, row 14').fill('ZZ01FXT');
  await expect(page.getByTestId('preview-counts')).toContainText('13 ready, 1 flagged');
  await page.getByRole('button', { name: 'Import 13 rows' }).click();
  await expect(page.getByTestId('import-progress')).toContainText('of 13 rows');
  await expect(page.getByTestId('import-summary')).toHaveText('Imported 13 of 14 rows.', { timeout: 15_000 });
  await expect(page.getByTestId('result-row').filter({ hasText: 'Skipped, duplicate' })).toHaveCount(1);

  await page.getByRole('link', { name: 'View bookings' }).click();
  const ranked = page.getByRole('region', { name: /highest estimate first/i });
  // Assessments start pending, then resolve without a reload. Order is the service's rank_key, not the rounded percentage.
  await expect(ranked.getByTestId('booking-row')).toHaveCount(8, { timeout: 15_000 });
  const order = await ranked.getByTestId('booking-row').evaluateAll((els) => els.map((e) => e.getAttribute('data-reg')));
  expect(order).toEqual(RANKED_ORDER);
  const pct = await ranked.getByTestId('booking-row').evaluateAll((els) => els.map((e) => e.querySelector('[data-testid=estimate]')?.textContent ?? ''));
  expect(pct.slice(0, 4).every((t) => t.startsWith('55%'))).toBe(true);                     // four equal rounded values, still ordered
  await expect(ranked.getByTestId('booking-row').first().getByTestId('estimate')).toContainText('55%');
  await expect(ranked.locator('[data-reg=ZZ14FXT]').getByTestId('appointment-time')).toHaveText('08:15');
  await expect(ranked.locator('[data-reg=ZZ39FXT]').getByTestId('appointment-time')).toHaveText('14:00');
  await expect(page.getByRole('region', { name: /not ranked yet/i })).toContainText('UNAV01X');
  await expect(page.getByRole('region', { name: /not ranked yet/i })).toContainText('FAIL01X');
  await expect(page.getByRole('region', { name: /^Unsupported vehicles/ })).toContainText('NOPR01X');
  await expect(page.getByRole('region', { name: /^Retests/ })).toContainText('RT11AAA');
  await expect(page.getByRole('region', { name: /^Service only/ })).toContainText('SV11AAA');

  // A real-shape briefing: the newest event of ZZ02FXT is a PRS-typed fail + pass one second later = ONE rectified record.
  const fx = FX.ZZ02FXT;
  await ranked.getByRole('link', { name: 'ZZ02FXT' }).click();
  await expect(page.getByRole('heading', { name: 'Briefing for ZZ02FXT' })).toBeVisible();
  await expect(page.getByText('estimated chance of failing or needing rectification during the MOT')).toBeVisible();
  await expect(page.getByTestId('band-legend')).toContainText('provisional');
  await expect(page.getByTestId('briefing-summary')).toHaveText(fx.summary);
  const questions = page.getByTestId('follow-ups').getByRole('listitem');
  await expect(questions).toHaveCount(fx.follow_up_questions.length);
  for (let i = 0; i < fx.follow_up_questions.length; i++) await expect(questions.nth(i)).toContainText(fx.follow_up_questions[i].text);
  const recs = page.getByTestId('source-records').getByRole('listitem').filter({ has: page.locator('time') });
  await expect(page.getByTestId('source-records').locator('[data-result=RECTIFIED]').first()).toContainText('Failed items rectified at the test, then passed');
  await expect(page.getByTestId('source-records')).toContainText('Defect rectified at the test, recorded at test on');
  await expect(page.getByTestId('source-records')).toContainText('historical');
  expect(await page.getByTestId('source-records').locator('[data-result]').count()).toBe(fx.source_records.length);
  await expect(page.getByTestId('briefing-provenance')).toContainText(fx.briefing_version);
  await expect(page.getByTestId('staff-only')).toHaveText('For workshop staff only. Not a defect finding.');
  await expect(page.getByText("Don't include customer names, phone numbers or other customer details.")).toBeVisible();
  await expect(page.locator('body')).not.toContainText(/current defects?/i);
  void recs;

  await page.getByRole('radio', { name: /Adjust the booking plan/ }).check();
  await page.getByLabel(/Short note/).fill('Bay 2, early slot');
  await page.getByRole('button', { name: 'Save decision' }).click();
  const item = page.getByTestId('decision-item').first();
  await expect(item).toContainText('Adjust the booking plan');
  await expect(item).toContainText('By owner@example.test');

  await page.getByRole('button', { name: 'Not helpful', exact: true }).click();
  await page.getByLabel(/What was wrong/).selectOption('too_long');
  await expect(page.getByRole('status').filter({ hasText: 'Thanks, saved.' })).toBeVisible();

  // Session survives a reload; the decision and this member's feedback (with its reason) are still there.
  await page.reload();
  await expect(page.getByTestId('decision-item')).toHaveCount(1);
  await expect(page.getByRole('button', { name: 'Not helpful', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByLabel(/What was wrong/)).toHaveValue('too_long');

  // A vehicle with no recorded history: the explicit gap comes from the same engine (G02), not from the page.
  await page.getByRole('link', { name: 'Back to bookings' }).click();
  await page.getByRole('region', { name: /^Unsupported vehicles/ }).getByRole('link', { name: 'NOPR01X' }).click();
  await expect(page.getByTestId('evidence-gaps')).toContainText(FX.__no_prior_test__.evidence_gaps[0].text);
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
