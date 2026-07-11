import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { trackConversion, trackFunnel, trackReportView } from './analytics';

beforeEach(() => {
  window.history.replaceState({}, '', '/app');
});

afterEach(() => {
  delete window.umami;
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

  it('never lets a third-party analytics exception break the report flow', () => {
    window.umami = { track: vi.fn(() => { throw new Error('analytics offline'); }) };
    window.gtag = vi.fn(() => { throw new Error('tag offline'); });

    expect(() => trackFunnel('reg_entered')).not.toThrow();
    expect(() => trackConversion('risk_check')).not.toThrow();
  });
});
