import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Link, MemoryRouter, useLocation, useParams } from 'react-router-dom';
import { HelmetProvider } from 'react-helmet-async';
import type { PublicStats } from './types';
import App from './App';
import { ReportApiError, mapErrorToMessage } from './services/errorMessages';
import { fixtureExactHigh, fixtureUnavailableDegraded, fixtureVehiclePrediction } from './fixtures/reportResponses';
import {
  __resetAcquisitionStateForTests,
  __setAcquisitionSinkForTests,
  type AcquisitionEvent,
} from './utils/acquisitionEvents';
import { loadEventSchema, validates } from './utils/acquisitionSchemaCheck.testutil';

// ---------------------------------------------------------------------------
// Module mocks
//
// - reportApi/autosafeApi/analytics are mocked so this file tests exactly
//   App's own responsibility (trigger the check, route on the result, fire
//   the right analytics calls) without depending on real network calls.
// - components/ReportScreen is mocked with a probe: ReportScreen's own
//   fetch/loading/error behaviour is covered by ReportScreen.test.tsx.
//   Decoupling here means this file only asserts *what App navigated to*
//   (path, token, location.state), not how ReportScreen renders it.
// ---------------------------------------------------------------------------
vi.mock('./services/reportApi', () => ({
  createReport: vi.fn(),
  getReport: vi.fn(),
}));

vi.mock('./services/autosafeApi', () => ({
  getPublicStats: vi.fn(),
}));

vi.mock('./utils/analytics', () => ({
  trackConversion: vi.fn(),
  trackFunnel: vi.fn(),
  trackPageView: vi.fn(),
  trackReportView: vi.fn(),
}));

vi.mock('./components/ReportScreen', () => ({
  default: function ReportScreenProbe() {
    const { token } = useParams();
    const location = useLocation();
    const state = (location.state ?? {}) as { postcode?: string; inlineReport?: unknown; operationId?: string };
    return (
      <div data-testid="report-screen-probe">
        <span data-testid="probe-token">{token}</span>
        <span data-testid="probe-postcode">{state.postcode ?? ''}</span>
        <span data-testid="probe-inline">{state.inlineReport ? 'yes' : 'no'}</span>
        <span data-testid="probe-operation">{state.operationId ?? ''}</span>
        <Link to="/app">back to form</Link>
      </div>
    );
  },
}));

import { createReport } from './services/reportApi';
import { getPublicStats } from './services/autosafeApi';
import { trackConversion, trackFunnel } from './utils/analytics';

const DEFAULT_STATS: PublicStats = { total_checks: 1000, checks_this_month: 1, mot_records: '148M+' };

function renderApp(initialEntries: string[]) {
  return render(
    <HelmetProvider>
      <MemoryRouter initialEntries={initialEntries}>
        <App />
      </MemoryRouter>
    </HelmetProvider>
  );
}

beforeEach(() => {
  sessionStorage.clear();
  vi.mocked(getPublicStats).mockResolvedValue(DEFAULT_STATS);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('App: home page rendering', () => {
  it.each([['/'], ['/app']])('renders the check form at %s', async (path) => {
    renderApp([path]);

    expect(
      await screen.findByRole('heading', {
        level: 1,
        name: 'Fix it before they find it.',
      })
    ).toBeInTheDocument();
    expect(screen.getByText('Start with your MOT record. Know what to check next.')).toBeInTheDocument();
    expect(screen.queryByText('See what the MOT evidence says.')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Registration Number', { exact: false })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Postcode' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /check this car/i })).toBeInTheDocument();
    expect(await screen.findByText(/148M\+ recorded MOT tests analysed/)).toBeInTheDocument();
    expect(screen.queryByText(/vehicles checked this month/i)).not.toBeInTheDocument();
    expect(screen.getByText('Free to check')).toBeInTheDocument();
  });

  it('prefills from one-use session storage without putting the registration in the URL', async () => {
    sessionStorage.setItem('autosafe_pending_registration', 'AB12CDE');
    renderApp(['/app']);

    expect(await screen.findByLabelText('Registration Number', { exact: false })).toHaveValue('AB12CDE');
    expect(sessionStorage.getItem('autosafe_pending_registration')).toBeNull();
  });

  it('does not read a legacy registration query parameter', async () => {
    renderApp(['/app?reg=AB12CDE']);

    expect(await screen.findByLabelText('Registration Number', { exact: false })).toHaveValue('');
  });
});

