import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReportV2 } from '../types';
import {
  fixtureExactHigh,
  fixturePopulationDefault,
  fixtureUnavailableDegraded,
  fixtureModelAverageLow,
  fixtureAnomalyMissing,
  fixtureLegacyEstimated2_0,
  fixtureObservedHighMileage,
} from '../fixtures/reportResponses';
import { riskPercentDisplay } from './ReportCopy';
import ReportDashboard from './ReportDashboard';

vi.mock('../services/autosafeApi', () => ({
  submitReportEmail: vi.fn().mockResolvedValue({ success: true }),
  submitGarageLead: vi.fn().mockResolvedValue({ success: true, lead_id: 'lead_1', message: 'ok' }),
  submitMotReminder: vi.fn().mockResolvedValue({ success: true, message: 'ok' }),
}));

const EXPERIMENTS_KEY = 'autosafe_experiments';

function setVariant(variant: 'control' | 'treatment') {
  localStorage.setItem(EXPERIMENTS_KEY, JSON.stringify({ results_page_v1: variant }));
}

function stubClipboard() {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
    writable: true,
  });
  return writeText;
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  // This project runs vitest with `globals: false` (vite.config.ts), so
  // Testing Library's implicit auto-cleanup -- which detects a *global*
  // afterEach -- never registers. Without this, every render() in this file
  // accumulates in document.body instead of unmounting, and later tests'
  // screen.getByTestId/getByRole queries see leftover nodes from earlier
  // fixtures/variants (surfaced here as "Found multiple elements").
  cleanup();
  vi.restoreAllMocks();
});

const VARIANTS = ['control', 'treatment'] as const;

