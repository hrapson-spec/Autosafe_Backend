import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation, useParams } from 'react-router-dom';
import { HelmetProvider } from 'react-helmet-async';
import type { PublicStats } from './types';
import App from './App';
import { ReportApiError, mapErrorToMessage } from './services/errorMessages';
import { fixtureExactHigh, fixtureUnavailableDegraded } from './fixtures/reportResponses';

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
  trackReportView: vi.fn(),
}));

vi.mock('./components/ReportScreen', () => ({
  default: function ReportScreenProbe() {
    const { token } = useParams();
    const location = useLocation();
    const state = (location.state ?? {}) as { postcode?: string; inlineReport?: unknown };
    return (
      <div data-testid="report-screen-probe">
        <span data-testid="probe-token">{token}</span>
        <span data-testid="probe-postcode">{state.postcode ?? ''}</span>
        <span data-testid="probe-inline">{state.inlineReport ? 'yes' : 'no'}</span>
      </div>
    );
  },
}));

import { createReport } from './services/reportApi';
import { getPublicStats } from './services/autosafeApi';
import { trackConversion, trackFunnel } from './utils/analytics';

const DEFAULT_STATS: PublicStats = { total_checks: 1000, checks_this_month: 1, mot_records: '142M+' };

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
  vi.mocked(getPublicStats).mockResolvedValue(DEFAULT_STATS);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('App: home page rendering', () => {
  it.each([['/'], ['/app']])('renders the check form at %s', async (path) => {
    renderApp([path]);

    expect(await screen.findByText('Fix it before they find it.')).toBeInTheDocument();
    expect(screen.getByLabelText('Registration Number', { exact: false })).toBeInTheDocument();
    expect(screen.getByLabelText('Post Code', { exact: false })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /check this car/i })).toBeInTheDocument();
  });
});

describe('App: submit success', () => {
  it('navigates to /app/report/<token> and fires conversion + funnel events', async () => {
    const user = userEvent.setup();
    vi.mocked(createReport).mockResolvedValue(fixtureExactHigh);

    renderApp(['/app']);

    await user.type(screen.getByLabelText('Registration Number', { exact: false }), 'AB12CDE');
    await user.type(screen.getByLabelText('Post Code', { exact: false }), 'SW1A 1AA');
    await user.click(screen.getByRole('button', { name: /check this car/i }));

    expect(await screen.findByTestId('report-screen-probe')).toBeInTheDocument();
    expect(screen.getByTestId('probe-token')).toHaveTextContent(fixtureExactHigh.report_token as string);
    expect(screen.getByTestId('probe-postcode')).toHaveTextContent('SW1A 1AA');
    expect(screen.getByTestId('probe-inline')).toHaveTextContent('no');

    expect(createReport).toHaveBeenCalledWith('AB12CDE', 'SW1A 1AA');
    expect(trackConversion).toHaveBeenCalledWith('risk_check');
    expect(trackFunnel).toHaveBeenCalledWith('reg_entered');
  });

  it('routes to /app/report/unsaved with the inline report when report_token is null (persistence-degraded)', async () => {
    const user = userEvent.setup();
    vi.mocked(createReport).mockResolvedValue(fixtureUnavailableDegraded);

    renderApp(['/app']);

    await user.type(screen.getByLabelText('Registration Number', { exact: false }), 'ZZ99ZZZ');
    await user.type(screen.getByLabelText('Post Code', { exact: false }), 'SW1A 1AA');
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
    const postcodeInput = screen.getByLabelText('Post Code', { exact: false });
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

    // checks_this_month is kept under 1000 deliberately: toLocaleString()
    // would insert a thousands separator whose exact character depends on
    // locale, which this assertion doesn't want to depend on.
    resolveStats({ total_checks: 999, checks_this_month: 421, mot_records: '142M+' });

    // Proves the resolved stats actually flowed through and re-rendered
    // HomePage before we assert the input survived that re-render.
    await screen.findByText(/421 vehicles checked this month/);

    expect(regInput).toHaveValue('AB12CDE');
  });
});
