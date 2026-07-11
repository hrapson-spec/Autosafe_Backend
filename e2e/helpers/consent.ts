/**
 * Cookie/consent banner handling for the E2E suite.
 *
 * The banner is injected by an inline bootstrap `<script>` in the vite
 * SOURCE template (root index.html, ~line 52-99) -- it runs before React
 * mounts, reads `localStorage.getItem('autosafe_consent')`, and appends a
 * `#consent-banner` div to <body> unless the stored value is already
 * 'accepted' or 'declined'. There is no frontend module/constant for this
 * key (it's plain inline JS, not part of the React app), so it was found by
 * reading index.html directly rather than grepping components/services --
 * flagged in the run report per the wave brief's instruction to flag what
 * was found here.
 *
 * Every spec pre-seeds 'declined' (never 'accepted') via seedConsent below:
 *   - it suppresses the banner exactly as well as 'accepted' would, and
 *   - 'accepted' would additionally call autosafeLoadGtag(), which appends
 *     a real <script src="https://www.googletagmanager.com/gtag/js..."> tag
 *     -- a genuine external network dependency this fully-mocked, offline
 *     suite must never trigger (see network.ts's blockExternalRequests,
 *     which would abort that request anyway, but not requesting it at all
 *     is cleaner and faster).
 */
import type { Page } from '@playwright/test';

/** localStorage key the consent banner reads/writes (root index.html). */
export const CONSENT_STORAGE_KEY = 'autosafe_consent';

/**
 * Pre-seeds the consent choice as 'declined' before any page script runs.
 * Must use addInitScript (runs before every document's scripts, including
 * the very first navigation) rather than page.evaluate after goto -- the
 * banner-injecting script runs at DOMContentLoaded/immediately, so a
 * post-navigation evaluate would lose the race and the banner would already
 * be in the DOM.
 */
export async function seedConsent(page: Page): Promise<void> {
  await page.addInitScript((key: string) => {
    window.localStorage.setItem(key, 'declined');
  }, CONSENT_STORAGE_KEY);
}
