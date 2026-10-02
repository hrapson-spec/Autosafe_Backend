/**
 * OA-005: a full App journey (landing -> check -> report -> displayed result)
 * through the REAL App, ReportScreen and ReportDashboard.
 *
 *  - Explicit disabled installer: a fetch spy proves nothing is ever
 *    sent, even though every acknowledgement point runs.
 *  - Default enabled release: the same journey sends exactly the expected
 *    first-party events, carrying only enums, booleans and random ids.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { HelmetProvider } from 'react-helmet-async';
import App from '../App';
import { fixtureExactHigh } from '../fixtures/reportResponses';
import {
  ACQUISITION_COLLECTOR_ENABLED,
  __resetAcquisitionStateForTests,
  installAcquisitionTransport,
} from './acquisitionEvents';
import { initAcquisitionLanding } from './acquisitionLanding';
import { loadEventSchema, validates } from './acquisitionSchemaCheck.testutil';

vi.mock('../services/reportApi', () => ({ createReport: vi.fn(), getReport: vi.fn() }));
vi.mock('../services/autosafeApi', () => ({
  getPublicStats: vi.fn(),
  submitReportEmail: vi.fn(),
  submitGarageLead: vi.fn(),
  submitMotReminder: vi.fn(),
}));
vi.mock('./analytics', () => ({
  trackConversion: vi.fn(),
  trackFunnel: vi.fn(),
  trackPageView: vi.fn(),
  trackReportView: vi.fn(),
}));

import { createReport, getReport } from '../services/reportApi';
import { getPublicStats } from '../services/autosafeApi';

const AL = '5b1d7a52-5d5c-4f55-9a52-3c6e1f1c8a11';
const schema = loadEventSchema();
let fetchSpy: ReturnType<typeof vi.fn>;

function renderApp(path: string) {
  return render(
    <HelmetProvider>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </HelmetProvider>,
  );
}

async function journey() {
  const user = userEvent.setup();
  renderApp('/app');
  await user.type(screen.getByLabelText('Registration Number', { exact: false }), 'AB12CDE');
  await user.type(screen.getByRole('textbox', { name: 'Postcode' }), 'SW1A 1AA');
  await user.click(screen.getByRole('button', { name: /check this car/i }));
  await screen.findByTestId('comparison-result');
}

beforeEach(() => {
  __resetAcquisitionStateForTests();
  sessionStorage.clear();
  fetchSpy = vi.fn(() => Promise.resolve({ status: 202 } as Response));
  vi.stubGlobal('fetch', fetchSpy);
  vi.mocked(getPublicStats).mockResolvedValue({ total_checks: 1, checks_this_month: 1, mot_records: '148M+' });
  vi.mocked(createReport).mockResolvedValue(fixtureExactHigh);
  vi.mocked(getReport).mockResolvedValue(fixtureExactHigh);
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { value: vi.fn(), configurable: true });
});

afterEach(() => {
  cleanup();
  delete window.autosafeMeasurement;
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  __resetAcquisitionStateForTests();
  window.history.replaceState(null, '', '/');
});

describe('explicit disabled transport', () => {
  it('sends nothing during a full journey', async () => {
    window.autosafeMeasurement = { getContext: () => ({landingId: AL, sourceGroup: 'google_organic', windowStartMinute: Math.floor(Date.now()/60000), pilotGroup:'cost'}), isOff: () => false, setOff: vi.fn() };
    expect(installAcquisitionTransport(false)).toBe(false);
    initAcquisitionLanding();
    await journey();
    await new Promise((r) => setTimeout(r, 50));
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe('default enabled release', () => {
  it('sends check_started, report_created and result_rendered with one session and the landing id, and nothing sensitive', async () => {
    window.autosafeMeasurement = { getContext: () => ({landingId: AL, sourceGroup: 'google_organic', pageFamily:'app', windowStartMinute: Math.floor(Date.now()/60000), pilotGroup:'cost'}), isOff: () => false, setOff: vi.fn() };
    initAcquisitionLanding(true);
    expect(ACQUISITION_COLLECTOR_ENABLED).toBe(true);
    expect(installAcquisitionTransport()).toBe(true);
    await journey();
    await waitFor(() => expect(fetchSpy.mock.calls.length).toBeGreaterThanOrEqual(3));

    const wires = fetchSpy.mock.calls.map((c) => JSON.parse((c[1] as { body: string }).body));
    expect(wires.map((w) => w.event)).toEqual(['check_started', 'report_created', 'result_rendered']);
    expect(new Set(wires.map((w) => w.session_id)).size).toBe(1);
    expect(new Set(wires.map((w) => w.landing_id))).toEqual(new Set([AL]));
    expect(new Set(wires.map((w) => w.source_group))).toEqual(new Set(['google_organic']));
    expect(new Set(wires.map((w) => w.page_family))).toEqual(new Set(['app']));
    expect(wires[2]).toMatchObject({ supported_result: true, outcome_group: 'exact_comparison', entry_mode: 'fresh_check' });
    expect(wires[0].operation_id).toBe(wires[2].operation_id);

    for (const w of wires) {
      const { session_id: _s, landing_id: _l, release_sha: _r, window_start_minute: _w, pilot_group: _c, page_family: _p, source_group: _g, ...event } = w;
      expect(validates(schema, event), JSON.stringify(event)).toBe(true);
    }
    const all = fetchSpy.mock.calls.map((c) => JSON.stringify(c)).join('\n');
    for (const secret of ['AB12CDE', 'SW1A', fixtureExactHigh.report_token as string, fixtureExactHigh.vehicle.make]) {
      expect(all).not.toContain(secret);
    }
    for (const c of fetchSpy.mock.calls) {
      expect(c[0]).toBe('/api/acquisition/events');
      expect(c[1]).toMatchObject({ keepalive: true, credentials: 'omit', referrerPolicy: 'no-referrer', cache: 'no-store' });
    }
  });
});
