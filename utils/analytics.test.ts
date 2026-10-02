import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { trackConversion, trackFunnel, trackPageView, trackReportView } from './analytics';

type Payload = Record<string, unknown>;
type BeforeSend = (type: string, payload: Payload) => Payload | false;

declare global {
  interface Window {
    autosafeUmamiBeforeSend?: BeforeSend;
  }
}

beforeEach(() => {
  window.history.replaceState({}, '', '/app');
});

afterEach(() => {
  window.dispatchEvent(new Event('autosafe:umami-ready'));
  delete window.umami;
  delete window.autosafeUmamiBeforeSend;
  document.head.querySelectorAll('script[data-website-id]').forEach((s) => s.remove());
  delete window.gtag;
  window.history.replaceState({}, '', '/');
});


describe('analytics privacy boundary', () => {
  it('forwards only the reviewed aggregate event fields', () => {
    const track = vi.fn();
    window.umami = { track };

    trackFunnel('report_viewed', {
      make: 'FORD',
      model: 'FIESTA',
      risk_bucket: 'low',
      registration: 'AB12CDE',
      postcode: 'SW1A 1AA',
      report_token: 'secret-token',
      email: 'owner@example.com',
    } as Record<string, string | number>);

    expect(track).toHaveBeenCalledWith('report_viewed', {
      make: 'FORD',
      model: 'FIESTA',
      risk_bucket: 'low',
    });
  });

  it('buckets the rate rather than sending the precise number from report-view tracking', () => {
    const track = vi.fn();
    window.umami = { track };

    trackReportView('FORD', 'FIESTA', 42);

    expect(track).toHaveBeenCalledWith('report_viewed', {
      make: 'FORD',
      model: 'FIESTA',
      risk_bucket: 'medium',
    });
  });

  it('suppresses all custom analytics after SPA navigation onto a bearer report route', () => {
    const umamiTrack = vi.fn();
    const gtag = vi.fn();
    window.umami = { track: umamiTrack };
    window.gtag = gtag;
    window.history.pushState({}, '', '/app/report/opaque-bearer-token');

    trackReportView('FORD', 'FIESTA', 42);
    trackFunnel('share_copy_link', { risk_percent: 42 });
    trackConversion('mot_reminder');

    expect(umamiTrack).not.toHaveBeenCalled();
    expect(gtag).not.toHaveBeenCalled();
  });

  // react-router matches routes case-insensitively, so /app/Report/<token>
  // renders the report; suppression must not depend on the URL's case.
  it.each(['/app/Report/opaque-bearer-token', '/app/REPORT/opaque-bearer-token', '/App/report/opaque-bearer-token'])(
    'suppresses all custom analytics and page views on the case variant %s',
    (path) => {
      const umamiTrack = vi.fn();
      const gtag = vi.fn();
      window.umami = { track: umamiTrack };
      window.gtag = gtag;
      window.history.pushState({}, '', path);

      trackReportView('FORD', 'FIESTA', 42);
      trackFunnel('share_copy_link', { risk_percent: 42 });
      trackConversion('mot_reminder');
      trackPageView();
      window.dispatchEvent(new Event('autosafe:umami-ready'));

      expect(umamiTrack).not.toHaveBeenCalled();
      expect(gtag).not.toHaveBeenCalled();
    },
  );

  it('never lets a third-party analytics exception break the report flow', () => {
    window.umami = { track: vi.fn(() => { throw new Error('analytics offline'); }) };
    window.gtag = vi.fn(() => { throw new Error('tag offline'); });

    expect(() => trackFunnel('reg_entered')).not.toThrow();
    expect(() => trackConversion('risk_check')).not.toThrow();
  });
});


describe('page views', () => {
  it('sends a path-only page view, dropping query string and hash', () => {
    const track = vi.fn();
    window.umami = { track };
    window.history.replaceState({}, '', '/app?utm_source=x#top');

    trackPageView();

    expect(track).toHaveBeenCalledTimes(1);
    const build = track.mock.calls[0][0] as (p: Record<string, unknown>) => Record<string, unknown>;
    expect(build({ url: 'https://www.autosafe.one/app?utm_source=x#top', title: 'AutoSafe' })).toEqual({
      url: '/app',
      title: 'AutoSafe',
    });
  });

  it('never sends a page view on a bearer report route', () => {
    const track = vi.fn();
    window.umami = { track };
    window.history.pushState({}, '', '/app/report/opaque-bearer-token');

    trackPageView();
    window.dispatchEvent(new Event('autosafe:umami-ready'));

    expect(track).not.toHaveBeenCalled();
  });

  it('waits for the deferred script and sends one view for the route current at load', () => {
    trackPageView();
    window.history.pushState({}, '', '/app/terms');
    trackPageView();

    const track = vi.fn();
    window.umami = { track };
    window.dispatchEvent(new Event('autosafe:umami-ready'));

    expect(track).toHaveBeenCalledTimes(1);
    const build = track.mock.calls[0][0] as (p: Record<string, unknown>) => Record<string, unknown>;
    expect(build({}).url).toBe('/app/terms');
  });

  it('drops a queued view if the user reached a report route before the script loaded', () => {
    trackPageView();
    window.history.pushState({}, '', '/app/report/opaque-bearer-token');

    const track = vi.fn();
    window.umami = { track };
    window.dispatchEvent(new Event('autosafe:umami-ready'));

    expect(track).not.toHaveBeenCalled();
  });
});

