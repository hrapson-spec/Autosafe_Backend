/**
 * OA-004: a delayed lazy dashboard chunk. While the chunk is pending the
 * Suspense spinner is shown and NOTHING is emitted; only once the final
 * view commits does result_rendered fire, once.
 */
import { useEffect } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor, act } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { HelmetProvider } from 'react-helmet-async';
import type { ReportV2 } from '../types';
import { fixtureExactHigh } from '../fixtures/reportResponses';
import {
  __resetAcquisitionStateForTests,
  __setAcquisitionSinkForTests,
  type AcquisitionEvent,
} from '../utils/acquisitionEvents';

const gate = vi.hoisted(() => {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
});

vi.mock('../services/reportApi', () => ({ getReport: vi.fn() }));

// The chunk does not resolve until the gate is released.
vi.mock('./ReportDashboard', async () => {
  await gate.promise;
  return {
    default: function DelayedDashboard(props: { report: ReportV2; onRendered?: () => void }) {
      useEffect(() => {
        props.onRendered?.();
      }, [props]);
      return <div data-testid="delayed-dashboard">{props.report.vehicle.make}</div>;
    },
  };
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
  __resetAcquisitionStateForTests();
});

describe('delayed lazy chunk', () => {
  it('emits nothing while the chunk is loading, then result_rendered exactly once', async () => {
    vi.mocked(getReport).mockResolvedValue(fixtureExactHigh);
    render(
      <HelmetProvider>
        <MemoryRouter
          initialEntries={[{ pathname: '/app/report/tok', state: { operationId: '33333333-3333-4333-8333-333333333333' } }]}
        >
          <Routes>
            <Route path="/app/report/:token" element={<ReportScreen />} />
          </Routes>
        </MemoryRouter>
      </HelmetProvider>
    );

    // API has returned (200) but the chunk has not: spinner only.
    await waitFor(() => expect(getReport).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId('delayed-dashboard')).not.toBeInTheDocument();
    expect(events).toHaveLength(0);

    await act(async () => {
      gate.release();
      await gate.promise;
    });
    expect(await screen.findByTestId('delayed-dashboard')).toBeInTheDocument();
    await waitFor(() => expect(events.filter((e) => e.event === 'result_rendered')).toHaveLength(1));
    expect(events).toHaveLength(1);
  });
});
