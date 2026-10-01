/**
 * OA-005: static/acquisition-landing.js, the same-origin script on public
 * server-rendered pages. The shipped file is OFF (`var ENABLED = false;`);
 * tests evaluate a copy with the flag flipped, in jsdom, with a fetch spy.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import scriptSource from '../static/acquisition-landing.js?raw';
import baseTemplate from '../templates/seo_base.html?raw';
import { ACQUISITION_METRIC_VERSION, ACQUISITION_ENDPOINT } from './acquisitionEvents';
import { classifyReferrer } from './acquisitionLanding';

const ENABLED_SOURCE = scriptSource.replace('var ENABLED = false;', 'var ENABLED = true;');
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function run(source: string) {
  new Function(source)();
}

function setReferrer(value: string) {
  Object.defineProperty(document, 'referrer', { value, configurable: true });
}

function goto(url: string) {
  window.history.replaceState(null, '', url);
}

let fetchSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchSpy = vi.fn(() => Promise.resolve({ status: 202 } as Response));
  vi.stubGlobal('fetch', fetchSpy);
  Object.defineProperty(window, 'fetch', { value: fetchSpy, configurable: true, writable: true });
  delete (window as unknown as { autosafeLandingQuery?: string }).autosafeLandingQuery;
  setReferrer('');
  goto('/guides/mot-cost');
  document.body.innerHTML = '';
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  // @ts-expect-error cleanup of the test-defined property
  delete window.navigator.globalPrivacyControl;
});

const CTA_HTML = `
  <a id="cta-app" href="/app">Enter a registration</a>
  <a id="cta-app-slash" href="/app/">slash</a>
  <a id="cta-abs" href="${window.location.origin}/app#top">absolute same-origin</a>
  <a id="cta-marked" href="/" data-acq-cta>marked home</a>
  <a id="logo" href="/">logo</a>
  <a id="with-query" href="/app?x=1">has query</a>
  <a id="external" href="https://example.com/app">external</a>
  <a id="other-origin-port" href="http://localhost:9999/app">other port</a>
  <a id="guide" href="/guides/mot-checklist">guide</a>
  <a id="report" href="/app/report/abc" data-acq-cta>report</a>
  <a id="mailto" href="mailto:autosafehq@gmail.com">mail</a>
  <a id="no-href">none</a>
`;

describe('shipped file is OFF', () => {
  it('declares ENABLED exactly once and as false', () => {
    expect(scriptSource.match(/var ENABLED = /g)).toHaveLength(1);
    expect(scriptSource).toContain('var ENABLED = false;');
  });

  it('does nothing when run as shipped: no request, no link change, no global', () => {
    setReferrer('https://www.google.com/');
    document.body.innerHTML = CTA_HTML;
    run(scriptSource);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(document.getElementById('cta-app')!.getAttribute('href')).toBe('/app');
    expect((window as unknown as { autosafeLandingQuery?: string }).autosafeLandingQuery).toBeUndefined();
  });
});

describe('enabled (test copy)', () => {
  it('emits one landing_observed with source from the referrer origin and an allowlisted family', () => {
    setReferrer('https://www.google.com/search?q=SECRET-QUERY&reg=AB12CDE');
    goto('/mot-check/ford/fiesta/?utm=x');
    run(ENABLED_SOURCE);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit & { body: string }];
    expect(url).toBe(ACQUISITION_ENDPOINT);
    expect(init).toMatchObject({
      method: 'POST', keepalive: true, credentials: 'omit', referrerPolicy: 'no-referrer', cache: 'no-store', mode: 'same-origin',
    });
    const wire = JSON.parse(init.body);
    expect(wire).toMatchObject({
      schema_version: 1,
      metric_version: ACQUISITION_METRIC_VERSION,
      event: 'landing_observed',
      page_family: 'model',
      source_group: 'google_organic',
      observation_state: 'observed',
    });
    for (const k of ['event_id', 'session_id', 'landing_id']) expect(wire[k]).toMatch(UUID);
    expect(new Set([wire.event_id, wire.session_id, wire.landing_id]).size).toBe(3);
    expect(Object.keys(wire).sort()).toEqual([
      'event', 'event_id', 'landing_id', 'metric_version', 'observation_state', 'page_family', 'schema_version',
      'session_id', 'source_group',
    ]);
    const text = init.body;
    for (const secret of ['SECRET', 'AB12CDE', 'utm', 'google.com', 'ford', 'fiesta', 'localhost']) {
      expect(text).not.toContain(secret);
    }
  });

  it('rewrites only same-origin /app links and marked CTA links, preserving the hash', () => {
    setReferrer('https://www.bing.com/');
    document.body.innerHTML = CTA_HTML;
    run(ENABLED_SOURCE);
    const wire = JSON.parse((fetchSpy.mock.calls[0][1] as { body: string }).body);
    const q = `?al=${wire.landing_id}&src=other_search`;
    const href = (id: string) => document.getElementById(id)!.getAttribute('href');
    expect(href('cta-app')).toBe(`/app${q}`);
    expect(href('cta-app-slash')).toBe(`/app/${q}`);
    expect(href('cta-abs')).toBe(`/app${q}#top`);
    expect(href('cta-marked')).toBe(`/${q}`);
    // untouched
    expect(href('logo')).toBe('/');
    expect(href('with-query')).toBe('/app?x=1');
    expect(href('external')).toBe('https://example.com/app');
    expect(href('other-origin-port')).toBe('http://localhost:9999/app');
    expect(href('guide')).toBe('/guides/mot-checklist');
    expect(href('report')).toBe('/app/report/abc');
    expect(href('mailto')).toBe('mailto:autosafehq@gmail.com');
    expect(document.getElementById('no-href')!.hasAttribute('href')).toBe(false);
    // only the two parameters, nothing else is appended
    expect(href('cta-app')!.replace(/^\/app/, '')).toMatch(/^\?al=[0-9a-f-]{36}&src=other_search$/);
  });

  it('exposes the same query for the registration form that navigates to /app itself', () => {
    document.body.innerHTML = CTA_HTML;
    run(ENABLED_SOURCE);
    const wire = JSON.parse((fetchSpy.mock.calls[0][1] as { body: string }).body);
    expect((window as unknown as { autosafeLandingQuery: string }).autosafeLandingQuery).toBe(
      `?al=${wire.landing_id}&src=direct`,
    );
    expect(baseTemplate).toContain("window.location.assign('/app' + (window.autosafeLandingQuery || ''));");
  });

  it('rewrites links that appear before DOMContentLoaded (script runs while parsing)', () => {
    Object.defineProperty(document, 'readyState', { value: 'loading', configurable: true });
    document.body.innerHTML = '<a id="late" href="/app">x</a>';
    run(ENABLED_SOURCE);
    expect(document.getElementById('late')!.getAttribute('href')).toBe('/app');
    Object.defineProperty(document, 'readyState', { value: 'complete', configurable: true });
    document.dispatchEvent(new Event('DOMContentLoaded'));
    expect(document.getElementById('late')!.getAttribute('href')).toMatch(/^\/app\?al=[0-9a-f-]{36}&src=direct$/);
  });

  it('never runs on a report route', () => {
    goto('/app/report/9c7f2b1a');
    document.body.innerHTML = CTA_HTML;
    run(ENABLED_SOURCE);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(document.getElementById('cta-app')!.getAttribute('href')).toBe('/app');
  });

  it('Global Privacy Control: sends nothing and leaves every link alone', () => {
    Object.defineProperty(window.navigator, 'globalPrivacyControl', { value: true, configurable: true });
    document.body.innerHTML = CTA_HTML;
    run(ENABLED_SOURCE);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(document.getElementById('cta-app')!.getAttribute('href')).toBe('/app');
  });

  it('measures nothing when no secure random source exists', () => {
    const original = window.crypto;
    Object.defineProperty(window, 'crypto', { value: undefined, configurable: true });
    try {
      document.body.innerHTML = CTA_HTML;
      run(ENABLED_SOURCE);
      expect(fetchSpy).not.toHaveBeenCalled();
      expect(document.getElementById('cta-app')!.getAttribute('href')).toBe('/app');
    } finally {
      Object.defineProperty(window, 'crypto', { value: original, configurable: true });
    }
  });

  it('retries once on a 5xx with the same body and never on a 4xx', async () => {
    vi.useFakeTimers();
    try {
      fetchSpy.mockImplementationOnce(() => Promise.resolve({ status: 503 } as Response));
      run(ENABLED_SOURCE);
      await vi.advanceTimersByTimeAsync(5_000);
      expect(fetchSpy).toHaveBeenCalledTimes(2);
      expect((fetchSpy.mock.calls[1][1] as { body: string }).body).toBe((fetchSpy.mock.calls[0][1] as { body: string }).body);

      fetchSpy.mockClear();
      fetchSpy.mockImplementation(() => Promise.resolve({ status: 429 } as Response));
      run(ENABLED_SOURCE);
      await vi.advanceTimersByTimeAsync(60_000);
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('touches no storage and sets no cookie', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    const getItem = vi.spyOn(Storage.prototype, 'getItem');
    const cookieSet = vi.spyOn(document, 'cookie', 'set');
    document.body.innerHTML = CTA_HTML;
    run(ENABLED_SOURCE);
    expect(setItem).not.toHaveBeenCalled();
    expect(getItem).not.toHaveBeenCalled();
    expect(cookieSet).not.toHaveBeenCalled();
  });
});

describe('source-group parity with the SPA classifier', () => {
  const referrers = [
    '', 'https://www.google.com/', 'https://www.google.co.uk/search?q=a', 'https://www.google.com.au/', 'https://mail.google.com/',
    'https://www.bing.com/', 'https://duckduckgo.com/', 'https://uk.search.yahoo.com/', 'https://www.ecosia.org/',
    'https://localhost/x', 'https://www.localhost/x', 'https://t.co/x', 'https://google.com.evil.example/', 'not a url',
  ];
  it.each(referrers)('referrer %j', (referrer) => {
    setReferrer(referrer);
    run(ENABLED_SOURCE);
    const wire = JSON.parse((fetchSpy.mock.calls[0][1] as { body: string }).body);
    expect(wire.source_group).toBe(classifyReferrer(referrer, window.location.hostname));
  });
});

describe('page family mapping covers every public route shape', () => {
  it.each([
    ['/guides/mot-cost', 'guide'],
    ['/static/guides/mot-cost.html', 'guide'],
    ['/mot-check/', 'other_public'],
    ['/mot-check/ford/', 'make'],
    ['/mot-check/ford/fiesta/', 'model'],
    ['/mot-check/ford/fiesta/2018/', 'model'],
    ['/mot-check/ford/fiesta/problems/brakes/', 'model'],
    ['/mot-check/problems/brakes/', 'problem_hub'],
    ['/mot-check/compare/ford-fiesta-vs-vauxhall-corsa/', 'comparison'],
    ['/will-my-car-pass-mot/', 'pillar'],
    ['/local-mot/leeds/', 'other_public'],
    ['/insights/march-mot-rush-2026/', 'other_public'],
    ['/privacy', 'other_public'],
  ])('%s -> %s', (path, family) => {
    goto(path);
    run(ENABLED_SOURCE);
    expect(JSON.parse((fetchSpy.mock.calls[0][1] as { body: string }).body).page_family).toBe(family);
  });
});
