/**
 * OA-005: SPA landing attribution (utils/acquisitionLanding.ts) and the
 * ordering guarantee that the `al`/`src` handoff is stripped before any
 * analytics page view can fire.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  __resetAcquisitionStateForTests,
  __setAcquisitionSinkForTests,
  getAcquisitionContext,
  type AcquisitionEvent,
} from './acquisitionEvents';
import {
  classifyReferrer,
  initAcquisitionLanding,
  parseHandoff,
  spaLandingFamily,
  stripHandoffParams,
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

describe('handoff parsing and stripping', () => {
  it('accepts exactly the shapes the public pages mint', () => {
    expect(parseHandoff(`?al=${AL}&src=google_organic`)).toEqual({ landingId: AL, sourceGroup: 'google_organic' });
    expect(parseHandoff(`?src=internal&al=${AL}`)).toEqual({ landingId: AL, sourceGroup: 'internal' });
  });

  it.each([
    ['uppercase uuid', `?al=${AL.toUpperCase()}&src=direct`],
    ['short id', '?al=abc&src=direct'],
    ['registration-shaped', '?al=AB12CDE&src=direct'],
    ['unknown source', `?al=${AL}&src=evil`],
    ['internal_test cannot be handed over', `?al=${AL}&src=internal_test`],
    ['missing src', `?al=${AL}`],
    ['missing al', '?src=direct'],
    ['empty', ''],
    ['url as source', `?al=${AL}&src=https://google.com`],
  ])('ignores %s', (_n, search) => {
    expect(parseHandoff(search)).toBeNull();
  });

  it('removes only al and src, keeping other params, the hash and history state', () => {
    goto(`/app?keep=1&al=${AL}&src=direct&z=2#frag`, { key: 'k' });
    expect(stripHandoffParams()).toBe(true);
    expect(window.location.pathname + window.location.search + window.location.hash).toBe('/app?keep=1&z=2#frag');
    expect(window.history.state).toEqual({ key: 'k' });
  });

  it('does nothing when neither parameter is present', () => {
    goto('/app?keep=1');
    const spy = vi.spyOn(window.history, 'replaceState');
    expect(stripHandoffParams()).toBe(false);
    expect(spy).not.toHaveBeenCalled();
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
  it('handoff: validates, holds in memory, strips the URL, and does not emit (the public page already did)', () => {
    goto(`/app?al=${AL}&src=google_organic`);
    initAcquisitionLanding(true);
    expect(window.location.search).toBe('');
    expect(getAcquisitionContext()).toEqual({ landingId: AL, sourceGroup: 'google_organic', pageFamily: 'app' });
    expect(events).toEqual([]);
  });

  it('invalid al/src are ignored (but still stripped); the document becomes an ordinary SPA landing', () => {
    setReferrer('https://www.bing.com/');
    goto('/app?al=AB12CDE&src=evil');
    initAcquisitionLanding(true);
    expect(window.location.search).toBe('');
    expect(JSON.stringify(getAcquisitionContext())).not.toContain('AB12CDE');
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

  it('a forged handoff on a report route is stripped and never adopted as a landing there', () => {
    goto(`/app/report/9c7f2b1a?al=${AL}&src=google_organic`);
    initAcquisitionLanding(true);
    expect(window.location.search).toBe('');
    // A valid pair is still adopted (it is a valid, random, non-secret id) but nothing is emitted from a report route.
    expect(events).toEqual([]);
  });

  it('disabled (the shipped state): strips the parameters and does nothing else', () => {
    setReferrer('https://www.google.com/');
    goto(`/app?al=${AL}&src=google_organic&keep=1`);
    initAcquisitionLanding(false);
    expect(window.location.search).toBe('?keep=1');
    expect(getAcquisitionContext()).toEqual({ sourceGroup: 'unknown', pageFamily: 'app' });
    expect(events).toEqual([]);
  });

  it('Global Privacy Control: strips, then measures nothing', () => {
    Object.defineProperty(window.navigator, 'globalPrivacyControl', { value: true, configurable: true });
    goto(`/app?al=${AL}&src=direct`);
    initAcquisitionLanding(true);
    expect(window.location.search).toBe('');
    expect(events).toEqual([]);
    expect(getAcquisitionContext().landingId).toBeUndefined();
  });
});

describe('ordering: the handoff is stripped before the first analytics page view', () => {
  it('index.tsx strips al/src before React renders, so the first page view sees a clean URL', async () => {
    const seen: string[] = [];
    vi.doMock('../App', () => ({
      default: function ProbeApp() {
        // App fires trackPageView from a mount effect; record the URL at that moment.
        React.useEffect(() => {
          seen.push(window.location.pathname + window.location.search);
        }, []);
        return null;
      },
    }));
    document.body.innerHTML = '<div id="root"></div>';
    goto(`/app?al=${AL}&src=google_organic`);
    await import('../index');
    // index.tsx renders under React.StrictMode, so the mount effect runs twice.
    await vi.waitFor(() => expect(seen.length).toBeGreaterThanOrEqual(1));
    expect(new Set(seen)).toEqual(new Set(['/app']));
  });

  it('the real analytics page view sends a path with no handoff parameters', async () => {
    const sent: string[] = [];
    (window as unknown as { umami: unknown }).umami = {
      track: (fn: (p: Record<string, unknown>) => Record<string, unknown>) => {
        sent.push(String(fn({}).url) + '|' + window.location.search);
      },
    };
    vi.doMock('../App', async () => {
      const { trackPageView } = await import('./analytics');
      return {
        default: function ProbeApp() {
          React.useEffect(() => {
            trackPageView();
          }, []);
          return null;
        },
      };
    });
    document.body.innerHTML = '<div id="root"></div>';
    goto(`/app?al=${AL}&src=google_organic`);
    await import('../index');
    await vi.waitFor(() => expect(sent.length).toBeGreaterThanOrEqual(1));
    expect(new Set(sent)).toEqual(new Set(['/app|']));
    delete (window as unknown as { umami?: unknown }).umami;
  });

  it('the source order in index.tsx is: install transport, strip handoff, then render', async () => {
    const source = (await import('../index.tsx?raw')).default;
    const strip = source.indexOf('initAcquisitionLanding();');
    expect(source.indexOf('installAcquisitionTransport();')).toBeGreaterThan(-1);
    expect(strip).toBeGreaterThan(source.indexOf('installAcquisitionTransport();'));
    expect(source.indexOf('root.render(')).toBeGreaterThan(strip);
  });
});
