/**
 * Shared per-test bootstrap. Specs import `test`/`expect` from here instead
 * of directly from '@playwright/test' and get, automatically, before every
 * test body runs:
 *
 *   1. Consent pre-seeded as 'declined' (consent.ts) -- the cookie banner
 *      never renders, so no spec has to find-and-dismiss it.
 *   2. All non-same-origin requests aborted (network.ts) -- determinism
 *      invariant, nothing here depends on outbound network reachability.
 *   3. GET /api/stats mocked with a default fixture (mockApi.ts) -- every
 *      page that renders HomePage calls getPublicStats() on mount. Specs
 *      that need different stats behaviour (e2e/helpers/mockApi.ts's
 *      gateStats) re-route it themselves inside the test body; Playwright
 *      runs the most-recently-registered matching handler first, so that
 *      re-registration overrides this default without needing to unroute.
 *
 * This is the "shared beforeEach" the wave brief asks for, implemented as
 * an auto-fixture (`{ auto: true }`) rather than a literal
 * `test.beforeEach` repeated in every spec file -- every spec gets it just
 * by importing from here, with nothing to remember to call. The one
 * exception is share-flow.spec.ts's fresh-context case, which deliberately
 * opens a second, unrelated BrowserContext (simulating a different device)
 * and wires consent/network/mocks onto it by hand -- this fixture only
 * instruments the default `page`/`context` Playwright Test provides.
 */
import { test as base, expect } from '@playwright/test';
import { seedConsent } from './consent';
import { blockExternalRequests } from './network';
import { mockStats } from './mockApi';

export const test = base.extend<{ forEachTest: void }>({
  forEachTest: [
    async ({ page }, use) => {
      await seedConsent(page);
      await blockExternalRequests(page);
      await mockStats(page);
      await use();
    },
    { auto: true },
  ],
});

export { expect };
