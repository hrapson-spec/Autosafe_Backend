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

  it('states the pass/fail likelihood truthfully on both sides of 50%', () => {
    const { unmount } = render(
      <ReportResult report={fixtureVehiclePrediction} onReminder={vi.fn()} onGarage={vi.fn()} />
    );
    // 12% predicted failure: a pass genuinely is more likely.
    expect(screen.getByText(/A pass is still more likely/)).toBeInTheDocument();
    unmount();

    render(
      <ReportResult
        report={{
          ...fixtureVehiclePrediction,
          risk: { ...fixtureVehiclePrediction.risk, failure_risk: 0.62 },
        }}
        onReminder={vi.fn()}
        onGarage={vi.fn()}
      />
    );
    // 62% predicted failure: never claim a pass is more likely.
    expect(screen.queryByText(/A pass is still more likely/)).not.toBeInTheDocument();
    expect(screen.getByText(/A fail is more likely than not/)).toBeInTheDocument();
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
    expect(
      screen.getByRole('heading', {
        name: 'Your car’s predicted chance of failing its next MOT',
      })
    ).toBeInTheDocument();
    expect(screen.getByText('12%')).toBeInTheDocument();
    // Low band (<30%): visible status pill plus a combined screen-reader label,
    // replacing the removed progress bar.
    expect(screen.getByText('Low chance')).toBeInTheDocument();
    expect(screen.getByText('12%, Low chance')).toBeInTheDocument();
    expect(screen.getByText('That’s about 1 in 8.')).toBeInTheDocument();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Check these first' })).toBeInTheDocument();
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
    // Medium band (30–49%) pill + combined screen-reader label; no progress bar.
    expect(screen.getByText('Medium chance')).toBeInTheDocument();
    expect(screen.getByText('36%, Medium chance')).toBeInTheDocument();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
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

  it('renders zero risk as a finite human frequency in the summary', () => {
    const zeroRiskReport = {
      ...fixtureVehiclePrediction,
      risk: {
        ...fixtureVehiclePrediction.risk,
        failure_risk: 0,
      },
    };

    render(<ReportResult report={zeroRiskReport} onReminder={vi.fn()} onGarage={vi.fn()} />);

    expect(screen.getByText('0%')).toBeInTheDocument();
    expect(screen.getByText('That’s a chance of fewer than 1 in 100.')).toBeInTheDocument();
    expect(screen.queryByText(/Infinity|NaN/i)).not.toBeInTheDocument();
  });

  it('expresses the predicted chance as the nearest simple X-in-Y fraction', () => {
    const report = {
      ...fixtureVehiclePrediction,
      risk: { ...fixtureVehiclePrediction.risk, failure_risk: 0.39 },
    };

    render(<ReportResult report={report} onReminder={vi.fn()} onGarage={vi.fn()} />);

    expect(screen.getByText('39%')).toBeInTheDocument();
    expect(screen.getByText('That’s about 2 in 5.')).toBeInTheDocument();
    // 39% sits in the medium band.
    expect(screen.getByText('Medium chance')).toBeInTheDocument();
    expect(screen.getByText('39%, Medium chance')).toBeInTheDocument();
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
    expect(screen.getByText('Prepare before your MOT')).toBeInTheDocument();
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
      screen.getByRole('link', { name: 'Start the 10-minute checklist' })
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
