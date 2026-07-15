import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { HelmetProvider } from 'react-helmet-async';
import type { ReportV2 } from '../types';
import ReportScreen from './ReportScreen';
import type { ReportUnavailableReason } from './ReportUnavailable';
import { ReportApiError, mapErrorToMessage } from '../services/errorMessages';
import { fixtureExactHigh, fixtureUnavailableDegraded } from '../fixtures/reportResponses';

// ---------------------------------------------------------------------------
// Module mocks
//
// - services/reportApi's getReport is mocked so this file controls exactly
//   what "fetching" resolves/rejects to.
// - components/ReportDashboard is mocked with a probe. ReportDashboard is
//   owned by a parallel agent and is mid-rewrite to the frozen
//   { report: ReportV2; postcode?: string; onReset } seam this file already
//   codes ReportScreen against -- mocking it here decouples this test file
//   from that in-flight rewrite. FLAGGED per the wave's invariants.
// ---------------------------------------------------------------------------
vi.mock('../services/reportApi', () => ({
  getReport: vi.fn(),
}));

vi.mock('../utils/analytics', () => ({
  trackReportView: vi.fn(),
}));

vi.mock('./ReportDashboard', () => ({
  default: (props: { report: ReportV2; postcode?: string; onReset: () => void }) => (
    <div data-testid="dashboard-probe">
      <span data-testid="dashboard-make">{props.report.vehicle.make}</span>
      <span data-testid="dashboard-model">{props.report.vehicle.model}</span>
      <span data-testid="dashboard-postcode">{props.postcode ?? ''}</span>
      <button type="button" onClick={props.onReset}>reset</button>
    </div>
  ),
}));

import { getReport } from '../services/reportApi';
import { trackReportView } from '../utils/analytics';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderAtToken(token: string, state?: { postcode?: string; inlineReport?: ReportV2 }) {
  return render(
    <HelmetProvider>
      <MemoryRouter initialEntries={[{ pathname: `/app/report/${token}`, state }]}>
        <Routes>
          <Route path="/app/report/:token" element={<ReportScreen />} />
          <Route path="/app" element={<div>HOME</div>} />
        </Routes>
      </MemoryRouter>
    </HelmetProvider>
  );
}

describe('ReportScreen: loading', () => {
  it('shows an accessible loading state while the fetch is pending', async () => {
    let resolveGetReport!: (r: ReportV2) => void;
    vi.mocked(getReport).mockReturnValue(
      new Promise<ReportV2>((resolve) => {
        resolveGetReport = resolve;
      })
    );

    renderAtToken('sometoken');

    expect(await screen.findByRole('status')).toBeInTheDocument();
    expect(screen.queryByTestId('dashboard-probe')).not.toBeInTheDocument();

    // Drain the pending promise so it doesn't resolve after unmount.
    resolveGetReport(fixtureExactHigh);
    await screen.findByTestId('dashboard-probe');
  });
});

describe('ReportScreen: success', () => {
  it('fetches by token, renders the dashboard, and fires report_viewed', async () => {
    vi.mocked(getReport).mockResolvedValue(fixtureExactHigh);

    renderAtToken(fixtureExactHigh.report_token as string, { postcode: 'SW1A 1AA' });

    expect(await screen.findByTestId('dashboard-probe')).toBeInTheDocument();
    expect(screen.getByTestId('dashboard-make')).toHaveTextContent(fixtureExactHigh.vehicle.make);
    expect(screen.getByTestId('dashboard-model')).toHaveTextContent(fixtureExactHigh.vehicle.model);
    expect(screen.getByTestId('dashboard-postcode')).toHaveTextContent('SW1A 1AA');

    expect(getReport).toHaveBeenCalledWith(fixtureExactHigh.report_token);
    expect(trackReportView).toHaveBeenCalledTimes(1);
    expect(trackReportView).toHaveBeenCalledWith(
      fixtureExactHigh.vehicle.make,
      fixtureExactHigh.vehicle.model,
      expect.any(Number)
    );
  });
});

describe('ReportScreen: inline (degraded-token) path', () => {
  it('renders the inline report at the unsaved token without calling getReport', async () => {
    renderAtToken('unsaved', { postcode: 'SW1A 1AA', inlineReport: fixtureUnavailableDegraded });

    expect(await screen.findByTestId('dashboard-probe')).toBeInTheDocument();
    expect(screen.getByTestId('dashboard-make')).toHaveTextContent(fixtureUnavailableDegraded.vehicle.make);
    expect(screen.getByTestId('dashboard-postcode')).toHaveTextContent('SW1A 1AA');
    expect(getReport).not.toHaveBeenCalled();
  });
});

describe('ReportScreen: error variants', () => {
  const cases: Array<[ReportApiError['code'], ReportUnavailableReason, string]> = [
    ['report_not_found', 'not_found', "That report link isn't valid"],
    ['report_expired', 'expired', 'This report link has expired'],
    ['storage_unavailable', 'error', "We couldn't load this report"],
    ['network_error', 'error', "We couldn't load this report"],
  ];

  it.each(cases)('maps %s to the correct ReportUnavailable copy with no vehicle data', async (code, _reason, heading) => {
    vi.mocked(getReport).mockRejectedValue(new ReportApiError(code, mapErrorToMessage(code), 404, 'corr123'));

    renderAtToken('sometoken');

    expect(await screen.findByText(heading)).toBeInTheDocument();
    expect(screen.queryByTestId('dashboard-probe')).not.toBeInTheDocument();
    expect(screen.queryByText(fixtureExactHigh.vehicle.make)).not.toBeInTheDocument();
    expect(screen.queryByText(fixtureExactHigh.vehicle.model)).not.toBeInTheDocument();
    expect(screen.queryByText(fixtureUnavailableDegraded.vehicle.make)).not.toBeInTheDocument();
  });
});
