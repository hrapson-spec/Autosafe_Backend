/**
 * Form lifecycle: the HeroForm/HomePage regressions this wave's App
 * restructure specifically had to fix (see App.tsx's HomePage doc comment)
 * -- a failed submission, or unrelated state resolving (stats), must never
 * wipe out what the user already typed. Case (4) covers the same
 * edit-lock guarantee for the `?reg=` pre-fill path (HeroForm.tsx's
 * `userEdited` flag). Case (5) is the intentional, user-initiated reset --
 * the one path that's *supposed* to clear the form.
 */
import { test, expect } from './helpers/setup';
import { mockCreateReport, mockGetReport, gateStats, DEFAULT_STATS } from './helpers/mockApi';
import { forceVariant } from './helpers/experiments';
import { registrationInput, postcodeInput } from './helpers/heroForm';
import { fixtureErrorEnvelopes, fixtureExactHigh } from '../fixtures/reportResponses';
import { mapErrorToMessage } from '../services/errorMessages';

const ENTRY_PATHS = ['/', '/app'] as const;

test.describe('form-lifecycle', () => {
  // ---------------------------------------------------------------------
  // (1) & (2): a failed submission must leave both fields exactly as typed
  // and show the error code's mapped copy. Run against both entry paths
  // per the wave brief; the named screenshot deliverable is only captured
  // once (on the '/' pass) since the two paths render the identical
  // HomePage component and a second copy would just overwrite the first.
  // ---------------------------------------------------------------------
  for (const entryPath of ENTRY_PATHS) {
    test(`404 vehicle_not_found preserves both fields @ ${entryPath}`, async ({ page }) => {
      await mockCreateReport(page, fixtureErrorEnvelopes.vehicle_not_found, 404);
      await page.goto(entryPath);

      const regInput = registrationInput(page);
      const postInput = postcodeInput(page);
      await regInput.fill('ZZ99ZZZ');
      await postInput.fill('SW1A 1AA');
      await page.getByRole('button', { name: /check this car/i }).click();

      const banner = page.getByRole('alert');
      await expect(banner).toBeVisible();
      await expect(banner).toHaveText(mapErrorToMessage('vehicle_not_found'));

      await expect(regInput).toHaveValue('ZZ99ZZZ');
      await expect(postInput).toHaveValue('SW1A 1AA');

      if (entryPath === '/') {
        await page.screenshot({ path: 'e2e-artifacts/form-preserved-after-404.png', fullPage: true });
      }
    });

    test(`503 dvsa_unavailable preserves both fields with a distinct message @ ${entryPath}`, async ({ page }) => {
      await mockCreateReport(page, fixtureErrorEnvelopes.dvsa_unavailable, 503);
      await page.goto(entryPath);

      const regInput = registrationInput(page);
      const postInput = postcodeInput(page);
      await regInput.fill('ZZ99ZZZ');
      await postInput.fill('SW1A 1AA');
      await page.getByRole('button', { name: /check this car/i }).click();

      const banner = page.getByRole('alert');
      await expect(banner).toBeVisible();
      await expect(banner).toHaveText(mapErrorToMessage('dvsa_unavailable'));
      // The two error codes must not collapse to the same copy.
      expect(mapErrorToMessage('dvsa_unavailable')).not.toBe(mapErrorToMessage('vehicle_not_found'));

      await expect(regInput).toHaveValue('ZZ99ZZZ');
      await expect(postInput).toHaveValue('SW1A 1AA');

      if (entryPath === '/') {
        await page.screenshot({ path: 'e2e-artifacts/form-preserved-after-503.png', fullPage: true });
      }
    });
  }

  // ---------------------------------------------------------------------
  // (3) The historical remount bug: HomePage used to be recreated as a new
  // component type on every App re-render (including the one triggered by
  // getPublicStats() resolving), so React unmounted+remounted HeroForm and
  // wiped the input. Reproduced here by gating /api/stats behind a manual
  // release, typing part of the registration, resolving stats mid-type,
  // then finishing typing -- the resolved-stats re-render must not touch
  // what's already in the field, and typing must keep working afterwards.
  // ---------------------------------------------------------------------
  test('registration input survives /api/stats resolving mid-type', async ({ page }) => {
    const resolveStats = await gateStats(page);
    await page.goto('/app');

    const regInput = registrationInput(page);
    await regInput.pressSequentially('AB12', { delay: 20 });
    await expect(regInput).toHaveValue('AB12');

    resolveStats();
    // Concrete proof the resolved stats actually flowed into a HomePage
    // re-render (rather than an arbitrary sleep) before we keep typing.
    await expect(page.getByText(`${DEFAULT_STATS.checks_this_month} vehicles checked this month`)).toBeVisible();

    await regInput.pressSequentially('CDE', { delay: 20 });
    await expect(regInput).toHaveValue('AB12CDE');
  });

  // ---------------------------------------------------------------------
  // (4) ?reg= pre-fills the field; once the user edits it, HeroForm's
  // userEdited edit-lock must keep their edit even across a failed submit
  // (a second potential wipe point, distinct from case (1)'s plain-typed
  // value).
  // ---------------------------------------------------------------------
  test('edited ?reg= value survives a failed submit', async ({ page }) => {
    await mockCreateReport(page, fixtureErrorEnvelopes.vehicle_not_found, 404);
    await page.goto('/?reg=AB12CDE');

    const regInput = registrationInput(page);
    const postInput = postcodeInput(page);
    await expect(regInput).toHaveValue('AB12CDE');

    await regInput.fill('DE45FGH');
    await postInput.fill('SW1A 1AA');
    await page.getByRole('button', { name: /check this car/i }).click();

    await expect(page.getByRole('alert')).toBeVisible();
    await expect(regInput).toHaveValue('DE45FGH');
  });

  // ---------------------------------------------------------------------
  // (5) The one path that's supposed to clear the form: a successful check
  // followed by "Check Another Vehicle" must land back on an EMPTY /app,
  // not carry anything over. Run against both entry paths per the wave
  // brief.
  // ---------------------------------------------------------------------
  for (const entryPath of ENTRY_PATHS) {
    test(`"Check Another Vehicle" resets to an empty form @ ${entryPath}`, async ({ page }) => {
      await forceVariant(page, 'control');
      await mockCreateReport(page, fixtureExactHigh, 200);
      await mockGetReport(page, fixtureExactHigh.report_token as string, fixtureExactHigh, 200);
      await page.goto(entryPath);

      await registrationInput(page).fill('AB12CDE');
      await postcodeInput(page).fill('SW1A 1AA');
      await page.getByRole('button', { name: /check this car/i }).click();

      await expect(page).toHaveURL(new RegExp(`/app/report/${fixtureExactHigh.report_token}$`));

      await page.getByRole('button', { name: /check another vehicle/i }).first().click();

      await expect(page).toHaveURL(/\/app$/);
      await expect(page.getByText('Fix it before they find it.')).toBeVisible();
      await expect(registrationInput(page)).toHaveValue('');
      await expect(postcodeInput(page)).toHaveValue('');
    });
  }
});
