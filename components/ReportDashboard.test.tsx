import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  fixtureExactHigh,
  fixtureModelAverageLow,
  fixtureUnavailableDegraded,
  fixtureVehiclePrediction,
} from '../fixtures/reportResponses';
import {
  submitGarageLead,
  submitReportEmail,
} from '../services/autosafeApi';
import { trackFunnel } from '../utils/analytics';
import { buildScopeDisclosure, sampleSizeBadge } from './ReportCopy';
import ReportDashboard from './ReportDashboard';

vi.mock('../services/autosafeApi', () => ({
  submitReportEmail: vi.fn().mockResolvedValue({ success: true }),
  submitGarageLead: vi.fn().mockResolvedValue({ success: true, lead_id: 'lead_1', message: 'ok' }),
  submitMotReminder: vi.fn().mockResolvedValue({ success: true, message: 'ok' }),
}));

vi.mock('../utils/analytics', () => ({
  trackConversion: vi.fn(),
  trackFunnel: vi.fn(),
}));

const EXPERIMENTS_KEY = 'autosafe_experiments';
const submitGarageLeadMock = vi.mocked(submitGarageLead);
const submitReportEmailMock = vi.mocked(submitReportEmail);
const trackFunnelMock = vi.mocked(trackFunnel);

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
  vi.clearAllMocks();
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    value: vi.fn(),
    configurable: true,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('ReportDashboard final layout', () => {
  it('shows the honest comparison result while keeping methodology collapsed', async () => {
    const user = userEvent.setup();

    render(<ReportDashboard report={fixtureExactHigh} postcode="SW1A 1AA" onReset={vi.fn()} />);

    expect(screen.getByTestId('comparison-result')).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'This result isn’t a prediction for AB12 CDE' })
    ).toBeInTheDocument();
    expect(screen.queryByTestId('vehicle-prediction-result')).not.toBeInTheDocument();

    expect(screen.getByRole('heading', { name: 'Vehicle Report' })).toBeInTheDocument();
    expect(
      screen.getByText('2021 TESTMAKE TESTMODEL • 62,411 miles (last recorded MOT mileage)')
    ).toBeInTheDocument();
    expect(screen.getByText('Last recorded MOT mileage: 62,411 miles on 18 Apr 2026')).toBeInTheDocument();

    expect(screen.queryByRole('heading', { name: 'Evidence Quality' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Evidence Summary' })).not.toBeInTheDocument();
    expect(screen.queryByText('Population average')).not.toBeInTheDocument();
    expect(screen.queryByText(/Based on 148M\+ recorded DVSA MOT tests/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Comparable-Vehicle Cost Context/i)).not.toBeInTheDocument();

    const result = screen.getByTestId('comparison-result');
    expect(within(result).queryByText(/1,842 recorded MOT tests/i)).not.toBeInTheDocument();
    expect(within(result).queryByText(/18%|9%|7%/)).not.toBeInTheDocument();

    const methodologySummary = screen.getByText('How this result was calculated');
    const methodology = methodologySummary.closest('details');
    expect(methodology).not.toBeNull();
    expect(methodology).not.toHaveAttribute('open');
    expect(
      within(methodology as HTMLDetailsElement).getByText(buildScopeDisclosure(fixtureExactHigh), { exact: true })
    ).not.toBeVisible();
    expect(
      within(methodology as HTMLDetailsElement).getByText(sampleSizeBadge(fixtureExactHigh), { exact: true })
    ).not.toBeVisible();

    await user.click(methodologySummary);

    expect(methodology).toHaveAttribute('open');
    expect(
      within(methodology as HTMLDetailsElement).getByText(buildScopeDisclosure(fixtureExactHigh), { exact: true })
    ).toBeVisible();
    expect(
      within(methodology as HTMLDetailsElement).getByText(sampleSizeBadge(fixtureExactHigh), { exact: true })
    ).toBeVisible();
    await waitFor(() => expect(trackFunnelMock).toHaveBeenCalledWith('accordion_opened'));
  });

  it('renders prediction copy only for the explicit vehicle-prediction contract state', () => {
    render(<ReportDashboard report={fixtureVehiclePrediction} onReset={vi.fn()} />);

    expect(screen.getByTestId('vehicle-prediction-result')).toBeInTheDocument();
    expect(screen.getByText('AutoSafe prediction for AB12 CDE')).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'Your car’s predicted chance of failing its next MOT' })
    ).toBeInTheDocument();
    expect(screen.queryByTestId('comparison-result')).not.toBeInTheDocument();
  });

  it('preserves sharing and reset without exposing the postcode', async () => {
    const user = userEvent.setup();
    const writeText = stubClipboard();
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    const onReset = vi.fn();

    const { container } = render(
      <ReportDashboard report={fixtureExactHigh} postcode="SW1A 1AA" onReset={onReset} />
    );

    await user.click(screen.getByRole('button', { name: /copy report link/i }));
    expect(writeText).toHaveBeenCalledWith(fixtureExactHigh.share_url);

    await user.click(screen.getByRole('button', { name: /share on whatsapp/i }));
    expect(openSpy).toHaveBeenCalledTimes(1);
    const [whatsAppUrl] = openSpy.mock.calls[0] as [string];
    expect(whatsAppUrl).toContain(encodeURIComponent(fixtureExactHigh.share_url as string));

    await user.click(screen.getByRole('button', { name: /check another vehicle/i }));
    expect(onReset).toHaveBeenCalledTimes(1);
    expect(container.innerHTML).not.toContain('SW1A 1AA');
    expect(whatsAppUrl.toUpperCase()).not.toContain('SW1A');
  });

  it('keeps the degraded demo banner and honest disabled-share state', () => {
    render(<ReportDashboard report={fixtureUnavailableDegraded} onReset={vi.fn()} />);

    expect(screen.getByTestId('demo-banner')).toHaveTextContent(
      'Demonstration data — not a real vehicle lookup.'
    );
    expect(screen.getByTestId('comparison-result')).toBeInTheDocument();

    const shareButtons = screen.getAllByRole('button', {
      name: /sharing isn't available for this report/i,
    });
    expect(shareButtons).toHaveLength(2);
    shareButtons.forEach((button) => expect(button).toBeDisabled());
    expect(screen.getByText("Sharing isn't available for this report")).toBeInTheDocument();
  });

  it('scrolls to and focuses the first email input from reminder actions without double-counting sticky analytics', async () => {
    const user = userEvent.setup();
    render(<ReportDashboard report={fixtureModelAverageLow} onReset={vi.fn()} />);

    const emailInputs = screen.getAllByPlaceholderText('your@email.com');
    expect(emailInputs).toHaveLength(2);

    await user.click(screen.getByRole('button', { name: 'Set an MOT reminder' }));
    expect(emailInputs[0]).toHaveFocus();

    emailInputs[0].blur();
    trackFunnelMock.mockClear();
    await user.click(screen.getByRole('button', { name: 'Get this report & reminders' }));
    expect(emailInputs[0]).toHaveFocus();
    expect(trackFunnelMock.mock.calls.filter(([event]) => event === 'sticky_cta_clicked')).toHaveLength(1);
  });

  it('opens the garage modal, tracks the action once, and keeps a visible success confirmation', async () => {
    const user = userEvent.setup();
    render(
      <ReportDashboard report={fixtureExactHigh} postcode="SW1A 1AA" onReset={vi.fn()} />
    );

    await user.click(screen.getByRole('button', { name: 'Find a local garage' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(trackFunnelMock.mock.calls.filter(([event]) => event === 'garage_cta_clicked')).toHaveLength(1);

    await new Promise((resolve) => setTimeout(resolve, 150));
    await user.type(screen.getByLabelText(/^email/i), 'driver@example.com');
    await user.click(screen.getByText(/I agree AutoSafe may share my details/i));
    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', {
        name: /find garages near me|find a local garage|book a pre-mot check/i,
      })
    );

    await waitFor(() => expect(submitGarageLeadMock).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('Request received')).toBeVisible();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('submits the email report but filters a retired results-page assignment from metadata', async () => {
    const user = userEvent.setup();
    localStorage.setItem(EXPERIMENTS_KEY, JSON.stringify({ results_page_v1: 'treatment' }));
    render(<ReportDashboard report={fixtureExactHigh} onReset={vi.fn()} />);

    const emailReportLabel = screen.getByText('Email me this report');
    const emailReportForm = emailReportLabel.closest('form');
    expect(emailReportForm).not.toBeNull();

    await user.type(
      within(emailReportForm as HTMLFormElement).getByPlaceholderText('your@email.com'),
      'owner@example.com'
    );
    await user.click(within(emailReportForm as HTMLFormElement).getByRole('button', { name: 'Send' }));

    await waitFor(() => expect(submitReportEmailMock).toHaveBeenCalledTimes(1));
    expect(submitReportEmailMock.mock.calls[0][0]).not.toHaveProperty('experiment_variant');
    expect(await screen.findByText('Report sent! Check your inbox.')).toBeVisible();
  });

  it('does not emit analytics events that belonged to the retired experiment branches', () => {
    render(<ReportDashboard report={fixtureExactHigh} onReset={vi.fn()} />);

    const eventNames = trackFunnelMock.mock.calls.map(([event]) => event);
    expect(eventNames).not.toContain('recommendation_viewed');
    expect(eventNames).not.toContain('motivator_card_viewed');
    expect(eventNames).not.toContain('secondary_cta_clicked');
  });
});
