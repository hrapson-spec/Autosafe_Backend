import { expect, test } from '@playwright/test';

test('fixture reviewer can sign in without hidden developer hooks', async ({ page }) => {
  await page.goto('/demo');
  await expect(page.getByRole('link', { name: 'Create a demo workshop' })).toBeVisible();
  await page.getByRole('link', { name: 'Create a demo workshop' }).click();
  await expect(page.getByText(/Fixture simulation — all vehicle histories/)).toBeVisible();
  await page.getByRole('radio', { name: /Create a workshop/ }).check();
  await page.getByLabel('Your work email').fill('reviewer@example.test');
  await page.getByLabel('Workshop name', { exact: true }).fill('Fixture review garage');
  await page.getByLabel('Invite code', { exact: true }).fill('PILOT-0001');
  await page.getByRole('button', { name: /email me a sign-in link/i }).click();
  await page.getByRole('button', { name: 'Confirm fixture sign-in' }).click();
  await expect(page.getByRole('heading', { name: 'Your workshop is ready' })).toBeVisible();
  await expect(page.getByText(/Fixture simulation — all vehicle histories/)).toBeVisible();
});
