/**
 * Locators for HeroForm's two fields (components/HeroForm.tsx).
 *
 * Deliberately NOT page.getByLabel(...): HeroForm.tsx passes the
 * registration Input both a visible `label` ("Registration Number") *and*
 * an `aria-label` ("Enter registration for MOT history check"). Per the
 * ARIA accessible-name-computation algorithm, aria-label on a form control
 * takes precedence over its associated <label> element -- so the field's
 * real accessible name is "Enter registration for MOT history check", not
 * "Registration Number". Playwright's getByLabel resolves the true
 * accessible name (confirmed empirically: it timed out searching for
 * "Registration Number" against a page that had genuinely finished
 * rendering the form -- see the Wave 5 run report), so it cannot find this
 * field by its visible label text.
 *
 * This is a real, if minor, WCAG 2.5.3 "Label in Name" mismatch in the app
 * (a screen-reader user hears a different field name than what a sighted
 * user reads) -- flagged as a FINDING in the Wave 5 run report and
 * e2e/README.md, not fixed here (this wave doesn't own component code).
 * @testing-library/dom's getByLabelText (used by the existing vitest
 * suite, e.g. App.test.tsx) has different semantics -- it matches by
 * <label> element text structurally and does not consult aria-label at
 * all -- which is why the equivalent vitest assertions using
 * 'Registration Number' pass there while the Playwright equivalent does
 * not.
 *
 * Locating by id sidesteps the accessible-name discrepancy entirely and is
 * exactly the stable contract HeroForm.tsx itself assigns
 * (`<Input id="registration" .../>` / `<Input id="postcode" .../>`); the
 * postcode field has no such aria-label override and would resolve fine
 * via getByLabel too, but both fields use the same id-based strategy here
 * for consistency.
 */
import type { Locator, Page } from '@playwright/test';

export function registrationInput(page: Page): Locator {
  return page.locator('#registration');
}

export function postcodeInput(page: Page): Locator {
  return page.locator('#postcode');
}
