/**
 * OA-004 review finding 1: a descendant's passive effect throws after the
 * dashboard has committed. React still runs ReportDashboard's own effect in
 * the same flush, then the error boundary replaces the view. Only
 * render_failed may be emitted; result_rendered must not be, because the
 * result was torn down (MEASUREMENT.md: runtime error boundary clear).
 */
import React, { useEffect } from 'react';
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

vi.mock('../services/reportApi', () => ({ getReport: vi.fn() }));
vi.mock('../services/autosafeApi', () => ({
  submitReportEmail: vi.fn(),
  submitGarageLead: vi.fn(),
  submitMotReminder: vi.fn(),
}));
// A descendant of the real ReportDashboard whose passive effect throws
// (stands in for an observer API missing in a child hook).
vi.mock('./StickyCta', () => ({
  default: function ThrowingStickyCta() {
    useEffect(() => {
      throw new Error('observer missing');
    }, []);
    return null;
  },
}));

import ReportScreen from './ReportScreen';
import { getReport } from '../services/reportApi';

let events: AcquisitionEvent[] = [];
beforeEach(() => {
  __resetAcquisitionStateForTests();
  events = [];
  __setAcquisitionSinkForTests({ emit: (e) => events.push(e) });
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { value: vi.fn(), configurable: true });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  __resetAcquisitionStateForTests();
});

describe('descendant passive effect throws after commit', () => {
  it.each([[false], [true]])('emits only render_failed(render), never result_rendered (StrictMode=%s)', async (strict) => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.mocked(getReport).mockResolvedValue(fixtureExactHigh);
    const tree = (
      <HelmetProvider>
        <MemoryRouter initialEntries={[{ pathname: '/app/report/tok', state: { operationId: '55555555-5555-4555-8555-555555555555' } }]}>
          <Routes>
            <Route path="/app/report/:token" element={<ReportScreen />} />
          </Routes>
        </MemoryRouter>
      </HelmetProvider>
    );
    render(strict ? <React.StrictMode>{tree}</React.StrictMode> : tree);
    expect(await screen.findByText("We couldn't load this report")).toBeInTheDocument();
    await waitFor(() => expect(events.filter((e) => e.event === 'render_failed')).toHaveLength(1));
    // Wait well past the deferred acknowledgement window.
    await new Promise((r) => setTimeout(r, 60));
    expect(events.map((e) => e.event)).toEqual(['render_failed']);
    expect(events[0]).toMatchObject({ stage: 'render', entry_mode: 'fresh_check' });
    expect(screen.queryByTestId('comparison-result')).not.toBeInTheDocument();
  });
});
