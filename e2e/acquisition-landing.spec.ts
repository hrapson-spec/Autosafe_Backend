/**
 * OA-005 / D-007.1: the landing handoff travels in the URL FRAGMENT and is
 * consumed by the first inline <head> script in index.html, before gtag or
 * Umami can run. With the collector OFF the app never talks to the first-party
 * collector, even through a full journey. These specs also run with Ads
 * consent ACCEPTED (so gtag and Umami really are injected) and capture every
 * request to a non-same-origin host to prove no landing id or source value
 * reaches a third party.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { test, expect } from './helpers/setup';
import { CONSENT_STORAGE_KEY } from './helpers/consent';
import { mockCreateReport, mockGetReport } from './helpers/mockApi';
import { registrationInput, postcodeInput } from './helpers/heroForm';
import { fixtureExactHigh } from '../fixtures/reportResponses';

const AL = '5b1d7a52-5d5c-4f55-9a52-3c6e1f1c8a11';
const SRC = 'google_organic';

test.describe('acquisition landing handoff (collection OFF)', () => {
  for (const path of ['/app', '/']) {
    test(`#al/#src are removed from ${path} and nothing is sent to the collector`, async ({ page }) => {
      const collectorRequests: string[] = [];
      page.on('request', (req) => {
        if (req.url().includes('/api/acquisition')) collectorRequests.push(req.url());
      });
      await page.goto(`${path}?keep=1#al=${AL}&src=${SRC}`);
      await expect(registrationInput(page)).toBeVisible();
      expect(new URL(page.url()).hash).toBe('');
      expect(new URL(page.url()).search).toBe('?keep=1');
      expect(page.url()).not.toContain(AL);
      expect(collectorRequests).toEqual([]);
    });
  }

  test('a full check journey sends no collector request while the flag is off', async ({ page }) => {
    const collectorRequests: string[] = [];
    page.on('request', (req) => {
      if (req.url().includes('/api/acquisition')) collectorRequests.push(req.url());
    });
    await mockCreateReport(page, fixtureExactHigh, 200);
    await mockGetReport(page, fixtureExactHigh.report_token as string, fixtureExactHigh, 200);
    await page.goto(`/app#al=${AL}&src=${SRC}`);
    await registrationInput(page).fill('AB12CDE');
    await postcodeInput(page).fill('SW1A 1AA');
    await page.getByRole('button', { name: /check this car/i }).click();
    await expect(page.getByTestId('comparison-result')).toBeVisible();
    expect(collectorRequests).toEqual([]);
    expect(page.url()).not.toContain(AL);
  });
});

test.describe('D-007.1: third parties never see the handoff (Ads consent ACCEPTED)', () => {
  type ExternalRequest = { url: string; headers: Record<string, string>; body: string | null };

  async function instrument(page: import('@playwright/test').Page) {
    // Declared AFTER setup.ts's auto-seed of 'declined', so this wins: consent is accepted
    // and the inline bootstrap really injects gtag.js and the Umami script.
    await page.addInitScript((key: string) => {
      window.localStorage.setItem(key, 'accepted');
      // Record the address-bar state at the moment any external <script> is attached.
      const w = window as unknown as { __externalScriptAppends: Array<{ src: string; hash: string; href: string }> };
      w.__externalScriptAppends = [];
      const original = Node.prototype.appendChild;
      Node.prototype.appendChild = function <T extends Node>(this: Node, child: T): T {
        if (child instanceof HTMLScriptElement && child.src && !child.src.startsWith(window.location.origin)) {
          w.__externalScriptAppends.push({ src: child.src, hash: window.location.hash, href: window.location.href });
        }
        return original.call(this, child) as T;
      } as typeof Node.prototype.appendChild;
    }, CONSENT_STORAGE_KEY);
    const external: ExternalRequest[] = [];
    page.on('request', (req) => {
      const host = new URL(req.url()).hostname;
      if (host !== 'localhost' && host !== '127.0.0.1') {
        external.push({ url: req.url(), headers: req.headers(), body: req.postData() });
      }
    });
    return external;
  }

  test('fragment is gone before any third-party script is attached; no request, body, Referer or dataLayer carries the id or source', async ({ page }) => {
    const external = await instrument(page);
    await mockCreateReport(page, fixtureExactHigh, 200);
    await mockGetReport(page, fixtureExactHigh.report_token as string, fixtureExactHigh, 200);

    await page.goto(`/app#al=${AL}&src=${SRC}`);
    await registrationInput(page).fill('AB12CDE');
    await postcodeInput(page).fill('SW1A 1AA');
    await page.getByRole('button', { name: /check this car/i }).click();
    await expect(page.getByTestId('comparison-result')).toBeVisible();

    // Gtag and Umami were really attempted (otherwise this test proves nothing).
    const appends = await page.evaluate(
      () => (window as unknown as { __externalScriptAppends: Array<{ src: string; hash: string; href: string }> }).__externalScriptAppends,
    );
    expect(appends.some((a) => a.src.includes('googletagmanager.com'))).toBe(true);
    expect(appends.some((a) => a.src.includes('umami'))).toBe(true);
    expect(external.length).toBeGreaterThan(0);

    // The fragment was already gone from location when each external script was attached.
    for (const a of appends) {
      expect(a.hash, a.src).toBe('');
      expect(a.href, a.src).not.toContain(AL);
      expect(a.href, a.src).not.toContain('al=');
    }
    // No third-party request carries the id or the source value anywhere.
    for (const r of external) {
      const everything = JSON.stringify([r.url, r.body, r.headers]);
      expect(everything, r.url).not.toContain(AL);
      expect(everything, r.url).not.toContain(SRC);
      expect(everything, r.url).not.toMatch(/[#?&]al=/);
      expect(r.headers.referer ?? '', r.url).not.toContain(AL);
    }
    // gtag's own queue (what it would send) holds neither.
    const dataLayer = await page.evaluate(() => JSON.stringify((window as unknown as { dataLayer?: unknown[] }).dataLayer ?? []));
    expect(dataLayer).not.toContain(AL);
    expect(dataLayer).not.toContain(SRC);
    expect(dataLayer).toContain('conversion'); // the journey really reached the conversion call
    expect(page.url()).not.toContain(AL);
  });

  test('a fragment on a bearer report route is consumed too', async ({ page }) => {
    const external = await instrument(page);
    await mockGetReport(page, fixtureExactHigh.report_token as string, fixtureExactHigh, 200);
    await page.goto(`/app/report/${fixtureExactHigh.report_token}#al=${AL}&src=${SRC}`);
    await expect(page.getByTestId('comparison-result')).toBeVisible();
    expect(new URL(page.url()).hash).toBe('');
    for (const r of external) expect(JSON.stringify(r)).not.toContain(AL);
  });
});

test.describe('D-007.1: the SEO registration form hands off through the fragment', () => {
  test('location.assign(/app + fragment) keeps the fragment across the navigation and the app consumes it', async ({ page }) => {
    // Use the real inline submit handler from the server-rendered template.
    const template = readFileSync(resolve(process.cwd(), 'templates/seo_base.html'), 'utf8');
    const handler = [...template.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]).find((s) => s.includes('reg-lookup-form'));
    expect(handler).toBeTruthy();
    await page.route('**/seo-probe', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: `<!doctype html><html><body>
          <form class="reg-lookup-form"><input data-registration value="ab12 cde"><button>Go</button></form>
          <script>${handler}</script></body></html>`,
      }),
    );
    const navigated: string[] = [];
    page.on('framenavigated', (frame) => {
      if (frame === page.mainFrame()) navigated.push(frame.url());
    });
    await page.goto('/seo-probe');
    // What static/acquisition-landing.js leaves for the form when collection is enabled.
    await page.evaluate((fragment) => {
      (window as unknown as { autosafeLandingHandoff: string }).autosafeLandingHandoff = fragment;
    }, `#al=${AL}&src=${SRC}`);
    await page.getByRole('button', { name: 'Go' }).click();
    await page.waitForURL(/\/app(#.*)?$/);
    await expect(registrationInput(page)).toBeVisible();
    // The browser kept the fragment on the navigation commit...
    expect(navigated.some((u) => u.endsWith(`/app#al=${AL}&src=${SRC}`))).toBe(true);
    // ...and index.html's head script consumed it.
    expect(new URL(page.url()).hash).toBe('');
    expect(page.url()).not.toContain(AL);
  });
});
