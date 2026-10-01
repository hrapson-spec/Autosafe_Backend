/**
 * OA-004: committed-render acknowledgement lifecycle through the real
 * ReportScreen -> ResultErrorBoundary -> Suspense -> real ReportDashboard.
 * Events are captured by a recording sink injected through the test-only
 * setter; no data leaves the browser in this build.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import { HelmetProvider } from 'react-helmet-async';
import type { ReportV2 } from '../types';
import ReportScreen from './ReportScreen';
import ReportDashboard from './ReportDashboard';
import { buildScopeDisclosure } from './ReportCopy';
import { ReportApiError } from '../services/errorMessages';
import {
  fixtureExactHigh,
  fixtureLegacyEstimated2_0,
  fixtureModelAverageLow,
  fixturePopulationDefault,
  fixtureUnavailableDegraded,
  fixtureVehiclePrediction,
} from '../fixtures/reportResponses';
import {
  __resetAcquisitionStateForTests,
  __setAcquisitionSinkForTests,
  type AcquisitionEvent,
} from '../utils/acquisitionEvents';
import { classifyResult } from '../utils/resultAcknowledgement';
import { loadEventSchema, validates } from '../utils/acquisitionSchemaCheck.testutil';

vi.mock('../services/reportApi', () => ({ getReport: vi.fn() }));
vi.mock('../services/autosafeApi', () => ({
  submitReportEmail: vi.fn(),
  submitGarageLead: vi.fn(),
  submitMotReminder: vi.fn(),
}));

import { getReport } from '../services/reportApi';

const OP1 = '11111111-1111-4111-8111-111111111111';
const OP2 = '22222222-2222-4222-8222-222222222222';
const schema = loadEventSchema();

let events: AcquisitionEvent[] = [];

beforeEach(() => {
  __resetAcquisitionStateForTests();
  events = [];
  __setAcquisitionSinkForTests({ emit: (e) => events.push(e) });
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { value: vi.fn(), configurable: true });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
  __resetAcquisitionStateForTests();
});

interface NavState {
  postcode?: string;
  inlineReport?: ReportV2;
  operationId?: string;
}

function ui(token: string, state?: NavState, strict = false) {
  const tree = (
    <HelmetProvider>
      <MemoryRouter initialEntries={[{ pathname: `/app/report/${token}`, state }]}>
        <Routes>
          <Route path="/app/report/:token" element={<ReportScreen />} />
          <Route path="/app" element={<div>HOME</div>} />
        </Routes>
      </MemoryRouter>
    </HelmetProvider>
  );
  return strict ? <React.StrictMode>{tree}</React.StrictMode> : tree;
}

const byName = (name: string) => events.filter((e) => e.event === name);

function expectAllSchemaValid() {
  for (const e of events) expect(validates(schema, e), JSON.stringify(e)).toBe(true);
}

describe('result_rendered: supported and reference states from the committed final view', () => {
  it('exact comparison from a fresh check', async () => {
    vi.mocked(getReport).mockResolvedValue(fixtureExactHigh);
    render(ui(fixtureExactHigh.report_token as string, { postcode: 'SW1A 1AA', operationId: OP1 }));
    await screen.findByTestId('comparison-result');
    await waitFor(() => expect(byName('result_rendered')).toHaveLength(1));
    expect(byName('result_rendered')[0]).toMatchObject({
      event: 'result_rendered',
      operation_id: OP1,
      entry_mode: 'fresh_check',
      persistence_mode: 'saved',
      render_delivered: true,
      supported_result: true,
      outcome_group: 'exact_comparison',
      rate_valid: true,
      sample_nonzero: true,
      scope_visible: true,
      result_kind: 'comparison',
      match_scope: 'exact_band',
    });
    expect(events).toHaveLength(1);
    expectAllSchemaValid();
  });

  it('vehicle prediction omits sample_nonzero', async () => {
    vi.mocked(getReport).mockResolvedValue(fixtureVehiclePrediction);
    render(ui('tok-pred', { operationId: OP1 }));
    await screen.findByTestId('vehicle-prediction-result');
    await waitFor(() => expect(byName('result_rendered')).toHaveLength(1));
    const e = byName('result_rendered')[0] as unknown as Record<string, unknown>;
    expect(e.outcome_group).toBe('prediction');
    expect(e.supported_result).toBe(true);
    expect('sample_nonzero' in e).toBe(false);
    expectAllSchemaValid();
  });

  it.each([
    ['age_band_only', fixtureLegacyEstimated2_0],
    ['model_average', fixtureModelAverageLow],
  ] as const)('%s is a broader supported comparison', async (scope, fixture) => {
    vi.mocked(getReport).mockResolvedValue(fixture);
    render(ui('tok-broad', { operationId: OP1 }));
    await screen.findByTestId('comparison-result');
    await waitFor(() => expect(byName('result_rendered')).toHaveLength(1));
    expect(byName('result_rendered')[0]).toMatchObject({
      outcome_group: 'broader_supported_comparison',
      supported_result: true,
      match_scope: scope,
    });
    expectAllSchemaValid();
  });

  it('population_default renders as dataset_reference and is not supported', async () => {
    vi.mocked(getReport).mockResolvedValue(fixturePopulationDefault);
    render(ui('tok-ref', { operationId: OP1 }));
    await screen.findByTestId('comparison-result');
    await waitFor(() => expect(byName('result_rendered')).toHaveLength(1));
    expect(byName('result_rendered')[0]).toMatchObject({
      outcome_group: 'dataset_reference',
      supported_result: false,
      render_delivered: true,
      match_scope: 'population_default',
    });
    expectAllSchemaValid();
  });

  it('inline unsaved success counts when supported: persistence_mode inline_unsaved, getReport never called', async () => {
    render(ui('unsaved', { inlineReport: fixtureVehiclePrediction, operationId: OP1 }));
    await screen.findByTestId('vehicle-prediction-result');
    await waitFor(() => expect(byName('result_rendered')).toHaveLength(1));
    expect(byName('result_rendered')[0]).toMatchObject({
      entry_mode: 'fresh_check',
      persistence_mode: 'inline_unsaved',
      supported_result: true,
      outcome_group: 'prediction',
    });
    expect(getReport).not.toHaveBeenCalled();
    expectAllSchemaValid();
  });

  it.each([
    ['demo (the degraded fixture; D-004 precedence)', fixtureUnavailableDegraded],
    ['real vehicle data', { ...fixtureUnavailableDegraded, vehicle_data_source: 'dvsa' as const }],
  ])('fully degraded (unavailable scope, %s) mounts as result_unavailable/unavailable, not result_rendered', async (_n, degraded) => {
    render(ui('unsaved', { inlineReport: degraded, operationId: OP1 }));
    await screen.findByTestId('comparison-result');
    await waitFor(() => expect(byName('result_unavailable')).toHaveLength(1));
    expect(byName('result_unavailable')[0]).toMatchObject({ reason: 'unavailable', entry_mode: 'fresh_check', operation_id: OP1 });
    expect(byName('result_rendered')).toHaveLength(0);
    expectAllSchemaValid();
  });

  it.each([
    ['exact_band', fixtureExactHigh],
    ['model_average', fixtureModelAverageLow],
    ['population_default', fixturePopulationDefault],
    ['model_prediction', fixtureVehiclePrediction],
  ])('D-004: a demo report (%s) is result_rendered with outcome_group demo, never supported, never result_unavailable', async (scope, fixture) => {
    vi.mocked(getReport).mockResolvedValue({ ...fixture, vehicle_data_source: 'demo' });
    render(ui('tok-demo', { operationId: OP1 }));
    await waitFor(() => expect(byName('result_rendered')).toHaveLength(1));
    const e = byName('result_rendered')[0] as unknown as Record<string, unknown>;
    expect(e).toMatchObject({ render_delivered: true, supported_result: false, outcome_group: 'demo', match_scope: scope });
    expect('sample_nonzero' in e).toBe(scope !== 'model_prediction');
    expect(byName('result_unavailable')).toHaveLength(0);
    expectAllSchemaValid();
  });
});

describe('restored / shared links', () => {
  it('are entry_mode restored_link, carry no operation id, and fabricate no check_started', async () => {
    vi.mocked(getReport).mockResolvedValue(fixtureExactHigh);
    render(ui(fixtureExactHigh.report_token as string));
    await screen.findByTestId('comparison-result');
    await waitFor(() => expect(byName('result_rendered')).toHaveLength(1));
    const e = byName('result_rendered')[0] as unknown as Record<string, unknown>;
    expect(e.entry_mode).toBe('restored_link');
    expect('operation_id' in e).toBe(false);
    expect(byName('check_started')).toHaveLength(0);
    expect(byName('report_created')).toHaveLength(0);
    expectAllSchemaValid();
  });

  it('ignores a malformed operation id in navigation state (untrusted history.state)', async () => {
    vi.mocked(getReport).mockResolvedValue(fixtureExactHigh);
    render(ui('tok', { operationId: 'AB12CDE' }));
    await screen.findByTestId('comparison-result');
    await waitFor(() => expect(byName('result_rendered')).toHaveLength(1));
    expect(byName('result_rendered')[0]).toMatchObject({ entry_mode: 'restored_link' });
    expect(JSON.stringify(events)).not.toContain('AB12CDE');
  });

  it('an unsaved route without an inline payload is unavailable (not found), never a completion', async () => {
    vi.mocked(getReport).mockRejectedValue(new ReportApiError('report_not_found', 'x', 404));
    render(ui('unsaved'));
    await screen.findByText("That report link isn't valid");
    await waitFor(() => expect(byName('result_unavailable')).toHaveLength(1));
    expect(byName('result_unavailable')[0]).toMatchObject({ reason: 'not_found', entry_mode: 'restored_link' });
    expect(byName('result_rendered')).toHaveLength(0);
  });
});

describe('failed retrieval: result_unavailable with a fixed reason', () => {
  it.each([
    ['report_not_found', 'not_found'],
    ['report_expired', 'expired'],
    ['network_error', 'error'],
    ['storage_unavailable', 'error'],
    ['rate_limited', 'error'],
  ] as const)('%s -> %s', async (code, reason) => {
    vi.mocked(getReport).mockRejectedValue(new ReportApiError(code, 'server said AB12CDE secret', 500));
    render(ui('tok', { operationId: OP1 }));
    await waitFor(() => expect(byName('result_unavailable')).toHaveLength(1));
    expect(byName('result_unavailable')[0]).toMatchObject({ reason, entry_mode: 'fresh_check', operation_id: OP1 });
    expect(byName('result_rendered')).toHaveLength(0);
    expect(JSON.stringify(events)).not.toContain('secret');
    expectAllSchemaValid();
  });

  it('a non-ReportApiError rejection is reason error', async () => {
    vi.mocked(getReport).mockRejectedValue(new TypeError('boom'));
    render(ui('tok'));
    await waitFor(() => expect(byName('result_unavailable')).toHaveLength(1));
    expect(byName('result_unavailable')[0]).toMatchObject({ reason: 'error' });
  });
});

describe('loading never emits', () => {
  it('a pending retrieval (spinner) emits nothing; API 200 alone is not completion', async () => {
    let resolve!: (r: ReportV2) => void;
    vi.mocked(getReport).mockReturnValue(new Promise<ReportV2>((r) => { resolve = r; }));
    render(ui('tok', { operationId: OP1 }));
    expect(await screen.findByRole('status')).toBeInTheDocument();
    expect(events).toHaveLength(0);
    resolve(fixtureExactHigh);
    await waitFor(() => expect(byName('result_rendered')).toHaveLength(1));
  });
});

describe('render failures inside the dashboard', () => {
  it('a render throw shows the existing unavailable error view and emits render_failed/render only', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    // mot missing => ReportDashboard throws while rendering.
    const broken = { ...fixtureExactHigh, mot: undefined } as unknown as ReportV2;
    vi.mocked(getReport).mockResolvedValue(broken);
    render(ui('tok', { operationId: OP1 }));
    expect(await screen.findByText("We couldn't load this report")).toBeInTheDocument();
    await waitFor(() => expect(byName('render_failed')).toHaveLength(1));
    expect(byName('render_failed')[0]).toMatchObject({ stage: 'render', entry_mode: 'fresh_check', operation_id: OP1 });
    expect(byName('result_rendered')).toHaveLength(0);
    expect(byName('result_unavailable')).toHaveLength(0);
    expectAllSchemaValid();
    spy.mockRestore();
  });

  it.each([
    ['NaN risk', { ...fixtureExactHigh, risk: { ...fixtureExactHigh.risk, failure_risk: Number.NaN } }],
    ['risk above 1', { ...fixtureExactHigh, risk: { ...fixtureExactHigh.risk, failure_risk: 1.5 } }],
    ['zero tests', { ...fixtureExactHigh, evidence: { ...fixtureExactHigh.evidence, total_tests: 0 } }],
    ['prediction with comparison scope', { ...fixtureVehiclePrediction, evidence: { ...fixtureVehiclePrediction.evidence, match_scope: 'exact_band' as const } }],
  ])('a mounted view with %s is render_failed/contract_invalid, never a result', async (_n, report) => {
    vi.mocked(getReport).mockResolvedValue(report as ReportV2);
    render(ui('tok', { operationId: OP1 }));
    await waitFor(() => expect(byName('render_failed')).toHaveLength(1));
    expect(byName('render_failed')[0]).toMatchObject({ stage: 'contract_invalid' });
    expect(byName('result_rendered')).toHaveLength(0);
    expectAllSchemaValid();
  });
});

describe('deduplication and repeated checks', () => {
  it('StrictMode double effects emit result_rendered once (fresh check)', async () => {
    vi.mocked(getReport).mockResolvedValue(fixtureExactHigh);
    render(ui('tok', { operationId: OP1 }, true));
    await screen.findByTestId('comparison-result');
    await waitFor(() => expect(byName('result_rendered')).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 30));
    expect(byName('result_rendered')).toHaveLength(1);
  });

  it('StrictMode double effects emit result_rendered once (restored link, per-mount id)', async () => {
    vi.mocked(getReport).mockResolvedValue(fixtureExactHigh);
    render(ui('tok', undefined, true));
    await screen.findByTestId('comparison-result');
    await waitFor(() => expect(byName('result_rendered')).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 30));
    expect(byName('result_rendered')).toHaveLength(1);
  });

  it('StrictMode emits result_unavailable once', async () => {
    vi.mocked(getReport).mockRejectedValue(new ReportApiError('report_expired', 'x', 410));
    render(ui('tok', { operationId: OP1 }, true));
    await waitFor(() => expect(byName('result_unavailable')).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 30));
    expect(byName('result_unavailable')).toHaveLength(1);
  });

  it('a remount of the same operation does not emit again', async () => {
    vi.mocked(getReport).mockResolvedValue(fixtureExactHigh);
    const first = render(ui('tok', { operationId: OP1 }));
    await waitFor(() => expect(byName('result_rendered')).toHaveLength(1));
    first.unmount();
    render(ui('tok', { operationId: OP1 }));
    await screen.findByTestId('comparison-result');
    await new Promise((r) => setTimeout(r, 30));
    expect(byName('result_rendered')).toHaveLength(1);
  });

  it('a genuine remount of a restored link is a new mount (collector aggregation counts the session once)', async () => {
    vi.mocked(getReport).mockResolvedValue(fixtureExactHigh);
    const first = render(ui('tok'));
    await waitFor(() => expect(byName('result_rendered')).toHaveLength(1));
    first.unmount();
    render(ui('tok'));
    await waitFor(() => expect(byName('result_rendered')).toHaveLength(2));
  });

  it('a repeated deliberate check of the same vehicle is a second operation with its own emission', async () => {
    vi.mocked(getReport).mockResolvedValue(fixtureExactHigh);
    const first = render(ui('tok', { operationId: OP1 }));
    await waitFor(() => expect(byName('result_rendered')).toHaveLength(1));
    first.unmount();
    render(ui('tok', { operationId: OP2 }));
    await waitFor(() => expect(byName('result_rendered')).toHaveLength(2));
    const ops = byName('result_rendered').map((e) => (e as { operation_id?: string }).operation_id);
    expect(ops).toEqual([OP1, OP2]);
  });
});

describe('privacy and third-party boundary on the report route', () => {
  it('sends nothing: no fetch, no beacon, no gtag, no umami; report-route analytics stay blocked', async () => {
    window.history.pushState({}, '', '/app/report/9c7f2b1a4e6d8035c2f19a7b6e4d3c81');
    const gtag = vi.fn();
    const track = vi.fn();
    const fetchSpy = vi.fn();
    const beacon = vi.fn();
    (window as unknown as { gtag: unknown }).gtag = gtag;
    (window as unknown as { umami: unknown }).umami = { track };
    vi.stubGlobal('fetch', fetchSpy);
    Object.defineProperty(navigator, 'sendBeacon', { value: beacon, configurable: true, writable: true });
    vi.mocked(getReport).mockResolvedValue(fixtureExactHigh);

    render(ui(fixtureExactHigh.report_token as string, { operationId: OP1 }));
    await screen.findByTestId('comparison-result');
    await waitFor(() => expect(byName('result_rendered')).toHaveLength(1));

    expect(gtag).not.toHaveBeenCalled();
    expect(track).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(beacon).not.toHaveBeenCalled();
    // The recorded events contain no report identity.
    const json = JSON.stringify(events);
    expect(json).not.toContain(fixtureExactHigh.report_token as string);
    expect(json).not.toContain(fixtureExactHigh.registration);

    delete (window as unknown as { gtag?: unknown }).gtag;
    delete (window as unknown as { umami?: unknown }).umami;
    delete (navigator as unknown as { sendBeacon?: unknown }).sendBeacon;
    vi.unstubAllGlobals();
    window.history.pushState({}, '', '/');
  });
});

describe('scope_visible tracks what the real dashboard renders', () => {
  it.each([
    ['exact_band', fixtureExactHigh],
    ['age_band_only', fixtureLegacyEstimated2_0],
    ['model_average', fixtureModelAverageLow],
    ['population_default', fixturePopulationDefault],
    ['unavailable', fixtureUnavailableDegraded],
    ['model_prediction', fixtureVehiclePrediction],
  ] as const)('%s: the scope disclosure text the rule relies on is in the mounted DOM', (_scope, fixture) => {
    const { container } = render(
      <HelmetProvider>
        <MemoryRouter>
          <ReportDashboard report={fixture} onReset={() => undefined} />
        </MemoryRouter>
      </HelmetProvider>
    );
    expect(classifyResult(fixture).scope_visible).toBe(true);
    const disclosure = buildScopeDisclosure(fixture);
    expect(container.textContent).toContain(disclosure);
    // ...inside the collapsed-by-default "How this result was calculated" disclosure.
    const details = container.querySelector('details');
    expect(details).not.toBeNull();
    expect(details!.open).toBe(false);
    expect(details!.textContent).toContain(disclosure);
  });

  it('onRendered fires once, after the commit, and is cancelled if the view unmounts first', async () => {
    vi.useFakeTimers();
    try {
      const onRendered = vi.fn();
      const mount = () =>
        render(
          <HelmetProvider>
            <MemoryRouter>
              <ReportDashboard report={fixtureExactHigh} onReset={() => undefined} onRendered={onRendered} />
            </MemoryRouter>
          </HelmetProvider>
        );
      const first = mount();
      expect(onRendered).not.toHaveBeenCalled(); // deferred past the commit
      vi.advanceTimersByTime(5);
      expect(onRendered).toHaveBeenCalledTimes(1);
      first.unmount();

      onRendered.mockClear();
      const second = mount();
      second.unmount(); // torn down before the timer fires
      vi.advanceTimersByTime(50);
      expect(onRendered).not.toHaveBeenCalled();

      // StrictMode replays the effect (mount, cleanup, mount): still exactly once.
      onRendered.mockClear();
      render(
        <React.StrictMode>
          <HelmetProvider>
            <MemoryRouter>
              <ReportDashboard report={fixtureExactHigh} onReset={() => undefined} onRendered={onRendered} />
            </MemoryRouter>
          </HelmetProvider>
        </React.StrictMode>
      );
      vi.advanceTimersByTime(50);
      expect(onRendered).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('restored link navigated within the same mount (A -> B)', () => {
  it('is a new observation per route entry: two result_rendered, none stored from the token', async () => {
    vi.mocked(getReport).mockImplementation(async (t: string) =>
      t === 'tok-A' ? fixtureExactHigh : fixtureModelAverageLow
    );
    function Nav() {
      const navigate = useNavigate();
      return <button type="button" onClick={() => navigate('/app/report/tok-B')}>go-b</button>;
    }
    render(
      <HelmetProvider>
        <MemoryRouter initialEntries={['/app/report/tok-A']}>
          <Nav />
          <Routes>
            <Route path="/app/report/:token" element={<ReportScreen />} />
          </Routes>
        </MemoryRouter>
      </HelmetProvider>
    );
    await waitFor(() => expect(byName('result_rendered')).toHaveLength(1));
    expect(byName('result_rendered')[0]).toMatchObject({ match_scope: 'exact_band', entry_mode: 'restored_link' });
    screen.getByRole('button', { name: 'go-b' }).click();
    await waitFor(() => expect(byName('result_rendered')).toHaveLength(2));
    expect(byName('result_rendered')[1]).toMatchObject({ match_scope: 'model_average', entry_mode: 'restored_link' });
    await new Promise((r) => setTimeout(r, 30));
    expect(byName('result_rendered')).toHaveLength(2);
    expect(JSON.stringify(events)).not.toContain('tok-');
  });
});
