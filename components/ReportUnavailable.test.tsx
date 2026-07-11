import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ReportUnavailable, { type ReportUnavailableReason } from './ReportUnavailable';

afterEach(() => {
  cleanup();
});

function renderUnavailable(reason: ReportUnavailableReason) {
  return render(
    <MemoryRouter initialEntries={['/app/report/sometoken']}>
      <Routes>
        <Route path="/app/report/:token" element={<ReportUnavailable reason={reason} />} />
        <Route path="/app" element={<div>HOME PAGE MARKER</div>} />
      </Routes>
    </MemoryRouter>
  );
}

describe('ReportUnavailable: per-reason copy', () => {
  it('shows expired copy', () => {
    renderUnavailable('expired');

    expect(screen.getByText('This report link has expired')).toBeInTheDocument();
    expect(
      screen.getByText('Reports are kept for a limited time. Run a fresh check to get an up-to-date result.')
    ).toBeInTheDocument();
  });

  it("shows not_found copy", () => {
    renderUnavailable('not_found');

    expect(screen.getByText("That report link isn't valid")).toBeInTheDocument();
    expect(screen.getByText('The link may be incomplete or mistyped.')).toBeInTheDocument();
  });

  it('shows error copy', () => {
    renderUnavailable('error');

    expect(screen.getByText("We couldn't load this report")).toBeInTheDocument();
    expect(screen.getByText('Please check your connection and try again.')).toBeInTheDocument();
  });
});

describe('ReportUnavailable: CTA', () => {
  it.each<ReportUnavailableReason>(['expired', 'not_found', 'error'])(
    'navigates to /app when "Check a vehicle" is clicked (%s)',
    async (reason) => {
      const user = userEvent.setup();
      renderUnavailable(reason);

      await user.click(screen.getByRole('button', { name: /check a vehicle/i }));

      expect(await screen.findByText('HOME PAGE MARKER')).toBeInTheDocument();
    }
  );
});
