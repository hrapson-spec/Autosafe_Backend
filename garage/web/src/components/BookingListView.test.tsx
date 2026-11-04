import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { BookingListView } from './BookingListView';
import { demoBookings } from '../mocks/fixtures';
import type { Booking } from '../api/types';

const renderList = () =>
  render(<MemoryRouter><BookingListView bookings={demoBookings('2026-10-26')} linkFor={(b) => `/b/${b.id}`} /></MemoryRouter>);

describe('BookingListView', () => {
  it('lists ranked MOTs by the service-supplied percentage, showing percent and band as given', () => {
    renderList();
    const ranked = within(screen.getByRole('region', { name: /highest estimate first/i }));
    const rows = ranked.getAllByTestId('booking-row');
    expect(rows.map((r) => r.getAttribute('data-reg'))).toEqual(['DEMO01', 'DEMO02', 'DEMO04', 'DEMO12', 'DEMO03']);
    expect(within(rows[0]).getByText('45%')).toBeInTheDocument();
    expect(within(rows[0]).getByText('High')).toBeInTheDocument();
    expect(within(rows[1]).getByText('30%')).toBeInTheDocument();
    expect(within(rows[1]).getByText('Medium')).toBeInTheDocument();
    expect(within(rows[4]).getByText('10%')).toBeInTheDocument();
    expect(within(rows[4]).getByText('Low')).toBeInTheDocument();
  });
  it('displays whatever band the service returned, even one a client would not have chosen', () => {
    const bookings = demoBookings('2026-10-26');
    bookings[0].assessment = { ...bookings[0].assessment!, result: { percent_rounded: 5, band: 'high' } };
    render(<MemoryRouter><BookingListView bookings={bookings} linkFor={() => '/x'} /></MemoryRouter>);
    const row = screen.getAllByTestId('booking-row').find((r) => r.getAttribute('data-reg') === 'DEMO01')!;
    expect(within(row).getByText('5%')).toBeInTheDocument();
    expect(within(row).getByText('High')).toBeInTheDocument();
  });
  it('states the estimate wording above the ranked list', () => {
    renderList();
    expect(screen.getByText(/estimated chance of failing or needing rectification during the MOT, rounded by the service/i)).toBeInTheDocument();
  });
  it('shows retests, service-only, unsupported and cancelled in their own sections without percentages', () => {
    renderList();
    for (const [name, reg] of [[/^Retests/, 'DEMO09'], [/^Service only/, 'DEMO10'], [/^Unsupported vehicles/, 'DEMO07'], [/^Cancelled/, 'DEMO13']] as const) {
      const sec = within(screen.getByRole('region', { name }));
      expect(sec.getByText(reg)).toBeInTheDocument();
      expect(sec.queryByTestId('estimate')).toBeNull();
    }
    const ranked = within(screen.getByRole('region', { name: /highest estimate first/i }));
    for (const reg of ['DEMO09', 'DEMO10', 'DEMO13']) expect(ranked.queryByText(reg)).toBeNull();
  });
  it('labels service + MOT bookings and ranks them', () => {
    renderList();
    const row = screen.getAllByTestId('booking-row').find((r) => r.getAttribute('data-reg') === 'DEMO12')!;
    expect(within(row).getByText('Service + MOT')).toBeInTheDocument();
    expect(within(row).getByText('20%')).toBeInTheDocument();
  });
  it('shows every assessment state with its reason text', () => {
    renderList();
    expect(screen.getByText('Pending')).toBeInTheDocument();
    expect(screen.getByText('Unavailable')).toBeInTheDocument();
    expect(screen.getByText('Failed')).toBeInTheDocument();
    expect(screen.getAllByText('Unsupported').length).toBeGreaterThan(0);
    expect(screen.getByText(/No earlier MOT test is recorded/)).toBeInTheDocument();
    expect(screen.getByText(/could not be retrieved/)).toBeInTheDocument();
    expect(screen.getByText(/did not complete/)).toBeInTheDocument();
    expect(screen.getByText(/Fetching recorded MOT history/)).toBeInTheDocument();
  });
  it('links each registration to its briefing and marks a rescheduled booking', () => {
    const bookings = demoBookings('2026-10-26');
    bookings[1] = { ...bookings[1], rescheduled: true, original_appointment_date: '2026-10-20' };
    render(<MemoryRouter><BookingListView bookings={bookings} linkFor={(b) => `/b/${b.id}`} /></MemoryRouter>);
    expect(screen.getByRole('link', { name: 'DEMO01' })).toHaveAttribute('href', '/b/demo-1');
    expect(screen.getByText(/Moved from Tue 20 Oct/)).toBeInTheDocument();
  });
});