describe.each(VARIANTS)('ReportDashboard (%s)', (variant) => {
  beforeEach(() => setVariant(variant));

  it('renders the evidence-backed narrative, components, repair estimate, and working share', async () => {
    const user = userEvent.setup();
    const writeText = stubClipboard();
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);

    const { container } = render(
      <ReportDashboard report={fixtureExactHigh} postcode="SW1A 1AA" onReset={vi.fn()} />
    );

    // Narrative (buildNarrative): risk text + mileage clause + sample size + scope, all evidence-sourced
    expect(container.textContent).toContain('recorded MOT failure rate of 12%');
    expect(container.textContent).toContain('using a mileage band selected from 62,411 miles recorded at its MOT');
    expect(container.textContent).toContain('1,842 recorded MOT tests');
    expect(container.textContent).toContain(
      'This comparison uses TESTMAKE TESTMODEL records in the matched age and mileage bands.'
    );

    // Components: real item labels + the ReportCopy caption
    expect(screen.getByText('Brakes')).toBeInTheDocument();
    expect(screen.getByText('Tyres')).toBeInTheDocument();
    expect(screen.getByText('Suspension')).toBeInTheDocument();
    expect(container.textContent).toContain(
      'Indicative estimates: components most often linked to MOT failure among similar vehicles'
    );

    // Repair estimate card: control-only by design (unchanged from the
    // original layout -- treatment never had a standalone repair card; its
    // repair-cost framing flows through MotivatorCard's COST_ESTIMATE type
    // instead, which only activates for certain recommendation branches).
    if (variant === 'control') {
      expect(container.textContent).toContain('£180 - £460');
      expect(container.textContent).toContain('Indicative repair-cost range for similar vehicles, not a quote.');
    }

    // Share: Copy Link writes share_url exactly
    await user.click(screen.getByRole('button', { name: /copy report link/i }));
    expect(writeText).toHaveBeenCalledWith(fixtureExactHigh.share_url);

    // Share: WhatsApp opens with the encoded share_url embedded in the message
    await user.click(screen.getByRole('button', { name: /share on whatsapp/i }));
    expect(openSpy).toHaveBeenCalledTimes(1);
    const [waUrl] = openSpy.mock.calls[0] as [string];
    expect(waUrl).toContain(encodeURIComponent(fixtureExactHigh.share_url as string));

    // No postcode anywhere in the rendered DOM or in the constructed share link
    expect(container.innerHTML).not.toContain('SW1A 1AA');
    expect(waUrl.toUpperCase()).not.toContain('SW1A');
  });

  it('hides the components and repair cards and shows an honest sample-size/scope state for a population-default report', () => {
    const { container } = render(
      <ReportDashboard report={fixturePopulationDefault} onReset={vi.fn()} />
    );

    expect(screen.queryByText('Common Faults To Watch')).not.toBeInTheDocument();
    expect(screen.queryByText('Comparable-Vehicle Cost Context')).not.toBeInTheDocument();
    expect(container.textContent).toContain('Vehicle-matched sample unavailable');
    expect(container.textContent).toContain(
      "We don't have enough TESTMAKE OBSCUREMODEL group data, so this is the dataset-wide reference rate."
    );
    // mileage.source === 'missing' -- no fabricated mileage slot anywhere ("no '— miles'")
    expect(container.textContent).not.toMatch(/\bmiles\b/i);
  });

  it('disables sharing with visible microcopy and shows the demo banner for a fully-degraded report', () => {
    render(<ReportDashboard report={fixtureUnavailableDegraded} onReset={vi.fn()} />);

    const banner = screen.getByTestId('demo-banner');
    expect(banner).toHaveTextContent('Demonstration data — not a real vehicle lookup.');

    const shareButtons = screen.getAllByRole('button', { name: /sharing isn't available for this report/i });
    expect(shareButtons).toHaveLength(2);
    shareButtons.forEach(btn => expect(btn).toBeDisabled());

    expect(screen.getAllByText("Sharing isn't available for this report").length).toBeGreaterThan(0);
  });

  it('renders the population-average badge next to the displayed rate for every match scope (exact_band, age_band_only, legacy estimated)', () => {
    const badgeReports: ReportV2[] = [fixtureExactHigh, fixtureAnomalyMissing, fixtureLegacyEstimated2_0];
    badgeReports.forEach(report => {
      const { unmount } = render(<ReportDashboard report={report} onReset={vi.fn()} />);
      const badges = screen.getAllByTestId('population-badge');
      expect(badges.length).toBeGreaterThan(0);
      badges.forEach(badge => {
        expect(badge).toHaveTextContent('Population average');
        expect(badge).toHaveAttribute(
          'title',
          'A recorded failure rate for a group of similar vehicles — not a prediction for this vehicle.'
        );
      });
      unmount();
    });
  });

  it('renders the last-recorded-mileage line for an observed reading, and omits it entirely when mileage is missing', () => {
    const { unmount: unmountObserved } = render(
      <ReportDashboard report={fixtureObservedHighMileage} onReset={vi.fn()} />
    );
    expect(screen.getByText('Last recorded MOT mileage: 112,406 miles on 2 Nov 2025')).toBeInTheDocument();
    unmountObserved();

    const { container, unmount: unmountMissing } = render(
      <ReportDashboard report={fixturePopulationDefault} onReset={vi.fn()} />
    );
    expect(container.textContent).not.toContain('Last recorded MOT mileage');
    unmountMissing();
  });

  it('shows the mileage anomaly and unit-conversion suffixes for an observed-but-flagged reading', () => {
    const { container } = render(
      <ReportDashboard report={fixtureModelAverageLow} onReset={vi.fn()} />
    );
    expect(container.textContent).toContain('an inconsistent newer reading was ignored');
    expect(container.textContent).toContain('converted from kilometres');
  });

  it('presents one comparable-vehicle failure-rate number, never a renamed complement', () => {
    const cases: Array<Pick<ReportV2['risk'], 'failure_risk' | 'confidence'>> = [
      { failure_risk: 0.12, confidence: 'High' },
      { failure_risk: 0.345, confidence: 'Medium' },
      { failure_risk: 0.28, confidence: 'Very Low' }, // exercises the nearest-5 coarsening path
    ];

    cases.forEach(risk => {
      const report: ReportV2 = { ...fixtureExactHigh, risk };
      const expected = riskPercentDisplay(risk.failure_risk, risk.confidence);
      const { unmount, queryAllByTestId } = render(<ReportDashboard report={report} onReset={vi.fn()} />);

      // Every dedicated score slot is the same failure rate. The complement
      // must not be relabelled as a reliability score or pass probability.
      const scoreNodes = [
        ...queryAllByTestId('score-value'),
        ...queryAllByTestId('failure-rate-value'),
      ];
      expect(scoreNodes.length).toBeGreaterThan(0);
      scoreNodes.forEach(node => {
        const n = parseInt(node.textContent ?? '', 10);
        expect(n).toBe(expected.value);
      });

      expect(screen.queryByText(/reliability score/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/pass probability/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/MOT prediction/i)).not.toBeInTheDocument();

      unmount();
    });
  });
});
