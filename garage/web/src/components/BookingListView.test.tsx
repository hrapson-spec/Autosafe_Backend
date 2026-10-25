import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { BookingListView } from './BookingListView';
import { demoBookings } from '../mocks/fixtures';

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
