/**
 * Blocks every request that isn't same-origin with the preview server.
 *
 * index.html references Google Fonts, and conditionally loads Umami only on
 * non-report routes plus gtag only after consent. This suite declines consent,
 * but still blocks every external host as a defense-in-depth determinism
 * boundary. share-flow.spec.ts's WhatsApp share button opens a real
 * `wa.me` popup too. None of these are mocked by mockApi.ts (they're not
 * app `/api/*` calls), and the suite's determinism invariant rules out any
 * reliance on real network reachability (CI runners are not guaranteed to
 * have it, and even when they do, an external host being slow/down, or
 * -- as observed for wa.me, which redirects to api.whatsapp.com when the
 * request is genuinely allowed through -- behaving differently than
 * expected, must never make this suite flaky).
 *
 * Routes at the BrowserContext level, not the Page level: window.open()
 * (the WhatsApp share button) creates a brand-new Page that a page-scoped
 * route would never see, letting its navigation reach the real network
 * unmocked. context.route() applies to every page in the context,
 * including ones opened after this call, such as that popup.
 *
 * Same-origin requests (the app bundle, the mocked /api/* calls) are left
 * alone -- this only matches non-localhost hostnames, so it never competes
 * with mockApi.ts's route handlers.
 */
import type { Page } from '@playwright/test';

const ALLOWED_HOSTNAMES = new Set(['localhost', '127.0.0.1']);

export async function blockExternalRequests(page: Page): Promise<void> {
  await page.context().route(
    (url) => !ALLOWED_HOSTNAMES.has(url.hostname),
    async (route) => {
      await route.abort();
    }
  );
}
