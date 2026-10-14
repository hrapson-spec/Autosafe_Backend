/**
 * OA-003 browser evidence for the bearer report route (/app/report/:token):
 *
 *  - no report token (or registration / postcode) in any request to a
 *    non-same-origin host -- URL, POST body, or Referer -- with consent
 *    accepted AND declined, for a home -> report client transition and for
 *    a directly opened (restored) link;
 *  - the token is never in document.title;
 *  - the homepage canonical / description / social tags / JSON-LD inherited
 *    from the shared shell are gone while the report is mounted, the report
 *    asserts robots noindex,nofollow, and going back home restores them;
 *  - same-origin outbound navigation from the report carries no Referer
 *    once the document is served with `Referrer-Policy: no-referrer`.
 *
 * The third-party hosts are stubbed with scripts that behave like the real
 * Umami / gtag libraries at their most indiscreet (full location.href,
 * document.referrer and document.title in every hit), so a regression in
 * index.html's suppression or analytics.ts would show up as a recorded
 * request. Without stubs the blocked hosts would never define
 * window.umami / the gtag pipeline and the check would pass vacuously. A
 * control assertion proves the stubs are live on the homepage.
 *
 * Header limitation: this suite runs against `vite preview` (no FastAPI),
 * so the response headers are emulated for the Referer test by adding the
 * exact `Referrer-Policy: no-referrer` header to the /app/report/* document
 * response. The real headers and the server-rendered report HTML are
 * verified by tests/test_report_protection.py and the local uvicorn smoke
 * in docs/acquisition/OA-003_EVIDENCE.md, not here.
 */
import type { Page, Request } from '@playwright/test';
import { test, expect } from './helpers/setup';
import { seedConsentChoice } from './helpers/consent';
import { mockCreateReport, mockGetReport } from './helpers/mockApi';
import { registrationInput, postcodeInput } from './helpers/heroForm';
import { fixtureExactHigh } from '../fixtures/reportResponses';

const TOKEN = fixtureExactHigh.report_token as string;
const REGISTRATION = 'AB12CDE';
const POSTCODE = 'SW1A 1AA';

const UMAMI_HOST = 'https://umami-production-cb51.up.railway.app';
const GTAG_URL = 'https://www.googletagmanager.com/gtag/js**';

const UMAMI_STUB = `
(function () {
  var s = document.currentScript;
  var beforeSend = s && s.getAttribute('data-before-send');
  function send(type, payload) {
    var f = beforeSend && window[beforeSend];
    if (f) { payload = f(type, payload); if (payload === false || !payload) return; }
    fetch('${UMAMI_HOST}/api/send', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: type, payload: payload })
    }).catch(function () {});
  }
  window.umami = { track: function (a, b) {
    var base = { website: 'stub', hostname: location.hostname, url: location.href,
                 referrer: document.referrer, title: document.title };
    if (typeof a === 'function') send('event', a(base));
    else if (typeof a === 'string') send('event', Object.assign({}, base, { name: a, data: b }));
    else send('event', base);
  } };
})();`;

const GTAG_STUB = `
(function () {
  window.dataLayer = window.dataLayer || [];
  var orig = window.dataLayer.push;
  window.dataLayer.push = function () {
    var r = orig.apply(this, arguments);
    try {
      var a = arguments[0];
      if (a && a[0] === 'event') {
        new Image().src = 'https://www.google-analytics.com/g/collect?dl=' + encodeURIComponent(location.href)
          + '&dr=' + encodeURIComponent(document.referrer) + '&dt=' + encodeURIComponent(document.title);
      }
    } catch (e) {}
    return r;
  };
})();`;

interface Seen {
  url: string;
  method: string;
  postData: string | null;
  referer: string | undefined;
  documentUrlAtRequest: string;
}

async function stubThirdParties(page: Page): Promise<void> {
  // Page-level routes take precedence over the fixture's context-level abort.
  await page.route(`${UMAMI_HOST}/script.js`, (route) =>
    route.fulfill({ status: 200, contentType: 'application/javascript', body: UMAMI_STUB })
  );
  await page.route(GTAG_URL, (route) =>
    route.fulfill({ status: 200, contentType: 'application/javascript', body: GTAG_STUB })
  );
}