describe('App: submit success', () => {
  it('navigates to /app/report/<token> and fires conversion + funnel events', async () => {
    const user = userEvent.setup();
    vi.mocked(createReport).mockResolvedValue(fixtureExactHigh);

    renderApp(['/app']);

    await user.type(screen.getByLabelText('Registration Number', { exact: false }), 'AB12CDE');
    await user.type(screen.getByRole('textbox', { name: 'Postcode' }), 'SW1A 1AA');
    await user.click(screen.getByRole('button', { name: /check this car/i }));

    expect(await screen.findByTestId('report-screen-probe')).toBeInTheDocument();
    expect(screen.getByTestId('probe-token')).toHaveTextContent(fixtureExactHigh.report_token as string);
    expect(screen.getByTestId('probe-postcode')).toHaveTextContent('SW1A 1AA');
    expect(screen.getByTestId('probe-inline')).toHaveTextContent('no');

    expect(createReport).toHaveBeenCalledWith(
      'AB12CDE',
      'SW1A 1AA',
      expect.any(String),
    );
    expect(trackConversion).toHaveBeenCalledWith('risk_check');
    expect(trackFunnel).toHaveBeenCalledWith('reg_entered');
  });

  it('routes to /app/report/unsaved with the inline report when report_token is null (persistence-degraded)', async () => {
    const user = userEvent.setup();
    vi.mocked(createReport).mockResolvedValue(fixtureUnavailableDegraded);

    renderApp(['/app']);

    await user.type(screen.getByLabelText('Registration Number', { exact: false }), 'ZZ99ZZZ');
    await user.type(screen.getByRole('textbox', { name: 'Postcode' }), 'SW1A 1AA');
    await user.click(screen.getByRole('button', { name: /check this car/i }));

    expect(await screen.findByTestId('probe-token')).toHaveTextContent('unsaved');
    expect(screen.getByTestId('probe-inline')).toHaveTextContent('yes');
    expect(screen.getByTestId('probe-postcode')).toHaveTextContent('SW1A 1AA');
  });
});

