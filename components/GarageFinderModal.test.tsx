import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { fixtureExactHigh, fixturePopulationDefault } from '../fixtures/reportResponses';
import { submitGarageLead } from '../services/autosafeApi';
import type { GarageLeadSubmission } from '../types';
import GarageFinderModal from './GarageFinderModal';

vi.mock('../services/autosafeApi', () => ({
  submitGarageLead: vi.fn().mockResolvedValue({ success: true, lead_id: 'lead_1', message: 'ok' }),
}));

const submitGarageLeadMock = vi.mocked(submitGarageLead);

async function fillAndSubmit(user: ReturnType<typeof userEvent.setup>) {
  // GarageFinderModal schedules a one-shot setTimeout(..., 100) on open to
  // auto-focus the email field (pre-existing behaviour, unchanged by this
  // pass). Left unawaited, that timer can fire *during* userEvent's typing
  // into a later field (postcode) and yank focus back to email mid-keystroke,
  // corrupting both fields and silently failing form validation --
  // order-dependent flakiness, not a real assertion failure. Waiting past
  // the timer's fire point first makes the sequence deterministic.
  await new Promise(resolve => setTimeout(resolve, 150));

  await user.type(screen.getByLabelText(/^email/i), 'driver@example.com');
  await user.type(screen.getByLabelText(/^postcode/i), 'SW1A 1AA');
  // Step 2 (including consent) only reveals once email+postcode are valid.
  await user.click(screen.getByText(/I agree AutoSafe may share my details/i));
  await user.click(
    screen.getByRole('button', { name: /find garages near me|book a pre-mot check|reduce your failure risk/i })
  );
}

function submittedPayload(): GarageLeadSubmission {
  expect(submitGarageLeadMock).toHaveBeenCalledTimes(1);
  return submitGarageLeadMock.mock.calls[0][0];
}

describe('GarageFinderModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    // vite.config.ts runs vitest with `globals: false`, so Testing
    // Library's auto-cleanup (which looks for a *global* afterEach) never
    // registers -- explicit cleanup() is required or renders accumulate in
    // document.body across tests. See ReportDashboard.test.tsx for the same note.
    cleanup();
  });

  it('omits the mileage key (not undefined/null) when effective_value is null, but still includes mileage_source', async () => {
    const user = userEvent.setup();
    render(
      <GarageFinderModal isOpen onClose={vi.fn()} onSubmitSuccess={vi.fn()} report={fixturePopulationDefault} />
    );

    await fillAndSubmit(user);

    const payload = submittedPayload();
    expect('mileage' in payload.vehicle).toBe(false);
    expect(payload.vehicle.mileage_source).toBe('missing');
    expect(payload.vehicle.year).toBeNull();
  });

  it('includes the mileage key when effective_value is present', async () => {
    const user = userEvent.setup();
    render(<GarageFinderModal isOpen onClose={vi.fn()} onSubmitSuccess={vi.fn()} report={fixtureExactHigh} />);

    await fillAndSubmit(user);

    const payload = submittedPayload();
    expect(payload.vehicle.mileage).toBe(62411);
    expect(payload.vehicle.mileage_source).toBe('observed_mot');
  });

  it('derives reliability_score as 100 - Math.round(failure_risk * 100), and passes failure_risk through raw', async () => {
    const user = userEvent.setup();
    render(<GarageFinderModal isOpen onClose={vi.fn()} onSubmitSuccess={vi.fn()} report={fixtureExactHigh} />);

    await fillAndSubmit(user);

    const payload = submittedPayload();
    const expected = 100 - Math.round(fixtureExactHigh.risk.failure_risk * 100);
    expect(payload.risk_data.reliability_score).toBe(expected);
    expect(payload.risk_data.failure_risk).toBe(fixtureExactHigh.risk.failure_risk);
  });

  it('never includes a registration key anywhere in the payload (backend LeadSubmission has none)', async () => {
    const user = userEvent.setup();
    render(<GarageFinderModal isOpen onClose={vi.fn()} onSubmitSuccess={vi.fn()} report={fixtureExactHigh} />);

    await fillAndSubmit(user);

    const payload = submittedPayload();
    expect('registration' in payload).toBe(false);
    expect('registration' in payload.vehicle).toBe(false);
  });

  it('sends an empty top_risks array when components are unavailable, and shows the honest no-data state (not a fabricated "healthy" claim)', async () => {
    const user = userEvent.setup();
    render(
      <GarageFinderModal isOpen onClose={vi.fn()} onSubmitSuccess={vi.fn()} report={fixturePopulationDefault} />
    );

    expect(screen.getByText(/no component breakdown is available/i)).toBeInTheDocument();
    expect(screen.queryByText(/your car looks healthy/i)).not.toBeInTheDocument();

    await fillAndSubmit(user);

    const payload = submittedPayload();
    expect(payload.risk_data.top_risks).toEqual([]);
  });

  it('sends the top 3 highest-risk component keys, ranked by magnitude, when components are available', async () => {
    const user = userEvent.setup();
    render(<GarageFinderModal isOpen onClose={vi.fn()} onSubmitSuccess={vi.fn()} report={fixtureExactHigh} />);

    await fillAndSubmit(user);

    const payload = submittedPayload();
    // fixtureExactHigh items: brakes .18, tyres .09, suspension .07 (already <=3, already descending)
    expect(payload.risk_data.top_risks).toEqual(['brakes', 'tyres', 'suspension']);
  });

  it('seeds the postcode field from the postcode prop but the field remains user-editable', () => {
    render(
      <GarageFinderModal
        isOpen
        onClose={vi.fn()}
        onSubmitSuccess={vi.fn()}
        report={fixtureExactHigh}
        postcode="EC1A 1BB"
      />
    );
    expect(screen.getByLabelText(/^postcode/i)).toHaveValue('EC1A 1BB');
  });
});