function recordRequests(page: Page): Seen[] {
  const seen: Seen[] = [];
  page.on('request', (req: Request) => {
    seen.push({
      url: req.url(),
      method: req.method(),
      postData: req.postData(),
      referer: req.headers()['referer'],
      documentUrlAtRequest: page.url(),
    });
  });
  return seen;
}

function thirdParty(seen: Seen[], baseURL: string): Seen[] {
  const origin = new URL(baseURL).origin;
  return seen.filter((s) => new URL(s.url).origin !== origin);
}

function expectNoSecretsIn(requests: Seen[]): void {
  for (const r of requests) {
    for (const secret of [TOKEN, REGISTRATION, POSTCODE, 'SW1A', encodeURIComponent(POSTCODE)]) {
      expect(r.url, `url of ${r.method} ${r.url}`).not.toContain(secret);
      expect(r.postData ?? '', `body of ${r.method} ${r.url}`).not.toContain(secret);
      expect(r.referer ?? '', `referer of ${r.method} ${r.url}`).not.toContain(secret);
    }
  }
}

async function headState(page: Page) {
  return page.evaluate(() => {
    const q = (sel: string) => document.head.querySelectorAll(sel).length;
    return {
      title: document.title,
      canonical: q('link[rel~="canonical"]'),
      ldJson: q('script[type="application/ld+json"]'),
      description: q('meta[name="description"]'),
      og: q('meta[property^="og:"]'),
      twitter: q('meta[name^="twitter:"]'),
      robots: Array.from(document.head.querySelectorAll('meta[name="robots"]')).map(
        (m) => m.getAttribute('content')
      ),
    };
  });
}

/** Opens the homepage, submits, lands on the report; returns the homepage head state seen before leaving. */
async function openReportViaTransition(page: Page) {
  await mockCreateReport(page, fixtureExactHigh, 200);
  await mockGetReport(page, TOKEN, fixtureExactHigh, 200);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Fix it before they find it.' })).toBeVisible();
  // Helmet commits head tags asynchronously after the visible React tree.
  // Capture the settled homepage, including its owned canonical, so a fast
  // first render is not compared with a fully committed return navigation.
  await expect(page.locator('head link[rel="canonical"][data-rh]')).toHaveCount(1);
  const homeBefore = await headState(page);
  await registrationInput(page).fill(REGISTRATION);
  await postcodeInput(page).fill(POSTCODE);
  await page.getByRole('button', { name: /check this car/i }).click();
  await expect(page).toHaveURL(new RegExp(`/app/report/${TOKEN}$`));
  await expect(page.getByTestId('comparison-result')).toBeVisible();
  return homeBefore;
}

async function openReportDirect(page: Page, prefix = '/app/report'): Promise<void> {
  await mockGetReport(page, TOKEN, fixtureExactHigh, 200);
  await page.goto(`${prefix}/${TOKEN}`);
  await expect(page.getByTestId('comparison-result')).toBeVisible();
}

