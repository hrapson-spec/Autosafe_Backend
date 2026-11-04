import { test } from '@playwright/test';
import { csvFor, signUp } from './helpers';

/** One screenshot per product area into screenshots/ (desktop), plus phone width for the list. */
const dir = 'screenshots';
const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/London' }).format(new Date());

test('screenshots of the four areas', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });

  // 1. Public intro / demo
  await page.goto('/');
  await page.getByRole('heading', { level: 1 }).waitFor();
  await page.screenshot({ path: `${dir}/1-public-intro.png`, fullPage: true });
  await page.goto('/demo');
  await page.getByText('invented to show how the service works').waitFor();
  await page.screenshot({ path: `${dir}/1-public-demo.png`, fullPage: true });

  await signUp(page);

  // 2b. Import preview (CSV with flagged rows)
  await page.getByRole('button', { name: /import first bookings/i }).click();
  await page.getByText('Upload a CSV', { exact: true }).click();
  await page.getByLabel(/CSV file/).setInputFiles({ name: 'bookings.csv', mimeType: 'text/csv', buffer: Buffer.from(csvFor(today())) });
  await page.getByTestId('preview-row').nth(13).waitFor();
  await page.getByText('Checking with the service').waitFor({ state: 'detached' });
  await page.screenshot({ path: `${dir}/2-import-preview.png`, fullPage: true });
  await page.getByLabel('Registration, row 14').fill('ZZ01FXT');
  await page.getByRole('button', { name: 'Import 13 rows' }).click();
  await page.getByTestId('import-summary').waitFor({ timeout: 15_000 });
  await page.screenshot({ path: `${dir}/2-import-result.png`, fullPage: true });

  // 2. Booking workspace (let assessments resolve first)
  await page.evaluate(() => (window as any).__garageMock.seedDemoBookings());
  await page.getByRole('link', { name: 'View bookings' }).click();
  await page.getByRole('region', { name: /highest estimate first/i }).getByText('ZZ14FXT').waitFor({ timeout: 15_000 });
  await page.getByRole('region', { name: /highest estimate first/i }).getByText('DEMO05').waitFor({ timeout: 15_000 });
  await page.screenshot({ path: `${dir}/2-booking-workspace.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: `${dir}/2-booking-workspace-phone.png`, fullPage: true });
  await page.setViewportSize({ width: 1280, height: 900 });

  // 3. Individual briefing, with a decision recorded
  await page.getByRole('link', { name: 'ZZ02FXT' }).click();           // a real-shape briefing (backend engine output)
  await page.getByRole('heading', { name: 'Briefing for ZZ02FXT' }).waitFor();
  await page.getByRole('radio', { name: /Offer an inspection/ }).check();
  await page.getByLabel(/Short note/).fill('Ask about the rectified items first');
  await page.getByRole('button', { name: 'Save decision' }).click();
  await page.getByTestId('decision-item').first().waitFor();
  await page.screenshot({ path: `${dir}/3-briefing.png`, fullPage: true });
  await page.getByRole('link', { name: 'Back to bookings' }).click();
  await page.getByRole('region', { name: /^Unsupported vehicles/ }).getByRole('link', { name: 'NOPR01X' }).click();
  await page.getByTestId('evidence-gaps').waitFor();
  await page.screenshot({ path: `${dir}/3-briefing-evidence-gap.png`, fullPage: true });

  // 4. Team and settings
  await page.getByRole('link', { name: 'Team and settings' }).click();
  await page.getByLabel('Add a colleague by email').fill('mechanic@example.test');
  await page.getByRole('button', { name: 'Add and email a link' }).click();
  await page.getByText('A sign-in link has been emailed to them.').waitFor();
  await page.screenshot({ path: `${dir}/4-team-settings.png`, fullPage: true });
});
