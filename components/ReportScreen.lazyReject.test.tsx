/**
 * OA-004: a rejected lazy dashboard chunk must not blank the app. The
 * existing ReportUnavailable error view is shown, render_failed/lazy_load is
 * reported, and neither result_rendered nor result_unavailable is emitted.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { HelmetProvider } from 'react-helmet-async';
import { fixtureExactHigh } from '../fixtures/reportResponses';
import {
  __resetAcquisitionStateForTests,
  __setAcquisitionSinkForTests,
  type AcquisitionEvent,
} from '../utils/acquisitionEvents';
import { loadEventSchema, validates } from '../utils/acquisitionSchemaCheck.testutil';

vi.mock('../services/reportApi', () => ({ getReport: vi.fn() }));
vi.mock('./ReportDashboard', () => {
  throw new Error('Failed to fetch dynamically imported module: /assets/ReportDashboard-abc123.js');
});

import ReportScreen from './ReportScreen';
import { getReport } from '../services/reportApi';

let events: AcquisitionEvent[] = [];
beforeEach(() => {
  __resetAcquisitionStateForTests();
  events = [];
  __setAcquisitionSinkForTests({ emit: (e) => events.push(e) });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  __resetAcquisitionStateForTests();
});

describe('rejected lazy chunk', () => {
  it('shows the unavailable error view, reports render_failed/lazy_load, never a result', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.mocked(getReport).mockResolvedValue(fixtureExactHigh);
    const op = '44444444-4444-4444-8444-444444444444';
    render(
      <HelmetProvider>
        <MemoryRouter initialEntries={[{ pathname: '/app/report/tok', state: { operationId: op } }]}>
          <Routes>
            <Route path="/app/report/:token" element={<ReportScreen />} />
          </Routes>
        </MemoryRouter>
      </HelmetProvider>
    );

    // Never blank: the existing error view is on screen.
    expect(await screen.findByText("We couldn't load this report")).toBeInTheDocument();
    await waitFor(() => expect(events.filter((e) => e.event === 'render_failed')).toHaveLength(1));
    expect(events[0]).toMatchObject({ event: 'render_failed', stage: 'lazy_load', entry_mode: 'fresh_check', operation_id: op });
    expect(events.filter((e) => e.event === 'result_rendered')).toHaveLength(0);
    expect(events.filter((e) => e.event === 'result_unavailable')).toHaveLength(0);
    expect(events).toHaveLength(1);
    expect(validates(loadEventSchema(), events[0])).toBe(true);
    // The chunk URL / error text is never carried.
    expect(JSON.stringify(events)).not.toContain('abc123');
    expect(JSON.stringify(events)).not.toContain('dynamically');
    spy.mockRestore();
  });
});
