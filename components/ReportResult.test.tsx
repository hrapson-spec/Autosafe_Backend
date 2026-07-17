import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  fixtureModelAverageLow,
  fixturePopulationDefault,
  fixtureUnavailableDegraded,
  fixtureVehiclePrediction,
} from '../fixtures/reportResponses';
import ReportResult from './ReportResult';

describe('ReportResult', () => {
  afterEach(() => {
    cleanup();
  });

  it('renders a vehicle prediction only for the explicit vehicle_prediction state', () => {
    render(
      <ReportResult
        report={fixtureVehiclePrediction}
        onReminder={vi.fn()}
        onGarage={vi.fn()}
      />
    );

    expect(screen.getByTestId('vehicle-prediction-result')).toBeInTheDocument();
    expect(screen.getByText('AutoSafe prediction for AB12 CDE')).toBeInTheDocument();
    expect(
      screen.getByRole('heading', {
        name: 'Your car’s predicted chance of failing its next MOT',
      })
    ).toBeInTheDocument();
    expect(screen.getByText('12%')).toBeInTheDocument();
    expect(screen.getByText('about 1 in 8')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Check these first' })).toBeInTheDocument();
    const progressbar = screen.getByRole('progressbar', { name: 'Predicted failure chance' });
    expect(progressbar).toHaveAttribute('aria-valuemin', '0');
    expect(progressbar).toHaveAttribute('aria-valuemax', '100');
    expect(progressbar).toHaveAttribute('aria-valuenow', '12');
    expect(progressbar).toHaveAttribute('aria-valuetext', '12%');
  });

  it('renders the honest comparison fallback for current reports', () => {
    render(
      <ReportResult
        report={fixtureModelAverageLow}
        onReminder={vi.fn()}
        onGarage={vi.fn()}
      />
    );

    expect(screen.getByTestId('comparison-result')).toBeInTheDocument();
    expect(
      screen.getByRole('heading', {
        name: 'This result isn’t a prediction for EF56 HIJ',
      })
    ).toBeInTheDocument();
    expect(
      screen.getByText(/TESTMAKE RAREMODEL comparison: about 1 in 3 failed their MOT/i)
    ).toBeInTheDocument();
    expect(screen.getByText('36%')).toBeInTheDocument();
    const progressbar = screen.getByRole('progressbar', { name: 'Comparison failure rate' });
    expect(progressbar).toHaveAttribute('aria-valuemin', '0');
    expect(progressbar).toHaveAttribute('aria-valuemax', '100');
    expect(progressbar).toHaveAttribute('aria-valuenow', '36');
    expect(progressbar).toHaveAttribute('aria-valuetext', '36%');
    expect(screen.queryByText(/AutoSafe prediction for/i)).not.toBeInTheDocument();
  });

  it.each([
    ['population-default', fixturePopulationDefault, 'GH78 JKL'],
    ['unavailable', fixtureUnavailableDegraded, 'ZZ99 ZZZ'],
  ])('uses dataset-reference wording for the %s comparison state', (_, report, registration) => {
    render(<ReportResult report={report} onReminder={vi.fn()} onGarage={vi.fn()} />);

    expect(
      screen.getByText(
        `The result available today is a dataset-wide reference comparison, not a prediction for ${registration}.`
      )
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Dataset-wide reference comparison: about 1 in 4 recorded MOT tests resulted in failure/i
      )
    ).toBeInTheDocument();
    expect(
      screen.queryByText(new RegExp(`${report.vehicle.make} ${report.vehicle.model} comparison`, 'i'))
    ).not.toBeInTheDocument();
  });

  it('renders zero risk as a finite human frequency in both the support line and summary', () => {
    const zeroRiskReport = {
      ...fixtureVehiclePrediction,
      risk: {
        ...fixtureVehiclePrediction.risk,
        failure_risk: 0,
      },
    };

    render(<ReportResult report={zeroRiskReport} onReminder={vi.fn()} onGarage={vi.fn()} />);

    expect(screen.getByText('0%')).toBeInTheDocument();
    expect(screen.getByText('fewer than 1 in 100')).toBeInTheDocument();
    expect(screen.getByText('That’s a chance of fewer than 1 in 100.')).toBeInTheDocument();
    expect(screen.queryByText(/Infinity|NaN/i)).not.toBeInTheDocument();
  });

  it.each([
    ['a null', null],
    ['a malformed', 'not-a-date'],
    ['an impossible', '2027-02-30T00:00:00'],
  ])('hides the due date and uses generic preparation copy for %s MOT expiry date', (_, expiryDate) => {
    const report = {
      ...fixtureVehiclePrediction,
      mot: {
        ...fixtureVehiclePrediction.mot,
        expiry_date: expiryDate,
      },
    };

    render(<ReportResult report={report} onReminder={vi.fn()} onGarage={vi.fn()} />);

    expect(screen.queryByText(/^MOT due /i)).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Get ready before your MOT' })).toBeInTheDocument();
    expect(screen.queryByText(/Invalid Date/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/2 Mar 2027|2 February/i)).not.toBeInTheDocument();
  });

  it('calls the reminder and garage actions and links to the ten-minute checklist', async () => {
    const user = userEvent.setup();
    const onReminder = vi.fn();
    const onGarage = vi.fn();

    render(
      <ReportResult
        report={fixtureVehiclePrediction}
        onReminder={onReminder}
        onGarage={onGarage}
      />
    );

    await user.click(screen.getByRole('button', { name: 'Set an MOT reminder' }));
    await user.click(screen.getByRole('button', { name: 'Find a local garage' }));

    expect(onReminder).toHaveBeenCalledTimes(1);
    expect(onGarage).toHaveBeenCalledTimes(1);
    expect(
      screen.getByRole('link', { name: 'Open the 10-minute checklist' })
    ).toHaveAttribute('href', '/app/guides/mot-checklist');
  });

  it('sorts up to three prediction priorities by descending model component value', () => {
    const report = {
      ...fixtureVehiclePrediction,
      components: {
        available: true,
        items: [
          { key: 'tyres', label: 'Tyres', risk: 0.09 },
          { key: 'body', label: 'Body & Chassis', risk: 0.04 },
          { key: 'brakes', label: 'Brakes', risk: 0.18 },
          { key: 'suspension', label: 'Suspension', risk: 0.12 },
        ],
      },
    };

    render(<ReportResult report={report} onReminder={vi.fn()} onGarage={vi.fn()} />);

    const checks = screen.getByRole('complementary', { name: 'Recommended checks' });
    const priorities = within(checks).getAllByRole('listitem');
    expect(priorities).toHaveLength(3);
    expect(priorities.map((item) => within(item).getByRole('heading').textContent)).toEqual([
      'Brakes',
      'Suspension',
      'Tyres',
    ]);
    expect(within(checks).queryByText('Body & Chassis')).not.toBeInTheDocument();
    expect(within(checks).queryByText(/%/)).not.toBeInTheDocument();
  });

  it('uses fixed universal checks for comparisons instead of cohort component rates', () => {
    render(
      <ReportResult
        report={fixtureModelAverageLow}
        onReminder={vi.fn()}
        onGarage={vi.fn()}
      />
    );

    const checks = screen.getByRole('complementary', { name: 'Universal pre-MOT checks' });
    expect(within(checks).getByText('Test lights and dashboard warnings')).toBeInTheDocument();
    expect(within(checks).getByText('Check tyres, wipers and washer fluid')).toBeInTheDocument();
    expect(within(checks).getByText('Book a garage check for anything uncertain')).toBeInTheDocument();
    expect(within(checks).queryByText('Brakes')).not.toBeInTheDocument();
    expect(within(checks).queryByText('Suspension')).not.toBeInTheDocument();
    expect(within(checks).queryByText('Steering')).not.toBeInTheDocument();
    expect(within(checks).queryByText('Body & Chassis')).not.toBeInTheDocument();
  });

  it('keeps sample counts, confidence, repair estimates, and component percentages out of both result states', () => {
    const { rerender } = render(
      <ReportResult
        report={fixtureVehiclePrediction}
        onReminder={vi.fn()}
        onGarage={vi.fn()}
      />
    );

    expect(screen.queryByText(/1,842|1842/)).not.toBeInTheDocument();
    expect(screen.queryByText(/High confidence/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/£320|£180|£460/)).not.toBeInTheDocument();
    expect(screen.queryByText('18%')).not.toBeInTheDocument();
    expect(screen.queryByText('9%')).not.toBeInTheDocument();
    expect(screen.queryByText('7%')).not.toBeInTheDocument();

    rerender(
      <ReportResult
        report={fixtureModelAverageLow}
        onReminder={vi.fn()}
        onGarage={vi.fn()}
      />
    );

    expect(screen.queryByText(/47 recorded MOT tests|47 tests/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Low confidence/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/£480|£260|£720/)).not.toBeInTheDocument();
    expect(screen.queryByText('22%')).not.toBeInTheDocument();
    expect(screen.queryByText('15%')).not.toBeInTheDocument();
    expect(screen.queryByText('8%')).not.toBeInTheDocument();
    expect(screen.queryByText('12%')).not.toBeInTheDocument();
  });
});