for (const consent of ['accepted', 'declined'] as const) {
  test.describe(`consent ${consent}`, () => {
    test.beforeEach(async ({ page }) => {
      await seedConsentChoice(page, consent);
      await stubThirdParties(page);
    });

    test('home -> report transition: no token to third parties, title clean, homepage metadata neutralised and restored', async ({
      page,
      baseURL,
    }) => {
      const seen = recordRequests(page);
      const homeBefore = await openReportViaTransition(page);
      // the shell really carried homepage metadata to neutralise
      expect(homeBefore.canonical).toBeGreaterThanOrEqual(1);
      expect(homeBefore.ldJson).toBe(2);
      await page.waitForLoadState('networkidle');

      // Automatic Umami has been retired: neither its script nor events
      // may load on the homepage or the report, regardless of Ads consent.
      expect(seen.filter((s) => s.url.startsWith(UMAMI_HOST))).toHaveLength(0);
      // Consent accepted additionally loads (stubbed) gtag on the homepage.
      const gtagLoaded = seen.some((s) => s.url.startsWith('https://www.googletagmanager.com/gtag/js'));
      expect(gtagLoaded).toBe(consent === 'accepted');

      expectNoSecretsIn(thirdParty(seen, baseURL as string));

      const onReport = await headState(page);
      expect(onReport.title).not.toContain(TOKEN);
      expect(onReport.title).toBe('Your Vehicle Report | AutoSafe');
      expect(onReport).toMatchObject({ canonical: 0, ldJson: 0, description: 0, og: 0, twitter: 0 });
      expect(onReport.robots).toEqual(['noindex, nofollow']);

      // Back to the homepage: inherited metadata is intact again.
      await page.goBack();
      await expect(page.getByRole('heading', { name: 'Fix it before they find it.' })).toBeVisible();
      await expect.poll(() => headState(page)).toEqual(homeBefore);
      const home = await headState(page);
      expect(home).toEqual(homeBefore);
      expect(home.robots).toEqual([]);
      expect(home.title).not.toContain(TOKEN);
    });

    test('restored link (direct load): no third-party traffic carries the token, no analytics loaded, metadata neutral', async ({
      page,
      baseURL,
    }) => {
      const seen = recordRequests(page);
      await openReportDirect(page);
      await page.waitForLoadState('networkidle');

      expectNoSecretsIn(thirdParty(seen, baseURL as string));
      // index.html's report-route suppression: no Umami/gtag script is even requested.
      expect(seen.filter((s) => s.url.startsWith(UMAMI_HOST))).toHaveLength(0);
      expect(seen.filter((s) => s.url.startsWith('https://www.googletagmanager.com'))).toHaveLength(0);
      expect(seen.filter((s) => s.url.startsWith('https://www.google-analytics.com'))).toHaveLength(0);

      const state = await headState(page);
      expect(state.title).not.toContain(TOKEN);
      expect(state).toMatchObject({ canonical: 0, ldJson: 0, description: 0, og: 0, twitter: 0 });
      expect(state.robots).toEqual(['noindex, nofollow']);
    });

    // react-router matches routes case-insensitively, so /app/Report/<token>
    // renders the report. Every suppression gate must treat it as a report
    // route (index.html inline script, analytics.ts, metadata hook).
    for (const prefix of ['/app/Report', '/app/REPORT']) {
      test(`mixed-case direct load ${prefix}/<token>: no analytics loaded, no token to third parties, metadata neutral`, async ({
        page,
        baseURL,
      }) => {
        const seen = recordRequests(page);
        await openReportDirect(page, prefix);
        await page.waitForLoadState('networkidle');

        expect(page.url()).toContain(`${prefix}/${TOKEN}`);
        expectNoSecretsIn(thirdParty(seen, baseURL as string));
        expect(seen.filter((s) => s.url.startsWith(UMAMI_HOST))).toHaveLength(0);
        expect(seen.filter((s) => s.url.startsWith('https://www.googletagmanager.com'))).toHaveLength(0);
        expect(seen.filter((s) => s.url.startsWith('https://www.google-analytics.com'))).toHaveLength(0);

        const state = await headState(page);
        expect(state.title).not.toContain(TOKEN);
        expect(state).toMatchObject({ canonical: 0, ldJson: 0, description: 0, og: 0, twitter: 0 });
        expect(state.robots).toEqual(['noindex, nofollow']);
      });
    }
  });
}

test.describe('same-origin outbound navigation from the report', () => {
  const REPORT_URL_RE = new RegExp(`/app/report/${TOKEN}$`);

  async function clickHomeLinkAndGetReferer(page: Page): Promise<string | undefined> {
    const navigation = page.waitForRequest(
      (req) => req.isNavigationRequest() && new URL(req.url()).pathname === '/'
    );
    await page.getByRole('link', { name: 'AutoSafe home' }).click();
    return (await navigation).headers()['referer'];
  }

  test('with Referrer-Policy: no-referrer on the report document, no Referer is sent', async ({ page }) => {
    // Emulates the FastAPI response header (see header comment).
    await page.route('**/app/report/**', async (route) => {
      if (route.request().resourceType() !== 'document') return route.fallback();
      const response = await route.fetch();
      await route.fulfill({ response, headers: { ...response.headers(), 'referrer-policy': 'no-referrer' } });
    });
    await openReportDirect(page);
    await expect(page).toHaveURL(REPORT_URL_RE);

    expect(await clickHomeLinkAndGetReferer(page)).toBeUndefined();
  });

  test('control: without the header the browser would send the full report URL as Referer', async ({ page }) => {
    await openReportDirect(page);
    const referer = await clickHomeLinkAndGetReferer(page);
    expect(referer).toBeDefined();
    expect(referer).toContain(TOKEN);
  });
});
