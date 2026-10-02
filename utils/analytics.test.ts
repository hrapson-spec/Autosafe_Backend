import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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

describe('legacy analytics collection is retired', () => {
  it('ships no automatic Umami loader in either entry surface', async () => {
    const index = await import('../index.html?raw');
    const loader = await import('../static/umami.js?raw');
    expect(index.default).not.toContain('umami-production-cb51.up.railway.app/script.js');
    expect(loader.default).not.toContain('createElement');
    expect(loader.default).not.toContain('fetch(');
  });
});