const bandCase = (registration: string, ranking: boolean, key: number | null): Booking => {
  const b = demoBookings('2026-10-26')[0];
  return { ...b, id: registration, registration, assessment: { ...b.assessment!, rank_key: key,
    // Even a stale numeric payload must never be rendered by a bands-only policy.
    result: { band: 'high', percent_rounded: 95 },
    display_policy: { ...b.assessment!.display_policy!, display_state: 'bands', provisional: false,
      operational_qualified: true, model_version: 'synthetic-policy-shape-only', suppression_reason: null, ranking_enabled: ranking } } };
};
const show = (bookings: Booking[]) => render(<MemoryRouter><BookingListView bookings={bookings} linkFor={b => `/x/${b.id}`} /></MemoryRouter>);

it('prioritises bands and percentages by server ordinals, displays bands without hidden percentages', () => {
  const band = bandCase('BANDFIRST', true, 3);
  const disabled = bandCase('BANDDISABLED', false, 9);
  const numeric = { ...demoBookings('2026-10-26')[0], id: 'NUMERIC', registration: 'NUMERIC' };
  numeric.assessment = { ...numeric.assessment!, rank_key: 2 };
  show([numeric, disabled, band]);
  const ranked = within(screen.getByRole('region', { name: /highest priority first/i }));
  expect(ranked.getAllByTestId('booking-row').map(row => row.getAttribute('data-reg'))).toEqual(['BANDFIRST', 'NUMERIC']);
  const row = ranked.getAllByTestId('booking-row')[0];
  expect(row).toHaveTextContent('High band');
  expect(row).not.toHaveTextContent('%');
  expect(ranked.getByText('45%')).toBeVisible();
  expect(ranked.queryByText(/Each percentage/)).not.toBeInTheDocument();
  expect(within(screen.getByRole('region', { name: /not ranked yet/i })).getByText('BANDDISABLED')).toBeVisible();
  expect(screen.queryByText('95%')).not.toBeInTheDocument();
});

it('keeps missing-key and unqualified bands out of priority and excludes other booking types', () => {
  const missing = bandCase('MISSINGKEY', true, null);
  const unsafe = bandCase('UNQUALIFIED', true, 9);
  unsafe.assessment!.display_policy = { ...unsafe.assessment!.display_policy!, operational_qualified: false };
  const retest = { ...bandCase('RETEST', true, 10), booking_type: 'retest' as const };
  const service = { ...bandCase('SERVICE', true, 11), booking_type: 'service' as const };
  const cancelled = { ...bandCase('CANCELLED', true, 12), status: 'cancelled' as const };
  show([missing, unsafe, retest, service, cancelled]);
  expect(screen.queryByRole('list', { name: 'Ranked MOT bookings' })).not.toBeInTheDocument();
  expect(screen.getByText('No MOT bookings with prioritisation available in this window.')).toBeVisible();
  expect(within(screen.getByRole('region', { name: /not ranked yet/i })).getAllByTestId('booking-row')).toHaveLength(2);
  expect(within(screen.getByRole('region', { name: /^Retests/ })).getByText('RETEST')).toBeVisible();
  expect(screen.queryByText('95%')).not.toBeInTheDocument();
});