describe('App: submit failure (remount regression)', () => {
  it('shows the mapped error message and keeps the typed values in place', async () => {
    const user = userEvent.setup();
    vi.mocked(createReport).mockRejectedValue(
      new ReportApiError('vehicle_not_found', mapErrorToMessage('vehicle_not_found'), 404, 'corr123')
    );

    renderApp(['/app']);

    const regInput = screen.getByLabelText('Registration Number', { exact: false });
    const postcodeInput = screen.getByRole('textbox', { name: 'Postcode' });
    await user.type(regInput, 'AB12CDE');
    await user.type(postcodeInput, 'SW1A 1AA');
    await user.click(screen.getByRole('button', { name: /check this car/i }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(mapErrorToMessage('vehicle_not_found'));

    // Load-bearing: before the HomePage hoist, an App re-render (triggered
    // here by setError) recreated HomePage as a new component type each
    // render, so React unmounted+remounted HeroForm and wiped these values.
    expect(regInput).toHaveValue('AB12CDE');
    expect(postcodeInput).toHaveValue('SW1A 1AA');
  });

  it('reuses the same idempotency key when the same logical submission is retried', async () => {
    const user = userEvent.setup();
    vi.mocked(createReport)
      .mockRejectedValueOnce(new ReportApiError('network_error', mapErrorToMessage('network_error')))
      .mockResolvedValueOnce(fixtureExactHigh);

    renderApp(['/app']);
    await user.type(screen.getByLabelText('Registration Number', { exact: false }), 'AB12CDE');
    await user.type(screen.getByRole('textbox', { name: 'Postcode' }), 'SW1A 1AA');

    await user.click(screen.getByRole('button', { name: /check this car/i }));
    await screen.findByRole('alert');
    await user.click(screen.getByRole('button', { name: /check this car/i }));

    expect(vi.mocked(createReport)).toHaveBeenCalledTimes(2);
    // idempotencyKey is createReport's 3rd positional argument (index 2)
    // now that the removed mileageUser parameter no longer occupies index 2.
    const firstKey = vi.mocked(createReport).mock.calls[0][2];
    const secondKey = vi.mocked(createReport).mock.calls[1][2];
    expect(firstKey).toBeTruthy();
    expect(secondKey).toBe(firstKey);
  });

  it('mints a new key after an idempotency conflict makes the old key unusable', async () => {
    const user = userEvent.setup();
    vi.mocked(createReport)
      .mockRejectedValueOnce(
        new ReportApiError(
          'idempotency_conflict',
          mapErrorToMessage('idempotency_conflict'),
          409,
          'corr-conflict'
        )
      )
      .mockResolvedValueOnce(fixtureExactHigh);

    renderApp(['/app']);
    await user.type(screen.getByLabelText('Registration Number', { exact: false }), 'AB12CDE');
    await user.type(screen.getByRole('textbox', { name: 'Postcode' }), 'SW1A 1AA');

    await user.click(screen.getByRole('button', { name: /check this car/i }));
    await screen.findByRole('alert');
    await user.click(screen.getByRole('button', { name: /check this car/i }));

    // idempotencyKey is createReport's 3rd positional argument (index 2)
    // now that the removed mileageUser parameter no longer occupies index 2.
    const firstKey = vi.mocked(createReport).mock.calls[0][2];
    const secondKey = vi.mocked(createReport).mock.calls[1][2];
    expect(firstKey).toBeTruthy();
    expect(secondKey).toBeTruthy();
    expect(secondKey).not.toBe(firstKey);
  });
});

describe('App: remount regression via stats resolving mid-typing', () => {
  it('does not clear the registration input when getPublicStats resolves after the user has started typing', async () => {
    const user = userEvent.setup();
    let resolveStats!: (stats: PublicStats) => void;
    vi.mocked(getPublicStats).mockReturnValue(
      new Promise<PublicStats>((resolve) => {
        resolveStats = resolve;
      })
    );

    renderApp(['/app']);

    const regInput = screen.getByLabelText('Registration Number', { exact: false });
    await user.type(regInput, 'AB12CDE');
    expect(regInput).toHaveValue('AB12CDE');

    resolveStats({ total_checks: 999, checks_this_month: 421, mot_records: '149M+' });

    // Proves the resolved stats actually flowed through and re-rendered
    // HomePage before we assert the input survived that re-render.
    expect(await screen.findByText(/149M\+ recorded MOT tests analysed/)).toBeInTheDocument();
    expect(screen.queryByText(/vehicles checked this month/i)).not.toBeInTheDocument();

    expect(regInput).toHaveValue('AB12CDE');
  });
});

// ---------------------------------------------------------------------------
// OA-004: acquisition measurement emitted by App.tsx. Collection is OFF: the
// recording sink below is the test-only injection point; nothing is sent.
// ---------------------------------------------------------------------------
describe('App: acquisition events (OA-004)', () => {
  let events: AcquisitionEvent[] = [];
  let log: string[] = [];
  const schema = loadEventSchema();

  async function submit(user: ReturnType<typeof userEvent.setup>, reg = 'AB12CDE', postcode = 'SW1A 1AA') {
    const regInput = screen.getByLabelText('Registration Number', { exact: false });
    await user.clear(regInput);
    await user.type(regInput, reg);
    const pcInput = screen.getByRole('textbox', { name: 'Postcode' });
    await user.clear(pcInput);
    await user.type(pcInput, postcode);
    await user.click(screen.getByRole('button', { name: /check this car/i }));
  }

  beforeEach(() => {
    __resetAcquisitionStateForTests();
    events = [];
    log = [];
    __setAcquisitionSinkForTests({
      emit: (e) => {
        events.push(e);
        log.push(`event:${e.event}`);
      },
    });
  });

  afterEach(() => {
    __resetAcquisitionStateForTests();
  });

  it('emits check_started before the API call and report_created after it, both schema-valid and free of form values', async () => {
    const user = userEvent.setup();
    vi.mocked(createReport).mockImplementation(async () => {
      log.push('createReport');
      return fixtureExactHigh;
    });
    renderApp(['/app']);
    await submit(user);
    expect(await screen.findByTestId('report-screen-probe')).toBeInTheDocument();

    expect(log.indexOf('event:check_started')).toBeGreaterThanOrEqual(0);
    expect(log.indexOf('event:check_started')).toBeLessThan(log.indexOf('createReport'));
    expect(log.indexOf('createReport')).toBeLessThan(log.indexOf('event:report_created'));

    const started = events.find((e) => e.event === 'check_started')!;
    const created = events.find((e) => e.event === 'report_created')!;
    expect(created).toMatchObject({
      result_kind: 'comparison',
      match_scope: 'exact_band',
      persistence_mode: 'saved',
      entry_mode: 'fresh_check',
    });
    const op = (started as { operation_id: string }).operation_id;
    expect((created as { operation_id: string }).operation_id).toBe(op);
    // The operation id travels to the report route in navigation state.
    expect(screen.getByTestId('probe-operation')).toHaveTextContent(op);

    for (const e of events) expect(validates(schema, e), JSON.stringify(e)).toBe(true);
    const json = JSON.stringify(events);
    for (const secret of ['AB12CDE', 'SW1A', fixtureExactHigh.report_token as string, fixtureExactHigh.vehicle.make]) {
      expect(json).not.toContain(secret);
    }
    // The operation id is not derived from the inputs.
    expect(op).not.toContain('AB12');
  });

  it('pins the existing third-party calls: same payload, same order, after createReport, before navigation, once each', async () => {
    const user = userEvent.setup();
    const probeVisibleAt: Record<string, boolean> = {};
    vi.mocked(createReport).mockImplementation(async () => {
      log.push('createReport');
      return fixtureExactHigh;
    });
    vi.mocked(trackConversion).mockImplementation((...args: unknown[]) => {
      log.push('trackConversion');
      probeVisibleAt.conversion = screen.queryByTestId('report-screen-probe') !== null;
      expect(args).toEqual(['risk_check']);
    });
    vi.mocked(trackFunnel).mockImplementation((...args: unknown[]) => {
      log.push('trackFunnel');
      probeVisibleAt.funnel = screen.queryByTestId('report-screen-probe') !== null;
      expect(args).toEqual(['reg_entered']);
    });
    renderApp(['/app']);
    await submit(user);
    await screen.findByTestId('report-screen-probe');

    expect(trackConversion).toHaveBeenCalledTimes(1);
    expect(trackConversion).toHaveBeenCalledWith('risk_check');
    expect(trackFunnel).toHaveBeenCalledTimes(1);
    expect(trackFunnel).toHaveBeenCalledWith('reg_entered');
    expect(probeVisibleAt).toEqual({ conversion: false, funnel: false });
    // check_started -> createReport -> risk_check -> reg_entered -> report_created (navigation follows)
    expect(log).toEqual([
      'event:check_started',
      'createReport',
      'trackConversion',
      'trackFunnel',
      'event:report_created',
    ]);
  });

  it('a throwing sink cannot change the user flow or the analytics calls', async () => {
    const user = userEvent.setup();
    __setAcquisitionSinkForTests({ emit: () => { throw new Error('sink down'); } });
    vi.mocked(createReport).mockResolvedValue(fixtureExactHigh);
    renderApp(['/app']);
    await submit(user);
    expect(await screen.findByTestId('report-screen-probe')).toBeInTheDocument();
    expect(trackConversion).toHaveBeenCalledWith('risk_check');
    expect(trackFunnel).toHaveBeenCalledWith('reg_entered');
  });

  it('inline persistence-degraded success: report_created persistence_mode inline_unsaved; operation id in state', async () => {
    const user = userEvent.setup();
    vi.mocked(createReport).mockResolvedValue({ ...fixtureVehiclePrediction, report_token: null });
    renderApp(['/app']);
    await submit(user);
    expect(await screen.findByTestId('probe-inline')).toHaveTextContent('yes');
    const created = events.find((e) => e.event === 'report_created')!;
    expect(created).toMatchObject({
      persistence_mode: 'inline_unsaved',
      result_kind: 'vehicle_prediction',
      match_scope: 'model_prediction',
    });
    expect(screen.getByTestId('probe-operation')).not.toHaveTextContent('');
    expect(validates(schema, created)).toBe(true);
  });

  it('check_failed carries a fixed category and stage, never the message; no report_created, no navigation', async () => {
    const user = userEvent.setup();
    vi.mocked(createReport).mockRejectedValue(new ReportApiError('rate_limited', mapErrorToMessage('rate_limited'), 429));
    renderApp(['/app']);
    await submit(user);
    await screen.findByText(mapErrorToMessage('rate_limited'));
    const failed = events.find((e) => e.event === 'check_failed')!;
    expect(failed).toMatchObject({ error_category: 'rate_limited', stage: 'create_report', entry_mode: 'fresh_check' });
    expect(events.map((e) => e.event)).toEqual(['check_started', 'check_failed']);
    expect(JSON.stringify(events)).not.toContain(mapErrorToMessage('rate_limited'));
    for (const e of events) expect(validates(schema, e)).toBe(true);
    expect(trackConversion).not.toHaveBeenCalled();
    expect(trackFunnel).not.toHaveBeenCalled();
  });

  it('a non-ReportApiError failure is category unknown', async () => {
    const user = userEvent.setup();
    vi.mocked(createReport).mockRejectedValue(new TypeError('AB12CDE exploded'));
    renderApp(['/app']);
    await submit(user);
    await screen.findByText(mapErrorToMessage('unknown'));
    expect(events.find((e) => e.event === 'check_failed')).toMatchObject({ error_category: 'unknown' });
    expect(JSON.stringify(events)).not.toContain('exploded');
  });

  it('a retry of the same unresolved operation keeps the operation id and does not announce a second start', async () => {
    const user = userEvent.setup();
    vi.mocked(createReport)
      .mockRejectedValueOnce(new ReportApiError('network_error', mapErrorToMessage('network_error')))
      .mockResolvedValueOnce(fixtureExactHigh);
    renderApp(['/app']);
    await submit(user);
    await screen.findByText(mapErrorToMessage('network_error'));
    await user.click(screen.getByRole('button', { name: /check this car/i }));
    await screen.findByTestId('report-screen-probe');

    expect(events.map((e) => e.event)).toEqual(['check_started', 'check_failed', 'report_created']);
    const ops = new Set(events.map((e) => (e as { operation_id: string }).operation_id));
    expect(ops.size).toBe(1);
    expect(screen.getByTestId('probe-operation')).toHaveTextContent([...ops][0]);
  });

  it('a later deliberate check of the same vehicle, in the same App instance, is a new operation', async () => {
    const user = userEvent.setup();
    vi.mocked(createReport).mockResolvedValue(fixtureExactHigh);
    renderApp(['/app']);
    await submit(user);
    await screen.findByTestId('report-screen-probe');
    const op1 = screen.getByTestId('probe-operation').textContent;

    await user.click(screen.getByRole('link', { name: 'back to form' }));
    await submit(user); // identical registration and postcode
    await waitFor(() => expect(screen.getByTestId('probe-operation').textContent).not.toBe(op1));
    const op2 = screen.getByTestId('probe-operation').textContent;

    expect(op1).toBeTruthy();
    expect(op2).toBeTruthy();
    expect(op1).not.toBe(op2);
    expect(createReport).toHaveBeenCalledTimes(2);
    // A completed operation does not leak its idempotency key to the next deliberate check either.
    const keys = vi.mocked(createReport).mock.calls.map((c) => c[2]);
    expect(keys[0]).not.toBe(keys[1]);
    const starts = events.filter((e) => e.event === 'check_started').map((e) => (e as { operation_id: string }).operation_id);
    expect(starts).toEqual([op1, op2]);
    expect(events.filter((e) => e.event === 'report_created')).toHaveLength(2);
  });
});