// The shipped before-send filters live in index.html and static/umami.js;
// exercise the real source text, not a copy.
const ROOT = resolve(__dirname, '..');

function loadIndexBeforeSend(): BeforeSend {
  const html = readFileSync(resolve(ROOT, 'index.html'), 'utf-8');
  const match = html.match(/window\.autosafeUmamiBeforeSend = function[\s\S]*?\n {6}\};/);
  if (!match) throw new Error('autosafeUmamiBeforeSend not found in index.html');
  new Function(match[0])();
  return window.autosafeUmamiBeforeSend as BeforeSend;
}

function loadStaticBeforeSend(): BeforeSend {
  new Function(readFileSync(resolve(ROOT, 'static', 'umami.js'), 'utf-8'))();
  return window.autosafeUmamiBeforeSend as BeforeSend;
}

describe.each([
  ['index.html', loadIndexBeforeSend],
  ['static/umami.js', loadStaticBeforeSend],
])('Umami before-send filter (%s)', (_name, load) => {
  it('reduces url to the path and an external referrer to its origin', () => {
    window.history.replaceState({}, '', '/guides/mot-cost?reg=AB12CDE&postcode=SW1A1AA#x');
    const beforeSend = load();

    const out = beforeSend('event', {
      url: 'https://www.autosafe.one/guides/mot-cost?reg=AB12CDE&postcode=SW1A1AA#x',
      referrer: 'https://www.google.com/search?q=AB12CDE',
      name: 'reg_entered',
    });

    expect(out).toEqual({ url: '/guides/mot-cost', referrer: 'https://www.google.com/', name: 'reg_entered' });
    expect(JSON.stringify(out)).not.toMatch(/AB12CDE|SW1A/);
  });

  it('blanks same-site and malformed referrers', () => {
    window.history.replaceState({}, '', '/app');
    const beforeSend = load();

    expect((beforeSend('event', { referrer: `${window.location.origin}/app/report/token` }) as Payload).referrer).toBe('');
    expect((beforeSend('event', { referrer: 'not a url' }) as Payload).referrer).toBe('');
  });

  it('drops every payload on a bearer report route', () => {
    window.history.replaceState({}, '', '/app');
    const beforeSend = load();
    window.history.pushState({}, '', '/app/report/opaque-bearer-token');

    expect(beforeSend('event', { url: '/app/report/opaque-bearer-token' })).toBe(false);
  });

  it.each(['/app/Report/opaque-bearer-token', '/app/REPORT/opaque-bearer-token'])(
    'drops every payload on the case variant %s',
    (path) => {
      window.history.replaceState({}, '', '/app');
      const beforeSend = load();
      window.history.pushState({}, '', path);

      expect(beforeSend('event', { url: path })).toBe(false);
    },
  );
});

describe('index.html inline analytics gate', () => {
  function inlineAllowed(path: string): boolean {
    const html = readFileSync(resolve(ROOT, 'index.html'), 'utf-8');
    const match = html.match(/window\.autosafeAnalyticsAllowed = [^;]+;/);
    if (!match) throw new Error('autosafeAnalyticsAllowed assignment not found in index.html');
    window.history.replaceState({}, '', path);
    new Function(match[0])();
    return (window as unknown as { autosafeAnalyticsAllowed: boolean }).autosafeAnalyticsAllowed;
  }

  it.each([
    ['/app/report/opaque-bearer-token', false],
    ['/app/Report/opaque-bearer-token', false],
    ['/app/REPORT/opaque-bearer-token', false],
    ['/app', true],
    ['/app/terms', true],
    ['/app/reports', true],
  ])('allows analytics on %s: %s', (path, allowed) => {
    expect(inlineAllowed(path)).toBe(allowed);
  });
});

describe('static/umami.js loader', () => {
  it('does not load Umami at all on a report route', () => {
    window.history.replaceState({}, '', '/app/report/opaque-bearer-token');
    new Function(readFileSync(resolve(ROOT, 'static', 'umami.js'), 'utf-8'))();

    expect(document.head.querySelector('script[data-website-id]')).toBeNull();
    expect(window.autosafeUmamiBeforeSend).toBeUndefined();
  });

  it.each(['/app/Report/opaque-bearer-token', '/app/REPORT/opaque-bearer-token'])(
    'does not load Umami at all on the case variant %s',
    (path) => {
      window.history.replaceState({}, '', path);
      new Function(readFileSync(resolve(ROOT, 'static', 'umami.js'), 'utf-8'))();

      expect(document.head.querySelector('script[data-website-id]')).toBeNull();
      expect(window.autosafeUmamiBeforeSend).toBeUndefined();
    },
  );

  it('loads Umami with automatic tracking off and the before-send filter attached', () => {
    window.history.replaceState({}, '', '/guides/mot-cost');
    new Function(readFileSync(resolve(ROOT, 'static', 'umami.js'), 'utf-8'))();

    const script = document.head.querySelector('script[data-website-id]');
    expect(script?.getAttribute('data-auto-track')).toBe('false');
    expect(script?.getAttribute('data-before-send')).toBe('autosafeUmamiBeforeSend');
  });
});
