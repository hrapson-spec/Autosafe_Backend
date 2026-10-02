/**
 * OA-005: SPA landing attribution (utils/acquisitionLanding.ts) and the
 * ordering guarantee that the `al`/`src` handoff is stripped before any
 * analytics page view can fire.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import indexHtml from '../index.html?raw';
import {
  __resetAcquisitionStateForTests,
  __setAcquisitionSinkForTests,
  getAcquisitionContext,
  type AcquisitionEvent,
} from './acquisitionEvents';
import {
  classifyReferrer,
  classifySource,
  hasPaidSearchMarker,
  initAcquisitionLanding,
  isFreshNavigation,
  parseHandoff,
  spaLandingFamily,
} from './acquisitionLanding';

const AL = '5b1d7a52-5d5c-4f55-9a52-3c6e1f1c8a11';
const HOST = 'www.autosafe.one';

let events: AcquisitionEvent[] = [];

function goto(url: string, state: unknown = null) {
  window.history.replaceState(state, '', url);
}

function setReferrer(value: string) {
  Object.defineProperty(document, 'referrer', { value, configurable: true });
}

beforeEach(() => {
  __resetAcquisitionStateForTests();
  events = [];
  __setAcquisitionSinkForTests({ emit: (e) => events.push(e) });
  setReferrer('');
  goto('/');
});

afterEach(() => {
  __resetAcquisitionStateForTests();
  // @ts-expect-error cleanup of the test-defined property
  delete window.navigator.globalPrivacyControl;
  vi.resetModules();
  vi.restoreAllMocks();
});

describe('classifyReferrer uses the origin only', () => {
  it.each([
    ['', 'direct'],
    ['https://www.google.com/', 'google_organic'],
    ['https://www.google.com/search?q=will+my+car+pass+mot+AB12CDE', 'google_organic'],
    ['https://google.co.uk/', 'google_organic'],
    ['https://www.google.de/', 'google_organic'],
    ['https://www.google.com.au/', 'google_organic'],
    ['https://www.bing.com/', 'other_search'],
    ['https://duckduckgo.com/', 'other_search'],
    ['https://uk.search.yahoo.com/', 'other_search'],
    ['https://www.ecosia.org/', 'other_search'],
    ['https://search.brave.com/', 'other_search'],
    ['https://www.autosafe.one/guides/mot-cost', 'internal'],
    ['https://autosafe.one/', 'internal'],
    ['https://mail.google.com/', 'referral'],
    ['https://docs.google.com/', 'referral'],
    ['https://www.facebook.com/', 'referral'],
    ['https://t.co/abc', 'referral'],
    ['https://notgoogle.com/', 'referral'],
    ['https://google.com.evil.example/', 'referral'],
    ['not a url', 'unknown'],
  ])('%s -> %s', (referrer, expected) => {
    expect(classifyReferrer(referrer, HOST)).toBe(expected);
  });

  it('never returns anything derived from the referrer path or query', () => {
    const out = classifyReferrer('https://www.google.com/search?q=SECRET-QUERY', HOST);
    expect(out).not.toContain('SECRET');
  });
});

function setHandoff(al: unknown, src: unknown) {
  window.__autosafeLandingHandoff = { al, src };
}

describe('handoff parsing (values left by the inline head script)', () => {
  it('accepts exactly the shapes the public pages mint', () => {
    expect(parseHandoff({ al: AL, src: 'google_organic' })).toEqual({ landingId: AL, sourceGroup: 'google_organic' });
    expect(parseHandoff({ al: AL, src: 'internal' })).toEqual({ landingId: AL, sourceGroup: 'internal' });
  });

  it.each([
    ['uppercase uuid', { al: AL.toUpperCase(), src: 'direct' }],
    ['short id', { al: 'abc', src: 'direct' }],
    ['registration-shaped', { al: 'AB12CDE', src: 'direct' }],
    ['unknown source', { al: AL, src: 'evil' }],
    ['internal_test cannot be handed over', { al: AL, src: 'internal_test' }],
    ['missing src', { al: AL }],
    ['missing al', { src: 'direct' }],
    ['null values (param absent)', { al: null, src: null }],
    ['non-string', { al: 123, src: ['direct'] }],
    ['url as source', { al: AL, src: 'https://google.com' }],
    ['empty object', {}],
  ])('ignores %s', (_n, raw) => {
    expect(parseHandoff(raw)).toBeNull();
  });

  it('ignores undefined and null', () => {
    expect(parseHandoff(undefined)).toBeNull();
    expect(parseHandoff(null)).toBeNull();
  });
});

describe('spaLandingFamily', () => {
  it.each([
    ['/', 'home'], ['/app', 'app'], ['/app/', 'app'],
    ['/app/report/abc123', null], ['/app/report/unsaved', null], ['/app/privacy', null],
    ['/app/guides/mot-checklist', null], ['/anything', null],
  ])('%s -> %s', (path, family) => {
    expect(spaLandingFamily(path)).toBe(family);
  });
});

describe('initAcquisitionLanding', () => {
  it('handoff: validates, holds in memory, consumes the window variable, and does not emit (the public page already did)', () => {
    goto('/app');
    setHandoff(AL, 'google_organic');
    initAcquisitionLanding(true);
    expect(getAcquisitionContext()).toEqual({ landingId: AL, sourceGroup: 'google_organic', pageFamily: 'app' });
    expect(events).toEqual([]);
    expect(window.__autosafeLandingHandoff).toBeUndefined();
  });

  it('never reads the fragment or the al/src query itself: only the variable counts', () => {
    goto(`/app?al=${AL}&src=google_organic#al=${AL}&src=google_organic`);
    initAcquisitionLanding(true);
    expect(JSON.stringify(getAcquisitionContext())).not.toContain(AL);
    expect(events).toHaveLength(1); // an ordinary direct landing
    expect(window.location.search).toBe(`?al=${AL}&src=google_organic`); // untouched: no query handling any more
  });

  it('invalid values are ignored; the document becomes an ordinary SPA landing', () => {
    setReferrer('https://www.bing.com/');
    goto('/app');
    setHandoff('AB12CDE', 'evil');
    initAcquisitionLanding(true);
    expect(JSON.stringify(getAcquisitionContext())).not.toContain('AB12CDE');
    expect(window.__autosafeLandingHandoff).toBeUndefined();
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ event: 'landing_observed', page_family: 'app', source_group: 'other_search' });
  });

  it('direct SPA landing on / mints a landing id and emits one landing_observed with an allowlisted family', () => {
    setReferrer('https://www.google.com/search?q=mot');
    goto('/');
    initAcquisitionLanding(true);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      event: 'landing_observed', page_family: 'home', source_group: 'google_organic', observation_state: 'observed',
    });
    const ctx = getAcquisitionContext();
    expect(ctx.landingId).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect(ctx.pageFamily).toBe('app'); // later events happen on the app
    expect(JSON.stringify(events)).not.toContain('/search');
    expect(JSON.stringify(events)).not.toContain('google.com');
  });

  it('direct SPA landing on /app has page_family app and source direct when there is no referrer', () => {
    goto('/app');
    initAcquisitionLanding(true);
    expect(events[0]).toMatchObject({ page_family: 'app', source_group: 'direct' });
  });

  it.each(['/app/report/9c7f2b1a', '/app/report/unsaved', '/app/privacy', '/app/guides/mot-checklist'])(
    '%s is not a landing: nothing emitted, no landing id',
    (path) => {
      setReferrer('https://www.google.com/');
      goto(path);
      initAcquisitionLanding(true);
      expect(events).toEqual([]);
      expect(getAcquisitionContext()).toEqual({ sourceGroup: 'unknown', pageFamily: 'app' });
    },
  );

  it('a handoff on a report route is adopted in memory but nothing is emitted from a report route', () => {
    goto('/app/report/9c7f2b1a');
    setHandoff(AL, 'google_organic');
    initAcquisitionLanding(true);
    expect(events).toEqual([]);
  });

  it('disabled (the shipped state): consumes the variable and does nothing else', () => {
    setReferrer('https://www.google.com/');
    goto('/app');
    setHandoff(AL, 'google_organic');
    initAcquisitionLanding(false);
    expect(window.__autosafeLandingHandoff).toBeUndefined();
    expect(getAcquisitionContext()).toEqual({ sourceGroup: 'unknown', pageFamily: 'app' });
    expect(events).toEqual([]);
  });

  it('Global Privacy Control: consumes the variable, then measures nothing', () => {
    Object.defineProperty(window.navigator, 'globalPrivacyControl', { value: true, configurable: true });
    goto('/app');
    setHandoff(AL, 'direct');
    initAcquisitionLanding(true);
    expect(window.__autosafeLandingHandoff).toBeUndefined();
    expect(events).toEqual([]);
    expect(getAcquisitionContext().landingId).toBeUndefined();
  });
});

function navType(type: string | null) {
  // null = API returns no entry; 'throw' handled separately
  vi.spyOn(performance, 'getEntriesByType').mockReturnValue(
    (type === null ? [] : [{ type }]) as unknown as PerformanceEntryList,
  );
}

describe('hasPaidSearchMarker / classifySource (D-006)', () => {
  it.each([
    ['?gclid=abc', true],
    ['?GCLID=abc', true],
    ['?gclid=', true],
    ['?x=1&gbraid=z', true],
    ['?WBRAID=z', true],
    ['?utm_medium=cpc', true],
    ['?utm_medium=CPC', true],
    ['?UTM_MEDIUM=Ppc', true],
    ['?utm_medium=%20paid%20', true],
    ['?utm_medium=organic', false],
    ['?utm_medium=cpcx', false],
    ['?utm_source=cpc', false],
    ['?medium=cpc', false],
    ['?gclid_not=1', false],
    ['', false],
  ])('%s -> %s', (search, expected) => {
    expect(hasPaidSearchMarker(search)).toBe(expected);
  });

  it('a paid marker wins over the referrer classification', () => {
    expect(classifySource('https://www.google.com/', HOST, '?gclid=1')).toBe('paid_search');
    expect(classifySource('', HOST, '?utm_medium=cpc')).toBe('paid_search');
    expect(classifySource('https://www.google.com/', HOST, '?utm_medium=organic')).toBe('google_organic');
    expect(classifySource('https://www.google.com/', HOST, '')).toBe('google_organic');
  });

  it('handoff accepts src=paid_search', () => {
    expect(parseHandoff({ al: AL, src: 'paid_search' })).toEqual({ landingId: AL, sourceGroup: 'paid_search' });
  });
});

describe('isFreshNavigation (D-006)', () => {
  it.each([
    ['navigate', true],
    ['reload', false],
    ['back_forward', false],
    ['prerender', false],
  ])('%s -> %s', (type, expected) => {
    navType(type);
    expect(isFreshNavigation()).toBe(expected);
  });

  it('fails open when the API returns no entry or is unavailable', () => {
    navType(null);
    expect(isFreshNavigation()).toBe(true);
    vi.spyOn(performance, 'getEntriesByType').mockImplementation(() => {
      throw new Error('unsupported');
    });
    expect(isFreshNavigation()).toBe(true);
    vi.restoreAllMocks();
    // jsdom has no navigation entries: also fail open
    expect(isFreshNavigation()).toBe(true);
  });
});

describe('initAcquisitionLanding: reload and paid search (D-006)', () => {
  it.each(['reload', 'back_forward', 'prerender'])('%s is not a landing: no event, no landing id', (type) => {
    navType(type);
    setReferrer('https://www.google.com/');
    goto('/');
    initAcquisitionLanding(true);
    expect(events).toEqual([]);
    expect(getAcquisitionContext()).toEqual({ sourceGroup: 'unknown', pageFamily: 'app' });
  });

  it('navigate is a landing', () => {
    navType('navigate');
    setReferrer('https://www.google.com/');
    initAcquisitionLanding(true);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ source_group: 'google_organic' });
  });

  it('missing Navigation Timing entry still emits the landing (documented fail-open)', () => {
    navType(null);
    initAcquisitionLanding(true);
    expect(events).toHaveLength(1);
  });

  it.each([
    ['/app?gclid=SECRETGCLID123XYZ', 'SECRETGCLID123XYZ'],
    ['/?GBRAID=SECRETGBRAID456&keep=1', 'SECRETGBRAID456'],
    ['/app?wbraid=SECRETWBRAID789', 'SECRETWBRAID789'],
    ['/app?utm_medium=CPC&utm_campaign=SECRETCAMPAIGN', 'SECRETCAMPAIGN'],
  ])('%s -> paid_search, and no parameter or value is emitted', (url, secret) => {
    setReferrer('https://www.google.com/search?q=mot');
    goto(url);
    initAcquisitionLanding(true);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ event: 'landing_observed', source_group: 'paid_search' });
    const json = JSON.stringify([events, getAcquisitionContext()]);
    for (const needle of [secret, 'gclid', 'gbraid', 'wbraid', 'utm_', 'cpc', 'CPC']) {
      expect(json.toLowerCase()).not.toContain(needle.toLowerCase());
    }
    // the query is left exactly as the visitor arrived with it
    expect(window.location.search).toContain(url.split('?')[1].split('&')[0]);
  });

  it('a paid reload is still not a landing', () => {
    navType('reload');
    goto('/app?gclid=abc');
    initAcquisitionLanding(true);
    expect(events).toEqual([]);
  });
});

describe('ordering: the handoff is consumed before gtag, Umami and React (D-007.1)', () => {
  const lines = indexHtml.split('\n');
  const executingScripts = [...indexHtml.matchAll(/<script(?![^>]*type="application\/ld\+json")[^>]*>([\s\S]*?)<\/script>/g)];
  const headScripts = executingScripts.filter((m) => (m.index as number) < indexHtml.indexOf('</head>'));
  const handoffScript = headScripts[0][1];

  it('the first executing inline script in <head> is the handoff consumer, ahead of gtag and Umami', () => {
    expect(handoffScript).toContain('__autosafeLandingHandoff');
    expect(handoffScript).toContain('replaceState');
    for (const later of headScripts.slice(1)) expect(later[1]).not.toContain('__autosafeLandingHandoff');
    const handoffAt = indexHtml.indexOf('__autosafeLandingHandoff');
    expect(handoffAt).toBeGreaterThan(0);
    expect(handoffAt).toBeLessThan(indexHtml.indexOf('googletagmanager'));
    expect(handoffAt).toBeLessThan(indexHtml.indexOf('umami-production'));
    expect(handoffAt).toBeLessThan(indexHtml.indexOf("gtag('js'"));
    expect(lines.length).toBeGreaterThan(10);
  });

  function runHandoffScript() {
    new Function(handoffScript)();
  }

  it('moves #al=&src= into the window variable and removes it from the address bar', () => {
    goto(`/app?keep=1#al=${AL}&src=google_organic`, { key: 'k' });
    runHandoffScript();
    expect(window.location.hash).toBe('');
    expect(window.location.pathname + window.location.search).toBe('/app?keep=1');
    expect(window.history.state).toEqual({ key: 'k' });
    expect(window.__autosafeLandingHandoff).toEqual({ al: AL, src: 'google_organic' });
    initAcquisitionLanding(true);
    expect(getAcquisitionContext().landingId).toBe(AL);
    expect(window.__autosafeLandingHandoff).toBeUndefined();
  });

  it('keeps any other fragment content and ignores unrelated fragments', () => {
    goto(`/app#top&al=${AL}`);
    runHandoffScript();
    expect(window.location.hash).toBe('#top=');
    delete window.__autosafeLandingHandoff;
    goto('/app#section-2');
    runHandoffScript();
    expect(window.location.hash).toBe('#section-2');
    expect(window.__autosafeLandingHandoff).toBeUndefined();
  });

  it('is a no-op without a fragment', () => {
    goto('/app?x=1');
    const spy = vi.spyOn(window.history, 'replaceState');
    runHandoffScript();
    expect(spy).not.toHaveBeenCalled();
    expect(window.__autosafeLandingHandoff).toBeUndefined();
  });

  it('index.tsx reads no URL for the handoff: nothing there mentions the fragment, hash or al/src', async () => {
    const source = (await import('../index.tsx?raw')).default;
    expect(source).not.toMatch(/location\.hash|searchParams|URLSearchParams|'al'|"al"/);
  });

  it('the source order in index.tsx is: install transport, consume handoff, then render', async () => {
    const source = (await import('../index.tsx?raw')).default;
    const consume = source.indexOf('initAcquisitionLanding();');
    expect(source.indexOf('installAcquisitionTransport();')).toBeGreaterThan(-1);
    expect(consume).toBeGreaterThan(source.indexOf('installAcquisitionTransport();'));
    expect(source.indexOf('root.render(')).toBeGreaterThan(consume);
  });
});
